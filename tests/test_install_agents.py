# SPDX-License-Identifier: MIT
"""Tests for `cera install/uninstall --agent ... --mode plugin|link`, `--print-config`, and the fixed entry point (launcher).

HOME, CODEX_HOME, and CERA_HOME are always temporary folders. PATH holds only a fake claude and codex (which record their arguments) plus /usr/bin:/bin,
so the real claude, codex, and agy are never called. The tests also check that cera's locations in the real home (skill links, ~/.local/bin, Antigravity mcp_config) are unchanged."""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["CERA_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "cera"
# Real locations that would change if a test picked the wrong HOME (~/.claude.json and ~/.codex/config.toml, which other programs edit constantly, can't be checked by mtime
# so they are left out; only the locations cera writes are watched)
REAL = [Path.home() / ".agents" / "skills" / "cera", Path.home() / ".claude" / "skills" / "cera",
        Path.home() / ".gemini" / "config" / "skills" / "cera", Path.home() / ".gemini" / "config" / "mcp_config.json",
        Path.home() / ".local" / "bin" / "cera"]

RECORDER = r'''#!{py}
import json, os, sys
rec = [os.path.basename(sys.argv[0])] + sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps(rec) + "\n")
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''

# fake codex -- records its arguments and keeps MCP registrations in FAKE_CODEX_DB (JSON). `mcp get <name> --json` answers in the shape of the real 0.159.
FAKE_CODEX = r'''#!{py}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps(["codex"] + argv) + "\n")
dbp = os.environ.get("FAKE_CODEX_DB", "")
db = json.load(open(dbp)) if dbp and os.path.exists(dbp) else {{}}
def save():
    json.dump(db, open(dbp, "w"))
if argv[:2] == ["mcp", "get"]:
    if os.environ.get("FAKE_CODEX_GET_BROKEN"):
        sys.stderr.write("Error: failed to load configuration\n"); sys.exit(1)
    e = db.get(argv[2])
    if e is None:
        sys.stderr.write("Error: No MCP server named '%s' found.\n" % argv[2]); sys.exit(1)
    print(json.dumps({{"name": argv[2], "enabled": True, "disabled_reason": None,
                      "transport": {{"type": "stdio", "command": e["command"], "args": e["args"], "env": e.get("env"),
                                    "env_vars": [], "cwd": None}},
                      "enabled_tools": None, "disabled_tools": e.get("disabled_tools"), "startup_timeout_sec": None,
                      "tool_timeout_sec": None}}))
    sys.exit(0)
if argv[:2] == ["mcp", "add"]:
    i = argv.index("--"); db[argv[2]] = {{"command": argv[i + 1], "args": argv[i + 2:]}}; save()
elif argv[:2] == ["mcp", "remove"]:
    db.pop(argv[2], None); save()
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


def mtimes():
    return {str(p): (p.lstat().st_mtime_ns if (p.exists() or p.is_symlink()) else None) for p in REAL}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_before = mtimes()

    @classmethod
    def tearDownClass(cls):
        assert mtimes() == cls.real_before, "실제 사용자 설정이 바뀌었다"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.home.mkdir()
        self.bin = self.t / "fakebin"
        self.bin.mkdir()
        self.log = self.t / "calls.log"
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CERA_DIST", "CLAUDECODE")}
        self.env = {**env, "HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"), "CERA_HOME": str(self.t / "tb"),
                    "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", "FAKE_LOG": str(self.log),
                    "FAKE_CODEX_DB": str(self.t / "codex-db.json")}

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, *names):
        for n in names:
            p = self.bin / n
            p.write_text((FAKE_CODEX if n == "codex" else RECORDER).format(py=sys.executable))
            p.chmod(0o755)

    def tb(self, *args, check=True):
        p = subprocess.run([sys.executable, str(CLI), *args], env=self.env, capture_output=True, text=True, timeout=60)
        if check:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def calls(self, with_get=False):
        """Recorded calls. `codex mcp get` (read-only) is excluded."""
        out = [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []
        return out if with_get else [c for c in out if c[:3] != ["codex", "mcp", "get"]]

    def codex_db(self):
        f = self.t / "codex-db.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def files(self):
        # (/usr/bin/python3 writes pycache into $HOME/Library/Caches/com.apple.python -- that folder is excluded)
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*") if p.relative_to(self.home).parts[0] != "Library")


class LinkMode(Base):
    def test_codex_and_claude_link(self):
        """Calls the fake codex/claude with exactly the right arguments, and creates the skill links and the ~/.local/bin link. --dry-run does nothing."""
        self.fake("codex", "claude")
        p = self.tb("install", "--agent", "codex", "--mode", "link", "--dry-run")
        self.assertIn("codex mcp add cera --", p.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.files(), [])
        self.tb("install", "--agent", "codex", "--mode", "link")
        self.tb("install", "--agent", "claude", "--mode", "link")
        mcp = [sys.executable, str(HERE / "bin" / "cera"), "mcp"]
        self.assertEqual(self.calls(), [["codex", "mcp", "add", "cera", "--"] + mcp,
                                        ["claude", "mcp", "add", "--scope", "user", "cera", "--"] + mcp])
        for link, target in ((self.home / ".agents/skills/cera", HERE / "skills/cera"),
                             (self.home / ".claude/skills/cera", HERE / "skills/cera"),
                             (self.home / ".local/bin/cera", HERE / "bin/cera")):
            self.assertTrue(link.is_symlink(), link)
            self.assertEqual(os.path.realpath(link), str(target.resolve()))
        # the CLI runs via ~/.local/bin/cera (ROOT is found through the link)
        r = subprocess.run([str(self.home / ".local/bin/cera"), "--help"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("install", r.stdout)
        # uninstall: removing only codex leaves the ~/.local/bin link, since claude still uses it
        self.log.unlink()
        self.tb("uninstall", "--agent", "codex")
        self.assertEqual(self.calls(), [["codex", "mcp", "remove", "cera"]])
        self.assertFalse((self.home / ".agents/skills/cera").exists())
        self.assertTrue((self.home / ".local/bin/cera").is_symlink())
        self.tb("uninstall", "--agent", "claude")
        self.assertFalse((self.home / ".claude/skills/cera").exists())
        self.assertFalse((self.home / ".local/bin/cera").exists(), "남은 링크 모드가 없으면 지운다")

    def test_antigravity_link_merge_idempotent_uninstall(self):
        """mcp_config.json is merged (other servers preserved, .bak made), skills are linked, doing it twice gives the same result, and uninstall removes only what we added."""
        cfg = self.home / ".gemini/config/mcp_config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": ["y"]}}, "keep": 1}))
        cfg.chmod(0o640)
        self.tb("install", "--agent", "antigravity", "--mode", "link")
        d = json.loads(cfg.read_text())
        self.assertEqual(d["mcpServers"]["other"], {"command": "x", "args": ["y"]})
        self.assertEqual(d["keep"], 1)
        self.assertEqual(d["mcpServers"]["cera"], {"command": sys.executable, "args": [str(HERE / "bin/cera"), "mcp"]})
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o640, "권한을 그대로")
        baks = list(cfg.parent.glob("mcp_config.json.bak-*"))
        self.assertEqual(len(baks), 1)
        self.assertNotIn("cera", baks[0].read_text())
        link = self.home / ".gemini/config/skills/cera"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(link), str((HERE / "skills/cera").resolve()))
        before = (cfg.read_text(), self.files())
        p = self.tb("install", "--agent", "antigravity", "--mode", "link")
        self.assertEqual((cfg.read_text(), self.files()), before, "두 번째는 바뀌는 것이 없다")
        self.assertIn("이미", p.stdout)
        self.assertIn("agy", p.stdout, "agy 가 없다는 안내")
        self.tb("uninstall", "--agent", "antigravity", "--mode", "link")
        d = json.loads(cfg.read_text())
        self.assertNotIn("cera", d["mcpServers"])
        self.assertEqual(d["mcpServers"]["other"], {"command": "x", "args": ["y"]})
        self.assertFalse(link.exists() or link.is_symlink())
        self.assertFalse((self.home / ".local/bin/cera").exists())

    def test_empty_and_broken_json(self):
        cfg = self.home / ".gemini/config/mcp_config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("")   # 0 bytes, like this Mac's old Antigravity
        self.tb("install", "--agent", "antigravity", "--mode", "link")
        self.assertIn("cera", json.loads(cfg.read_text())["mcpServers"])
        cfg.write_text("{broken")
        p = self.tb("install", "--agent", "antigravity", "--mode", "link", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(cfg.read_text(), "{broken", "깨진 파일은 손대지 않는다")

    def test_does_not_clobber_foreign_files(self):
        self.fake("codex")
        own = self.home / ".agents/skills/cera"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("---\nname: cera\ndescription: 내 사본\n---\n")
        p = self.tb("install", "--agent", "codex", "--mode", "link", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue((own / "SKILL.md").is_file() and not own.is_symlink(), "사용자의 폴더는 그대로")
        # nothing is half-installed -- no MCP registration and no ~/.local/bin link either
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.home / ".local/bin/cera").exists())
        # someone else's link (not a cera skill) is not counted as link mode -> plugin install is not refused for that reason
        import shutil as _sh
        _sh.rmtree(own)
        other = self.t / "other-skill"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\n")
        own.symlink_to(other)
        self.tb("install", "--agent", "codex")
        self.assertEqual(self.calls()[-1], ["codex", "plugin", "add", "cera@cera"])

    def test_missing_cli_gives_print_config_hint(self):
        p = self.tb("install", "--agent", "codex", "--mode", "link")
        self.assertIn("--print-config codex", p.stdout)
        self.assertTrue((self.home / ".agents/skills/cera").is_symlink())


