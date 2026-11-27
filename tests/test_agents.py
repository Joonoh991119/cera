"""Tests for agent tokens (server hook-chain entry, re-reading, 'AI' presence), the patch endpoint, and the `cera agent` CLI.

Everything uses temporary folders and a temporary CERA_HOME (~/.cera is never touched). No real tunnel is opened."""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["CERA_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import hashlib
import http.client
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "cera"


def _load(name):
    key = "cera_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "server" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


srv = _load("server")
ak = _load("agentkit")
EK, VK = "edit-key-123", "view-key-456"
TOK_E, TOK_V, TOK_X = "tb_ag_" + "e" * 32, "tb_ag_" + "v" * 32, "tb_ag_" + "x" * 32


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def raw(port, method, path, headers, body=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    h = {"Host": host or f"127.0.0.1:{port}", **headers}
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    d = r.read()
    c.close()
    try:
        return r.status, json.loads(d) if d else None
    except ValueError:
        return r.status, d


def bearer(tok, **more):
    return {"Authorization": "Bearer " + tok, "X-Whiteboard": "1", "Content-Type": "application/json", **more}


def keyh(key=EK, name="사람", **more):
    return {"X-Tb-Key": key, "X-Tb-Name": urllib.parse.quote(name), "X-Whiteboard": "1", "Content-Type": "application/json", **more}


class Server(unittest.TestCase):
    state_extra = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        site = Path(self.tmp.name) / "s1"
        (site / "data").mkdir(parents=True)
        now = time.time()
        self.state = {"edit_key": EK, "view_key": VK, "expires_at": 0, **self.state_extra, "agents": [
            {"id": "e1", "name": "Claude", "sha256": sha(TOK_E), "role": "edit", "expires_at": now + 3600, "created_at": now, "by": "cli"},
            {"id": "v1", "name": "Viewer", "sha256": sha(TOK_V), "role": "view", "expires_at": now + 3600, "created_at": now, "by": "cli"},
            {"id": "x1", "name": "Old", "sha256": sha(TOK_X), "role": "edit", "expires_at": now - 1, "created_at": now, "by": "cli"}]}
        (site / "state.json").write_text(json.dumps(self.state))
        self.access = srv.Access(EK, VK, site="s1")
        self.httpd, self.store = srv.build(site / "data", HERE / "web" / "dist", access=self.access, save_interval=0.2,
                                           revalidate_interval=0.3, hosts=self.hosts())
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        st, d = raw(self.port, "POST", "/api/whiteboard/boards", keyh(), {"title": "b"})
        self.assertEqual(st, 201)
        self.bid = d["id"]

    def hosts(self):
        return None

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def presence(self):
        return self.store.get(self.bid).presence_list()


class Tokens(Server):
    def test_roles_and_deny(self):
        """view token: POST /elements gives 403, GET gives 200. An unknown or expired token gives 401 (does not fall through to the key). Initial loading into state."""
        box = ak.make_box("rectangle", "A", 0, 0, by="Claude")
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", bearer(TOK_E), {"client": "c1", "elements": box})[0], 200)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", bearer(TOK_V), {"client": "c2", "elements": box})[0], 403)
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_V))[0], 200)
        for t in (TOK_X, "tb_ag_nope"):
            self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(t))[0], 401)
            # a wrong token with a valid key still gives 401 -- a request that carries a token is judged by the token alone
            self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(t, **{"X-Tb-Key": EK}))[0], 401)
        # another Bearer (not ours) is not seen by the hook -> key rules
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", {"Authorization": "Bearer abc", "X-Tb-Key": EK, "X-Tb-Name": "a"})[0], 200)
        # temporary mode accepts tokens even for requests that come through the tunnel (remote agent -- quick tunnel)
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_E, **{"Cf-Connecting-IP": "203.0.113.9"}))[0], 200)
        # ?token= is not accepted
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards?token={TOK_E}", {})[0], 403)

    def test_agent_presence_name_and_flag(self):
        """Adding X-Tb-Name to a token request still gives the name 'X · AI' with agent:true. Without a token, claiming 'Claude · AI' does not make it an agent."""
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/presence", bearer(TOK_E, **{"X-Tb-Name": urllib.parse.quote("사람")}),
            {"client": "ag1", "pointer": {"x": 1, "y": 2, "tool": "pointer"}})
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/presence", keyh(name="Claude · AI"), {"client": "fake", "pointer": {"x": 3, "y": 4}})
        p = {x["client"]: x for x in self.presence()}
        self.assertEqual(p["ag1"]["user"], "Claude · AI")
        self.assertIs(p["ag1"].get("agent"), True)
        self.assertEqual(p["ag1"]["short"], "Claude AI")
        self.assertEqual(p["fake"]["user"], "Claude", "사람 이름 끝의 '· AI' 는 뗀다(r1 사소함 1)")
        self.assertNotIn("agent", p["fake"])
        # /me: an agent is not given the invite key
        st, me = raw(self.port, "GET", "/api/whiteboard/me", bearer(TOK_E))
        self.assertEqual(st, 200)
        self.assertEqual(me["invite"], {})
        self.assertTrue(me["agent"])
        st, me = raw(self.port, "GET", "/api/whiteboard/me", keyh())
        self.assertIn("edit", me["invite"])

    def test_idle_agent_presence_swept_and_broadcast(self):
        """If an agent does nothing for 30 seconds, it is dropped from the list and that is broadcast, even if nobody else moves."""
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/presence", bearer(TOK_E), {"client": "ag1", "pointer": {"x": 1, "y": 2}})
        b = self.store.get(self.bid)
        with b.lock:
            b.presence["ag1"]["_t"] -= 31
            pver = b.pver
        time.sleep(0.6)   # the flusher (0.2 s) calls sweep
        with b.lock:
            self.assertNotIn("ag1", b.presence)
            self.assertGreater(b.pver, pver, "빠진 것이 방송됐다")

    def test_reload_revokes_within_a_second_and_closes_ws(self):
        """Revoking = removing from state and access.reload -> only that token gets 401, and only that token's WebSocket is closed."""
        sys.path.insert(0, str(HERE / "tests"))
        from test_security import ws_open, ws_read   # noqa: E402
        st_, s, f = ws_open(self.port, f"/api/whiteboard/boards/{self.bid}/ws?client=wa&since=0&epoch=", {"Host": f"127.0.0.1:{self.port}", "Authorization": "Bearer " + TOK_E})
        self.assertEqual(st_, 101)
        st2, s2, f2 = ws_open(self.port, f"/api/whiteboard/boards/{self.bid}/ws?client=wb&since=0&epoch=", {"Host": f"127.0.0.1:{self.port}", "Authorization": "Bearer " + TOK_V})
        self.assertEqual(st2, 101)
        for ff in (f, f2):
            ws_read(ff), ws_read(ff)
        new = dict(self.state, agents=[a for a in self.state["agents"] if a["id"] != "e1"])
        t0 = time.time()
        self.access.reload(new)
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_E))[0], 401)
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_V))[0], 200)
        s.settimeout(3)
        closed = False
        try:
            while True:
                op, _ = ws_read(f)
                if op in (None, 8):
                    closed = True
                    break
        except OSError:
            closed = True
        self.assertTrue(closed and time.time() - t0 < 2.5)
        s2.settimeout(0.5)
        try:
            op, _ = ws_read(f2)
            self.assertNotIn(op, (None, 8), "view 토큰 WebSocket 은 살아 있다")
        except OSError:
            pass   # timed out = still alive
        s.close()
        s2.close()

    def test_token_expiry_mid_connection(self):
        """Auth.expires_at -- a request gets 401 even if the token ends during a connection (same path as the periodic check)."""
        new = json.loads(json.dumps(self.state))
        new["agents"][0]["expires_at"] = time.time() + 0.5
        self.access.reload(new)
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_E))[0], 200)
        time.sleep(0.7)
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_E))[0], 401)


