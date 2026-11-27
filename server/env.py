# SPDX-License-Identifier: MIT
"""Environment variables and the data folder, with the old (tempboard) names still accepted.

    env("HOME")      # $CERA_HOME, or the old $TEMPBOARD_HOME when CERA_HOME is unset or empty
    home()           # the data folder: $CERA_HOME > $TEMPBOARD_HOME > ~/.cera (> ~/.tempboard if only that exists)
    migrate_home()   # once, on the CLI's first run: move ~/.tempboard to ~/.cera and leave ~/.tempboard -> ~/.cera

Every CERA_* variable is read through env(), so each also works under its old TEMPBOARD_* name.
Standard library only; importable on Python 3.9.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

PREFIX = "CERA_"
OLD_PREFIX = "TEMPBOARD_"
DIR = ".cera"
OLD_DIR = ".tempboard"


def env(name: str, default: Optional[str] = None, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """CERA_<name>, else TEMPBOARD_<name>, else default. An empty value counts as unset."""
    e = os.environ if environ is None else environ
    return e.get(PREFIX + name) or e.get(OLD_PREFIX + name) or default


def home_override(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The data folder set explicitly ($CERA_HOME or $TEMPBOARD_HOME), or None."""
    v = env("HOME", environ=environ)
    return Path(v).expanduser() if v else None


def home(environ: Optional[Mapping[str, str]] = None, user_home: Optional[Path] = None) -> Path:
    """The data folder, without changing anything on disk."""
    o = home_override(environ)
    if o is not None:
        return o
    base = Path(user_home) if user_home is not None else Path.home()
    new, old = base / DIR, base / OLD_DIR
    if not os.path.lexists(new) and old.is_dir():   # not migrated (yet, or the move failed): keep using the old folder
        return old
    return new


def migrate_home(environ: Optional[Mapping[str, str]] = None, user_home: Optional[Path] = None) -> str:
    """Move ~/.tempboard to ~/.cera once and leave a symlink ~/.tempboard -> ~/.cera, so an always-on service
    installed earlier (it runs ~/.tempboard/bin/tempboard with TEMPBOARD_HOME=~/.tempboard) keeps working.

    Returns what happened: "override" (CERA_HOME / TEMPBOARD_HOME is set, nothing touched), "fresh" (no old folder),
    "done" (already migrated or ~/.cera already exists), "migrated", or "failed: <reason>" (the old folder stays in use).
    Never deletes anything: the move is one rename on the same disk, and if ~/.cera already exists nothing is moved."""
    if home_override(environ) is not None:
        return "override"
    base = Path(user_home) if user_home is not None else Path.home()
    new, old = base / DIR, base / OLD_DIR
    if os.path.lexists(new):
        return "done"
    if not old.is_dir():   # missing, a file, or a dangling link (for example to a ~/.cera that was removed)
        return "fresh"
    try:
        os.rename(old, new)   # atomic; fails instead of overwriting if ~/.cera appeared meanwhile
    except FileNotFoundError:   # another process migrated it at the same moment
        return "done" if os.path.lexists(new) else "fresh"
    except OSError as e:
        if os.path.lexists(new):
            return "done"
        return f"failed: {type(e).__name__}: {e}"
    try:
        os.symlink(new, old)
    except FileExistsError:
        pass   # something recreated ~/.tempboard meanwhile; leave it alone
    except OSError as e:
        return f"migrated (no link {old}: {type(e).__name__})"
    return "migrated"
