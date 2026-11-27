# SPDX-License-Identifier: MIT
"""tempboard install / uninstall: attach to and detach from Claude Code, Codex and Antigravity.

    tempboard install --agent claude|codex|antigravity|all [--mode plugin|link] [--source OWNER/REPO|PATH] [--dry-run]
    tempboard uninstall --agent claude|codex|antigravity|all [--mode plugin|link] [--dry-run]
    tempboard install --print-config claude|codex|antigravity     # print only the MCP config snippet to paste (writes nothing)

plugin mode (default): calls each tool's own installer; the tool manages versions and updates.
  claude       claude plugin marketplace add <source> → claude plugin install tempboard@tempboard
  codex        codex plugin marketplace add <source> → codex plugin add tempboard@tempboard
  antigravity  agy plugin install <git URL|path>   (if agy is missing, only print instructions)
link mode (development, using the checkout as is): point at this repository without copying.
  common         ~/.local/bin/tempboard → <repo>/bin/tempboard
  claude       claude mcp add --scope user tempboard -- <python> <repo>/bin/tempboard mcp  + ~/.claude/skills/tempboard link
  codex        codex mcp add tempboard -- <python> <repo>/bin/tempboard mcp               + ~/.agents/skills/tempboard link
  antigravity  ~/.gemini/config/mcp_config.json: merge mcpServers.tempboard (backup .bak-<timestamp>) + ~/.gemini/config/skills/tempboard link
Rules: running it repeatedly gives the same result (idempotent). User config files are never edited by hand; MCP registration for Claude and Codex goes only through each tool's CLI.
  If a tempboard entry already exists and differs, **nothing is changed**: the differences and the command to change it are shown and the script exits with 3 (to protect a remote token in env).
  Only Antigravity's mcp_config.json is merged with the json module (backed up before changing; a symlink is written through to the file it points to; re-read after writing to verify).
Exit codes: 0 = done, 1 = failed, 2 = bad arguments, 3 = an existing tempboard MCP entry differs and was left alone (the command to run is printed).
Do not install plugin and link modes for the same tool (the skill and MCP would show up twice). `all` targets only the tools that are installed.
Paths: CLAUDE_CONFIG_DIR (default ~/.claude), CODEX_HOME (default ~/.codex) · ~/.gemini/config · ~/.agents/skills · ~/.local/bin.

There is also a fixed entry point, $TEMPBOARD_HOME/bin/tempboard, used by always-on mode (site install); see write_launcher.
Standard library only, Python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import importlib.util
from pathlib import Path
from typing import List, Optional


def T(ko, en):
    """Korean / English text (language per server/i18n.py). Loaded lazily by path so this module needs no package layout."""
    key = "tempboard_i18n"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().with_name("i18n.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key].T(ko, en)


AGENTS = ("claude", "codex", "antigravity")
EXE = {"claude": "claude", "codex": "codex", "antigravity": "agy"}
LABEL = {"claude": "Claude Code", "codex": "Codex", "antigravity": "Antigravity"}
NAME = "tempboard"                       # name of the plugin, marketplace, MCP server and skill
SELECTOR = f"{NAME}@{NAME}"
DEFAULT_SOURCE = "Joonoh991119/tempboard"
OWNER_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(@[^\s]+)?$")


# ------------------------------------------------------------------ paths
class Paths:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.home = Path.home()
        self.claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or self.home / ".claude")
        self.claude_json = (self.claude / ".claude.json") if os.environ.get("CLAUDE_CONFIG_DIR") else self.home / ".claude.json"
        self.codex = Path(os.environ.get("CODEX_HOME") or self.home / ".codex")
        self.gemini = self.home / ".gemini" / "config"
        self.local_bin = self.home / ".local" / "bin"

    @property
    def cli(self) -> Path:
        return self.root / "bin" / NAME

    @property
    def skill(self) -> Path:
        return self.root / "skills" / NAME

    def skill_link(self, agent) -> Path:
        return {"claude": self.claude / "skills", "codex": self.home / ".agents" / "skills",
                "antigravity": self.gemini / "skills"}[agent] / NAME

    @property
    def ag_mcp(self) -> Path:
        return self.gemini / "mcp_config.json"


def python_exe() -> str:
    py = sys.executable
    if not py or not os.path.isabs(py):
        raise SystemExit(T("파이썬 절대경로를 알 수 없다(sys.executable) — 절대경로의 python3 로 다시 실행하세요",
                           "Cannot determine the absolute path of Python (sys.executable) — rerun with an absolute-path python3"))
    return py


def mcp_argv(p: Paths) -> List[str]:
    """Link-mode MCP command: uses the absolute path of the current Python so a GUI app does not pick up a different python3."""
    return [python_exe(), str(p.cli), "mcp"]


# ------------------------------------------------------------------ detect what is installed now (read-only)
def _read_json(path: Path):
    try:
        txt = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not txt.strip():
        return {}
    return json.loads(txt)


def _is_tempboard_skill(path: Path) -> bool:
    try:
        head = (path / "SKILL.md").read_text(encoding="utf-8")[:400]
    except OSError:
        return False
    return re.search(r"^name:\s*tempboard\s*$", head, re.M) is not None


def _is_tempboard_cli(path: Path) -> bool:
    try:
        return "tempboard" in path.resolve().read_text(encoding="utf-8", errors="replace")[:600]
    except OSError:
        return False


def plugin_installed(p: Paths, agent) -> bool:
    if agent == "claude":
        try:
            d = _read_json(p.claude / "plugins" / "installed_plugins.json") or {}
        except ValueError:
            return False
        return any(k.split("@")[0] == NAME for k in (d.get("plugins") or {}))
    if agent == "codex":
        return any(x.is_dir() for x in (p.codex / "plugins" / "cache").glob(f"*/{NAME}"))
    return (p.gemini / "plugins" / NAME).exists()


def mcp_registered(p: Paths, agent) -> bool:
    if agent == "claude":
        try:
            d = _read_json(p.claude_json) or {}
        except ValueError:
            return False
        return NAME in (d.get("mcpServers") or {})
    if agent == "codex":
        if shutil.which("codex"):   # ask codex itself, so any TOML shape works (tables, inline tables, dotted keys, quoted headers)
            return codex_existing(p, None).state in ("same", "differs")
        try:
            txt = (p.codex / "config.toml").read_text(encoding="utf-8")
        except OSError:
            return False
        return re.search(rf"""^\s*(\[\s*mcp_servers\s*\.\s*["']?{NAME}["']?\s*\]|mcp_servers\s*\.\s*["']?{NAME}["']?\s*[.=]|{NAME}\s*=\s*\{{)""",
                         txt, re.M) is not None
    try:
        d = _read_json(p.ag_mcp) or {}
    except ValueError:
        return False
    return isinstance(d, dict) and NAME in (d.get("mcpServers") or {})


def our_link(link: Path, target_check) -> bool:
    return link.is_symlink() and target_check(link)


def link_installed(p: Paths, agent) -> bool:
    """Treat it as link mode if our skill link exists, or if the tempboard MCP is registered without the plugin (links that are not ours do not count)."""
    return our_link(p.skill_link(agent), _is_tempboard_skill) or (mcp_registered(p, agent) and not plugin_installed(p, agent))


def foreign_at(link: Path, target: Path, is_ours) -> bool:
    """Whether a file, folder or link that is not ours sits at a link location (if so, the install does not start)."""
    if link.is_symlink() and os.path.realpath(link) == os.path.realpath(target):
        return False
    return (link.exists() or link.is_symlink()) and not our_link(link, is_ours)


# ------------------------------------------------------------------ existing MCP entries (read-only; user config files are never edited by hand)
class Existing:
    """state: absent, same, differs (exists and is different), or error (could not read). cur = (command, args) or (None, None).
    extras = names of fields that would be lost on change (for env, key names only; values are never stored). transport = its name if it is not stdio."""
    def __init__(self, state, cur=(None, None), extras=(), why="", transport="stdio"):
        self.state, self.cur, self.extras, self.why, self.transport = state, cur, list(extras), why, transport


def _same(cur, argv) -> bool:
    return cur[0] == argv[0] and list(cur[1] or []) == list(argv[1:])


def claude_existing(p: Paths, argv) -> Existing:
    """The tempboard entry in ~/.claude.json (user scope). Read-only, via the json module."""
    try:
        d = _read_json(p.claude_json)
    except ValueError:
        return Existing("error", why=T(f"{p.claude_json} 가 올바른 JSON 이 아니다", f"{p.claude_json} is not valid JSON"))
    e = ((d if isinstance(d, dict) else {}).get("mcpServers") or {}).get(NAME)
    if e is None:
        return Existing("absent")
    if not isinstance(e, dict):
        return Existing("error", why=T(f"{p.claude_json} 의 mcpServers.tempboard 가 객체가 아니다", f"mcpServers.tempboard in {p.claude_json} is not an object"))
    transport = e.get("type") or ("http" if e.get("url") else "stdio")
    cur = (e.get("command"), list(e.get("args") or [])) if transport == "stdio" else (None, None)
    extras = []
    for k, v in e.items():
        if k in ("type", "command", "args") or v in (None, {}, [], ""):
            continue
        extras.append(f"env({', '.join(sorted(v))})" if k == "env" and isinstance(v, dict) else k)
    if transport == "stdio" and argv is not None and _same(cur, argv):
        return Existing("same", cur, extras)
    return Existing("differs", cur, extras, transport=transport)


def codex_existing(p: Paths, argv) -> Existing:
    """Ask with `codex mcp get tempboard --json` (whatever the TOML shape, we get what codex read). The output contains env values, so it is never printed."""
    if not p.codex.exists():
        return Existing("absent")   # codex reads nothing if the CODEX_HOME folder is missing, so there is no entry either
    try:
        r = subprocess.run(["codex", "mcp", "get", NAME, "--json"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Existing("error", why=T(f"codex mcp get 을 부르지 못했다({type(e).__name__})", f"Could not run codex mcp get ({type(e).__name__})"))
    if r.returncode != 0:
        if f"No MCP server named '{NAME}'" in (r.stderr + r.stdout):
            return Existing("absent")
        return Existing("error", why=T(f"codex mcp get 이 실패했다(exit {r.returncode}) — codex 가 {p.codex / 'config.toml'} 를 "
                                       "읽지 못하는 것 같다. `codex mcp list` 로 확인한다",
                                       f"codex mcp get failed (exit {r.returncode}) — codex seems unable to read {p.codex / 'config.toml'}. "
                                       "Check with `codex mcp list`"))
    try:
        d = json.loads(r.stdout)
        t = d["transport"]
    except (ValueError, KeyError, TypeError):
        return Existing("error", why=T("codex mcp get --json 의 출력을 읽지 못했다", "Could not read the output of codex mcp get --json"))
    transport = t.get("type") or "stdio"
    cur = (t.get("command"), list(t.get("args") or [])) if transport == "stdio" else (None, None)
    extras = []
    if isinstance(t.get("env"), dict) and t["env"]:
        extras.append(f"env({', '.join(sorted(t['env']))})")
    for k in ("env_vars", "cwd"):
        if t.get(k):
            extras.append(k)
    for k in ("enabled_tools", "disabled_tools", "startup_timeout_sec", "tool_timeout_sec"):
        if d.get(k) not in (None, [], {}):
            extras.append(k)
    if d.get("enabled") is False:
        extras.append("enabled = false")
    if transport == "stdio" and argv is not None and _same(cur, argv):
        return Existing("same", cur, extras)
    return Existing("differs", cur, extras, transport=transport)


def conflict_text(agent, where: str, ex: Existing, argv) -> str:
    """Text shown when an existing entry differs: the differences (values only for command and args), the names of fields that would be lost, and the one-liner to change it."""
    q = lambda xs: " ".join(_q(str(x)) for x in xs)
    lines = [T(f"  그대로 둠: {LABEL[agent]} 에 tempboard MCP 항목이 이미 있고 다르다 — 아무것도 바꾸지 않았다({where})",
               f"  Left as is: {LABEL[agent]} already has a different tempboard MCP entry — nothing was changed ({where})")]
    if ex.transport != "stdio":
        lines.append(T(f"    방식     지금 {ex.transport}  →  새로 stdio", f"    type     now {ex.transport}  →  new stdio"))
    else:
        if ex.cur[0] != argv[0]:
            lines += [T(f"    command  지금 {ex.cur[0]}", f"    command  now {ex.cur[0]}"),
                      T(f"             새로 {argv[0]}", f"             new {argv[0]}")]
        if list(ex.cur[1] or []) != argv[1:]:
            lines += [T(f"    args     지금 {json.dumps(ex.cur[1], ensure_ascii=False)}", f"    args     now {json.dumps(ex.cur[1], ensure_ascii=False)}"),
                      T(f"             새로 {json.dumps(argv[1:], ensure_ascii=False)}", f"             new {json.dumps(argv[1:], ensure_ascii=False)}")]
    rm = " ".join(_mcp_remove(agent))
    add = q(_mcp_add(agent, argv))
    lines.append(T(f"  바꾸려면(터미널에서 한 줄):\n    {rm} && {add}", f"  To change it (one line in a terminal):\n    {rm} && {add}"))
    if ex.extras:
        lines.append(T(f"  주의: 이 한 줄은 지금 항목의 {' · '.join(ex.extras)} 도 지운다. 지키려면 {where} 에서 command · args 만 손으로 고친다"
                       f"(조각: tempboard install --print-config {agent}).",
                       f"  Note: this line also deletes {' · '.join(ex.extras)} of the current entry. To keep them, edit only command / args by hand in {where} "
                       f"(snippet: tempboard install --print-config {agent})."))
    lines.append(T("  (늘 켜 두기 · 플러그인 업데이트에는 이 일이 필요 없다 — 체크아웃 · 파이썬을 바꿨을 때만 생긴다)",
                   "  (Always-on mode and plugin updates do not need this — it only comes up when you change the checkout or Python)"))
    return "\n".join(lines)


def foreign_at_all(p: Paths, agent) -> List[Path]:
    out = []
    for link, target, chk in ((p.local_bin / NAME, p.cli, _is_tempboard_cli), (p.skill_link(agent), p.skill, _is_tempboard_skill)):
        if foreign_at(link, target, chk):
            out.append(link)
    return out


def marketplace_known(p: Paths, agent) -> bool:
    if agent == "claude":
        try:
            d = _read_json(p.claude / "plugins" / "known_marketplaces.json") or {}
        except ValueError:
            return False
        return NAME in d
    if agent == "codex":
        try:
            txt = (p.codex / "config.toml").read_text(encoding="utf-8")
        except OSError:
            return False
        return re.search(rf'^\[marketplaces\.(?:{NAME}|"{NAME}")\]\s*$', txt, re.M) is not None
    return False


# ------------------------------------------------------------------ actions (plan, then show or execute)
class Plan:
    def __init__(self, dry_run: bool):
        self.dry = dry_run
        self.failed = False
        self.conflict = False         # an existing entry differs and was left alone -> exit 3
        self.local_bin_done = False   # ~/.local/bin/tempboard is shared by several tools, so handle it only once

    def say(self, msg):
        print(msg, flush=True)

    def note(self, msg):
        print("  ※ " + msg, flush=True)

    def run(self, argv: List[str], ok_codes=(0,), quiet_fail=False) -> bool:
        self.say("  $ " + " ".join(_q(x) for x in argv))
        if self.dry:
            return True
        try:
            r = subprocess.run(argv, stdin=subprocess.DEVNULL)
        except OSError as e:
            print(T(f"  실패: {e}", f"  Failed: {e}"), file=sys.stderr)
            self.failed = True
            return False
        if r.returncode not in ok_codes:
            if not quiet_fail:
                print(T(f"  실패(exit {r.returncode}): {' '.join(argv)}", f"  Failed (exit {r.returncode}): {' '.join(argv)}"), file=sys.stderr)
                self.failed = True
            return False
        return True

    def symlink(self, link: Path, target: Path, is_ours) -> bool:
        """link -> target. Unchanged if already the same. Replaced if it is a different link of ours (is_ours). Left alone if it is someone else's file or folder."""
        if link.is_symlink() and os.path.realpath(link) == os.path.realpath(target):
            self.say(T(f"  = {link} → {target} (이미 있음)", f"  = {link} → {target} (already there)"))
            return True
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or not is_ours(link):
                print(T(f"  건너뜀: {link} 에 다른 것이 있다 — 직접 옮긴 뒤 다시", f"  Skipped: {link} holds something else — move it yourself and retry"), file=sys.stderr)
                self.failed = True
                return False
            self.say(T(f"  ~ {link} → {target} (바꿈)", f"  ~ {link} → {target} (replaced)"))
            if not self.dry:
                link.unlink()
        else:
            self.say(f"  + {link} → {target}")
        if not self.dry:
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link)
        return True

    def unlink(self, link: Path, is_ours) -> None:
        if not link.is_symlink():
            if link.exists():
                self.say(T(f"  건너뜀: {link} 는 링크가 아니다(손대지 않음)", f"  Skipped: {link} is not a link (left alone)"))
            return
        if not is_ours(link):
            self.say(T(f"  건너뜀: {link} 는 tempboard 를 가리키지 않는다", f"  Skipped: {link} does not point to tempboard"))
            return
        self.say(f"  - {link}")
        if not self.dry:
            link.unlink()

    def backup(self, path: Path) -> Optional[Path]:
        """Back up before changing (for a symlink, the contents of the file it points to). Reports the path written."""
        if self.dry or not (path.exists() or path.is_symlink()) or not os.path.exists(os.path.realpath(path)):
            return None
        bak = backup_path(path)
        shutil.copy2(os.path.realpath(path), bak)
        self.say(T(f"    백업 {bak}", f"    backup {bak}"))
        return bak

    def json_edit(self, path: Path, fn, what: str) -> None:
        """Edit a JSON file with fn(dict). Left untouched if it cannot be read or the top level is not an object. Only when something changes: backup ->
        tmp file (O_EXCL) next to the real file the link points to, with the same permissions -> atomic replace (a symlink stays a symlink) -> re-read to verify (restore the backup if wrong)."""
        state, cur = read_json_obj(path)
        if state == "bad":
            print(T(f"  건너뜀: {path} 를 고칠 수 없다({cur}) — 손대지 않았다", f"  Skipped: cannot edit {path} ({cur}) — left untouched"), file=sys.stderr)
            self.failed = True
            return
        new = fn(json.loads(json.dumps(cur)) if isinstance(cur, dict) else {})
        if state == "ok" and json.dumps(new, sort_keys=True) == json.dumps(cur, sort_keys=True):
            self.say(T(f"  = {path} ({what}, 이미 그대로)", f"  = {path} ({what}, already as wanted)"))
            return
        self.say(f"  {'~' if state == 'ok' else '+'} {path} ({what})")
        if self.dry:
            return
        bak = self.backup(path) if state == "ok" else None
        real = Path(os.path.realpath(path))
        real.parent.mkdir(parents=True, exist_ok=True)
        mode = (real.stat().st_mode & 0o777) if real.exists() else 0o600
        data = (json.dumps(new, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        tmp = real.with_name(f".{real.name}.tmp-{os.getpid()}")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)   # with those permissions from the start (so a token is not exposed through the umask)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, real)
        finally:
            if tmp.exists():
                tmp.unlink()
        try:
            ok = json.loads(real.read_text(encoding="utf-8")) == new
        except (OSError, ValueError):
            ok = False
        if not ok:
            if bak:
                shutil.copyfile(bak, real)
            print(T(f"  실패: {path} 를 쓴 뒤 다시 읽으니 맞지 않아 되돌렸다" + (f"(백업 {bak})" if bak else ""),
                    f"  Failed: {path} did not read back correctly after writing, so it was restored" + (f" (backup {bak})" if bak else "")), file=sys.stderr)
            self.failed = True


def read_json_obj(path: Path):
    """('missing', None), ('ok', dict) or ('bad', reason). An empty file counts as missing. Bad if the top level is not an object."""
    if not (path.exists() or path.is_symlink()) or not os.path.exists(os.path.realpath(path)):
        return "missing", None
    try:
        txt = Path(os.path.realpath(path)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return "bad", T(f"읽지 못함: {type(e).__name__}", f"could not read: {type(e).__name__}")
    if not txt.strip():
        return "missing", None
    try:
        d = json.loads(txt)
    except ValueError as e:
        return "bad", T(f"올바른 JSON 이 아니다: {e}", f"not valid JSON: {e}")
    if not isinstance(d, dict):
        return "bad", T("최상위가 객체({})가 아니다", "the top level is not an object ({})")
    if "mcpServers" in d and not isinstance(d["mcpServers"], dict):
        return "bad", T("mcpServers 가 객체가 아니다", "mcpServers is not an object")
    return "ok", d


def _q(s: str) -> str:
    return s if re.fullmatch(r"[A-Za-z0-9_@%+=:,./${}-]+", s) else "'" + s.replace("'", "'\\''") + "'"


def backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while bak.exists():
        n += 1
        bak = path.with_name(f"{path.name}.bak-{stamp}-{n}")
    return bak


# ------------------------------------------------------------------ install
def install_link(p: Paths, agent, plan: Plan) -> None:
    if plugin_installed(p, agent):
        print(T(f"  거부: {LABEL[agent]} 에는 이미 플러그인으로 깔려 있다 — 둘 다 깔면 스킬 · MCP 가 두 번 보인다.\n"
                f"  먼저:  tempboard uninstall --agent {agent} --mode plugin",
                f"  Refused: {LABEL[agent]} already has the plugin installed — installing both shows the skill and MCP twice.\n"
                f"  First:  tempboard uninstall --agent {agent} --mode plugin"), file=sys.stderr)
        plan.failed = True
        return
    bad = foreign_at_all(p, agent)
    if bad:   # avoid a half install: stop before doing anything
        for b in bad:
            print(T(f"  거부: {b} 에 tempboard 가 아닌 것이 있다 — 직접 옮긴 뒤 다시", f"  Refused: {b} holds something that is not tempboard — move it yourself and retry"), file=sys.stderr)
        plan.failed = True
        return
    argv = mcp_argv(p)
    exe = shutil.which(EXE[agent]) if agent != "antigravity" else None
    # pre-write check (also before changing anything): can the config be read, and is the existing entry the same
    ex = None
    if agent == "antigravity":
        state, why = read_json_obj(p.ag_mcp)
        if state == "bad":
            print(T(f"  거부: {p.ag_mcp} 를 고칠 수 없다({why}) — 아무것도 바꾸지 않았다. 고친 뒤 다시, 또는 "
                    "tempboard install --print-config antigravity",
                    f"  Refused: cannot edit {p.ag_mcp} ({why}) — nothing was changed. Fix it and retry, or use "
                    "tempboard install --print-config antigravity"), file=sys.stderr)
            plan.failed = True
            return
    elif exe:
        ex = claude_existing(p, argv) if agent == "claude" else codex_existing(p, argv)
        where = str(p.claude_json) if agent == "claude" else str(p.codex / "config.toml")
        if ex.state == "error":
            print(T(f"  거부: {ex.why} — 아무것도 바꾸지 않았다", f"  Refused: {ex.why} — nothing was changed"), file=sys.stderr)
            plan.failed = True
            return
        if ex.state == "differs":
            print(conflict_text(agent, where, ex, argv), file=sys.stderr)
            plan.conflict = True
            return
    if "/plugins/cache/" in str(p.root).replace(os.sep, "/"):
        plan.note(T(f"{p.root} 는 플러그인 캐시(판 번호 폴더)다 — 플러그인을 올리면 이 링크들이 끊긴다. link 모드는 git 체크아웃에서 쓴다",
                    f"{p.root} is a plugin cache (versioned folder) — upgrading the plugin breaks these links. Use link mode from a git checkout"))
    if not plan.local_bin_done:
        plan.symlink(p.local_bin / NAME, p.cli, _is_tempboard_cli)
        plan.local_bin_done = True
    plan.symlink(p.skill_link(agent), p.skill, _is_tempboard_skill)
    if agent == "antigravity":
        def merge(d):
            d.setdefault("mcpServers", {})
            cur = d["mcpServers"].get(NAME)
            entry = dict(cur) if isinstance(cur, dict) else {}   # keep env, disabledTools and unknown fields as they are; change only command and args
            entry["command"], entry["args"] = argv[0], argv[1:]
            d["mcpServers"][NAME] = entry
            return d
        plan.json_edit(p.ag_mcp, merge, T("mcpServers.tempboard 의 command · args", "command / args of mcpServers.tempboard"))
        if not shutil.which("agy"):
            plan.note(T("agy 가 없다 — 파일만 써 두었다. Antigravity 를 설치하면 이 설정을 읽는다(App 은 다시 열기, CLI 는 /mcp 로 다시 읽기)",
                        "agy is missing — only the file was written. Antigravity reads this config once installed (reopen the App; in the CLI re-read with /mcp)"))
        return
    if not exe:
        plan.note(T(f"{EXE[agent]} 명령이 없다 — MCP 등록은 건너뛰었다. 나중에: tempboard install --print-config {agent}",
                    f"The {EXE[agent]} command is missing — MCP registration was skipped. Later: tempboard install --print-config {agent}"))
        return
    if ex.state == "same":
        plan.say(T(f"  = MCP tempboard ({LABEL[agent]}, 이미 그대로)", f"  = MCP tempboard ({LABEL[agent]}, already as wanted)"))
        return
    # Absent -> add it through the tool's CLI (the tool writes the file). Codex gets a config.toml backup first.
    ensure_codex_home(p, agent, plan)
    if agent == "codex":
        plan.backup(p.codex / "config.toml")
    if plan.run(_mcp_add(agent, argv)) and not plan.dry and agent == "codex":
        after = codex_existing(p, argv)
        if after.state != "same":
            print(T("  실패: codex mcp add 뒤 codex mcp get 으로 확인했는데 맞지 않는다 — `codex mcp list` 로 확인한다",
                    "  Failed: after codex mcp add, codex mcp get shows a mismatch — check with `codex mcp list`"), file=sys.stderr)
            plan.failed = True


def ensure_codex_home(p: Paths, agent, plan: Plan) -> None:
    """If the CODEX_HOME folder is missing, codex cannot read its config and exits (observed: 'CODEX_HOME ... does not exist'), so create an empty folder."""
    if agent == "codex" and not p.codex.exists():
        plan.say(T(f"  + {p.codex}/ (codex 설정 폴더)", f"  + {p.codex}/ (codex config folder)"))
        if not plan.dry:
            p.codex.mkdir(parents=True, mode=0o700)


def _mcp_add(agent, argv) -> List[str]:
    if agent == "claude":
        return ["claude", "mcp", "add", "--scope", "user", NAME, "--"] + argv
    return ["codex", "mcp", "add", NAME, "--"] + argv


def _mcp_remove(agent) -> List[str]:
    if agent == "claude":
        return ["claude", "mcp", "remove", "--scope", "user", NAME]
    return ["codex", "mcp", "remove", NAME]


def source_for(agent, source: str) -> str:
    if agent == "antigravity" and OWNER_REPO.match(source) and not os.path.exists(source):
        repo, _, ref = source.partition("@")
        return f"https://github.com/{repo}" + (f"@{ref}" if ref else "")
    if os.path.exists(source):
        return str(Path(source).resolve())
    return source


def install_plugin(p: Paths, agent, plan: Plan, source: str) -> None:
    if link_installed(p, agent):
        print(T(f"  거부: {LABEL[agent]} 에는 이미 링크 모드로 깔려 있다 — 둘 다 깔면 스킬 · MCP 가 두 번 보인다.\n"
                f"  먼저:  tempboard uninstall --agent {agent} --mode link",
                f"  Refused: {LABEL[agent]} is already installed in link mode — installing both shows the skill and MCP twice.\n"
                f"  First:  tempboard uninstall --agent {agent} --mode link"), file=sys.stderr)
        plan.failed = True
        return
    exe = shutil.which(EXE[agent])
    if not exe:
        if agent == "antigravity":
            plan.note(T("agy(Antigravity CLI)가 없다 — 설치한 뒤:  agy plugin install " + source_for(agent, source)
                        + "\n     지금 바로 쓰려면:  tempboard install --agent antigravity --mode link",
                        "agy (Antigravity CLI) is missing — after installing it:  agy plugin install " + source_for(agent, source)
                        + "\n     To use it right now:  tempboard install --agent antigravity --mode link"))
        else:
            print(T(f"  {EXE[agent]} 명령이 없다 — {LABEL[agent]} 를 설치한 뒤 다시(또는 --mode link)",
                    f"  The {EXE[agent]} command is missing — install {LABEL[agent]} and retry (or use --mode link)"), file=sys.stderr)
            plan.failed = True
        return
    if plugin_installed(p, agent):
        up = {"claude": f"claude plugin update {SELECTOR}", "codex": f"codex plugin marketplace upgrade {NAME}  " + T("그리고", "and") + f"  codex plugin add {SELECTOR}",
              "antigravity": f"agy plugin install {source_for(agent, source)}"}[agent]
        plan.say(T(f"  = 이미 깔려 있다 — 올리려면:  {up}", f"  = already installed — to upgrade:  {up}"))
        return
    src = source_for(agent, source)
    ensure_codex_home(p, agent, plan)
    if agent == "antigravity":
        plan.run(["agy", "plugin", "install", src])
        return
    if not marketplace_known(p, agent):
        if not plan.run([agent, "plugin", "marketplace", "add", src]):
            return
    else:
        plan.say(T(f"  = 마켓플레이스 {NAME} 는 이미 있다", f"  = marketplace {NAME} already exists"))
    plan.run(["claude", "plugin", "install", SELECTOR] if agent == "claude" else ["codex", "plugin", "add", SELECTOR])


# ------------------------------------------------------------------ uninstall
def uninstall_link(p: Paths, agent, plan: Plan) -> None:
    plan.unlink(p.skill_link(agent), _is_tempboard_skill)
    if agent == "antigravity":
        if p.ag_mcp.exists():
            def drop(d):
                (d.get("mcpServers") or {}).pop(NAME, None)
                return d
            plan.json_edit(p.ag_mcp, drop, T("mcpServers.tempboard 지움", "mcpServers.tempboard removed"))
        return
    if shutil.which(EXE[agent]):
        ex = claude_existing(p, None) if agent == "claude" else codex_existing(p, None)
        if ex.state == "absent":
            plan.say(T(f"  = MCP tempboard ({LABEL[agent]}) 없음", f"  = no MCP tempboard ({LABEL[agent]})"))
            return
        if ex.extras:   # back up the config file before removing an entry that has env (token) or similar fields
            plan.backup(p.claude_json if agent == "claude" else p.codex / "config.toml")
        plan.run(_mcp_remove(agent), quiet_fail=True)
    elif mcp_registered(p, agent):
        plan.note(T(f"{EXE[agent]} 명령이 없어 MCP 등록을 지우지 못했다 — 설정 파일에서 tempboard 항목을 직접 지운다",
                    f"The {EXE[agent]} command is missing, so the MCP registration was not removed — delete the tempboard entry from the config file yourself"))


def uninstall_plugin(p: Paths, agent, plan: Plan) -> None:
    if not plugin_installed(p, agent):
        plan.say(T(f"  = {LABEL[agent]} 에 플러그인으로 깔려 있지 않다", f"  = {LABEL[agent]} has no plugin installed"))
        return
    if not shutil.which(EXE[agent]):
        print(T(f"  {EXE[agent]} 명령이 없어 지우지 못했다", f"  The {EXE[agent]} command is missing, so it could not be removed"), file=sys.stderr)
        plan.failed = True
        return
    plan.run({"claude": ["claude", "plugin", "uninstall", SELECTOR], "codex": ["codex", "plugin", "remove", SELECTOR],
              "antigravity": ["agy", "plugin", "uninstall", NAME]}[agent])
    if agent != "antigravity":
        plan.note(T(f"마켓플레이스 {NAME} 등록은 남겨 두었다(지우려면  {agent} plugin marketplace remove {NAME})",
                    f"The marketplace {NAME} registration was kept (to remove it:  {agent} plugin marketplace remove {NAME})"))


def drop_local_bin_if_unused(p: Paths, plan: Plan, removed) -> None:
    """~/.local/bin/tempboard is shared by all three link modes; remove it only when no link-mode install other than the one just removed remains."""
    if any(p.skill_link(a).is_symlink() and _is_tempboard_skill(p.skill_link(a)) for a in AGENTS if a not in removed):
        return
    lb = p.local_bin / NAME
    if lb.is_symlink() and os.path.realpath(lb) == os.path.realpath(p.cli):   # keep a link that points to a different checkout
        plan.unlink(lb, _is_tempboard_cli)


# ------------------------------------------------------------------ config snippets
def print_config(p: Paths, agent) -> None:
    """stdout gets only the snippet, readable as saved (JSON or TOML); where to put it and alternatives go to stderr."""
    argv = mcp_argv(p)
    cmdline = " ".join(_q(x) for x in argv)
    note = {"codex": T(f"{p.codex / 'config.toml'} 에 붙여 넣는다(또는:  codex mcp add {NAME} -- {cmdline})",
                       f"Paste it into {p.codex / 'config.toml'} (or:  codex mcp add {NAME} -- {cmdline})"),
            "claude": T(f"{p.claude_json} 의 mcpServers 에 넣는다(또는:  claude mcp add --scope user {NAME} -- {cmdline})",
                        f"Put it in mcpServers of {p.claude_json} (or:  claude mcp add --scope user {NAME} -- {cmdline})"),
            "antigravity": T(f"{p.ag_mcp} 에 넣는다(작업 폴더만이면 .agents/mcp_config.json)",
                             f"Put it in {p.ag_mcp} (for one working folder only: .agents/mcp_config.json)")}[agent]
    print(f"# {note}", file=sys.stderr)
    print(T("# 이미 tempboard 항목이 있으면 그 항목의 env(토큰) 등을 지키도록 command · args 만 바꾼다 — "
            "`tempboard install --agent … --mode link` 가 다른 점과 할 일을 보여 준다",
            "# If a tempboard entry already exists, change only command / args so its env (token) etc. is kept — "
            "`tempboard install --agent … --mode link` shows the differences and what to do"), file=sys.stderr)
    print(T(f"# 파이썬({argv[0]})을 바꾸거나 지우면 이 조각을 다시 만든다", f"# If you change or delete Python ({argv[0]}), regenerate this snippet"), file=sys.stderr)
    if agent == "codex":
        print(f"[mcp_servers.{NAME}]")
        print(f"command = {json.dumps(argv[0], ensure_ascii=False)}")
        print(f"args = [{', '.join(json.dumps(x, ensure_ascii=False) for x in argv[1:])}]")
        print("startup_timeout_sec = 20")
        return
    print(json.dumps({"mcpServers": {NAME: {"command": argv[0], "args": argv[1:]}}}, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ fixed entry point (always-on mode)
LAUNCHER = r'''#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# tempboard fixed launcher — written by `tempboard site install` (do not edit). The always-on service (launchd / systemd) runs this.
# Plugin upgrades move the install folder. If the recorded folder (ROOT) is gone, use the highest version found in the known install locations.
import glob, json, os, sys
ROOT = {root!r}

def korean():
    v = (os.environ.get("TEMPBOARD_LANG") or "").strip().lower()
    if v in ("ko", "en"):
        return v == "ko"
    try:
        with open(os.path.join(os.environ.get("TEMPBOARD_HOME") or os.path.join(os.path.expanduser("~"), ".tempboard"), "config.json"), encoding="utf-8") as fh:
            v = json.load(fh).get("lang")
        if v in ("ko", "en"):
            return v == "ko"
    except Exception:
        pass
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = (os.environ.get(k) or "").strip().lower()
        if v and v not in ("c", "posix") and not v.startswith("c."):
            return v.startswith("ko")
    return False

def ver(root):
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return tuple(int(x) for x in json.load(fh)["version"].split("-")[0].split("."))
    except Exception:
        return (0,)

def found():
    here = os.path.realpath(__file__)
    ok = lambda r: os.path.isfile(os.path.join(r, "bin", "tempboard")) and os.path.realpath(os.path.join(r, "bin", "tempboard")) != here
    in_cache = "/plugins/cache/" in ROOT.replace(os.sep, "/")
    if ok(ROOT) and not in_cache:
        return ROOT   # git checkout (link mode): use as is
    home = os.path.expanduser("~")
    pats = [os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"), "plugins", "cache", "*", "tempboard", "*"),
            os.path.join(os.environ.get("CODEX_HOME") or os.path.join(home, ".codex"), "plugins", "cache", "*", "tempboard", "*"),
            os.path.join(home, ".gemini", "config", "plugins", "tempboard")]
    roots = [r for pat in pats for r in glob.glob(pat) if ok(r)]
    lb = os.path.join(home, ".local", "bin", "tempboard")
    if os.path.exists(lb):
        r = os.path.dirname(os.path.dirname(os.path.realpath(lb)))
        if ok(r):
            roots.append(r)
    if ok(ROOT):
        roots.append(ROOT)   # inside a plugin cache: prefer a newer version in the same cache if there is one
    return max(roots, key=ver) if roots else None

root = found()
if not root:
    if korean():
        sys.stderr.write("tempboard 를 찾지 못했다(" + ROOT + " 가 없다) — 플러그인을 다시 깔고 `tempboard site install --load`\n")
    else:
        sys.stderr.write("tempboard was not found (" + ROOT + " is missing) — reinstall the plugin and run `tempboard site install --load`\n")
    sys.exit(1)
cli = os.path.join(root, "bin", "tempboard")
os.execv(sys.executable, [sys.executable, cli] + sys.argv[1:])
'''


def launcher_path(home) -> Path:
    return Path(home).expanduser().absolute() / "bin" / NAME


def write_launcher(home, root) -> Path:
    path = launcher_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = LAUNCHER.replace("{root!r}", repr(str(Path(root).resolve())))
    if path.is_file() and path.read_text(encoding="utf-8") == body:
        return path
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ CLI
def targets(agent: str, mode: str, p: Paths):
    """(target tools, [(skipped tool, reason)]). `all` covers only installed tools."""
    if agent != "all":
        return [agent], []
    out, skipped = [], []
    for a in ("claude", "codex"):
        if shutil.which(EXE[a]):
            out.append(a)
        else:
            skipped.append((a, T(f"{EXE[a]} 명령이 없어 건너뜀", f"skipped: the {EXE[a]} command is missing")))
    if shutil.which("agy"):
        out.append("antigravity")
    elif mode != "plugin" and (p.home / ".gemini").is_dir():
        out.append("antigravity")   # link mode only writes the config file
    else:
        why = (T("agy 가 없어 건너뜀", "skipped: agy is missing") if mode == "plugin"
               else T("agy 도 ~/.gemini 도 없어 건너뜀", "skipped: neither agy nor ~/.gemini exists"))
        skipped.append(("antigravity", why))
    return out, skipped


def path_note(p: Paths) -> Optional[str]:
    dirs = [os.path.realpath(d) for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    if os.path.realpath(p.local_bin) not in dirs:
        return T(f"{p.local_bin} 가 PATH 에 없다 — 셸 설정(~/.zshrc 등)에  export PATH=\"$HOME/.local/bin:$PATH\"  를 더하면 "
                 "어디서나 `tempboard` 가 된다(MCP 는 절대경로라 상관없다)",
                 f"{p.local_bin} is not on PATH — add  export PATH=\"$HOME/.local/bin:$PATH\"  to your shell config (~/.zshrc etc.) "
                 "to get `tempboard` everywhere (MCP uses an absolute path, so it is unaffected)")
    return None


_PRINT_CONFIG_HELP_KO = """--print-config 에는 도구 하나를 준다: claude · codex · antigravity
  tempboard install --print-config claude        > tempboard-mcp.json   # JSON 조각
  tempboard install --print-config codex         > tempboard-mcp.toml   # TOML 조각
  tempboard install --print-config antigravity   > tempboard-mcp.json
stdout 에는 조각만 나온다(설명은 stderr). 아무것도 쓰거나 부르지 않는다."""
_PRINT_CONFIG_HELP_EN = """--print-config takes one tool: claude · codex · antigravity
  tempboard install --print-config claude        > tempboard-mcp.json   # JSON snippet
  tempboard install --print-config codex         > tempboard-mcp.toml   # TOML snippet
  tempboard install --print-config antigravity   > tempboard-mcp.json
Only the snippet goes to stdout (the explanation goes to stderr). Nothing is written or run."""
PRINT_CONFIG_HELP = _PRINT_CONFIG_HELP_KO   # kept for compatibility; use print_config_help() for the chosen language


def print_config_help() -> str:
    return T(_PRINT_CONFIG_HELP_KO, _PRINT_CONFIG_HELP_EN)


def cmd_install(ctx, a):
    p = Paths(ctx.ROOT)
    if a.print_config is not None:
        if a.print_config not in AGENTS:
            print(print_config_help() if a.print_config == "" else T(f"모르는 도구: {a.print_config!r}\n", f"Unknown tool: {a.print_config!r}\n") + print_config_help(), file=sys.stderr)
            sys.exit(2)
        ignored = [f for f, v in (("--agent", a.agent), ("--mode", a.mode), ("--source", a.source != DEFAULT_SOURCE or None),
                                  ("--dry-run", a.dry_run or None)) if v]
        if ignored:
            print(T(f"# --print-config 는 조각만 보인다 — {' · '.join(ignored)} 는 무시했다",
                    f"# --print-config shows only the snippet — {' · '.join(ignored)} ignored"), file=sys.stderr)
        return print_config(p, a.print_config)
    if not a.agent:
        raise SystemExit(T("--agent claude|codex|antigravity|all (또는 --print-config <도구>)", "--agent claude|codex|antigravity|all (or --print-config <tool>)"))
    mode = a.mode or "plugin"
    plan = Plan(a.dry_run)
    ts, skipped = targets(a.agent, mode, p)
    if a.dry_run:
        print(T("(--dry-run — 아무것도 쓰거나 부르지 않는다)", "(--dry-run — nothing is written or run)"))
    for agent, why in skipped:
        plan.say(f"[{LABEL[agent]}] {why}")
    if not ts:
        raise SystemExit(T("깔린 도구(claude · codex · agy)가 없다 — --agent 로 하나를 고른다", "No installed tool (claude, codex, agy) found — pick one with --agent"))
    for agent in ts:
        plan.say(T(f"[{LABEL[agent]}] {mode} 모드", f"[{LABEL[agent]}] {mode} mode"))
        if mode == "link":
            install_link(p, agent, plan)
        else:
            install_plugin(p, agent, plan, a.source)
    if mode == "link" and not plan.failed:
        n = path_note(p)
        if n:
            plan.say("")
            plan.say(f"※ {n}")
    if not plan.failed and not plan.conflict and not a.dry_run:
        print(T("끝 — 도구를 다시 시작하면 스킬 `tempboard` 와 MCP 도구(site_open · board_add …)가 보인다.",
                "Done — restart the tool and the skill `tempboard` and the MCP tools (site_open, board_add …) will appear."))
    if plan.failed:
        sys.exit(1)
    if plan.conflict:
        sys.exit(3)


def cmd_uninstall(ctx, a):
    p = Paths(ctx.ROOT)
    plan = Plan(a.dry_run)
    ts = list(AGENTS) if a.agent == "all" else [a.agent]
    if a.dry_run:
        print(T("(--dry-run — 아무것도 지우거나 부르지 않는다)", "(--dry-run — nothing is removed or run)"))
    removed = []
    for agent in ts:
        modes = [a.mode] if a.mode else [m for m, f in (("plugin", plugin_installed), ("link", link_installed)) if f(p, agent)]
        if not modes:
            if a.agent != "all":
                plan.say(T(f"[{LABEL[agent]}] 깔려 있지 않다", f"[{LABEL[agent]}] not installed"))
            continue
        for m in modes:
            plan.say(T(f"[{LABEL[agent]}] {m} 모드 지우기", f"[{LABEL[agent]}] removing {m} mode"))
            if m == "link":
                uninstall_link(p, agent, plan)
                removed.append(agent)
            else:
                uninstall_plugin(p, agent, plan)
    if removed:
        drop_local_bin_if_unused(p, plan, removed)
    if plan.failed:
        sys.exit(1)


def register(sub, ctx):
    """Called by the extension hook (EXT_MODULES) in bin/tempboard."""
    s = sub.add_parser("install", help=T("Claude Code · Codex · Antigravity 에 붙이기(plugin · link 모드)", "Attach to Claude Code, Codex and Antigravity (plugin / link mode)"),
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("--agent", choices=list(AGENTS) + ["all"], default=None)
    s.add_argument("--mode", choices=["plugin", "link"], default=None,
                   help=T("plugin(기본) = 각 도구의 설치기 · link = 이 체크아웃을 링크(개발용)", "plugin (default) = each tool's own installer; link = link this checkout (for development)"))
    s.add_argument("--source", default=DEFAULT_SOURCE, help=T(f"plugin 모드의 출처 — OWNER/REPO[@ref] · git 주소 · 로컬 경로(기본 {DEFAULT_SOURCE})",
                                                                      f"Source for plugin mode — OWNER/REPO[@ref], git URL or local path (default {DEFAULT_SOURCE})"))
    s.add_argument("--dry-run", action="store_true", help=T("쓰거나 부를 것만 보인다", "Only show what would be written or run"))
    s.add_argument("--print-config", nargs="?", const="", default=None, metavar="{claude,codex,antigravity}",
                   help=T("붙여 넣을 MCP 설정 조각만 stdout 에(설명은 stderr, 아무것도 쓰지 않는다)", "Print only the MCP config snippet to paste, to stdout (explanation on stderr, nothing is written)"))
    s.set_defaults(f=lambda a: cmd_install(ctx, a))
    u = sub.add_parser("uninstall", help=T("install 을 되돌리기(링크 · MCP 등록 · 플러그인)", "Undo install (links, MCP registration, plugin)"),
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    u.add_argument("--agent", choices=list(AGENTS) + ["all"], required=True)
    u.add_argument("--mode", choices=["plugin", "link"], default=None, help=T("없으면 깔린 쪽을 알아서", "If omitted, whichever is installed"))
    u.add_argument("--dry-run", action="store_true")
    u.set_defaults(f=lambda a: cmd_uninstall(ctx, a))
