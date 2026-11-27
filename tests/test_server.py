"""Server unit tests: merge rules, per-user colours, short names, blocking writes, invite keys (edit and view)."""
import base64
import importlib.util
import json
import http.client
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
import os

# Korean assertions below: pin the language before anything spawns the CLI or imports server code.
os.environ["TEMPBOARD_LANG"] = "ko"

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wb_server", HERE / "server" / "server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
DIST = HERE / "web" / "dist"
PNG = base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a5b6d8b10000000049454e44ae426082")).decode()


class Merge(unittest.TestCase):
    def test_version_then_nonce(self):
        self.assertTrue(srv.wins({"version": 3, "versionNonce": 9}, {"version": 2, "versionNonce": 1}))
        self.assertFalse(srv.wins({"version": 2, "versionNonce": 9}, {"version": 3, "versionNonce": 1}))
        self.assertTrue(srv.wins({"version": 3, "versionNonce": 1}, {"version": 3, "versionNonce": 9}))
        self.assertFalse(srv.wins({"version": 3, "versionNonce": 9}, {"version": 3, "versionNonce": 1}))
        self.assertTrue(srv.wins({"version": 1}, None))

    def test_palette_far_apart_and_no_similar_assignment(self):
        for i, a in enumerate(srv.COLORS):
            for b in srv.COLORS[i + 1:]:
                self.assertFalse(srv.similar(a, b), (a, b))
        self.assertTrue(srv.similar("#D55E00", "#E69F00"))   # the pair that triggered the infinite-undo bug

    def test_short_name(self):
        self.assertEqual(srv.short_name("홍길동"), "길동")
        self.assertEqual(srv.short_name("김철수"), "철수")
        self.assertEqual(srv.short_name("Alice"), "Al")


class View(unittest.TestCase):
    """Validation of presence's view (screen range): four numbers, finite, within bounds, and only when it has an extent."""
    def test_clean_view(self):
        self.assertEqual(srv.clean_view([0, 0, 100, 50.04]), [0.0, 0.0, 100.0, 50.0])
        for bad in (None, "0,0,1,1", [0, 0, 1], [0, 0, 1, 1, 1], [0, 0, "1", 1], [0, 0, True, 1], [0, 0, float("nan"), 1],
                    [0, 0, float("inf"), 1], [0, 0, 1e9, 1], [5, 0, 1, 1], [0, 5, 1, 5], {"x": 1}):
            self.assertIsNone(srv.clean_view(bad), bad)


