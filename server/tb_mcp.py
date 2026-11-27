# SPDX-License-Identifier: MIT
"""Cera MCP server — stdio JSON-RPC, standard library only (runs on Python 3.9).

    python3 bin/cera mcp            # Claude Code, Codex and Antigravity launch this command

Spec: MCP 2025-11-25 / 2025-06-18 / 2025-03-26 (https://modelcontextprotocol.io/specification/2025-11-25).
One line on stdin/stdout = one JSON-RPC message. **Only JSON-RPC goes to stdout** (logs and accidental prints go to stderr).

Same Mac (default): reads the site address (local_url) from $CERA_HOME/sites/*/state.json and, the first time a site is used, creates its own agent
token (by:"mcp"). On exit it revokes that token (best effort). It appears on screen as '<name> · AI' (the name is clientInfo.name;
the environment variable CERA_AGENT_NAME takes precedence).
Remote (a board on another computer): giving CERA_URL=<board address> plus one (or both) of the following hides the site_* tools and uses only that address.
  - CERA_TOKEN=tb_ag_…  : agent token (`cera agent add <site> --name …`). For temporary boards (quick tunnel).
  - CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET : Cloudflare Access service token (personal board web / Access mode).
      Sent in the CF-Access-Client-Id / CF-Access-Client-Secret headers. (CERA_CF_CLIENT_ID/SECRET are also accepted.)
      In Access mode, agent tokens are not used for requests from outside this Mac, so the service token alone is enough, without CERA_TOKEN.
  If CERA_URL is set but neither is, a warning is printed to stderr at startup and the board tools report an error.

All board writes go through the HTTP API (writing to disk directly while the server is running would be overwritten). Elements are built by agentkit (the element factory).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import re
import signal
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST = PROTOCOLS[0]
WAIT_DEFAULT = 60             # board_wait timeout_s when omitted (still capped per client below)
WAIT_SCHEMA_MAX = 600
WAIT_CAPS = {"claude-code": 600, "codex-mcp-client": 50}   # exact clientInfo.name → most seconds one board_wait may block (Codex tool_timeout_sec default 60)
WAIT_CAP_OTHER = 55          # anything else (Antigravity etc., names unconfirmed) — under a 60 s tool timeout
WAIT_PROGRESS_S = 20         # notifications/progress interval while waiting, when the request carried a progressToken
ECHO_S = 2.0                 # a stroke re-written by the same person this soon after it was reported is the smoothing echo, not news
ITEM_FIELDS = {              # board_add: fields each kind accepts — anything else is refused (nothing is added)
    "text": ("kind", "ref", "text", "x", "y", "size", "color", "width"),
    "box": ("kind", "ref", "shape", "label", "text", "x", "y", "w", "h", "fill", "color"),
    "arrow": ("kind", "ref", "from", "to", "label", "dashed", "color", "head"),
    "line": ("kind", "ref", "points", "dashed", "color"),
    "frame_page": ("kind", "title", "x", "y"),
    "image": ("kind", "ref", "path", "w", "x", "y"),
}
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
             ".svg": "image/svg+xml"}
MAX_FILE = 10 * 1024 * 1024   # server /files cap (data URL length)
ELEMENTS_BYTES = 45_000        # cap on element JSON per board_read(elements) call — beyond it, continue reading with offset
READ_LIMIT = 120              # board_read default line / element count — so summary text + structured fit within MAX_RESPONSE
MAX_RESPONSE = 60_000          # cap in bytes on a tool response (text + structured) — stays within the Claude Code default MCP output cap (25k tokens) even when filled with Korean (3 bytes · about 1 token per character)
_INSTRUCTIONS_KO = (
    "Cera 는 여러 사람이 같이 보는 화이트보드다. 보드는 지금 다른 사람이 보고 있을 수 있다.\n"
    "- 지우기(board_delete) · 공개 터널 열기(site_open tunnel:\"cloudflared\") 전에는 사용자에게 먼저 묻는다.\n"
    "- 먼저 board_read(요약)로 보드를 읽는다. 줄 끝의 (vN · 라벨 vM) 이 그 요소의 판이다. 글 · 색을 고치는 board_update 에는 "
    "base_version=N(라벨 있는 상자면 label_base_version=M 도)을 꼭 준다 — 그사이 사람이 고쳤으면 덮지 않고 conflict 로 돌아온다. "
    "자리 · 크기만 옮길 때는 없어도 된다. 라벨 글도 상자 id 로 고친다(라벨 id 는 받지 않는다). "
    "지우기(board_delete)도 항목마다 같은 판을 준다.\n"
    "- 그린 것은 사람 화면에 바로 보이고, 화면에는 이 에이전트가 '<이름> · AI' 로 보인다.\n"
    "- 수식 · mermaid 흐름도는 아직 화면의 수식 · 흐름도 도구로만 넣을 수 있다.\n"
    "The board is shared and may be watched live. Ask the user before deleting or opening a public tunnel."
)
_INSTRUCTIONS_EN = (
    "Cera is a whiteboard that several people view together. Someone else may be looking at the board right now.\n"
    "- Ask the user first before deleting (board_delete) or opening a public tunnel (site_open tunnel:\"cloudflared\").\n"
    "- Read the board first with board_read (summary). The (vN · label vM) at the end of a line is that element's version. "
    "When board_update changes text or color, always pass base_version=N (and label_base_version=M too for a box that has a label) — "
    "if a person edited it in the meantime, nothing is overwritten and it comes back as conflict. "
    "It may be omitted when only moving or resizing. Label text is also changed through the box id (label ids are not accepted). "
    "Deleting (board_delete) also takes the same version per item.\n"
    "- What you draw shows up on people's screens right away, where this agent appears as '<name> · AI'.\n"
    "- Math and mermaid flowcharts can still only be added with the on-screen math / flowchart tools.\n"
    "The board is shared and may be watched live. Ask the user before deleting or opening a public tunnel."
)


def instructions() -> str:
    return T(_INSTRUCTIONS_KO, _INSTRUCTIONS_EN)


def _sibling(name):
    key = "cera_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


def T(ko, en):
    return _sibling("i18n").T(ko, en)


ak = _sibling("agentkit")
agents = _sibling("agents")
importers = _sibling("importers")   # board_import


def log(*a):
    print("cera-mcp:", *a, file=sys.stderr, flush=True)


class ToolError(Exception):
    """Tool execution error — returned as an isError:true result (so the model can fix it and call again)."""


def wait_cap(client_name: Optional[str]) -> int:
    """Most seconds one board_wait may block for this client (B7). CERA_WAIT_MAX overrides (1-3600)."""
    env = _sibling("i18n").env("WAIT_MAX")
    if env and env.strip():
        try:
            return int(min(3600, max(1, float(env))))
        except ValueError:
            log("CERA_WAIT_MAX is not a number:", env[:20])
    return WAIT_CAPS.get((client_name or "").strip().lower(), WAIT_CAP_OTHER)   # exact names only: an unknown 'claude-code-x' gets 55


def agent_name_for(client_name: Optional[str]) -> str:
    env = _sibling("i18n").env("AGENT_NAME")
    if env and env.strip():
        return agents.clean_name(env)
    n = (client_name or "").strip().lower()
    if n.startswith("claude"):
        return "Claude"
    if n.startswith("codex"):
        return "Codex"
    if n.startswith("antigravity") or n in ("agy",) or n.startswith("agy-") or n.startswith("agy "):
        return "Antigravity"
    return agents.clean_name(client_name) if client_name else "Agent"


# Hides the agent cursor if this process dies without cleaning up (SIGKILL, crash): a tiny separate process reads one line per
# board the cursor was shown on; when the pipe hits EOF without a "done" line, it posts pointer:null for each. Standard library only.
_WATCHDOG = r"""
import json, sys, urllib.parse, urllib.request
boards = {}
for line in sys.stdin:
    try:
        d = json.loads(line)
    except ValueError:
        continue
    if d.get("done"):
        sys.exit(0)
    boards[(d["base"], d["bid"])] = d
for d in boards.values():
    h = {"X-Whiteboard": "1", "Content-Type": "application/json"}
    h.update(d.get("extra") or {})
    if d.get("token"):
        h["Authorization"] = "Bearer " + d["token"]
    local = (urllib.parse.urlsplit(d["base"]).hostname or "") in ("127.0.0.1", "localhost", "::1")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    body = json.dumps({"client": d["client"], "pointer": None, "button": "up"}).encode()
    try:
        op.open(urllib.request.Request(d["base"] + "/api/whiteboard/boards/" + d["bid"] + "/presence", data=body, headers=h, method="POST"), timeout=2).read()
    except Exception:
        pass
