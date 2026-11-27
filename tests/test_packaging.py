# SPDX-License-Identifier: MIT
"""Packaging tests: the manifest generator (packaging/gen.py), the manifests for the three tools, the MCP files, the shared SKILL.md, license notices,
the MCP command handshake, and fetching dist into $TEMPBOARD_HOME/dist/<version>/ from a read-only plugin root.

Only temporary folders are used (TEMPBOARD_HOME, a copied repo). No real tunnel and no real Release download (a fake tarball server)."""
from __future__ import annotations

import http.server
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

# Korean assertions below: pin the language before anything spawns the CLI or imports server code.
os.environ["TEMPBOARD_LANG"] = "ko"

HERE = Path(__file__).resolve().parents[1]
GEN = HERE / "packaging" / "gen.py"
CLI = HERE / "bin" / "tempboard"
GENERATED = ["plugin.json", "mcp.json", ".antigravity-plugin/plugin.json", "mcp_config.json", ".claude-plugin/marketplace.json"]
# Files that do not yet carry an SPDX line. Once a file gets one, remove it from this set (the test below enforces this).
SPDX_PENDING = set()


def gen(*args, root=None):
    argv = [sys.executable, str(GEN), *args] + (["--root", str(root)] if root else [])
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def load(rel):
    return json.loads((HERE / rel).read_text(encoding="utf-8"))


class Generator(unittest.TestCase):
    def test_check_passes_on_repo(self):
        p = gen("--check")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_version_drift_is_caught_then_fixed(self):
        """Changing the source version makes --check exit 1 (diff); running gen brings it back to 0 and every generated file has that version."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel in [".claude-plugin/plugin.json"] + GENERATED:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HERE / rel, root / rel)
            self.assertEqual(gen("--check", root=root).returncode, 0)
            src = json.loads((root / ".claude-plugin/plugin.json").read_text())
            src["version"] = "9.8.7"
            (root / ".claude-plugin/plugin.json").write_text(json.dumps(src, indent=2))
            p = gen("--check", root=root)
            self.assertEqual(p.returncode, 1)
            self.assertIn("9.8.7", p.stdout)
            self.assertIn("packaging/gen.py", p.stderr)
            self.assertEqual(gen(root=root).returncode, 0)
            self.assertEqual(gen("--check", root=root).returncode, 0)
            for rel in ("plugin.json", ".antigravity-plugin/plugin.json"):
                self.assertEqual(json.loads((root / rel).read_text())["version"], "9.8.7", rel)
            mp = json.loads((root / ".claude-plugin/marketplace.json").read_text())
            self.assertEqual(mp["plugins"][0]["version"], "9.8.7")
            # a variable in command is rejected
            src["mcpServers"]["tempboard"]["command"] = "${CLAUDE_PLUGIN_ROOT}/x"
            (root / ".claude-plugin/plugin.json").write_text(json.dumps(src))
            self.assertNotEqual(gen("--check", root=root).returncode, 0)

    def test_web_versions_and_license_are_checked(self):
        """--check returns 1 if web/package.json or package-lock.json (version, packages[""].version, license) differs from the source."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel in [".claude-plugin/plugin.json", "web/package.json", "web/package-lock.json"] + GENERATED:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HERE / rel, root / rel)
            self.assertEqual(gen("--check", root=root).returncode, 0)
            src = json.loads((root / ".claude-plugin/plugin.json").read_text())
            src["version"] = "99.0.0"
            (root / ".claude-plugin/plugin.json").write_text(json.dumps(src, indent=2))
            self.assertEqual(gen(root=root).returncode, 0, "쓰기 모드는 web 판을 쓰지 않고 알리기만")
            p = gen("--check", root=root)
            self.assertEqual(p.returncode, 1)
            for where in ("web/package.json version", "web/package-lock.json version", 'packages[""].version'):
                self.assertIn(where, p.stderr)
            for rel in ("web/package.json", "web/package-lock.json"):   # bumped by hand (the lock has two places at the root)
                d = json.loads((root / rel).read_text())
                d["version"] = "99.0.0"
                if rel.endswith("lock.json"):
                    d["packages"][""]["version"] = "99.0.0"
                (root / rel).write_text(json.dumps(d, indent=2) + "\n")
            self.assertEqual(gen("--check", root=root).returncode, 0)
            lk = json.loads((root / "web/package-lock.json").read_text())
            del lk["packages"][""]["license"]
            (root / "web/package-lock.json").write_text(json.dumps(lk, indent=2) + "\n")
            p = gen("--check", root=root)
            self.assertEqual(p.returncode, 1)
            self.assertIn('packages[""].license', p.stderr)

    def test_output_is_stable_json_with_newline(self):
        for rel in GENERATED:
            txt = (HERE / rel).read_text(encoding="utf-8")
            self.assertTrue(txt.endswith("}\n"), rel)
            self.assertEqual(txt, json.dumps(json.loads(txt), indent=2, ensure_ascii=False) + "\n", rel)