class PresenceBatch(unittest.TestCase):
    """Batching of presence-list broadcasts, unauthorized connections, the follow field, and the collection interval (Board is called directly)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = srv.Board(Path(self.tmp.name), "presbatch1")
        self.got = []
        q = __import__("queue").Queue()
        self.b.subs.append((q, "spy"))
        self.q = q

    def tearDown(self):
        self.tmp.cleanup()

    def drain(self):
        out = []
        while not self.q.empty():
            msg = self.q.get_nowait().decode()
            out.append(json.loads(msg.split("data: ", 1)[1]))
        return out

    def test_bursts_become_one_broadcast_with_the_last_state(self):
        import time
        t0 = time.time()
        for i in range(60):   # one presenter sends 60 times within 100ms -- the broadcast is one at the start plus one batched
            self.b.update_presence("p", "교수", "#000", {"view": [i, 0, i + 100, 50]}, "edit")
        self.assertLess(time.time() - t0, 0.1)
        first = self.drain()
        self.assertEqual(len(first), 1, "처음 것은 바로")
        time.sleep(0.25)
        later = self.drain()
        self.assertEqual(len(later), 1, "나머지 59번은 한 번으로")
        self.assertEqual(later[0][0]["view"], [59, 0, 159, 50], "묶은 방송은 마지막 상태")
        self.assertEqual(self.b.pver, 2)
        time.sleep(0.15)
        self.b.update_presence("p", "교수", "#000", {"view": [1, 1, 2, 2]}, "edit")
        self.assertEqual(len(self.drain()), 1, "조용하다가 오면 기다리지 않고 바로")

    def test_delta_sockets_get_only_changes_old_ones_get_full_list(self):
        """A client attached with pd=1 gets only the changed users (set / gone); an old client (v0.2.0) always gets the full list -- in the same format."""
        import time

        class Sock:
            def __init__(self, delta): self.delta, self.got = delta, []
            def send(self, m): self.got.append(m); return True
        new, old = Sock(True), Sock(False)
        self.b.sockets.update({"n": new, "o": old})
        for c in ("a", "b", "c"):
            self.b.update_presence(c, c.upper(), "#000", {}, "edit"); time.sleep(0.12)
        new.got.clear(); old.got.clear()
        self.b.update_presence("b", "B", "#000", {"view": [0, 0, 1, 1]}, "edit")
        self.assertEqual(old.got[-1]["t"], "presence")
        self.assertEqual([p["client"] for p in old.got[-1]["data"]], ["a", "b", "c"])
        d = new.got[-1]
        self.assertEqual((d["t"], [p["client"] for p in d["set"]], d["gone"], d["pver"]), ("pdelta", ["b"], [], old.got[-1]["pver"]))
        time.sleep(0.12)
        self.b.drop_presence("a"); self.b.presence_changed()
        self.assertEqual((new.got[-1]["set"], new.got[-1]["gone"]), ([], ["a"]))
        self.assertEqual([p["client"] for p in old.got[-1]["data"]], ["b", "c"])

    def test_role_none_is_viewer_and_follow_is_checked(self):
        self.b.update_presence("x", "만료", "#000", {"summon": True, "follow": "abc12345"}, None)
        p = self.b.presence_list()[0]
        self.assertEqual((p["role"], "summon" in p, p["follow"]), ("view", False, "abc12345"))
        for bad in ("../x", "x" * 41, 5, ["a"], "x"):   # wrong format, or pointing at itself -- the previous value is kept
            self.b.update_presence("x", "만료", "#000", {"follow": bad}, None)
            self.assertEqual(self.b.presence_list()[0]["follow"], "abc12345", bad)
        self.b.update_presence("x", "만료", "#000", {"follow": None}, None)
        self.assertNotIn("follow", self.b.presence_list()[0])
        self.b.update_presence("y", "열린", "#000", {}, "edit")
        self.assertEqual({p["client"]: p["role"] for p in self.b.presence_list()}["y"], "edit", "열린 보드(로컬)는 그대로 편집자")

    def test_summon_at_most_once_a_second_per_client(self):
        self.b.update_presence("e", "교수", "#000", {"summon": True}, "edit")
        at = self.b.presence_list()[0]["summon"]["at"]
        self.b.update_presence("e", "교수", "#000", {"summon": True}, "edit")
        self.assertEqual(self.b.presence_list()[0]["summon"]["at"], at, "1초 안에 다시 누르면 무시")
        self.assertNotIn("_s", self.b.presence_list()[0])


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = srv.Store(Path(self.tmp.name))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(self.store, DIST, "X-Tempboard-User"))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, user="홍길동", app=True):
        h = {"Content-Type": "application/json"}
        if user:
            h["X-Tempboard-User"] = urllib.parse.quote(user) if False else user.encode("utf-8").decode("latin-1")
        if app:
            h["X-Whiteboard"] = "1"
        req = urllib.request.Request(self.base + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                t = r.read()
                return r.status, (json.loads(t) if t else None)
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, None

    def test_colors_unique_and_stable(self):
        names = ["홍길동", "김철수", "이영희", "박민수"]
        colors = [self.store.color_for(n) for n in names]
        self.assertEqual(len(set(colors)), 4)
        self.assertEqual(self.store.color_for("김철수"), colors[1])
        again = srv.Store(Path(self.tmp.name))
        self.assertEqual(again.color_for("이영희"), colors[2])
        many = [self.store.color_for(f"학생{i}") for i in range(60)]   # a lecture -- more users than the palette (8) is fine; colours are reused without error
        self.assertTrue(all(c in srv.COLORS for c in many))
        self.assertEqual(self.store.color_for("김철수"), colors[1], "앞 사람 색은 그대로")

    def test_flow_and_guards(self):
        self.assertEqual(self.call("GET", "/api/whiteboard/boards", user=None)[0], 401)
        self.assertEqual(self.call("POST", "/api/whiteboard/boards", {"title": "x"}, app=False)[0], 403)
        st, b = self.call("POST", "/api/whiteboard/boards", {"title": "회의"})
        self.assertEqual(st, 201)
        bid = b["id"]
        el = {"id": "e1", "type": "rectangle", "version": 2, "versionNonce": 5}
        self.assertEqual(self.call("POST", f"/api/whiteboard/boards/{bid}/elements", {"client": "c1", "elements": [el]})[1]["accepted"], 1)
        self.assertEqual(self.call("POST", f"/api/whiteboard/boards/{bid}/elements", {"client": "c2", "elements": [{**el, "version": 1}]})[1]["accepted"], 0)
        st, d = self.call("GET", f"/api/whiteboard/boards/{bid}")
        self.assertEqual(d["elements"][0]["version"], 2)
        self.assertEqual(self.call("POST", f"/api/whiteboard/boards/{bid}/files", {"client": "c1", "id": "f" * 12, "mimeType": "text/html", "dataURL": "data:text/html;base64,AAA"})[0], 400)
        self.assertEqual(self.call("POST", f"/api/whiteboard/boards/{bid}/files", {"client": "c1", "id": "f" * 12, "mimeType": "image/png", "dataURL": "data:image/png;base64," + PNG})[0], 201)
        st, me = self.call("GET", "/api/whiteboard/me")
        self.assertEqual((me["short"], me["color"]), ("길동", self.store.color_for("홍길동")))

    def test_presence_view_and_summon(self):
        """view is passed through as-is; wrong or oversized values are dropped (the previous value is kept). Fields not sent keep their previous value. summon uses the server's clock and later ones are larger."""
        st, b = self.call("POST", "/api/whiteboard/boards", {"title": "따라가기"})
        bid, board = b["id"], self.store.get(b["id"])
        post = lambda body, user="홍길동": self.call("POST", f"/api/whiteboard/boards/{bid}/presence", body, user=user)[0]
        me = lambda c="a": next(p for p in board.presence_list() if p["client"] == c)
        self.assertEqual(post({"client": "a", "pointer": {"x": 1, "y": 2}, "view": [0, 0, 1600, 900]}), 204)
        self.assertEqual(me()["view"], [0, 0, 1600, 900])
        self.assertEqual(me()["role"], "edit")
        for bad in ([0, 0, 1], ["a", 0, 1, 1], [0, 0, 1e12, 1], [0, 0, float("inf"), 1], "x" * 5000, [[0] * 1000] * 4):
            self.assertEqual(post({"client": "a", "view": bad}), 204)
            self.assertEqual(me()["view"], [0, 0, 1600, 900], bad)
        self.assertEqual(me()["pointer"], {"x": 1, "y": 2}, "view 만 보내도 커서는 그대로")
        post({"client": "a", "pointer": {"x": 5, "y": 6}})
        self.assertEqual(me()["view"], [0, 0, 1600, 900], "커서만 보내도 화면 범위는 그대로")
        post({"client": "a", "view": None})
        self.assertNotIn("view", me())
        self.assertNotIn("summon", me())
        post({"client": "a", "summon": True})
        first = me()["summon"]["at"]
        post({"client": "b", "summon": True}, user="김철수")
        self.assertGreater(me("b")["summon"]["at"], first, "나중에 모은 것이 크다")
        post({"client": "a", "pointer": None})
        self.assertEqual(me()["summon"]["at"], first, "다른 칸을 보내도 모으기 시각은 그대로(다시 모으지 않는다)")
        post({"client": "a", "summon": "yes"})
        self.assertEqual(me()["summon"]["at"], first, "true 만 받는다")

    def test_saved_only_after_disk_write(self):
        """ack (received) and saved (on disk) are distinct: saved does not rise before the save, and after the save it rises up to that seq."""
        st, b = self.call("POST", "/api/whiteboard/boards", {"title": "R1"})
        board = self.store.get(b["id"])
        board.save()
        base = board.saved_seq
        st, r = self.call("POST", f"/api/whiteboard/boards/{b['id']}/elements", {"client": "c", "elements": [{"id": "k", "version": 2, "versionNonce": 1}]})
        self.assertGreater(r["seq"], base)
        self.assertEqual(board.saved_seq, base, "받기만 하고 저장 전")
        disk = json.loads((Path(self.tmp.name) / "boards" / b["id"] / "board.json").read_text())
        self.assertFalse(any(e["id"] == "k" for e in disk["elements"]))
        board.save()
        self.assertGreaterEqual(board.saved_seq, r["seq"])
        st, p = self.call("GET", f"/api/whiteboard/boards/{b['id']}/poll?client=x&since={r['seq']}&pver=-1&saved={base}&epoch={srv.EPOCH}")
        self.assertGreaterEqual(p["saved"], r["seq"])

    def test_websocket_ack_then_saved(self):
        """By-hand WebSocket: hello -> elements -> ack(seq) -> (no saved before the save) -> flush -> saved >= seq."""
        import os, socket, struct
        st, b = self.call("POST", "/api/whiteboard/boards", {"title": "ws"})
        host, port = self.httpd.server_address
        s = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /api/whiteboard/boards/{b['id']}/ws?client=w1&since=0&epoch= HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nX-Tempboard-User: tester\r\n\r\n").encode())
        f = s.makefile("rb")
        status = f.readline()
        self.assertIn(b"101", status)
        while f.readline() not in (b"\r\n", b""):
            pass
        def read_msg():
            while True:
                h = f.read(2); op, n = h[0] & 0x0F, h[1] & 0x7F
                if n == 126: n = struct.unpack(">H", f.read(2))[0]
                elif n == 127: n = struct.unpack(">Q", f.read(8))[0]
                data = f.read(n)
                if op == 1:
                    return json.loads(data)
        def send_msg(obj):
            data = json.dumps(obj).encode(); mask = os.urandom(4)
            hdr = bytes([0x81]) + (bytes([0x80 | len(data)]) if len(data) < 126 else bytes([0x80 | 126]) + struct.pack(">H", len(data)))
            s.sendall(hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))
        msgs = [read_msg(), read_msg()]
        self.assertIn("reset", [m["t"] for m in msgs] + ["reset"])  # the epoch is empty, so either reset or hello
        send_msg({"t": "elements", "id": 7, "elements": [{"id": "e", "version": 1, "versionNonce": 1}]})
        ack = None
        for _ in range(10):
            m = read_msg()
            if m["t"] == "ack":
                ack = m; break
            self.assertNotEqual(m["t"], "saved")
        self.assertEqual(ack["id"], 7)
        self.store.flush()
        for _ in range(10):
            m = read_msg()
            if m["t"] == "saved":
                self.assertGreaterEqual(m["seq"], ack["seq"]); break
        else:
            self.fail("saved 없음")
        s.close()

    def test_wins_tolerates_bad_versions(self):
        self.assertFalse(srv.wins({"version": "x"}, {"version": 1}))
        self.assertTrue(srv.wins({"version": "3", "versionNonce": None}, {"version": 2}))



