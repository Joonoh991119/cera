"""CLI tests: state-file locking (concurrent writes keep every field), keys --rotate (within 2 seconds the old key gets 403 and the new key 200, open WS closed),
start --json prints exactly one JSON document (even on the npm build path), the extension slot (EXT_MODULES), and Python 3.9 (/usr/bin/python3).

Every test uses a temporary CERA_HOME (~/.cera is never touched). No real cloudflared tunnel is opened."""
from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

# Korean assertions below: pin the language before anything spawns the CLI or imports server code.
os.environ["CERA_LANG"] = "ko"

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "cera"
PY39 = Path("/usr/bin/python3")


def run(args, env, py=None, timeout=60, root=None, check=True):
    p = subprocess.run([py or sys.executable, str((root or HERE) / "bin" / "cera"), *args], env=env, capture_output=True,
                       text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise AssertionError(f"cera {args} → {p.returncode}\nstdout:{p.stdout}\nstderr:{p.stderr}")
    return p


def get(port, path, headers):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers=headers)
    r = c.getresponse()
    r.read()
    c.close()
    return r.status


def copy_tree(dst: Path):
    """A fake repository holding only bin/cera and server/{server,security,supervise,i18n,env}.py (ROOT becomes this)."""
    (dst / "bin").mkdir(parents=True)
    (dst / "server").mkdir()
    shutil.copy(CLI, dst / "bin" / "cera")
    for n in ("server.py", "security.py", "supervise.py", "i18n.py", "env.py"):
        shutil.copy(HERE / "server" / n, dst / "server" / n)
    return dst


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.dist = self.t / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<!doctype html><title>tb</title>")
        self.env = {**os.environ, "CERA_HOME": str(self.t / "home"), "CERA_DIST": str(self.dist)}
        self.started = []

    def tearDown(self):
        for sid in self.started:
            run(["stop", sid], self.env, check=False)
        self.tmp.cleanup()

    def start(self, *extra, root=None, env=None):
        p = run(["start", "--tunnel", "none", "--view-link", "--title", "t", "--json", *extra], env or self.env, root=root)
        out = json.loads(p.stdout)
        self.started.append(out["id"])
        return out


class StateLock(Base):
    def test_concurrent_writers_keep_both_fields(self):
        """Overlapping a simulated supervisor writing public_url (50 times) with `keys --rotate all` (50 times) keeps both fields."""
        sid = "0101-0000-abcd"
        d = self.t / "home" / "sites" / sid
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({"id": sid, "status": "stopped", "edit_key": "k0", "view_key": "v0",
                                                  "local_url": "http://127.0.0.1:1", "tunnel": "cloudflared", "host": "127.0.0.1"}))
        writer = textwrap.dedent(f"""
            import importlib.machinery, importlib.util, sys, time
            loader = importlib.machinery.SourceFileLoader("tbcli", {str(CLI)!r})
            spec = importlib.util.spec_from_loader("tbcli", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
            for i in range(50):
                def fn(cur, i=i):
                    cur["public_url"] = "https://w%d.trycloudflare.com" % i
                    cur["a_n"] = cur.get("a_n", 0) + 1
                m.update_state({sid!r}, fn)
                time.sleep(0.06)   # keys --rotate 50번(프로세스마다 ~0.1초)과 시간이 겹치게
        """)
        a = subprocess.Popen([sys.executable, "-c", writer], env=self.env)
        last = None
        for _ in range(50):
            last = json.loads(run(["keys", sid, "--rotate", "all", "--json"], self.env).stdout)
        self.assertEqual(a.wait(60), 0)
        st = json.loads((d / "state.json").read_text())
        self.assertEqual(st["a_n"], 50, "감독 쪽 쓰기가 하나도 사라지지 않았다")
        self.assertEqual(st["public_url"], "https://w49.trycloudflare.com")
        self.assertIn(f"?k={st['edit_key']}", last["links"]["edit"], "마지막 회전 키가 남았다")
        self.assertIn(f"?k={st['view_key']}", last["links"]["view"])
        self.assertIn("keys_rotated_at", st)
        self.assertEqual(stat.S_IMODE((d / "state.json").stat().st_mode), 0o600)
        self.assertEqual([p.name for p in d.glob("*.tmp")], [], "임시 파일이 남지 않는다")


    def test_lock_prevents_lost_updates(self):
        """The lock really works -- two processes that are slow between read and write each raise their own field 50 times and both end at 50.
        (In a control run with locking disabled, updates were lost, e.g. 26 and 24.)"""
        sites = self.t / "home" / "sites"
        (sites / "s1").mkdir(parents=True)
        code = textwrap.dedent(f"""
            import importlib.util, sys, time
            s = importlib.util.spec_from_file_location("sv", {str(HERE / "server" / "supervise.py")!r})
            m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
            k = sys.argv[1]
            def fn(c):
                c[k] = c.get(k, 0) + 1
                time.sleep(0.005)
            for _ in range(50):
                m.update_state({str(sites)!r}, "s1", fn)
        """)
        ps = [subprocess.Popen([sys.executable, "-c", code, k]) for k in ("a", "b")]
        self.assertEqual([p.wait(60) for p in ps], [0, 0])
        st = json.loads((sites / "s1" / "state.json").read_text())
        self.assertEqual((st["a"], st["b"]), (50, 50))


