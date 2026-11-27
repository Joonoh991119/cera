# SPDX-License-Identifier: MIT
"""Personal-board web tests: `cera site init/run/install/uninstall/status/doctor/links`.

No real Cloudflare login, tunnel, or DNS is used. A **fake cloudflared** (a script that records its arguments and, for create / token,
writes a credentials file) is placed first on PATH, but `ingress ...` is passed through to this Mac's **real cloudflared** so the generated config is genuinely validated.
HOME and CERA_HOME are always temporary folders (~/.cloudflared, ~/Library/LaunchAgents, and ~/.cera are never touched).
Fake launchctl and systemctl on PATH only record calls if invoked by accident, and the tests check that they were never called."""
from __future__ import annotations

import http.client
import json
import os
import plistlib
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["CERA_LANG"] = "ko"   # existing assertions are Korean; set before anything spawns the CLI or imports server code

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "cera"
REAL_CF = shutil.which("cloudflared")
PY39 = Path("/usr/bin/python3")
HOST = "board.example.com"
UUID = "6ff42ae2-765d-4adf-8112-31c55c1551ef"

FAKE_CF = r'''#!{py}
import json, os, subprocess, sys, time
argv = sys.argv[1:]
with open(os.environ["FAKE_CF_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")
def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None
def creds(path):
    with open(path, "w") as fh:
        json.dump({{"AccountTag": "acc", "TunnelSecret": "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0MTI=", "TunnelID": "{uuid}"}}, fh)
if argv[:1] == ["--version"]:
    print("cloudflared version 2026.2.0 (fake)"); sys.exit(0)
if "ingress" in argv:
    real = os.environ.get("REAL_CF")
    if real:
        os.execv(real, [real] + argv)
    print("OK" if "validate" in argv else "Matched rule #0"); sys.exit(0)
if "list" in argv:
    print(os.environ.get("FAKE_CF_LIST", "[]")); sys.exit(0)
if "create" in argv:
    creds(opt("--credentials-file")); print("Created tunnel " + argv[-1] + " with id {uuid}"); sys.exit(0)
if "token" in argv:
    creds(opt("--cred-file")); sys.exit(0)
if "route" in argv:
    sys.exit(int(os.environ.get("FAKE_CF_ROUTE_RC", "0")))
if "info" in argv:
    if os.environ.get("FAKE_CF_INFO"):
        print(os.environ["FAKE_CF_INFO"]); sys.exit(0)
    print(json.dumps({{"id": "{uuid}", "conns": [{{"conns": [{{"id": "c"}}]}}]}})); sys.exit(0)
if "run" in argv:
    runs = os.environ["FAKE_CF_RUNS"]
    with open(runs, "a") as fh:
        fh.write(str(time.time()) + "\n")
    n = sum(1 for _ in open(runs))
    sys.stderr.write("INF Starting tunnel run #%d\n" % n); sys.stderr.flush()
    if n <= int(os.environ.get("FAKE_CF_DIE", "0")):
        time.sleep(0.2); sys.exit(1)
    while True:
        time.sleep(1)
sys.exit(3)
'''

