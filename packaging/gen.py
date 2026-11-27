#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Manifest generator: builds the manifest and MCP files for three tools from a single source (.claude-plugin/plugin.json).

    python3 packaging/gen.py           # write the files
    python3 packaging/gen.py --check   # on mismatch, print a diff and exit 1 (CI, tests)

Source (edit by hand): .claude-plugin/plugin.json - name (its capitalised form is the Antigravity displayName), version, description, author, license, keywords, mcpServers.
Generated files (do not edit by hand; edit the source, then run this script):
  plugin.json                      Agent Plugins 1.0 manifest - Codex reads it before .claude-plugin
  mcp.json                         Agent Plugins MCP - ${PLUGIN_ROOT} (Codex substitutes it in args)
  .antigravity-plugin/plugin.json  Antigravity manifest - shadows the root plugin.json
  mcp_config.json                  Antigravity plugin MCP - ${PLUGIN_ROOT}
  .claude-plugin/marketplace.json  only plugins[cera].version and license are synced (other fields are left as they are)
Checked only, never written (versions are bumped by hand at integration time): version and license in web/package.json, and in web/package-lock.json
  version, packages[""].version and packages[""].license must equal the source.
  THIRD_PARTY_NOTICES.md must match web/package-lock.json and web/node_modules (packaging/notices.py; only when node_modules exists).
Not generated: the root .mcp.json (Claude would read it as a project MCP config when working on this repository) and
.agents/plugins/marketplace.json (Codex reads it first, which changes the marketplace name; observed).
Standard library only, Python 3.9.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ".claude-plugin/plugin.json"
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
ANTIGRAVITY_SCHEMA = "https://antigravity.google/schemas/v1/plugin.json"
CLAUDE_VAR = "${CLAUDE_PLUGIN_ROOT}"
PORTABLE_VAR = "${PLUGIN_ROOT}"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def dump(obj) -> str:
    """Keys keep insertion order (source order), 2-space indent, ending with a newline."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def load_source(root: Path) -> dict:
    src = json.loads((root / SOURCE).read_text(encoding="utf-8"))
    for k in ("name", "version", "description", "license", "mcpServers"):
        if not src.get(k):
            raise SystemExit(f"{SOURCE}: field '{k}' is empty")
    if not SEMVER.match(src["version"]):
        raise SystemExit(f"{SOURCE}: version '{src['version']}' is not x.y.z")
    for name, srv in src["mcpServers"].items():
        if "$" in str(srv.get("command", "")):
            raise SystemExit(f"{SOURCE}: mcpServers.{name}.command must not contain variables (they are not substituted) - put them in args only")
    return src


def portable_servers(src: dict, with_type: bool) -> dict:
    out = {}
    for name, srv in src["mcpServers"].items():
        s = {"type": "stdio"} if with_type else {}
        s["command"] = srv["command"]
        s["args"] = [str(x).replace(CLAUDE_VAR, PORTABLE_VAR) for x in srv.get("args", [])]
        for k in ("env", "cwd"):
            if srv.get(k):
                s[k] = json.loads(json.dumps(srv[k]).replace(CLAUDE_VAR, PORTABLE_VAR))
        out[name] = s
    return out


def outputs(root: Path = ROOT) -> dict:
    """{relative path: contents} for all generated files."""
    src = load_source(root)
    common = {k: src[k] for k in ("name", "version", "description") if k in src}
    meta = {k: src[k] for k in ("author", "license", "keywords") if k in src}
    out = {}
    out["plugin.json"] = dump({"$schema": AGENT_PLUGINS_SCHEMA, **common, **meta})
    out["mcp.json"] = dump({"$schema": AGENT_MCP_SCHEMA, "mcpServers": portable_servers(src, True)})
    ag = {"$schema": ANTIGRAVITY_SCHEMA, "name": src["name"], "displayName": src["name"][:1].upper() + src["name"][1:], "description": src["description"],
          "version": src["version"]}
    ag.update({k: src[k] for k in ("author", "license") if k in src})
    out[".antigravity-plugin/plugin.json"] = dump(ag)
    out["mcp_config.json"] = dump({"mcpServers": portable_servers(src, False)})
    mp_path = root / ".claude-plugin" / "marketplace.json"
    mp = json.loads(mp_path.read_text(encoding="utf-8"))
    hit = [p for p in mp.get("plugins", []) if p.get("name") == src["name"]]
    if not hit:
        raise SystemExit(f"no plugins[name={src['name']}] entry in .claude-plugin/marketplace.json")
    for p in hit:
        p["version"] = src["version"]
        p["license"] = src["license"]
    out[".claude-plugin/marketplace.json"] = dump(mp)
    return out


def web_mismatches(root: Path = ROOT) -> list:
    """Places where version or license in web/package.json and package-lock.json differ from the source (list of messages). Empty list if web/ is missing."""
    src = load_source(root)
    pj, lk = root / "web" / "package.json", root / "web" / "package-lock.json"
    out = []
    if pj.is_file():
        d = json.loads(pj.read_text(encoding="utf-8"))
        for k in ("version", "license"):
            if d.get(k) != src[k]:
                out.append(f"web/package.json {k}: {d.get(k)!r} ≠ {src[k]!r}")
    if lk.is_file():
        d = json.loads(lk.read_text(encoding="utf-8"))
        r = (d.get("packages") or {}).get("", {})
        for where, have, want in (("version", d.get("version"), src["version"]),
                                  ('packages[""].version', r.get("version"), src["version"]),
                                  ('packages[""].license', r.get("license"), src["license"])):
            if have != want:
                out.append(f"web/package-lock.json {where}: {have!r} ≠ {want!r}")
    return out


def notices_check(root: Path) -> int:
    """0 = same, 1 = different, None = skipped (no web/node_modules)."""
    if not (root / "web" / "node_modules").is_dir() or not (root / "web" / "package-lock.json").is_file():
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import notices
    finally:
        sys.path.pop(0)
    return notices.check(root)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare only, do not write - on a difference print the diff and exit 1")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root)
    bad = 0
    for rel, text in outputs(root).items():
        path = root / rel
        cur = path.read_text(encoding="utf-8") if path.is_file() else None
        if cur == text:
            continue
        if a.check:
            bad += 1
            sys.stdout.writelines(difflib.unified_diff((cur or "").splitlines(True), text.splitlines(True),
                                                       f"{rel} (current)", f"{rel} (generated)"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        print(f"wrote: {rel}")
    if a.check and bad:
        print(f"\n{bad} generated file(s) differ from the source ({SOURCE}) - run python3 packaging/gen.py and commit the result together", file=sys.stderr)
    web = web_mismatches(root)
    for m in web:
        print(f"version / license mismatch: {m} - fix by hand (in the lock only the two root entries; no sed)", file=sys.stderr)
    nc = notices_check(root) if a.check else None
    if a.check and nc is None:
        print("Note: web/node_modules is missing, so THIRD_PARTY_NOTICES.md was not checked (run cd web && npm ci, then again)", file=sys.stderr)
    if a.check and (bad or web or nc):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