"""


# ------------------------------------------------------------------ HTTP
class Conn:
    """Route to one board server — Bearer token, X-Whiteboard, and (optionally) CF Access service token headers."""

    def __init__(self, base: str, token: Optional[str], client: str, extra: Optional[dict] = None):
        self.base = base.rstrip("/")
        self.token, self.client, self.extra = token, client, dict(extra or {})
        host = urllib.parse.urlsplit(self.base).hostname or ""
        # addresses on this Mac bypass the proxy (with http_proxy set, a 127.0.0.1 request would go out through it)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if host in ("127.0.0.1", "localhost", "::1") \
            else urllib.request.build_opener()

    def req(self, method: str, path: str, body=None, timeout: float = 30) -> Tuple[int, object]:
        h = {"X-Whiteboard": "1", "Accept": "application/json", **self.extra}
        if self.token:   # for a remote (Access) using only a service token, do not attach Authorization
            h["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with self.opener.open(r, timeout=timeout) as resp:
                raw = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        except (urllib.error.URLError, OSError) as e:
            raise ToolError(T(f"보드 서버에 닿지 않는다({self.base}): {getattr(e, 'reason', e)}",
                              f"Cannot reach the board server ({self.base}): {getattr(e, 'reason', e)}"))
        try:
            return code, (json.loads(raw.decode("utf-8")) if raw else None)
        except ValueError:
            return code, raw.decode("utf-8", "replace")[:300]

    def ok(self, method, path, body=None, timeout=30, want=(200, 201, 204)):
        code, data = self.req(method, path, body, timeout)
        if code in want:
            return data
        if code == 401:
            raise ToolError(T("인증이 거부됐다(401) — 에이전트 토큰이 취소 · 만료됐거나, Access 서비스 토큰이 이 보드의 members.json "
                              "agents 에 없다. 사용자에게 물어 본다.",
                              "Authentication was refused (401) — the agent token was revoked or expired, or the Access service token is not in this "
                              "board's members.json agents. Ask the user."))
        if code == 403:
            msg = (data or {}).get("error") if isinstance(data, dict) else data
            raise ToolError(T(f"권한이 없다(403: {msg}) — 보기 전용 토큰이거나 사이트 설정이 막는다.",
                              f"No permission (403: {msg}) — a view-only token, or the site settings block it."))
        if code == 404:
            raise ToolError(T(f"없다(404): {path}", f"Not found (404): {path}"))
        raise ToolError(T(f"서버 오류 {code}: {data}", f"Server error {code}: {data}"))


# ------------------------------------------------------------------ Sessions (sites, tokens)
class Session:
    def __init__(self, ctx):
        self.ctx = ctx
        self.client_name = None
        self.lock = threading.Lock()
        self.conns: Dict[str, Conn] = {}          # sid → Conn (my token)
        self.tokens: Dict[str, dict] = {}         # sid → the token entry I created (id, expires_at)
        self.remote = None
        self.remote_error = None
        self.pointed: Dict[Tuple[str, str], "Conn"] = {}   # (server, board) → Conn where this agent's cursor is shown
        self.recent_ink: Dict[str, tuple] = {}   # stroke id → (connection id, element as reported added, when)
        self.seen: Dict[Tuple[str, str], List[tuple]] = {}   # (server, board) → [(epoch, seq, {id: element})] — the board as last seen at a seq
        self.closed = False
        self.watchdog = None
        env = _sibling("i18n").env   # CERA_* first, then the old TEMPBOARD_* names
        url, tok = env("URL"), env("TOKEN") or None
        cid = os.environ.get("CF_ACCESS_CLIENT_ID") or env("CF_CLIENT_ID")
        sec = os.environ.get("CF_ACCESS_CLIENT_SECRET") or env("CF_CLIENT_SECRET")
        extra = {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": sec} if cid and sec else {}
        if url:
            if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
                self.remote_error = T(f"CERA_URL 이 http(s) 주소가 아니다: {url[:80]}", f"CERA_URL is not an http(s) address: {url[:80]}")
            elif not tok and not extra:
                self.remote_error = T("CERA_URL 만 있고 인증이 없다 — CERA_TOKEN(에이전트 토큰) 또는 "
                                      "CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET(Access 서비스 토큰)을 준다",
                                      "CERA_URL is set but there is no authentication — give CERA_TOKEN (agent token) or "
                                      "CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET (Access service token)")
            if self.remote_error:
                log(T("주의:", "Warning:"), self.remote_error)
            seed = tok or cid or url
            self.remote = Conn(url, tok, "ag-" + hashlib.sha256(seed.encode()).hexdigest()[:10], extra)

    @property
    def name(self):
        return agent_name_for(self.client_name)

    # -- Sites
    def running_sites(self):
        return [s for s in self.ctx.all_sites() if self.ctx.live_status(s) == "running"]

    def resolve_site(self, sid: Optional[str]) -> str:
        if sid:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(sid)):
                raise ToolError(T("site id 형식이 틀리다", "The site id format is wrong"))
            return str(sid)
        run = self.running_sites()
        if len(run) == 1:
            return run[0]["id"]
        if not run:
            raise ToolError(T("열린 사이트가 없다 — site_open 으로 연다(사용자가 원할 때).", "No site is open — open one with site_open (when the user wants)."))
        raise ToolError(T("열린 사이트가 여럿이다 — site 를 준다: ", "Several sites are open — give site: ") + ", ".join(s["id"] for s in run))

    def conn(self, sid: Optional[str]) -> Tuple[Conn, Optional[str]]:
        if self.remote:
            if self.remote_error:
                raise ToolError(self.remote_error)
            return self.remote, None
        sid = self.resolve_site(sid)
        with self.lock:
            st = self.ctx.read_state(sid)
            if not st:
                raise ToolError(T(f"사이트 {sid} 가 없다", f"No such site: {sid}"))
            if self.ctx.live_status(st) != "running" or not st.get("local_url"):
                raise ToolError(T(f"사이트 {sid} 는 닫혀 있다 — 다시 열려면 site_open(새 사이트)", f"Site {sid} is closed — to reopen, use site_open (a new site)"))
            mine = self.tokens.get(sid)
            if sid in self.conns and mine and (not mine["expires_at"] or mine["expires_at"] > time.time() + 30):
                return self.conns[sid], sid
            ent, token = agents.add(self.ctx.SITES, sid, self.name, "edit", None, by="mcp")
            c = Conn(st["local_url"], token, "ag-" + ent["id"])
            # wait until the supervisor loop re-reads state.json (usually within 1 second)
            deadline = time.time() + 5
            while True:
                code, _ = c.req("GET", "/api/whiteboard/me", timeout=5)
                if code == 200:
                    break
                if time.time() > deadline:
                    raise ToolError(T(f"토큰이 {sid} 에서 아직 받아들여지지 않는다(HTTP {code}) — 사이트 감독이 살아 있는지 cera list",
                                      f"The token is not yet accepted by {sid} (HTTP {code}) — check with cera list that the site supervisor is alive"))
                time.sleep(0.15)
            self.conns[sid], self.tokens[sid] = c, ent
            return c, sid

    def drop(self, sid: str):
        with self.lock:
            ent = self.tokens.pop(sid, None)
            self.conns.pop(sid, None)
        if ent:
            try:
                agents.revoke(self.ctx.SITES, sid, [ent["id"]])
            except Exception as e:   # noqa: BLE001
                log("revoke failed", sid, e)

    def remember(self, base_url: str, bid: str, epoch: str, seq, els):
        """Keep the board as seen at seq (board_read / board_wait results) so a later board_wait since_seq=seq compares correctly."""
        if not isinstance(seq, int):
            return
        snap = dict(els) if isinstance(els, dict) else {e["id"]: e for e in els or [] if isinstance(e, dict) and isinstance(e.get("id"), str)}
        with self.lock:
            lst = [x for x in self.seen.get((base_url, bid), []) if x[1] != seq][-3:]
            lst.append((epoch, seq, snap))
            self.seen[(base_url, bid)] = lst

    def note_own(self, base_url: str, bid: str, els):
        """This agent's own writes never come back from /poll — put them into every remembered snapshot of the board,
        so a later board_wait(since_seq) sees a person moving / deleting them as a move / delete, not as 'added' / nothing."""
        with self.lock:
            for _, _, snap in self.seen.get((base_url, bid), []):
                for e in els:
                    if isinstance(e, dict) and isinstance(e.get("id"), str):
                        snap[e["id"]] = copy.deepcopy(e)

    def forget(self, base_url: str, bid: str):
        with self.lock:
            self.seen.pop((base_url, bid), None)

    def seen_at(self, base_url: str, bid: str, epoch: str, seq: int) -> Optional[dict]:
        with self.lock:
            for ep, sq, snap in self.seen.get((base_url, bid), []):
                if sq == seq and ep == epoch:
                    return dict(snap)
        return None

    def pointed_at(self, c: "Conn", bid: str):
        """Record a board where this agent's cursor is shown, and tell the watchdog (started on first use)."""
        with self.lock:
            first = (c.base, bid) not in self.pointed
            self.pointed[(c.base, bid)] = c
            if not first or self.closed:
                return
            try:
                if self.watchdog is None:
                    self.watchdog = subprocess.Popen([sys.executable, "-I", "-c", _WATCHDOG], stdin=subprocess.PIPE,
                                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                line = json.dumps({"base": c.base, "bid": bid, "client": c.client, "token": c.token, "extra": c.extra})
                self.watchdog.stdin.write((line + "\n").encode())
                self.watchdog.stdin.flush()
            except (OSError, ValueError) as e:
                log("cursor watchdog unavailable:", e)

    def clear_pointers(self):
        """Hide this agent's cursor on every board it pointed at (before the token is revoked) — so it does not cover the drawing."""
        with self.lock:
            items = list(self.pointed.items())
            self.pointed.clear()
        for (_, bid), c in items:
            try:
                c.req("POST", f"/api/whiteboard/boards/{bid}/presence", {"client": c.client, "pointer": None, "button": "up"}, timeout=1.5)
            except Exception as e:   # noqa: BLE001 — leaving anyway
                log("pointer clear failed", bid, e)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.clear_pointers()
        wd, self.watchdog = self.watchdog, None
        if wd is not None:   # cleaned up normally — the watchdog leaves without doing anything
            try:
                wd.stdin.write(b'{"done": true}\n')
                wd.stdin.close()
            except (OSError, ValueError):
                pass
        for sid in list(self.tokens):
            self.drop(sid)

    def board_id(self, c: Conn, sid: Optional[str], board: Optional[str]) -> str:
        if board:
            if not re.fullmatch(r"[a-z0-9]{8,32}", str(board)):
                raise ToolError(T("board id 형식이 틀리다(board_list 의 id)", "The board id format is wrong (use an id from board_list)"))
            return str(board)
        if sid:
            st = self.ctx.read_state(sid) or {}
            if st.get("board"):
                return st["board"]
        bl = c.ok("GET", "/api/whiteboard/boards") or []
        if len(bl) == 1:
            return bl[0]["id"]
        if not bl:
            raise ToolError(T("보드가 없다 — board_create 로 만든다", "There is no board — create one with board_create"))
        raise ToolError(T("보드가 여럿이다 — board 를 준다: ", "Several boards exist — give board: ") + ", ".join(f'{b["id"]}("{b.get("title", "")}")' for b in bl[:20]))


# ------------------------------------------------------------------ Helper functions
def _live(els):
    return [e for e in els if isinstance(e, dict) and not e.get("isDeleted")]


def _copy(e):
    return copy.deepcopy(e)


def _fetch(c: Conn, bid: str) -> dict:
    d = c.ok("GET", f"/api/whiteboard/boards/{bid}")
    if not isinstance(d, dict):
        raise ToolError(T("보드를 읽지 못했다", "Could not read the board"))
    return d


def _num(v, name, lo=-1e7, hi=1e7):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
        raise ToolError(T(f"{name} 는 {lo}..{hi} 사이의 수", f"{name} must be a number between {lo} and {hi}"))
    return float(v)


def _str(v, name, n=4000, allow_empty=False):
    if not isinstance(v, str) or (not allow_empty and not v.strip()) or len(v) > n:
        raise ToolError(T(f"{name} 는 1–{n}자 글", f"{name} must be text of 1-{n} characters"))
    return v


COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|transparent|[a-z]{3,20})$")


def _color(v, name):
    if not isinstance(v, str) or not COLOR.match(v):
        raise ToolError(T(f"{name} 는 #rrggbb 같은 색", f"{name} must be a color such as #rrggbb"))
    return v


def _labels(els) -> Dict[str, dict]:
    """container id → bound text (the live ones)."""
    out = {}
    for e in _live(els):
        if e.get("type") == "text" and isinstance(e.get("containerId"), str):
            out[e["containerId"]] = e
    return out


def _bound_arrows(els, eid):
    return [a for a in _live(els) if a.get("type") == "arrow" and
            eid in ((a.get("startBinding") or {}).get("elementId"), (a.get("endBinding") or {}).get("elementId"))]


def _kind_word(e) -> str:
    t = e.get("type")
    cd = e.get("customData") if isinstance(e.get("customData"), dict) else {}
    if t in ak.SHAPE_WORD:
        return ak.shape_word(t)
    if t == "text":
        return T("라벨", "label") if e.get("containerId") else T("글", "text")
    if t == "arrow":
        return T("연결", "arrow")
    if t == "line":
        return T("선", "line")
    if t == "freedraw":
        return T("손글씨 획", "ink stroke")
    if t == "image":
        return T("수식", "math") if cd.get("latex") else T("그림", "image")
    if t == "frame":
        return T("장", "page") if cd.get("tbPage") is not None else T("틀", "frame")
    return str(t)


WAIT_KINDS = ("ink", "text", "shape", "any")


def _wait_kind(e) -> str:
    t = e.get("type")
    return "ink" if t == "freedraw" else ("text" if t == "text" else "shape")


def _is_echo(prev: dict, el: dict) -> bool:
    """el re-writes stroke prev without moving it, within ECHO_S by the element's own 'updated' clock (the app's smoothing pass)."""
    if not (isinstance(prev, dict) and el.get("type") == "freedraw" and not el.get("isDeleted")):
        return False
    if el.get("x") != prev.get("x") or el.get("y") != prev.get("y"):
        return False
    u0, u1 = prev.get("updated"), el.get("updated")
    if not all(isinstance(u, (int, float)) and not isinstance(u, bool) for u in (u0, u1)):
        return False
    return 0 <= u1 - u0 <= ECHO_S * 1000


def _txt(e) -> str:
    v = e.get("originalText") if isinstance(e.get("originalText"), str) else e.get("text")
    return ak._clip(v or "", 80)


_ACT_EN = {"지움": "deleted", "추가": "added", "글 고침": "text edited", "옮김": "moved", "크기": "resized", "고침": "edited", "손글씨": "ink"}


def ACT_WORD(act: str) -> str:
    """Action word in board_wait's structured changes (the internal key stays Korean; the shown word follows the language)."""
    return T(act, _ACT_EN.get(act, act))


BAD = object()   # the version field got a non-integer value