RECORDER = r'''#!{py}
import json, os, sys
with open(os.environ["FAKE_SVC_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\n")
if os.path.basename(sys.argv[0]) == "caffeinate":
    import time
    while True:
        time.sleep(1)
sys.exit(1)
'''


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(port, path, host, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers={"Host": host, **(headers or {})})
    r = c.getresponse()
    r.read()
    c.close()
    return r.status, dict((k.lower(), v) for k, v in r.getheaders())


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "user"
        self.home.mkdir()
        self.tbhome = self.t / "tbhome"
        self.dist = self.t / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<!doctype html><title>tb</title>")
        self.bin = self.t / "fakebin"
        self.bin.mkdir()
        cf = self.bin / "cloudflared"
        cf.write_text(FAKE_CF.format(py=sys.executable, uuid=UUID))
        cf.chmod(0o755)
        for n in ("launchctl", "systemctl", "caffeinate", "loginctl"):
            p = self.bin / n
            p.write_text(RECORDER.format(py=sys.executable))
            p.chmod(0o755)
        self.cflog, self.runs, self.svclog = self.t / "cf.log", self.t / "runs.txt", self.t / "svc.log"
        env = {k: v for k, v in os.environ.items() if k not in ("TUNNEL_ORIGIN_CERT", "CERA_DIST", "XDG_CONFIG_HOME",
                                                                "CERA_TEST_INSECURE_JWKS")}
        self.env = {**env, "HOME": str(self.home), "CERA_HOME": str(self.tbhome), "CERA_DIST": str(self.dist),
                    "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin{os.pathsep}/usr/sbin{os.pathsep}/sbin",
                    "FAKE_CF_LOG": str(self.cflog), "FAKE_CF_RUNS": str(self.runs), "FAKE_SVC_LOG": str(self.svclog)}
        if REAL_CF:
            self.env["REAL_CF"] = REAL_CF
        self.procs = []
        self.port = free_port()

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(8)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, 9)
                p.wait()
        self.tmp.cleanup()

    def tb(self, *args, env=None, check=True, py=None, timeout=60):
        # start in a new session so that on timeout the whole group (supervisor + fake tunnel child) is killed -- no orphans left behind
        proc = subprocess.Popen([py or sys.executable, str(CLI), *args], env=env or self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
            proc.communicate()
            raise
        p = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
        if check and p.returncode != 0:
            raise AssertionError(f"cera {args} → {p.returncode}\nstdout:{p.stdout}\nstderr:{p.stderr}")
        return p

    def cert(self):
        d = self.home / ".cloudflared"
        d.mkdir(exist_ok=True)
        (d / "cert.pem").write_text("-----BEGIN ARGO TUNNEL TOKEN-----\nZmFrZQ==\n-----END ARGO TUNNEL TOKEN-----\n")

    def calls(self):
        return [json.loads(x) for x in self.cflog.read_text().splitlines()] if self.cflog.exists() else []

    def svc_calls(self):
        return [json.loads(x) for x in self.svclog.read_text().splitlines()] if self.svclog.exists() else []

    def state(self, sid="personal"):
        return json.loads((self.tbhome / "sites" / sid / "state.json").read_text())

    def init(self, *extra):
        self.cert()
        return self.tb("site", "init", "--hostname", HOST, "--port", str(self.port), *extra)

    def run_site(self, sid="personal"):
        with open(self.t / f"run-{sid}.log", "ab") as log:
            p = subprocess.Popen([sys.executable, str(CLI), "site", "run", "--id", sid], env=self.env, stdout=log, stderr=log,
                                 start_new_session=True)
        self.procs.append(p)
        t0 = time.time()
        while time.time() - t0 < 15:
            try:
                if get(self.port, "/healthz", f"127.0.0.1:{self.port}")[0] == 200:
                    return p
            except OSError:
                pass
            time.sleep(0.2)
        raise AssertionError("site run 이 뜨지 않았다:\n" + (self.t / f"run-{sid}.log").read_text())


class Init(Base):
    def test_no_cert_exit2_creates_nothing(self):
        """With no cert.pem in HOME, init exits 2, the guidance mentions `cloudflared tunnel login`, and nothing is created under sites/."""
        p = self.tb("site", "init", "--hostname", HOST, check=False)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("cloudflared tunnel login", p.stderr)
        self.assertIn("네임서버", p.stderr)
        self.assertFalse((self.tbhome / "sites").exists() and any((self.tbhome / "sites").iterdir()))
        self.assertEqual(self.calls(), [], "cloudflared 를 부르지 않았다")
        env = {**self.env, "PATH": f"/usr/bin{os.pathsep}/bin"}   # exit 2 also when cloudflared itself is missing
        p = self.tb("site", "init", "--hostname", HOST, env=env, check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("brew install cloudflared", p.stderr)

    def test_bad_args(self):
        self.cert()
        for args in (["--hostname", "example.com"], ["--hostname", "x.trycloudflare.com"], ["--hostname", "board.example.com", "--port", "80"],
                     ["--hostname", HOST, "--access-team", "myteam"], ["--hostname", HOST, "--id", "Bad_ID"]):
            p = self.tb("site", "init", *args, check=False)
            self.assertNotEqual(p.returncode, 0, args)
        self.assertFalse((self.tbhome / "sites").exists() and any((self.tbhome / "sites").iterdir()))

    def test_local_managed_order_config_and_real_validate(self):
        """create runs before route dns, and the generated config passes the real cloudflared's ingress validate and rule #0 (including the Access block)."""
        p = self.init()
        names = [c for c in self.calls() if "--version" not in c]
        sub = [next((w for w in ("list", "create", "token", "route", "ingress") if w in c), "?") for c in names]
        self.assertEqual(sub[:3], ["list", "create", "route"], names)
        create = names[1]
        self.assertEqual(create[-1], "cera-board-example-com", "기본 이름에 주소가 들어간다")
        self.assertEqual(names[2][-3:], ["dns", "cera-board-example-com", HOST])
        self.assertIn("--origincert", create)
        self.assertNotIn("--overwrite-dns", names[2], "사용자가 줄 때만")
        d = self.tbhome / "sites" / "personal"
        cfg = d / "cloudflared.yml"
        self.assertEqual(stat.S_IMODE((d / "tunnel.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        st = self.state()
        self.assertEqual((st["mode"], st["tunnel"], st["hostname"], st["public_url"], st["expires_at"], st["tunnel_id"]),
                         ("personal", "named", HOST, f"https://{HOST}", 0, UUID))
        self.assertTrue(st["edit_key"])
        self.assertIsNone(st["access"])
        self.assertIn("site run", p.stdout)
        self.real_validate(cfg)
        self.assertNotIn("access", cfg.read_text())
        self.assertFalse((self.home / ".cloudflared" / "config.yml").exists(), "전역 config 는 건드리지 않는다")
        # init again (+Access) -> credentials are reused (create is not called again), the key is kept, the Access block is present
        key = st["edit_key"]
        n_create = sum(1 for c in self.calls() if "create" in c)
        self.init("--access-team", "MyTeam", "--access-aud", "b" * 64, "--overwrite-dns")
        self.assertEqual(sum(1 for c in self.calls() if "create" in c), n_create)
        self.assertIn("--overwrite-dns", [c for c in self.calls() if "route" in c][-1])
        st = self.state()
        self.assertEqual(st["edit_key"], key)
        self.assertEqual(st["access"], {"team": "myteam", "aud": "b" * 64})
        text = cfg.read_text()
        for s in ("originRequest:", "required: true", 'teamName: "myteam"', "audTag:"):
            self.assertIn(s, text)
        self.real_validate(cfg)
        self.init("--no-access")
        self.assertIsNone(self.state()["access"])

    def real_validate(self, cfg):
        if not REAL_CF:
            self.skipTest("진짜 cloudflared 없음")
        env = {**self.env, "PATH": f"/usr/bin{os.pathsep}/bin"}
        p = subprocess.run([REAL_CF, "tunnel", "--config", str(cfg), "ingress", "validate"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("OK", p.stdout + p.stderr)
        p = subprocess.run([REAL_CF, "tunnel", "--config", str(cfg), "ingress", "rule", f"https://{HOST}/api/x"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("rule #0", p.stdout + p.stderr)
        p = subprocess.run([REAL_CF, "tunnel", "--config", str(cfg), "ingress", "rule", "https://other.example.com/"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertNotIn("rule #0", p.stdout + p.stderr)

    def test_existing_tunnel_refused_unless_reuse(self):
        """If a tunnel with the same name already exists in the account, it is not taken over and init exits 2 (with the reason and what to do); credentials are fetched only with --reuse-tunnel."""
        self.cert()
        env = {**self.env, "FAKE_CF_LIST": json.dumps([{"id": UUID, "name": "cera-board-example-com", "connections": [{"id": "x"}]}])}
        p = self.tb("site", "init", "--hostname", HOST, "--port", str(self.port), env=env, check=False)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("--reuse-tunnel", p.stderr)
        self.assertIn("나눠", p.stderr)
        self.assertFalse([c for c in self.calls() if "token" in c or "create" in c or "route" in c])
        self.assertFalse((self.tbhome / "sites" / "personal").exists(), "이번에 만든 빈 폴더는 치운다")
        env["FAKE_CF_ROUTE_RC"] = "1"
        p = self.tb("site", "init", "--hostname", HOST, "--port", str(self.port), "--reuse-tunnel", env=env, check=False)
        self.assertIn("연결 1개", p.stderr, "지금 붙어 있는 연결을 알린다")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--overwrite-dns", p.stderr)
        self.assertTrue(any("token" in c for c in self.calls()))
        self.assertFalse(any("create" in c for c in self.calls()))
        self.assertTrue((self.tbhome / "sites" / "personal" / "tunnel.json").is_file(), "받은 자격은 남긴다")

    def test_token_file_mode(self):
        tf = self.t / "tunnel.token"
        tf.write_text("eyJhIjoiYiJ9")
        tf.chmod(0o644)
        p = self.tb("site", "init", "--hostname", HOST, "--token-file", str(tf), check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("chmod 600", p.stderr)
        tf.chmod(0o600)
        p = self.tb("site", "init", "--hostname", HOST, "--port", str(self.port), "--token-file", str(tf))
        self.assertIn("127.0.0.1:%d" % self.port, p.stdout)
        self.assertFalse([c for c in self.calls() if "create" in c or "route" in c], "원격 관리는 create · route 없음")
        st = self.state()
        self.assertEqual(st["token_file"], str(tf))
        self.assertFalse((self.tbhome / "sites" / "personal" / "cloudflared.yml").exists())
        self.assertNotIn("eyJhIjoiYiJ9", json.dumps(st), "토큰 내용은 state 에 없다")


class Install(Base):
    def test_dry_run_plist_lint_and_no_secrets(self):
        """The --dry-run plist passes plutil -lint, has an absolute ProgramArguments[0], contains no secrets, and writes nothing."""
        self.init("--view-link")
        st = self.state()
        p = self.tb("site", "install", "--dry-run", "--platform", "macos")
        body = p.stdout.split("\n", 1)[1].rsplit("# 올리기", 1)[0]
        pl = plistlib.loads(body.encode())
        args = pl["ProgramArguments"]
        self.assertTrue(os.path.isabs(args[0]) and Path(args[0]).exists(), args)
        # The plugin root changes with each version, so point at the fixed entry point $CERA_HOME/bin/cera (written by site install)
        self.assertEqual(args[1:], [str(self.tbhome / "bin" / "cera"), "site", "run", "--id", "personal"])
        self.assertEqual(pl["EnvironmentVariables"]["CERA_SERVICE"], "1")
        self.assertEqual(pl["KeepAlive"], {"SuccessfulExit": False})
        self.assertTrue(pl["RunAtLoad"])
        self.assertEqual(pl["StandardOutPath"], str(self.tbhome / "sites" / "personal" / "log.txt"))
        self.assertIn(str(self.bin), pl["EnvironmentVariables"]["PATH"].split(":"), "cloudflared 폴더가 PATH 에")
        self.assertEqual(pl["EnvironmentVariables"]["CERA_HOME"], str(self.tbhome))
        for secret in (st["edit_key"], st["view_key"]):
            self.assertNotIn(secret, body)
        # (/usr/bin/python3 writes pycache into ~/Library/Caches/com.apple.python -- only LaunchAgents is checked)
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists(), "--dry-run 은 쓰지 않는다")
        if shutil.which("plutil"):
            f = self.t / "x.plist"
            f.write_text(body)
            lint = subprocess.run(["plutil", "-lint", str(f)], capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertEqual(self.svc_calls(), [])

    def test_systemd_unit(self):
        self.init()
        p = self.tb("site", "install", "--dry-run", "--platform", "linux")
        self.assertIn(f"# {self.home}/.config/systemd/user/cera-personal.service", p.stdout)
        unit = p.stdout
        exec_line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
        first = exec_line[len("ExecStart="):].split('" "')[0].strip('"')
        self.assertTrue(os.path.isabs(first), exec_line)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn('"site" "run" "--id" "personal"', exec_line)
        self.assertIn("WantedBy=default.target", unit)
        self.assertNotIn(self.state()["edit_key"], unit)
        self.assertIn("systemctl --user enable --now cera-personal.service", unit)

    def test_install_writes_file_without_loading_then_uninstall(self):
        self.init()
        p = self.tb("site", "install", "--platform", "macos")
        path = self.home / "Library" / "LaunchAgents" / "com.cera.personal.plist"
        self.assertTrue(path.is_file())
        self.assertIn("--load", p.stdout)
        launcher = self.tbhome / "bin" / "cera"   # the fixed entry point is written too
        self.assertTrue(launcher.is_file() and os.access(launcher, os.X_OK))
        self.assertEqual(plistlib.loads(path.read_bytes())["ProgramArguments"][1], str(launcher))
        if shutil.which("plutil"):
            self.assertEqual(subprocess.run(["plutil", "-lint", str(path)], capture_output=True).returncode, 0)
        self.assertEqual(self.svc_calls(), [], "--load 없이는 launchctl 을 부르지 않는다")
        self.assertEqual(self.state()["service"]["path"], str(path))
        p = self.tb("site", "uninstall", "--platform", "macos")
        self.assertFalse(path.exists())
        self.assertIn("bootout", p.stdout)
        self.assertEqual(self.svc_calls(), [])
        self.assertTrue((self.tbhome / "sites" / "personal" / "state.json").is_file(), "사이트는 남는다")
        # files that belong to someone else are not deleted
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps({"Label": "com.other.thing"}))
        self.assertNotEqual(self.tb("site", "uninstall", "--platform", "macos", check=False).returncode, 0)
        self.assertTrue(path.exists())


class Run(Base):
    def test_backoff_healthz_list_links_keys_stop(self):
        """A fake tunnel that dies 3 times is reopened at growing intervals, the server keeps responding meanwhile, list / links / keys work, and stop takes 5 seconds."""
        self.init()
        env_die = dict(self.env, FAKE_CF_DIE="3")
        self.env = env_die
        p = self.run_site()
        t0 = time.time()
        while time.time() - t0 < 15 and (not self.runs.exists() or len(self.runs.read_text().split()) < 4):
            st, hs = get(self.port, "/healthz", HOST)
            self.assertEqual(st, 200, "터널이 죽고 다시 여는 동안에도 서버는 응답")
            time.sleep(0.3)
        starts = [float(x) for x in self.runs.read_text().split()]
        self.assertGreaterEqual(len(starts), 4, starts)
        gaps = [b - a for a, b in zip(starts, starts[1:])][:3]
        self.assertTrue(gaps[0] < gaps[1] < gaps[2], f"백오프가 늘어나야 한다: {gaps}")
        # a tunnel that keeps dying (3 times) is flagged by doctor as WARN
        dr = self.tb("site", "doctor", "--offline", check=False, timeout=90)
        self.assertRegex(dr.stdout, r"WARN\s+터널 다시 열기 — 최근 15분에 3번")
        run_argv = [c for c in self.calls() if "run" in c][-1]
        self.assertEqual(run_argv[-4:], ["--config", str(self.tbhome / "sites" / "personal" / "cloudflared.yml"), "run", UUID])
        self.assertIn("--no-autoupdate", run_argv)
        st, hs = get(self.port, "/healthz", HOST)
        self.assertEqual(hs.get("strict-transport-security"), "max-age=15552000")
        st, hs = get(self.port, "/healthz", f"127.0.0.1:{self.port}")
        self.assertNotIn("strict-transport-security", hs)
        self.assertEqual(get(self.port, "/healthz", "evil.example")[0], 421)
        # list · links
        self.assertIn("personal", self.tb("list").stdout)
        ln = json.loads(self.tb("links", "personal", "--json").stdout)
        self.assertTrue(ln["links"]["edit"].startswith(f"https://{HOST}/?k="), ln)
        old = ln["links"]["edit"].split("?k=")[1]
        self.assertEqual(get(self.port, "/api/whiteboard/boards", HOST, {"X-Tb-Key": old, "X-Tb-Name": "a"})[0], 200)
        # keys --rotate all is accepted as-is
        new = json.loads(self.tb("keys", "personal", "--rotate", "all", "--json").stdout)["links"]["edit"].split("?k=")[1]
        t0 = time.time()
        while time.time() - t0 < 2.5 and get(self.port, "/api/whiteboard/boards", HOST, {"X-Tb-Key": old, "X-Tb-Name": "a"})[0] != 403:
            time.sleep(0.1)
        self.assertEqual(get(self.port, "/api/whiteboard/boards", HOST, {"X-Tb-Key": old, "X-Tb-Name": "a"})[0], 403)
        self.assertEqual(get(self.port, "/api/whiteboard/boards", HOST, {"X-Tb-Key": new, "X-Tb-Name": "a"})[0], 200)
        # status
        stj = json.loads(self.tb("site", "status", "--json").stdout)
        self.assertEqual((stj["status"], stj["url"], stj["pid"]), ("running", f"https://{HOST}", p.pid))
        # a second run is refused
        self.assertNotEqual(self.tb("site", "run", check=False, timeout=20).returncode, 0)
        # stop -> the supervisor ends within 5 seconds, exit 0, state stopped, no tunnel child left.
        # `cera stop` treats a child that has not been reaped (a zombie) as alive and waits the full 5 seconds, so instead of that command
        # we measure when the supervisor (the Popen child) ended -- stop is started in the background and reaped here.
        t0 = time.time()
        stopper = subprocess.Popen([sys.executable, str(CLI), "stop", "personal"], env=self.env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.wait(5)
        took = time.time() - t0
        stopper.wait(15)
        self.assertLess(took, 5.0, f"감독이 끝나는 데 {took:.2f}초")
        self.assertEqual(p.returncode, 0, "깨끗한 끝 = 0(launchd 가 다시 띄우지 않는다)")
        st = self.state()
        self.assertEqual((st["status"], st["pid"], st["public_url"]), ("stopped", None, f"https://{HOST}"))
        self.assertEqual(self.svc_calls(), [])

    def test_access_links_keep_awake_and_missing_cloudflared(self):
        """links shows the public address without a key plus Access guidance. With --keep-awake, caffeinate -s -w <pid> is started as a child."""
        self.init("--access-team", "myteam", "--access-aud", "c" * 64, "--keep-awake")
        st = self.state()
        st["access"]["jwks_url"] = "http://127.0.0.1:9/certs"   # keeps the test from reaching the outside (cloudflareaccess.com)
        self.env["CERA_TEST_INSECURE_JWKS"] = "1"           # an http JWKS is allowed only when this environment variable is set
        (self.tbhome / "sites" / "personal" / "state.json").write_text(json.dumps(st))
        p = self.run_site()
        txt = self.tb("links", "personal").stdout
        self.assertIn(f"https://{HOST}/ ", txt)
        self.assertIn("Access", txt)
        self.assertNotIn(f"https://{HOST}/?k=", txt)
        j = json.loads(self.tb("links", "personal", "--json").stdout)
        self.assertEqual(j["links"]["edit"], f"https://{HOST}/")
        self.assertIn("?k=", j["links"]["local"])
        self.assertIn("Access 모드", self.tb("keys", "personal").stdout)
        self.assertEqual(get(self.port, "/healthz", HOST)[0], 401, "JWT 없는 공개 Host → 401")
        if sys.platform == "darwin":
            t0 = time.time()
            while time.time() - t0 < 5 and not any(c[0] == "caffeinate" for c in self.svc_calls()):
                time.sleep(0.1)
            caf = [c for c in self.svc_calls() if c[0] == "caffeinate"]
            self.assertEqual(caf[0][1:], ["-s", "-w", str(p.pid)])
        self.tb("stop", "personal")
        p.wait(5)
        if sys.platform == "darwin":
            ps = subprocess.run(["pgrep", "-f", f"{self.bin}/caffeinate"], capture_output=True, text=True).stdout.split()
            self.assertEqual(ps, [], "caffeinate 자식도 끝난다")

    def test_insecure_jwks_and_bad_members(self):
        """An http jwks_url is rejected by run without the test environment variable. A members.json that has only agents gets a doctor FAIL and a warning in the run log."""
        self.init("--access-team", "myteam", "--access-aud", "c" * 64)
        st = self.state()
        st["access"]["jwks_url"] = "http://127.0.0.1:9/certs"
        (self.tbhome / "sites" / "personal" / "state.json").write_text(json.dumps(st))
        p = self.tb("site", "run", check=False, timeout=20)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("https", p.stderr)
        self.env["CERA_TEST_INSECURE_JWKS"] = "1"
        (self.tbhome / "sites" / "personal" / "members.json").write_text(json.dumps({"agents": {"cid.access": {"name": "a"}}}))
        d = self.tb("site", "doctor", "--offline", check=False)
        self.assertEqual(d.returncode, 1)
        self.assertRegex(d.stdout, r"FAIL\s+members\.json")
        self.assertIn('"*"', d.stdout)
        self.run_site()
        self.assertIn("members.json", (self.t / "run-personal.log").read_text())
        self.tb("stop", "personal")

    def test_trust_header_refused(self):
        self.init("--access-team", "myteam", "--access-aud", "c" * 64)
        st = self.state()
        st["trust_header"] = "Cf-Access-Authenticated-User-Email"
        (self.tbhome / "sites" / "personal" / "state.json").write_text(json.dumps(st))
        p = self.tb("site", "run", check=False, timeout=20)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("trust", p.stderr)


class English(Base):
    def test_guide_and_offline_doctor_in_english(self):
        import re
        env = {**self.env, "CERA_LANG": "en"}
        for args in (("site", "guide"), ("site", "doctor", "--offline"), ("site", "init", "--help"), ("site", "status")):
            p = self.tb(*args, env=env, check=False)
            out = p.stdout + p.stderr
            self.assertIsNone(re.search("[가-힣]", out), (args, out))
        g = self.tb("site", "guide", env=env).stdout
        self.assertIn("Add a domain", g)
        self.assertIn("One-time PIN", g)
        d = self.tb("site", "doctor", "--offline", env=env, check=False)
        self.assertRegex(d.stdout, r"FAIL\s+personal site")


class Doctor(Base):
    def doctor(self, *extra, env=None):
        return self.tb("site", "doctor", *extra, env=env, check=False, timeout=90)

    def test_fail_cases(self):
        """cloudflared missing / cert missing / ingress failure / local /healthz failure each give FAIL, exit 1."""
        p = self.doctor("--offline", env={**self.env, "PATH": f"/usr/bin{os.pathsep}/bin"})
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout, r"FAIL\s+cloudflared")
        p = self.doctor("--offline")
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout, r"FAIL\s+Cloudflare 로그인")
        self.assertIn("cloudflared tunnel login", p.stdout)
        self.init()
        p = self.doctor("--offline")
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout, r"FAIL\s+이 컴퓨터의 서버")
        self.assertRegex(p.stdout, r"OK\s+경로 설정")
        cfg = self.tbhome / "sites" / "personal" / "cloudflared.yml"
        cfg.write_text(cfg.read_text().replace("  - service: http_status:404\n", ""))   # the last catch-all rule is gone
        p = self.doctor("--offline")
        self.assertEqual(p.returncode, 1)
        if REAL_CF:
            self.assertRegex(p.stdout, r"FAIL\s+경로 설정")

    def test_all_good_exit0(self):
        self.init()
        self.run_site()
        p = self.doctor("--offline")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"OK\s+이 컴퓨터의 서버")
        self.assertRegex(p.stdout, r"OK\s+공개 Host 규칙")
        self.assertRegex(p.stdout, r"WARN\s+공개 주소 — 건너뜀")
        self.assertNotRegex(p.stdout, r"(?m)^FAIL")
        self.tb("stop", "personal")

    def test_public_check_levels(self):
        """Public-address check: WARN if unreachable, OK if Access answers 302, FAIL if Access answers 401 (no app), FAIL on 530."""
        sys.path.insert(0, str(HERE / "server"))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("tb_personal_t", HERE / "server" / "personal.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.pop(0)
        import http.server
        import io
        import threading
        mode = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(mode["code"])
                if mode.get("loc"):
                    self.send_header("Location", mode["loc"])
                self.send_header("Content-Length", "0")
                self.end_headers()
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            def level(access, **m):
                mode.clear()
                mode.update(m)
                rep = mod.Report(io.StringIO())
                mod.check_public(rep, url, access, timeout=3)
                return rep.rows[0][0]
            self.assertEqual(level(True, code=302, loc="https://myteam.cloudflareaccess.com/cdn-cgi/access/login/x"), "OK")
            self.assertEqual(level(True, code=401), "FAIL")
            self.assertEqual(level(False, code=200), "OK")
            self.assertEqual(level(False, code=530), "FAIL")
            rep = mod.Report(io.StringIO())
            mod.check_public(rep, f"http://127.0.0.1:{free_port()}", False, timeout=2)
            self.assertEqual(rep.rows[0][0], "WARN", "닿지 않으면 WARN")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_guide_order(self):
        g = self.tb("site", "guide").stdout
        order = ["네임서버", "cloudflared tunnel login", "site init --hostname", "site run", "site install --load",
                 "One-time PIN", "Self-hosted", "Application Audience (AUD) Tag", "--access-aud", "members.json", "Service Auth"]
        pos = [g.find(s) for s in order]
        self.assertTrue(all(p >= 0 for p in pos), dict(zip(order, pos)))
        self.assertEqual(pos, sorted(pos), "사람이 할 일의 순서")


class Other401:
    """Imitates another program -- answers 401 to anything on 127.0.0.1:<port> (as when another whiteboard on this Mac held port 8790)."""

    def __init__(self, port=0):
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = b'{"error":"login required"}'
                self.send_response(401)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class R2(Base):
    """Port conflicts, the first link and key-free logs, doctor hiding raw errors, and a tunnel that keeps dying."""

    def setUp(self):
        super().setUp()
        self.other = Other401()

    def tearDown(self):
        super().tearDown()
        self.other.close()

    def test_init_picks_free_port_when_default_is_taken(self):
        """Port conflict, part 1: if another program (401) holds the default port, init picks a free port and says so, and run and doctor are OK."""
        self.env["CERA_SITE_PORT"] = str(self.other.port)
        self.cert()
        p = self.tb("site", "init", "--hostname", HOST)
        st = self.state()
        self.assertNotEqual(st["port"], self.other.port)
        self.assertIn(f"포트 {self.other.port}: 다른 프로그램이 쓰는 중 → 이 사이트는 포트 {st['port']} 사용", p.stdout)
        cfg = (self.tbhome / "sites" / "personal" / "cloudflared.yml").read_text()
        self.assertIn(f"http://127.0.0.1:{st['port']}", cfg)
        self.port = st["port"]
        self.run_site()
        d = self.tb("site", "doctor", "--offline", check=False, timeout=90)
        self.assertEqual(d.returncode, 0, d.stdout)
        self.assertRegex(d.stdout, r"OK\s+이 컴퓨터의 서버")
        self.tb("stop", "personal")

    def test_run_conflict_prints_fix_that_works_and_doctor_agrees(self):
        """Port conflict, parts 2 and 3: for a site made with that port, run shows the port and the command to fix it, and running that command as-is makes run work.
        doctor prescribes the same command under the 'another program' FAIL (it does not prescribe run)."""
        self.cert()
        p = self.tb("site", "init", "--hostname", HOST, "--port", str(self.other.port))
        self.assertIn(f"포트 {self.other.port}: 다른 프로그램이 쓰는 중", p.stderr)
        self.assertEqual(self.state()["port"], self.other.port, "--port 를 주면 그대로 둔다(경고만)")
        r = self.tb("site", "run", check=False, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"포트 {self.other.port}", r.stderr)
        fix = next(l.split(":", 1)[1].strip() for l in r.stderr.splitlines() if "고치기" in l)
        self.assertRegex(fix, r"^cera site port \d+$")
        self.assertIn("다른 프로그램이 쓰는 중", self.tb("site", "status").stdout)
        d = self.tb("site", "doctor", "--offline", check=False, timeout=90)
        self.assertEqual(d.returncode, 1)
        self.assertRegex(d.stdout, rf"FAIL\s+이 컴퓨터의 서버 — 포트 {self.other.port}: 다른 프로그램이 쓰는 중\(응답 401\)")
        self.assertIn(fix, d.stdout, "doctor 처방 = run 이 보인 명령")
        self.assertNotIn("→ cera site run(시험)", d.stdout)
        n_tunnel_calls = len([c for c in self.calls() if "create" in c or "route" in c or "list" in c])
        out = self.tb(*fix.split()[1:])                              # run it as-is
        self.assertIn("→", out.stdout)
        self.assertEqual(len([c for c in self.calls() if "create" in c or "route" in c or "list" in c]), n_tunnel_calls,
                         "포트 바꾸기는 터널 · DNS 를 건드리지 않는다(F2 거부에 걸리지 않음)")
        new = int(fix.split()[-1])
        self.assertEqual(self.state()["port"], new)
        self.assertIn(f"http://127.0.0.1:{new}", (self.tbhome / "sites" / "personal" / "cloudflared.yml").read_text())
        self.port = new
        self.run_site()                                              # now it works
        self.assertNotEqual(self.tb("site", "port", check=False).returncode, 0, "도는 중에는 포트를 바꾸지 않는다")
        self.tb("stop", "personal")

    def test_first_link_once_no_keys_in_run_log_and_stop_wording(self):
        """init shows the keyed link (once, the first time) and that link opens. run output contains no key. There is exactly one sentence on how to turn it off."""
        p = self.init("--view-link")
        st = self.state()
        edit = f"https://{HOST}/?k={st['edit_key']}"
        self.assertIn(edit, p.stdout)
        self.assertIn(f"https://{HOST}/?k={st['view_key']}", p.stdout)
        self.assertIn("cera links personal", p.stdout)
        again = self.init()
        self.assertNotIn(st["edit_key"], again.stdout, "두 번째 init 은 키를 다시 찍지 않는다")
        self.assertIn("cera links personal", again.stdout)
        self.run_site()
        code, _ = get(self.port, f"/?k={st['edit_key']}", HOST)
        self.assertEqual(code, 200, "init 이 보인 링크는 '초대 링크가 필요해요'(403)가 아니다")
        self.tb("stop", "personal")
        time.sleep(0.5)
        log = (self.t / "run-personal.log").read_text()
        self.assertIn("초대 링크: cera links personal", log)
        for secret in (st["edit_key"], st["view_key"]):
            self.assertNotIn(secret, log)
        self.assertNotIn("k=", log)
        stop = next(l.strip() for l in log.splitlines() if l.startswith("끄기:"))
        self.assertIn(stop, self.tb("site", "guide").stdout)
        helptext = " ".join(self.tb("site", "run", "--help").stdout.split())
        self.assertIn(" ".join(stop.split()), helptext)

    def test_access_init_has_no_keyed_link(self):
        p = self.init("--access-team", "myteam", "--access-aud", "c" * 64)
        self.assertNotIn("?k=", p.stdout)
        self.assertIn("Access 로그인", p.stdout)

    def test_doctor_hides_raw_errors_and_flags_restart_storm(self):
        """Raw exceptions (Expecting value ...) appear only with --verbose, and 7 restarts at the tail of log.txt give WARN."""
        self.init()
        log = self.tbhome / "sites" / "personal" / "log.txt"
        now = time.time()
        lines = [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 3000)) + " tunnel exited (1) — 1초 뒤 다시 연다(1)"] * 5
        lines.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 600)) + " server http://127.0.0.1:1")
        lines += [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 500 + i * 30)) + f" tunnel exited (0) — {i}초 뒤 다시 연다({i})"
                  for i in range(7)]
        log.write_text("\n".join(lines) + "\n")
        (self.tbhome / "sites" / "personal" / "members.json").write_text("{broken")
        st = self.state()
        # an https local closed address -- keeps the test from reaching cloudflareaccess.com (a failed download = WARN)
        st["access"] = {"team": "myteam", "aud": "c" * 64, "jwks_url": "https://127.0.0.1:9/certs"}
        (self.tbhome / "sites" / "personal" / "state.json").write_text(json.dumps(st))
        env = dict(self.env, FAKE_CF_INFO="not json")
        d = self.tb("site", "doctor", env=env, check=False, timeout=120)
        out = d.stdout
        self.assertRegex(out, r"WARN\s+터널 다시 열기 — 최근 15분에 7번", "옛 감독의 줄(5개)은 세지 않는다")
        self.assertRegex(out, r"WARN\s+터널 연결 — 확인하지 못했다")
        self.assertRegex(out, r"WARN\s+Access 키\(JWKS\) — 받지 못했다\(연결 거부")
        self.assertRegex(out, r"FAIL\s+members\.json — 틀렸다 — .*JSON 형식이 틀렸다\(줄 1, 칸 2\)")
        for raw in ("Expecting", "Error:", "Errno", "JSONDecodeError", "Traceback"):
            self.assertNotIn(raw, out, raw)
        v = self.tb("site", "doctor", "--verbose", env=env, check=False, timeout=120).stdout
        self.assertIn("(원문)", v)
        self.assertIn("Expecting", v)


@unittest.skipUnless(PY39.is_file(), "/usr/bin/python3 없음")
class Python39(Base):
    def test_site_cli_on_system_python(self):
        self.assertIn("doctor", self.tb("site", "--help", py=str(PY39)).stdout)
        p = self.tb("site", "init", "--hostname", HOST, py=str(PY39), check=False)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.cert()
        self.tb("site", "init", "--hostname", HOST, "--port", str(self.port), py=str(PY39))
        self.assertIn("ProgramArguments", self.tb("site", "install", "--dry-run", "--platform", "macos", py=str(PY39)).stdout)
        code = ("import importlib.util\n"
                f"s=importlib.util.spec_from_file_location('cera_cfaccess',{str(HERE / 'server' / 'cfaccess.py')!r});"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.team_name('x'))")
        self.assertEqual(subprocess.run([str(PY39), "-c", code], capture_output=True, text=True).stdout.strip(), "x")


if __name__ == "__main__":
    unittest.main()
