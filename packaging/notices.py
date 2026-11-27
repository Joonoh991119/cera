#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generator for THIRD_PARTY_NOTICES.md: license notices for the third-party code and fonts shipped in the web bundle (web/dist, the Release tarball).

    python3 packaging/notices.py           # write the file
    python3 packaging/notices.py --check   # on mismatch, print a diff and exit 1 (gen.py --check also calls this when web/node_modules exists)

The list is not hand-written: it includes every **production dependency** (not dev) from web/package-lock.json (versions and licenses come from the lock),
and each package's LICENSE / NOTICE text is copied verbatim from web/node_modules (identical texts appear once, listing the packages that use them).
Anything that does not go into the bundle is left out: platform binaries (packages restricted by os / cpu) and type definitions (@types/*).
The fonts Excalidraw ships in dist/fonts have no license in their npm metadata, so they are listed in the FONT_LICENSES table below;
an unknown font folder makes the script fail (add one line to the table). The Korean font subsets are covered by web/public/kfonts/LICENSE.txt.
Reading the files requires `cd web && npm ci` first. Standard library only, Python 3.9.
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
OUT = "THIRD_PARTY_NOTICES.md"
FONT_DIR = "web/node_modules/@excalidraw/excalidraw/dist/prod/fonts"
# Excalidraw 0.18 dist/prod/fonts folder -> (font name, license, source). Basis: each font's repository and the font README in the excalidraw repository.
FONT_LICENSES = {
    "Assistant": ("Assistant", "OFL-1.1", "https://github.com/hafontia/Assistant"),
    "Cascadia": ("Cascadia Code", "OFL-1.1", "https://github.com/microsoft/cascadia-code"),
    "ComicShanns": ("Comic Shanns", "MIT", "https://github.com/shannpersand/comic-shanns"),
    "Excalifont": ("Excalifont", "OFL-1.1", "https://github.com/excalidraw/excalifont"),
    # Excalidraw ships Liberation Sans 1.05 (checked in the font's name table). 1.x is GPL v2 + font exception, not OFL (OFL starts with 2.x).
    "Liberation": ("Liberation Sans 1.05", "GPL-2.0 WITH font exception (Liberation 1.x)", "https://github.com/liberationfonts/liberation-fonts"),
    "Lilita": ("Lilita One", "OFL-1.1", "https://github.com/google/fonts/tree/main/ofl/lilitaone"),
    "Nunito": ("Nunito", "OFL-1.1", "https://github.com/googlefonts/nunito"),
    "Virgil": ("Virgil", "OFL-1.1", "https://github.com/excalidraw/virgil"),
    "Xiaolai": ("Xiaolai SC (Excalidraw subset)", "OFL-1.1", "https://github.com/lxgw/kose-font"),
}
LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice)([.-].*)?$", re.I)


def prod_packages(root: Path):
    lock = json.loads((root / "web" / "package-lock.json").read_text(encoding="utf-8"))
    out = []
    for path, meta in lock.get("packages", {}).items():
        if not path or meta.get("dev") or meta.get("devOptional") or meta.get("link"):
            continue
        name = meta.get("name") or path.split("node_modules/")[-1]
        if name.startswith("@types/") or meta.get("os") or meta.get("cpu"):
            continue
        out.append((name, meta.get("version", "?"), path, meta.get("license")))
    return sorted(out, key=lambda x: (x[0].lower(), x[1], x[2]))


def license_of(root: Path, path: str, lock_license):
    if lock_license:
        return lock_license
    pj = root / "web" / path / "package.json"
    try:
        d = json.loads(pj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "UNKNOWN"
    lic = d.get("license") or d.get("licenses")
    if isinstance(lic, list):
        return " OR ".join((x.get("type") if isinstance(x, dict) else str(x)) for x in lic)
    if isinstance(lic, dict):
        return lic.get("type") or "UNKNOWN"
    return lic or "UNKNOWN"


SPDX_URL = "https://spdx.org/licenses/{}.html"


def author_of(root: Path, path: str) -> str:
    try:
        d = json.loads((root / "web" / path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    a = d.get("author")
    if isinstance(a, dict):
        a = a.get("name")
    return str(a or "").strip()


def license_texts(root: Path, path: str):
    d = root / "web" / path
    if not d.is_dir():
        raise SystemExit(f"{d} not found - first run  cd web && npm ci")
    out = []
    for f in sorted(d.iterdir(), key=lambda x: x.name.lower()):
        if f.is_file() and LICENSE_FILE.match(f.name):
            out.append((f.name, f.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()))
    return out


def render(root: Path = ROOT) -> str:
    pkgs = prod_packages(root)
    fonts_dir = root / FONT_DIR
    if not fonts_dir.is_dir():
        raise SystemExit(f"{fonts_dir} not found - first run  cd web && npm ci")
    fams = sorted(p.name for p in fonts_dir.iterdir() if p.is_dir())
    unknown = [f for f in fams if f not in FONT_LICENSES]
    if unknown:
        raise SystemExit(f"Excalidraw font folders with unknown license: {', '.join(unknown)} - add them to FONT_LICENSES in packaging/notices.py")
    lines = ["# Third-party notices", "",
             "<!-- Generated by packaging/notices.py from web/package-lock.json and web/node_modules. Do not edit by hand. -->", "",
             "tempboard itself is MIT-licensed (see `LICENSE`). The web app bundle (`web/dist`, and the `tempboard-dist.tar.gz` "
             "release asset) contains the third-party software and fonts below. Each keeps its own license; the full license "
             "texts follow the tables.", "",
             "## Fonts bundled by Excalidraw (`dist/fonts/`)", "", "| Folder | Font | License | Source |", "|---|---|---|---|"]
    for f in fams:
        n, lic, src = FONT_LICENSES[f]
        lines.append(f"| {f} | {n} | {lic} | {src} |")
    lines += ["", "SIL Open Font License 1.1: https://openfontlicense.org/open-font-license-official-text/", "",
              "Liberation Sans 1.05 (Copyright 2007 Red Hat, Inc., digitized data Ascender Corporation) is licensed under the "
              "GNU General Public License version 2 (https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt) with this exception: "
              "\"As a special exception, if you create a document which uses this font, and embed this font or unaltered portions "
              "of this font into the document, this font does not by itself cause the resulting document to be covered by the GNU "
              "General Public License. This exception does not however invalidate any other reasons why the document might be "
              "covered by the GNU General Public License.\" The font is shipped unmodified as part of the Excalidraw package; "
              "its source is https://github.com/liberationfonts/liberation-fonts (1.x releases).", "",
              "## Korean font subsets (`kfonts/`)", "",
              "Gaegu, Noto Sans KR, Nanum Pen Script, Black Han Sans, Nanum Gothic Coding, Jua, Nanum Myeongjo, Dokdo, "
              "Poor Story, Do Hyeon — SIL Open Font License 1.1. Copyright notices and the license text: "
              "`web/public/kfonts/LICENSE.txt` (shipped as `kfonts/LICENSE.txt` in the bundle).", "",
              "## Code ported into this repository", "",
              "- `server/fracindex.py` — from rocicorp/fractional-indexing (CC0-1.0).", "",
              f"## npm packages ({len(pkgs)}, production dependencies from `web/package-lock.json`)", "",
              "| Package | Version | License |", "|---|---|---|"]
    by_text = {}
    order = []
    seen = set()
    for name, ver, path, lic in pkgs:
        texts = license_texts(root, path)
        lic = license_of(root, path, lic)
        if lic == "UNKNOWN" and texts and re.search(r"\bMIT License\b", texts[0][1][:200]):
            lic = "MIT (from the license file)"
        if (name, ver) not in seen:
            lines.append(f"| {name} | {ver} | {lic} |")
        key = "\n\n".join(f"--- {fn} ---\n{t}" if len(texts) > 1 else t for fn, t in texts)
        if not key:   # the package has no license file; state the declared license, the author and the standard license URLs
            who = author_of(root, path)
            ids = re.findall(r"[A-Za-z0-9.+-]+", lic)
            urls = ", ".join(SPDX_URL.format(i) for i in ids if i not in ("AND", "OR", "WITH"))
            key = (f"No license file ships in this package. package.json declares: {lic}"
                   + (f"; author: {who}" if who else "") + f". Standard license text: {urls}")
        if key not in by_text:
            by_text[key] = []
            order.append(key)
        if (name, ver) not in seen:
            by_text[key].append(f"{name}@{ver}")
        seen.add((name, ver))
    lines += ["", "## License texts", ""]
    for key in order:
        who = by_text[key]
        lines += [f"### {', '.join(who)}", "", "```text", key.replace("```", "``​`"), "```", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def check(root: Path = ROOT, quiet=False) -> int:
    want = render(root)
    path = root / OUT
    cur = path.read_text(encoding="utf-8") if path.is_file() else ""
    if cur == want:
        return 0
    if not quiet:
        sys.stdout.writelines(difflib.unified_diff(cur.splitlines(True), want.splitlines(True), f"{OUT} (current)", f"{OUT} (generated)", n=1))
        print(f"\n{OUT} differs from web/package-lock.json / node_modules - run python3 packaging/notices.py", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="notices.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.check:
        return check(root)
    text = render(root)
    tmp = root / (OUT + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, root / OUT)
    print(f"wrote: {OUT} ({len(text.encode()) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
