# SPDX-License-Identifier: MIT
"""Operational fixes that the personal-site review handed off to the bin side.

- `cera links/list/keys` show the key-free public address on an Access-mode personal site (bin does it directly, without personal's wrapper).
- `cera stop` does not spend 5 seconds waiting for a child that was never reaped (a zombie); it reads stopped from state.
- Personal-site `delete`: refused if the always-on service is installed; when deleting, the tunnel credentials are moved to $CERA_HOME/kept/.
- A `site run` started as a service (CERA_SERVICE=1) waits and takes over if another supervisor is running, instead of exiting 1 and being relaunched in a loop;
  exits 0 if it was shut down by `cera stop`.
HOME and CERA_HOME are temporary folders. No server or tunnel is started (only state files and fake processes)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["CERA_LANG"] = "ko"   # existing assertions are Korean; set before anything spawns the CLI or imports server code

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "cera"
HOST = "board.example.com"

# On SIGTERM it writes state as stopped and exits, like a supervisor. Its parent (the test) does not reap it, so it stays a zombie.
CHILD = r'''
import json, os, signal, sys, time
path = sys.argv[1]
def bye(*_):
    st = json.load(open(path)); st.update(status="stopped", pid=None)
    open(path + ".tmp", "w").write(json.dumps(st)); os.replace(path + ".tmp", path)
    os._exit(0)
signal.signal(signal.SIGTERM, bye)
print("ready", flush=True)
while True:
    time.sleep(1)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.home.mkdir()
        self.tbh = self.t / "tb"
        env = {k: v for k, v in os.environ.items() if k not in ("CERA_DIST", "CERA_SERVICE")}
        self.env = {**env, "HOME": str(self.home), "CERA_HOME": str(self.tbh)}
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            for f in (p.stdout, p.stderr):
                if f:
                    f.close()
        self.tmp.cleanup()

    def tb(self, *args, check=True, env=None, timeout=60):
        p = subprocess.run([sys.executable, str(CLI), *args], env=env or self.env, capture_output=True, text=True, timeout=timeout)
        if check:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def site(self, sid="personal", **kw):
        d = self.tbh / "sites" / sid
        (d / "data").mkdir(parents=True)
        st = {"id": sid, "title": "", "board": "b1", "status": "stopped", "created_at": time.time(), "expires_at": 0,
              "edit_key": "EDITKEY", "view_key": None, "tunnel": "named", "host": "127.0.0.1", "port": 8790, "pid": None,
              "public_url": f"https://{HOST}", "local_url": "http://127.0.0.1:8790", "mode": "personal", "hostname": HOST,
              "tunnel_name": "cera-board-example-com", "access": None}
        st.update(kw)
        (d / "state.json").write_text(json.dumps(st))
        os.chmod(d / "state.json", 0o600)
        return d

    def state(self, sid="personal"):
        return json.loads((self.tbh / "sites" / sid / "state.json").read_text())

    def sleeper(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        self.procs.append(p)
        return p


class AccessLinks(Base):
    def test_links_list_keys_without_wrappers(self):
        self.site(status="running", pid=os.getpid(), access={"team": "myteam", "aud": "c" * 64})
        j = json.loads(self.tb("links", "personal", "--json").stdout)
        self.assertEqual(j["links"]["edit"], f"https://{HOST}/#b=b1")
        self.assertEqual(j["links"]["local"], "http://127.0.0.1:8790/?k=EDITKEY#b=b1")
        self.assertEqual(j["access"], {"team": "myteam"})
        txt = self.tb("links", "personal").stdout
        self.assertIn(f"https://{HOST}/#b=b1", txt)
        self.assertNotIn(f"https://{HOST}/?k=", txt)
        self.assertIn("Access", txt)
        lst = json.loads(self.tb("list", "--json").stdout)
        self.assertEqual(lst[0]["links"]["edit"], f"https://{HOST}/#b=b1", "list 도 키 없는 공개 주소")
        self.assertIn("Access 모드", self.tb("keys", "personal").stdout)
        rot = self.tb("keys", "personal", "--rotate", "edit")
        self.assertIn("Access 모드", rot.stdout)
        self.assertNotEqual(self.state()["edit_key"], "EDITKEY")
        j = json.loads(self.tb("site", "links", "--json").stdout)
        self.assertEqual(j["links"]["edit"], f"https://{HOST}/#b=b1")

    def test_rotate_all_says_agent_tokens_stay(self):
        """CLI: even when 'all keys' are rotated, the agent token stays the same, and the output says so along with the command that cuts it off."""
        far = time.time() + 3600
        self.site("t3", mode=None, tunnel="none", public_url=None,
                  agents=[{"id": "a1", "name": "C", "sha256": "x", "role": "edit", "expires_at": far},
                          {"id": "a2", "name": "D", "sha256": "y", "role": "edit", "expires_at": time.time() - 5}])
        out = self.tb("keys", "t3", "--rotate", "all").stdout
        self.assertIn("에이전트 토큰(지금 1개)은 그대로다", out)
        self.assertIn("cera agent revoke t3 --all", out)
        j = json.loads(self.tb("keys", "t3", "--rotate", "edit", "--json").stdout)   # --json prints exactly one JSON document
        self.assertIn("links", j)
        self.assertEqual(j["agent_tokens_kept"], 1)
        self.assertEqual(j["revoke_agents"], "cera agent revoke t3 --all")

    def test_without_access_keeps_invite_links(self):
        self.site(status="running", pid=os.getpid())
        j = json.loads(self.tb("links", "personal", "--json").stdout)
        self.assertEqual(j["links"]["edit"], f"https://{HOST}/?k=EDITKEY#b=b1")


class Stop(Base):
    def test_stop_does_not_wait_for_unreaped_child(self):
        self.site("t1", tunnel="none", mode=None, public_url=None)
        path = self.tbh / "sites" / "t1" / "state.json"
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(path)], stdout=subprocess.PIPE, text=True)
        self.procs.append(child)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        st = json.loads(path.read_text())
        st.update(status="running", pid=child.pid)
        path.write_text(json.dumps(st))
        t0 = time.time()
        p = self.tb("stop", "t1")
        took = time.time() - t0
        self.assertIn("닫음", p.stdout)
        self.assertLess(took, 3.0, f"좀비를 기다렸다({took:.1f}초)")
        child.wait(5)
        st = self.state("t1")
        self.assertEqual(st["status"], "stopped")
        self.assertIn("stop_requested_at", st)


class Delete(Base):
    def test_personal_delete_guards_service_and_keeps_credentials(self):
        d = self.site()
        (d / "tunnel.json").write_text('{"TunnelID": "x", "TunnelSecret": "s"}')
        os.chmod(d / "tunnel.json", 0o600)
        (d / "members.json").write_text('{"edit": ["*"]}')
        svc = self.home / "svc.plist"
        svc.write_text("com.cera.personal")
        st = self.state()
        st["service"] = {"platform": "macos", "path": str(svc)}
        (d / "state.json").write_text(json.dumps(st))
        p = self.tb("delete", "personal", "--yes", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("site uninstall --id personal --load", p.stderr)
        self.assertTrue(d.is_dir())
        svc.unlink()
        p = self.tb("delete", "personal", "--yes")
        self.assertFalse(d.exists())
        kept = list((self.tbh / "kept").iterdir())
        self.assertEqual(len(kept), 1)
        self.assertEqual(sorted(x.name for x in kept[0].iterdir()), ["members.json", "tunnel.json"])
        self.assertEqual(kept[0].stat().st_mode & 0o777, 0o700)
        self.assertEqual((kept[0] / "tunnel.json").stat().st_mode & 0o777, 0o600)
        self.assertIn("cloudflared tunnel delete cera-board-example-com", p.stdout)
        self.assertNotIn("TunnelSecret", p.stdout + p.stderr)

    def test_temp_site_delete_unchanged(self):
        self.site("t2", mode=None, tunnel="cloudflared", public_url=None)
        self.tb("delete", "t2", "--yes")
        self.assertFalse((self.tbh / "kept").exists())


class ServiceWait(Base):
    def test_service_run_waits_then_honours_stop(self):
        other = self.sleeper()
        self.site(status="running", pid=other.pid)
        # a site run started by a person is simply refused (exit 1)
        p = self.tb("site", "run", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("이미 돌고 있다", p.stderr)
        # one started as a service waits
        env = {**self.env, "CERA_SERVICE": "1"}
        svc = subprocess.Popen([sys.executable, str(CLI), "site", "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(svc)
        self.assertIn("이어받는다", svc.stdout.readline())
        time.sleep(2.5)
        self.assertIsNone(svc.poll(), "다른 감독이 도는 동안 끝나지 않는다(다시 뜨는 고리 없음)")
        # cera stop personal -> shuts down the other supervisor, and the service side ends with exit 0 (launchd does not relaunch it)
        self.tb("stop", "personal")
        other.wait(5)
        self.assertEqual(svc.wait(10), 0, svc.stderr.read())

    def test_stop_during_startup_is_honoured(self):
        """If a `cera stop` arrived during the web-files phase (stop_requested_at is after the start), no supervisor is started and it exits 0."""
        dist = self.t / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html>")
        self.site(stop_requested_at=time.time() + 3600)
        env = {**self.env, "CERA_SERVICE": "1", "CERA_DIST": str(dist)}
        p = self.tb("site", "run", env=env, timeout=30)
        self.assertIn("띄우지 않는다", p.stdout)
        self.assertEqual(self.state()["status"], "stopped")

    def test_service_run_takes_over_when_other_exits(self):
        other = self.sleeper()
        self.site(status="running", pid=other.pid)
        # make it stop at the web-files phase after taking over (no downloading from outside, no npm)
        env = {**self.env, "CERA_SERVICE": "1", "CERA_DIST": str(self.t / "nodist"),
               "CERA_RELEASE_URL": "http://127.0.0.1:9/", "PATH": "/usr/bin:/bin"}
        svc = subprocess.Popen([sys.executable, str(CLI), "site", "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(svc)
        self.assertIn("이어받는다", svc.stdout.readline())
        other.send_signal(signal.SIGKILL)   # the foreground supervisor ended by hand (Ctrl+C) or by accident -- no stop_requested_at
        other.wait(5)
        rc = svc.wait(15)   # it takes over and goes on to the real run, and ends at the web-files phase because dist is missing
        err = svc.stderr.read()
        self.assertNotEqual(rc, 0)
        self.assertIn("화면 파일", err)


if __name__ == "__main__":
    unittest.main()