class KeepExistingEntry(Base):
    """Antigravity mcp_config.json -- merged via the json module: only command and args change; env (remote tokens), tool settings, and unknown fields are preserved."""
    ENV = {"CERA_URL": "https://board.example.com", "CERA_TOKEN": "tb_ag_SECRET", "CF_ACCESS_CLIENT_ID": "cid"}

    def argv(self):
        return [sys.executable, str(HERE / "bin/cera"), "mcp"]

    def test_antigravity_keeps_env_and_tool_settings(self):
        cfg = self.home / ".gemini/config/mcp_config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcpServers": {"cera": {"command": "/old/python", "args": ["/old/bin/cera", "mcp"],
                                                                "env": self.ENV, "disabledTools": ["board_delete"],
                                                                "timeoutSeconds": 90, "x-mine": 1}}}))
        self.tb("install", "--agent", "antigravity", "--mode", "link")
        e = json.loads(cfg.read_text())["mcpServers"]["cera"]
        self.assertEqual((e["command"], e["args"]), (self.argv()[0], self.argv()[1:]))
        self.assertEqual(e["env"], self.ENV)
        self.assertEqual(e["disabledTools"], ["board_delete"])
        self.assertEqual((e["timeoutSeconds"], e["x-mine"]), (90, 1))
        before = cfg.read_text()
        self.tb("install", "--agent", "antigravity", "--mode", "link")
        self.assertEqual(cfg.read_text(), before)

    def test_antigravity_writes_through_symlink_and_refuses_non_object(self):
        """A linked mcp_config.json stays a link and the change lands in the original; if the top level is not an object it is left untouched."""
        real = self.t / "dotfiles" / "mcp_config.json"
        real.parent.mkdir()
        body = json.dumps({"mcpServers": {"other": {"command": "x"}}})
        real.write_text(body)
        real.chmod(0o640)
        cfg = self.home / ".gemini/config/mcp_config.json"
        cfg.parent.mkdir(parents=True)
        cfg.symlink_to(real)
        self.tb("install", "--agent", "antigravity", "--mode", "link")
        self.assertTrue(cfg.is_symlink(), "링크를 보통 파일로 바꾸지 않는다")
        d = json.loads(real.read_text())
        self.assertEqual(d["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(d["mcpServers"]["cera"]["args"], self.argv()[1:])
        self.assertEqual(real.stat().st_mode & 0o777, 0o640)
        baks = list(cfg.parent.glob("mcp_config.json.bak-*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_text(), body)
        self.assertEqual([p.name for p in real.parent.iterdir()], ["mcp_config.json"], "tmp 가 남지 않는다")
        # top level is an array, or mcpServers is an array -> refused, bytes unchanged, and no link is made
        self.tb("uninstall", "--agent", "antigravity", "--mode", "link")
        for bad in ("[1, 2]", '{"mcpServers": []}'):
            real.write_text(bad)
            p = self.tb("install", "--agent", "antigravity", "--mode", "link", check=False)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertEqual(real.read_text(), bad)
            self.assertFalse((self.home / ".gemini/config/skills/cera").exists())
            self.assertFalse((self.home / ".local/bin/cera").exists())


class ExistingEntryLeftAlone(Base):
    """An existing cera entry in Claude or Codex is never touched: if identical, left as is; if different, it reports the differences, a one-line command, and exit 3;
    only when absent is it added through that tool's CLI (for Codex, config.toml is backed up first)."""
    ENV = {"CERA_URL": "https://board.example.com", "CERA_TOKEN": "tb_ag_SECRET"}

    def argv(self):
        return [sys.executable, str(HERE / "bin/cera"), "mcp"]

    def no_change_expected(self, p, files_before, extra_files=()):
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertNotIn("tb_ag_SECRET", p.stdout + p.stderr, "토큰 값은 찍지 않는다")
        self.assertEqual(self.calls(), [], "add · remove 를 부르지 않는다")
        self.assertEqual(self.files(), files_before, "링크 · 백업 · 새 파일 없음")
        self.assertNotIn("끝 —", p.stdout)

    def test_claude(self):
        self.fake("claude")
        cj = self.home / ".claude.json"
        raw = json.dumps({"numStartups": 3, "mcpServers": {"cera": {"type": "stdio", "command": "/old/python",
                                                                         "args": ["/old/bin/cera", "mcp"], "env": self.ENV},
                                                           "other": {"command": "o"}}}, indent=1)
        cj.write_text(raw)
        before = self.files()
        p = self.tb("install", "--agent", "claude", "--mode", "link", check=False)
        self.no_change_expected(p, before)
        self.assertEqual(cj.read_text(), raw)
        for want in ("command  지금 /old/python", f"새로 {sys.executable}", "args     지금", "env(CERA_TOKEN, CERA_URL)",
                     "claude mcp remove --scope user cera && claude mcp add --scope user cera --"):
            self.assertIn(want, p.stderr)
        # identical -> do nothing (0)
        same = {"mcpServers": {"cera": {"type": "stdio", "command": self.argv()[0], "args": self.argv()[1:], "env": self.ENV}}}
        cj.write_text(json.dumps(same))
        p = self.tb("install", "--agent", "claude", "--mode", "link")
        self.assertIn("이미 그대로", p.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(json.loads(cj.read_text()), same)
        # absent -> claude mcp add
        self.tb("uninstall", "--agent", "claude", "--mode", "link")
        cj.write_text(json.dumps({"mcpServers": {"other": {"command": "o"}}}))
        self.log.unlink()
        self.tb("install", "--agent", "claude", "--mode", "link")
        self.assertEqual(self.calls(), [["claude", "mcp", "add", "--scope", "user", "cera", "--"] + self.argv()])
        # broken ~/.claude.json -> refused (1), untouched
        cj.write_text("{oops")
        self.tb("uninstall", "--agent", "claude", "--mode", "link")
        self.log.unlink()
        p = self.tb("install", "--agent", "claude", "--mode", "link", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(cj.read_text(), "{oops")
        self.assertEqual(self.calls(), [])

    def test_codex(self):
        self.fake("codex")
        cfg = self.home / ".codex/config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_bytes(b"# mine\r\n[mcp_servers.cera]\r\ncommand = \"/old/python\"\r\n")   # the fake codex never looks at the content
        (self.t / "codex-db.json").write_text(json.dumps({"cera": {"command": "/old/python", "args": ["/old", "mcp"],
                                                                       "env": self.ENV, "disabled_tools": ["board_delete"]}}))
        before = self.files()
        raw = cfg.read_bytes()
        p = self.tb("install", "--agent", "codex", "--mode", "link", check=False)
        self.no_change_expected(p, before)
        self.assertEqual(cfg.read_bytes(), raw)
        for want in ("env(CERA_TOKEN, CERA_URL)", "disabled_tools", "codex mcp remove cera && codex mcp add cera --",
                     str(cfg)):
            self.assertIn(want, p.stderr)
        # identical -> left as is
        (self.t / "codex-db.json").write_text(json.dumps({"cera": {"command": self.argv()[0], "args": self.argv()[1:],
                                                                       "env": self.ENV}}))
        p = self.tb("install", "--agent", "codex", "--mode", "link")
        self.assertIn("이미 그대로", p.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(cfg.read_bytes(), raw)
        self.assertEqual(list(cfg.parent.glob("config.toml.bak-*")), [])
        # absent: back up config.toml -> codex mcp add -> verify with get
        self.tb("uninstall", "--agent", "codex", "--mode", "link")
        self.assertEqual(len(list(cfg.parent.glob("config.toml.bak-*"))), 1, "env 가 있는 항목을 지우기 전에 백업")
        self.log.unlink()
        self.tb("install", "--agent", "codex", "--mode", "link")
        self.assertEqual(self.calls(), [["codex", "mcp", "add", "cera", "--"] + self.argv()])
        self.assertEqual(len(list(cfg.parent.glob("config.toml.bak-*"))), 2, "넣기 전에도 백업")
        self.assertEqual(self.codex_db()["cera"], {"command": self.argv()[0], "args": self.argv()[1:]})

    def test_codex_unreadable_config_refuses(self):
        self.fake("codex")
        cfg = self.home / ".codex/config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("broken = [\n")
        self.env["FAKE_CODEX_GET_BROKEN"] = "1"
        before = self.files()
        p = self.tb("install", "--agent", "codex", "--mode", "link", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("codex mcp list", p.stderr)
        self.assertEqual(cfg.read_text(), "broken = [\n")
        self.assertEqual(self.files(), before)
        self.assertEqual(self.calls(), [])


CODEX = shutil.which("codex")
NODE = shutil.which("node")


@unittest.skipUnless(CODEX, "codex 가 없다")
class CodexRealShapes(Base):
    """Reproduction shapes for earlier bugs: if the real codex (isolated CODEX_HOME) reads cera as present, install leaves the file
    byte-identical (exit 3). Only when absent does codex mcp add write (other servers keep the values codex read)."""
    OLD = "/old/python"
    SHAPES = {
        "header_eq": (b'[mcp_servers.cera]\ncommand = "/old/python"\nargs = ["/old/bin/cera", "mcp"]\n\n'
                      b'[mcp_servers.cera.env]\nCERA_TOKEN = "tb_ag_SECRET"\n\n'
                      b'[mcp_servers.other]  # see docs: key=value\ncommand = "node"\nargs = ["other.js"]\n'),
        "quoted_keys": b'[mcp_servers.cera]\n"command" = "/old/python"\n"args" = ["/old/bin/cera", "mcp"]\n',
        "multiline_string": (b'[mcp_servers.cera]\ncommand = "/old/python"\nargs = ["x"]\n'
                             b'[mcp_servers.cera.env]\nNOTE = """\n[see below]\nkey = 1\n"""\n'),
        "no_trailing_newline": b'model = "x"\n[mcp_servers.cera]\ncommand = "/old/python"\nargs = ["a"]',
        "inline_table": (b'[mcp_servers]\ncera = { command = "/old/python", args = ["a"], '
                         b'env = { CERA_TOKEN = "tb_ag_SECRET" } }\n'),
        "dotted": (b'mcp_servers.cera.command = "/old/python"\nmcp_servers.cera.args = ["a"]\n'
                   b'mcp_servers.cera.env.CERA_TOKEN = "tb_ag_SECRET"\n'),
        "literal_header": b"[mcp_servers.'cera']\ncommand = '/old/python'\nargs = ['a']\n",
        "crlf": b'# c\r\n[mcp_servers.cera]\r\ncommand = "/old/python"\r\nargs = ["a"]\r\n',
        "http_entry": b'[mcp_servers.cera]\nurl = "https://example.com/mcp"\n',
    }

    def setUp(self):
        super().setUp()
        dirs = list(dict.fromkeys(os.path.dirname(x) for x in (CODEX, NODE) if x))
        self.env["PATH"] = os.pathsep.join([str(self.bin)] + dirs + ["/usr/bin", "/bin"])
        self.cfg = self.home / ".codex/config.toml"
        self.cfg.parent.mkdir(parents=True)

    def get(self, name):
        r = subprocess.run([CODEX, "mcp", "get", name, "--json"], env=self.env, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 else None

    def test_existing_entry_shapes_are_byte_identical(self):
        for name, raw in self.SHAPES.items():
            with self.subTest(shape=name):
                self.cfg.write_bytes(raw)
                self.assertIsNotNone(self.get("cera"), f"{name}: codex 가 이 모양을 읽어야 시험이 뜻이 있다")
                p = self.tb("install", "--agent", "codex", "--mode", "link", check=False)
                self.assertEqual(p.returncode, 3, name + p.stdout + p.stderr)
                self.assertEqual(self.cfg.read_bytes(), raw, name)
                self.assertEqual(list(self.cfg.parent.glob("config.toml.bak-*")), [], name)
                self.assertFalse((self.home / ".agents/skills/cera").exists(), name)
                self.assertNotIn("tb_ag_SECRET", p.stdout + p.stderr)

    def test_symlinked_config_is_untouched(self):
        real = self.t / "dotfiles" / "config.toml"
        real.parent.mkdir()
        real.write_bytes(self.SHAPES["header_eq"])
        self.cfg.symlink_to(real)
        p = self.tb("install", "--agent", "codex", "--mode", "link", check=False)
        self.assertEqual(p.returncode, 3)
        self.assertTrue(self.cfg.is_symlink())
        self.assertEqual(real.read_bytes(), self.SHAPES["header_eq"])

    def test_absent_entry_added_other_server_kept(self):
        raw = (b'# mine: key=value\n[mcp_servers.other]  # see docs: key=value\ncommand = "node"\nargs = ["other.js"]\n\n'
               b'[mcp_servers.other.env]\nTOKEN = "keep"\n')
        self.cfg.write_bytes(raw)
        other_before = self.get("other")
        self.tb("install", "--agent", "codex", "--mode", "link")
        self.assertEqual(self.get("other"), other_before, "다른 서버는 그대로")
        t = self.get("cera")["transport"]
        self.assertEqual([t["command"]] + t["args"], self.argv())
        baks = list(self.cfg.parent.glob("config.toml.bak-*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_bytes(), raw)
        p = self.tb("install", "--agent", "codex", "--mode", "link")   # running again leaves it as is
        self.assertIn("이미 그대로", p.stdout)

    def argv(self):
        return [sys.executable, str(HERE / "bin/cera"), "mcp"]


class PluginMode(Base):
    def test_codex_plugin_and_conflict_with_link(self):
        """With the fake codex: marketplace add -> plugin add; refused if link mode is already installed."""
        self.fake("codex")
        self.tb("install", "--agent", "codex")
        self.assertEqual(self.calls(), [["codex", "plugin", "marketplace", "add", "Joonoh991119/cera"],
                                        ["codex", "plugin", "add", "cera@cera"]])
        # refused if link mode already exists (nothing is called)
        self.log.unlink()
        self.tb("install", "--agent", "codex", "--mode", "link")
        self.log.unlink()
        p = self.tb("install", "--agent", "codex", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("uninstall --agent codex --mode link", p.stderr)
        self.assertEqual(self.calls(), [])

    def test_link_refused_when_plugin_installed_and_uninstall_plugin(self):
        self.fake("claude", "codex")
        reg = self.home / ".claude/plugins/installed_plugins.json"
        reg.parent.mkdir(parents=True)
        reg.write_text(json.dumps({"version": 2, "plugins": {"cera@cera": [{"scope": "user"}]}}))
        p = self.tb("install", "--agent", "claude", "--mode", "link", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.home / ".claude/skills/cera").exists())
        p = self.tb("install", "--agent", "claude")
        self.assertIn("이미 깔려", p.stdout)
        self.assertEqual(self.calls(), [])
        self.tb("uninstall", "--agent", "claude")
        self.assertEqual(self.calls(), [["claude", "plugin", "uninstall", "cera@cera"]])
        # codex: a cache folder counts as installed
        (self.home / ".codex/plugins/cache/cera/cera/0.3.0").mkdir(parents=True)
        self.log.unlink()
        self.tb("uninstall", "--agent", "codex", "--mode", "plugin")
        self.assertEqual(self.calls(), [["codex", "plugin", "remove", "cera@cera"]])

    def test_local_source_and_known_marketplace(self):
        self.fake("claude")
        known = self.home / ".claude/plugins/known_marketplaces.json"
        known.parent.mkdir(parents=True)
        known.write_text(json.dumps({"cera": {}}))
        self.tb("install", "--agent", "claude", "--source", str(self.t))
        self.assertEqual(self.calls(), [["claude", "plugin", "install", "cera@cera"]], "마켓플레이스가 있으면 add 를 건너뛴다")

    def test_antigravity_without_agy_only_advises(self):
        p = self.tb("install", "--agent", "antigravity")
        self.assertIn("agy plugin install https://github.com/Joonoh991119/cera", p.stdout)
        self.assertEqual(self.files(), [])

    def test_all_targets_installed_tools_only(self):
        self.fake("codex")
        p = self.tb("install", "--agent", "all", "--dry-run")
        self.assertIn("[Codex] plugin 모드", p.stdout)
        self.assertIn("[Claude Code] claude 명령이 없어 건너뜀", p.stdout, "U10 ④ 건너뛴 도구를 한 줄씩")
        self.assertIn("[Antigravity] agy 가 없어 건너뜀", p.stdout)
        self.assertEqual(self.calls(), [])
        p = self.tb("install", "--agent", "all", "--mode", "link", "--dry-run")
        self.assertIn("[Antigravity] agy 도 ~/.gemini 도 없어 건너뜀", p.stdout)
        (self.home / ".gemini").mkdir()
        p = self.tb("install", "--agent", "all", "--mode", "link", "--dry-run")
        self.assertIn("[Antigravity] link 모드", p.stdout, "link 모드는 ~/.gemini 가 있으면 파일만 써 둔다")
        self.assertEqual(self.files(), [".gemini"])

    def test_link_all_output_tidy(self):
        """The ~/.local/bin link line appears only once, and the PATH guidance only once at the end (outside the tool blocks)."""
        self.fake("codex", "claude")
        (self.home / ".gemini").mkdir()
        for dry in (True, False):
            p = self.tb("install", "--agent", "all", "--mode", "link", *(["--dry-run"] if dry else []))
            lines = p.stdout.splitlines()
            self.assertEqual(sum(".local/bin/cera" in l for l in lines), 1, p.stdout)
            path_lines = [i for i, l in enumerate(lines) if "PATH 에 없다" in l]
            self.assertEqual(len(path_lines), 1, p.stdout)
            self.assertTrue(lines[path_lines[0]].startswith("※ "))
            self.assertEqual(lines[path_lines[0] - 1], "", "도구 블록과 떨어져 있다")
            self.assertGreater(path_lines[0], max(i for i, l in enumerate(lines) if l.startswith("[")))


class PrintConfig(Base):
    def test_snippets_parse(self):
        """stdout can be parsed as-is by json.loads / tomllib.loads (explanations go to stderr)."""
        argv = [str(HERE / "bin/cera"), "mcp"]
        for agent in ("claude", "antigravity"):
            p = self.tb("install", "--print-config", agent)
            self.assertEqual(json.loads(p.stdout)["mcpServers"]["cera"], {"command": sys.executable, "args": argv})
            self.assertIn("mcp_config.json" if agent == "antigravity" else "claude.json", p.stderr)
        p = self.tb("install", "--print-config", "codex")
        self.assertIn("codex mcp add", p.stderr)
        self.assertFalse(any(l.lstrip().startswith("#") for l in p.stdout.splitlines()), "주석은 stdout 에 없다")
        out = p.stdout
        self.assertIn("[mcp_servers.cera]", out)
        try:
            import tomllib
        except ImportError:   # 3.9 · 3.10
            self.assertIn(f"command = {json.dumps(sys.executable)}", out)
        else:
            d = tomllib.loads(out)
            self.assertEqual(d["mcp_servers"]["cera"]["args"], argv)
        self.assertEqual(self.files(), [], "아무것도 쓰지 않는다")

    def test_without_value_or_with_other_flags(self):
        """With no value, it shows the choices and exits 2; if --agent and --mode are both given, it says on stderr that they are ignored."""
        for args in (["--print-config"], ["--agent", "all", "--mode", "link", "--print-config"], ["--print-config", "vscode"]):
            p = self.tb("install", *args, check=False)
            self.assertEqual(p.returncode, 2, args)
            self.assertEqual(p.stdout, "")
            for name in ("claude", "codex", "antigravity"):
                self.assertIn(f"--print-config {name}", p.stderr)
        self.fake("codex")
        p = self.tb("install", "--agent", "all", "--mode", "link", "--print-config", "claude")
        self.assertIn("--agent · --mode 는 무시했다", p.stderr)
        json.loads(p.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.files(), [])


class Launcher(Base):
    def test_launcher_follows_root_then_finds_newest_install(self):
        sys.path.insert(0, str(HERE / "server"))
        try:
            import install_agents as ia
        finally:
            sys.path.pop(0)
        tbh = self.t / "tb"
        path = ia.write_launcher(tbh, HERE)
        self.assertEqual(path, tbh / "bin" / "cera")
        self.assertTrue(os.access(path, os.X_OK))
        r = subprocess.run([sys.executable, str(path), "--help"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("site", r.stdout)
        mtime = path.stat().st_mtime_ns
        time.sleep(0.01)
        ia.write_launcher(tbh, HERE)
        self.assertEqual(path.stat().st_mtime_ns, mtime, "같으면 다시 쓰지 않는다")
        # after the recorded folder is gone (plugin update) -- the highest version in the cache
        for ver in ("0.3.0", "0.10.0", "0.9.9"):
            root = self.home / ".claude/plugins/cache/cera/cera" / ver
            (root / "bin").mkdir(parents=True)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin/plugin.json").write_text(json.dumps({"version": ver}))
            (root / "bin/cera").write_text(f"import sys; print('FAKE {ver}', sys.argv[1:])")
        # if the recorded ROOT is inside the plugin cache, even if it still exists, use the higher version in the same cache
        ia.write_launcher(tbh, self.home / ".claude/plugins/cache/cera/cera/0.3.0")
        r = subprocess.run([sys.executable, str(path), "x"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "FAKE 0.10.0 ['x']", r.stderr)
        gone = self.t / "gone"
        ia.write_launcher(tbh, gone)
        r = subprocess.run([sys.executable, str(path), "site", "run", "--id", "personal"], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "FAKE 0.10.0 ['site', 'run', '--id', 'personal']", r.stderr)
        # if ~/.local/bin/cera points at the launcher itself, it is dropped from the candidates (so it never execs itself forever)
        empty = self.t / "empty"
        (empty / ".local/bin").mkdir(parents=True)
        (empty / ".local/bin/cera").symlink_to(path)
        env = {**self.env, "HOME": str(empty)}
        r = subprocess.run([sys.executable, str(path), "--help"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("site install --load", r.stderr)


if __name__ == "__main__":
    unittest.main()
