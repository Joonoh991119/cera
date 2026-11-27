# SPDX-License-Identifier: MIT
"""Import — converts .excalidraw, .excalidrawlib and images (png, jpeg, gif, webp, svg) into board elements.

Three entry points use this one module:
  - server `POST /api/whiteboard/boards/import` (new board) and `POST /api/whiteboard/boards/:id/import` (merge) → parse + apply
  - CLI  `tempboard import <site> <file> [--board ID | --new [--title T]] [--at x,y] [--json]` (register)
  - MCP  `board_import` (tb_mcp → read_path + parse + push)

Flow
  parse(name, bytes)  → {kind, title, elements (clean, all fields), files{id: rec}, skipped{reason: count}}
      · type whitelist (unknown types and broken elements are dropped and counted in skipped)
      · an image file must have a data URL MIME that matches its file header (magic), and its id is reassigned as the SHA-1 of its content
          (so another file's id cannot collide with a different image id on the board and show the wrong image). The element's fileId is changed to match.
      · a library gets fresh ids per item and is laid out in a grid (ids may collide between items).
  prepare(elements, existing, mode, at)
      · "merge": all ids are reassigned — containerId, boundElements, start/endBinding, groupIds and frameId via the same mapping table.
          A whiteboard page (customData.tbPage) gets the board's next page number. The group is repositioned (default: right of existing drawings; with at={x,y}, that point is the group center) and
          index (z-order) is reassigned above the existing drawings (original order kept). version starts at 1.
      · "new": id and version are kept as is (an empty new board). If index is broken, it is reassigned in the original order.
  apply(board, parsed, mode, at, client, by, save_file) — for the server and for a closed site (CLI). Under the board lock: files first, then elements (2000 at a time).
  push(req, parsed, mode, …) — for HTTP clients (CLI, MCP). Images go first, one at a time, to /files (after creating the board if new);
        elements go to /import as a document without the images — so even with many images, no single request exceeds 12 MB.

Standard library only; importable on Python 3.9.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import stat
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent


def _sibling(name):
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


ak = _sibling("agentkit")


def T(ko, en):
    return _sibling("i18n").T(ko, en)

MAX_IMPORT_BYTES = 50 * 1024 * 1024   # one file to import (a file read by the CLI / MCP)
MAX_FILE = 10 * 1024 * 1024           # one image (data URL length) — same as the server /files
MAX_ELEMENTS = 20000                  # elements imported at once
MAX_FILES = 500
MAX_LIB_ITEMS = 1000
MAX_POINTS = 100000                   # points in one line / handwriting stroke
MAX_TEXT = 20000
MAX_CUSTOM = 4096                     # customData JSON bytes
CHUNK = 2000                          # one server merge (MAX_ELEMENTS_PER_POST)
MAX_BODY = 12 * 1024 * 1024           # server MAX_BODY (one request) — measured on the whole body to send (UTF-8, after re-encoding the JSON)
SCENE_MIME = "application/vnd.excalidraw+json"   # marker that a scene is embedded in an image (PNG tEXt / SVG comment) (Excalidraw 'embed scene' export)
CUSTOM_KEYS = {"latex": "str", "size": "num", "color": "color", "w0": "num", "h0": "num", "tbPage": "int", "tbBg": "bool"}
# customData keeps only the fields tempboard uses (formula, whiteboard page, background). tbBy (who drew it) is not accepted; if an agent supplies it, the server stamps it again
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
BOARD_ID = re.compile(r"^[a-z0-9]{8,32}$")

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
             ".svg": "image/svg+xml"}
DOC_EXT = (".excalidraw", ".excalidrawlib", ".json")
ALLOWED_EXT = tuple(DOC_EXT) + tuple(IMAGE_EXT)
IMAGE_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")
DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|gif|webp|svg\+xml));base64,([A-Za-z0-9+/=\s]*)$")

TYPES = ("rectangle", "ellipse", "diamond", "text", "arrow", "line", "freedraw", "image", "frame")
# Excluded types: embeddable, iframe (shows someone else's URL inside the screen), magicframe (AI feature), selection
FILL = ("hachure", "cross-hatch", "solid", "zigzag")
STROKE = ("solid", "dashed", "dotted")
ALIGN = ("left", "center", "right")
VALIGN = ("top", "middle", "bottom")
ARROWHEADS = (None, "arrow", "bar", "dot", "circle", "circle_outline", "triangle", "triangle_outline", "diamond",
              "diamond_outline", "crowfoot_one", "crowfoot_many", "crowfoot_one_or_many")
LINK_OK = re.compile(r"^(https?://|mailto:|#|/(?![/\\]))", re.IGNORECASE)   # '//host' and '/\host' are someone else's address, so they are excluded
COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|transparent|[a-zA-Z]{3,20}|rgba?\([\d.,\s%]{5,40}\)|hsla?\([\d.,\s%deg]{5,40}\))$")
REASONS_EN = {
    "type": "unknown type", "broken": "broken element", "deleted": "deleted element", "dup_id": "duplicate id", "too_many": "count limit",
    "file": "broken image file", "image_no_file": "image without a file", "big_file": "oversized image", "lib_item": "broken library item",
    "files_too_many": "image count limit",
}
REASONS = {
    "type": "모르는 유형", "broken": "깨진 요소", "deleted": "지운 요소", "dup_id": "겹친 id", "too_many": "개수 상한",
    "file": "깨진 그림 파일", "image_no_file": "그림 파일 없는 그림", "big_file": "너무 큰 그림", "lib_item": "깨진 라이브러리 항목",
    "files_too_many": "그림 수 상한",
}


class ImportFailed(ValueError):
    """Import failure (a short message shown to people)."""


class ImportTooBig(ImportFailed):
    """An image over the size limit (counted separately as big_file)."""


# ────────────────────────────────────────────── Image files
def sniff_mime(data: bytes) -> Optional[str]:
    """Image MIME as determined from the file header (magic). None if unknown."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<svg") or ((head.startswith(b"<?xml") or head.startswith(b"<!--") or head.startswith(b"<!doctype svg"))
                                    and b"<svg" in data[:8192].lower()):
        return "image/svg+xml"
    return None


def image_size(data: bytes, mime: str) -> Optional[Tuple[float, float]]:
    """Image size (px) from the header. None if unknown."""
    try:
        if mime == "image/png" and data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if mime == "image/gif" and data[:4] == b"GIF8":
            return struct.unpack("<HH", data[6:10])
        if mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if kind == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return 1 + (b & 0x3FFF), 1 + ((b >> 14) & 0x3FFF)
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
        if mime == "image/jpeg" and data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                ln = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + ln
        if mime == "image/svg+xml":
            head = data[:8192].decode("utf-8", "replace")
            mw = re.search(r'<svg[^>]*\swidth="([\d.]+)(px)?"', head)
            mh = re.search(r'<svg[^>]*\sheight="([\d.]+)(px)?"', head)
            if mw and mh:
                return float(mw.group(1)), float(mh.group(1))
            vb = re.search(r'viewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', head)
            if vb:
                return float(vb.group(1)), float(vb.group(2))
    except (struct.error, ValueError, IndexError):
        return None
    return None