def ws_upgrade(port, path, headers):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    extra = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{extra}\r\n").encode())
    f = s.makefile("rb")
    code = int(f.readline().split()[1])
    while f.readline() not in (b"\r\n", b""):
        pass
    return code, s, f


def ws_frame(f):
    h = f.read(2)
    if len(h) < 2:
        return None
    n = h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", f.read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", f.read(8))[0]
    f.read(n)
    return h[0] & 0x0F


class Rotate(Base):
    def test_rotate_all_within_two_seconds(self):
        """On a running site, within 2 seconds of keys --rotate all the old key gets 403 and the new key 200, open WebSockets close, and links shows the new key."""
        out = self.start()
        sid, edit = out["id"], out["links"]["edit"]
        port = int(edit.split(":")[2].split("/")[0])
        old = edit.split("?k=")[1].split("#")[0]
        board = edit.split("#b=")[1]
        H = lambda k: {"Host": f"127.0.0.1:{port}", "X-Tb-Key": k, "X-Tb-Name": "a"}
        self.assertEqual(get(port, "/api/whiteboard/boards", H(old)), 200)
        code, s, f = ws_upgrade(port, f"/api/whiteboard/boards/{board}/ws?client=c1&since=0&epoch=", {"X-Tb-Key": old, "X-Tb-Name": "a",
                                                                                                    "Origin": f"http://127.0.0.1:{port}"})
        self.assertEqual(code, 101)
        ws_frame(f), ws_frame(f)
        p = run(["keys", sid, "--rotate", "all"], self.env)
        self.assertIn("바꿨다", p.stdout)
        t0 = time.time()
        new = json.loads(run(["links", sid, "--json"], self.env).stdout)["links"]
        nk = new["edit"].split("?k=")[1].split("#")[0]
        self.assertNotEqual(nk, old)
        while get(port, "/api/whiteboard/boards", H(old)) != 403:
            self.assertLess(time.time() - t0, 2.0, "2초 안에 옛 키가 막혀야 한다")
            time.sleep(0.1)
        self.assertEqual(get(port, "/api/whiteboard/boards", H(nk)), 200)
        vk = new["view"].split("?k=")[1].split("#")[0]
        self.assertNotEqual(vk, out["links"]["view"].split("?k=")[1].split("#")[0])
        self.assertEqual(get(port, "/api/whiteboard/me", H(vk)), 200)
        s.settimeout(2.5)
        while True:
            op = ws_frame(f)
            if op in (None, 8):
                break
        s.close()
        self.assertLess(time.time() - t0, 3.0, "열린 WebSocket 도 곧 닫힌다")
        self.assertIn(nk, run(["links", sid], self.env).stdout)
        with self.assertRaises(AssertionError):
            run(["keys", "nope-site", "--rotate", "all"], self.env)

    def test_rotate_view_needs_view_link(self):
        p = run(["start", "--tunnel", "none", "--json"], self.env)
        sid = json.loads(p.stdout)["id"]
        self.started.append(sid)
        r = run(["keys", sid, "--rotate", "view"], self.env, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("보기 링크가 없다", r.stderr)


class FakeQuickTunnel(Base):
    def test_supervisor_narrows_hosts_to_tunnel_name(self):
        """Through the whole supervisor loop (fake cloudflared) -- once the address is in state only that name passes, any other trycloudflare gets 421,
        and a link opened with that name gets __Host- and Secure cookies. After stop the tunnel child ends and public_url is cleared."""
        fakebin = self.t / "fakebin"
        fakebin.mkdir()
        pidfile = self.t / "cf.pid"
        cf = fakebin / "cloudflared"
        cf.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            echo $$ > {str(pidfile)!r}
            echo "INF |  https://fake-name-1.trycloudflare.com  |" >&2
            exec sleep 300
            """))
        cf.chmod(0o755)
        env = {**self.env, "PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"}
        out = json.loads(run(["start", "--tunnel", "cloudflared", "--json"], env).stdout)
        self.started.append(out["id"])
        self.assertTrue(out["links"]["edit"].startswith("https://fake-name-1.trycloudflare.com/?k="), out)
        port = int(out["links"]["local"].split(":")[2].split("/")[0])
        key = out["links"]["edit"].split("?k=")[1].split("#")[0]
        h = lambda host: {"Host": host, "X-Tb-Key": key, "X-Tb-Name": "a"}
        t0 = time.time()
        while get(port, "/api/whiteboard/boards", h("other-name.trycloudflare.com")) != 421:
            self.assertLess(time.time() - t0, 2.0, "주소가 나오면 와일드카드가 그 이름 하나로 좁혀진다")
            time.sleep(0.1)
        self.assertEqual(get(port, "/api/whiteboard/boards", h("fake-name-1.trycloudflare.com")), 200)
        self.assertEqual(get(port, "/api/whiteboard/boards", h(f"127.0.0.1:{port}")), 200)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", f"/?k={key}", headers={"Host": "fake-name-1.trycloudflare.com"})
        r = c.getresponse(); r.read(); c.close()
        self.assertTrue(r.getheader("Set-Cookie").startswith("__Host-tb_"), r.getheader("Set-Cookie"))
        self.assertIn("; Secure;", r.getheader("Set-Cookie"))
        cfpid = int(pidfile.read_text())
        run(["stop", out["id"]], env)
        self.started.remove(out["id"])
        st = json.loads((self.t / "home" / "sites" / out["id"] / "state.json").read_text())
        self.assertEqual((st["status"], st["public_url"]), ("stopped", None))
        for _ in range(30):
            try:
                os.kill(cfpid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("터널 자식이 남았다")


class R1Supervisor(Base):
    """SIGUSR1 means 'reload' (no crash), the supervisor survives a wrong hostname, and an exception in the main loop still ends the tunnel
    and writes the last state. Change detection uses (inode, mtime, size)."""

    def fake_cf(self):
        fakebin = self.t / "fakebin"
        fakebin.mkdir(exist_ok=True)
        pidfile = self.t / "cf.pid"
        cf = fakebin / "cloudflared"
        cf.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            echo $$ > {str(pidfile)!r}
            echo "INF |  https://sig-test-1.trycloudflare.com  |" >&2
            exec sleep 300
            """))
        cf.chmod(0o755)
        return {**self.env, "PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"}, pidfile

    @staticmethod
    def gone(pid, secs=3.0):
        t0 = time.time()
        while time.time() - t0 < secs:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.1)
        return False

    def test_sigusr1_reloads_and_bad_hostname_does_not_kill(self):
        env, pidfile = self.fake_cf()
        out = json.loads(run(["start", "--tunnel", "cloudflared", "--json"], env).stdout)
        sid = out["id"]
        self.started.append(sid)
        sp = SITES = self.t / "home" / "sites"
        st = json.loads((sp / sid / "state.json").read_text())
        port, pid, key = st["port"], st["pid"], st["edit_key"]
        os.kill(pid, signal.SIGUSR1)
        time.sleep(1.5)
        self.assertEqual(get(port, "/healthz", {"Host": f"127.0.0.1:{port}"}), 200, "SIGUSR1 에 죽지 않는다")
        self.assertEqual(json.loads((sp / sid / "state.json").read_text())["status"], "running")
        # the supervisor survives a wrong public address or hostname written to state, and drops only that entry
        code = (f"import importlib.util; s=importlib.util.spec_from_file_location('sv',{str(HERE / 'server' / 'supervise.py')!r});"
                f"m=importlib.util.module_from_spec(s); s.loader.exec_module(m);"
                f"m.update_state({str(SITES)!r}, {sid!r}, lambda c: c.update(hostname='bad host!', allow_hosts=['also bad!']))")
        subprocess.run([sys.executable, "-c", code], check=True)
        time.sleep(1.5)
        self.assertEqual(get(port, "/api/whiteboard/boards", {"Host": "sig-test-1.trycloudflare.com", "X-Tb-Key": key, "X-Tb-Name": "a"}), 200)
        os.kill(pid, signal.SIGUSR1)
        run(["keys", sid, "--rotate", "edit"], env)   # rotation still works after SIGUSR1
        time.sleep(1.5)
        self.assertEqual(get(port, "/api/whiteboard/boards", {"Host": f"127.0.0.1:{port}", "X-Tb-Key": key, "X-Tb-Name": "a"}), 403)
        cfpid = int(pidfile.read_text())
        run(["stop", sid], env)
        self.started.remove(sid)
        self.assertTrue(self.gone(cfpid), "터널 자식이 남지 않는다")

    def test_loop_exception_still_cleans_up(self):
        """Even if the main loop raises, the tunnel child is ended and status=failed is written (the supervisor is run directly in a thread)."""
        import importlib.util as iu
        import threading
        sv = iu.spec_from_file_location("sv_r1", HERE / "server" / "supervise.py")
        sup = iu.module_from_spec(sv); sv.loader.exec_module(sup)
        ss = iu.spec_from_file_location("srv_r1", HERE / "server" / "server.py")
        server = iu.module_from_spec(ss); ss.loader.exec_module(server)
        sites = self.t / "home" / "sites"
        sid = "r1-boom"
        (sites / sid / "data").mkdir(parents=True)
        st = {"id": sid, "edit_key": "k" * 12, "tunnel": "cloudflared", "host": "127.0.0.1", "port": 0, "status": "starting"}
        sup.update_state(sites, sid, lambda _c: dict(st))
        pidfile = self.t / "child.pid"
        child = [sys.executable, "-c", f"import os,sys,time; open({str(pidfile)!r},'w').write(str(os.getpid()));"
                 "print('https://boom-1.trycloudflare.com', file=sys.stderr, flush=True); time.sleep(60)"]
        calls = {"n": 0}
        real = sup.state_mtime

        def flaky(*a):
            calls["n"] += 1
            if calls["n"] > 3:
                raise RuntimeError("boom")
            return real(*a)
        sup.state_mtime = flaky
        err = {}

        def go():
            try:
                sup.run(sid, st, lambda port: child, lambda line: "https://boom-1.trycloudflare.com" if "boom-1" in line else None,
                        sites=sites, dist=self.dist, server=server, stop=threading.Event())
            except RuntimeError as e:
                err["e"] = e
        th = threading.Thread(target=go)
        th.start()
        th.join(20)
        self.assertFalse(th.is_alive())
        self.assertIn("e", err, "예외는 위로 다시 던진다")
        final = sup.read_state(sites, sid)
        self.assertEqual((final["status"], final["pid"], final["public_url"]), ("failed", None, None))
        self.assertTrue(self.gone(int(pidfile.read_text())), "터널 자식을 끝냈다")

    def test_change_mark_sees_same_mtime_rewrite(self):
        """Even with the same mtime, a rewrite (new inode) counts as a change."""
        import importlib.util as iu
        sv = iu.spec_from_file_location("sv_r1b", HERE / "server" / "supervise.py")
        sup = iu.module_from_spec(sv); sv.loader.exec_module(sup)
        sites = self.t / "home" / "sites"
        (sites / "m").mkdir(parents=True)
        sup.update_state(sites, "m", lambda c: c.update(a=1))
        p = sites / "m" / "state.json"
        m1, ns = sup.state_mtime(sites, "m"), p.stat().st_mtime_ns
        sup.update_state(sites, "m", lambda c: c.update(a=2))
        os.utime(p, ns=(ns, ns))   # simulates a filesystem with 1-second resolution -- the mtime is the same
        self.assertNotEqual(sup.state_mtime(sites, "m"), m1)


