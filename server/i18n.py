# SPDX-License-Identifier: MIT
"""Language choice for CLI, server and MCP text: Korean ("ko") or English ("en").

Order: $CERA_LANG (ko | en; the old name TEMPBOARD_LANG also works) > the saved choice in $CERA_HOME/config.json ("lang", set by
`cera lang ko|en|auto`) > the system locale ($LC_ALL, $LC_MESSAGES, $LANG starting with "ko") > English.

Usage, with both texts side by side at the call site:

    from i18n import T            # or _sibling("i18n").T inside server modules
    print(T(f"사이트 {sid} 를 열었다", f"Opened site {sid}"))

Standard library only; importable on Python 3.9.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Optional

LANGS = ("ko", "en")
_cache: dict = {}


def _env_module():
    """server/env.py (CERA_* names with the old TEMPBOARD_* fallback), loaded once by path as `cera_env`."""
    key = "cera_env"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().with_name("env.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """CERA_<name>, else the old TEMPBOARD_<name> (see server/env.py)."""
    return _env_module().env(name, default)


def _home() -> Path:
    return _env_module().home()


def config_path() -> Path:
    return _home() / "config.json"


def saved() -> Optional[str]:
    """The language saved with `cera lang`, or None (auto)."""
    try:
        v = json.loads(config_path().read_text(encoding="utf-8")).get("lang")
    except (OSError, ValueError, AttributeError):
        return None
    return v if v in LANGS else None


def save(lang: Optional[str]) -> None:
    """Save "ko" / "en", or None for automatic. Other keys in config.json are kept."""
    p = config_path()
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    if lang in LANGS:
        cfg["lang"] = lang
    else:
        cfg.pop("lang", None)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    _cache.clear()


def from_locale() -> str:
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = (os.environ.get(k) or "").strip().lower()
        if v and v not in ("c", "posix") and not v.startswith("c."):
            return "ko" if v.startswith("ko") else "en"
    return "en"


def lang() -> str:
    v = (env("LANG") or "").strip().lower()
    if v in LANGS:
        return v
    key = (str(config_path()), os.environ.get("LC_ALL"), os.environ.get("LC_MESSAGES"), os.environ.get("LANG"))
    if key not in _cache:
        _cache.clear()
        _cache[key] = saved() or from_locale()
    return _cache[key]


def T(ko: str, en: str) -> str:
    """Pick the Korean or English text for the current language."""
    return ko if lang() == "ko" else en