class Manifests(unittest.TestCase):
    def test_versions_names_licenses_agree(self):
        """2 manifests + 4 generated files (+ web/package.json) all have license MIT and the same name and version."""
        src = load(".claude-plugin/plugin.json")
        mp = load(".claude-plugin/marketplace.json")
        entry = next(p for p in mp["plugins"] if p["name"] == "tempboard")
        for name, d in (("claude", src), ("marketplace", entry), ("agent-plugins", load("plugin.json")),
                        ("antigravity", load(".antigravity-plugin/plugin.json"))):
            self.assertEqual(d["license"], "MIT", name)
            self.assertEqual(d["name"], "tempboard", name)
            self.assertEqual(d["version"], src["version"], name)
        self.assertEqual(load("web/package.json")["license"], "MIT")

    def test_mcp_definitions(self):
        """Claude uses ${CLAUDE_PLUGIN_ROOT}, Codex and Antigravity use ${PLUGIN_ROOT}, and the command is a single python3."""
        claude = load(".claude-plugin/plugin.json")["mcpServers"]["tempboard"]
        self.assertEqual(claude, {"command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/bin/tempboard", "mcp"]})
        ap = load("mcp.json")
        self.assertEqual(ap["$schema"], "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json")
        self.assertEqual(ap["mcpServers"]["tempboard"], {"type": "stdio", "command": "python3",
                                                         "args": ["${PLUGIN_ROOT}/bin/tempboard", "mcp"]})
        self.assertEqual(load("plugin.json")["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
        ag = load(".antigravity-plugin/plugin.json")
        self.assertEqual(ag["$schema"], "https://antigravity.google/schemas/v1/plugin.json")
        mc = load("mcp_config.json")["mcpServers"]["tempboard"]
        self.assertEqual(mc["command"], "python3")
        self.assertTrue(mc["args"][0].startswith("${PLUGIN_ROOT}/bin/tempboard"))
        for rel in ("plugin.json", "mcp.json", ".antigravity-plugin/plugin.json", "mcp_config.json"):
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", (HERE / rel).read_text(), rel)

    def test_files_we_must_not_ship(self):
        """The root .mcp.json (read as a project MCP during development), .agents/plugins/marketplace.json (Codex reads it under a renamed name), and no gemini-extension.json."""
        for rel in (".mcp.json", ".agents/plugins/marketplace.json", "gemini-extension.json"):
            self.assertFalse((HERE / rel).exists(), rel)


class SkillAndCommands(unittest.TestCase):
    def test_skill_is_portable(self):
        """skills/ has zero CLAUDE_PLUGIN_ROOT lines, frontmatter has only name and description, and name = folder name."""
        for f in (HERE / "skills").rglob("*"):
            if f.is_file():
                self.assertNotIn("CLAUDE_PLUGIN_ROOT", f.read_text(encoding="utf-8"), str(f))
        for sk in (HERE / "skills").iterdir():
            txt = (sk / "SKILL.md").read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
            self.assertTrue(m, sk)
            keys = [l.split(":", 1)[0] for l in m.group(1).splitlines() if l and not l.startswith(" ")]
            self.assertEqual(keys, ["name", "description"], sk)
            name = re.search(r"^name:\s*(\S+)", m.group(1), re.M).group(1)
            self.assertEqual(name, sk.name)
            desc = re.search(r"^description:\s*(.+)$", m.group(1), re.M).group(1)
            self.assertLessEqual(len(desc), 1024)
            self.assertIn("../../bin/tempboard", txt)

    def test_commands_and_mcp_tools_documented(self):
        names = {p.stem for p in (HERE / "commands").glob("*.md")}
        self.assertTrue({"open", "list", "stop", "export", "import", "site"} <= names, names)
        tools = ["site_list", "site_open", "site_close", "board_list", "board_create", "board_read", "board_add",
                 "board_update", "board_delete", "board_wait", "board_export", "board_import"]
        readme = (HERE / "README.md").read_text(encoding="utf-8")
        skill = (HERE / "skills/tempboard/SKILL.md").read_text(encoding="utf-8")
        for t in tools:
            self.assertIn(t, readme, t)
            self.assertIn(t, skill, t)

    def test_readme_personal_guide_follows_site_guide_order(self):
        """The README's order of human to-dos matches the order of `tempboard site guide` (personal.GUIDE_*)."""
        readme = (HERE / "README.md").read_text(encoding="utf-8")
        steps = ["Add a domain", "cloudflared tunnel login", "site init --hostname", "site run", "site install --load",
                 "One-time PIN", "Self-hosted", "Application Audience (AUD) Tag", "--access-aud", "members.json", "Service Auth"]
        pos = [readme.find(s) for s in steps]
        self.assertNotIn(-1, pos, list(zip(steps, pos)))
        self.assertEqual(pos, sorted(pos), list(zip(steps, pos)))
        with tempfile.TemporaryDirectory() as td:
            out = subprocess.run([sys.executable, str(CLI), "site", "guide"], capture_output=True, text=True, timeout=30,
                                 env={**os.environ, "TEMPBOARD_HOME": td}).stdout
        gpos = [out.find(s) for s in steps]
        self.assertNotIn(-1, gpos, list(zip(steps, gpos)))
        self.assertEqual(gpos, sorted(gpos))


def fake_web(root: Path, fonts=("Excalifont", "Virgil")):
    """A small fake web/ for notices.py: a lock (2 production, 1 dev, 1 platform, 1 @types) + node_modules + an Excalidraw fonts folder."""
    pk = {"": {"name": "w", "version": "1.0.0", "license": "MIT"},
          "node_modules/a": {"version": "1.2.3", "license": "ISC"},
          "node_modules/a/node_modules/b": {"version": "0.1.0", "license": "BSD-3-Clause"},
          "node_modules/devonly": {"version": "9.9.9", "license": "MIT", "dev": True},
          "node_modules/fsx": {"version": "2.0.0", "license": "MIT", "os": ["darwin"], "optional": True},
          "node_modules/@types/t": {"version": "1.0.0", "license": "MIT"},
          "node_modules/@excalidraw/excalidraw": {"version": "0.18.1", "license": "MIT"}}
    (root / "web").mkdir(parents=True)
    (root / "web/package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": pk}))
    for path in pk:
        if path:
            (root / "web" / path).mkdir(parents=True, exist_ok=True)
            (root / "web" / path / "package.json").write_text(json.dumps({"author": "Kim"}))
    (root / "web/node_modules/a/LICENSE").write_text("ISC License\nCopyright (c) A")
    (root / "web/node_modules/a/node_modules/b/LICENSE.md").write_text("BSD 3-Clause\nCopyright (c) B")
    for f in fonts:
        (root / "web/node_modules/@excalidraw/excalidraw/dist/prod/fonts" / f).mkdir(parents=True)