class StartJson(Base):
    def test_stdout_is_one_json_even_when_building(self):
        """Even when the web files are missing and it builds with npm, stdout is exactly one JSON document (npm output goes to stderr)."""
        root = copy_tree(self.t / "repo")
        web = root / "web"
        web.mkdir()
        fakebin = self.t / "fakebin"
        fakebin.mkdir()
        npm = fakebin / "npm"
        npm.write_text(textwrap.dedent("""\
            #!/bin/sh
            echo "npm noise on stdout: $*"
            echo "npm noise on stderr" >&2
            if [ "$1" = "run" ] && [ "$2" = "build" ]; then
              mkdir -p dist && echo '<!doctype html><title>built</title>' > dist/index.html
            fi
            """))
        npm.chmod(0o755)
        empty = self.t / "emptydist"
        empty.mkdir()   # an empty folder -- takes the download / build path
        env = {**self.env, "CERA_DIST": str(empty), "PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}",
               "https_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": ""}
        p = run(["start", "--tunnel", "none", "--json"], env, root=root)
        out = json.loads(p.stdout)   # must be a single JSON document
        self.started.append(out["id"])
        self.assertEqual(out["status"], "running")
        self.assertNotIn("npm noise", p.stdout)
        self.assertIn("npm noise on stdout", p.stderr, "npm 출력은 stderr 로")
        self.assertTrue((empty / "index.html").is_file(), "빈 dist 폴더도 채운다")
        run(["stop", out["id"]], env, root=root)
        self.started.remove(out["id"])