class LocalOnly(Server):
    """Personal / Access mode (state.mode=personal): tokens are accepted only from requests inside this Mac (loopback Host and peer)."""
    state_extra = {"mode": "personal"}

    def hosts(self):
        return [srv.HostRule("board.example.com", public=True)]

    def test_token_ignored_on_public_host(self):
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_E))[0], 200)
        st, _ = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_E), host="board.example.com")
        self.assertEqual(st, 403, "공개 Host 에서는 토큰을 보지 않는다 → 키 규칙(키 없음 403)")

    def test_cloudflare_forwarded_request_is_never_local(self):
        """cloudflared connects from loopback. Even if Host is 127.0.0.1, a request with Cf-Connecting-IP or Cf-Ray is not local
        -> in personal mode the agent token is ignored and only the invite-key rules remain."""
        for extra in ({"Cf-Connecting-IP": "203.0.113.9"}, {"Cf-Ray": "8a1b2c3d4e5f-ICN"}, {"CF-Connecting-IP": "203.0.113.9", "Cf-Ray": "x"}):
            st, _ = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(TOK_E, **extra))
            self.assertEqual(st, 403, f"{extra}: 토큰을 보지 않는다 → 키 없음 403")
            st, _ = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", {**keyh(), **extra})
            self.assertEqual(st, 200, "초대 키 규칙은 그대로")
            st, me = raw(self.port, "GET", "/api/whiteboard/me", bearer(TOK_E, **{**extra, "X-Tb-Key": EK, "X-Tb-Name": "p"}))
            self.assertNotIn("agent", me or {}, "토큰이 무시돼 사람(키)으로 본다")


