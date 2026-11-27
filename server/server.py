#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tempboard whiteboard server (standard library only).

    python3 server/server.py --data ./data --port 8790 [--edit-key K] [--view-key V]
    → http://127.0.0.1:8790/?k=K

Normally you do not start it directly; `bin/tempboard start` does (it also sets up the temporary tunnel, expiry and invite keys).

Access: with --edit-key, every request requires an invite key. Opening a link with `?k=<key>` for the first time swaps the key for a cookie and
removes the key from the address. An edit key can draw and create boards; a view key can only read and see cursors. With no key at all the server is local-only (anyone can edit).
Names are collected on the first screen (cookie `wb_user`). If a front proxy decides the user, use `--trust-header X-User` (beware of spoofing).

Host allow-list: only 127.0.0.1:PORT, localhost:PORT and [::1]:PORT are accepted (anything else gets 421, to block DNS rebinding).
To accept other names, use `--allow-host board.example.com` (treated as https) or `--allow-host http://localhost:5173` (vite dev proxy).

Sync rule (same as Excalidraw): per element, the higher version wins; on a tie, the lower versionNonce wins.
Deleted elements are kept with isDeleted so they disappear on other people's screens too. Undo only reverts your own actions on your own screen.
"""
from __future__ import annotations

import argparse
import hmac
import importlib.util
import sys
import os
import base64
import hashlib
import socket
import struct
import collections
import json
import math
import queue
import re
import secrets
import threading
import time
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _sibling(name):
    """Load a sibling module (security, etc.) once under the name `tempboard_<name>`, so that even when this file is loaded as a spec
    (bin/tempboard, tests) the same classes are shared."""
    key = "tempboard_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


def T(ko, en):
    """Korean or English text (server/i18n.py: TEMPBOARD_LANG > `tempboard lang` > locale > English)."""
    return _sibling("i18n").T(ko, en)


security = _sibling("security")
HostRule, HostAllow, Auth, Req = security.HostRule, security.HostAllow, security.Auth, security.Req
CF_EDGE_HEADERS = ("Cf-Connecting-IP", "Cf-Ray")   # headers Cloudflare's edge always adds; if present, the request came through the tunnel
HSTS = "max-age=15552000"   # 6 months. includeSubDomains and preload are left out (they could break other names on the user's domain)
BOARD_ID = re.compile(r"^[a-z0-9]{8,32}$")
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MAX_BODY = 12 * 1024 * 1024        # roughly the upper limit for one image (data URL)
MAX_FILE = 10 * 1024 * 1024
MAX_ELEMENTS_PER_POST = 2000
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
# Person colors: chosen so hues are at least 30 degrees apart (wider than the 18 degrees the UI uses to judge 'similar colors'). The first four are colors from the visualization spec 2.0
EPOCH = secrets.token_hex(4)   # changes on every server start; the UI resyncs everything when it changes
COLORS = ["#D55E00", "#0072B2", "#009E73", "#CC79A7", "#F0E442", "#7B61FF", "#66A61E", "#E31A1C"]


def _hsl(hexc):
    n = int(hexc.lstrip("#"), 16)
    r, g, b = (n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255
    mx, mn = max(r, g, b), min(r, g, b)
    l, d = (mx + mn) / 2, mx - mn
    if d == 0:
        return 0.0, 0.0, l
    s = d / (1 - abs(2 * l - 1))
    h = 60 * (((g - b) / d) % 6) if mx == r else 60 * ((b - r) / d + 2) if mx == g else 60 * ((r - g) / d + 4)
    return h % 360, s, l


def similar(a, b):
    """Same test as the UI (Board.jsx sameColor): the same hex, or both colors are saturated and their hues are within 18 degrees."""
    if a.lower() == b.lower():
        return True
    (h1, s1, l1), (h2, s2, l2) = _hsl(a), _hsl(b)
    if min(s1, s2) < 0.35 or min(l1, l2) < 0.18 or max(l1, l2) > 0.85:
        return False
    dh = abs(h1 - h2)
    return min(dh, 360 - dh) <= 18


PRESENCE_MS = 0.1  # presence-list broadcast interval; changes in between are coalesced into the last state (30-60 people in a lecture)
SUMMON_GAP = 1.0   # minimum interval (seconds) between one connection's 'everyone to me' (summon) actions
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
VIEW_LIMIT = 1e7   # scene-coordinate limit; a view range larger than this is treated as a bad value


def clean_view(v):
    """The view range [x1, y1, x2, y2] (scene coordinates) from presence: accepted only if it has four finite numbers within the limit with x2>x1 and y2>y1. Otherwise None (dropped)."""
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and abs(n) <= VIEW_LIMIT for n in v):
        return None
    x1, y1, x2, y2 = (round(float(n), 1) for n in v)
    return [x1, y1, x2, y2] if x2 > x1 and y2 > y1 else None


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSConn:
    """One WebSocket connection (RFC 6455, standard library only). Sending goes through a queue and a dedicated thread, so one slow client does not block the others."""

    def __init__(self, handler):
        self.h = handler
        self.q = queue.Queue(maxsize=2000)
        self.alive = True
        self.delta = False   # client that receives only changes to the presence list (?pd=1)
        self.writer = threading.Thread(target=self._write_loop, daemon=True)
        self.writer.start()

    def _frame(self, opcode, payload: bytes):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([n])
        elif n < 65536:
            head += bytes([126]) + struct.pack(">H", n)
        else:
            head += bytes([127]) + struct.pack(">Q", n)
        return head + payload

    def send(self, obj):
        if not self.alive:
            return False
        try:
            self.q.put_nowait(self._frame(0x1, json.dumps(obj, ensure_ascii=False).encode("utf-8")))
            return True
        except queue.Full:   # client too far behind: drop it, and it reconnects and resyncs from scratch (closed separately so a call under the lock does not block)
            threading.Thread(target=self.close, daemon=True).start()
            return False

    def _write_loop(self):
        while self.alive:
            try:
                data = self.q.get(timeout=20)
            except queue.Empty:
                data = self._frame(0x9, b"hb")   # ping every 20 s so the front end (tunnel) does not drop an idle connection
            try:
                self.h.wfile.write(data)
                self.h.wfile.flush()
            except (OSError, ValueError):   # ValueError: file already closed while the server is shutting down
                self.alive = False
                break

    def _read_exact(self, n):
        """TCP may not deliver n bytes at once, so keep reading until we have them all. None if the connection drops."""
        buf = bytearray()
        while len(buf) < n:
            chunk = self.h.rfile.read(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def recv(self):
        """(opcode, payload). Fragmented messages are reassembled (the total must also stay within MAX_BODY). A dropped connection or protocol violation gives (8, b'')."""
        buf, first_op = bytearray(), None
        while True:
            hdr = self._read_exact(2)
            if not hdr:
                return 8, b""
            fin, op = hdr[0] & 0x80, hdr[0] & 0x0F
            masked, n = hdr[1] & 0x80, hdr[1] & 0x7F
            if n in (126, 127):
                ext = self._read_exact(2 if n == 126 else 8)
                if not ext:
                    return 8, b""
                n = struct.unpack(">H" if len(ext) == 2 else ">Q", ext)[0]
            if n > MAX_BODY or len(buf) + n > MAX_BODY:
                return 8, b""
            mask = self._read_exact(4) if masked else None
            if masked and not mask:
                return 8, b""
            raw = self._read_exact(n) if n else b""
            if raw is None:
                return 8, b""
            data = bytearray(raw)
            if mask:
                for i in range(n):
                    data[i] ^= mask[i & 3]
            if op in (0x8, 0x9, 0xA):   # control frames can arrive between fragments
                if op == 0x9:
                    try:
                        self.q.put_nowait(self._frame(0xA, bytes(data)))
                    except queue.Full:
                        pass
                    continue
                if op == 0xA:
                    continue
                return 8, b""
            if op != 0:
                if first_op is not None:
                    return 8, b""   # a new message before the previous one finished: protocol violation
                first_op = op
            elif first_op is None:
                return 8, b""       # continuation fragment with no start
            buf.extend(data)
            if fin:
                return first_op, bytes(buf)

    def close(self):
        if self.alive:
            self.alive = False
            try:
                self.h.wfile.write(self._frame(0x8, b""))
                self.h.wfile.flush()
            except OSError:
                pass
            try:
                self.h.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def wins(new, old):
    """Excalidraw rule: the higher version wins; on a tie, the lower versionNonce."""
    if old is None:
        return True
    try:
        nv, ov = int(new.get("version") or 0), int(old.get("version") or 0)
        if nv != ov:
            return nv > ov
        return int(new.get("versionNonce") or 0) < int(old.get("versionNonce") or 0)
    except (TypeError, ValueError):   # reject elements that cannot be compared (without killing the connection)
        return False


class Board:
    def __init__(self, root: Path, bid: str):
        self.root, self.id = root, bid
        self.lock = threading.RLock()
        self.subs = []          # (queue, client_id) — SSE
        self.sockets = {}       # client_id → WSConn
        self.seq = 0            # event number for long-polling (drawings, files and names only; cursors are tracked separately)
        self.log = collections.deque(maxlen=20000)  # (seq, from_client, event, data)
        self.pver = 0           # cursor / presence version, +1 on every change. Not stored in the event log, so it does not push drawing events out of it
        self.cond = threading.Condition(self.lock)
        self.gen = 0            # +1 whenever the key changes (close_streams); wakes waiting long-polls and returns an empty reply
        self.presence = {}      # client_id → dict
        self.summon_at = 0      # time (ms) of the last 'everyone to me' (summon); only increases (if two people press in the same ms, the later one is larger)
        self._plast = 0.0       # time of the last presence-list broadcast
        self._ptimer = None     # coalesced broadcast (after PRESENCE_MS)
        self._pdirty = set()    # connections that changed or left since the last broadcast; sent to clients that receive only changes (pd=1)
        self.meta, self.elements = {}, {}
        p = root / "board.json"
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            self.meta = d.get("meta", {})
            self.elements = {e["id"]: e for e in d.get("elements", []) if isinstance(e, dict) and e.get("id")}
        self.dirty = False
        self.saved_seq = 0      # last event number written to disk; reported as 'saved', separately from ack (received)
        self.save_lock = threading.Lock()

    def _announce_saved(self, upto):
        with self.lock:
            if upto <= self.saved_seq:
                return
            self.saved_seq = upto
            msg = {"t": "saved", "seq": upto}
            for c in list(self.sockets.values()):
                c.send(msg)
            self.cond.notify_all()   # also wake long-polls waiting for the save confirmation

    def save(self):
        """Under the lock only copy the list; serialization and the disk write happen outside it, so the pen does not stall while saving.
        Only after the write finishes do we announce 'saved' up to the event number at that moment (the UI keeps its resend list until then)."""
        with self.save_lock:
            with self.lock:
                upto = self.seq
                if not self.dirty:
                    meta = None
                else:
                    meta, els = dict(self.meta), list(self.elements.values())
                    self.dirty = False
            if meta is not None:
                try:
                    self.root.mkdir(parents=True, exist_ok=True)
                    tmp = self.root / "board.json.tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps({"meta": meta, "elements": els}, ensure_ascii=False))
                        fh.flush()
                        os.fsync(fh.fileno())
                    tmp.replace(self.root / "board.json")
                except OSError:
                    with self.lock:
                        self.dirty = True
                    return
            self._announce_saved(upto)

    def merge(self, elements, client):
        took = []
        with self.lock:
            for e in elements[:MAX_ELEMENTS_PER_POST]:
                if not isinstance(e, dict) or not isinstance(e.get("id"), str) or len(e["id"]) > 64:
                    continue
                if wins(e, self.elements.get(e["id"])):
                    self.elements[e["id"]] = e
                    took.append(e)
            if took:
                self.dirty = True
                self.meta["updated_at"] = now()
                self.publish("elements", {"from": client, "elements": took}, skip=client)   # under the lock, so the event number and send order match
        return took

    def patch(self, changes, client, atomic=False):
        """Race-free edit: under the lock, apply the set to the **current** element and broadcast with version+1, a new versionNonce and updated.
        If base_version differs from the current version, that item is a conflict (the person's change is kept). With atomic, if any item hits a conflict nothing
        is applied. Items that would change nothing (only a base_version check, or the same value) do not bump the version and are reported as unchanged. set keys are limited to PATCH_FIELDS. add_bound / remove_bound add to and remove from the current boundElements (no read-then-write race).
        → {applied:[{id, version}], conflict:[{id, version, base_version}], missing:[id], rejected:[{id, error}], seq}"""
        applied, conflict, missing, rejected, checked, out = [], [], [], [], [], []
        with self.lock:
            for ch in changes[:MAX_ELEMENTS_PER_POST]:
                if not isinstance(ch, dict) or not isinstance(ch.get("id"), str) or not 0 < len(ch["id"]) <= 64:
                    rejected.append({"id": ch.get("id") if isinstance(ch, dict) else None, "error": "id"})
                    continue
                eid = ch["id"]
                cur = self.elements.get(eid)
                if cur is None:
                    missing.append(eid)
                    continue
                try:
                    ver = int(cur.get("version") or 0)
                except (TypeError, ValueError):
                    ver = 0
                base = ch.get("base_version")
                if base is not None and (isinstance(base, bool) or not isinstance(base, int) or base != ver):
                    conflict.append({"id": eid, "version": ver, "base_version": base})
                    continue
                try:
                    new = patched(cur, ch.get("set") or {}, ch.get("add_bound"), ch.get("remove_bound"))
                except ValueError as e:
                    rejected.append({"id": eid, "error": str(e)})
                    continue
                if new == cur:   # nothing would change (check only, or the same value): do not bump the version and do not broadcast
                    checked.append({"id": eid, "version": ver})
                    continue
                new["version"] = ver + 1
                new["versionNonce"] = secrets.randbelow(2 ** 31)
                new["updated"] = int(time.time() * 1000)
                out.append(new)
            if atomic and (conflict or missing or rejected):
                out = []
            took = self.merge(out, client) if out else []
            applied = [{"id": e["id"], "version": e["version"]} for e in took]
            return {"applied": applied, "unchanged": checked, "conflict": conflict, "missing": missing, "rejected": rejected,
                    "seq": self.seq}

    def publish(self, event, data, skip=None, delta=None):
        msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
        with self.lock:
            if event == "presence":
                self.pver += 1
                wsmsg, wskip = {"t": "presence", "pver": self.pver, "data": data}, None
            else:
                self.seq += 1
                self.log.append((self.seq, skip, event, data))
                wsmsg, wskip = {"t": "ev", "seq": self.seq, "event": event, "data": data}, skip
            self.cond.notify_all()
            dmsg = {"t": "pdelta", "pver": self.pver, **delta} if delta is not None else None
            for cid, c in list(self.sockets.items()):   # only enqueue under the lock: it does not block, and the order matches the event numbers
                if cid != wskip:
                    c.send(dmsg if dmsg is not None and c.delta else wsmsg)
            for q, cid in list(self.subs):
                if cid != skip:
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        pass

    def poll(self, since, client, pver=-1, epoch="", timeout=20.0, saved=-1):
        """Long-poll: drawing events after `since` (excluding my own) and, if the cursor version (pver) changed, the presence list.
        If the server restarted (different epoch), the log was trimmed, or since is ahead, return reset (the client receives everything again; it merges without deleting)."""
        deadline = time.time() + timeout
        with self.cond:
            g0 = self.gen
            while True:
                if self.gen != g0:   # the key changed: return nothing (the handler rechecks permission and sends 403)
                    return {"epoch": EPOCH, "seq": since, "pver": self.pver, "saved": self.saved_seq, "events": []}
                if epoch != EPOCH or since > self.seq or (self.log and since < self.log[0][0] - 1):
                    return {"epoch": EPOCH, "seq": self.seq, "pver": self.pver, "saved": self.saved_seq, "reset": True, "events": [], "presence": self.presence_list()}
                evs = [{"event": e, "data": d} for (n, frm, e, d) in self.log if n > since and frm != client]
                if evs or pver != self.pver or (saved >= 0 and self.saved_seq > saved):
                    out = {"epoch": EPOCH, "seq": self.seq, "pver": self.pver, "saved": self.saved_seq, "events": evs}
                    if pver != self.pver:
                        out["presence"] = self.presence_list()
                    return out
                since = max(since, self.seq)   # if there were only my own events, skip them and keep waiting
                left = deadline - time.time()
                if left <= 0:
                    return {"epoch": EPOCH, "seq": since, "pver": self.pver, "saved": self.saved_seq, "events": []}
                self.cond.wait(left)
                if self.log and self.log[-1][0] > since:
                    self.cond.wait(0.04)   # once woken, collect consecutive changes for another 40 ms and return them together

    def update_presence(self, client, name, color, data, role, agent=False):
        """Presence entry: only the fields that were sent are changed; unsent fields keep their previous value (the view range arrives only when it changes).
        role: the connection's permission as decided by the server. If it is not edit (view, or None after key expiry), it is recorded as view-only.
        view: the view range (follow mode) - dropped with the previous value kept if malformed. follow: the connection id I am following (a follower
        does not send its range back, only this; the receiving side keeps tracking it to the end). summon: 'everyone to me' - editors only, once per second per connection,
        stamped with the server time (ms, increasing per version). Receivers follow the largest timestamp (the latest).
        agent: a connection that came in with an agent token - decided by the server only (a name header cannot turn it on). The UI puts an 'AI' chip on it."""
        role = "edit" if role == "edit" else "view"
        with self.lock:
            old = self.presence.get(client) or {}
            p = {"client": client, "user": name, "short": agent_short(name) if agent else short_name(name), "role": role,
                 "pointer": data["pointer"] if "pointer" in data else old.get("pointer"), "button": data.get("button", old.get("button", "up")),
                 "selected": (data.get("selected") or {}) if "selected" in data else old.get("selected", {}),
                 "color": color, "_t": time.time()}
            view = old.get("view")
            if "view" in data:
                view = None if data["view"] is None else (clean_view(data["view"]) or view)
            if view:
                p["view"] = view
            follow = old.get("follow")
            if "follow" in data:
                f = data["follow"]
                follow = None if f is None else (f if isinstance(f, str) and CLIENT_ID.match(f) and f != client else follow)
            if follow:
                p["follow"] = follow
            summon, last = old.get("summon"), old.get("_s", 0.0)
            if data.get("summon") is True and role == "edit" and time.time() - last >= SUMMON_GAP:
                self.summon_at = max(int(time.time() * 1000), self.summon_at + 1)
                summon, last = {"at": self.summon_at}, time.time()
            if summon:
                p["summon"], p["_s"] = summon, last
            if agent:
                p["agent"] = True
            self.presence[client] = p
            self._pdirty.add(client)
        self.presence_changed()

    def presence_changed(self):
        """Coalesce presence-list broadcasts: send immediately if PRESENCE_MS has passed since the last one, otherwise once at that time (changes in between go out as the last state).
        The broadcast format (event presence = full list) is unchanged, so older clients (v0.2.0) still receive it."""
        with self.lock:
            if self._ptimer is not None:
                return
            wait = self._plast + PRESENCE_MS - time.time()
            if wait <= 0:
                self._presence_broadcast()
                return
            self._ptimer = threading.Timer(wait, self._presence_flush)
            self._ptimer.daemon = True
            self._ptimer.start()

    def _presence_flush(self):
        with self.lock:
            self._ptimer = None
            self._presence_broadcast()

    def _presence_broadcast(self):
        """Under the lock. Older clients, SSE and long-polling get the full list; a WebSocket attached with pd=1 gets only the changed people ({t: pdelta, set, gone})."""
        self._plast = time.time()
        full = self.presence_list()
        dirty, self._pdirty = self._pdirty, set()
        here = {p["client"]: p for p in full}
        self.publish("presence", full, delta={"set": [here[c] for c in dirty if c in here], "gone": [c for c in dirty if c not in here]})

    def drop_presence(self, client):
        with self.lock:
            self.presence.pop(client, None)
            self._pdirty.add(client)

    def sweep_presence(self):
        """Remove and broadcast connections with no news for over 30 s (no WebSocket: agents, long-polling). Previously the list only went out
        when someone moved a cursor, so on a quiet board the 'AI' chip of an agent that had left stayed."""
        cut = time.time() - 30
        with self.lock:
            stale = any(v.get("_t", 0) < cut and k not in self.sockets for k, v in self.presence.items())
        if stale:
            self.presence_list()
            self.presence_changed()

    def presence_list(self):
        cut = time.time() - 30
        with self.lock:
            for k in [k for k, v in self.presence.items() if v.get("_t", 0) < cut and k not in self.sockets]:   # a connection with a WebSocket attached counts as present even when idle
                self.presence.pop(k, None)
                self._pdirty.add(k)
            return [{k: v for k, v in p.items() if k not in ("_t", "_s")} for p in self.presence.values()]


AI_SUFFIX = " · AI"


def agent_short(name):
    """Agent cursor / chip text: 'Claude · AI' -> 'Claude AI' (first 8 characters + AI). Unlike human name tags, it always shows AI."""
    n = str(name).strip()
    base = n[:-len(AI_SUFFIX)] if n.endswith(AI_SUFFIX) else n
    return (base.strip()[:8] or "AI") + " AI"


# Fields that a patch set may change. The binding fields (boundElements, start/endBinding, frameId) are included because
# delete and connect cleanup need them; no other field (type, id, index, seed, groupIds ...) can be changed.
_FIN = lambda v: not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= VIEW_LIMIT   # noqa: E731
_SID = lambda v: isinstance(v, str) and 0 < len(v) <= 64   # noqa: E731


def _ok_points(v):
    return isinstance(v, list) and 0 < len(v) <= 20000 and all(isinstance(p, list) and len(p) == 2 and _FIN(p[0]) and _FIN(p[1]) for p in v)


def _ok_bound(v):
    return v is None or (isinstance(v, list) and len(v) <= 2000 and all(
        isinstance(b, dict) and b.get("type") in ("text", "arrow") and _SID(b.get("id")) for b in v))


def _ok_binding(v):
    if v is None:
        return True
    if not isinstance(v, dict) or not _SID(v.get("elementId")):
        return False
    fp = v.get("fixedPoint")
    return (_FIN(v.get("focus", 0)) and _FIN(v.get("gap", 0))
            and (fp is None or (isinstance(fp, list) and len(fp) == 2 and all(_FIN(n) for n in fp))))


def _ok_custom(v):
    if v is None:
        return True
    if not isinstance(v, dict):
        return False
    try:
        return len(json.dumps(v)) <= 65536
    except (TypeError, ValueError):
        return False


PATCH_FIELDS = {
    "x": _FIN, "y": _FIN, "width": lambda v: _FIN(v) and v >= 0, "height": lambda v: _FIN(v) and v >= 0, "angle": _FIN,
    "text": lambda v: isinstance(v, str) and len(v) <= 100000, "originalText": lambda v: isinstance(v, str) and len(v) <= 100000,
    "strokeColor": lambda v: isinstance(v, str) and len(v) <= 40, "backgroundColor": lambda v: isinstance(v, str) and len(v) <= 40,
    "fillStyle": lambda v: v in ("solid", "hachure", "cross-hatch", "zigzag"),
    "strokeStyle": lambda v: v in ("solid", "dashed", "dotted"),
    "opacity": lambda v: _FIN(v) and 0 <= v <= 100, "isDeleted": lambda v: isinstance(v, bool), "points": _ok_points,
    "customData": _ok_custom, "link": lambda v: v is None or (isinstance(v, str) and len(v) <= 4000),
    "locked": lambda v: isinstance(v, bool),
    "boundElements": _ok_bound, "startBinding": _ok_binding, "endBinding": _ok_binding, "frameId": lambda v: v is None or _SID(v),
}


def patched(cur, sets, add_bound=None, remove_bound=None):
    """Apply sets to a copy of cur. A disallowed field or a bad value raises ValueError (only that item is rejected)."""
    if not isinstance(sets, dict):
        raise ValueError("set")
    new = dict(cur)
    for k, v in sets.items():
        check = PATCH_FIELDS.get(k)
        if check is None:
            raise ValueError("field not allowed: " + str(k)[:40])
        if not check(v):
            raise ValueError("bad value: " + k)
        new[k] = v
    if add_bound is not None or remove_bound is not None:
        if not _ok_bound(add_bound or []) or not (remove_bound is None or (isinstance(remove_bound, list) and all(_SID(i) for i in remove_bound))):
            raise ValueError("bound")
        be = [b for b in (new.get("boundElements") or []) if isinstance(b, dict)]
        drop = set(remove_bound or []) | {b["id"] for b in add_bound or []}
        be = [b for b in be if b.get("id") not in drop] + [{"type": b["type"], "id": b["id"]} for b in add_bound or []]
        new["boundElements"] = be or None
    return new


HUMAN_AI_TAIL = re.compile(r"(\s*[·・•]\s*|\s+)AI\s*$", re.IGNORECASE)


def human_name(name):
    """Human name: strip a trailing '· AI' or ' AI' (so it does not look like an agent). If stripping leaves nothing, keep the original name."""
    n = str(name).strip()
    m = HUMAN_AI_TAIL.sub("", n).strip()
    return m or n


def short_name(name):
    """Text next to the cursor: for a three-character Korean name, the last two characters (홍길동 -> 길동); otherwise the first two characters."""
    n = str(name).strip()
    if re.fullmatch(r"[가-힣]{3,4}", n):
        return n[-2:]
    return n[:2]


class Store:
    def __init__(self, data: Path):
        self.data = data
        (data / "boards").mkdir(parents=True, exist_ok=True)
        self.boards, self.lock = {}, threading.Lock()
        self.colors_path = data / "colors.json"
        self.colors = json.loads(self.colors_path.read_text(encoding="utf-8")) if self.colors_path.is_file() else {}

    def color_for(self, name):
        """A fixed color per person with no overlap: one palette color each, in order of first arrival. Two people never share a color."""
        with self.lock:
            if name not in self.colors:
                used = list(self.colors.values())
                free = [c for c in COLORS if not any(similar(c, u) for u in used)]
                # From the 9th person on, the palette is reused (colors repeat). Previously this raised an error here, so /me and presence
                # failed for the 9th person, which blocks a lecture of 30-60 people (found in a load test)
                self.colors[name] = free[0] if free else COLORS[len(self.colors) % len(COLORS)]
                self.colors_path.write_text(json.dumps(self.colors, ensure_ascii=False, indent=1), encoding="utf-8")
            return self.colors[name]

    def get(self, bid):
        if not BOARD_ID.match(bid or ""):
            return None
        with self.lock:
            if bid not in self.boards:
                root = self.data / "boards" / bid
                if not (root / "board.json").is_file():
                    return None
                self.boards[bid] = Board(root, bid)
            return self.boards[bid]

    def create(self, title, user):
        bid = secrets.token_hex(6)
        b = Board(self.data / "boards" / bid, bid)
        b.meta = {"id": bid, "title": title[:80] or T("새 보드", "New board"), "created_by": user["name"], "created_at": now(), "updated_at": now()}
        b.dirty = True
        b.save()
        with self.lock:
            self.boards[bid] = b
        return b

    def discard(self, bid):
        """Delete the board just created (when an import failed, so no empty board is left behind)."""
        with self.lock:
            b = self.boards.pop(bid, None)
        if b is not None:
            import shutil
            shutil.rmtree(b.root, ignore_errors=True)

    def list(self):
        out = []
        for p in sorted((self.data / "boards").glob("*/board.json")):
            b = self.get(p.parent.name)
            if b:
                with b.lock:
                    n = sum(1 for e in b.elements.values() if not e.get("isDeleted"))
                out.append({**b.meta, "elements": n, "online": len(b.presence_list())})
        out.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return out

    def flush(self):
        with self.lock:
            boards = list(self.boards.values())
        for b in boards:
            b.save()
            b.sweep_presence()

    def close_streams(self, predicate=None):
        """Close open WebSocket / SSE streams. Without a predicate, close all of them (on an invite-key change; waiting long-polls are woken too),
        otherwise only those for which predicate(handler) is true (token revoked, JWT expired). Clients reconnect, and are rejected if they no longer have permission.
        If predicate raises, the stream is closed (fail closed)."""
        def hit(h):
            if predicate is None:
                return True
            if h is None:   # leave alone subscriptions with no known handler (e.g. a test's peek queue)
                return False
            try:
                return bool(predicate(h))
            except Exception:   # noqa: BLE001
                return True
        with self.lock:
            boards = list(self.boards.values())
        for b in boards:
            with b.lock:
                socks, subs = list(b.sockets.values()), list(b.subs)
                if predicate is None:
                    b.gen += 1
                    b.cond.notify_all()
            for c in socks:
                if hit(c.h):
                    threading.Thread(target=c.close, daemon=True).start()   # outside the lock, so it does not block
            for q, _ in subs:
                if not hit(getattr(q, "owner", None)):
                    continue
                while True:
                    try:
                        q.put_nowait(None)   # events() ends when it receives None
                        break
                    except queue.Full:
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            pass


class Access:
    """Invite keys: edit and view. With no keys it is open local mode (everyone is edit).

    open (open mode) is decided **once, at construction**. Even if reload() receives an empty key it does not switch to open mode (all key requests
    are then rejected), which stops an empty edit_key from leaking as 'everyone can edit' (an Access-mode trap)."""

    def __init__(self, edit_key=None, view_key=None, site="local", expires_at=0.0, open=None):
        self.edit_key, self.view_key = edit_key or None, view_key or None
        self.cookie = "tb_" + re.sub(r"[^A-Za-z0-9]", "", site)[:24]
        self.cookie_secure = "__Host-" + self.cookie   # name used over https (public Host): Secure, Path=/, no Domain
        self.expires_at = float(expires_at or 0)
        self.keys = {k: v for k, v in (("edit", self.edit_key), ("view", self.view_key)) if v}
        self._open = (not self.edit_key) if open is None else bool(open)
        self._lock = threading.Lock()
        self.listeners = []   # called by bump(): fn(predicate). The server attaches store.close_streams
        self.gen = 0          # +1 whenever permissions change; handlers use it to invalidate the auth-chain answer cache
        self.agents = ()      # agent tokens: (sha256, entry) pairs from state.json agents. No plaintext is kept
        self.agents_local_only = False   # personal / Access mode: tokens are accepted only from requests inside this Mac (loopback)

    @property
    def open(self):
        return self._open

    def role_of(self, key):
        if self.open:
            return "edit"
        ek, vk = self.edit_key, self.view_key   # compare against one consistent snapshot even if a reload overlaps
        kb = str(key).encode("utf-8", "replace") if key else b""   # as bytes, so a non-ASCII key cannot kill the handler thread with a TypeError
        if kb and ek and hmac.compare_digest(kb, ek.encode("utf-8")):
            return "edit"
        if kb and vk and hmac.compare_digest(kb, vk.encode("utf-8")):
            return "view"
        return None

    def bump(self, predicate=None):
        """Announce that permissions changed. Invalidate all auth-chain answer caches (gen) and call listeners(predicate).
        No predicate = close all open WebSocket / SSE streams and wake long-polls (key rotation).
        With a predicate(handler) = close only those streams. Agent-token revocation is used like `access.bump(lambda h: not h.revalidate())`."""
        with self._lock:
            self.gen += 1
        for fn in list(self.listeners):
            try:
                fn(predicate)
            except Exception as e:   # noqa: BLE001 — so a failed close does not block the reload
                print("access listener:", e, file=sys.stderr)

    def load_agents(self, state):
        """Load agents from state.json (agent tokens, sha256 only). Expired ones are skipped. True if anything changed."""
        t = time.time()
        rows = []
        for a in state.get("agents") or []:
            if not isinstance(a, dict) or not isinstance(a.get("sha256"), str) or len(a["sha256"]) != 64:
                continue
            try:
                exp = float(a.get("expires_at") or 0)
            except (TypeError, ValueError):
                continue
            if exp and exp <= t:
                continue
            rows.append((a["sha256"], {"id": str(a.get("id") or a["sha256"][:8])[:24], "name": clean_agent_name(a.get("name")),
                                       "role": "view" if a.get("role") == "view" else "edit", "expires_at": exp}))
        local_only = state.get("mode") == "personal" or bool(state.get("access"))
        rows = tuple(sorted(rows, key=lambda r: r[0]))
        with self._lock:
            changed = rows != self.agents or local_only != self.agents_local_only
            self.agents, self.agents_local_only = rows, local_only
        return changed

    def agent_auth(self, token, is_local):
        """Bearer tb_ag_... -> Auth. An unknown, revoked or expired token is denied (401; it does not fall through to the invite key / cookie).
        In personal / Access mode, a request from outside this Mac gets None (the token is not even looked at; remote agents use an Access service token)."""
        if self.agents_local_only and not is_local:
            return None
        dig = hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()
        hit = None
        for h, a in self.agents:   # compare against all of them (so timing does not reveal which slot matched)
            if hmac.compare_digest(h, dig):
                hit = a
        if hit is None or (hit["expires_at"] and hit["expires_at"] <= time.time()):
            return Auth(deny=True)
        user = {"id": "agent:" + hit["id"], "name": hit["name"] + AI_SUFFIX, "agent": True}
        return Auth(hit["role"], user, expires_at=hit["expires_at"] or None)

    def reload(self, state):
        """Reload state.json (a dict): keys, expiry and agent tokens. If the keys changed, close all open streams (bump()),
        and if only tokens changed, close only the streams that lost permission (bump(predicate)). Returns True if the keys changed."""
        ek, vk = (state.get("edit_key") or None), (state.get("view_key") or None)
        with self._lock:
            changed = (ek, vk) != (self.edit_key, self.view_key)
            self.edit_key, self.view_key = ek, vk
            self.keys = {k: v for k, v in (("edit", ek), ("view", vk)) if v}
            self.expires_at = float(state.get("expires_at") or 0)
        agents_changed = self.load_agents(state)
        if changed:
            self.bump()
        elif agents_changed:
            self.bump(lambda h: not h.revalidate())   # close only the WebSocket / SSE streams of revoked tokens
        return changed


AGENT_TOKEN_PREFIX = "tb_ag_"


def clean_agent_name(name):
    """Token name: only letters, digits, spaces and -_.(), up to 32 characters. 'Agent' if empty."""
    n = re.sub(r"[^\w\s.()\-가-힣]", "", str(name or ""), flags=re.UNICODE)
    n = re.sub(r"\s+", " ", n).strip()[:32]
    return n or "Agent"


class AgentTokens:
    """Auth-chain entry: `Authorization: Bearer tb_ag_...` (header only; ?token= is not accepted). Any other header gives None (next entry)."""

    def __init__(self, access):
        self.access = access

    def authenticate(self, req):
        h = req.headers.get("Authorization") or ""
        if h[:7].lower() != "bearer ":
            return None
        tok = h[7:].strip()
        if not tok.startswith(AGENT_TOKEN_PREFIX):
            return None
        return self.access.agent_auth(tok, req.is_local_host)


_DENIED_HEAD = """<!doctype html><html lang="%s"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tempboard</title><body style="font:16px/1.6 system-ui,sans-serif;max-width:32rem;margin:18vh auto;padding:0 16px;color:#333">"""
_DENIED_KO = """<h1 style="font-size:20px">초대 링크가 필요해요</h1><p>이 화이트보드는 받은 초대 링크(주소 끝 <code>?k=…</code>)로만 열려요.
링크가 끝났다면(만료 · 종료) 보드를 연 사람에게 새 링크를 받으세요.</p>"""
_DENIED_EN = """<h1 style="font-size:20px">An invite link is required</h1><p>This whiteboard opens only from the invite link you were sent
(the <code>?k=…</code> at the end of the address). If the link has ended (expired or closed), ask whoever opened the board for a fresh one.</p>"""


def denied_page() -> bytes:
    """403 page. The host's language comes first and the other language follows in grey, so a visitor can read it either way."""
    ko_first = T("ko", "en") == "ko"
    first, second = (_DENIED_KO, _DENIED_EN) if ko_first else (_DENIED_EN, _DENIED_KO)
    second = second.replace('<h1 style="font-size:20px">', '<h2 style="font-size:16px">').replace("</h1>", "</h2>")
    other = "en" if ko_first else "ko"
    return (_DENIED_HEAD % ("ko" if ko_first else "en") + first + '<div lang="%s" style="color:#777">' % other + second + "</div></body></html>").encode("utf-8")


def make_handler(store: Store, dist: Path, trust_header: str | None, test_user: str | None = None, access: Access | None = None,
                 hosts: HostAllow | None = None, authn: list | None = None, hsts: bool = False):
    """hosts: Host allow-list (None disables the check; for tests and direct use. build() always supplies it).
    authn: auth chain; each entry's authenticate(Req) is asked in turn and the first non-None Auth is used (if empty, current behavior).
    hsts: add Strict-Transport-Security to responses for the public Host (personal mode)."""
    access = access or Access()
    authn = authn if authn is not None else []
    class H(BaseHTTPRequestHandler):
        server_version = "tempboard/0.1"
        protocol_version = "HTTP/1.1"   # stream SSE as chunked so a front proxy (cloudflared, etc.) does not buffer it until the end

        def log_message(self, fmt, *args):  # silent
            pass

        # ---------- Host and auth chain ----------
        def host_rule(self):
            """The rule this request's Host matched (once per request). None if there are no hosts."""
            if getattr(self, "_rule_for", None) is not self.headers:
                self._rule_for = self.headers
                self._rule = hosts.match(self.headers.get("Host")) if hosts is not None else None
            return self._rule

        def host_ok(self):
            """A Host not on the allow-list gets 421 (/healthz too). Two Host headers, or an absolute-form request target, gets 400. Returns False if handled."""
            if not self.path.startswith("/") or len(self.headers.get_all("Host") or []) > 1:
                self.close_connection = True
                self.send(400, {"error": "bad request"})
                return False
            if hosts is None or self.host_rule() is not None:
                return True
            self.close_connection = True
            self.send(421, {"error": "unknown host"})
            return False

        def is_public(self):
            r = self.host_rule()
            return bool(r and r.public)

        def is_local_host(self):
            """A request from inside this Mac: only if the Host is a loopback name **and the direct peer is also loopback**.
            (With --lan, someone on the same Wi-Fi sending `Host: 127.0.0.1:P` does not become local. cloudflared's peer is
            127.0.0.1, but its Host is the public name, so it is not local.)"""
            if any(self.headers.get(k) is not None for k in CF_EDGE_HEADERS):
                return False   # a request that came through Cloudflare is never local whatever its Host (the cloudflared peer is loopback)
            h, _ = security.split_host(self.headers.get("Host") or "")
            r = self.host_rule()
            peer = self.client_address[0] if self.client_address else ""
            return bool(h and security.is_loopback(h) and security.is_loopback_ip(peer)
                        and (hosts is None or (r is not None and not r.public)))

        def auth(self):
            """The auth chain's answer. Computed once per request (header object), but asked again when permissions change (access.gen) or the answer's expires_at has passed,
            so revocation and expiry also apply to WebSocket / SSE streams that live tens of minutes. None if the chain is empty or all entries return None."""
            cached = getattr(self, "_auth_for", None)
            a = getattr(self, "_auth", None)
            stale = a is not None and a.expires_at is not None and time.time() >= a.expires_at
            if cached is not self.headers or getattr(self, "_auth_gen", None) != access.gen or stale:
                # Compute everything into locals and store it in one step, so that while the revalidate thread is asking again the handler thread
                # does not read an empty value (None) and drop a WebSocket that came in through the chain
                gen, found = access.gen, None
                if authn:
                    req = Req(self.headers, self.headers.get("Host") or "", self.is_local_host(), self.path,
                              self.client_address[0] if self.client_address else "", self.is_public())
                    for item in authn:
                        r = item.authenticate(req)
                        if r is not None:
                            found = r
                            break
                self._auth, self._auth_gen, self._auth_for = found, gen, self.headers
                return found
            return a

        def revalidate(self):
            """Whether permission still exists: drop the auth-chain cache and ask again (for checking open streams). -> bool"""
            self._auth_for = None
            return self.role() is not None

        def denied(self):
            """If the chain denied, send 401 and return True."""
            a = self.auth()
            if a is not None and a.deny:
                self.send(401, {"error": "unauthorized"})
                return True
            return False

        # ---------- invite keys ----------
        def role(self):
            a = self.auth()
            if a is not None:
                return None if a.deny else (a.role if a.role in ("edit", "view") else None)
            if access.open:   # open mode with no keys accepts only a local Host; even if a public Host is added later, it stays blocked
                return None if self.is_public() else "edit"
            # Also work for browsers that block cookies (in-app browsers, Safari tracking prevention): the UI also sends the key in a header (X-Tb-Key) or ?k=
            hk = self.headers.get("X-Tb-Key")
            if hk and access.role_of(hk):
                return access.role_of(hk)
            qk = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("k", [None])[0]
            if qk and access.role_of(qk):
                return access.role_of(qk)
            c = self.cookies()
            for name in (access.cookie_secure, access.cookie):   # https name first, then the local http name
                if name in c and access.role_of(c[name].value):
                    return access.role_of(c[name].value)
            return None

        def cookies(self):
            try:
                return cookies.SimpleCookie(self.headers.get("Cookie", ""))
            except cookies.CookieError:
                return cookies.SimpleCookie()

        def https(self):
            """https is decided by configuration: only requests whose Host matched a public rule. The headers (X-Forwarded-Proto, Cf-Visitor) are not consulted."""
            return self.is_public()

        def cookie(self, name, value, max_age):
            """Set-Cookie value: over https `__Host-<name>` (Secure, Path=/, no Domain); over local http the plain name."""
            if self.https():
                return f"__Host-{name}={value}; Secure; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
            return f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"

        def admit(self, u, q):
            """The link's ?k=: if valid, also plant the cookie and serve the page right away (no redirect, so the page uses the key in the address even when cookies are blocked).
            The page moves the key into tab storage and removes it from the address. Returns True if handled."""
            if access.open or "k" not in q or u.path.startswith("/api/") or self.auth() is not None:
                return False
            key = q["k"][0]
            if not access.role_of(key):
                self.send(403, denied_page(), "text/html; charset=utf-8")
                return True
            age = max(60, int(access.expires_at - time.time())) if access.expires_at else 30 * 86400
            ck = self.cookie(access.cookie, key, age)
            self.static("index.html", extra={"Set-Cookie": ck, "Referrer-Policy": "no-referrer"})
            return True

        # ---------- common ----------
        def user(self):
            if test_user:   # tests only (enabled only on 127.0.0.1): every request is attributed to this person
                return {"id": test_user, "name": test_user}
            a = self.auth()
            if a is not None:
                if a.deny:
                    return None
                if a.user:
                    return a.user
            if trust_header and self.headers.get(trust_header):
                raw = self.headers.get(trust_header)
                try:  # HTTP headers are latin-1: undo UTF-8 Korean names sent that way, and also accept %-encoding
                    raw = raw.encode("latin-1").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                name = human_name(urllib.parse.unquote(raw).strip()[:40])
                return {"id": name, "name": name} if name else None
            c = self.cookies()
            raw = next((c[n].value for n in ("__Host-wb_user", "wb_user") if n in c), None)
            # browsers that block cookies: the UI sends the name in a header (X-Tb-Name) or the WebSocket's ?name=
            raw = raw or self.headers.get("X-Tb-Name") or urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name", [None])[0]
            if raw:
                name = human_name(urllib.parse.unquote(raw).strip()[:40])
                if name:
                    return {"id": name, "name": name}
            return None

        def send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cache-Control", "no-store")
            self.sec_headers()
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def sec_headers(self):
            if hsts and self.is_public():
                self.send_header("Strict-Transport-Security", HSTS)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("too large")
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def import_too_big(self):
            """If an import body exceeds MAX_BODY, read and discard it (up to 64 MB) and send 413 with a readable message. If the body is not read and the
            connection is closed, the sender only sees 'connection dropped'. Returns True if handled."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= MAX_BODY:
                return False
            if n <= 64 * 1024 * 1024:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    left -= len(chunk)
            else:
                self.close_connection = True
            self.send(413, {"error": _importers().too_big_message(n), "limit": MAX_BODY})
            return True

        def guard(self):
            """Write requests are allowed only from the same origin, with the app header, from a logged-in user (CSRF protection).."""
            if self.headers.get("X-Whiteboard") != "1":
                self.send(403, {"error": "missing X-Whiteboard header"})
                return None
            u = self.user()
            if not u:
                self.send(401, {"error": "login required"})
            return u

        # ---------- GET ----------
        def do_GET(self):
            if not self.host_ok() or self.denied():
                return
            u = urllib.parse.urlsplit(self.path)
            path, q = u.path, urllib.parse.parse_qs(u.query)
            if self.admit(u, q):
                return
            if path == "/healthz":
                return self.send(200, {"ok": True})
            if not path.startswith("/api/"):
                return self.static(path.lstrip("/") or "index.html")   # the UI code is public; board data is protected by a key in the APIs below
            role = self.role()
            if not role:
                return self.send(403, {"error": "invite link required"})
            if path == "/api/whiteboard/me":
                usr = self.user()
                if not usr:
                    return self.send(401, {"error": "login required", "role": role})
                color = store.color_for(usr["name"])
                invite = {k: v for k, v in access.keys.items() if role == "edit" or k == "view"}   # do not give the edit key to someone who came in through a view link
                if usr.get("agent"):
                    invite = {}   # agent tokens get no invite keys, so no key lingers after the token is revoked
                return self.send(200, {**usr, "short": short_name(usr["name"]), "color": color, "reserved": dict(store.colors),
                                       "role": role, "expires_at": access.expires_at or None, "invite": invite})
            if not self.user():
                return self.send(401, {"error": "login required"})
            if path == "/api/whiteboard/boards":
                return self.send(200, store.list())
            m = re.match(r"^/api/whiteboard/boards/([a-z0-9]+)(/.*)?$", path)
            if not m:
                return self.send(404, {"error": "not found"})
            b = store.get(m.group(1))
            if not b:
                return self.send(404, {"error": "no board"})
            sub = m.group(2) or ""
            if sub == "":
                with b.lock:
                    els = list(b.elements.values())
                files = sorted(p.name for p in (b.root / "files").glob("*")) if (b.root / "files").is_dir() else []
                return self.send(200, {"meta": b.meta, "elements": els, "files": files, "seq": b.seq, "pver": b.pver, "epoch": EPOCH})
            if sub == "/poll":
                try:
                    since = int((q.get("since") or ["0"])[0])
                except ValueError:
                    since = 0
                try:
                    pver = int((q.get("pver") or ["-1"])[0])
                    saved = int((q.get("saved") or ["-1"])[0])
                except ValueError:
                    pver, saved = -1, -1
                try:   # wait: seconds to wait (0.1-20, default 20); used when an agent's board_wait waits briefly
                    wait = min(20.0, max(0.1, float((q.get("wait") or ["20"])[0])))
                except ValueError:
                    wait = 20.0
                if not math.isfinite(wait):
                    wait = 20.0
                out = b.poll(since, (q.get("client") or [""])[0][:40], pver, (q.get("epoch") or [""])[0][:16], timeout=wait, saved=saved)
                if not self.role():   # the key changed while waiting, or the token / JWT ended: do not hand out drawings made in the meantime
                    return self.send(403, {"error": "invite link required"})
                return self.send(200, out)
            if sub == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
                try:
                    since = int((q.get("since") or ["0"])[0])
                except ValueError:
                    since = 0
                return self.ws(b, (q.get("client") or [""])[0][:40], since, (q.get("epoch") or [""])[0][:16], (q.get("pd") or [""])[0] == "1")
            if sub == "/events":
                return self.events(b, (q.get("client") or [""])[0][:40])
            fm = re.match(r"^/files/([A-Za-z0-9_-]+)$", sub)
            if fm and FILE_ID.match(fm.group(1)):
                p = b.root / "files" / fm.group(1)
                if not p.is_file():
                    return self.send(404, {"error": "no file"})
                return self.send(200, p.read_bytes(), extra={"Cache-Control": "private, max-age=86400"})
            return self.send(404, {"error": "not found"})

        do_HEAD = do_GET

        def static(self, rel, extra=None):
            if ".." in rel.split("/"):
                return self.send(404, b"", "text/plain")
            p = (dist / rel).resolve()
            if not str(p).startswith(str(dist.resolve())) or not p.is_file():
                p = dist / "index.html" if "." not in rel.split("/")[-1] else None
                if p is None or not p.is_file():
                    return self.send(404, b"not found", "text/plain")
            return self.send(200, p.read_bytes(), MIME.get(p.suffix, "application/octet-stream"),
                             extra={"Cache-Control": "public, max-age=3600" if "/assets/" in str(p) else "no-cache", **(extra or {})})

        def presence_update(self, b, client, usr, data, role="edit"):
            b.update_presence(client, usr["name"], store.color_for(usr["name"]), data, role, agent=bool(usr.get("agent")))

        def ws(self, b, client, since, epoch, delta=False):
            """WebSocket: first replay drawing events the client missed (reset if not possible), then push events and presence right away.
            The UI keeps each drawing batch until it gets an ack, and resends it after a disconnect and reconnect."""
            origin = self.headers.get("Origin")
            self.close_connection = True   # do not reuse this connection even if the upgrade fails
            if origin is not None and hosts is not None and not hosts.origin_ok(origin):
                # Browsers always send Origin; if it is not an allowed Host this is cross-origin WebSocket hijacking. Non-browsers (no Origin) only get the key check
                return self.send(403, {"error": "origin"})
            usr = self.user()
            key = self.headers.get("Sec-WebSocket-Key")
            if not usr or not key or not client:
                return self.send(401 if not usr else 400, {"error": "websocket"})
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.sec_headers()
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            self.connection.settimeout(60.0)   # the UI pings every 25 s; 60 s of silence counts as disconnected and is cleaned up (prevents ghost connections)
            conn = WSConn(self)
            conn.delta = delta
            with b.lock:
                old = b.sockets.get(client)
                b.sockets[client] = conn
                if epoch != EPOCH or since > b.seq or (b.log and since < b.log[0][0] - 1):
                    first = {"t": "reset", "epoch": EPOCH, "seq": b.seq, "saved": b.saved_seq, "elements": list(b.elements.values())}   # included so the client does not have to come back for it
                else:
                    evs = [{"seq": n, "event": e, "data": d} for (n, frm, e, d) in b.log if n > since and frm != client]
                    first = {"t": "hello", "epoch": EPOCH, "seq": b.seq, "saved": b.saved_seq, "events": evs}
                pres = {"t": "presence", "pver": b.pver, "data": b.presence_list()}
                conn.send(first)   # under the lock, so a live event does not go out before hello/reset
                conn.send(pres)
            if old is not None and old is not conn:
                old.close()
            self.presence_update(b, client, usr, {"pointer": None}, self.role())
            try:
                while conn.alive:
                    op, payload = conn.recv()
                    if op == 0x8:
                        break
                    if op != 0x1:
                        continue
                    try:
                        msg = json.loads(payload.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    t = msg.get("t")
                    role_now = self.role() if t in ("elements", "presence") else "edit"
                    if role_now is None:
                        break   # permission is gone (key rotation, token revoked or expired): disconnect
                    if t == "elements" and isinstance(msg.get("elements"), list):
                        if role_now != "edit":
                            continue   # view link: drawings are not accepted
                        b.merge(msg["elements"], client)
                        with b.lock:
                            seq_now = b.seq
                        conn.send({"t": "ack", "id": msg.get("id"), "seq": seq_now})
                    elif t == "presence":
                        self.presence_update(b, client, usr, msg, role_now)
                    elif t == "ping":
                        conn.send({"t": "pong"})
            except (OSError, ValueError, struct.error, socket.timeout):
                pass
            finally:
                with b.lock:
                    if b.sockets.get(client) is conn:
                        b.sockets.pop(client, None)
                        b.drop_presence(client)
                conn.close()
                b.presence_changed()

        def events(self, b, client):
            q = queue.Queue(maxsize=500)
            q.owner = self   # so close_streams(predicate) can recheck this stream's permission
            with b.lock:
                b.subs.append((q, client))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "keep-alive")
            self.sec_headers()
            self.end_headers()

            def chunk(data: bytes):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            try:
                chunk(b": " + b" " * 2048 + b"\n\n")   # flush the front-end buffer once
                chunk(f"event: presence\ndata: {json.dumps(b.presence_list(), ensure_ascii=False)}\n\n".encode())
                while True:
                    try:
                        msg = q.get(timeout=15)
                    except queue.Empty:
                        msg = b": ping\n\n"
                    if msg is None:   # close_streams: the key changed. Send the final chunk and close (the UI reconnects)
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                        self.close_connection = True
                        break
                    chunk(msg)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with b.lock:
                    b.subs = [s for s in b.subs if s[0] is not q]
                    b.drop_presence(client)
                b.presence_changed()

        # ---------- POST ----------
        def do_POST(self):
            if not self.host_ok() or self.denied():
                return
            path = urllib.parse.urlsplit(self.path).path
            role = self.role()
            if not role:
                return self.send(403, {"error": "invite link required"})
            if path == "/api/whiteboard/login":  # name from the first screen; use --trust-header if a front end decides the user
                try:
                    name = human_name(str(self.body().get("name", "")).strip()[:40])
                except Exception:
                    name = ""
                if not name:
                    return self.send(400, {"error": "name"})
                return self.send(200, {"id": name, "name": name},
                                 extra={"Set-Cookie": self.cookie("wb_user", urllib.parse.quote(name), 2592000)})
            usr = self.guard()
            if not usr:
                return
            if role != "edit" and not path.endswith("/presence"):
                return self.send(403, {"error": "view-only link"})
            if path.endswith("/import") and self.import_too_big():
                return
            try:
                data = self.body()
            except Exception:
                return self.send(400, {"error": "bad json or too large"})
            if path == "/api/whiteboard/boards":
                b = store.create(str(data.get("title", "")).strip(), usr)
                return self.send(201, b.meta)
            if path == "/api/whiteboard/boards/import":   # import into a new board
                return self.do_import(None, data, "new", usr, client=str(data.get("client", ""))[:40])
            m = re.match(r"^/api/whiteboard/boards/([a-z0-9]+)/(elements|presence|files|title|patch|import)$", path)
            b = store.get(m.group(1)) if m else None
            if not b:
                return self.send(404, {"error": "no board"})
            kind = m.group(2)
            client = str(data.get("client", ""))[:40]
            if kind == "elements":
                els = data.get("elements")
                if not isinstance(els, list):
                    return self.send(400, {"error": "elements"})
                took = b.merge(els, client)
                with b.lock:
                    seq_now = b.seq
                return self.send(200, {"accepted": len(took), "seq": seq_now})
            if kind == "presence":
                self.presence_update(b, client, usr, data, role)
                return self.send(204, b"")
            if kind == "patch":
                changes = data.get("changes")
                if not isinstance(changes, list):
                    return self.send(400, {"error": "changes"})
                return self.send(200, b.patch(changes, client, atomic=data.get("atomic") is True))
            if kind == "files":
                fid, url = str(data.get("id", "")), str(data.get("dataURL", ""))
                if not FILE_ID.match(fid) or not re.match(r"^data:image/(png|jpeg|gif|webp|svg\+xml);base64,", url) or len(url) > MAX_FILE:
                    return self.send(400, {"error": "file"})
                save_file(b, {"id": fid, "mimeType": str(data.get("mimeType", ""))[:40], "dataURL": url}, usr["name"], client)
                return self.send(201, {"id": fid})
            if kind == "import":   # merge: edit permission only (view-only was already rejected with 403 above)
                if data.get("mode", "merge") != "merge":
                    return self.send(400, {"error": T("mode — 이 보드에는 merge 만(새 보드는 POST /boards/import)", "mode - only merge for this board (new boards: POST /boards/import)")})
                return self.do_import(b, data, "merge", usr, client)
            if kind == "title":
                with b.lock:
                    b.meta["title"] = str(data.get("title", "")).strip()[:80] or b.meta.get("title")
                    b.dirty = True
                b.publish("meta", b.meta)
                return self.send(200, b.meta)
            return self.send(404, {"error": "not found"})

        def do_import(self, b, data, mode, usr, client):
            """Import: body {name, content (text or image data URL), title?, at?, client?}. Images are either inside the document (data URL) or
            uploaded beforehand via /files (the document carries only id and mimeType). If b is None, a new board is created (keeping the file's ids)."""
            imp = _importers()
            name, content = str(data.get("name") or "import.excalidraw")[:200], data.get("content")
            agent = re.sub(r"\s*·\s*AI$", "", usr["name"]) if usr.get("agent") else None   # if an agent inserts it, stamp tbBy
            made = None
            try:
                if not isinstance(content, str) or not content:
                    raise imp.ImportFailed(T("content 가 없다", "content is missing"))
                raw = imp.decode_data_url(content)[1] if content.startswith("data:") else content.encode("utf-8")
                parsed = imp.parse(name, raw)
                if b is None:
                    imp.prepare(parsed["elements"], [], mode)   # create the board only after the whole document validates (so no empty board is left behind)
                    title = str(data.get("title") or parsed["title"]).strip()[:80]
                    b = made = store.create(title, usr)
                res = imp.apply(b, parsed, mode, data.get("at"), client, usr["name"], save_file, agent=agent)
            except Exception as e:   # noqa: BLE001 — answer 400 whatever happens (do not drop the connection)
                if made is not None:
                    store.discard(made.id)
                if isinstance(e, imp.ImportFailed):
                    return self.send(400, {"error": str(e)})
                print("import:", type(e).__name__, str(e)[:300], file=sys.stderr)
                return self.send(400, {"error": T(f"가져오지 못했다({type(e).__name__})", f"Import failed ({type(e).__name__})")})
            if mode == "new":
                res["meta"] = b.meta
            return self.send(201 if mode == "new" else 200, res)


    return H


def _importers():
    """Import module: loaded on first use (so server startup stays light)."""
    return _sibling("importers")


def save_file(b, rec, by, client=""):
    """Write one image into the board's files and send a 'file' event. If it already exists, leave it (same id means same image; imports use a content SHA-1 id).
    -> True if newly written. The caller does the checks (FILE_ID, MIME, size)."""
    d = b.root / "files"
    d.mkdir(parents=True, exist_ok=True)
    fid = rec["id"]
    if (d / fid).exists():
        return False
    out = {"id": fid, "mimeType": str(rec.get("mimeType", ""))[:40], "dataURL": rec["dataURL"], "created": int(time.time() * 1000), "by": by}
    (d / fid).write_text(json.dumps(out), encoding="utf-8")
    b.publish("file", {"id": fid}, skip=client)
    return True


class Ctl:
    """Handle that build() attaches to the server (srv.tb); the supervisor loop uses it to reload and to change Hosts."""

    def __init__(self, store, access, hosts, authn):
        self.store, self.access, self.hosts, self.authn = store, access, hosts, authn


def build(data, dist=None, host="127.0.0.1", port=0, trust_header=None, test_user=None, access=None, save_interval=0.5,
          authn=None, hosts=None, hsts=False, revalidate_interval=5.0, agent_tokens=True):
    """Create one server, attach the save thread and return it (the caller runs serve_forever). -> (httpd, store)

    hosts: extra Hosts to allow: a list of HostRule objects or 'host[:port]' strings, or a HostAllow. The three local ones (127.0.0.1, localhost, [::1] on
           this port) are always included. When bound to 0.0.0.0, the LAN addresses are added too. To change later, use httpd.tb.hosts.set(...).
    authn: auth chain (authenticate(req) entries returning security.Auth). If there is a chain, every revalidate_interval seconds the permissions of open
           WebSocket / SSE streams are rechecked and those that lost them are closed. To close immediately, use access.bump(predicate). hsts: HSTS on public Host responses.
    httpd.tb = Ctl(store, access, hosts, authn). If the invite keys change (access.reload), open WebSocket / SSE streams are closed.
    agent_tokens: if there is an invite key, plug agent tokens (AgentTokens) in at the very front of the chain. The token list is loaded by access.reload(state).
           If data is `sites/<id>/data`, it is loaded once from the adjacent state.json (so tokens survive a supervisor restart)."""
    store = Store(Path(data))
    if test_user and host not in ("127.0.0.1", "localhost"):
        raise SystemExit(T("--test-user 는 127.0.0.1 에서만", "--test-user works only on 127.0.0.1"))
    access = access or Access()
    authn = list(authn or [])
    allow = hosts if isinstance(hosts, HostAllow) else HostAllow(hosts or ())
    if access.open and (authn or any(r.public for r in allow.rules)):
        # open mode with no keys is local-Host only: combined with a public Host or an auth chain (Access mode), requests with no key would all become edit
        raise SystemExit(T("초대 키(edit_key) 없이 공개 Host · 인증 고리를 쓸 수 없다", "A public Host or auth chain cannot be used without an invite key (edit_key)"))
    if trust_header:
        print(security.trust_header_warning(), file=sys.stderr)
    if agent_tokens and not access.open:
        authn.insert(0, AgentTokens(access))
        stp = Path(data).parent / "state.json"
        if Path(data).name == "data" and stp.is_file():
            try:
                access.load_agents(json.loads(stp.read_text(encoding="utf-8")))
            except (OSError, ValueError) as e:
                print(T("agents: state.json 을 읽지 못했다 —", "agents: could not read state.json -"), e, file=sys.stderr)
    srv = ThreadingHTTPServer((host, port), make_handler(store, Path(dist or ROOT / "web" / "dist"), trust_header, test_user, access,
                                                         hosts=allow, authn=authn, hsts=hsts))
    srv.daemon_threads = True
    bound = srv.server_address[1]
    base = security.local_rules(bound)
    if host in ("0.0.0.0", "::", ""):
        base += security.lan_rules(bound)   # any IP of this Mac (multiple interfaces, changing IPs) and <name>.local
    elif host not in ("127.0.0.1", "localhost", "::1"):
        base.append(HostRule(f"[{host}]:{bound}" if ":" in host else f"{host}:{bound}"))
    allow.set_base(base)
    access.listeners.append(store.close_streams)
    srv.tb = Ctl(store, access, allow, authn)

    if authn and revalidate_interval:
        def revalidate():   # chain credentials (tokens, JWTs) can expire during a connection, so recheck open streams periodically
            while True:
                time.sleep(revalidate_interval)
                store.close_streams(lambda h: not h.revalidate())
        threading.Thread(target=revalidate, daemon=True).start()

    def flusher():
        while True:
            time.sleep(save_interval)   # keep the gap before the save confirmation (saved) short
            store.flush()
    threading.Thread(target=flusher, daemon=True).start()
    return srv, store


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="./tempboard-data")
    ap.add_argument("--dist", default=str(ROOT / "web" / "dist"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--edit-key", default=None, help=T("편집 초대 키. 주면 키 없는 요청은 막힌다", "Edit invite key. If given, requests without a key are blocked"))
    ap.add_argument("--view-key", default=None, help=T("보기 전용 초대 키(선택)", "View-only invite key (optional)"))
    ap.add_argument("--trust-header", default=None, help=T("앞단이 넣는 사용자 머리(예: X-User). 없으면 첫 화면에서 이름을 받는다. 위조될 수 있다", "User header set by a front proxy (e.g. X-User). Without it, the first screen asks for a name. Can be forged"))
    ap.add_argument("--allow-host", action="append", default=[], metavar="HOST",
                    help=T("더 받을 Host(여러 번). 'board.example.com'·'https://…' 는 https, 'http://localhost:5173' 은 http 로 본다",
                      "Extra Hosts to accept (repeatable). 'board.example.com' and 'https://…' are treated as https, 'http://localhost:5173' as http"))
    ap.add_argument("--save-interval", type=float, default=0.5, help=T("디스크 저장 간격(초)", "Disk save interval (seconds)"))
    ap.add_argument("--test-user", default=None, help=T("시험 전용 — 모든 요청을 이 사람으로(127.0.0.1 에서만)", "For tests only - treat every request as this user (127.0.0.1 only)"))
    a = ap.parse_args()
    if a.host not in ("127.0.0.1", "localhost") and not a.edit_key:
        raise SystemExit(T("127.0.0.1 밖으로 열 때는 --edit-key 가 필요하다", "--edit-key is required when listening beyond 127.0.0.1"))
    try:
        extra = [HostRule.parse(h) for h in a.allow_host]
    except ValueError as e:
        raise SystemExit(str(e))
    srv, store = build(a.data, a.dist, a.host, a.port, a.trust_header, a.test_user,
                       Access(a.edit_key, a.view_key, site=str(a.port)), a.save_interval, hosts=extra)
    k = f"?k={a.edit_key}" if a.edit_key else ""
    print(T(f"화이트보드: http://{a.host}:{srv.server_address[1]}/{k}  (자료 {a.data})", f"Whiteboard: http://{a.host}:{srv.server_address[1]}/{k}  (data {a.data})"))
    try:
        srv.serve_forever()
    finally:
        store.flush()


if __name__ == "__main__":
    main()