class Extensions(Base):
    def test_no_extensions_then_fake_one(self):
        """The CLI works even without an EXT_MODULES file. With a fake server/agents.py present, register is called. A broken extension only warns."""
        root = copy_tree(self.t / "repo")
        self.assertIn("start", run(["--help"], self.env, root=root).stdout)
        self.assertIn("사이트가 없다", run(["list"], self.env, root=root).stdout)
        (root / "server" / "agents.py").write_text(textwrap.dedent("""\
            from __future__ import annotations
            def register(sub, ctx):
                s = sub.add_parser("hello", help="가짜 확장")
                def go(a):
                    print("hello", ctx.SITES.name, ctx.version(), callable(ctx.update_state), callable(ctx.load_server),
                          callable(ctx.links), callable(ctx.alive), callable(ctx.ensure_dist), callable(ctx.read_state))
                s.set_defaults(f=go)
            """))
        self.assertEqual(run(["hello"], self.env, root=root).stdout.strip(), "hello sites 0.0.0 True True True True True True")
        (root / "server" / "importers.py").write_text("raise RuntimeError('broken')\n")
        p = run(["list"], self.env, root=root)
        self.assertIn("확장 importers", p.stderr)
        self.assertIn("hello", run(["--help"], self.env, root=root).stdout, "깨진 확장 하나가 다른 확장을 막지 않는다")


