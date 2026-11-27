# SPDX-License-Identifier: MIT
"""Agent tokens — storage, issuing, listing, revoking, and the `cera agent …` command.

    cera agent add <id> --name Claude [--role edit|view] [--ttl 2h] [--json]   # the token is shown only once, at this point
    cera agent list <id> [--json]
    cera agent revoke <id> <token-id> | --all | --by mcp

Token = `tb_ag_<token_urlsafe(24)>`. Only the sha256 is stored in `agents` in state.json (no plaintext; the file is 0600).
The server accepts tokens only via the `Authorization: Bearer` header (server.AgentTokens). The supervisor loop re-reads
state.json every second, so issuing and revoking take effect within 2 seconds. Default expiry is min(site expiry, 12 hours).
Expired tokens are not loaded by the server; this module deletes them the next time it edits state.json (add, revoke, list).

Standard library only; importable on Python 3.9.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PREFIX = "tb_ag_"
DEFAULT_TTL = 12 * 3600
MAX_TTL = 30 * 86400


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


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_name(name) -> str:
    """Same rules as server.clean_agent_name (letters, digits, spaces, -_.(), 32 chars)."""
    n = re.sub(r"[^\w\s.()\-가-힣]", "", str(name or ""), flags=re.UNICODE)
    n = re.sub(r"\s+", " ", n).strip()[:32]
    return n or "Agent"


def parse_ttl(s) -> int:
    s = str(s).strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s)
    if not m:
        raise ValueError(T(f"--ttl 을 읽지 못했다: {s} (예: 30m · 2h · 1d)", f"Could not read --ttl: {s} (e.g. 30m, 2h, 1d)"))
    sec = int(float(m.group(1)) * {"": 3600, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)])
    if sec <= 0:
        raise ValueError(T("--ttl 은 0 보다 커야 한다(토큰은 늘 끝이 있다)", "--ttl must be greater than 0 (a token always expires)"))
    return min(sec, MAX_TTL)


def prune(st: dict, now: Optional[float] = None) -> int:
    """Delete expired tokens in place. Returns the number deleted."""
    now = time.time() if now is None else now
    rows = st.get("agents") or []
    keep = [a for a in rows if isinstance(a, dict) and not (a.get("expires_at") and float(a["expires_at"]) <= now)]
    st["agents"] = keep
    return len(rows) - len(keep)


def expiry_for(st: dict, ttl: Optional[int], now: float) -> float:
    exp = now + (ttl if ttl else DEFAULT_TTL)
    site = float(st.get("expires_at") or 0)
    if site:
        exp = min(exp, site)
    return exp


def add(sites, sid: str, name: str, role: str = "edit", ttl: Optional[int] = None, by: str = "cli") -> Tuple[dict, str]:
    """Create one token. Returns (entry written to state, plaintext token). The plaintext exists only in this return value."""
    if role not in ("edit", "view"):
        raise ValueError(T("role 은 edit 또는 view", "role must be edit or view"))
    sv = _sibling("supervise")
    if sv.read_state(sites, sid) is None:
        raise ValueError(T(f"사이트 {sid} 가 없다", f"No such site: {sid}"))
    token = PREFIX + secrets.token_urlsafe(24)
    box = {}

    def fn(st):
        now = time.time()
        prune(st, now)
        exp = expiry_for(st, ttl, now)
        if exp <= now:
            raise ValueError(T("사이트가 이미 만료됐다", "The site has already expired"))
        ent = {"id": secrets.token_hex(4), "name": clean_name(name), "sha256": digest(token), "role": role,
               "expires_at": exp, "created_at": now, "by": str(by)[:16]}
        st["agents"].append(ent)
        box["ent"] = ent
    sv.update_state(sites, sid, fn)
    return box["ent"], token


def revoke(sites, sid: str, ids: Optional[List[str]] = None, by: Optional[str] = None, all_: bool = False) -> List[dict]:
    """Revoke tokens (removed from state). Exactly one of ids, by (issuer), all_. Returns the removed entries."""
    sv = _sibling("supervise")
    if sv.read_state(sites, sid) is None:
        raise ValueError(T(f"사이트 {sid} 가 없다", f"No such site: {sid}"))
    gone = []

    def fn(st):
        prune(st)
        keep = []
        for a in st["agents"]:
            hit = all_ or (ids and a.get("id") in ids) or (by and a.get("by") == by)
            (gone if hit else keep).append(a)
        st["agents"] = keep
    sv.update_state(sites, sid, fn)
    return gone


def listing(sites, sid: str) -> List[dict]:
    """List tokens (without sha256). Expired ones are deleted at this point."""
    sv = _sibling("supervise")
    st = sv.read_state(sites, sid)
    if st is None:
        raise ValueError(T(f"사이트 {sid} 가 없다", f"No such site: {sid}"))
    if any(isinstance(a, dict) and a.get("expires_at") and float(a["expires_at"]) <= time.time() for a in st.get("agents") or []):
        def fn(cur):
            prune(cur)
        st = sv.update_state(sites, sid, fn)
    return [{k: v for k, v in a.items() if k != "sha256"} for a in st.get("agents") or [] if isinstance(a, dict)]


def url_for(st: dict) -> Tuple[Optional[str], Optional[str]]:
    """The address an agent should use as CERA_URL, plus a note. Personal / Access mode accepts tokens only on this Mac, so a local address is returned."""
    local, public = st.get("local_url"), st.get("public_url")
    if st.get("mode") == "personal" or st.get("access"):
        return local, T("이 보드는 개인 · Access 모드라 에이전트 토큰은 이 컴퓨터 안(로컬 주소)에서만 통한다. 다른 컴퓨터의 에이전트는 "
                        "Access 서비스 토큰(CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET)과 공개 주소를 쓴다",
                        "This board is in personal / Access mode, so agent tokens only work on this computer (local address). "
                        "Agents on other computers use an Access service token (CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET) and the public address")
    if public:
        return public, T("다른 컴퓨터에서도 이 주소로 붙는다(공개 터널). 이 컴퓨터 안이면 " + (local or "로컬 주소") + " 도 된다",
                         "Other computers can connect to this address too (public tunnel). On this computer " + (local or "the local address") + " also works")
    return local, (T("이 컴퓨터에서만 열린 사이트다(--tunnel none) — 다른 컴퓨터에서 쓰려면 공개 링크로 연 사이트에서 발급한다",
                     "This site is only open on this computer (--tunnel none) — to use it from another computer, issue the token from a site opened with a public link")
                   if st.get("host") != "0.0.0.0" else None)


# ------------------------------------------------------------------ CLI
def _when(t):
    return time.strftime("%m/%d %H:%M", time.localtime(t)) if t else "-"


def _await_live(st: dict, token: str, timeout: float = 5.0) -> bool:
    """Make a freshly issued token usable before `agent add` returns: ask the running supervisor to re-read
    state.json now (SIGUSR1) and wait until the token is accepted. Without this, a script or agent that uses the
    token right away gets 401 for up to a second. Returns False (silently) if the site is not running."""
    import os
    import signal
    import urllib.error
    import urllib.request
    pid, local = st.get("pid"), (st.get("local_url") or "").rstrip("/")
    if not pid or not local or st.get("status") != "running":
        return False
    try:
        if hasattr(signal, "SIGUSR1"):
            os.kill(int(pid), signal.SIGUSR1)
    except (OSError, ValueError):
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = urllib.request.Request(local + "/api/whiteboard/me", headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.1)
    return False


def register(sub, ctx):
    p = sub.add_parser("agent", help=T("에이전트 토큰(MCP · 다른 컴퓨터의 에이전트) 발급 · 목록 · 취소", "Issue, list and revoke agent tokens (MCP / agents on other computers)"))
    ss = p.add_subparsers(dest="agent_cmd", required=True)

    def cmd_add(a):
        ctx.need(a.id)
        try:
            ttl = parse_ttl(a.ttl) if a.ttl else None
            ent, token = add(ctx.SITES, a.id, a.name, a.role, ttl, by="cli")
        except ValueError as e:
            raise SystemExit(str(e))
        _await_live(ctx.read_state(a.id) or {}, token)
        url, note = url_for(ctx.read_state(a.id) or {})
        if a.json:
            print(json.dumps({**{k: v for k, v in ent.items() if k != "sha256"}, "token": token, "url": url}, ensure_ascii=False))
            return
        print(T(f"에이전트 토큰 {ent['id']} ({ent['name']} · {ent['role']}) — {_when(ent['expires_at'])} 까지",
                f"Agent token {ent['id']} ({ent['name']} · {ent['role']}) — until {_when(ent['expires_at'])}"))
        print(T("  에이전트(MCP `cera mcp`) 쪽 환경 변수 두 줄 — 토큰은 지금 한 번만 보인다:",
                "  Two environment variables for the agent (MCP `cera mcp`) — the token is shown only this once:"))
        print(f"    CERA_URL=" + (url or T('<사이트가 닫혀 있다 — 열린 뒤 cera links ' + a.id + ' 의 주소>',
                                                '<the site is closed — use the address from cera links ' + a.id + ' once it is open>')))
        print(f"    CERA_TOKEN={token}")
        if note:
            print(f"  ※ {note}")
        print(T("  ※ 토큰은 MCP 설정의 env 칸에 넣는다 — 셸에 그대로 붙이거나 채팅 · 에이전트에게 보이면 셸 기록 · 대화 기록에 남는다"
                "(그랬다면 취소하고 새로 발급).",
                "  Note: put the token in the env field of the MCP config — pasting it into a shell or showing it in a chat / to an agent "
                "leaves it in shell history / conversation logs (if that happened, revoke it and issue a new one)."))
        print(T(f"  끊으려면 cera agent revoke {a.id} {ent['id']}", f"  To cut it off: cera agent revoke {a.id} {ent['id']}"))

    def cmd_list(a):
        ctx.need(a.id)
        rows = listing(ctx.SITES, a.id)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False))
            return
        if not rows:
            print(T("토큰이 없다 — cera agent add " + a.id + " --name <이름>", "No tokens — cera agent add " + a.id + " --name <name>"))
        for r in rows:
            print(f"{r.get('id')}  {r.get('name', ''):16} {r.get('role', ''):4}  ~{_when(r.get('expires_at'))}  ({r.get('by', '')})")

    def cmd_revoke(a):
        ctx.need(a.id)
        if not (a.token_id or a.all or a.by):
            raise SystemExit(T("취소할 토큰 id, --all, --by 중 하나", "Give a token id, --all, or --by"))
        gone = revoke(ctx.SITES, a.id, [a.token_id] if a.token_id else None, a.by, a.all)
        if a.json:
            print(json.dumps([{k: v for k, v in g.items() if k != "sha256"} for g in gone], ensure_ascii=False))
            return
        if not gone:
            raise SystemExit(T("맞는 토큰이 없다 — cera agent list " + a.id, "No matching token — cera agent list " + a.id))
        live = ctx.live_status(ctx.read_state(a.id) or {}) == "running"
        print(T(f"토큰 {len(gone)}개 취소 — " + ("2초 안에 끊긴다" if live else "사이트는 닫혀 있다"),
                f"Revoked {len(gone)} token(s) — " + ("cut off within 2 seconds" if live else "the site is closed")))

    s = ss.add_parser("add", help=T("토큰 만들기(한 번만 보인다)", "Create a token (shown only once)"))
    s.add_argument("id")
    s.add_argument("--name", required=True, help=T("화면에 '<이름> · AI' 로 보인다", "Shown on the board as '<name> · AI'"))
    s.add_argument("--role", choices=["edit", "view"], default="edit")
    s.add_argument("--ttl", default=None, help=T("유효 시간(30m · 2h · 1d). 기본 min(사이트 만료, 12h)", "Lifetime (30m, 2h, 1d). Default: min(site expiry, 12h)"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(f=cmd_add)
    s = ss.add_parser("list", help=T("토큰 목록(평문은 없다)", "List tokens (no plaintext)"))
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(f=cmd_list)
    s = ss.add_parser("revoke", help=T("토큰 취소(2초 안에 끊긴다)", "Revoke a token (cut off within 2 seconds)"))
    s.add_argument("id")
    s.add_argument("token_id", nargs="?")
    s.add_argument("--all", action="store_true")
    s.add_argument("--by", default=None, help=T("발급처로 취소(예: mcp)", "Revoke by issuer (e.g. mcp)"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(f=cmd_revoke)