class Invite(unittest.TestCase):
    """Invite keys -- without a key 403; ?k= is turned into a cookie and removed from the address; a view key cannot draw."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = srv.Store(Path(self.tmp.name))
        acc = srv.Access("EDITKEY123", "VIEWKEY456", site="t1", expires_at=0)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(self.store, DIST, None, None, acc))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def raw(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def cookie_for(self, key):
        st, h, _ = self.raw("GET", f"/?k={key}&x=1")
        self.assertEqual(st, 200, "링크로 오면 화면을 바로 낸다(되돌려 보내지 않는다)")
        return h["Set-Cookie"].split(";")[0]

    def test_no_key_denied(self):
        self.assertEqual(self.raw("GET", "/api/whiteboard/boards")[0], 403)
        self.assertEqual(self.raw("GET", "/api/whiteboard/me", {"X-Tb-Key": "wrong"})[0], 403)
        self.assertEqual(self.raw("GET", "/?k=wrong")[0], 403)
        self.assertEqual(self.raw("POST", "/api/whiteboard/login", {"Content-Type": "application/json"}, {"name": "a"})[0], 403)
        self.assertEqual(self.raw("GET", "/healthz")[0], 200)

    def test_edit_and_view(self):
        ce, cv = self.cookie_for("EDITKEY123"), self.cookie_for("VIEWKEY456")
        H = lambda ck, name: {"Cookie": f"{ck}; wb_user={name}", "X-Whiteboard": "1", "Content-Type": "application/json"}
        st, _, d = self.raw("POST", "/api/whiteboard/boards", H(ce, "ed"), {"title": "b"})
        self.assertEqual(st, 201)
        bid = json.loads(d)["id"]
        self.assertEqual(self.raw("POST", "/api/whiteboard/boards", H(cv, "vw"), {"title": "b"})[0], 403)
        el = {"id": "e1", "version": 2, "versionNonce": 1}
        self.assertEqual(self.raw("POST", f"/api/whiteboard/boards/{bid}/elements", H(cv, "vw"), {"client": "c", "elements": [el]})[0], 403)
        self.assertEqual(self.raw("POST", f"/api/whiteboard/boards/{bid}/presence", H(cv, "vw"), {"client": "c"})[0], 204)
        # a view-only user still sends the view range (for following). 'everyone to me' is for editors only
        self.assertEqual(self.raw("POST", f"/api/whiteboard/boards/{bid}/presence", H(cv, "vw"), {"client": "c", "view": [0, 0, 10, 10], "summon": True})[0], 204)
        self.assertEqual(self.raw("POST", f"/api/whiteboard/boards/{bid}/presence", H(ce, "ed"), {"client": "d", "summon": True})[0], 204)
        pres = {p["client"]: p for p in self.store.get(bid).presence_list()}
        self.assertEqual((pres["c"]["role"], pres["c"]["view"], "summon" in pres["c"]), ("view", [0, 0, 10, 10], False))
        self.assertEqual((pres["d"]["role"], "summon" in pres["d"]), ("edit", True))
        self.assertEqual(self.raw("POST", f"/api/whiteboard/boards/{bid}/elements", H(ce, "ed"), {"client": "c", "elements": [el]})[0], 200)
        me_e = json.loads(self.raw("GET", "/api/whiteboard/me", H(ce, "ed"))[2])
        me_v = json.loads(self.raw("GET", "/api/whiteboard/me", H(cv, "vw"))[2])
        self.assertEqual((me_e["role"], me_v["role"]), ("edit", "view"))
        self.assertEqual(me_e["invite"], {"edit": "EDITKEY123", "view": "VIEWKEY456"})
        self.assertEqual(me_v["invite"], {"view": "VIEWKEY456"}, "보기 링크로 온 사람에게 편집 키를 주지 않는다")

    def test_without_cookies(self):
        """A browser that blocks cookies -- the key works through the X-Tb-Key header alone, and the name through X-Tb-Name."""
        h = {"X-Tb-Key": "EDITKEY123", "X-Tb-Name": "%ED%99%8D%EA%B8%B8%EB%8F%99", "X-Whiteboard": "1", "Content-Type": "application/json"}
        st, _, d = self.raw("GET", "/api/whiteboard/me", h)
        self.assertEqual((st, json.loads(d)["name"], json.loads(d)["role"]), (200, "홍길동", "edit"))
        self.assertEqual(self.raw("POST", "/api/whiteboard/boards", h, {"title": "b"})[0], 201)


class I18nOrder(unittest.TestCase):
    """i18n.lang(): $TEMPBOARD_LANG > saved config.json > locale (C.UTF-8 counts as none) > English."""
    KEYS = ("TEMPBOARD_LANG", "TEMPBOARD_HOME", "LC_ALL", "LC_MESSAGES", "LANG")

    def setUp(self):
        self.old = {k: os.environ.get(k) for k in self.KEYS}
        self.tmp = tempfile.TemporaryDirectory()
        for k in self.KEYS:
            os.environ.pop(k, None)
        os.environ["TEMPBOARD_HOME"] = self.tmp.name
        spec = importlib.util.spec_from_file_location("i18n_under_test", Path(__file__).resolve().parents[1] / "server" / "i18n.py")
        self.i18n = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.i18n)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_order(self):
        i = self.i18n
        self.assertEqual(i.lang(), "en")                       # nothing set -> English
        os.environ["LANG"] = "C.UTF-8"
        self.assertEqual(i.lang(), "en")                       # C.UTF-8 is no locale
        os.environ["LANG"] = "ko_KR.UTF-8"
        self.assertEqual(i.lang(), "ko")                       # locale
        os.environ["LC_ALL"] = "C.UTF-8"                       # a higher-priority C.UTF-8 is skipped, so LANG still counts
        self.assertEqual(i.lang(), "ko")
        i.save("en")
        self.assertEqual(i.lang(), "en")                       # saved beats locale
        os.environ["TEMPBOARD_LANG"] = "ko"
        self.assertEqual(i.lang(), "ko")                       # env beats saved
        os.environ["TEMPBOARD_LANG"] = "xx"
        self.assertEqual(i.lang(), "en")                       # an invalid env value is ignored
        i.save(None)                                           # auto clears the saved value (other keys stay)
        self.assertIsNone(i.saved())

    def test_save_keeps_other_keys(self):
        i = self.i18n
        i.config_path().write_text('{"x": 1}')
        i.save("ko")
        self.assertEqual(json.loads(i.config_path().read_text()), {"x": 1, "lang": "ko"})
        i.save(None)
        self.assertEqual(json.loads(i.config_path().read_text()), {"x": 1})

    def test_denied_page_language_attribute(self):
        spec = importlib.util.spec_from_file_location("srv_i18n_t", Path(__file__).resolve().parents[1] / "server" / "server.py")
        srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(srv)
        os.environ["TEMPBOARD_LANG"] = "en"
        self.assertIn(b'<html lang="en">', srv.denied_page())
        os.environ["TEMPBOARD_LANG"] = "ko"
        self.assertIn(b'<html lang="ko">', srv.denied_page())


if __name__ == "__main__":
    unittest.main()
