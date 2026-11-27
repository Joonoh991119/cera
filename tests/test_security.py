"""Security tests: Host allow-list (421), narrowing for quick tunnels, WS Origin, https detection, __Host- cookies, HSTS,
re-reading keys (closing open WS / SSE), the auth hook chain, and rejecting open mode."""
from __future__ import annotations

import base64
import contextlib
import http.client
import importlib.util
import io
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Korean assertions below: pin the language before anything spawns the CLI or imports server code.
os.environ["CERA_LANG"] = "ko"

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wb_server_sec", HERE / "server" / "server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
sec = srv.security
EK, VK = "EDITKEY123456", "VIEWKEY456789"


def raw(port, method, path, headers=None, body=None, timeout=5):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheaders(), data


def header(hs, name):
    return [v for k, v in hs if k.lower() == name.lower()]


def ws_open(port, path, headers):
    """Hand-rolled WebSocket upgrade -> (status code, socket, read file)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    extra = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    s.sendall((f"GET {path} HTTP/1.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n{extra}\r\n").encode())
    f = s.makefile("rb")
    status = int(f.readline().split()[1])
    while f.readline() not in (b"\r\n", b""):
        pass
    return status, s, f


def ws_read(f):
    """Next frame -> (opcode, payload). Returns (None, b'') if the connection closed."""
    h = f.read(2)
    if len(h) < 2:
        return None, b""
    op, n = h[0] & 0x0F, h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", f.read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", f.read(8))[0]
    return op, f.read(n)


class Rules(unittest.TestCase):
    def test_split_host(self):
        self.assertEqual(sec.split_host("Board.Example.com."), ("board.example.com", ""))
        self.assertEqual(sec.split_host("127.0.0.1:08790"), ("127.0.0.1", "8790"))
        self.assertEqual(sec.split_host("[::1]:9"), ("[::1]", "9"))
        for bad in ("", "a b", "x:y", "h:70000", "::1", "[::1", "evil.example/x", "a\r\nb"):
            self.assertEqual(sec.split_host(bad), (None, None), bad)

    def test_rule_match(self):
        r = sec.HostRule("127.0.0.1:8790")
        self.assertTrue(r.matches(*sec.split_host("127.0.0.1:8790")))
        self.assertFalse(r.matches(*sec.split_host("127.0.0.1:8791")))
        self.assertFalse(r.matches(*sec.split_host("127.0.0.1")))
        pub = sec.HostRule("board.example.com", public=True)
        self.assertTrue(pub.matches(*sec.split_host("board.example.com")))
        self.assertTrue(pub.matches(*sec.split_host("board.example.com:443")))
        self.assertFalse(pub.matches(*sec.split_host("board.example.com:80")))
        self.assertFalse(pub.matches(*sec.split_host("x.board.example.com")))
        w = sec.HostRule("*.trycloudflare.com", public=True)
        self.assertTrue(w.matches(*sec.split_host("abc-def.trycloudflare.com")))
        for bad in ("trycloudflare.com", "a.b.trycloudflare.com", "evil.com", "-x.trycloudflare.com", "abctrycloudflare.com"):
            self.assertFalse(w.matches(*sec.split_host(bad)), bad)
        with self.assertRaises(ValueError):
            sec.HostRule("a.*.com")

    def test_parse_public(self):
        self.assertTrue(sec.HostRule.parse("board.example.com").public)
        self.assertTrue(sec.HostRule.parse("https://board.example.com").public)
        self.assertFalse(sec.HostRule.parse("http://localhost:5173").public)
        self.assertFalse(sec.HostRule.parse("192.168.0.5:8790").public)
        self.assertFalse(sec.HostRule.parse("localhost:5173").public)
        with self.assertRaises(ValueError):
            sec.HostRule.parse("ftp://x")

    def test_origin(self):
        a = sec.HostAllow(sec.local_rules(8790) + [sec.HostRule("x.trycloudflare.com", public=True)])
        self.assertTrue(a.origin_ok("http://127.0.0.1:8790"))
        self.assertTrue(a.origin_ok("http://localhost:8790"))
        self.assertTrue(a.origin_ok("https://x.trycloudflare.com"))
        for bad in ("null", "", "https://evil.example", "https://127.0.0.1:8790", "http://x.trycloudflare.com",
                    "http://127.0.0.1:8791", "http://127.0.0.1:8790/x", "javascript:alert(1)"):
            self.assertFalse(a.origin_ok(bad), bad)

    def test_rules_for_state(self):
        pend = sec.rules_for_state({"tunnel": "cloudflared"}, 9000)
        self.assertIn(sec.HostRule("*.trycloudflare.com", True), pend)
        got = sec.rules_for_state({"tunnel": "cloudflared", "public_url": "https://abc.trycloudflare.com"}, 9000)
        self.assertIn(sec.HostRule("abc.trycloudflare.com", True), got)
        self.assertNotIn(sec.HostRule("*.trycloudflare.com", True), got, "주소가 나오면 그 이름 하나로")
        none = sec.rules_for_state({"tunnel": "none"}, 9000)
        self.assertEqual(none, sec.local_rules(9000))
        lan = sec.rules_for_state({"tunnel": "none", "host": "0.0.0.0"}, 9000, lan="192.168.0.7")
        self.assertIn(sec.HostRule("192.168.0.7:9000"), lan)
        extra = sec.rules_for_state({"tunnel": "named", "hostname": "board.example.com", "allow_hosts": ["proxy.example.org", "bad host!", "http://localhost:5173"]}, 9000)
        self.assertIn(sec.HostRule("board.example.com", True), extra)
        self.assertIn(sec.HostRule("proxy.example.org", True), extra)
        self.assertIn(sec.HostRule("localhost:5173", False), extra)
        self.assertEqual(len(extra), 3 + 3, "틀린 항목만 버린다")


class Base(unittest.TestCase):
    build_kw = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.access = srv.Access(EK, VK, site="t-1", expires_at=0)
        self.httpd, self.store = srv.build(self.tmp.name, HERE / "web" / "dist", access=self.access, save_interval=0.2, **self.build_kw)
        self.port = self.httpd.server_address[1]
        self.hosts = self.httpd.tb.hosts
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def H(self, host=None, key=EK, name="tester", **more):
        h = {"Host": host or f"127.0.0.1:{self.port}", "X-Tb-Name": name, "X-Whiteboard": "1", "Content-Type": "application/json"}
        if key:
            h["X-Tb-Key"] = key
        h.update(more)
        return h

    def board(self):
        st, _, d = raw(self.port, "POST", "/api/whiteboard/boards", self.H(), {"title": "b"})
        self.assertEqual(st, 201)
        return json.loads(d)["id"]


class HostCheck(Base):
    def test_unknown_host_is_421_everywhere(self):
        """An unknown Host gets 421 (/healthz, the page, and POST too). A local Host gets the same response as before."""
        p = self.port
        for path in ("/api/whiteboard/boards", "/healthz", "/", f"/?k={EK}"):
            self.assertEqual(raw(p, "GET", path, {"Host": "evil.example"})[0], 421, path)
        self.assertEqual(raw(p, "POST", "/api/whiteboard/boards", self.H(host="evil.example"), {"title": "x"})[0], 421)
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", {"Host": f"evil.example:{p}", "X-Tb-Key": EK, "X-Tb-Name": "a"})[0], 421)
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", {"Host": "127.0.0.1"})[0], 421, "포트 없는 로컬 Host 도 아니다")
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", {"Host": f"127.0.0.1:{p + 1}"})[0], 421)
        for h in (f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"):
            self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", {"Host": h})[0], 403, h)
            self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H(host=h))[0], 200, h)
            self.assertEqual(raw(p, "GET", "/healthz", {"Host": h})[0], 200, h)

    def test_quick_tunnel_narrowing(self):
        """Before the address is known, *.trycloudflare.com is accepted; once it is recorded in state, only that one name is."""
        p = self.port
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H(host="abc.trycloudflare.com"))[0], 421, "처음에는 로컬만")
        self.hosts.set(sec.rules_for_state({"tunnel": "cloudflared"}, p))
        for n in ("abc", "zzz-1"):
            self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H(host=f"{n}.trycloudflare.com"))[0], 200, n)
        self.hosts.set(sec.rules_for_state({"tunnel": "cloudflared", "public_url": "https://abc.trycloudflare.com"}, p))
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H(host="abc.trycloudflare.com"))[0], 200)
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H(host="zzz-1.trycloudflare.com"))[0], 421)
        self.assertEqual(raw(p, "GET", "/healthz", {"Host": "zzz-1.trycloudflare.com"})[0], 421)
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H())[0], 200, "로컬은 그대로")

    def test_ws_origin(self):
        """If the upgrade carries an Origin it must be an allowed Host. Without one (non-browser clients) only the key check applies."""
        bid, p = self.board(), self.port
        path = f"/api/whiteboard/boards/{bid}/ws?client=w1&since=0&epoch="
        base = {"Host": f"127.0.0.1:{p}", "X-Tb-Key": EK, "X-Tb-Name": "w"}
        for origin, want in (("https://evil.example", 403), ("null", 403), (f"https://127.0.0.1:{p}", 403),
                             (f"http://127.0.0.1:{p}", 101), (f"http://localhost:{p}", 101), (None, 101)):
            h = dict(base)
            if origin is not None:
                h["Origin"] = origin
            st, s, _ = ws_open(p, path, h)
            s.close()
            self.assertEqual(st, want, origin)
        st, s, _ = ws_open(p, path, {"Host": f"127.0.0.1:{p}", "X-Tb-Name": "w", "Origin": f"http://127.0.0.1:{p}"})
        s.close()
        self.assertEqual(st, 403, "Origin 이 맞아도 키는 따로 본다")
        self.hosts.set([sec.HostRule("abc.trycloudflare.com", public=True)])
        st, s, _ = ws_open(p, path, {**base, "Host": "abc.trycloudflare.com", "Origin": "https://abc.trycloudflare.com"})
        s.close()
        self.assertEqual(st, 101)

    def test_ipv6_literal_rule(self):
        self.assertTrue(self.hosts.match(f"[::1]:{self.port}"))
        self.assertIsNone(self.hosts.match("[::2]:1"))


class Cookies(Base):
    def setUp(self):
        super().setUp()
        self.hosts.set([sec.HostRule("abc.trycloudflare.com", public=True)])

    def test_host_prefix_cookie_on_public_only(self):
        """A public Host gets __Host- and Secure cookies; a local Host keeps the current name (no Secure). A spoofed header cannot add Secure."""
        st, hs, _ = raw(self.port, "GET", f"/?k={EK}", {"Host": "abc.trycloudflare.com"})
        self.assertEqual(st, 200)
        ck = header(hs, "Set-Cookie")[0]
        self.assertTrue(ck.startswith(f"__Host-tb_t1={EK}; Secure; Path=/; HttpOnly; SameSite=Lax"), ck)
        self.assertNotIn("Domain", ck)
        for forged in ({"X-Forwarded-Proto": "https"}, {"Cf-Visitor": '{"scheme":"https"}'}, {}):
            st, hs, _ = raw(self.port, "GET", f"/?k={EK}", {"Host": f"127.0.0.1:{self.port}", **forged})
            ck = header(hs, "Set-Cookie")[0]
            self.assertTrue(ck.startswith(f"tb_t1={EK}; Path=/"), ck)
            self.assertNotIn("Secure", ck, forged)
        st, hs, _ = raw(self.port, "POST", "/api/whiteboard/login", {"Host": "abc.trycloudflare.com", "X-Tb-Key": EK, "Content-Type": "application/json"}, {"name": "홍길동"})
        self.assertTrue(header(hs, "Set-Cookie")[0].startswith("__Host-wb_user=%ED%99%8D"), "이름 쿠키도 https 이름")
        st, hs, _ = raw(self.port, "POST", "/api/whiteboard/login", {"Host": f"127.0.0.1:{self.port}", "X-Tb-Key": EK, "Content-Type": "application/json"}, {"name": "a"})
        self.assertEqual(header(hs, "Set-Cookie")[0], "wb_user=a; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")

    def test_role_reads_both_cookie_names(self):
        for host, ck in (("abc.trycloudflare.com", f"__Host-tb_t1={EK}; __Host-wb_user=a"), (f"127.0.0.1:{self.port}", f"tb_t1={VK}; wb_user=b")):
            st, _, d = raw(self.port, "GET", "/api/whiteboard/me", {"Host": host, "Cookie": ck})
            self.assertEqual(st, 200, host)
            me = json.loads(d)
            self.assertEqual((me["role"], me["name"]), ("edit", "a") if "abc" in host else ("view", "b"))
        st, _, _ = raw(self.port, "GET", "/api/whiteboard/me", {"Host": f"127.0.0.1:{self.port}", "Cookie": "tb_t1=wrong; __Host-tb_t1=wrong2"})
        self.assertEqual(st, 403)

    def test_no_hsts_by_default(self):
        _, hs, _ = raw(self.port, "GET", "/healthz", {"Host": "abc.trycloudflare.com"})
        self.assertEqual(header(hs, "Strict-Transport-Security"), [], "임시 보드(quick tunnel)는 HSTS 를 보내지 않는다")


class Hsts(Base):
    build_kw = {"hsts": True, "hosts": ["board.example.com"]}

    def test_hsts_on_public_host_only(self):
        _, hs, _ = raw(self.port, "GET", "/api/whiteboard/boards", self.H(host="board.example.com"))
        self.assertEqual(header(hs, "Strict-Transport-Security"), ["max-age=15552000"])
        _, hs, _ = raw(self.port, "GET", "/healthz", {"Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(header(hs, "Strict-Transport-Security"), [])


class Reload(Base):
    def test_rotate_closes_streams_and_swaps_keys(self):
        """Server side: when reload changes the keys, the old key gets 403 and the new key 200, and open WebSockets / SSE streams are closed."""
        bid, p = self.board(), self.port
        st, s, f = ws_open(p, f"/api/whiteboard/boards/{bid}/ws?client=w1&since=0&epoch=", {"Host": f"127.0.0.1:{p}", "X-Tb-Key": EK, "X-Tb-Name": "w"})
        self.assertEqual(st, 101)
        ws_read(f), ws_read(f)   # hello/reset · presence
        sse = http.client.HTTPConnection("127.0.0.1", p, timeout=5)
        sse.request("GET", f"/api/whiteboard/boards/{bid}/events?client=s1", headers=self.H())
        r = sse.getresponse()
        self.assertEqual(r.status, 200)
        r.read1(4096) if hasattr(r, "read1") else None
        changed = self.access.reload({"edit_key": "NEWEDIT0000001", "view_key": "NEWVIEW0000001", "expires_at": 0})
        self.assertTrue(changed)
        t0 = time.time()
        while True:   # WebSocket: close frame or EOF
            op, _ = ws_read(f)
            if op in (None, 8):
                break
        s.close()
        got = b""
        try:
            while True:
                chunk = r.read1(4096) if hasattr(r, "read1") else r.read(1)
                if not chunk:
                    break
                got += chunk
        except (http.client.IncompleteRead, OSError):
            pass
        sse.close()
        self.assertLess(time.time() - t0, 3, "열린 흐름이 바로 닫힌다")
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H())[0], 403, "옛 키")
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", self.H(key="NEWEDIT0000001"))[0], 200)
        self.assertEqual(json.loads(raw(p, "GET", "/api/whiteboard/me", self.H(key="NEWVIEW0000001"))[2])["role"], "view")
        self.assertFalse(self.access.reload({"edit_key": "NEWEDIT0000001", "view_key": "NEWVIEW0000001"}), "같으면 바뀐 것 없음")

    def test_empty_key_never_opens(self):
        """Re-reading an empty edit_key must not turn on open mode (everyone can edit); everything is blocked instead."""
        self.access.reload({"edit_key": "", "view_key": None})
        self.assertFalse(self.access.open)
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H())[0], 403)
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H(key=None))[0], 403)
        self.assertIsNone(self.access.role_of(""))


class Chain(unittest.TestCase):
    """Auth hook chain: None -> next hook (if all return None, the invite key is used); an Auth -> that role / user; deny -> 401 (does not fall through to the key)."""

    class Item:
        def __init__(self, fn):
            self.fn, self.seen = fn, []

        def authenticate(self, req):
            self.seen.append((req.host, req.is_local_host, req.public, req.path, req.client_ip))
            return self.fn(req)

    def start(self, *items, hosts=("board.example.com",)):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd, self.store = srv.build(self.tmp.name, HERE / "web" / "dist", access=srv.Access(EK, None, site="c"),
                                           authn=list(items), hosts=list(hosts))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def test_chain(self):
        def decide(req):
            tok = req.headers.get("Authorization")
            if tok == "Bearer agent-view":
                return srv.Auth("view", {"id": "agent:x", "name": "X · AI", "agent": True})
            if tok == "Bearer weird":
                return srv.Auth("owner", {"id": "w", "name": "w"})
            if req.public and not req.headers.get("Cf-Access-Jwt-Assertion"):
                return srv.Auth(deny=True)
            return None
        none_item = self.Item(lambda req: None)
        item = self.Item(decide)
        self.start(none_item, item)
        p, loc = self.port, f"127.0.0.1:{self.port}"
        st, _, d = raw(p, "POST", "/api/whiteboard/boards", {"Host": loc, "X-Tb-Key": EK, "X-Tb-Name": "%EC%82%AC%EB%9E%8C", "X-Whiteboard": "1"}, {"title": "t"})
        self.assertEqual(st, 201, "고리가 모두 None 이면 초대 키")
        bid = json.loads(d)["id"]
        self.assertEqual(none_item.seen[-1][:3], (loc, True, False))
        ag = {"Host": loc, "Authorization": "Bearer agent-view", "X-Tb-Name": "%EC%82%AC%EB%9E%8C", "X-Whiteboard": "1"}
        self.assertEqual(raw(p, "GET", f"/api/whiteboard/boards/{bid}", ag)[0], 200)
        self.assertEqual(raw(p, "POST", f"/api/whiteboard/boards/{bid}/elements", ag, {"client": "a", "elements": []})[0], 403)
        me = json.loads(raw(p, "GET", "/api/whiteboard/me", ag)[2])
        self.assertEqual((me["role"], me["name"]), ("view", "X · AI"), "고리의 user 가 X-Tb-Name 을 이긴다")
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", {"Host": loc, "Authorization": "Bearer weird"})[0], 403, "모르는 role 은 권한 없음")
        pub = {"Host": "board.example.com", "X-Tb-Key": EK, "X-Tb-Name": "a"}
        for path in ("/api/whiteboard/boards", "/", f"/?k={EK}", "/healthz"):
            self.assertEqual(raw(p, "GET", path, pub)[0], 401, path)
        self.assertEqual(raw(p, "POST", "/api/whiteboard/boards", {**pub, "X-Whiteboard": "1"}, {"title": "x"})[0], 401)
        self.assertEqual(item.seen[-1][1:3], (False, True), "공개 Host 는 로컬이 아니다")
        self.assertEqual(raw(p, "GET", "/api/whiteboard/boards", {**pub, "Cf-Access-Jwt-Assertion": "x"})[0], 200, "고리가 None → 키")
        st, s, _ = ws_open(p, f"/api/whiteboard/boards/{bid}/ws?client=a&since=0&epoch=", {"Host": loc, "Authorization": "Bearer agent-view", "X-Tb-Name": "z"})
        self.assertEqual(st, 101, "WebSocket 도 고리를 탄다")
        for _ in range(50):
            pres = self.store.get(bid).presence_list()
            if pres:
                break
            time.sleep(0.05)
        s.close()
        self.assertTrue(any(x["user"] == "X · AI" and x["role"] == "view" for x in pres), pres)

    def test_open_mode_refused_with_public_or_chain(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                srv.build(d, access=srv.Access(), hosts=["board.example.com"])
            with self.assertRaises(SystemExit):
                srv.build(d, access=srv.Access(), authn=[self.Item(lambda r: None)])
            h, _ = srv.build(d, access=srv.Access(), hosts=["http://localhost:5173"])   # a local name results in open mode
            port = h.server_address[1]
            threading.Thread(target=h.serve_forever, daemon=True).start()
            try:
                self.assertEqual(raw(port, "GET", "/api/whiteboard/boards", {"Host": "localhost:5173", "X-Tb-Name": "a"})[0], 200)
                h.tb.hosts.set(["board.example.com"])   # even if a public Host is added later, open mode is not opened to it
                self.assertEqual(raw(port, "GET", "/api/whiteboard/boards", {"Host": "board.example.com", "X-Tb-Name": "a"})[0], 403)
                self.assertEqual(raw(port, "GET", "/api/whiteboard/boards", {"Host": f"127.0.0.1:{port}", "X-Tb-Name": "a"})[0], 200)
            finally:
                h.shutdown()
                h.server_close()

    def test_trust_header_warns(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(err):
            h, _ = srv.build(d, trust_header="X-User")
            h.server_close()
        self.assertIn("위조", err.getvalue())



# ---------------------------------------------------------------- regressions from review round 1
class R1Poll(Base):
    def test_rotation_wakes_long_poll_and_old_key_gets_nothing(self):
        """A long-poll started with an old key does not receive new drawings after rotation (it wakes immediately and gets 403)."""
        bid, p = self.board(), self.port
        _, _, d = raw(p, "GET", f"/api/whiteboard/boards/{bid}", self.H())
        info = json.loads(d)
        res = {}

        def poller():
            t0 = time.time()
            st, _, body = raw(p, "GET", f"/api/whiteboard/boards/{bid}/poll?client=old&since={info['seq']}&pver={info['pver']}&epoch={srv.EPOCH}",
                              self.H(), timeout=30)
            res.update(st=st, body=body, dt=time.time() - t0)
        th = threading.Thread(target=poller)
        th.start()
        time.sleep(0.3)
        self.assertTrue(self.access.reload({"edit_key": "NEWEDIT0000001", "view_key": "NV0000000001"}))
        st, _, _ = raw(p, "POST", f"/api/whiteboard/boards/{bid}/elements", self.H(key="NEWEDIT0000001"),
                       {"client": "new", "elements": [{"id": "s1", "type": "text", "text": "SECRET", "version": 1, "versionNonce": 1}]})
        self.assertEqual(st, 200)
        th.join(10)
        self.assertEqual(res["st"], 403, res)
        self.assertNotIn(b"SECRET", res["body"])
        self.assertLess(res["dt"], 3, "회전이 기다리던 롱폴링을 바로 깨운다")
        st, _, body = raw(p, "GET", f"/api/whiteboard/boards/{bid}/poll?client=new2&since=0&pver=-1&epoch={srv.EPOCH}", self.H(key="NEWEDIT0000001"))
        self.assertEqual(st, 200)
        self.assertIn(b"SECRET", body, "새 키는 그대로 받는다")


class R1Misc(Base):
    def test_non_ascii_key_is_403_not_crash(self):
        """A non-ASCII key gets 403 (the handler thread does not die)."""
        st, _, _ = raw(self.port, "GET", "/api/whiteboard/boards", {"Host": f"127.0.0.1:{self.port}", "X-Tb-Key": "\xe9\xe9", "X-Tb-Name": "a"})
        self.assertEqual(st, 403)
        self.assertEqual(raw(self.port, "GET", "/?k=%ED%95%9C", {"Host": f"127.0.0.1:{self.port}"})[0], 403)

    def test_duplicate_host_and_absolute_form(self):
        """Two Host headers, or an absolute-form request target, get 400."""
        def send(lines):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            s.sendall(lines.encode())
            line = s.makefile("rb").readline()
            s.close()
            return int(line.split()[1])
        loc = f"127.0.0.1:{self.port}"
        self.assertEqual(send(f"GET /healthz HTTP/1.1\r\nHost: {loc}\r\nHost: evil.example\r\nConnection: close\r\n\r\n"), 400)
        self.assertEqual(send(f"GET http://evil.example/healthz HTTP/1.1\r\nHost: {loc}\r\nConnection: close\r\n\r\n"), 400)
        self.assertEqual(send(f"GET /healthz HTTP/1.1\r\nHost: {loc}\r\nConnection: close\r\n\r\n"), 200)

    def test_rule_details(self):
        """If the tunnel is abandoned the wildcard is not reopened, '.local' is http, and an unreadable public address drops only that entry."""
        self.assertNotIn(sec.HostRule("*.trycloudflare.com", True), sec.rules_for_state({"tunnel": "cloudflared", "error": "x"}, 9))
        self.assertFalse(sec.HostRule.parse("mymac.local:8790").public)
        self.assertTrue(sec.HostRule.parse("a_b.example.com").public)
        got = sec.rules_for_state({"tunnel": "named", "public_url": "https://bad host!", "hostname": "also bad!"}, 9)
        self.assertEqual(got, sec.local_rules(9))
        self.assertTrue(sec.is_loopback_ip("127.0.0.5") and sec.is_loopback_ip("::1") and sec.is_loopback_ip("::ffff:127.0.0.1"))
        self.assertFalse(sec.is_loopback_ip("147.47.1.1") or sec.is_loopback_ip("") or sec.is_loopback_ip("x"))


class R1Lan(unittest.TestCase):
    """0.0.0.0 (--lan): accepts any IP-literal Host whose port matches, plus <name>.local (other names get 421).
    is_local_host is true only when the direct peer is loopback."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.seen = []
        item = type("Rec", (), {"authenticate": lambda _s, req: self.seen.append((req.host, req.is_local_host, req.client_ip)) or None})()
        self.httpd, _ = srv.build(self.tmp.name, HERE / "web" / "dist", host="0.0.0.0", access=srv.Access(EK, None, site="l"), authn=[item])
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def get(self, host, addr="127.0.0.1"):
        c = http.client.HTTPConnection(addr, self.port, timeout=5)
        c.request("GET", "/api/whiteboard/boards", headers={"Host": host, "X-Tb-Key": EK, "X-Tb-Name": "a"})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def test_any_ip_literal_and_local_name(self):
        p = self.port
        for h in (f"192.168.77.5:{p}", f"10.0.0.9:{p}", f"147.47.66.137:{p}", f"127.0.0.1:{p}"):
            self.assertEqual(self.get(h), 200, h)
        for n in sec.local_names():
            self.assertEqual(self.get(f"{n}:{p}"), 200, n)
        for h in (f"evil.example:{p}", f"192.168.77.5:{p + 1}", "192.168.77.5", f"x.local:{p}"):
            self.assertEqual(self.get(h), 421, h)
        self.assertIn(sec.HostRule.any_ip_rule(p), sec.rules_for_state({"host": "0.0.0.0"}, p))

    def test_is_local_host_needs_loopback_peer(self):
        p = self.port
        self.get(f"127.0.0.1:{p}")
        self.assertEqual(self.seen[-1][1:], (True, "127.0.0.1"))
        lan = sec.lan_ip()
        if lan == "127.0.0.1":
            self.skipTest("LAN 주소 없음")
        self.get(f"127.0.0.1:{p}", addr=lan)   # same as someone on the same Wi-Fi spoofing the Host
        self.assertEqual(self.seen[-1], (f"127.0.0.1:{p}", False, lan))