@unittest.skipUnless(PY39.is_file(), "/usr/bin/python3 없음")
class Python39(Base):
    def test_cli_and_modules_import_on_system_python(self):
        """The CLI, server.py, and supervise.py run on the system python3 (3.9.6 on this Mac)."""
        ver = subprocess.run([str(PY39), "-c", "import sys;print(sys.version_info[:2])"], capture_output=True, text=True).stdout
        self.assertIn("help", run(["--help"], self.env, py=str(PY39)).stdout)
        code = ("import importlib.util,sys\n"
                "for n in ('server','supervise','security'):\n"
                f"    s=importlib.util.spec_from_file_location('tb_'+n,{str(HERE / 'server')!r}+'/'+n+'.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                "print('ok')")
        p = subprocess.run([str(PY39), "-c", code], capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), "ok", p.stderr)
        out = json.loads(run(["start", "--tunnel", "none", "--json"], self.env, py=str(PY39)).stdout)   # the supervisor process is 3.9 too
        self.started.append(out["id"])
        port = int(out["links"]["edit"].split(":")[2].split("/")[0])
        key = out["links"]["edit"].split("?k=")[1].split("#")[0]
        self.assertEqual(get(port, "/api/whiteboard/boards", {"Host": f"127.0.0.1:{port}", "X-Tb-Key": key, "X-Tb-Name": "a"}), 200)
        run(["keys", out["id"], "--rotate", "edit"], self.env, py=str(PY39))
        self.assertEqual(json.loads(run(["links", out["id"], "--json"], self.env, py=str(PY39)).stdout)["id"], out["id"])
        self.assertTrue(ver)