class Patch(Server):
    def put(self, els, tok=TOK_E):
        st, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", bearer(tok), {"client": "ag", "elements": els})
        self.assertEqual(st, 200)
        return d

    def patch(self, changes, atomic=False, headers=None):
        return raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/patch", headers or bearer(TOK_E),
                   {"client": "ag", "changes": changes, "atomic": atomic})

    def el(self, i):
        return self.store.get(self.bid).elements[i]

    def test_conflict_keeps_human_change(self):
        """If a human edited first and the version went up, a patch with a stale base_version gets conflict and the human's change is kept."""
        box, lab = ak.make_box("rectangle", "A", 0, 0, by="Claude")
        self.put([box, lab])
        v0 = self.el(lab["id"])["version"]
        human = dict(self.el(lab["id"]), text="사람", originalText="사람", version=v0 + 1, versionNonce=5)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", keyh(), {"client": "h", "elements": [human]})[0], 200)
        st, r = self.patch([{"id": lab["id"], "base_version": v0, "set": {"text": "AI", "originalText": "AI"}}])
        self.assertEqual(st, 200)
        self.assertEqual(r["applied"], [])
        self.assertEqual(r["conflict"], [{"id": lab["id"], "version": v0 + 1, "base_version": v0}])
        self.assertEqual(self.el(lab["id"])["text"], "사람")
        st, r = self.patch([{"id": lab["id"], "base_version": v0 + 1, "set": {"text": "AI", "originalText": "AI"}}])
        self.assertEqual(r["applied"], [{"id": lab["id"], "version": v0 + 2}])
        self.assertEqual(self.el(lab["id"])["text"], "AI")

    def test_atomic_and_allowlist(self):
        box, lab = ak.make_box("rectangle", "A", 0, 0)
        self.put([box, lab])
        st, r = self.patch([{"id": box["id"], "set": {"x": 50}}, {"id": lab["id"], "base_version": 999, "set": {"x": 60}}], atomic=True)
        self.assertEqual((r["applied"], len(r["conflict"])), ([], 1))
        self.assertEqual(self.el(box["id"])["x"], 0.0, "atomic — 하나가 걸리면 아무것도 바뀌지 않는다")
        for bad in ({"type": "ellipse"}, {"id": "zz"}, {"index": "a0"}, {"x": float("inf")}, {"x": "1"}, {"opacity": 300},
                    {"boundElements": [{"type": "x", "id": "a"}]}, {"startBinding": {"focus": 0}}):
            st, r = self.patch([{"id": box["id"], "set": bad}])
            self.assertEqual(st, 200)
            self.assertEqual(len(r["rejected"]), 1, bad)
        v = self.el(box["id"])["version"]
        st, r = self.patch([{"id": box["id"], "base_version": v, "set": {}}, {"id": box["id"], "set": {"x": 0.0}}])
        self.assertEqual((r["applied"], len(r["unchanged"])), ([], 2), "바뀌는 것 없는 항목은 판을 올리지 않는다(r3)")
        self.assertEqual(self.el(box["id"])["version"], v)
        st, r = self.patch([{"id": "nope", "set": {"x": 1}}])
        self.assertEqual(r["missing"], ["nope"])
        self.assertEqual(self.patch([])[0], 200)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/patch", bearer(TOK_E), {"changes": "x"})[0], 400)

    def test_bound_ops_and_view_token(self):
        box, lab = ak.make_box("rectangle", "A", 0, 0)
        self.put([box, lab])
        v = self.el(box["id"])["version"]
        st, r = self.patch([{"id": box["id"], "add_bound": [{"type": "arrow", "id": "ar1"}, {"type": "arrow", "id": "ar2"}]}])
        self.assertEqual(r["applied"][0]["version"], v + 1)
        st, r = self.patch([{"id": box["id"], "remove_bound": ["ar1"]}])
        self.assertEqual([b["id"] for b in self.el(box["id"])["boundElements"]], [lab["id"], "ar2"])
        self.assertEqual(self.patch([{"id": box["id"], "set": {"x": 1}}], headers=bearer(TOK_V))[0], 403)
        # a human (key) can use patch too (edit permission)
        st, r = self.patch([{"id": box["id"], "set": {"isDeleted": True}}], headers=keyh())
        self.assertTrue(self.el(box["id"])["isDeleted"])

    def test_patch_broadcasts_to_poll(self):
        box, lab = ak.make_box("rectangle", "A", 0, 0)
        d = self.put([box, lab])
        seq = d["seq"]
        epoch = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", keyh())[1]["epoch"]
        self.patch([{"id": box["id"], "set": {"x": 10}}])
        st, r = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}/poll?since={seq}&client=h&pver=-1&epoch={epoch}&wait=1", keyh())
        got = [e for ev in r["events"] if ev["event"] == "elements" for e in ev["data"]["elements"]]
        self.assertEqual([(e["id"], e["x"]) for e in got], [(box["id"], 10)])
        t = time.time()
        raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}/poll?since={r['seq']}&client=h&pver={r['pver']}&epoch={epoch}&wait=0.5", keyh())
        self.assertLess(time.time() - t, 3, "wait 로 짧게 기다린다")