class R1Revoke(unittest.TestCase):
    """Hook answers are re-verified while connected: immediately via access.bump(predicate), and by the periodic check once Auth.expires_at has passed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.valid = {"tok-a": True, "tok-b": True}
        self.exp = {}

        class Tok:
            def authenticate(_s, req):
                t = (req.headers.get("Authorization") or "")[7:]
                if t not in self.valid:
                    return None
                if not self.valid[t]:
                    return srv.Auth(deny=True)
                return srv.Auth("edit", {"id": "agent:" + t, "name": t + " · AI", "agent": True}, expires_at=self.exp.get(t))
        self.access = srv.Access(EK, None, site="r")
        self.httpd, self.store = srv.build(self.tmp.name, HERE / "web" / "dist", access=self.access, authn=[Tok()], revalidate_interval=0.2)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        st, _, d = raw(self.port, "POST", "/api/whiteboard/boards", {"Host": f"127.0.0.1:{self.port}", "X-Tb-Key": EK, "X-Tb-Name": "a", "X-Whiteboard": "1"}, {"title": "r"})
        self.bid = json.loads(d)["id"]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def ws(self, tok, client):
        st, s, f = ws_open(self.port, f"/api/whiteboard/boards/{self.bid}/ws?client={client}&since=0&epoch=",
                           {"Host": f"127.0.0.1:{self.port}", "Authorization": "Bearer " + tok})
        self.assertEqual(st, 101)
        ws_read(f), ws_read(f)
        s.settimeout(3)
        return s, f

    def closed(self, f):
        try:
            while True:
                op, _ = ws_read(f)
                if op in (None, 8):
                    return True
        except socket.timeout:
            return False

    def test_bump_closes_only_revoked_streams(self):
        sa, fa = self.ws("tok-a", "ca")
        sb, fb = self.ws("tok-b", "cb")
        self.valid["tok-a"] = False   # revoked
        t0 = time.time()
        self.access.bump(lambda h: not h.revalidate())
        self.assertTrue(self.closed(fa), "취소된 토큰의 WebSocket 은 닫힌다")
        self.assertLess(time.time() - t0, 1.5)
        sb.settimeout(0.6)
        self.assertFalse(self.closed(fb), "다른 토큰은 그대로")
        sa.close(); sb.close()
        h = {"Host": f"127.0.0.1:{self.port}", "Authorization": "Bearer tok-a"}
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", h)[0], 401)

    def test_expiry_closes_stream_without_bump(self):
        self.exp["tok-a"] = time.time() + 0.6
        sa, fa = self.ws("tok-a", "ca")
        self.valid["tok-a"] = False   # after expiry, asking again is denied -- but nobody calls bump
        t0 = time.time()
        self.assertTrue(self.closed(fa), "답의 expires_at 이 지나면 주기 점검이 닫는다")
        self.assertLess(time.time() - t0, 2.5)
        sa.close()

    def test_ws_write_after_revoke_is_dropped(self):
        """Even before the periodic check, writes re-check permission (cache invalidated via gen) -- after revocation, drawings are not accepted."""
        sa, fa = self.ws("tok-a", "ca")
        self.valid["tok-a"] = False
        self.access.bump(lambda h: False)   # only invalidates the cache, closes nothing
        data = json.dumps({"t": "elements", "id": 1, "elements": [{"id": "late", "version": 1, "versionNonce": 1}]}).encode()
        mask = os.urandom(4)
        sa.sendall(bytes([0x81, 0x80 | len(data)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))
        self.assertTrue(self.closed(fa))
        sa.close()
        self.assertNotIn("late", self.store.get(self.bid).elements)


if __name__ == "__main__":
    unittest.main()