def file_record(raw: bytes, mime: Optional[str] = None) -> dict:
    """Image bytes → {id (SHA-1), mimeType, dataURL}. ImportFailed if the header is not an image, the MIME differs from the header, or it is too large."""
    real = sniff_mime(raw)
    if real is None:
        raise ImportFailed(T("그림 파일이 아니다(png · jpeg · gif · webp · svg 만)", "Not an image file (only png, jpeg, gif, webp, svg)"))
    if mime is not None and mime != real:
        raise ImportFailed(T(f"그림 형식이 맞지 않는다({mime} 라고 했지만 {real})", f"Image format does not match (declared {mime}, but it is {real})"))
    url = f"data:{real};base64," + base64.b64encode(raw).decode()
    if len(url) > MAX_FILE:
        raise ImportTooBig(T("그림이 너무 크다(하나에 약 7.5 MB 까지)", "The image is too large (about 7.5 MB each)"))
    return {"id": hashlib.sha1(raw).hexdigest(), "mimeType": real, "dataURL": url}


def decode_data_url(url) -> Tuple[str, bytes]:
    """data:image/…;base64,… → (mime, bytes). Checks format, length, base64 and header (magic)."""
    if not isinstance(url, str) or len(url) > MAX_FILE:
        raise (ImportTooBig(T("그림이 너무 크다", "The image is too large")) if isinstance(url, str)
               else ImportFailed(T("그림 data URL 이 없다", "The image data URL is missing")))
    m = DATA_URL.match(url)
    if not m:
        raise ImportFailed(T("그림 data URL 형식이 아니다(png · jpeg · gif · webp · svg 만)", "Not an image data URL (only png, jpeg, gif, webp, svg)"))
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
    except (binascii.Error, ValueError):
        raise ImportFailed(T("그림 base64 가 깨졌다", "The image base64 is corrupt"))
    return m.group(1), raw