class Notices(unittest.TestCase):
    def notices(self, *args, root):
        return subprocess.run([sys.executable, str(HERE / "packaging/notices.py"), *args, "--root", str(root)],
                              capture_output=True, text=True, timeout=60)

    def test_generated_from_lock_production_only(self):
        """The list comes from the lock's production dependencies (excluding dev, platform binaries, @types), the text from node_modules, plus the font table and --check."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_web(root)
            self.assertEqual(self.notices(root=root).returncode, 0)
            txt = (root / "THIRD_PARTY_NOTICES.md").read_text()
            self.assertIn("| a | 1.2.3 | ISC |", txt)
            self.assertIn("| b | 0.1.0 | BSD-3-Clause |", txt)
            self.assertIn("Copyright (c) B", txt)
            for gone in ("devonly", "fsx", "@types/t"):
                self.assertNotIn(f"| {gone} |", txt)
            self.assertIn("| Excalifont | Excalifont | OFL-1.1 |", txt)
            self.assertIn("No license file ships in this package. package.json declares: MIT; author: Kim", txt)
            self.assertEqual(self.notices("--check", root=root).returncode, 0)
            (root / "web/node_modules/a/LICENSE").write_text("ISC License\nCopyright (c) A2")
            p = self.notices("--check", root=root)
            self.assertEqual(p.returncode, 1)
            self.assertIn("A2", p.stdout)
            # unknown font folder -> failure (add a line to the table)
            (root / "web/node_modules/@excalidraw/excalidraw/dist/prod/fonts/NewFont").mkdir()
            p = self.notices(root=root)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("FONT_LICENSES", p.stderr)

    @unittest.skipUnless((HERE / "web/node_modules").is_dir(), "web/node_modules 없음(npm ci 먼저)")
    def test_repo_notices_current_and_cover_reviewed_packages(self):
        self.assertEqual(self.notices("--check", root=HERE).returncode, 0)
        txt = (HERE / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for name in ("dompurify", "pako", "chevrotain", "mathjax-full", "react", "@excalidraw/excalidraw", "d3-color"):
            self.assertRegex(txt, rf"\n\| {re.escape(name)} \| ", name)
        for fam in ("Xiaolai", "Nunito", "ComicShanns"):
            self.assertIn(f"| {fam} |", txt)


class License(unittest.TestCase):
    def test_license_and_spdx(self):
        """LICENSE has the fractional-indexing CC0 line, and server/*.py, bin/tempboard and packaging/gen.py start with an SPDX header."""
        lic = (HERE / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(lic.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 Joonoh Park and contributors", lic)
        self.assertRegex(lic, r"fractional-indexing.*\n.*CC0-1\.0")
        self.assertIn("server/fracindex.py", lic)
        self.assertIn("THIRD_PARTY_NOTICES.md", lic)
        self.assertIn("OFL", lic)
        missing = set()
        for f in sorted((HERE / "server").glob("*.py")) + [CLI, GEN, HERE / "server" / "install_agents.py"]:
            head = "\n".join(f.read_text(encoding="utf-8").splitlines()[:3])
            if "SPDX-License-Identifier: MIT" not in head:
                missing.add(f.name)
        self.assertEqual(missing, SPDX_PENDING, "SPDX 줄이 빠졌거나, 넣은 파일을 SPDX_PENDING 에서 빼지 않았다")

    def test_ci_runs_discover_and_gen_check(self):
        """CI runs unittest discover and packaging/gen.py --check."""
        ci = (HERE / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn("python3 -m unittest discover -s tests", ci)
        self.assertIn("python3 packaging/gen.py --check", ci)


def mcp_handshake(argv, env, timeout=20):
    """Start the MCP command and send initialize -> tools/list. Returns (initialize result, tool names)."""
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    try:
        out, err = p.communicate("".join(json.dumps(m) + "\n" for m in msgs), timeout=timeout)
    finally:
        if p.poll() is None:
            p.kill()
    res = {}
    for line in out.splitlines():
        d = json.loads(line)
        res[d.get("id")] = d
    return res[1]["result"], [t["name"] for t in res[2]["result"]["tools"]]


class McpCommands(unittest.TestCase):
    def test_manifest_commands_start_mcp(self):
        """The Claude manifest's command (${CLAUDE_PLUGIN_ROOT} -> repo) and the Codex/Antigravity command (${PLUGIN_ROOT} -> repo)
        both answer initialize and tools/list."""
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "TEMPBOARD_HOME": td}
            for rel, var, key in ((".claude-plugin/plugin.json", "${CLAUDE_PLUGIN_ROOT}", None), ("mcp.json", "${PLUGIN_ROOT}", None),
                                  ("mcp_config.json", "${PLUGIN_ROOT}", None)):
                srv = load(rel)["mcpServers"]["tempboard"]
                argv = [shutil.which(srv["command"]) or sys.executable] + [a.replace(var, str(HERE)) for a in srv["args"]]
                init, tools = mcp_handshake(argv, env)
                self.assertEqual(init["serverInfo"]["name"], "tempboard", rel)
                self.assertIn("board_add", tools, rel)
                self.assertIn("site_open", tools, rel)


class FakeRelease(http.server.BaseHTTPRequestHandler):
    hits = 0
    body = b""

    def do_GET(self):   # noqa: N802
        type(self).hits += 1
        if not self.path.endswith("/tempboard-dist.tar.gz") or "/missing/" in self.path:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


def tarball(marker: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in (("dist/index.html", f"<!doctype html><title>{marker}</title>".encode()),
                           ("dist/assets/app.js", b"console.log(1)")):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class ReadOnlyRoot(unittest.TestCase):
    """From a read-only copy of the plugin root (no web/dist) and an empty TEMPBOARD_HOME, start --tunnel none --json works,
    and dist is created in $TEMPBOARD_HOME/dist/<version>/. Downloads come from a fake tarball server (TEMPBOARD_RELEASE_URL)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.root = self.t / "plugin"
        for rel in ("bin", "server", "skills", ".claude-plugin"):
            shutil.copytree(HERE / rel, self.root / rel, ignore=shutil.ignore_patterns("__pycache__"))
        for p in [self.root] + list(self.root.rglob("*")):
            m = p.stat().st_mode
            p.chmod(m & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        self.home = self.t / "tbhome"
        FakeRelease.body = tarball("FROM-RELEASE")
        FakeRelease.hits = 0
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeRelease)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        env = {k: v for k, v in os.environ.items() if k not in ("TEMPBOARD_DIST",) and "proxy" not in k.lower()}
        self.env = {**env, "TEMPBOARD_HOME": str(self.home), "NO_PROXY": "*",
                    "TEMPBOARD_RELEASE_URL": f"http://127.0.0.1:{self.httpd.server_address[1]}/v-test/"}
        self.ver = json.loads((HERE / ".claude-plugin/plugin.json").read_text())["version"]

    def tearDown(self):
        subprocess.run([sys.executable, str(self.root / "bin" / "tempboard"), "stop", "--all"], env=self.env,
                       capture_output=True, timeout=30)
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in [self.root] + list(self.root.rglob("*")):
            p.chmod(p.stat().st_mode | stat.S_IWUSR)
        self.tmp.cleanup()

    def start(self):
        p = subprocess.run([sys.executable, "-B", str(self.root / "bin" / "tempboard"), "start", "--tunnel", "none", "--json"],
                           env=self.env, capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        return json.loads(p.stdout)

    def test_dist_goes_to_tempboard_home(self):
        self.assertFalse(os.access(self.root / "bin", os.W_OK), "사본이 읽기 전용이 아니다")
        j = self.start()
        dist = self.home / "dist" / self.ver
        self.assertTrue((dist / "index.html").is_file())
        self.assertEqual(FakeRelease.hits, 1)
        self.assertFalse((self.root / "web").exists(), "플러그인 루트에 쓰지 않는다")
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(j["links"]["edit"].split("#")[0], timeout=10) as r:
            self.assertIn(b"FROM-RELEASE", r.read())
        self.assertEqual([p.name for p in (self.home / "dist").iterdir()], [self.ver], "임시 폴더가 남지 않는다")
        # the second run does not download
        self.start()
        self.assertEqual(FakeRelease.hits, 1)

    def test_missing_release_asset_says_so(self):
        """A 404 reports that the Release asset for that version is missing, not 'check your network'."""
        env = {**self.env, "TEMPBOARD_RELEASE_URL": self.env["TEMPBOARD_RELEASE_URL"] + "missing/", "PATH": "/usr/bin:/bin"}
        p = subprocess.run([sys.executable, "-B", str(self.root / "bin" / "tempboard"), "start", "--tunnel", "none", "--json"],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(f"v{self.ver} Release 자산", p.stderr)

    def test_no_write_access_and_no_release_gives_clear_error(self):
        FakeRelease.body = b"not a tarball"
        env = {**self.env, "PATH": "/usr/bin:/bin"}   # no npm -> after the download fails, the build cannot run either
        p = subprocess.run([sys.executable, "-B", str(self.root / "bin" / "tempboard"), "start", "--tunnel", "none", "--json"],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "", "실패해도 stdout 은 비어 있다(MCP 가 JSON 으로 읽는다)")
        self.assertIn("화면 파일", p.stderr)
        self.assertFalse((self.home / "dist" / self.ver / "index.html").exists())


class DevCheckout(unittest.TestCase):
    @unittest.skipUnless((HERE / "web" / "dist" / "index.html").is_file(), "web/dist 가 빌드돼 있지 않다")
    def test_built_checkout_keeps_web_dist(self):
        """If web/dist exists in a development checkout, it is used as-is (existing flow; local_board.sh must not break)."""
        code = ("import importlib.machinery,importlib.util,sys;l=importlib.machinery.SourceFileLoader('tbcli',sys.argv[1]);"
                "s=importlib.util.spec_from_loader('tbcli',l);m=importlib.util.module_from_spec(s);l.exec_module(m);print(m.DIST)")
        with tempfile.TemporaryDirectory() as td:
            env = {k: v for k, v in os.environ.items() if k != "TEMPBOARD_DIST"}
            out = subprocess.run([sys.executable, "-c", code, str(CLI)], env={**env, "TEMPBOARD_HOME": td},
                                 capture_output=True, text=True, timeout=30).stdout.strip()
            self.assertEqual(out, str(HERE / "web" / "dist"))
            out = subprocess.run([sys.executable, "-c", code, str(CLI)], env={**env, "TEMPBOARD_HOME": td, "TEMPBOARD_DIST": "/x/y"},
                                 capture_output=True, text=True, timeout=30).stdout.strip()
            self.assertEqual(out, "/x/y")


if __name__ == "__main__":
    unittest.main()