class Cli(unittest.TestCase):
    """`cera agent add/list/revoke` against a real site (start --tunnel none)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        dist = t / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>tb</title>")
        self.env = {**os.environ, "CERA_HOME": str(t / "home"), "CERA_DIST": str(dist)}
        out = self.cli("start", "--tunnel", "none", "--title", "t", "--json")
        self.site = json.loads(out)
        self.sid = self.site["id"]
        self.state_file = t / "home" / "sites" / self.sid / "state.json"
        self.port = int(json.loads(self.state_file.read_text())["port"])
        self.bid = json.loads(self.state_file.read_text())["board"]

    def tearDown(self):
        subprocess.run([sys.executable, str(CLI), "stop", self.sid], env=self.env, capture_output=True)
        self.tmp.cleanup()

    def cli(self, *args, check=True):
        p = subprocess.run([sys.executable, str(CLI), *args], env=self.env, capture_output=True, text=True, timeout=60)
        if check and p.returncode:
            raise AssertionError(f"{args}: {p.stdout}\n{p.stderr}")
        return p.stdout

    def wait_status(self, tok, want, limit=2.0):
        t0 = time.time()
        while True:
            st = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(tok))[0]
            if st == want or time.time() - t0 > limit:
                return st, time.time() - t0
            time.sleep(0.05)

    def test_english_output(self):
        """With CERA_LANG=en, `agent add / list / revoke` print English and no Korean."""
        hangul = re.compile("[\uac00-\ud7a3]")
        self.env["CERA_LANG"] = "en"
        try:
            empty = self.cli("agent", "list", self.sid)
            self.assertIn("No tokens", empty)
            out = self.cli("agent", "add", self.sid, "--name", "Remote")
            self.assertIn("Agent token", out)
            self.assertIn("the token is shown only this once", out)
            self.assertRegex(out, r"(?m)^\s*CERA_TOKEN=tb_ag_\S+$")
            self.assertIn("agent revoke", out)
            self.assertIsNone(hangul.search(out), out)
            tid = re.search(r"Agent token (\w+) ", out).group(1)
            self.assertIsNone(hangul.search(self.cli("agent", "list", self.sid)))
            rv = self.cli("agent", "revoke", self.sid, tid)
            self.assertIn("Revoked 1 token(s)", rv)
            self.assertIsNone(hangul.search(rv), rv)
            h = subprocess.run([sys.executable, str(CLI), "agent", "add", "-h"], env=self.env, capture_output=True, text=True).stdout
            self.assertIn("Create a token", h + subprocess.run([sys.executable, str(CLI), "agent", "-h"], env=self.env,
                                                               capture_output=True, text=True).stdout)
            self.assertIsNone(hangul.search(h), h)
            bad = subprocess.run([sys.executable, str(CLI), "agent", "add", self.sid, "--name", "Z", "--ttl", "xx"], env=self.env,
                                 capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("Could not read --ttl", bad.stderr + bad.stdout)
            self.assertIsNone(hangul.search(bad.stderr + bad.stdout))
        finally:
            self.env["CERA_LANG"] = "ko"

    def test_add_view_revoke(self):
        v = json.loads(self.cli("agent", "add", self.sid, "--name", "X", "--role", "view", "--json"))
        e = json.loads(self.cli("agent", "add", self.sid, "--name", "Claude", "--json"))
        self.assertTrue(v["token"].startswith("tb_ag_") and len(v["token"]) > 30)
        st, dt = self.wait_status(v["token"], 200)
        self.assertEqual(st, 200, "발급 2초 안에 받아들여진다")
        box = ak.make_box("rectangle", "A", 0, 0)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", bearer(v["token"]), {"client": "c", "elements": box})[0], 403)
        self.wait_status(e["token"], 200)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", bearer(e["token"]), {"client": "c", "elements": box})[0], 200)
        text = self.state_file.read_text()
        self.assertNotIn(v["token"], text, "state.json 에 토큰 평문이 없다")
        self.assertNotIn(e["token"], text)
        self.assertIn(sha(v["token"]), text)
        self.assertEqual(oct(self.state_file.stat().st_mode & 0o777), "0o600")
        rows = json.loads(self.cli("agent", "list", self.sid, "--json"))
        self.assertEqual({r["name"] for r in rows}, {"X", "Claude"})
        self.assertTrue(all("sha256" not in r for r in rows))
        exp = json.loads(self.state_file.read_text())["expires_at"]
        self.assertTrue(all(r["expires_at"] <= exp + 1 for r in rows), "만료 = min(사이트, 12h)")
        human = self.cli("agent", "add", self.sid, "--name", "Y")
        self.assertIn("한 번만", human)
        self.assertIn("셸 기록 · 대화 기록에 남는다", human, "토큰을 셸 · 채팅에 붙이지 말라는 한 줄(r3)")
        self.cli("agent", "revoke", self.sid, v["id"])
        st, dt = self.wait_status(v["token"], 401)
        self.assertEqual(st, 401)
        self.assertLess(dt, 2.0, "취소 2초 안에 401")
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(e["token"]))[0], 200, "다른 토큰은 산다")
        out = self.cli("agent", "revoke", self.sid, "--all", "--json")
        self.assertEqual(len(json.loads(out)), 2)
        p = subprocess.run([sys.executable, str(CLI), "agent", "revoke", self.sid, "nope"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)

    def test_token_works_as_soon_as_add_returns(self):
        """A token printed by `agent add` is accepted on the very next request (no ~1s gap that looks like a revoked token)."""
        for _ in range(3):
            tok = json.loads(self.cli("agent", "add", self.sid, "--name", "Now", "--json"))["token"]
            self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", bearer(tok))[0], 200)

    def test_ttl_and_bad_input(self):
        r = json.loads(self.cli("agent", "add", self.sid, "--name", "<b>Bad;</b>", "--ttl", "30m", "--json"))
        self.assertEqual(r["name"], "bBadb")
        self.assertLess(r["expires_at"] - time.time(), 1801)
        p = subprocess.run([sys.executable, str(CLI), "agent", "add", self.sid, "--name", "a", "--ttl", "0"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)


class Unit(unittest.TestCase):
    def test_agent_short_and_names(self):
        self.assertEqual(srv.agent_short("Claude · AI"), "Claude AI")
        self.assertEqual(srv.agent_short("Antigravity · AI"), "Antigrav AI")
        self.assertEqual(srv.clean_agent_name("  "), "Agent")
        ag = _load("agents")
        self.assertEqual(ag.parse_ttl("2h"), 7200)
        self.assertEqual(ag.parse_ttl("90d"), ag.MAX_TTL)
        st = {"expires_at": time.time() + 100, "agents": [{"expires_at": 1}, {"expires_at": time.time() + 50}]}
        self.assertEqual(ag.prune(st), 1)
        self.assertLessEqual(ag.expiry_for(st, None, time.time()), st["expires_at"])

    def test_access_load_agents(self):
        a = srv.Access(EK)
        self.assertTrue(a.load_agents({"agents": [{"sha256": sha(TOK_E), "role": "view", "name": "N", "expires_at": time.time() + 9}]}))
        self.assertFalse(a.load_agents({"agents": [{"sha256": sha(TOK_E), "role": "view", "name": "N", "expires_at": a.agents[0][1]["expires_at"]}]}))
        r = a.agent_auth(TOK_E, True)
        self.assertEqual((r.role, r.user["name"], r.user["agent"]), ("view", "N · AI", True))
        self.assertTrue(a.agent_auth(TOK_V, True).deny)
        # a broken entry is dropped
        a.load_agents({"agents": [{"sha256": "short"}, "x", {"sha256": sha(TOK_V), "expires_at": "?"}]})
        self.assertEqual(a.agents, ())


class SupervisorStartWindow(unittest.TestCase):
    """A revoke that overlaps the start window -- after the supervisor read st and after build loaded the token -- is loaded into memory (re-read on the first tick)."""

    def test_revoke_during_startup_takes_effect(self):
        import types
        sv = _load("supervise")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sites = Path(tmp.name) / "sites"
        (sites / "s1" / "data").mkdir(parents=True)
        st = {"id": "s1", "edit_key": EK, "view_key": None, "expires_at": 0, "host": "127.0.0.1", "port": 0, "status": "starting",
              "agents": [{"id": "e1", "name": "C", "sha256": sha(TOK_E), "role": "edit", "expires_at": time.time() + 3600}]}
        sv.update_state(sites, "s1", lambda cur: st)

        def build(*a, **kw):
            out = srv.build(*a, **kw)
            sv.update_state(sites, "s1", lambda cur: cur.update(agents=[]))   # a revoke inside the start window (simulates cera agent revoke)
            return out
        fake = types.SimpleNamespace(security=srv.security, Access=srv.Access, build=build)
        stop = threading.Event()
        th = threading.Thread(target=sv.run, args=("s1", st), kwargs=dict(sites=sites, dist=HERE / "web" / "dist", server=fake, stop=stop),
                              daemon=True)
        th.start()
        self.addCleanup(lambda: (stop.set(), th.join(10)))
        port = None
        for _ in range(100):
            port = (sv.read_state(sites, "s1") or {}).get("port")
            if port and (sv.read_state(sites, "s1") or {}).get("status") == "running":
                break
            time.sleep(0.05)
        t0 = time.time()
        code = None
        while time.time() - t0 < 3:
            code = raw(port, "GET", "/api/whiteboard/boards", bearer(TOK_E))[0]
            if code == 401:
                break
            time.sleep(0.1)
        self.assertEqual(code, 401, "시작 창에 취소한 토큰은 2초 안에 막힌다")
        self.assertLess(time.time() - t0, 2.5)


if __name__ == "__main__":
    unittest.main()
