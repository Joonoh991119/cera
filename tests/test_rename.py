# SPDX-License-Identifier: MIT
"""The rename tempboard -> Cera (0.5.0): old names keep working, nothing the user had is lost.

- server/env.py: CERA_* first, then the old TEMPBOARD_* name; the data folder ~/.tempboard is moved to ~/.cera once,
  with a link left behind (fresh · migrate · already migrated · env override · both exist · dangling link).
- bin/tempboard is a shim: one deprecation line on stderr, then bin/cera with the same arguments and exit code.
- `cera install / uninstall` report (never remove) a plugin, MCP entry or skill link left under the old name.
- `cera site uninstall` (and `site install`, which replaces it) removes the old com.tempboard.<id> / tempboard-<id>.service,
  but only a file this program wrote.
HOME is always a temporary folder; the real ~/.tempboard, ~/.cera, ~/.claude and ~/.codex are never touched."""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["CERA_LANG"] = "en"

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "cera"
SHIM = HERE / "bin" / "tempboard"
NAMES = ("HOME", "DIST", "LANG", "SERVICE", "SITE_PORT", "URL", "TOKEN", "AGENT_NAME", "RELEASE_URL")


def clean_env(**extra):
    """os.environ without any CERA_* / TEMPBOARD_* variable, plus extra."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CERA_", "TEMPBOARD_", "CLAUDE_CONFIG_DIR", "CODEX_HOME"))}
    e.update(extra)
    return e


def load_env_module():
    spec = importlib.util.spec_from_file_location("env_under_test", HERE / "server" / "env.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class EnvHelper(unittest.TestCase):
    def setUp(self):
        self.m = load_env_module()

    def test_new_name_first_then_old_name(self):
        env = self.m.env
        self.assertEqual(env("DIST", environ={"CERA_DIST": "/new", "TEMPBOARD_DIST": "/old"}), "/new")
        self.assertEqual(env("DIST", environ={"TEMPBOARD_DIST": "/old"}), "/old")
        self.assertEqual(env("DIST", environ={"CERA_DIST": "", "TEMPBOARD_DIST": "/old"}), "/old", "empty counts as unset")
        self.assertIsNone(env("DIST", environ={}))
        self.assertEqual(env("DIST", "d", environ={}), "d")

    def test_home_override_with_either_name(self):
        with tempfile.TemporaryDirectory() as td:
            u = Path(td)
            self.assertEqual(self.m.home({"CERA_HOME": "/a"}, u), Path("/a"))
            self.assertEqual(self.m.home({"TEMPBOARD_HOME": "/b"}, u), Path("/b"))
            self.assertEqual(self.m.home({"CERA_HOME": "/a", "TEMPBOARD_HOME": "/b"}, u), Path("/a"))
            self.assertEqual(self.m.home({}, u), u / ".cera")


class Migration(unittest.TestCase):
    def setUp(self):
        self.m = load_env_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.u = Path(self.tmp.name)
        self.new, self.old = self.u / ".cera", self.u / ".tempboard"

    def tearDown(self):
        self.tmp.cleanup()

    def old_data(self):
        (self.old / "sites" / "s1" / "data").mkdir(parents=True)
        (self.old / "sites" / "s1" / "state.json").write_text('{"id": "s1"}')
        (self.old / "config.json").write_text('{"lang": "ko"}')

    def test_fresh(self):
        self.assertEqual(self.m.migrate_home({}, self.u), "fresh")
        self.assertFalse(os.path.lexists(self.new) or os.path.lexists(self.old), "nothing is created")
        self.assertEqual(self.m.home({}, self.u), self.new)

    def test_migrate_moves_and_leaves_a_link(self):
        self.old_data()
        self.assertEqual(self.m.migrate_home({}, self.u), "migrated")
        self.assertTrue(self.new.is_dir() and not self.new.is_symlink())
        self.assertEqual((self.new / "sites" / "s1" / "state.json").read_text(), '{"id": "s1"}')
        self.assertTrue(self.old.is_symlink())
        self.assertEqual(os.readlink(self.old), str(self.new))
        self.assertEqual((self.old / "config.json").read_text(), '{"lang": "ko"}', "old paths (an installed service) still resolve")
        self.assertEqual(self.m.home({}, self.u), self.new)

    def test_already_migrated_is_idempotent(self):
        self.old_data()
        self.m.migrate_home({}, self.u)
        before = sorted(str(p) for p in self.new.rglob("*"))
        self.assertEqual(self.m.migrate_home({}, self.u), "done")
        self.assertEqual(self.m.migrate_home({}, self.u), "done")
        self.assertEqual(sorted(str(p) for p in self.new.rglob("*")), before)
        self.assertTrue(self.old.is_symlink())

    def test_env_override_skips_migration(self):
        self.old_data()
        for var in ("CERA_HOME", "TEMPBOARD_HOME"):
            self.assertEqual(self.m.migrate_home({var: str(self.u / "elsewhere")}, self.u), "override")
            self.assertTrue(self.old.is_dir() and not self.old.is_symlink())
            self.assertFalse(os.path.lexists(self.new))
        self.assertEqual(self.m.home({"TEMPBOARD_HOME": str(self.old)}, self.u), self.old)

    def test_both_exist_nothing_is_moved(self):
        self.old_data()
        self.new.mkdir()
        (self.new / "mine").write_text("x")
        self.assertEqual(self.m.migrate_home({}, self.u), "done")
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink(), "the old folder is never touched when ~/.cera exists")
        self.assertTrue((self.old / "config.json").is_file())
        self.assertEqual(self.m.home({}, self.u), self.new)

    def test_dangling_old_link_is_left_alone(self):
        self.old.symlink_to(self.new)   # migrated earlier, then ~/.cera was removed
        self.assertEqual(self.m.migrate_home({}, self.u), "fresh")
        self.assertTrue(self.old.is_symlink() and not os.path.lexists(self.new))

    def test_failed_move_keeps_using_the_old_folder(self):
        self.old_data()
        os.chmod(self.u, 0o555)   # the rename into the same folder fails
        try:
            res = self.m.migrate_home({}, self.u)
            self.assertTrue(res.startswith("failed"), res)
            self.assertEqual(self.m.home({}, self.u), self.old)
        finally:
            os.chmod(self.u, 0o755)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())


class CliMigration(unittest.TestCase):
    """End to end through bin/cera with HOME pointing at a temporary folder."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, cli, *args, **env):
        return subprocess.run([sys.executable, str(cli), *args], env=clean_env(HOME=str(self.home), CERA_LANG="en", **env),
                              capture_output=True, text=True, timeout=60)

    def make_site(self, base: Path, sid="0101-0000-abcd"):
        d = base / "sites" / sid
        (d / "data").mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({"id": sid, "title": "old board", "status": "stopped", "created_at": time.time()}))
        return sid

    def test_first_run_moves_the_old_folder(self):
        sid = self.make_site(self.home / ".tempboard")
        p = self.run_cli(CLI, "list", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([s["id"] for s in json.loads(p.stdout)], [sid])
        self.assertIn("Moved the data folder", p.stderr)
        self.assertTrue((self.home / ".tempboard").is_symlink())
        self.assertTrue((self.home / ".cera" / "sites" / sid / "state.json").is_file())
        p = self.run_cli(CLI, "list", "--json")   # second run: nothing more to do, no message
        self.assertEqual([s["id"] for s in json.loads(p.stdout)], [sid])
        self.assertNotIn("Moved", p.stderr)

    def test_old_env_name_still_selects_the_folder(self):
        other = self.home / "custom"
        sid = self.make_site(other)
        p = self.run_cli(CLI, "list", "--json", TEMPBOARD_HOME=str(other))
        self.assertEqual([s["id"] for s in json.loads(p.stdout)], [sid])
        self.assertFalse(os.path.lexists(self.home / ".cera"), "an explicit home skips the migration")

    def test_shim_forwards_args_and_exit_code(self):
        sid = self.make_site(self.home / ".cera")
        p = self.run_cli(SHIM, "list", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([s["id"] for s in json.loads(p.stdout)], [sid])
        self.assertEqual(len([l for l in p.stderr.splitlines() if "cera" in l]), 1, p.stderr)
        self.assertIn("tempboard is now cera", p.stderr)
        bad = self.run_cli(SHIM, "links", "nope")
        ref = self.run_cli(CLI, "links", "nope")
        self.assertNotEqual(ref.returncode, 0)
        self.assertEqual(bad.returncode, ref.returncode)
        usage = self.run_cli(SHIM, "no-such-command")
        self.assertEqual(usage.returncode, 2)


RECORDER = r'''#!{py}
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\n")
'''


class LegacyAgentInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.bin = self.t / "fakebin"
        for d in (self.home, self.bin):
            d.mkdir()
        self.log = self.t / "calls.log"
        for n in ("claude", "codex", "agy"):
            f = self.bin / n
            f.write_text(RECORDER.format(py=sys.executable))
            f.chmod(0o755)
        self.env = clean_env(HOME=str(self.home), CERA_HOME=str(self.t / "cera"), CERA_LANG="en", CODEX_HOME=str(self.home / ".codex"),
                             PATH=f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", FAKE_LOG=str(self.log))
        # what tempboard 0.4 left behind: Claude plugin + marketplace, a user-scope MCP entry and a link-mode skill link
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {"tempboard@tempboard": [{}]}}))
        (plugins / "known_marketplaces.json").write_text(json.dumps({"tempboard": {}}))
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {"tempboard": {"command": "python3", "args": ["/x/bin/tempboard", "mcp"],
                                                                                        "env": {"TEMPBOARD_TOKEN": "secret"}}}}))
        (self.home / ".claude" / "skills").mkdir()
        (self.home / ".claude" / "skills" / "tempboard").symlink_to(self.t)
        (self.home / ".local" / "bin").mkdir(parents=True)
        (self.home / ".local" / "bin" / "tempboard").symlink_to(HERE / "bin" / "tempboard")

    def tearDown(self):
        self.tmp.cleanup()

    def tb(self, *args):
        return subprocess.run([sys.executable, str(CLI), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_reported_not_removed(self, out):
        for cmd in ("claude plugin uninstall tempboard@tempboard", "claude plugin marketplace remove tempboard",
                    "claude mcp remove --scope user tempboard", f"rm {self.home / '.claude' / 'skills' / 'tempboard'}",
                    f"rm {self.home / '.local' / 'bin' / 'tempboard'}"):
            self.assertIn(cmd, out)
        self.assertNotIn("secret", out, "env values are never printed")
        self.assertFalse(any("tempboard" in " ".join(c) for c in self.calls()), self.calls())
        self.assertIn("tempboard", json.loads((self.home / ".claude.json").read_text())["mcpServers"])
        self.assertTrue((self.home / ".claude" / "skills" / "tempboard").is_symlink())
        self.assertTrue((self.home / ".local" / "bin" / "tempboard").is_symlink())

    def test_install_reports_old_entries(self):
        p = self.tb("install", "--agent", "claude", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assert_reported_not_removed(p.stdout)
        self.assertEqual(self.calls(), [])
        p = self.tb("install", "--agent", "claude")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(["claude", "plugin", "install", "cera@cera"], self.calls())
        self.assert_reported_not_removed(p.stdout)

    def test_uninstall_reports_old_entries(self):
        p = self.tb("uninstall", "--agent", "claude")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assert_reported_not_removed(p.stdout)

    def test_nothing_old_means_no_note(self):
        for f in (self.home / ".claude", self.home / ".claude.json", self.home / ".local"):
            subprocess.run(["rm", "-rf", str(f)], check=True)
        p = self.tb("install", "--agent", "claude", "--dry-run")
        self.assertNotIn("old name", p.stdout)


class LegacyService(unittest.TestCase):
    SID = "personal"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.bin = self.t / "fakebin"
        for d in (self.home, self.bin):
            d.mkdir()
        self.log = self.t / "calls.log"
        for n in ("launchctl", "systemctl"):
            f = self.bin / n
            f.write_text(RECORDER.format(py=sys.executable))
            f.chmod(0o755)
        self.cera_home = self.t / "cera"
        self.env = clean_env(HOME=str(self.home), CERA_HOME=str(self.cera_home), CERA_LANG="en", XDG_CONFIG_HOME=str(self.home / ".config"),
                             PATH=f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", FAKE_LOG=str(self.log))
        d = self.cera_home / "sites" / self.SID
        (d / "data").mkdir(parents=True)
        st = {"id": self.SID, "title": "", "board": "b1", "status": "stopped", "created_at": time.time(), "expires_at": 0,
              "edit_key": "EDITKEY", "view_key": None, "tunnel": "named", "host": "127.0.0.1", "port": 8790, "pid": None,
              "public_url": "https://board.example.com", "local_url": "http://127.0.0.1:8790", "mode": "personal",
              "hostname": "board.example.com", "tunnel_name": "tempboard-board-example-com", "access": None}
        (d / "state.json").write_text(json.dumps(st))
        os.chmod(d / "state.json", 0o600)
        self.old_plist = self.home / "Library" / "LaunchAgents" / f"com.tempboard.{self.SID}.plist"
        self.new_plist = self.home / "Library" / "LaunchAgents" / f"com.cera.{self.SID}.plist"
        self.old_unit = self.home / ".config" / "systemd" / "user" / f"tempboard-{self.SID}.service"

    def tearDown(self):
        self.tmp.cleanup()

    def tb(self, *args):
        return subprocess.run([sys.executable, str(CLI), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def write_old_plist(self, label=None):
        self.old_plist.parent.mkdir(parents=True, exist_ok=True)
        self.old_plist.write_bytes(plistlib.dumps({"Label": label or f"com.tempboard.{self.SID}", "ProgramArguments": ["/usr/bin/python3"]}))

    def test_uninstall_removes_old_named_plist(self):
        self.write_old_plist()
        p = self.tb("site", "uninstall", "--platform", "macos", "--load")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(self.old_plist.exists())
        self.assertIn(["launchctl", "bootout", f"gui/{os.getuid()}/com.tempboard.{self.SID}"], self.calls())
        self.assertTrue((self.cera_home / "sites" / self.SID / "state.json").is_file(), "the site stays")

    def test_uninstall_leaves_a_foreign_file(self):
        self.write_old_plist(label="com.someone.else")
        p = self.tb("site", "uninstall", "--platform", "macos")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.old_plist.exists())
        self.assertIn("left untouched", p.stdout)

    def test_install_replaces_old_named_plist(self):
        self.write_old_plist()
        p = self.tb("site", "install", "--platform", "macos")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(self.old_plist.exists())
        self.assertTrue(self.new_plist.is_file())
        self.assertEqual(plistlib.loads(self.new_plist.read_bytes())["Label"], f"com.cera.{self.SID}")
        self.assertIn(f"launchctl bootout gui/{os.getuid()}/com.tempboard.{self.SID}", p.stdout, "without --load the unload command is shown")
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.cera_home / "bin" / "cera").is_file())

    def test_status_and_delete_see_the_old_service(self):
        self.write_old_plist()
        if sys.platform == "darwin":
            j = json.loads(self.tb("site", "status", "--json").stdout)
            self.assertEqual(j["service"]["path"], str(self.old_plist))
            self.assertTrue(j["service"]["legacy"])
            p = self.tb("delete", self.SID, "--yes")
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("site uninstall", p.stderr)

    def test_linux_old_unit(self):
        self.old_unit.parent.mkdir(parents=True)
        self.old_unit.write_text(f"[Unit]\nDescription=tempboard personal board ({self.SID})\n")
        p = self.tb("site", "uninstall", "--platform", "linux", "--load")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(self.old_unit.exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", f"tempboard-{self.SID}.service"], self.calls())


if __name__ == "__main__":
    unittest.main()
