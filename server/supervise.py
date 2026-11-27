# SPDX-License-Identifier: MIT
"""Cera supervise loop + state file (standard library only).

`bin/cera _serve` (quick tunnel, 5 attempts) and `cera site run` (named tunnel, unlimited exponential backoff)
share the same loop. One process starts the server + (optionally) a tunnel child, and every second it
  - checks expiry (expires_at),
  - re-reads state.json if its mtime changed and calls access.reload(st) / hosts.set(...)
    (so `cera keys --rotate` and agent-token revocation take effect within 2 seconds; SIGHUP still means "stop").

All state-file writes go through update_state() — take a flock on `sites/<id>/state.lock`, read -> fn -> atomic replace (0600).
So a supervisor public_url write overlapping with `keys --rotate` never loses either field.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, List, Optional

try:
    import fcntl
except ImportError:   # Windows — no locking (Cera targets macOS and Linux)
    fcntl = None


# ------------------------------------------------------------------ state file
def state_path(sites, sid) -> Path:
    return Path(sites) / sid / "state.json"


def T(ko, en):
    """Korean or English text (server/i18n.py: CERA_LANG > `cera lang` > locale > English)."""
    key = "cera_i18n"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().parent / "i18n.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key].T(ko, en)


def read_state(sites, sid) -> Optional[dict]:
    try:
        return json.loads(state_path(sites, sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@contextmanager
def state_lock(sites, sid):
    d = Path(sites) / sid
    fd = os.open(str(d / "state.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write(sites, sid, st):
    p = state_path(sites, sid)
    tmp = p.with_name(f"state.json.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # 0600 from the start — it holds the invite key
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(st, ensure_ascii=False, indent=1))
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def update_state(sites, sid, fn: Callable[[dict], Optional[dict]]) -> dict:
    """Lock, read (or {} if missing), modify via fn(st), then write atomically. fn mutates st or returns a new dict. -> the written state."""
    with state_lock(sites, sid):
        st = read_state(sites, sid) or {}
        out = fn(st)
        st = st if out is None else out
        _write(sites, sid, st)
        return st


def state_mtime(sites, sid):
    """Change-detection token — (inode, mtime_ns, size). os.replace always yields a new inode, so even two writes within the
    same second are caught on filesystems with 1-second mtime resolution (SMB, NFS, HFS+)."""
    try:
        s = state_path(sites, sid).stat()
        return (s.st_ino, s.st_mtime_ns, s.st_size)
    except OSError:
        return None


# ------------------------------------------------------------------ supervise loop
def _log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def default_backoff(fails: int) -> float:
    return min(30, 3 * fails)


def exp_backoff(fails: int, cap: float = 60.0) -> float:
    """Named tunnel — 1, 2, 4, ... capped at 60 seconds."""
    return min(cap, 2 ** max(0, fails - 1))


def run(sid, st, tunnel_cmd: Optional[Callable[[int], Optional[List[str]]]] = None,
        url_from_line: Optional[Callable[[str], Optional[str]]] = None, retries: Optional[int] = 5,
        backoff: Optional[Callable[[int], float]] = None, *, sites, dist, server, authn=(), hsts=False,
        extra_hosts=(), on_start=None, echo_tunnel=False, stop: Optional[threading.Event] = None,
        lan: Optional[str] = None) -> str:
    """Run one site until it ends. -> reason it ended ('stopped' | 'expired').

    tunnel_cmd(port) -> tunnel argv (None means no tunnel). url_from_line(one stderr line) -> public address (quick tunnel); if present,
    write it to state and narrow the Host allowlist to that one name. retries=None means unlimited. backoff(n) is seconds to wait after the n-th failure.
    server: the server.py module. authn, hsts: passed on to server.build. extra_hosts: additional Hosts to allow (rules or strings).
    on_start(port, stop) -> (optional) function to call at shutdown — the place to attach auxiliary children (caffeinate etc.).
    stop: an Event to end the loop from outside (tests). If omitted, SIGTERM, SIGINT and SIGHUP end it. SIGUSR1 means "re-read now".
    Whatever goes wrong in the main loop (an exception), it ends the tunnel child and writes the final state before exiting."""
    sec = server.security
    backoff = backoff or default_backoff
    stop = stop or threading.Event()
    reload_now = threading.Event()
    try:
        for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sg, lambda *_: stop.set())
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, lambda *_: reload_now.set())   # with the default action (terminate, no cleanup) an orphan tunnel would remain
    except ValueError:
        pass   # not the main thread (tests) — it ends only via stop

    access = server.Access(st.get("edit_key"), st.get("view_key"), site=sid, expires_at=st.get("expires_at") or 0)
    hosts = sec.HostAllow()
    try:
        httpd, store = server.build(Path(sites) / sid / "data", dist, st["host"], st.get("port") or 0, access=access,
                                    authn=list(authn), hosts=hosts, hsts=hsts)
    except (OSError, SystemExit) as e:
        update_state(sites, sid, lambda cur: cur.update(status="failed", error=str(e)))
        raise
    port = httpd.server_address[1]
    lan = lan or (sec.lan_ip() if st["host"] == "0.0.0.0" else None)

    def host_rules(cur):
        return sec.rules_for_state(cur, port, lan) + [r if isinstance(r, sec.HostRule) else sec.HostRule.parse(r) for r in extra_hosts]

    local = f"http://{lan or '127.0.0.1'}:{port}"
    tunnel = {"p": None}
    tlock = threading.Lock()
    cleanup = None
    reason = "stopped"
    try:
        st = update_state(sites, sid, lambda cur: cur.update(pid=os.getpid(), port=port, local_url=local, status="running"))
        # On the first tick (1 s), always re-read the disk — so a token revocation or key rotation that landed in the window between the
        # caller reading st / build loading the tokens and this point also reaches memory. Previously this took the mtime after such a change and missed it until the next change.
        seen = None
        hosts.set(host_rules(st))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        _log("server", local)
        cleanup = on_start(port, stop) if on_start else None

        def run_tunnel():
            fails = 0
            while not stop.is_set() and (retries is None or fails < retries):
                argv = tunnel_cmd(port)
                if not argv:
                    return
                with tlock:   # so it cannot overlap with the terminating side and leave an orphan tunnel
                    if stop.is_set():
                        return
                    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         text=True, errors="replace")
                    tunnel["p"] = p
                began = time.time()
                for line in p.stderr:
                    if echo_tunnel:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    url = url_from_line(line) if url_from_line else None
                    if url:
                        def put(cur, url=url, n=fails):
                            if cur.get("public_url") != url:
                                cur["public_url"] = url
                                cur["tunnel_restarts"] = n
                        try:
                            cur = update_state(sites, sid, put)
                            hosts.set(host_rules(cur))   # the address is out — narrow from the wildcard to this one name
                            _log("tunnel", url)
                        except Exception as e:   # noqa: BLE001 — a write failure must not stop tunnel supervision
                            _log("tunnel url write failed:", e)
                p.wait()
                if stop.is_set():
                    return
                fails = 1 if time.time() - began > 300 else fails + 1   # if it lived long before dying, restart the backoff from the beginning
                wait = backoff(fails)
                _log(T(f"tunnel exited ({p.returncode}) — {wait:.0f}초 뒤 다시 연다({fails})", f"tunnel exited ({p.returncode}) - reopening in {wait:.0f}s ({fails})"))
                stop.wait(wait)
            if not stop.is_set():
                def gone(cur):
                    cur["error"] = T("터널을 다시 열지 못했다", "Could not reopen the tunnel")
                    if url_from_line:
                        cur["public_url"] = None
                cur = update_state(sites, sid, gone)
                hosts.set(host_rules(cur))   # if there is an error, do not reopen the wildcard either

        if tunnel_cmd:
            threading.Thread(target=run_tunnel, daemon=True).start()

        while not stop.wait(1.0):
            mt = state_mtime(sites, sid)
            if mt != seen or reload_now.is_set():
                seen = mt
                reload_now.clear()
                try:
                    cur = read_state(sites, sid)
                    if cur:
                        access.reload(cur)          # keys, expiry (agent tokens). If a key changes, close open WS / SSE connections
                        hosts.set(host_rules(cur))
                except Exception as e:   # noqa: BLE001 — one bad state must not kill the site (keep the previous settings)
                    _log("reload failed:", type(e).__name__, e)
            if access.expires_at and time.time() >= access.expires_at:
                reason = "expired"
                break
    except BaseException as e:   # noqa: BLE001 — whatever happens, still run the cleanup below
        reason = "failed"
        _log("supervisor error:", type(e).__name__, e)
        raise
    finally:
        stop.set()
        with tlock:
            p = tunnel["p"]
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        if cleanup:
            try:
                cleanup()
            except Exception as e:   # noqa: BLE001
                _log("cleanup:", e)
        try:
            httpd.shutdown()
            httpd.server_close()
            store.flush()
        except Exception as e:   # noqa: BLE001
            _log("shutdown:", e)

        def closed(cur):
            cur.update(status=reason, pid=None, closed_at=time.time())
            if url_from_line:   # a quick tunnel address cannot be reused. Keep a named tunnel address (configured)
                cur["public_url"] = None
        try:
            update_state(sites, sid, closed)
        except Exception as e:   # noqa: BLE001
            _log("final state write failed:", e)
        _log(reason)
    return reason