class Language(Base):
    """`cera lang` and the English / Korean switch of CLI text (CERA_LANG > saved > locale > English)."""

    def env_for(self, lang=None, **extra):
        e = {k: v for k, v in self.env.items() if k not in ("CERA_LANG", "TEMPBOARD_LANG", "LC_ALL", "LC_MESSAGES", "LANG")}
        if lang:
            e["CERA_LANG"] = lang
        e.update(extra)
        return e

    def test_same_command_prints_both_languages(self):
        ko = run(["list"], self.env_for("ko")).stdout
        en = run(["list"], self.env_for("en")).stdout
        self.assertIn("사이트가 없다", ko)
        self.assertIn("No sites", en)
        self.assertNotRegex(en, "[\uac00-\ud7a3]")
        self.assertIn("No site zzz", run(["links", "zzz"], self.env_for("en"), check=False).stderr)
        self.assertIn("사이트 zzz 가 없다", run(["links", "zzz"], self.env_for("ko"), check=False).stderr)

    def test_lang_command_save_auto_and_env_override(self):
        e = self.env_for()
        self.assertIn("source: default", run(["lang"], e).stdout)
        self.assertIn("ko", run(["lang", "ko"], e).stdout)
        cfg = json.loads((self.t / "home" / "config.json").read_text())
        self.assertEqual(cfg["lang"], "ko")
        out = run(["lang"], e).stdout
        self.assertIn("언어: ko", out)
        self.assertIn("saved", out)
        self.assertIn("사이트가 없다", run(["list"], e).stdout)          # the saved choice is used
        self.assertIn("No sites", run(["list"], self.env_for("en")).stdout)   # the environment overrides it
        self.assertIn("source: env", run(["lang"], self.env_for("en")).stdout)
        run(["lang", "en"], e)
        self.assertIn("No sites", run(["list"], e).stdout)
        run(["lang", "auto"], e)
        self.assertNotIn("lang", json.loads((self.t / "home" / "config.json").read_text()))
        self.assertIn("source: default", run(["lang"], e).stdout)
        self.assertNotEqual(run(["lang", "fr"], e, check=False).returncode, 0)

    def test_locale_picks_korean_when_nothing_else_is_set(self):
        self.assertIn("사이트가 없다", run(["list"], self.env_for(None, LANG="ko_KR.UTF-8")).stdout)
        self.assertIn("No sites", run(["list"], self.env_for(None, LANG="C.UTF-8")).stdout)
        self.assertIn("출처: locale", run(["lang"], self.env_for(None, LANG="ko_KR.UTF-8")).stdout)


if __name__ == "__main__":
    unittest.main()