# ────────────────────────────────────────────── Element cleanup
def _fin(v, default=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    v = float(v)
    return v if math.isfinite(v) else default


def _clamp(v, lo, hi, default):
    f = _fin(v)
    return default if f is None else min(hi, max(lo, f))


def _color(v, default):
    return v if isinstance(v, str) and COLOR.match(v.strip()) else default


def _sid(v) -> Optional[str]:
    return v if isinstance(v, str) and 0 < len(v) <= 64 else None


def _points(v) -> Optional[list]:
    if not isinstance(v, list) or len(v) > MAX_POINTS:
        return None
    out = []
    for p in v:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return None
        x, y = _fin(p[0]), _fin(p[1])
        if x is None or y is None:
            return None
        out.append([x, y])
    return out


def _binding(v) -> Optional[dict]:
    if not isinstance(v, dict) or not _sid(v.get("elementId")):
        return None
    out = {"elementId": v["elementId"]}
    for k in ("focus", "gap"):
        out[k] = _clamp(v.get(k), -1e6, 1e6, 0.0)
    fp = v.get("fixedPoint")
    if isinstance(fp, (list, tuple)) and len(fp) == 2 and _fin(fp[0]) is not None and _fin(fp[1]) is not None:
        out["fixedPoint"] = [float(fp[0]), float(fp[1])]
    elif fp is None and "fixedPoint" in v:
        out["fixedPoint"] = None
    return out


def _custom(v) -> Optional[dict]:
    """customData — only allowed fields and formats (no deep structures survive)."""
    if not isinstance(v, dict) or not v:
        return None
    out = {}
    for k, kind in CUSTOM_KEYS.items():
        x = v.get(k)
        if kind == "str" and isinstance(x, str) and len(x) <= MAX_CUSTOM:
            out[k] = x
        elif kind == "num" and _fin(x) is not None and abs(x) <= 1e7:
            out[k] = float(x)
        elif kind == "int" and _fin(x) is not None and float(x).is_integer() and 0 < x < 1e6:
            out[k] = int(x)
        elif kind == "color" and isinstance(x, str) and COLOR.match(x.strip()):
            out[k] = x.strip()
        elif kind == "bool" and isinstance(x, bool):
            out[k] = x
    return out or None


def _pt(v) -> Optional[list]:
    if isinstance(v, (list, tuple)) and len(v) == 2 and _fin(v[0]) is not None and _fin(v[1]) is not None:
        return [float(v[0]), float(v[1])]
    return None


def _fixed_segments(v) -> Optional[list]:
    """Fixed segments of an elbow arrow — only [{start:[x,y], end:[x,y], index:int}]. None if the format differs."""
    if not isinstance(v, list) or len(v) > 1000:
        return None
    out = []
    for seg in v:
        if not isinstance(seg, dict):
            return None
        a, b, i = _pt(seg.get("start")), _pt(seg.get("end")), seg.get("index")
        if a is None or b is None or isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < 100000:
            return None
        out.append({"start": a, "end": b, "index": i})
    return out


CROP_KEYS = ("x", "y", "width", "height", "naturalWidth", "naturalHeight")


def _crop(v) -> Optional[dict]:
    if not isinstance(v, dict) or any(_fin(v.get(k)) is None for k in CROP_KEYS):
        return None
    return {k: float(v[k]) for k in CROP_KEYS}


def clean_element(e) -> Tuple[Optional[dict], Optional[str]]:
    """One element made by someone else → (full-field element, None) or (None, reason dropped). Fills in the same defaults as Excalidraw 0.18 restore
    (the live receive path of an open screen does not go through restoreElements, so any empty field would become NaN)."""
    if not isinstance(e, dict):
        return None, "broken"
    t = e.get("type")
    if t not in TYPES:
        return None, "type"
    if e.get("isDeleted") is True:
        return None, "deleted"
    eid = _sid(e.get("id"))
    x, y = _fin(e.get("x")), _fin(e.get("y"))
    if eid is None or x is None or y is None or abs(x) > 1e7 or abs(y) > 1e7:
        return None, "broken"
    w, h = _fin(e.get("width"), 0.0), _fin(e.get("height"), 0.0)
    out = {
        "id": eid, "type": t, "x": x, "y": y, "width": min(abs(w), 1e7), "height": min(abs(h), 1e7),
        "angle": _clamp(e.get("angle"), -1e3, 1e3, 0.0),
        "strokeColor": _color(e.get("strokeColor"), ak.INK), "backgroundColor": _color(e.get("backgroundColor"), "transparent"),
        "fillStyle": e.get("fillStyle") if e.get("fillStyle") in FILL else "solid",
        "strokeWidth": _clamp(e.get("strokeWidth"), 0, 100, 2), "strokeStyle": e.get("strokeStyle") if e.get("strokeStyle") in STROKE else "solid",
        "roughness": _clamp(e.get("roughness"), 0, 2, 0), "opacity": _clamp(e.get("opacity"), 0, 100, 100),
        "groupIds": [g for g in (e.get("groupIds") or []) if _sid(g)][:20] if isinstance(e.get("groupIds"), list) else [],
        "frameId": _sid(e.get("frameId")), "index": e.get("index") if isinstance(e.get("index"), str) else None,
        "roundness": None, "seed": int(_clamp(e.get("seed"), 1, 2 ** 31 - 1, 1)) or 1,
        "version": int(_clamp(e.get("version"), 1, 2 ** 31 - 1, 1)), "versionNonce": int(_clamp(e.get("versionNonce"), 0, 2 ** 31 - 1, 0)),
        "isDeleted": False, "boundElements": None, "updated": int(_clamp(e.get("updated"), 0, 1e14, time.time() * 1000)),
        "link": None, "locked": e.get("locked") is True,
    }
    rd = e.get("roundness")
    if isinstance(rd, dict) and isinstance(rd.get("type"), int) and not isinstance(rd.get("type"), bool) and 1 <= rd["type"] <= 3:
        out["roundness"] = {"type": rd["type"]}
        if _fin(rd.get("value")) is not None:
            out["roundness"]["value"] = float(rd["value"])
    link = e.get("link")
    if isinstance(link, str) and len(link) <= 2000 and LINK_OK.match(link.strip()):
        out["link"] = link.strip()
    be = e.get("boundElements")
    if isinstance(be, list):
        out["boundElements"] = [{"id": b["id"], "type": b["type"]} for b in be
                                if isinstance(b, dict) and _sid(b.get("id")) and b.get("type") in ("text", "arrow")][:200] or None
    cd = _custom(e.get("customData"))
    if cd:
        out["customData"] = cd

    if t == "text":
        text = e.get("text")
        if not isinstance(text, str):
            return None, "broken"
        text = text[:MAX_TEXT]
        orig = e.get("originalText") if isinstance(e.get("originalText"), str) else text
        ff = e.get("fontFamily")
        ff = int(ff) if isinstance(ff, (int, float)) and not isinstance(ff, bool) and 1 <= ff <= 64 and float(ff).is_integer() else ak.DEFAULT_FONT
        fs = _clamp(e.get("fontSize"), 1, 1000, 20)
        out.update({"text": text, "originalText": orig[:MAX_TEXT], "fontSize": fs, "fontFamily": ff,
                    "textAlign": e.get("textAlign") if e.get("textAlign") in ALIGN else "left",
                    "verticalAlign": e.get("verticalAlign") if e.get("verticalAlign") in VALIGN else "top",
                    "containerId": _sid(e.get("containerId")), "autoResize": e.get("autoResize") is not False,
                    "lineHeight": _clamp(e.get("lineHeight"), 0.5, 5, ak.line_height(ff))})
        if out["width"] <= 0 or out["height"] <= 0:
            mw, mh = ak.measure_text(text, fs, ff)
            out["width"], out["height"] = out["width"] or mw, out["height"] or mh
    elif t in ("arrow", "line"):
        pts = _points(e.get("points"))
        if not pts or len(pts) < 2:
            return None, "broken"
        out.update({"points": pts, "lastCommittedPoint": None, "startBinding": _binding(e.get("startBinding")),
                    "endBinding": _binding(e.get("endBinding")),
                    "startArrowhead": e.get("startArrowhead") if e.get("startArrowhead") in ARROWHEADS else None,
                    "endArrowhead": e.get("endArrowhead") if e.get("endArrowhead") in ARROWHEADS else (
                        "arrow" if t == "arrow" and "endArrowhead" not in e else None)})
        if t == "arrow":
            out["elbowed"] = e.get("elbowed") is True
            if out["elbowed"]:
                out["fixedSegments"] = _fixed_segments(e.get("fixedSegments"))
                out["startIsSpecial"] = e.get("startIsSpecial") is True
                out["endIsSpecial"] = e.get("endIsSpecial") is True
        else:
            out["polygon"] = e.get("polygon") is True
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out["width"], out["height"] = max(xs) - min(xs), max(ys) - min(ys)
    elif t == "freedraw":
        pts = _points(e.get("points"))
        if not pts:
            return None, "broken"
        pr = e.get("pressures")
        pr = [float(p) for p in pr if _fin(p) is not None] if isinstance(pr, list) and len(pr) <= MAX_POINTS else []
        out.update({"points": pts, "pressures": pr if len(pr) == len(pts) else [], "simulatePressure": e.get("simulatePressure") is not False
                    if len(pr) == len(pts) else True, "lastCommittedPoint": None})
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out["width"], out["height"] = max(xs) - min(xs), max(ys) - min(ys)
    elif t == "image":
        fid = e.get("fileId")
        if not isinstance(fid, str) or not FILE_ID.match(fid):
            return None, "image_no_file"
        sc = e.get("scale")
        scale = [float(sc[0]), float(sc[1])] if isinstance(sc, (list, tuple)) and len(sc) == 2 and all(
            _fin(s) is not None and abs(s) == 1 for s in sc) else [1, 1]
        crop = _crop(e.get("crop"))
        out.update({"fileId": fid, "status": "saved", "scale": scale, "crop": crop})
        if out["width"] <= 0 or out["height"] <= 0:
            return None, "broken"
    elif t == "frame":
        nm = e.get("name")
        out["name"] = nm[:200] if isinstance(nm, str) else None
    return out, None


def _fix_refs(els: List[dict]) -> None:
    """Fix references within the group (in place) — removes containerId, bindings, frameId and boundElements that point to missing elements,
    and makes text ↔ box and arrow ↔ box pairs consistent on both sides. Result: no reference points outside the group (0 orphans)."""
    by = {e["id"]: e for e in els}
    for e in els:
        if e.get("frameId") and (e["frameId"] not in by or by[e["frameId"]]["type"] != "frame"):
            e["frameId"] = None
        if e["type"] == "text" and e.get("containerId"):
            c = by.get(e["containerId"])
            if c is None or c["type"] in ("text", "frame", "freedraw", "image", "line"):
                e["containerId"] = None
        if e["type"] in ("arrow", "line"):
            for k in ("startBinding", "endBinding"):
                b = e.get(k)
                if b and (b["elementId"] not in by or by[b["elementId"]]["type"] in ("arrow", "line", "freedraw")
                          or b["elementId"] == e["id"]):
                    e[k] = None
    # rebuild boundElements from the actual pairs (original order first)
    want: Dict[str, List[dict]] = {}
    for e in els:
        if e["type"] == "text" and e.get("containerId"):
            want.setdefault(e["containerId"], []).append({"id": e["id"], "type": "text"})
        if e["type"] == "arrow":
            for k in ("startBinding", "endBinding"):
                b = e.get(k)
                if b:
                    want.setdefault(b["elementId"], []).append({"id": e["id"], "type": "arrow"})
    for e in els:
        w = want.get(e["id"], [])
        seen, out = set(), []
        for b in (e.get("boundElements") or []) + w:
            if b["id"] in seen or b not in w:
                continue
            seen.add(b["id"])
            out.append(b)
        e["boundElements"] = out or None


def clean_elements(raw, skipped: Dict[str, int], seen_ids: Optional[set] = None) -> List[dict]:
    """Clean up the element list — filter by limits, types, broken elements and duplicate ids, and fix references."""
    out, seen = [], seen_ids if seen_ids is not None else set()
    if not isinstance(raw, list):
        raise ImportFailed(T("elements 가 목록이 아니다", "elements is not a list"))
    for i, e in enumerate(raw):
        if len(out) >= MAX_ELEMENTS:
            skipped["too_many"] = skipped.get("too_many", 0) + (len(raw) - i)
            break
        el, why = clean_element(e)
        if el is None:
            if why != "deleted":
                skipped[why] = skipped.get(why, 0) + 1
            continue
        if el["id"] in seen:
            skipped["dup_id"] = skipped.get("dup_id", 0) + 1
            continue
        seen.add(el["id"])
        out.append(el)
    _fix_refs(out)
    return out


# ────────────────────────────────────────────── Readers per format
def _title_from(name: str) -> str:
    stem = Path(str(name or "")).name
    for ext in ALLOWED_EXT:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.strip()[:80] or "가져온 보드"


def _from_excalidraw(doc: dict, skipped: Dict[str, int]) -> Tuple[List[dict], Dict[str, dict]]:
    files, remap = {}, {}
    raw_files = doc.get("files") if isinstance(doc.get("files"), dict) else {}
    for i, (fid, rec) in enumerate(raw_files.items()):
        if i >= MAX_FILES:
            skipped["files_too_many"] = skipped.get("files_too_many", 0) + (len(raw_files) - i)
            break
        if not isinstance(rec, dict):
            skipped["file"] = skipped.get("file", 0) + 1
            continue
        if rec.get("dataURL") is None:   # a document whose images were uploaded separately (push) — keep only the id (the server checks whether it is on the board)
            if isinstance(fid, str) and FILE_ID.match(fid):
                remap[fid] = fid
            continue
        try:
            mime, raw = decode_data_url(rec.get("dataURL"))
            fr = file_record(raw, mime)
        except ImportFailed as e:
            kind = "big_file" if isinstance(e, ImportTooBig) else "file"
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        remap[fid] = fr["id"]
        files[fr["id"]] = fr
    els = []
    raw_els = doc.get("elements")
    if not isinstance(raw_els, list):
        raise ImportFailed(T("elements 가 없다", "elements is missing"))
    for e in raw_els:
        if isinstance(e, dict) and e.get("type") == "image" and not e.get("isDeleted"):
            fid = e.get("fileId")
            if fid in remap:
                e = dict(e, fileId=remap[fid])
            elif isinstance(fid, str) and fid in raw_files:   # listed in the document but a broken image → drop that image element too
                skipped["image_no_file"] = skipped.get("image_no_file", 0) + 1
                continue
        els.append(e)
    return clean_elements(els, skipped), files


def _from_library(doc: dict, skipped: Dict[str, int]) -> List[dict]:
    """.excalidrawlib v1 (library: [[elements…]…]) · v2 (libraryItems: [{elements}…]) → fresh ids, a group and a grid cell per item."""
    if isinstance(doc.get("libraryItems"), list):
        items = [it.get("elements") if isinstance(it, dict) else None for it in doc["libraryItems"]]
    elif isinstance(doc.get("library"), list):
        items = list(doc["library"])
    else:
        raise ImportFailed(T("라이브러리 항목이 없다", "No library items"))
    if len(items) > MAX_LIB_ITEMS:
        skipped["too_many"] = skipped.get("too_many", 0) + len(items) - MAX_LIB_ITEMS
        items = items[:MAX_LIB_ITEMS]
    groups = []
    total = 0
    for it in items:
        if not isinstance(it, list) or not it:
            skipped["lib_item"] = skipped.get("lib_item", 0) + 1
            continue
        if total >= MAX_ELEMENTS:
            skipped["too_many"] = skipped.get("too_many", 0) + len(it)
            continue
        sub = {}
        els = clean_elements(it, sub, set())
        for k, v in sub.items():
            skipped[k] = skipped.get(k, 0) + v
        els = [e for e in els if e["type"] != "image"]   # a library has no image files
        if not els:
            skipped["lib_item"] = skipped.get("lib_item", 0) + 1
            continue
        els = remap_ids(els)
        if len(els) > 1:
            g = ak.new_id()
            for e in els:
                e["groupIds"] = list(e.get("groupIds") or []) + [g]
        groups.append(els)
        total += len(els)
    # grid: cell width = widest item, ceil(sqrt(n)) items per row
    if not groups:
        return []
    boxes = [ak.scene_bbox(g) or (0, 0, 0, 0) for g in groups]
    cw = max(b[2] - b[0] for b in boxes) + 40
    ch = max(b[3] - b[1] for b in boxes) + 40
    cols = max(1, math.ceil(math.sqrt(len(groups))))
    out = []
    for i, (g, b) in enumerate(zip(groups, boxes)):
        dx, dy = (i % cols) * cw - b[0], (i // cols) * ch - b[1]
        for e in g:
            e["x"] += dx
            e["y"] += dy
        out.extend(g)
    return out


def _from_image(name: str, data: bytes) -> Tuple[List[dict], Dict[str, dict]]:
    ext_mime = IMAGE_EXT.get(Path(str(name)).suffix.lower())
    fr = file_record(data)
    if ext_mime and ext_mime != fr["mimeType"]:
        raise ImportFailed(T(f"확장자와 그림 형식이 다르다({Path(str(name)).suffix} 인데 {fr['mimeType']})",
                         f"The extension and the image format differ ({Path(str(name)).suffix} but {fr['mimeType']})"))
    w, h = image_size(data, fr["mimeType"]) or (400, 300)
    w, h = float(w) or 400.0, float(h) or 300.0
    s = min(1.0, 1000.0 / max(w, h))   # large photos are scaled so the long side is 1000
    el = ak.make_image(fr["id"], w * s, h * s, 0, 0)
    return [el], {fr["id"]: fr}


def loads(text):
    """json.loads — a deeply nested document (a bomb) raises ImportFailed instead of RecursionError."""
    try:
        return json.loads(text)
    except RecursionError:
        raise ImportFailed(T("문서가 너무 깊게 중첩돼 있다(깨졌거나 이상한 파일)", "The document is nested too deeply (a corrupt or odd file)"))
    except ValueError:
        raise ImportFailed(T("읽을 수 없는 파일이다(.excalidraw · .excalidrawlib · 그림만)", "Unreadable file (only .excalidraw, .excalidrawlib or images)"))


def _decode_payload(text: str) -> Optional[dict]:
    """Excalidraw payload ({version, encoding:"bstring", compressed, encoded} or the scene JSON as is) → scene document."""
    j = loads(text)
    if isinstance(j, dict) and "encoded" in j:
        if j.get("encoding") != "bstring" or not isinstance(j.get("encoded"), str):
            raise ImportFailed(T("그림 안 장면의 인코딩을 모른다", "Unknown encoding of the scene inside the image"))
        try:
            raw = j["encoded"].encode("latin-1")
            if j.get("compressed"):
                d = zlib.decompressobj()
                raw = d.decompress(raw, MAX_IMPORT_BYTES + 1)
                if len(raw) > MAX_IMPORT_BYTES:
                    raise ImportFailed(T("그림 안 장면이 너무 크다(50 MB 까지)", "The scene inside the image is too large (up to 50 MB)"))
            j = loads(raw.decode("utf-8"))
        except (UnicodeError, zlib.error):
            raise ImportFailed(T("그림 안 장면이 깨졌다", "The scene inside the image is corrupt"))
    return j if isinstance(j, dict) and j.get("type") == "excalidraw" else None


def has_embedded_scene(data: bytes, mime: Optional[str] = None) -> bool:
    """Does the PNG tEXt / SVG comment carry an Excalidraw scene marker? (quick check — does not decode it)"""
    mime = mime or sniff_mime(data)
    if mime == "image/png":
        return any(k == SCENE_MIME for k, _ in _png_texts(data))
    if mime == "image/svg+xml":
        return b"payload-type:" + SCENE_MIME.encode() in data
    return False


def _png_texts(data: bytes):
    """PNG tEXt / iTXt chunks (keyword, text) — does not trust the declared size and checks bounds."""
    i = 8
    while i + 12 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + n]
        if len(body) < n:
            return
        if kind == b"tEXt" and b"\0" in body:
            k, t = body.split(b"\0", 1)
            yield k.decode("latin-1"), t.decode("latin-1")
        elif kind == b"iTXt" and b"\0" in body:
            k, rest = body.split(b"\0", 1)
            if len(rest) >= 2 and rest[0] == 0:   # uncompressed only
                parts = rest[2:].split(b"\0", 2)
                if len(parts) == 3:
                    yield k.decode("latin-1"), parts[2].decode("utf-8", "replace")
        elif kind == b"IEND":
            return
        i += 12 + n


def embedded_scene(data: bytes, mime: Optional[str] = None) -> Optional[dict]:
    """The scene embedded in an image (excalidraw.com 'embed scene' export, Obsidian *.excalidraw.png/svg) → document. None if absent.
    Same format as Excalidraw 0.18 data/image.ts (decodePngMetadata) and scene/export.ts (decodeSvgBase64Payload)."""
    mime = mime or sniff_mime(data)
    if mime == "image/png":
        for k, t in _png_texts(data):
            if k == SCENE_MIME:
                return _decode_payload(t)
        return None
    if mime == "image/svg+xml":
        txt = data.decode("utf-8", "replace")
        if "payload-type:" + SCENE_MIME not in txt:
            return None
        m = re.search(r"<!--\s*payload-start\s*-->\s*(.+?)\s*<!--\s*payload-end\s*-->", txt, re.S)
        if not m:
            raise ImportFailed(T("그림 안 장면이 깨졌다", "The scene inside the image is corrupt"))
        ver = re.search(r"<!--\s*payload-version:(\d+)\s*-->", txt)
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", m.group(1)), validate=True)
        except (binascii.Error, ValueError):
            raise ImportFailed(T("그림 안 장면이 깨졌다", "The scene inside the image is corrupt"))
        # variant 1: UTF-8 text in base64; variant 2: byte text (latin-1) in base64
        return _decode_payload(raw.decode("latin-1") if ver and ver.group(1) != "1" else raw.decode("utf-8", "replace"))
    return None


def parse(name: str, data: bytes) -> dict:
    """File name + bytes → {kind, title, elements, files, skipped}. ImportFailed for an unknown format."""
    if not isinstance(data, (bytes, bytearray)):
        raise ImportFailed(T("내용이 없다", "No content"))
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportFailed(T("파일이 너무 크다(50 MB 까지)", "The file is too large (up to 50 MB)"))
    data = bytes(data)
    ext = Path(str(name or "")).suffix.lower()
    skipped: Dict[str, int] = {}
    if ext in IMAGE_EXT or (ext not in DOC_EXT and sniff_mime(data)):
        scene = embedded_scene(data) if sniff_mime(data) in ("image/png", "image/svg+xml") else None
        if scene is not None:   # an image with an embedded scene — import the scene, not the image (same as Excalidraw)
            els, files = _from_excalidraw(scene, skipped)
            return {"kind": "embedded", "title": _title_from(re.sub(r"\.excalidraw(?=\.(png|svg)$)", "", str(name), flags=re.I)),
                    "elements": els, "files": files, "skipped": skipped}
        els, files = _from_image(name, data)
        return {"kind": "image", "title": _title_from(name), "elements": els, "files": files, "skipped": skipped}
    try:
        doc = loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise ImportFailed(T("읽을 수 없는 파일이다(.excalidraw · .excalidrawlib · 그림만)", "Unreadable file (only .excalidraw, .excalidrawlib or images)"))
    if not isinstance(doc, dict):
        raise ImportFailed(T("Excalidraw 파일이 아니다", "Not an Excalidraw file"))
    t = doc.get("type")
    if t in ("excalidraw", "excalidraw/clipboard"):
        els, files = _from_excalidraw(doc, skipped)
        kind = "excalidraw"
    elif t == "excalidrawlib":
        els, files, kind = _from_library(doc, skipped), {}, "library"
    else:
        raise ImportFailed(T("Excalidraw 파일이 아니다(type 이 excalidraw · excalidrawlib 가 아님)", "Not an Excalidraw file (type is neither excalidraw nor excalidrawlib)"))
    return {"kind": kind, "title": _title_from(name), "elements": els, "files": files, "skipped": skipped}


# ────────────────────────────────────────────── Merge preparation
def remap_ids(els: List[dict]) -> List[dict]:
    """Reassign all ids (a copy). containerId, boundElements, bindings, groupIds and frameId are rewritten via the same mapping table.
    If a box text id has the form '<box>-label', the new id keeps that form (the rule board_update uses to find labels)."""
    els = copy.deepcopy(els)
    idmap: Dict[str, str] = {}
    for e in els:
        if e["type"] != "text" or not e.get("containerId"):
            idmap[e["id"]] = ak.new_id()
    for e in els:
        if e["id"] in idmap:
            continue
        old_c = e["containerId"]
        nc = idmap.get(old_c)
        idmap[e["id"]] = nc + "-label" if nc and e["id"] == old_c + "-label" and len(nc) <= ak.MAX_CONTAINER_ID else ak.new_id()
    gmap: Dict[str, str] = {}
    for e in els:
        e["id"] = idmap[e["id"]]
        if e.get("containerId"):
            e["containerId"] = idmap.get(e["containerId"])
        if e.get("frameId"):
            e["frameId"] = idmap.get(e["frameId"])
        for k in ("startBinding", "endBinding"):
            if e.get(k):
                nid = idmap.get(e[k]["elementId"])
                e[k] = dict(e[k], elementId=nid) if nid else None
        if e.get("boundElements"):
            e["boundElements"] = [dict(b, id=idmap[b["id"]]) for b in e["boundElements"] if b["id"] in idmap] or None
        e["groupIds"] = [gmap.setdefault(g, ak.new_id()) for g in e.get("groupIds") or []]
    return els


def _z_order(els: List[dict]) -> List[dict]:
    """Original z-order — by index if all indexes are valid (array order for ties), otherwise array order."""
    if els and all(ak.is_valid_key(e.get("index")) for e in els):
        return [e for _, _, e in sorted((e["index"], i, e) for i, e in enumerate(els))]
    return list(els)


def _renumber_pages(els: List[dict], existing: List[dict]) -> None:
    """Renumber imported whiteboard pages (customData.tbPage) to the board's next page numbers (id tbpage-<k>, name 'page k' (Korean label)). Child frameIds follow too."""
    pages = [e for e in els if e["type"] == "frame" and isinstance(e.get("customData"), dict) and "tbPage" in e["customData"]]
    if not pages:
        return
    pages.sort(key=lambda e: (_fin(e["customData"].get("tbPage"), 0), e["x"]))
    used = {e.get("id") for e in existing if isinstance(e, dict)}
    old_by = {e.get("id"): e for e in existing if isinstance(e, dict)}
    k = ak.next_page(existing)
    ren: Dict[str, str] = {}
    for f in pages:
        while ak.page_id(k) in used and not (old_by.get(ak.page_id(k)) or {}).get("isDeleted"):
            k += 1
        nid = ak.page_id(k)
        ren[f["id"]] = nid
        old = old_by.get(nid)
        if old is not None:   # same id that was deleted — version must exceed it to survive the server merge
            f["version"] = int(_fin(old.get("version"), 0)) + 1
        f["customData"]["tbPage"] = k
        f["seed"] = ak.PAGE_SEED * k
        if f.get("name") is None or re.fullmatch(r"장 \d+", str(f.get("name"))):
            f["name"] = "장 %d" % k
        used.add(nid)
        k += 1
    for e in els:
        if e["id"] in ren:
            e["id"] = ren[e["id"]]
        if e.get("frameId") in ren:
            e["frameId"] = ren[e["frameId"]]
        if e.get("containerId") in ren:
            e["containerId"] = ren[e["containerId"]]
        for k in ("startBinding", "endBinding"):   # arrows attached to a page border — Excalidraw treats a frame as a binding target too
            if e.get(k) and e[k].get("elementId") in ren:
                e[k] = dict(e[k], elementId=ren[e[k]["elementId"]])
        if e.get("boundElements"):
            e["boundElements"] = [dict(b, id=ren.get(b["id"], b["id"])) for b in e["boundElements"]]


def _target(at, existing: List[dict], mine: Tuple[float, float, float, float], gap: float = 80.0) -> Tuple[float, float]:
    """Where the group's top-left goes. at: None, "right" (right of existing drawings), "below", {x, y} (group center), or (x, y)."""
    if isinstance(at, dict):
        at = (at.get("x"), at.get("y"))
    if isinstance(at, (list, tuple)):
        cx, cy = _fin(at[0] if len(at) > 0 else None), _fin(at[1] if len(at) > 1 else None)
        if cx is None or cy is None or abs(cx) > 1e7 or abs(cy) > 1e7:
            raise ImportFailed(T("at 은 {x, y} 수", "at must be numbers {x, y}"))
        return cx - (mine[2] - mine[0]) / 2, cy - (mine[3] - mine[1]) / 2
    board = ak.scene_bbox(existing)
    if board is None:
        return mine[0], mine[1]   # empty board — keep the original position
    if at in (None, "", "right"):
        return board[2] + gap, board[1]
    if at == "below":
        return board[0], board[3] + gap
    raise ImportFailed(T('at 은 "right" · "below" · {x, y}', 'at must be "right", "below" or {x, y}'))


def prepare(elements: List[dict], existing: List[dict], mode: str = "merge", at=None) -> List[dict]:
    """parse's elements → elements to put on the board (copies). existing = the board's current elements (including deleted ones)."""
    try:
        return _prepare(elements, existing, mode, at)
    except RecursionError:
        raise ImportFailed(T("문서가 너무 깊게 중첩돼 있다(깨졌거나 이상한 파일)", "The document is nested too deeply (a corrupt or odd file)"))


def _prepare(elements: List[dict], existing: List[dict], mode: str, at) -> List[dict]:
    existing = [e for e in existing or [] if isinstance(e, dict)]
    els = _z_order(copy.deepcopy(elements))
    if not els:
        return []
    if mode == "new":
        keys = [e.get("index") for e in els]
        ok = all(ak.is_valid_key(k) for k in keys) and all(a < b for a, b in zip(keys, keys[1:]))
        if not ok:
            ak.assign_indices(existing, els)
        return els
    if mode != "merge":
        raise ImportFailed(T('mode 는 "merge" · "new"', 'mode must be "merge" or "new"'))
    _target(at, [], (0.0, 0.0, 0.0, 0.0))   # validate the at format (so a bad value is rejected even if it is changed to "right" below because of pages)
    els = remap_ids(els)
    _renumber_pages(els, existing)
    now = int(time.time() * 1000)
    for e in els:
        if not (isinstance(e.get("customData"), dict) and "tbPage" in e["customData"] and e["id"].startswith("tbpage-")):
            e["version"] = 1
        e["versionNonce"] = secrets.randbelow(2 ** 31)
        e["updated"] = now
    mine = ak.scene_bbox(els)
    if mine is not None:
        live = [e for e in existing if not e.get("isDeleted")]
        pages_here = any(e.get("type") == "frame" and isinstance(e.get("customData"), dict) and "tbPage" in e["customData"] for e in live)
        if pages_here and any(e["type"] == "frame" and e["id"].startswith("tbpage-") for e in els) and isinstance(at, (dict, list, tuple)):
            at = "right"   # a group containing whiteboard pages goes to the right of existing drawings — placing it at the screen center would overlap existing pages
        tx, ty = _target(at, live, mine)
        dx, dy = tx - mine[0], ty - mine[1]
        for e in els:
            e["x"] += dx
            e["y"] += dy
    ak.assign_indices(existing, els)
    return els


def missing_files(els: List[dict], have: Callable[[str], bool]) -> List[str]:
    need = sorted({e["fileId"] for e in els if e.get("type") == "image" and isinstance(e.get("fileId"), str)})
    return [f for f in need if not have(f)]


def apply(board, parsed: dict, mode: str, at, client: str, by: str, save_file: Callable, agent: Optional[str] = None) -> dict:
    """Server side — under the board lock: prepare, save images (first), merge elements (2000 at a time, broadcast).
    save_file(board, rec, by, client) writes one image into the board files and emits a 'file' event (left as is if it already exists)."""
    with board.lock:
        existing = list(board.elements.values())
        items = prepare(parsed["elements"], existing, mode, at)
        if agent:   # added by an agent — stamp customData.tbBy (any tbBy in the file was already dropped by _custom)
            for e in items:
                e["customData"] = dict(e.get("customData") or {}, tbBy=ak.tb_by(agent))
        stored = 0
        for rec in (parsed.get("files") or {}).values():
            if save_file(board, rec, by, client):
                stored += 1
        files_dir = board.root / "files"
        miss = missing_files(items, lambda f: (files_dir / f).is_file())
        took = []
        for i in range(0, len(items), CHUNK):
            took += board.merge(items[i:i + CHUNK], client)
        seq = board.seq
    bb = ak.scene_bbox(items)
    return {"board": board.id, "added": len(took), "skipped": dict(parsed.get("skipped") or {}), "files": stored,
            "missing_files": miss, "seq": seq, "bbox": [round(v, 1) for v in bb] if bb else None, "kind": parsed.get("kind")}


# ────────────────────────────────────────────── HTTP client (CLI, MCP)
def stripped(parsed: dict) -> str:
    """The .excalidraw document (JSON text) without image data URLs — sent to /import (images go separately to /files)."""
    doc = {"type": "excalidraw", "version": 2, "source": "tempboard-import", "elements": parsed["elements"],
           "files": {fid: {"id": fid, "mimeType": r["mimeType"]} for fid, r in (parsed.get("files") or {}).items()}}
    return json.dumps(doc, ensure_ascii=False)


def body_bytes(body: dict) -> int:
    """Actual bytes of the request body (same as the CLI / MCP json.dumps(ensure_ascii=False) → UTF-8)."""
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def too_big_message(n: int) -> str:
    return T(f"보낼 내용이 서버 한도 {MAX_BODY // (1024 * 1024)} MB 를 넘는다({n / 1024 / 1024:.1f} MB, 그림 제외) — "
             "요소가 너무 많다. 사이트를 닫고(tempboard stop) `tempboard import` 로 넣는다",
             f"The content to send exceeds the server limit of {MAX_BODY // (1024 * 1024)} MB ({n / 1024 / 1024:.1f} MB, images excluded) — "
             "too many elements. Close the site (tempboard stop) and use `tempboard import`")


def check_body(body: dict) -> None:
    n = body_bytes(body)
    if n > MAX_BODY - 4096:   # headers and slack
        raise ImportFailed(too_big_message(n))


def push(req: Callable, parsed: dict, mode: str = "merge", board: Optional[str] = None, title: Optional[str] = None,
         at=None, client: str = "", name: str = "import.excalidraw", progress: Optional[Callable] = None) -> dict:
    """Push to a running board server. req(method, path, body) → (code, data).
    merge: images first to /boards/:id/files → /boards/:id/import. new: /boards/import (new board) → images into that board."""
    content = stripped(parsed)
    name = re.sub(r"[^\w가-힣.\- ]+", "_", Path(str(name)).name)[:80] or "import.excalidraw"
    if not name.lower().endswith(DOC_EXT):
        name += ".excalidraw"   # the server receives the document without images
    # measure by actually encoding the whole body to send — a " inside content becomes \" and a Hangul character is 3 bytes. Do this before uploading images.
    probe = {"name": name, "content": content, "title": title or parsed.get("title"), "mode": "merge", "at": at, "client": client}
    check_body(probe)

    def call(method, path, body, want=(200, 201)):
        code, data = req(method, path, body)
        if code not in want:
            msg = data.get("error") if isinstance(data, dict) else data
            raise ImportFailed(T(f"서버가 거부했다({code}: {msg})", f"The server refused ({code}: {msg})"))
        return data

    def upload(bid):
        recs = list((parsed.get("files") or {}).values())
        for i, rec in enumerate(recs):
            call("POST", f"/api/whiteboard/boards/{bid}/files", {"client": client, "id": rec["id"], "mimeType": rec["mimeType"],
                                                                 "dataURL": rec["dataURL"]})
            if progress:
                progress(i + 1, len(recs))
        return len(recs)

    if mode == "new":
        res = call("POST", "/api/whiteboard/boards/import", {"name": name, "content": content, "title": title or parsed.get("title"),
                                                            "client": client})
        n = upload(res["board"])
        res["files"] = n
        res["missing_files"] = []
        return res
    if mode != "merge":
        raise ImportFailed(T('mode 는 "merge" · "new"', 'mode must be "merge" or "new"'))
    if not board or not BOARD_ID.match(str(board)):
        raise ImportFailed(T("합칠 보드 id 가 없다", "No board id to merge into"))
    n = upload(board)
    res = call("POST", f"/api/whiteboard/boards/{board}/import", {"name": name, "content": content, "mode": "merge", "at": at,
                                                                  "client": client})
    res["files"] = n
    return res


def read_path(path_s, max_bytes: int = MAX_IMPORT_BYTES) -> Tuple[str, bytes]:
    """Validate the file path to import → (name, bytes). No '..' · only regular files after resolving symlinks · extension allowlist · size cap."""
    if not isinstance(path_s, str) or not path_s.strip() or "\x00" in path_s:
        raise ImportFailed(T("path 는 파일 경로 글", "path must be a file path string"))
    p = Path(os.path.expanduser(path_s.strip()))
    if ".." in p.parts:
        raise ImportFailed(T("경로에 '..' 를 쓸 수 없다 — 절대 경로로 준다", "'..' is not allowed in the path — give an absolute path"))
    try:
        rp = p.resolve(strict=True)
        st = rp.stat()
    except (OSError, RuntimeError):
        raise ImportFailed(T(f"파일이 없다: {path_s}", f"No such file: {path_s}"))
    if not stat.S_ISREG(st.st_mode):
        raise ImportFailed(T("일반 파일만 가져올 수 있다", "Only regular files can be imported"))
    if not rp.name.lower().endswith(ALLOWED_EXT) or not p.name.lower().endswith(ALLOWED_EXT):
        raise ImportFailed(T("가져올 수 있는 파일: ", "Importable files: ") + " · ".join(ALLOWED_EXT))
    if st.st_size > max_bytes:
        raise ImportFailed(T("파일이 너무 크다(50 MB 까지)", "The file is too large (up to 50 MB)"))
    with open(rp, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImportFailed(T("파일이 너무 크다(50 MB 까지)", "The file is too large (up to 50 MB)"))
    return rp.name, data


def summary(res: dict) -> str:
    sk = res.get("skipped") or {}
    parts = [T(f"요소 {res.get('added', 0)}개", f"{res.get('added', 0)} element(s)")]
    if res.get("files"):
        parts.append(T(f"그림 {res['files']}장", f"{res['files']} image(s)"))
    line = T(f"보드 {res.get('board')} 에 " + " · ".join(parts) + " 를 넣었다",
             "Added " + " · ".join(parts) + f" to board {res.get('board')}")
    if sk:
        line += T(" (버림: ", " (skipped: ") + ", ".join(
            f"{T(REASONS.get(k, k), REASONS_EN.get(k, k))} {v}" for k, v in sorted(sk.items())) + ")"
    if res.get("missing_files"):
        line += T(f" — 그림 파일 {len(res['missing_files'])}개가 없다", f" — {len(res['missing_files'])} image file(s) missing")
    return line


# ────────────────────────────────────────────── CLI(tempboard import)
def _http(base: str, key: Optional[str]):
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def req(method, path, body):
        h = {"X-Whiteboard": "1", "Accept": "application/json", "X-Tb-Name": "tempboard%20import"}
        if key:
            h["X-Tb-Key"] = key
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(base.rstrip("/") + path, data=data, headers=h, method=method)
        try:
            with opener.open(r, timeout=120) as resp:
                raw, code = resp.read(), resp.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        except (urllib.error.URLError, OSError) as e:
            raise ImportFailed(T(f"보드 서버에 닿지 않는다({base}): {getattr(e, 'reason', e)}",
                                 f"Cannot reach the board server ({base}): {getattr(e, 'reason', e)}"))
        try:
            return code, (json.loads(raw.decode("utf-8")) if raw else None)
        except ValueError:
            return code, raw.decode("utf-8", "replace")[:300]
    return req


def _parse_at(s):
    if not s:
        return None
    try:
        x, y = (float(v) for v in str(s).split(","))
    except ValueError:
        raise SystemExit(T("--at 은 x,y (예: 0,0)", "--at must be x,y (e.g. 0,0)"))
    return {"x": x, "y": y}


def run_cli(ctx, a) -> dict:
    st = ctx.need(a.id)
    try:
        name, data = read_path(a.file)
        parsed = parse(name, data)
    except ImportFailed as e:
        raise SystemExit(str(e))
    mode = "new" if a.new else "merge"
    board = a.board or (None if a.new else st.get("board"))
    if mode == "merge" and not board:
        raise SystemExit(T("합칠 보드가 없다 — --board ID 또는 --new", "No board to merge into — give --board ID or --new"))
    at = _parse_at(a.at)
    title = (a.title or parsed["title"])[:80]
    if ctx.live_status(st) == "running" and st.get("local_url"):
        try:
            return push(_http(st["local_url"], st.get("edit_key")), parsed, mode, board, title, at, client="cli-import", name=name)
        except ImportFailed as e:
            raise SystemExit(str(e))
    # closed site — write directly to the store (no supervisor or server, so no process to overwrite it)
    if st.get("pid") and ctx.alive(st["pid"]):
        raise SystemExit(T("사이트가 켜지는 중이다 — 잠시 뒤 다시", "The site is starting up — try again shortly"))
    srv = ctx.load_server()
    store = srv.Store(ctx.SITES / st["id"] / "data")
    who = {"id": "tempboard import", "name": "tempboard import"}
    made = None
    try:
        if mode == "new":
            prepare(parsed["elements"], [], mode)   # only create the board once everything checks out
            b = made = store.create(title, who)
        else:
            b = store.get(board)
            if b is None:
                raise SystemExit(T(f"보드가 없다: {board}", f"No such board: {board}"))
        res = apply(b, parsed, mode, at, "cli-import", who["name"], srv.save_file)
    except ImportFailed as e:
        if made is not None and hasattr(store, "discard"):
            store.discard(made.id)
        raise SystemExit(str(e))
    store.flush()
    return res


def register(sub, ctx):
    p = sub.add_parser("import", help=T(".excalidraw · .excalidrawlib · 그림을 보드에 합치거나 새 보드로", "Merge .excalidraw / .excalidrawlib / images into a board, or import as a new board"))
    p.add_argument("id", help=T("사이트 id", "Site id"))
    p.add_argument("file", help=T("가져올 파일(.excalidraw · .excalidrawlib · png · jpg · gif · webp · svg, 50 MB 까지)", "File to import (.excalidraw, .excalidrawlib, png, jpg, gif, webp, svg; up to 50 MB)"))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--board", default=None, help=T("합칠 보드 id(기본: 사이트의 첫 보드)", "Board id to merge into (default: the site's first board)"))
    g.add_argument("--new", action="store_true", help=T("새 보드로(파일의 id 를 그대로)", "As a new board (keeping the file's ids)"))
    p.add_argument("--title", default=None, help=T("--new 의 보드 이름(기본: 파일 이름)", "Board name for --new (default: the file name)"))
    p.add_argument("--at", default=None, help=T("합칠 자리 x,y(묶음 가운데). 기본: 기존 그림 오른쪽", "Position x,y to merge at (center of the group). Default: right of the existing drawing"))
    p.add_argument("--json", action="store_true")

    def cmd(a):
        if a.title and not a.new:
            raise SystemExit(T("--title 은 --new 와 함께", "--title goes with --new"))
        res = run_cli(ctx, a)
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(summary(res))
    p.set_defaults(f=cmd)