def _version(v):
    """base_version field — None if absent, the value if an integer ≥ 0, BAD for anything else ('v2', '2', 2.0, true)."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return BAD
    return v


def _is_bound_label(e, by_id) -> bool:
    return e.get("type") == "text" and isinstance(e.get("containerId"), str) and e["containerId"] in by_id


def refused_text(code: str) -> Optional[str]:
    """Explanation for a refusal code (a '{box}' placeholder is filled by the caller)."""
    if code == "label_id":
        return T("라벨 id 로는 고치거나 지우지 않는다. 상자 id [{box}] 로 주고, board_read 의 그 줄 끝 (vN · 라벨 vM) 에서 "
                 "base_version=N(상자 판) · label_base_version=M(라벨 판)을 함께 준다.",
                 "Labels are not edited or deleted by label id. Give the box id [{box}], and from the end of that line in board_read "
                 "(vN · label vM) pass base_version=N (box version) and label_base_version=M (label version) together.")
    if code == "bad_version":
        return T("base_version · label_base_version 은 정수다(줄 끝 'v2' 면 2). 다시 부른다.",
                 "base_version and label_base_version are integers (2 for 'v2' at the end of a line). Call again.")
    return None


_LINE_ID = re.compile(r"^(\s*)\[([^\]]+)\] ")


def annotate(lines: List[str], by_id: dict, labels: dict) -> List[str]:
    """Append the version to a summary line — '[id] box "A"  (v3 · label v2)' (example shown translated). So that even clients that do not pass structuredContent to the model
    let the agent learn base_version / label_base_version from the text alone."""
    out = []
    for ln in lines:
        m = _LINE_ID.match(ln)
        e = by_id.get(m.group(2)) if m else None
        if isinstance(e, dict) and e.get("version") is not None:
            tag = f"v{e['version']}"
            lab = labels.get(e["id"])
            if lab is not None and lab.get("version") is not None:
                tag += T(f" · 라벨 v{lab['version']}", f" · label v{lab['version']}")
            ln = f"{ln}  ({tag})"
        out.append(ln)
    return out


def image_size(data: bytes, mime: str) -> Optional[Tuple[float, float]]:
    """Image size (px) from the header. None if unknown."""
    try:
        if mime == "image/png" and data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if mime == "image/gif" and data[:4] == b"GIF8":
            return struct.unpack("<HH", data[6:10])
        if mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if kind == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return 1 + (b & 0x3FFF), 1 + ((b >> 14) & 0x3FFF)
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
        if mime == "image/jpeg" and data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                ln = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + ln
        if mime == "image/svg+xml":
            head = data[:4096].decode("utf-8", "replace")
            mw = re.search(r'<svg[^>]*\swidth="([\d.]+)(px)?"', head)
            mh = re.search(r'<svg[^>]*\sheight="([\d.]+)(px)?"', head)
            if mw and mh:
                return float(mw.group(1)), float(mh.group(1))
            vb = re.search(r'viewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', head)
            if vb:
                return float(vb.group(1)), float(vb.group(2))
    except (struct.error, ValueError, IndexError):
        return None
    return None


def read_image(path_s: str) -> Tuple[str, str, float, float, str]:
    """Image file → (file id, data URL, w, h, mime). Regular files only · extension allowlist · size cap."""
    p = Path(os.path.expanduser(str(path_s)))
    try:
        rp = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ToolError(T(f"파일이 없다: {path_s}", f"No such file: {path_s}"))
    if not rp.is_file():
        raise ToolError(T("일반 파일만 넣을 수 있다", "Only regular files can be added"))
    mime = IMAGE_EXT.get(rp.suffix.lower())
    if not mime:
        raise ToolError(T("그림은 png · jpg · gif · webp · svg 만", "Images must be png, jpg, gif, webp or svg"))
    if rp.stat().st_size > (MAX_FILE - 100) * 3 // 4:
        raise ToolError(T("그림이 너무 크다(약 7.5 MB 까지)", "The image is too large (up to about 7.5 MB)"))
    data = rp.read_bytes()
    url = f"data:{mime};base64," + base64.b64encode(data).decode()
    size = image_size(data, mime) or (400, 300)
    return hashlib.sha1(data).hexdigest(), url, float(size[0]), float(size[1]), mime


# ------------------------------------------------------------------ Tool implementations
class Tools:
    def __init__(self, s: Session):
        self.s = s

    # ---- Sites
    def site_list(self, a):
        rows = []
        for st in self.s.ctx.all_sites():
            live = self.s.ctx.live_status(st)
            n = len(list((self.s.ctx.SITES / st["id"] / "data" / "boards").glob("*/board.json")))
            rows.append({"site": st["id"], "title": st.get("title") or "", "status": live, "expires_at": st.get("expires_at") or None,
                         "boards": n, "links": self.s.ctx.links(st) if live == "running" else {}})
        lines = [T(f'{r["site"]}  {r["status"]}  보드 {r["boards"]}  {r["title"]}',
                   f'{r["site"]}  {r["status"]}  boards {r["boards"]}  {r["title"]}') for r in rows] or [T("사이트가 없다", "No sites")]
        return "\n".join(lines), {"sites": rows}

    def site_open(self, a):
        tunnel = a.get("tunnel", "none")
        if tunnel not in ("none", "cloudflared"):
            raise ToolError(T('tunnel 은 "none" 또는 "cloudflared"', 'tunnel must be "none" or "cloudflared"'))
        title = a.get("title") or T("보드", "Board")
        argv = [sys.executable, str(self.s.ctx.ROOT / "bin" / "cera"), "start", "--json", "--tunnel", tunnel,
                "--title", _str(title, "title", 80)]
        if a.get("ttl"):
            argv += ["--ttl", _str(str(a["ttl"]), "ttl", 12)]
        if a.get("view_link"):
            argv.append("--view-link")
        if a.get("lan"):
            argv.append("--lan")
        try:
            p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            raise ToolError(T("사이트를 여는 데 너무 오래 걸린다(화면 파일 빌드?) — cera start 를 직접 실행해 본다",
                              "Opening the site takes too long (building the UI files?) — try running cera start yourself"))
        if p.returncode != 0:
            raise ToolError(T("사이트를 열지 못했다: ", "Could not open the site: ") + (p.stderr or p.stdout)[-800:])
        try:
            out = json.loads(p.stdout)
        except ValueError:
            raise ToolError(T("cera start 출력을 읽지 못했다: ", "Could not read the output of cera start: ") + p.stdout[-300:])
        sid = out["id"]
        st = self.s.ctx.read_state(sid) or {}
        self.s.conn(sid)   # create the token now (so it shows as AI on the human screen)
        res = {"site": sid, "board": st.get("board"), "links": out.get("links", {}), "expires_at": out.get("expires_at"),
               "tunnel": tunnel}
        text = [T(f"사이트 {sid} 를 열었다 (보드 {st.get('board')}).", f"Opened site {sid} (board {st.get('board')}).")]
        for k, v in res["links"].items():
            text.append(f"  {k}: {v}")
        if tunnel == "none":
            text.append(T("  이 컴퓨터에서만 열린다. 다른 사람과 쓰려면 사용자에게 물은 뒤 tunnel:\"cloudflared\" 로 새로 연다.",
                          "  Open only on this computer. To use it with other people, ask the user, then open a new one with tunnel:\"cloudflared\"."))
        return "\n".join(text), res

    def site_close(self, a):
        sid = self.s.resolve_site(a.get("site"))
        self.s.drop(sid)
        p = subprocess.run([sys.executable, str(self.s.ctx.ROOT / "bin" / "cera"), "stop", sid], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise ToolError(T("닫지 못했다: ", "Could not close: ") + (p.stderr or p.stdout)[-400:])
        return T(f"사이트 {sid} 를 닫았다(그림은 남는다).", f"Closed site {sid} (the drawings are kept)."), {"site": sid, "closed": True}

    # ---- Board
    def board_list(self, a):
        c, _ = self.s.conn(a.get("site"))
        bl = c.ok("GET", "/api/whiteboard/boards") or []
        rows = [{"board": b.get("id"), "title": b.get("title"), "elements": b.get("elements"), "online": b.get("online"),
                 "updated_at": b.get("updated_at")} for b in bl]
        lines = [T(f'{r["board"]}  "{r["title"]}"  요소 {r["elements"]} · 접속 {r["online"]}',
                   f'{r["board"]}  "{r["title"]}"  elements {r["elements"]} · online {r["online"]}') for r in rows] or [T("보드가 없다", "No boards")]
        return "\n".join(lines), {"boards": rows}

    def board_create(self, a):
        c, _ = self.s.conn(a.get("site"))
        meta = c.ok("POST", "/api/whiteboard/boards", {"title": _str(a.get("title") or T("새 보드", "New board"), "title", 80)})
        return T(f'보드 {meta["id"]} "{meta.get("title")}" 를 만들었다.', f'Created board {meta["id"]} "{meta.get("title")}".'), {"board": meta["id"], "title": meta.get("title")}

    def board_read(self, a):
        c, sid = self.s.conn(a.get("site"))
        bid = self.s.board_id(c, sid, a.get("board"))
        d = _fetch(c, bid)
        els = d.get("elements") or []
        fmt = a.get("format", "summary")
        page = a.get("page")
        if page is not None:
            page = int(_num(page, "page", 1, 10000))
        limit = int(_num(a.get("limit", READ_LIMIT), "limit", 1, 2000))
        offset = int(_num(a.get("offset", 0), "offset", 0, 1e7))
        ids = a.get("ids")
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 2000):
            raise ToolError(T("ids 는 글 목록(2000개까지)", "ids must be a list of strings (up to 2000)"))
        meta = {"board": bid, "title": (d.get("meta") or {}).get("title"), "seq": d.get("seq"), "epoch": d.get("epoch")}
        self.s.remember(c.base, bid, d.get("epoch") or "", d.get("seq"), els)
        by_id = {e.get("id"): e for e in els if isinstance(e, dict)}
        if fmt == "elements" or ids:
            text, st = ak.describe(els, page)
            want = set(ids or [])
            if page is not None and not ids:
                want = {i["id"] for i in st["items"]} | {l["id"] for l in st["links"]} | \
                       {i.get("label_id") for i in st["items"] if i.get("label_id")} | \
                       {sid_ for s in st["strokes"] for sid_ in s["ids"]}
            pick = [e for e in _live(els) if not want or e.get("id") in want]
            if ids:   # choosing a box includes its label
                labs = _labels(els)
                pick += [labs[i] for i in ids if i in labs and labs[i] not in pick]
            out, size, n = [], 0, offset
            for e in pick[offset:offset + limit]:
                if not a.get("points") and e.get("type") in ("freedraw", "line", "arrow") and len(e.get("points") or []) > 8:
                    e = {**{k: v for k, v in e.items() if k not in ("points", "pressures")}, "points_n": len(e.get("points") or [])}
                b = len(json.dumps(e, ensure_ascii=False).encode("utf-8"))
                if out and size + b > ELEMENTS_BYTES:
                    break
                out.append(e)
                size += b
                n += 1
            rest = len(pick) - n
            res = {**meta, "total": len(pick), "offset": offset, "elements": out, "truncated": rest,
                   "next_offset": n if rest else None}
            head = T(f"요소 {len(out)}개(전체 {len(pick)} 중 {offset}번째부터)", f"{len(out)} element(s) (from #{offset} of {len(pick)} total)")
            if rest:
                head += T(f" — {rest}개 더: offset={n} 으로 이어 읽는다", f" — {rest} more: continue reading with offset={n}")
            if not a.get("points"):
                head += T(". 점이 많은 선 · 손글씨는 points 를 뺐다(points_n · 필요하면 points:true)",
                          ". Points were left out of lines / ink with many points (points_n; pass points:true if needed)")
            return head + "\n" + json.dumps(res, ensure_ascii=False), None   # only one copy (text) — do not carry the same JSON twice
        if fmt != "summary":
            raise ToolError(T('format 은 "summary" 또는 "elements"', 'format must be "summary" or "elements"'))
        text, st = ak.describe(els, page)
        for it in st["items"]:
            e = by_id.get(it["id"]) or {}
            it["version"] = e.get("version")
            if it.get("label_id"):
                it["label_version"] = (by_id.get(it["label_id"]) or {}).get("version")
        for l in st["links"]:
            l["version"] = (by_id.get(l["id"]) or {}).get("version")
            if l.get("label_id"):
                l["label_version"] = (by_id.get(l["label_id"]) or {}).get("version")
        cut = {"items": max(0, len(st["items"]) - limit), "links": max(0, len(st["links"]) - limit)}
        st["items"], st["links"] = st["items"][:limit], st["links"][:limit]
        for g in st["strokes"]:   # stroke ids only when a page was chosen and there are not many (a lecture board has thousands of strokes)
            if page is None or len(g["ids"]) > 300:
                g["ids_omitted"] = len(g.pop("ids"))
        lines = annotate(text.split("\n"), by_id, _labels(els))
        if len(lines) > limit + 1:
            lines = lines[:limit + 1] + [T(f"… {len(lines) - limit - 1}줄 더(page · ids · limit 로 좁힌다)",
                                           f"… {len(lines) - limit - 1} more line(s) (narrow with page, ids, limit)")]
        head = T(f'보드 {bid} "{meta["title"] or ""}" · seq {d.get("seq")} — 줄 끝 (vN · 라벨 vM): 글 · 색을 고칠 때 '
                 "board_update 의 base_version=N · label_base_version=M",
                 f'Board {bid} "{meta["title"] or ""}" · seq {d.get("seq")} — end of line (vN · label vM): when changing text / color, '
                 "pass base_version=N · label_base_version=M to board_update")
        st.update(meta)
        if any(cut.values()):
            st["truncated"] = cut
        return head + "\n" + "\n".join(lines), st

    # ---- Writing
    def _own(self, c: Conn, bid: str, ids, written=()):
        """Record what this agent just wrote (see Session.note_own). written: elements sent as is; ids: patched — read back their state."""
        if not self.s.seen.get((c.base, bid)):
            return
        els = list(written)
        ids = [i for i in ids if i not in {e.get("id") for e in els}]
        if ids:
            try:
                cur = {e.get("id"): e for e in _fetch(c, bid).get("elements") or [] if isinstance(e, dict)}
                els += [cur[i] for i in ids if i in cur]
            except ToolError:
                self.s.forget(c.base, bid)   # unknown state → board_wait falls back to comparing with the current board (and says so)
                return
        self.s.note_own(c.base, bid, els)

    def _pointer(self, c: Conn, bid: str, items):
        """Show where the agent worked — just outside the top-left of the group (24 px), not over the drawing."""
        bb = ak.scene_bbox(items)
        if not bb:
            return
        try:
            c.req("POST", f"/api/whiteboard/boards/{bid}/presence",
                  {"client": c.client, "pointer": {"x": bb[0] - 24, "y": bb[1] - 24, "tool": "pointer"},
                   "button": "up", "selected": {}}, timeout=5)
            self.s.pointed_at(c, bid)
        except ToolError:
            pass

    def board_add(self, a):
        items_in = a.get("items")
        if not isinstance(items_in, list) or not items_in or len(items_in) > 300:
            raise ToolError(T("items 는 1–300개 목록", "items must be a list of 1-300"))
        for n, it in enumerate(items_in):
            if isinstance(it, dict) and it.get("kind") in ITEM_FIELDS:
                extra = sorted(str(k) for k in it if k not in ITEM_FIELDS[it["kind"]])
                if extra:   # a wrong field name must not be silently ignored (e.g. heads:"none" drew an arrowhead)
                    raise ToolError(T(f"items[{n}] ({it['kind']}) 에 모르는 칸: {', '.join(extra)} — 쓸 수 있는 칸: "
                                      f"{', '.join(ITEM_FIELDS[it['kind']])}. 아무것도 넣지 않았다.",
                                      f"items[{n}] ({it['kind']}) has unknown field(s): {', '.join(extra)} — allowed: "
                                      f"{', '.join(ITEM_FIELDS[it['kind']])}. Nothing was added."))
                if it["kind"] == "arrow" and "head" in it and it["head"] not in ak.ARROW_HEADS:   # head:null is refused too (it is not "none")
                    raise ToolError(T(f'items[{n}].head 는 "end" · "start" · "both" · "none"', f'items[{n}].head must be "end", "start", "both" or "none"'))
            if isinstance(it, dict) and it.get("kind") in ("math", "mermaid"):
                raise ToolError(T("수식(math) · mermaid 흐름도는 아직 MCP 로 넣을 수 없다 — 사람에게 화면의 수식 · 흐름도 도구"
                                  "(메뉴 '글로 흐름도 만들기')를 쓰라고 안내하거나, 상자 · 화살표로 그린다.",
                                  "Math and mermaid flowcharts cannot be added through MCP yet — tell the person to use the on-screen math / flowchart tool "
                                  "(menu 'Make a flowchart from text'), or draw with boxes and arrows."))
        c, sid = self.s.conn(a.get("site"))
        bid = self.s.board_id(c, sid, a.get("board"))
        d = _fetch(c, bid)
        els = [e for e in d.get("elements") or [] if isinstance(e, dict)]
        live = {e["id"]: e for e in _live(els) if isinstance(e.get("id"), str)}
        by = self.s.name
        at = a.get("at", "right")
        if isinstance(at, dict):
            at = (_num(at.get("x"), "at.x"), _num(at.get("y"), "at.y"))
        elif at not in ("right", "below", "absolute"):
            raise ToolError(T('at 은 "right" · "below" · "absolute" · {x, y}', 'at must be "right", "below", "absolute" or {x, y}'))
        frame = None
        frame_new = False
        if a.get("page") is not None:
            k = int(_num(a["page"], "page", 1, 10000))
            frame = live.get(ak.page_id(k))
            if frame is None:
                raise ToolError(T(f"장 {k} 가 없다 — items 에 {{kind:\"frame_page\"}} 로 새 장을 만든다",
                                  f"Page {k} does not exist — create a new page with {{kind:\"frame_page\"}} in items"))
        refs: Dict[str, dict] = {}
        new: List[dict] = []
        touched_existing: Dict[str, List[str]] = {}   # existing element id → ids of arrows connecting to it (to add to boundElements)
        files = []
        cx = 0.0   # where items without coordinates are laid out in a row (inside the group)
        cy = 0.0
        for n, it in enumerate(items_in):
            if not isinstance(it, dict):
                raise ToolError(T(f"items[{n}] 는 객체", f"items[{n}] must be an object"))
            kind = it.get("kind")
            fid = frame["id"] if frame else None
            has_xy = "x" in it or "y" in it
            x = _num(it.get("x", cx), f"items[{n}].x")
            y = _num(it.get("y", cy), f"items[{n}].y")
            color = _color(it["color"], "color") if it.get("color") else ak.INK
            if kind == "frame_page":
                if frame is not None:
                    raise ToolError(T("frame_page 는 한 번에 하나, page 와 함께 쓰지 않는다", "frame_page is allowed once per call and not together with page"))
                k = ak.next_page(els)
                frame = ak.make_frame(_str(it["title"], "title", 60) if it.get("title") else None, x, y, page=k, existing=els, by=by)
                frame_new = True
                new.append(frame)
                cx, cy = x + 80, y + 120
                continue
            if kind == "text":
                el = ak.make_text(_str(it.get("text"), f"items[{n}].text"), x, y, font_size=_num(it.get("size", 20), "size", 8, 200),
                                  color=color, max_width=_num(it["width"], "width", 40, 4000) if it.get("width") else None,
                                  by=by, frame_id=fid)
                made = [el]
            elif kind == "box":
                shape = it.get("shape", "rectangle")
                if shape not in ak.SHAPES:
                    raise ToolError(T("shape 는 rectangle · ellipse · diamond", "shape must be rectangle, ellipse or diamond"))
                made = ak.make_box(shape, it.get("label", it.get("text")), x, y, _num(it["w"], "w", 10, 4000) if it.get("w") else None,
                                   _num(it["h"], "h", 10, 4000) if it.get("h") else None, color=color,
                                   fill=_color(it["fill"], "fill") if it.get("fill") else "transparent", by=by, frame_id=fid)
            elif kind == "arrow":
                ends = []
                for side in ("from", "to"):
                    v = it.get(side)
                    if isinstance(v, dict):
                        ends.append((_num(v.get("x"), side + ".x"), _num(v.get("y"), side + ".y")))
                    elif isinstance(v, str) and (v in refs or v in live):
                        tgt = refs[v] if v in refs else live[v]
                        if tgt.get("type") in ("arrow", "line", "freedraw"):   # arrows bind only to shapes, text, images, frames
                            raise ToolError(T(f"items[{n}].{side} = {v!r} 는 {tgt.get('type')} — 화살표는 선 · 화살표 · 손글씨에 붙지 않는다. {{x, y}} 로 준다",
                                              f"items[{n}].{side} = {v!r} is a {tgt.get('type')} — arrows do not attach to lines, arrows or ink; give {{x, y}}"))
                        ends.append(tgt if v in refs else _copy(tgt))   # existing element — use a copy only to compute the route; the connection is made via patch(add_bound)
                    else:
                        raise ToolError(T(f"items[{n}].{side} = {v!r} — 이번 items 의 ref 나 보드의 요소 id, 또는 {{x, y}}",
                                      f"items[{n}].{side} = {v!r} — must be a ref from this items list, an element id on the board, or {{x, y}}"))
                sh, eh = ak.ARROW_HEADS[it.get("head") or "end"]
                made = ak.make_arrow(ends[0], ends[1], it.get("label"), color=color, dashed=bool(it.get("dashed")), by=by, frame_id=fid,
                                     start_head=sh, end_head=eh)
                for e in ends:
                    if isinstance(e, dict) and e["id"] in live:
                        touched_existing.setdefault(e["id"], []).append(made[0]["id"])
            elif kind == "line":
                pts = it.get("points")
                if not isinstance(pts, list) or not 2 <= len(pts) <= 500 or \
                        not all(isinstance(p, (list, tuple)) and len(p) == 2 for p in pts):
                    raise ToolError(T(f"items[{n}].points 는 [x, y] 점 2–500개(묶음 안 자리)", f"items[{n}].points must be 2-500 [x, y] points (positions within the group)"))
                made = ak.make_line([(_num(p[0], f"items[{n}].points"), _num(p[1], f"items[{n}].points")) for p in pts],
                                    color=color, dashed=bool(it.get("dashed")), by=by, frame_id=fid)
            elif kind == "image":
                if not it.get("path"):
                    raise ToolError(T("image 는 path(이 컴퓨터의 그림 파일)", "image needs path (an image file on this computer)"))
                file_id, url, w0, h0, mime = read_image(it["path"])
                w = _num(it.get("w", min(480.0, w0)), "w", 10, 4000)
                h = w * h0 / w0 if w0 else w * 0.75
                files.append({"id": file_id, "dataURL": url, "mimeType": mime})
                made = [ak.make_image(file_id, w, h, x, y, by=by, frame_id=fid)]
            else:
                raise ToolError(T(f"items[{n}].kind = {kind!r} — text · box · arrow · line · frame_page · image",
                                  f"items[{n}].kind = {kind!r} — must be text, box, arrow, line, frame_page or image"))
            if it.get("ref"):
                refs[str(it["ref"])] = made[0]
            new.extend(made)
            if not has_xy and kind not in ("arrow", "line"):
                bb = ak.scene_bbox(made)
                if bb:
                    cx = bb[2] + 80
        if not new:
            raise ToolError(T("넣을 것이 없다", "Nothing to add"))
        if frame is not None and frame_new:   # a new page is enlarged so all children fit
            kids = [e for e in new if e is not frame]
            bb = ak.scene_bbox(kids)
            if bb:
                frame["width"] = max(frame["width"], bb[2] - frame["x"] + 80)
                frame["height"] = max(frame["height"], bb[3] - frame["y"] + 80)
        if at == "absolute":
            ak.assign_indices(els, new)
        elif frame is not None and not frame_new:   # inside an existing page — below the drawings in that page (top-left of the page if none)
            inside = [e for e in _live(els) if e.get("frameId") == frame["id"]]
            ib = ak.scene_bbox(inside)
            fx, fy = float(frame.get("x") or 0), float(frame.get("y") or 0)
            ak.place(els, new, at=(fx + 80, (ib[3] + 60) if ib else fy + 120))
        else:
            ak.place(els, new, at=at)
        for f in files:
            c.ok("POST", f"/api/whiteboard/boards/{bid}/files", {"client": c.client, **f}, want=(200, 201))
        for i in range(0, len(new), 2000):
            c.ok("POST", f"/api/whiteboard/boards/{bid}/elements", {"client": c.client, "elements": new[i:i + 2000]})
        if touched_existing:
            ch = [{"id": eid, "add_bound": [{"type": "arrow", "id": aid} for aid in aids]} for eid, aids in touched_existing.items()]
            r = c.ok("POST", f"/api/whiteboard/boards/{bid}/patch", {"client": c.client, "changes": ch}) or {}
            gone = set(r.get("missing") or [])
            if gone:   # a human deleted that box after it was read — release the binding on that end
                fix = []
                for e in new:
                    if e.get("type") != "arrow":
                        continue
                    sets = {end: None for end in ("startBinding", "endBinding") if (e.get(end) or {}).get("elementId") in gone}
                    if sets:
                        fix.append({"id": e["id"], "set": sets})
                if fix:
                    c.ok("POST", f"/api/whiteboard/boards/{bid}/patch", {"client": c.client, "changes": fix})
        self._own(c, bid, list(touched_existing) + [e["id"] for e in new if e.get("type") == "arrow"] if touched_existing else [], new)
        self._pointer(c, bid, new)
        text, st = ak.describe(new + [live[i] for i in touched_existing])
        created = {k: v["id"] for k, v in refs.items()}
        res = {"board": bid, "created": created, "ids": [e["id"] for e in new], "items": st["items"], "links": st["links"]}
        nb = {e["id"]: e for e in new}
        lines = annotate(text.split("\n")[1:], nb, _labels(new))
        return T(f"보드 {bid} 에 {len(new)}개를 넣었다.", f"Added {len(new)} item(s) to board {bid}.") + \
            (f" ref→id: {json.dumps(created, ensure_ascii=False)}" if created else "") + \
            "\n" + "\n".join(lines), res

    def _relabel(self, c_el: dict, lab: Optional[dict], text: str) -> Tuple[dict, dict, Optional[dict]]:
        """Change the text label of a box / arrow → (box set, label set, new label element | None)."""
        fs = float(lab.get("fontSize") or 20) if lab else (16.0 if c_el.get("type") == "arrow" else 20.0)
        ff = int(lab.get("fontFamily") or ak.DEFAULT_FONT) if lab else ak.DEFAULT_FONT
        box_set: dict = {}
        if c_el.get("type") == "arrow":
            limit = max(ak.ARROW_LABEL_WIDTH_FRACTION * float(c_el.get("width") or 0), fs * ak.ARROW_LABEL_MIN_WIDTH_RATIO)
            shown = ak.wrap_text(text, limit, fs, ff)
            w, h = ak.measure_text(shown, fs, ff)
            tmp = {"width": w, "height": h}
            ak.label_on_arrow(c_el, tmp)
        else:
            shape = c_el.get("type")
            bw, bh = float(c_el.get("width") or 0), float(c_el.get("height") or 0)
            limit = ak._inner_max_width(bw, shape) / ak.LABEL_SLACK
            shown = ak.wrap_text(text, max(limit, fs), fs, ff)
            w, h = ak.measure_text(shown, fs, ff)
            nw = max(bw, float(ak._container_dim(w * ak.LABEL_SLACK, shape)))
            nh = max(bh, float(ak._container_dim(h, shape)) + (2 * ak.BOUND_TEXT_PADDING if shape == "rectangle" else 0))
            if (nw, nh) != (bw, bh):
                box_set = {"width": nw, "height": nh}
            tmp = {"width": w, "height": h}
            ak._center_label({"x": c_el["x"], "y": c_el["y"], "width": nw, "height": nh}, tmp)
        lab_set = {"text": shown, "originalText": text, "width": w, "height": h, "x": tmp["x"], "y": tmp["y"]}
        new_lab = None
        if lab is None:
            new_lab = ak._text_el(shown, tmp["x"], tmp["y"], font_size=fs, font_family=ff, color=c_el.get("strokeColor") or ak.INK,
                                  align="center", valign="middle", container_id=c_el["id"], original=text, by=self.s.name,
                                  id=ak.label_id(c_el["id"]), frame_id=c_el.get("frameId"))
        return box_set, lab_set, new_lab

    def board_update(self, a):
        changes = a.get("changes")
        if not isinstance(changes, list) or not changes or len(changes) > 200:
            raise ToolError(T("changes 는 1–200개 목록", "changes must be a list of 1-200"))
        c, sid = self.s.conn(a.get("site"))
        bid = self.s.board_id(c, sid, a.get("board"))
        d = _fetch(c, bid)
        els = [e for e in d.get("elements") or [] if isinstance(e, dict)]
        all_by = {e.get("id"): e for e in els}
        by_id = {e["id"]: e for e in _live(els) if isinstance(e.get("id"), str)}
        labels = _labels(els)
        results, touched = [], []
        own_ids: List[str] = []
        for n, ch in enumerate(changes):
            if not isinstance(ch, dict) or not isinstance(ch.get("id"), str):
                raise ToolError(T(f"changes[{n}] 에 id 가 없다", f"changes[{n}] has no id"))
            eid = ch["id"]
            e = by_id.get(eid)
            if e is None:
                results.append({"id": eid, "status": "missing"})
                continue
            if _is_bound_label(e, by_id):
                # Not accepted by label id — the only route is the box id, and that route checks both the box version and the label version
                results.append({"id": eid, "status": "refused", "error": "label_id", "box": e["containerId"]})
                continue
            bv, lbv = _version(ch.get("base_version")), _version(ch.get("label_base_version"))
            if bv is BAD or lbv is BAD:
                results.append({"id": eid, "status": "refused", "error": "bad_version"})
                continue
            target = e
            cur = _copy(target)
            sets: Dict[str, dict] = {target["id"]: {}}
            bases = {target["id"]: bv}
            lab = labels.get(target["id"])
            if lab is not None and lbv is not None:
                bases[lab["id"]] = lbv
            touches_label = ch.get("text") is not None or ch.get("label") is not None or bool(ch.get("color"))
            if (touches_label or ch.get("fill")) and bv is None:
                # Text and color can be changed only with a base_version (contract change). So a human-edited text is not overwritten without a version
                results.append({"id": eid, "status": "refused", "error": "need_base",
                                "message": T("글 · 색을 고칠 때는 base_version 이 필요하다", "base_version is required when changing text or color")})
                continue
            if lab is not None and touches_label and lbv is None:
                # If a human edits only the label, it is the label text's version that rises — the box version alone cannot protect against that change
                results.append({"id": eid, "status": "refused", "error": "need_label_base",
                                "message": T("label_base_version 이 필요하다", "label_base_version is required")})
                continue
            new_label = None
            # Position / size
            nx, ny = float(cur.get("x") or 0), float(cur.get("y") or 0)
            if "x" in ch:
                nx = _num(ch["x"], "x")
            if "y" in ch:
                ny = _num(ch["y"], "y")
            nx += _num(ch.get("dx", 0), "dx")
            ny += _num(ch.get("dy", 0), "dy")
            dx, dy = nx - float(cur.get("x") or 0), ny - float(cur.get("y") or 0)
            moved = bool(dx or dy)
            if moved:
                cur["x"], cur["y"] = nx, ny
                sets[target["id"]].update(x=nx, y=ny)
            if "w" in ch or "h" in ch:
                if target.get("type") in ("arrow", "line", "freedraw"):
                    raise ToolError(T("선 · 화살표 크기는 w/h 로 바꾸지 않는다", "Line / arrow size is not changed with w/h"))
                if "w" in ch:
                    cur["width"] = _num(ch["w"], "w", 1, 1e5)
                if "h" in ch:
                    cur["height"] = _num(ch["h"], "h", 1, 1e5)
                sets[target["id"]].update(width=cur["width"], height=cur["height"])
            if ch.get("color"):
                col = _color(ch["color"], "color")
                sets[target["id"]]["strokeColor"] = col
                if lab is not None:
                    sets.setdefault(lab["id"], {})["strokeColor"] = col
            if ch.get("fill"):
                sets[target["id"]]["backgroundColor"] = _color(ch["fill"], "fill")
            text = ch.get("text", ch.get("label"))
            if text is not None:
                text = _str(text, "text", 20000)
                if target.get("type") == "text":
                    ff, fs = int(target.get("fontFamily") or ak.DEFAULT_FONT), float(target.get("fontSize") or 20)
                    if target.get("autoResize") is False:
                        shown = ak.wrap_text(text, float(target.get("width") or 200), fs, ff)
                        w, h = ak.measure_text(shown, fs, ff)
                        sets[target["id"]].update(text=shown, originalText=text, height=h, width=max(float(target.get("width") or 0), w))
                    else:
                        w, h = ak.measure_text(text, fs, ff)
                        sets[target["id"]].update(text=text, originalText=text, width=w, height=h)
                elif target.get("type") in ak.SHAPES or target.get("type") == "arrow":
                    box_set, lab_set, new_label = self._relabel(cur, lab, text)
                    if box_set:
                        sets[target["id"]].update(box_set)
                        cur.update(box_set)
                    if new_label is None:
                        sets.setdefault(lab["id"], {}).update(lab_set)
                    else:
                        old = all_by.get(new_label["id"])
                        if old is not None:   # if the same id was deleted, bump the version to revive it
                            new_label["version"] = int(old.get("version") or 0) + 1
                        ak.assign_indices(els, [new_label])
                else:
                    raise ToolError(T(f"{eid} ({target.get('type')}) 에는 글을 넣을 수 없다", f"Text cannot be put into {eid} ({target.get('type')})"))
            # Labels and bound arrows follow
            geo = moved or "w" in ch or "h" in ch or (text is not None and sets[target["id"]].get("width") is not None)
            if geo and target.get("type") != "arrow":
                if lab is not None and "x" not in sets.get(lab["id"], {}):
                    tmp = {"width": float(lab.get("width") or 0), "height": float(lab.get("height") or 0)}
                    ak._center_label(cur, tmp)
                    sets.setdefault(lab["id"], {}).update(x=tmp["x"], y=tmp["y"])
                for arr in _bound_arrows(els, target["id"]):
                    ac = _copy(arr)
                    sb, eb = (ac.get("startBinding") or {}).get("elementId"), (ac.get("endBinding") or {}).get("elementId")
                    ends = [cur if i == target["id"] else by_id.get(i) for i in (sb, eb)]
                    ak.reroute(ac, ends[0], ends[1])
                    sets.setdefault(ac["id"], {}).update(x=ac["x"], y=ac["y"], width=ac["width"], height=ac["height"], points=ac["points"])
                    al = labels.get(ac["id"])
                    if al is not None:
                        tmp = {"width": float(al.get("width") or 0), "height": float(al.get("height") or 0)}
                        ak.label_on_arrow(ac, tmp)
                        sets.setdefault(al["id"], {}).update(x=tmp["x"], y=tmp["y"])
            payload = []
            for i, s_ in sets.items():
                item = {"id": i, "set": s_}
                if bases.get(i) is not None:
                    item["base_version"] = bases[i]
                if new_label is not None and i == target["id"]:
                    item["add_bound"] = [{"type": "text", "id": new_label["id"]}]
                payload.append(item)
            r = c.ok("POST", f"/api/whiteboard/boards/{bid}/patch", {"client": c.client, "changes": payload, "atomic": True})
            if r.get("conflict") or r.get("missing") or r.get("rejected"):
                res = {"id": eid, "status": "conflict" if r.get("conflict") else "failed", "conflict": r.get("conflict"),
                       "missing": r.get("missing"), "rejected": r.get("rejected")}
                if r.get("conflict"):
                    res["what"] = self._what_changed(c, bid, r["conflict"], target["id"], lab["id"] if lab is not None else None)
                results.append(res)
                continue
            if new_label is not None:
                c.ok("POST", f"/api/whiteboard/boards/{bid}/elements", {"client": c.client, "elements": [new_label]})
            own_ids += list(sets) + ([new_label["id"]] if new_label is not None else [])
            vers = {x["id"]: x["version"] for x in r.get("applied", [])}   # entries not changed (check only) keep their version
            results.append({"id": eid, "status": "ok", "version": vers.get(target["id"], target.get("version")),
                            "label_version": vers.get(lab["id"], lab.get("version")) if lab is not None else (new_label or {}).get("version")})
            touched.append(cur)
        if own_ids:
            self._own(c, bid, own_ids)
        if touched:
            self._pointer(c, bid, touched)
        lines = []
        for r in results:
            if r["status"] == "ok":
                lv = T(f' · 라벨 v{r["label_version"]}', f' · label v{r["label_version"]}') if r.get("label_version") is not None else ""
                lines.append(T(f'[{r["id"]}] 고침  (v{r["version"]}{lv})', f'[{r["id"]}] updated  (v{r["version"]}{lv})'))
            elif r["status"] == "conflict":
                lines.append(T(f'[{r["id"]}] 충돌 — 그사이 {r.get("what") or "누가 고쳤다"}. 바꾸지 않았다. board_read 로 다시 읽고, '
                               '사람의 변경을 덮을지는 사용자와 정한다.',
                               f'[{r["id"]}] conflict — meanwhile {r.get("what") or "someone edited it"}. Not changed. Re-read with board_read, and '
                               'decide with the user whether to overwrite the person\'s change.'))
            elif r["status"] == "refused" and refused_text(r.get("error")) is not None:
                lines.append(T(f'[{r["id"]}] 바꾸지 않았다 — ', f'[{r["id"]}] not changed — ') + refused_text(r["error"]).format(box=r.get("box")))
            elif r["status"] == "refused" and r.get("error") == "need_base":
                lines.append(T(f'[{r["id"]}] 바꾸지 않았다 — 글 · 색을 고칠 때는 base_version 이 필요하다. board_read 를 먼저 하고 '
                               '그 줄 끝 (vN · 라벨 vM) 의 N 을 base_version, 라벨이 있으면 M 을 label_base_version 으로 준다.',
                               f'[{r["id"]}] not changed — base_version is required when changing text or color. Run board_read first and '
                               'pass the N of (vN · label vM) at the end of that line as base_version, and M as label_base_version if there is a label.'))
            elif r["status"] == "refused":
                lines.append(T(f'[{r["id"]}] 바꾸지 않았다 — 라벨이 있는 상자의 글 · 색은 label_base_version 도 준다(board_read 줄 끝의 '
                               '"라벨 vM" 의 M). 사람이 라벨만 고친 것을 덮지 않게.',
                               f'[{r["id"]}] not changed — for text / color of a box with a label, also pass label_base_version (the M of '
                               '"label vM" at the end of the board_read line), so a person\'s label-only edit is not overwritten.'))
            elif r["status"] == "missing":
                lines.append(T(f'[{r["id"]}] 없다(지웠거나 id 가 틀림)', f'[{r["id"]}] not found (deleted or wrong id)'))
            else:
                lines.append(T(f'[{r["id"]}] 실패: {r.get("rejected") or r.get("missing")}', f'[{r["id"]}] failed: {r.get("rejected") or r.get("missing")}'))
        return "\n".join(lines), {"board": bid, "results": results}

    def _what_changed(self, c: Conn, bid: str, conflict: list, box_id: str, label_id: Optional[str]) -> str:
        """conflict entry → what changed and how, e.g. 'label changed (now label v2, text "human text")' (can be relayed to the user as is)."""
        try:
            now = {e.get("id"): e for e in (_fetch(c, bid).get("elements") or []) if isinstance(e, dict)}
        except ToolError:
            now = {}
        parts = []
        for cf in conflict:
            e = now.get(cf.get("id")) or {}
            gone = T(" — 지워졌다", " — deleted") if e.get("isDeleted") else ""
            if cf.get("id") == label_id:
                parts.append(T(f'라벨이 바뀌었다(지금 라벨 v{cf.get("version")}, 글 "{_txt(e)}"{gone}; 준 값 v{cf.get("base_version")})',
                               f'the label changed (now label v{cf.get("version")}, text "{_txt(e)}"{gone}; you gave v{cf.get("base_version")})'))
            elif cf.get("id") == box_id:
                word = _kind_word(e) if e else T("요소", "element")
                parts.append(T(f'{word}가 바뀌었다(지금 v{cf.get("version")}{gone}; 준 값 v{cf.get("base_version")})',
                               f'the {word} changed (now v{cf.get("version")}{gone}; you gave v{cf.get("base_version")})'))
            else:
                parts.append(T(f'[{cf.get("id")}] 가 바뀌었다(지금 v{cf.get("version")})', f'[{cf.get("id")}] changed (now v{cf.get("version")})'))
        return " · ".join(parts)

    def board_delete(self, a):
        ids = a.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 2000:
            raise ToolError(T("ids 는 1–2000개 목록 — 항목은 {id, base_version, label_base_version}",
                              "ids must be a list of 1-2000 — each item is {id, base_version, label_base_version}"))
        c, sid = self.s.conn(a.get("site"))
        bid = self.s.board_id(c, sid, a.get("board"))
        d = _fetch(c, bid)
        els = [e for e in d.get("elements") or [] if isinstance(e, dict)]
        by_id = {e["id"]: e for e in _live(els) if isinstance(e.get("id"), str)}
        labels = _labels(els)
        results = []
        n_del = n_clean = 0
        own_del: List[str] = []
        for n, it in enumerate(ids):
            if isinstance(it, str):
                it = {"id": it}
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                raise ToolError(T(f"ids[{n}] 는 {{id, base_version, label_base_version}}", f"ids[{n}] must be {{id, base_version, label_base_version}}"))
            eid = it["id"]
            e = by_id.get(eid)
            if e is None:
                results.append({"id": eid, "status": "missing"})
                continue
            if _is_bound_label(e, by_id):
                results.append({"id": eid, "status": "refused", "error": "label_id", "box": e["containerId"]})
                continue
            bv, lbv = _version(it.get("base_version")), _version(it.get("label_base_version"))
            if bv is BAD or lbv is BAD:
                results.append({"id": eid, "status": "refused", "error": "bad_version"})
                continue
            lab = labels.get(eid)
            if bv is None:
                results.append({"id": eid, "status": "refused", "error": "need_base"})
                continue
            if lab is not None and lbv is None:
                results.append({"id": eid, "status": "refused", "error": "need_label_base"})
                continue
            gone = {eid} | ({lab["id"]} if lab is not None else set())
            changes: Dict[str, dict] = {}

            def ch(i):
                return changes.setdefault(i, {"id": i, "set": {}})
            ch(eid)["set"]["isDeleted"] = True
            ch(eid)["base_version"] = bv
            if lab is not None:
                ch(lab["id"])["set"]["isDeleted"] = True
                ch(lab["id"])["base_version"] = lbv
            for o in by_id.values():   # cleanup (without a version — only release fields pointing at deleted elements)
                if o["id"] in gone:
                    continue
                if o.get("type") == "arrow":   # remaining arrow — release the end on the deleted side (it stays in place)
                    for end in ("startBinding", "endBinding"):
                        if (o.get(end) or {}).get("elementId") in gone:
                            ch(o["id"])["set"][end] = None
                if o.get("frameId") in gone:
                    ch(o["id"])["set"]["frameId"] = None
                drop = [b.get("id") for b in o.get("boundElements") or [] if isinstance(b, dict) and b.get("id") in gone]
                if drop:
                    ch(o["id"])["remove_bound"] = drop
                if o.get("type") == "text" and o.get("containerId") in gone:
                    ch(o["id"])["set"]["isDeleted"] = True
            r = c.ok("POST", f"/api/whiteboard/boards/{bid}/patch", {"client": c.client, "changes": list(changes.values()), "atomic": True})
            if r.get("conflict") or r.get("missing") or r.get("rejected"):
                res = {"id": eid, "status": "conflict" if r.get("conflict") else "failed", "conflict": r.get("conflict"),
                       "missing": r.get("missing"), "rejected": r.get("rejected")}
                if r.get("conflict"):
                    res["what"] = self._what_changed(c, bid, r["conflict"], eid, lab["id"] if lab is not None else None)
                results.append(res)
                continue
            dels = sorted(i for i, v in changes.items() if v["set"].get("isDeleted"))
            own_del += list(changes)
            n_del += len(dels)
            n_clean += len(changes) - len(dels)
            results.append({"id": eid, "status": "ok", "deleted": dels,
                            "cleaned": sorted(i for i, v in changes.items() if not v["set"].get("isDeleted"))})
        lines = []
        for r in results:
            if r["status"] == "ok":
                lines.append(T(f'[{r["id"]}] 지움' + (f' (라벨 포함 {len(r["deleted"])}개)' if len(r["deleted"]) > 1 else ""),
                               f'[{r["id"]}] deleted' + (f' ({len(r["deleted"])} including the label)' if len(r["deleted"]) > 1 else "")))
            elif r["status"] == "conflict":
                lines.append(T(f'[{r["id"]}] 지우지 않았다 — 그사이 {r.get("what") or "누가 고쳤다"}. 사용자에게 다시 묻는다.',
                               f'[{r["id"]}] not deleted — meanwhile {r.get("what") or "someone edited it"}. Ask the user again.'))
            elif r["status"] == "missing":
                lines.append(T(f'[{r["id"]}] 없다(이미 지웠거나 id 가 틀림)', f'[{r["id"]}] not found (already deleted or wrong id)'))
            elif refused_text(r.get("error")) is not None:
                lines.append(T(f'[{r["id"]}] 지우지 않았다 — ', f'[{r["id"]}] not deleted — ') + refused_text(r["error"]).format(box=r.get("box")))
            elif r.get("error") == "need_base":
                lines.append(T(f'[{r["id"]}] 지우지 않았다 — 지울 때도 판이 필요하다: board_read 줄 끝 (vN · 라벨 vM) 의 N 을 base_version 으로 '
                               '(라벨이 있으면 M 을 label_base_version 으로) 준다. 읽은 뒤 사람이 고쳤으면 지우지 않고 알려 준다.',
                               f'[{r["id"]}] not deleted — a version is needed to delete too: pass the N of (vN · label vM) at the end of the board_read line '
                               'as base_version (and M as label_base_version if there is a label). If a person edited it after you read it, do not delete — tell them.'))
            elif r.get("error") == "need_label_base":
                lines.append(T(f'[{r["id"]}] 지우지 않았다 — 라벨이 함께 지워지므로 label_base_version(줄 끝 "라벨 vM" 의 M)도 준다.',
                               f'[{r["id"]}] not deleted — the label is deleted with it, so also pass label_base_version (the M of "label vM" at the end of the line).'))
            else:
                lines.append(T(f'[{r["id"]}] 실패: {r.get("rejected") or r.get("missing")}', f'[{r["id"]}] failed: {r.get("rejected") or r.get("missing")}'))
        if own_del:
            self._own(c, bid, own_del)
        if n_clean:
            lines.append(T(f"이어진 {n_clean}개의 바인딩 · 장 연결을 정리했다.", f"Cleaned up {n_clean} connected binding(s) / page link(s)."))
        deleted = [i for r in results if r["status"] == "ok" for i in r["deleted"]]
        return "\n".join(lines), {"board": bid, "results": results, "deleted": deleted,
                                  "missing": [r["id"] for r in results if r["status"] == "missing"]}

    def board_wait(self, a, cancelled=lambda: False, progress=None):
        """Wait for people's edits. idle_ms > 0: after the first change keep collecting until nobody changed anything for idle_ms
        (a person drawing 18 strokes wakes the agent once). kinds filters what counts (ink · text · shape · any).
        Other agents' writes and non-board events (tab-render jobs) never count.
        progress(done, total, message): called every WAIT_PROGRESS_S from a ticker thread (when the client sent a progressToken)."""
        t0 = time.time()   # the cap counts from the call, not from after the connection is set up (the client's timer started already)
        cap = wait_cap(self.s.client_name)
        timeout = min(float(cap), _num(a.get("timeout_s", min(WAIT_DEFAULT, cap)), "timeout_s", 1, 3600))
        idle = _num(a.get("idle_ms", 0), "idle_ms", 0, 30000) / 1000.0
        kinds = a.get("kinds", ["any"])
        if isinstance(kinds, str):
            kinds = [kinds]
        if not isinstance(kinds, list) or not kinds or any(k not in WAIT_KINDS for k in kinds):
            raise ToolError(T(f"kinds 는 {' · '.join(WAIT_KINDS)} 중에서 고른 목록", f"kinds must be a list chosen from {', '.join(WAIT_KINDS)}"))
        kinds = set(kinds)
        deadline = t0 + timeout
        stop = threading.Event()
        if progress is not None:
            def tick():
                n = 1
                while not stop.wait(max(0.0, t0 + n * WAIT_PROGRESS_S - time.time())):
                    if time.time() >= deadline:
                        return
                    try:
                        progress(n * WAIT_PROGRESS_S, int(timeout), T("보드를 지켜보는 중", "watching the board"))
                    except Exception:   # noqa: BLE001 — progress is best effort
                        return
                    n += 1
            threading.Thread(target=tick, daemon=True).start()
        try:
            return self._wait(a, kinds, idle, timeout, deadline, cancelled)
        finally:
            stop.set()

    def _wait(self, a, kinds, idle, timeout, deadline, cancelled):
        c, sid = self.s.conn(a.get("site"))
        bid = self.s.board_id(c, sid, a.get("board"))
        d = c.ok("GET", f"/api/whiteboard/boards/{bid}", timeout=max(1.0, min(30.0, deadline - time.time())))   # bounded by the cap too
        if not isinstance(d, dict):
            raise ToolError(T("보드를 읽지 못했다", "Could not read the board"))
        now_seq = int(d.get("seq") or 0)
        epoch = d.get("epoch") or ""
        since = a.get("since_seq")
        since = int(_num(since, "since_seq", 0, 1e15)) if since is not None else now_seq
        base = None
        from_old = False
        if since < now_seq:   # replaying from an older seq: compare with the board as this session saw it at that seq
            base = self.s.seen_at(c.base, bid, epoch, since)
            from_old = base is None
        if base is None:
            base = {e["id"]: e for e in d.get("elements") or [] if isinstance(e, dict) and isinstance(e.get("id"), str)}
        pver, names, agents_ = -1, {}, set()
        acc: Dict[str, list] = {}   # id → [latest element, first who, other editors]
        other: List[str] = []
        last_change = 0.0
        while True:
            now = time.time()
            left = deadline - now
            if cancelled():
                left = 0
            if acc or other:
                if idle <= 0 or now - last_change >= idle or left <= 0:
                    res = self._wait_result(c, bid, since, epoch, base, acc, other, from_old, unsettled=idle > 0 and now - last_change < idle)
                    if res is not None:
                        return res
                    acc, other = {}, []   # only things that cancel out (added then deleted) — keep waiting
                    continue
                span = min(left, idle - (now - last_change))
            elif left <= 0:
                self.s.remember(c.base, bid, epoch, since, base)
                return T(f"{int(timeout)}초 동안 바뀐 것이 없다.", f"Nothing changed for {int(timeout)} seconds."), \
                    {"board": bid, "seq": since, "epoch": epoch, "changes": [], "timeout": True}
            else:
                span = left
            span = max(0.05, min(span, 20))
            q = urllib.parse.urlencode({"since": since, "client": c.client, "pver": pver, "epoch": epoch, "wait": round(span, 2)})
            try:   # the HTTP timeout never runs past the deadline by more than a moment (a stuck server must not break the client's tool timeout)
                r = c.ok("GET", f"/api/whiteboard/boards/{bid}/poll?{q}", timeout=max(0.5, min(span + 10, deadline - time.time() + 1.0)))
            except ToolError:
                if time.time() < deadline - 0.5:
                    raise
                r = {}
            for p in r.get("presence") or []:
                names[p.get("client")] = p.get("user")
                if p.get("agent"):
                    agents_.add(p.get("client"))
            pver = r.get("pver", pver)
            if r.get("reset"):
                d2 = _fetch(c, bid)
                text, st = ak.describe(d2.get("elements") or [])
                return T("보드를 처음부터 다시 받았다(서버가 다시 켜졌거나 기록이 밀렸다).\n", "Re-fetched the board from scratch (the server restarted or the log was pushed out).\n") + text, \
                    {"board": bid, "seq": d2.get("seq"), "epoch": d2.get("epoch"), "reset": True, "changes": []}
            since = int(r.get("seq", since))
            now = time.time()
            for ev in r.get("events") or []:   # only board events: elements / meta. 'job' (tab render queue) and anything else is ignored
                if ev.get("event") == "elements":
                    data = ev.get("data") or {}
                    frm = data.get("from")
                    who = names.get(frm) or T("누군가", "someone")
                    for el in data.get("elements") or []:
                        if not isinstance(el, dict) or not isinstance(el.get("id"), str):
                            continue
                        eid = el["id"]
                        if frm in agents_ or (isinstance(frm, str) and frm.startswith("ag-")):
                            if eid not in acc:
                                base[eid] = el   # another agent's write — not a person's edit
                            elif not el.get("isDeleted"):
                                acc[eid][0] = el
                            continue
                        if eid not in acc and self._echo(eid, el, frm):
                            base[eid] = el   # smoothing re-write of a stroke already reported — not news
                            continue
                        if "any" not in kinds and _wait_kind(el) not in kinds:
                            if eid not in acc:
                                base[eid] = el
                            continue
                        ent = acc.get(eid)
                        if ent is None:
                            acc[eid] = [el, who, frm, []]
                        else:
                            prev = ent[0]
                            if frm != ent[2] and who not in ent[3] and who != ent[1]:
                                ent[3].append(who)
                            ent[0] = el
                            if frm == ent[2] and _is_echo(prev, el):
                                continue   # the smoothing pass inside the window does not extend it
                        last_change = now
                elif ev.get("event") == "meta" and "any" in kinds:
                    other.append(T(f'보드 이름이 "{(ev.get("data") or {}).get("title")}" 로 바뀌었다',
                                   f'The board title changed to "{(ev.get("data") or {}).get("title")}"'))
                    last_change = now

    def _echo(self, eid: str, el: dict, frm) -> bool:
        """The stroke was reported as added (same connection), and this re-write is the app's smoothing pass:
        same position, at most ECHO_S later by the element's own clock (not when we happen to read it)."""
        rec = self.s.recent_ink.get(eid)
        return bool(rec and rec[0] == frm and _is_echo(rec[1], el))

    def _wait_result(self, c, bid, since, epoch, base, acc, other, from_old, unsettled=False):
        """Lines for the collected changes, or None when nothing is left to report (base is then updated)."""
        out, lines = [], []
        per: Dict[str, Dict[str, int]] = {}   # who → counts for the first line
        boxes = []
        strokes: List[str] = []
        stroke_els: List[dict] = []
        for eid, (el, who, frm, others) in acc.items():
            old = base.get(eid)
            kind = _kind_word(el)
            cnt = per.setdefault(who, {})
            also = T(f" (뒤에 {', '.join(others)} 가 고침)", f" (then edited by {', '.join(others)})") if others else ""
            if el.get("isDeleted"):
                if old is None or old.get("isDeleted"):
                    continue
                act = "지움"
                line = T(f'{who} 가 [{eid}] {kind}{" " + chr(34) + _txt(old) + chr(34) if _txt(old) else ""} 를 지움',
                         f'{who} deleted [{eid}] {kind}{" " + chr(34) + _txt(old) + chr(34) if _txt(old) else ""}')
                boxes.append(old)
            elif old is None or old.get("isDeleted"):
                act = "추가"
                line = T(f'{who} 가 [{eid}] {kind}{" " + chr(34) + _txt(el) + chr(34) if _txt(el) else ""} 를 넣음',
                         f'{who} added [{eid}] {kind}{" " + chr(34) + _txt(el) + chr(34) if _txt(el) else ""}') + also
                boxes.append(el)
            elif el.get("type") == "text" and _txt(el) != _txt(old):
                act = "글 고침"
                tgt = T(f' ([{el["containerId"]}] 의 라벨)', f' (label of [{el["containerId"]}])') if el.get("containerId") else ""
                line = T(f'{who} 가 [{eid}] {kind}{tgt} 를 "{_txt(old)}" → "{_txt(el)}" 로 고침',
                         f'{who} changed [{eid}] {kind}{tgt} from "{_txt(old)}" to "{_txt(el)}"') + also
                boxes.append(el)
            elif any(el.get(k) != old.get(k) for k in ("x", "y")):
                act = "옮김"
                line = T(f'{who} 가 [{eid}] {kind} 를 옮김', f'{who} moved [{eid}] {kind}') + also
                boxes.append(el)
            elif any(el.get(k) != old.get(k) for k in ("width", "height")):
                act = "크기"
                line = T(f'{who} 가 [{eid}] {kind} 크기를 바꿈', f'{who} resized [{eid}] {kind}') + also
                boxes.append(el)
            else:
                act = "고침"
                line = T(f'{who} 가 [{eid}] {kind} 를 고침', f'{who} edited [{eid}] {kind}') + also
                if from_old and _txt(el):   # no record of the board at since_seq: compared with the current board — show the current text
                    line += T(f' — 지금 "{_txt(el)}"', f' — now "{_txt(el)}"')
                    if el.get("containerId"):
                        line += T(f' ([{el["containerId"]}] 의 라벨)', f' (label of [{el["containerId"]}])')
                boxes.append(el)
            if act == "추가":
                k = _wait_kind(el)
                cnt[k] = cnt.get(k, 0) + 1
            else:
                cnt[act] = cnt.get(act, 0) + 1
            if el.get("type") == "freedraw" and act == "추가" and not others:
                strokes.append(eid)
                stroke_els.append(el)
                continue   # handwriting strokes are counted together below
            lines.append(line)
            out.append({"id": eid, "type": el.get("type"), "action": ACT_WORD(act), "by": who,
                        "text": _txt(el) if el.get("type") == "text" else None})
        for eid, ent in acc.items():
            base[eid] = ent[0]
        if strokes:
            bb = ak.scene_bbox(stroke_els)
            lines.append(T(f"손글씨 획 {len(strokes)}개가 늘었다 {ak.at_text(bb)} — 글로 읽을 수 없음, board_snapshot 으로 본다",
                           f"{len(strokes)} ink stroke(s) were added {ak.at_text(bb)} — not readable as text, look with board_snapshot"))
            out.append({"action": ACT_WORD("손글씨"), "count": len(strokes), "ids": strokes, "bbox": ak._fmt_box(bb) if bb else None})
            now = time.time()
            for eid in strokes:
                self.s.recent_ink[eid] = (acc[eid][2], acc[eid][0], now)
            self.s.recent_ink = {k: v for k, v in self.s.recent_ink.items() if now - v[2] <= 600}
        lines += other
        if not lines:
            return None
        if from_old:
            lines.insert(0, T("(since_seq 때의 보드를 이 세션이 모른다 — 지금 보드와 비교했다. 그사이 바뀐 것은 '고침' 으로 보일 수 있다)",
                              "(this session has no record of the board at since_seq — compared with the current board; changes in between may show as 'edited')"))
        heads = []
        for who, cnt in per.items():
            add = [(T("획", "ink"), cnt.get("ink", 0)), (T("글", "text"), cnt.get("text", 0)), (T("도형", "shape"), cnt.get("shape", 0))]
            add_s = " · ".join(f"{w} {n}" for w, n in add if n)
            edit_n = sum(v for k, v in cnt.items() if k not in ("ink", "text", "shape", "지움"))
            parts = []
            if add_s:
                parts.append(T(f"{add_s} 을 더함", f"added {add_s}"))
            if edit_n:
                parts.append(T(f"{edit_n}개 고침", f"edited {edit_n}"))
            if cnt.get("지움"):
                parts.append(T(f"{cnt['지움']}개 지움", f"deleted {cnt['지움']}"))
            if parts:
                heads.append(T(f"{who} 가 ", f"{who}: ") + ", ".join(parts))
        bb = ak.scene_bbox(boxes)
        summary = " / ".join(heads) + (" " + ak.at_text(bb) if bb else "")
        tail = T(f"\n(seq {since} — 이어서 기다리려면 since_seq 로 준다)", f"\n(seq {since} — to keep waiting, pass it as since_seq)")
        if unsettled:
            tail = T(f"\n(시간 상한에 닿았다 — 아직 그리는 중일 수 있다. 이어서 기다리려면 since_seq={since} 로 — 위 변경은 다시 알리지 않는다)",
                     f"\n(the time cap was reached — the person may still be drawing; to keep waiting pass since_seq={since} — the changes above are not repeated)") + tail
        self.s.remember(c.base, bid, epoch, since, base)
        st = {"board": bid, "seq": since, "epoch": epoch, "changes": out, "summary": summary or None,
              "bbox": ak._fmt_box(bb) if bb else None}
        if unsettled:
            st["settled"] = False
        return (summary + "\n" if heads else "") + "\n".join(lines) + tail, st

    def board_export(self, a):
        c, sid = self.s.conn(a.get("site"))
        bid = self.s.board_id(c, sid, a.get("board"))
        d = _fetch(c, bid)
        els = _live(d.get("elements") or [])
        files = {}
        for e in els:
            fid = e.get("fileId")
            if isinstance(fid, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", fid) and fid not in files:
                code, rec = c.req("GET", f"/api/whiteboard/boards/{bid}/files/{fid}")
                if code == 200 and isinstance(rec, dict):
                    files[fid] = rec
        title = (d.get("meta") or {}).get("title") or bid
        out_dir = Path(os.path.expanduser(a["out_dir"])) if a.get("out_dir") else self.s.ctx.HOME / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w가-힣.-]+", "_", title).strip("_")[:60] or bid
        p = out_dir / f"{name}-{bid}.excalidraw"
        doc = {"type": "excalidraw", "version": 2, "source": "cera", "elements": els,
               "appState": {"viewBackgroundColor": "#ffffff"}, "files": files}
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return T(f"{p} 에 저장했다(요소 {len(els)} · 그림 {len(files)}).", f"Saved to {p} (elements {len(els)} · images {len(files)})."), {"path": str(p), "elements": len(els), "files": len(files)}

    def board_import(self, a):
        """This computer's .excalidraw, .excalidrawlib and image files — merged into a board (merge) or as a new board (new)."""
        mode = a.get("mode", "merge")
        if mode not in ("merge", "new"):
            raise ToolError(T('mode 는 "merge" · "new"', 'mode must be "merge" or "new"'))
        at = a.get("at")
        if at is not None and at not in ("right", "below") and not (isinstance(at, dict) and set(at) <= {"x", "y"}):
            raise ToolError(T('at 은 "right" · "below" · {x, y}(묶음 가운데)', 'at must be "right", "below" or {x, y} (center of the group)'))
        try:
            name, data = importers.read_path(a.get("path"))
            parsed = importers.parse(name, data)
        except importers.ImportFailed as e:
            raise ToolError(str(e))
        c, sid = self.s.conn(a.get("site"))
        bid = None
        if mode == "merge":
            bid = self.s.board_id(c, sid, a.get("board"))
        elif a.get("board"):
            raise ToolError(T('mode:"new" 에는 board 를 주지 않는다(새 보드를 만든다)', 'Do not give board with mode:"new" (it creates a new board)'))
        title = a.get("title")
        if title is not None:
            title = _str(title, "title", 80)
        try:
            res = importers.push(c.req, parsed, mode, bid, title, at, client=c.client, name=name)
        except importers.ImportFailed as e:
            raise ToolError(str(e))
        if res.get("bbox") and mode == "merge":
            b = res["bbox"]
            self._pointer(c, res["board"], [{"type": "rectangle", "x": b[0], "y": b[1], "width": b[2] - b[0], "height": b[3] - b[1]}])
        if res.get("board"):
            self.s.forget(c.base, res["board"])   # imported ids are not listed here — fall back to comparing with the current board
        st = {k: res.get(k) for k in ("board", "added", "skipped", "files", "missing_files", "bbox", "kind")}
        st["mode"] = mode
        return importers.summary(res) + ".", st


# ------------------------------------------------------------------ Tool list (schemas)
def _schemas():
    """Shared schema pieces. Built per call so that descriptions follow the language read at that moment."""
    site = {"type": "string", "description": T("사이트 id(site_list). 열린 사이트가 하나면 생략", "Site id (site_list). Omit if exactly one site is open")}
    board = {"type": "string", "description": T("보드 id(board_list). 사이트의 첫 보드면 생략", "Board id (board_list). Omit for the site's first board")}
    item = {
        "type": "object",
        "description": T("kind 별 칸(그 밖의 칸은 거절) — text{text,x?,y?,size?,color?,width?} · box{ref?,shape?(rectangle|ellipse|diamond),label?(또는 text),x?,y?,w?,h?,"
                         "fill?,color?} · arrow{from,to(ref|요소 id|{x,y}),label?,dashed?,color?,head?(end 기본|start|both|none — none 은 방향 없는 선)} · "
                         "line{points:[[x,y],…],dashed?,color?}(붙지 않는 꺾은선) · frame_page{title?}(새 장 — 뒤 항목은 그 안) · "
                         "image{path,w?,x?,y?}. x/y 는 묶음 안 자리(생략하면 왼→오로 늘어놓음). math · mermaid 는 아직 안 된다",
                         "Fields by kind (other fields are refused) — text{text,x?,y?,size?,color?,width?} · box{ref?,shape?(rectangle|ellipse|diamond),label?(or text),x?,y?,w?,h?,"
                         "fill?,color?} · arrow{from,to(ref|element id|{x,y}),label?,dashed?,color?,head?(end default|start|both|none — none is an undirected edge)} · "
                         "line{points:[[x,y],…],dashed?,color?} (an unbound polyline) · frame_page{title?} (a new page — the items after it go inside) · "
                         "image{path,w?,x?,y?}. x/y is the position within the group (if omitted, laid out left to right). math and mermaid are not supported yet"),
        "properties": {
            "kind": {"type": "string", "enum": ["text", "box", "arrow", "line", "frame_page", "image", "math", "mermaid"]},
            "head": {"type": "string", "enum": list(ak.ARROW_HEADS), "description": T("arrow 화살촉(기본 end)", "arrow heads (default end)")},
            "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                       "minItems": 2, "maxItems": 500, "description": T("line 의 점(묶음 안 자리)", "line points (positions within the group)")},
            "ref": {"type": "string", "description": T("이번 호출 안에서 화살표가 가리킬 이름", "A name arrows can point to within this call")},
            "text": {"type": "string"}, "label": {"type": "string"}, "title": {"type": "string"},
            "shape": {"type": "string", "enum": ["rectangle", "ellipse", "diamond"]},
            "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"}, "h": {"type": "number"},
            "width": {"type": "number", "description": T("글 줄바꿈 폭", "Text wrapping width")},
            "size": {"type": "number", "description": T("글 크기(기본 20)", "Text size (default 20)")},
            "color": {"type": "string"}, "fill": {"type": "string"}, "dashed": {"type": "boolean"},
            "from": {"description": T("ref · 요소 id · {x,y}", "ref, element id or {x,y}")}, "to": {"description": T("ref · 요소 id · {x,y}", "ref, element id or {x,y}")},
            "path": {"type": "string", "description": T("이 컴퓨터의 그림 파일(png · jpg · gif · webp · svg)", "An image file on this computer (png, jpg, gif, webp, svg)")},
        },
        "required": ["kind"],
    }
    return site, board, item


def tool_defs(remote: bool) -> List[dict]:
    SITE, BOARD, ITEM = _schemas()

    def obj(props, req=()):
        props = dict(props)
        if remote:
            props.pop("site", None)
        return {"type": "object", "properties": props, "required": [r for r in req if r in props], "additionalProperties": False}
    ro = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
    w = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
    defs = [
        {"name": "site_list", "title": T("사이트 목록", "List sites"),
         "description": T("이 컴퓨터의 Cera 사이트(열림 · 닫힘)와 열린 사이트의 초대 링크.",
                          "The Cera sites on this computer (open / closed) and the invite links of open sites."),
         "inputSchema": obj({}), "annotations": ro},
        {"name": "site_open", "title": T("사이트 열기", "Open a site"),
         "description": T("새 화이트보드 사이트를 연다(첫 보드 포함). 기본은 이 컴퓨터에서만(tunnel:\"none\"). "
                         "tunnel:\"cloudflared\" 는 인터넷 공개 링크다 — 사용자가 공개 링크를 원할 때만, 먼저 물어 본다.",
                         "Opens a new whiteboard site (with its first board). By default only on this computer (tunnel:\"none\"). "
                         "tunnel:\"cloudflared\" is a public internet link — only when the user wants a public link, and ask first."),
         "inputSchema": obj({"title": {"type": "string", "description": T("첫 보드 이름(기본 '보드')", "Name of the first board (default 'Board')")},
                             "ttl": {"type": "string", "description": T("열어 둘 시간(30m · 3h · 2d). 기본 12h", "How long to keep it open (30m, 3h, 2d). Default 12h")},
                             "tunnel": {"type": "string", "enum": ["none", "cloudflared"], "default": "none"},
                             "view_link": {"type": "boolean", "description": T("보기 전용 링크도", "Also a view-only link")},
                             "lan": {"type": "boolean", "description": T("같은 와이파이에서 들어오게", "Let people on the same Wi-Fi join")}}),
         "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}},
        {"name": "site_close", "title": T("사이트 닫기", "Close a site"),
         "description": T("사이트를 닫는다(그림은 남는다). 사람이 쓰는 중일 수 있으니 먼저 묻는다.",
                          "Closes the site (the drawings are kept). People may be using it, so ask first."),
         "inputSchema": obj({"site": SITE}), "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}},
        {"name": "board_list", "title": T("보드 목록", "List boards"),
         "description": T("사이트의 보드(id · 이름 · 요소 수 · 접속 수).", "The site's boards (id, name, element count, online count)."),
         "inputSchema": obj({"site": SITE}), "annotations": ro},
        {"name": "board_create", "title": T("보드 만들기", "Create a board"),
         "description": T("사이트에 새 보드를 만든다.", "Creates a new board in the site."),
         "inputSchema": obj({"site": SITE, "title": {"type": "string"}}, ["title"]), "annotations": w},
        {"name": "board_read", "title": T("보드 읽기", "Read a board"),
         "description": T("보드를 읽는다. summary(기본): 장 순서 · 위→아래로 상자 · 글 · 수식 · 연결(\"A\" → \"B\") · 손글씨 획 수와 "
                          "[id] 와 줄 끝 (vN · 라벨 vM) — 고칠 때 이 id 와 판을 쓴다. elements: 원래 요소 JSON(ids · page 로 좁힌다).",
                          "Reads the board. summary (default): in page order, top to bottom — boxes, text, math, arrows (\"A\" → \"B\"), ink stroke counts, "
                          "with the [id] and (vN · label vM) at the end of each line — use that id and version when editing. elements: the raw element JSON (narrow with ids / page)."),
         "inputSchema": obj({"site": SITE, "board": BOARD, "format": {"type": "string", "enum": ["summary", "elements"], "default": "summary"},
                             "page": {"type": "integer", "description": T("장 번호(판서 장)만", "Only this page number (a board page)")},
                             "ids": {"type": "array", "items": {"type": "string"}},
                             "limit": {"type": "integer", "default": READ_LIMIT, "maximum": 2000, "description": T("줄 · 요소 수 상한", "Maximum number of lines / elements")},
                             "offset": {"type": "integer", "default": 0, "description": T("elements: 이어 읽을 자리(next_offset)", "elements: where to continue reading (next_offset)")},
                             "points": {"type": "boolean", "description": T("elements: 손글씨 · 선의 점까지(크다)", "elements: include the points of ink / lines (large)")}}),
         "annotations": ro},
        {"name": "board_add", "title": T("보드에 그리기", "Draw on a board"),
         "description": T("상자 · 글 · 화살표 · 새 장 · 그림을 한 묶음으로 넣는다. 사람 화면에 바로 보인다. at: 묶음을 놓을 곳 — "
                          "\"right\"(기존 그림 오른쪽, 기본) · \"below\" · {x,y} · \"absolute\"(x/y 를 보드 좌표로). page: 그 장 안에.",
                          "Adds boxes, text, arrows, a new page and images as one group. It shows on people's screens right away. at: where to put the group — "
                          "\"right\" (right of the existing drawing, default), \"below\", {x,y} or \"absolute\" (x/y as board coordinates). page: inside that page."),
         "inputSchema": obj({"site": SITE, "board": BOARD, "items": {"type": "array", "items": ITEM, "minItems": 1},
                             "at": {"description": "\"right\" | \"below\" | \"absolute\" | {x, y}"},
                             "page": {"type": "integer"}}, ["items"]),
         "annotations": w},
        {"name": "board_update", "title": T("보드 고치기", "Edit a board"),
         "description": T("요소를 고친다(글 · 라벨 · 자리 · 크기 · 색). 서버가 지금 상태에 적용한다. base_version(board_read 의 version)을 주면 "
                          "그사이 사람이 고친 요소는 바꾸지 않고 conflict 로 돌려준다. 글(text) · 색(color · fill)을 바꿀 때는 base_version 이 "
                          "꼭 필요하고(없으면 refused), 라벨 있는 상자 · 화살표면 label_base_version 도 필요하다 — board_read 줄 끝 "
                          "(vN · 라벨 vM) 의 N · M. 자리 · 크기(x · y · dx · dy · w · h)만 바꿀 때는 없어도 된다. 라벨 id 는 받지 않는다 — "
                          "라벨 글도 상자 id 로 고친다. "
                          "상자를 옮기면 라벨 · 이어진 화살표가 따라온다.",
                          "Edits elements (text, label, position, size, color). The server applies it to the current state. If you pass base_version (the version from board_read), "
                          "an element a person edited in the meantime is not changed and comes back as conflict. When changing text or color (color / fill), base_version is "
                          "required (otherwise refused), and for a box / arrow with a label label_base_version is required too — the N and M of the "
                          "(vN · label vM) at the end of the board_read line. It may be omitted when only changing position / size (x, y, dx, dy, w, h). Label ids are not accepted — "
                          "edit label text through the box id. "
                          "When a box is moved, its label and connected arrows follow."),
         "inputSchema": obj({"site": SITE, "board": BOARD, "changes": {"type": "array", "minItems": 1, "items": {
             "type": "object", "required": ["id"],
             "properties": {"id": {"type": "string", "description": T("상자 · 글 · 화살표 id(라벨 id 는 안 됨 — 상자 id 로)", "Box / text / arrow id (not a label id — use the box id)")},
                            "base_version": {"type": "integer", "description": T("board_read 줄 끝 (vN) 의 N(정수). text · color · fill 을 바꿀 때 필수. 그사이 바뀌었으면 conflict",
                                                                                 "The N (integer) of (vN) at the end of the board_read line. Required when changing text / color / fill. If it changed in the meantime: conflict")},
                            "label_base_version": {"type": "integer", "description": T("board_read 줄 끝 (라벨 vM) 의 M. 라벨 있는 상자 · 화살표의 text · color 를 바꿀 때 필수",
                                                                                       "The M of (label vM) at the end of the board_read line. Required when changing text / color of a box / arrow with a label")},
                            "text": {"type": "string", "description": T("글이면 그 글, 상자 · 화살표면 라벨", "The text for a text element, the label for a box / arrow")},
                            "x": {"type": "number"}, "y": {"type": "number"}, "dx": {"type": "number"}, "dy": {"type": "number"},
                            "w": {"type": "number"}, "h": {"type": "number"}, "color": {"type": "string"}, "fill": {"type": "string"}}}}},
                            ["changes"]),
         "annotations": w},
        {"name": "board_delete", "title": T("보드에서 지우기", "Delete from a board"),
         "description": T("요소를 지운다(라벨 · 이어진 화살표 바인딩까지 정리). 다른 사람의 그림일 수 있으니 사용자에게 먼저 묻는다. "
                          "항목마다 board_read 줄 끝 (vN · 라벨 vM) 의 판을 준다: base_version=N(필수), 라벨이 있으면 label_base_version=M(필수). "
                          "읽은 뒤 사람이 고쳤으면 지우지 않고 conflict 로 무엇이 바뀌었는지 돌려준다. 라벨 id 는 받지 않는다(상자 id 로).",
                          "Deletes elements (also cleans up labels and the bindings of connected arrows). It may be someone else's drawing, so ask the user first. "
                          "For each item pass the version from the (vN · label vM) at the end of the board_read line: base_version=N (required), and label_base_version=M if there is a label (required). "
                          "If a person edited it after you read it, it is not deleted and conflict says what changed. Label ids are not accepted (use the box id)."),
         "inputSchema": obj({"site": SITE, "board": BOARD, "ids": {"type": "array", "minItems": 1, "items": {
             "type": "object", "required": ["id", "base_version"],
             "properties": {"id": {"type": "string", "description": T("상자 · 글 · 화살표 등의 id(라벨 id 는 안 됨)", "Id of a box / text / arrow etc. (not a label id)")},
                            "base_version": {"type": "integer", "description": T("board_read 줄 끝 (vN) 의 N", "The N of (vN) at the end of the board_read line")},
                            "label_base_version": {"type": "integer", "description": T("라벨이 있으면 줄 끝 (라벨 vM) 의 M", "If there is a label, the M of (label vM) at the end of the line")}}}}}, ["ids"]),
         "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}},
        {"name": "board_wait", "title": T("사람이 고칠 때까지 기다리기", "Wait until a person edits"),
         "description": T("사람이 보드를 고칠 때까지 기다렸다가 무엇이 바뀌었는지 돌려준다 — 첫 줄은 요약(누가 무엇을 더함 @(x,y w×h)), 이어서 한 줄씩. "
                          "since_seq 는 board_read · 앞 board_wait 의 seq(없으면 지금부터). idle_ms: 첫 변경 뒤 이만큼 아무도 안 고칠 때까지 모아서 "
                          "한 번에 돌려준다(사람이 다 그릴 때까지 — 3000 권장). kinds: 셀 변경(ink · text · shape · any). "
                          "timeout_s 상한은 클라이언트마다: Claude Code 600 · Codex 50 · 그 밖 55(CERA_WAIT_MAX 로 바꿈). 시간이 다 되면 timeout:true.",
                          "Waits until a person edits the board, then returns what changed — the first line is a summary (who added what @(x,y w×h)), then one line each. "
                          "since_seq is the seq from board_read or the previous board_wait (if omitted, from now). idle_ms: after the first change, keep collecting until "
                          "nobody edits for this long, then return once (until the person finishes drawing — 3000 recommended). kinds: which changes count (ink, text, shape, any). "
                          "The timeout_s cap depends on the client: Claude Code 600, Codex 50, others 55 (override with CERA_WAIT_MAX). When time runs out: timeout:true."),
         "inputSchema": obj({"site": SITE, "board": BOARD, "since_seq": {"type": "integer"},
                             "timeout_s": {"type": "integer", "default": WAIT_DEFAULT, "minimum": 1, "maximum": WAIT_SCHEMA_MAX},
                             "idle_ms": {"type": "integer", "default": 0, "minimum": 0, "maximum": 30000},
                             "kinds": {"type": "array", "items": {"type": "string", "enum": list(WAIT_KINDS)}, "minItems": 1}}), "annotations": ro},
        {"name": "board_import", "title": T("파일 가져오기", "Import a file"),
         "description": T("이 컴퓨터의 .excalidraw · .excalidrawlib · 그림(png · jpg · gif · webp · svg) 파일을 보드에 넣는다(50 MB 까지, "
                          "URL 은 안 된다). mode:\"merge\"(기본)는 지금 보드에 합친다 — id 를 새로 매기고 기존 그림 오른쪽(at)에 놓는다. "
                          "mode:\"new\" 는 새 보드를 만든다. 돌려주는 것: {board, added, skipped}.",
                          "Puts a .excalidraw, .excalidrawlib or image (png, jpg, gif, webp, svg) file from this computer into a board (up to 50 MB, "
                          "no URLs). mode:\"merge\" (default) merges into the current board — new ids are assigned and it is placed to the right (at) of the existing drawing. "
                          "mode:\"new\" creates a new board. Returns: {board, added, skipped}."),
         "inputSchema": obj({"site": SITE, "board": BOARD, "path": {"type": "string", "description": T("가져올 파일의 경로(절대 경로 권장, '..' 금지)", "Path of the file to import (absolute path recommended, no '..')")},
                             "mode": {"type": "string", "enum": ["merge", "new"], "default": "merge"},
                             "title": {"type": "string", "description": T("mode:new 의 보드 이름(기본: 파일 이름)", "Board name for mode:new (default: the file name)")},
                             "at": {"description": T("merge 자리 — \"right\"(기본) · \"below\" · {x, y}(묶음 가운데)", "merge position — \"right\" (default), \"below\" or {x, y} (center of the group)")}}, ["path"]),
         "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
        {"name": "board_export", "title": T(".excalidraw 로 저장", "Save as .excalidraw"),
         "description": T("보드를 .excalidraw 파일로 저장한다(기본 $CERA_HOME/exports). excalidraw.com 에서 열린다.",
                          "Saves the board as a .excalidraw file (default $CERA_HOME/exports). It opens on excalidraw.com."),
         "inputSchema": obj({"site": SITE, "board": BOARD, "out_dir": {"type": "string"}}), "annotations": w},
    ]
    if remote:
        defs = [t for t in defs if not t["name"].startswith("site_")]
    return defs


_REMOTE_HELP_KO = """원격(다른 컴퓨터의 보드): CERA_URL=<보드 주소> 와 아래 둘 중 하나(또는 둘 다)를 주면 site_* 도구를 숨기고 그 주소만 쓴다.
  - CERA_TOKEN=tb_ag_…  : 에이전트 토큰(`cera agent add <사이트> --name …`). 임시 보드(quick tunnel)용.
  - CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET : Cloudflare Access 서비스 토큰(개인 보드 웹 · Access 모드).
    CF-Access-Client-Id / CF-Access-Client-Secret 머리로 보낸다. (CERA_CF_CLIENT_ID/SECRET 도 받는다.)
    Access 모드에서는 에이전트 토큰이 이 맥 밖 요청에 쓰이지 않으므로 CERA_TOKEN 없이 서비스 토큰만 주면 된다.
  CERA_URL 만 있고 둘 다 없으면 시작할 때 stderr 에 경고하고, 보드 도구는 오류로 안내한다."""
_REMOTE_HELP_EN = """Remote (a board on another computer): give CERA_URL=<board address> plus one of the following (or both) and the site_* tools are hidden and only that address is used.
  - CERA_TOKEN=tb_ag_…  : an agent token (`cera agent add <site> --name …`). For temporary boards (quick tunnel).
  - CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET : a Cloudflare Access service token (personal board web / Access mode).
    Sent in the CF-Access-Client-Id / CF-Access-Client-Secret headers. (CERA_CF_CLIENT_ID/SECRET are also accepted.)
    In Access mode an agent token is not used for requests from outside this Mac, so give only the service token without CERA_TOKEN.
  If CERA_URL is set but neither is, a warning goes to stderr at startup and the board tools answer with an error."""


def remote_help() -> str:
    return T(_REMOTE_HELP_KO, _REMOTE_HELP_EN)


def cap_result(text: str, st) -> dict:
    """Tool result → MCP result. If text + structured exceeds MAX_RESPONSE bytes, drop structured; if still over, truncate the text
    (with a note that it was truncated and how to narrow). If there is no structured, only the text."""
    note = T("\n… 응답이 너무 커서 잘렸다 — page · ids · limit · offset 으로 좁혀 다시 읽는다.",
             "\n… The response was too large and was truncated — narrow it with page, ids, limit, offset and read again.")
    tb = text.encode("utf-8")
    sb = json.dumps(st, ensure_ascii=False).encode("utf-8") if st is not None else b""
    if st is not None and len(tb) + len(sb) > MAX_RESPONSE:
        st = {"truncated": True, "note": T("structuredContent 가 너무 커서 뺐다 — 글을 보거나 limit · page 로 좁힌다",
                                           "structuredContent was too large and was left out — read the text or narrow with limit / page")}
    if len(tb) > MAX_RESPONSE:
        text = tb[:MAX_RESPONSE - len(note.encode())].decode("utf-8", "ignore") + note
    res = {"content": [{"type": "text", "text": text}], "isError": False}
    if st is not None:
        res["structuredContent"] = st
    return res


# ------------------------------------------------------------------ JSON-RPC loop
class Server:
    def __init__(self, ctx, out):
        self.s = Session(ctx)
        self.tools = Tools(self.s)
        self.out = out
        self.wlock = threading.Lock()
        self.cancelled = set()
        self.threads: List[threading.Thread] = []

    def send(self, msg):
        data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.wlock:
            self.out.write(data)
            self.out.flush()

    def error(self, mid, code, message):
        self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    def call_tool(self, mid, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = getattr(self.tools, name, None) if isinstance(name, str) and not name.startswith("_") else None
        names = {t["name"] for t in tool_defs(bool(self.s.remote))}
        if name not in names or fn is None:
            return self.error(mid, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return self.error(mid, -32602, "arguments must be an object")
        schema = next(t for t in tool_defs(bool(self.s.remote)) if t["name"] == name)["inputSchema"]
        extra = sorted(set(args) - set(schema["properties"]))
        try:
            if extra:   # the server also honors the schema's additionalProperties:false (so a wrong field name is not silently ignored)
                raise ToolError(T(f"모르는 칸: {', '.join(extra)} — 쓸 수 있는 칸: {', '.join(schema['properties']) or '없음'}",
                                  f"Unknown field(s): {', '.join(extra)} — allowed fields: {', '.join(schema['properties']) or 'none'}"))
            if name == "board_wait":
                tok = (params.get("_meta") or {}).get("progressToken") if isinstance(params.get("_meta"), dict) else None
                prog = None
                if isinstance(tok, (str, int)) and not isinstance(tok, bool):
                    def prog(done, total, message, tok=tok):
                        self.send({"jsonrpc": "2.0", "method": "notifications/progress",
                                   "params": {"progressToken": tok, "progress": done, "total": total, "message": message}})
                text, st = fn(args, cancelled=lambda: mid in self.cancelled, progress=prog)
            else:
                text, st = fn(args)
            res = cap_result(text, st)
        except ToolError as e:
            res = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:   # noqa: BLE001 — so one tool's mistake does not kill the server
            log("tool error", name, "".join(traceback.format_exception(type(e), e, e.__traceback__))[-1500:])
            res = {"content": [{"type": "text", "text": T(f"내부 오류({type(e).__name__}): {e}", f"Internal error ({type(e).__name__}): {e}")}], "isError": True}
        if mid in self.cancelled:   # do not answer a cancelled request (spec SHOULD NOT)
            self.cancelled.discard(mid)
            return
        self.send({"jsonrpc": "2.0", "id": mid, "result": res})

    def handle(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method", ""), str):
            if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
                return   # a client response (we never send requests)
            return self.error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        method, mid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            if mid is not None:
                self.error(mid, -32602, "params must be an object")
            return
        if mid is None:   # notification — do not reply
            if method == "notifications/cancelled":
                self.cancelled.add(params.get("requestId"))
            return
        if method == "initialize":
            ci = params.get("clientInfo") or {}
            self.s.client_name = ci.get("name") if isinstance(ci, dict) else None
            want = params.get("protocolVersion")
            ver = want if want in PROTOCOLS else LATEST
            return self.send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cera", "title": T("Cera 화이트보드", "Cera whiteboard"), "version": self.s.ctx.version()},
                "instructions": instructions()}})
        if method == "ping":
            return self.send({"jsonrpc": "2.0", "id": mid, "result": {}})
        if method == "tools/list":
            return self.send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_defs(bool(self.s.remote))}})
        if method == "tools/call":
            t = threading.Thread(target=self.call_tool, args=(mid, params), daemon=True)
            t.start()
            self.threads = [x for x in self.threads if x.is_alive()] + [t]
            return
        return self.error(mid, -32601, f"Method not found: {method}")

    def run(self, inp):
        for raw in inp:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
            except (ValueError, UnicodeDecodeError):
                self.error(None, -32700, "Parse error")
                continue
            if isinstance(msg, list):   # 2025-03-26 batch — one by one
                if not msg:
                    self.error(None, -32600, "Invalid Request")
                for m in msg:
                    self.handle(m)
                continue
            self.handle(msg)
        self.s.clear_pointers()   # stdin closed = the client is leaving: hide the cursor now, not after the joins below
        for t in self.threads:   # briefly wait for calls in progress
            t.join(timeout=2)


def main(ctx):
    out = sys.stdout.buffer
    sys.stdout = sys.stderr   # so an accidental print does not corrupt the JSON-RPC lines
    srv = Server(ctx, out)

    def bye(*_):
        srv.s.close()
        os._exit(0)
    for sg in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sg, bye)
        except (ValueError, OSError):
            pass
    try:
        srv.run(sys.stdin.buffer)
    except KeyboardInterrupt:
        pass
    finally:
        srv.s.close()


def register(sub, ctx):
    import argparse
    p = sub.add_parser("mcp", help=T("MCP 서버(stdio) — Claude Code · Codex · Antigravity 가 띄운다", "MCP server (stdio) — started by Claude Code, Codex or Antigravity"),
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       description=T("stdio MCP 서버. 같은 맥에서는 $CERA_HOME 의 사이트를 쓴다(환경변수 CERA_AGENT_NAME 으로 "
                                     "화면 이름을 정한다).",
                                     "stdio MCP server. On the same Mac it uses the sites in $CERA_HOME (set the on-screen name with the "
                                     "environment variable CERA_AGENT_NAME)."),
                       epilog=remote_help())
    p.set_defaults(f=lambda a: main(ctx))
