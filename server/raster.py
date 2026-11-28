# SPDX-License-Identifier: MIT
"""Server raster for board_snapshot — Excalidraw elements -> grayscale PNG with the Python standard library only (zlib + struct; runs on 3.9).

Used when no person's edit tab can render (WP-3, plan 09 B4). It draws what recognition needs and nothing more:
  freedraw / line / arrow    thick polylines (+ arrowheads)
  rectangle / diamond / ellipse / frame / image   outlines (an image also gets a light fill)
  text (free and bound)      gray bars where the lines of text sit — there are no fonts here, the words go into the text result
No anti-aliasing, fills are ignored, colors become gray levels (mono=True: one dark ink, highlighter included).
Board coordinates of pixel (px, py) = (x0 + px / scale, y0 + py / scale), as in the tab renderer (web/src/features/jobs/renderJob.js).
Prototype: _workspace/tests/rough/raster.py.
"""
from __future__ import annotations

import math
import struct
import time
import zlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

INK = 30           # mono ink gray level
TEXT_GRAY = 165    # text placeholder bars
FRAME_GRAY = 190   # page frames
IMAGE_FILL = 236
MAX_SCALE = 4.0    # never blow a tiny selection up more than this (same as the tab renderer's renderScale)
MAX_PIXELS = 1568 * 1568
MAX_STROKE = 64.0  # stroke width cap (board units) — one absurd width must not shrink the whole picture to nothing
BUDGET_S = 4.0     # default time budget of one render (pure Python holds the GIL; past this it stops with RasterTimeout)


class RasterTimeout(Exception):
    """The render took longer than its budget, or was cancelled."""

Pt = Tuple[float, float]


# ------------------------------------------------------------------ PNG
def png_bytes(w: int, h: int, buf: bytes) -> bytes:
    """8-bit grayscale PNG from a w*h buffer (row-major)."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(buf[y * w:(y + 1) * w]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def png_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(w, h) from a complete, CRC-checked IHDR chunk; None for anything else (truncated header, wrong chunk, zero size)."""
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[8:16] != b"\x00\x00\x00\x0dIHDR":
        return None
    if struct.unpack(">I", data[29:33])[0] != zlib.crc32(data[12:29]) & 0xFFFFFFFF:
        return None
    w, h = struct.unpack(">II", data[16:24])
    return (w, h) if w > 0 and h > 0 else None


def png_complete(data: bytes) -> bool:
    """Every chunk whole with a matching CRC, IHDR first, at least one IDAT, and IEND as the last bytes (a cut-off PNG shows nothing)."""
    if png_size(data) is None:
        return False
    i, idat = 8, False
    while i + 12 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        if i + 12 + n > len(data):
            return False
        t = data[i + 4:i + 8]
        if struct.unpack(">I", data[i + 8 + n:i + 12 + n])[0] != zlib.crc32(data[i + 4:i + 8 + n]) & 0xFFFFFFFF:
            return False
        i += 12 + n
        if t == b"IDAT":
            idat = True
        elif t == b"IEND":
            return idat and i == len(data)
    return False


def scene_tag(elements: Iterable[dict]) -> str:
    """Same as web/src/features/jobs/jobs.js sceneTag: count + FNV-1a 32 over the UTF-16 code units of the sorted "id:version:versionNonce" lines
    (versionNonce: two edits of the same version are told apart — the server keeps the lower nonce, server.py wins).
    A tab render is described with the MCP's elements only when the tags match (the picture and the words show the same scene)."""
    els = list(elements)
    def num(v):
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else 0
    lines = ["%s:%d:%d" % (e.get("id"), num(e.get("version")), num(e.get("versionNonce"))) for e in els]
    text = "\n".join(sorted(lines))
    h = 0x811C9DC5
    for unit in struct.unpack("<%dH" % (len(text.encode("utf-16-le")) // 2), text.encode("utf-16-le")):
        h = ((h ^ unit) * 0x01000193) & 0xFFFFFFFF
    return "%d-%08x" % (len(els), h)


# ------------------------------------------------------------------ element helpers
def _f(v, d=0.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


def _pts(e) -> List[Pt]:
    out = []
    for p in e.get("points") or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append((_f(p[0]), _f(p[1])))
    return out


def gray_of(color, mono: bool) -> Optional[int]:
    if mono:
        return INK
    if not isinstance(color, str) or color == "transparent":
        return None
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return INK
    return min(200, int(0.299 * r + 0.587 * g + 0.114 * b))   # very light ink would vanish on white


def element_box(e) -> Optional[Tuple[float, float, float, float]]:
    """(x, y, w, h) like web/src/features/pages/pages.js elementBox — used for region overlap and page membership."""
    x, y = _f(e.get("x")), _f(e.get("y"))
    pts = _pts(e)
    if pts:
        xs, ys = [x + p[0] for p in pts], [y + p[1] for p in pts]
        a = _f(e.get("angle"))
        if a:   # rotated line / stroke: the box of the drawn (rotated) points
            r = _rot(list(zip(xs, ys)), (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, a)
            xs, ys = [p[0] for p in r], [p[1] for p in r]
        pad = min(MAX_STROKE, _f(e.get("strokeWidth"), 1)) * 2
        return min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad
    w, h = _f(e.get("width")), _f(e.get("height"))
    a = _f(e.get("angle"))
    if not a:
        return x, y, w, h
    cx, cy, c, s = x + w / 2, y + h / 2, abs(math.cos(a)), abs(math.sin(a))
    W, H = w * c + h * s, w * s + h * c
    return cx - W / 2, cy - H / 2, W, H


def _overlaps(b, r) -> bool:
    return b[0] < r[0] + r[2] and b[0] + b[2] > r[0] and b[1] < r[1] + r[3] and b[1] + b[3] > r[1]


def is_page(e) -> bool:
    cd = e.get("customData") if isinstance(e.get("customData"), dict) else {}
    return e.get("type") == "frame" and "tbPage" in cd and isinstance(cd.get("tbPage"), (int, float)) and not isinstance(cd.get("tbPage"), bool)


def pick(elements: Iterable[dict], ids: Optional[Sequence[str]] = None, region: Optional[Tuple[float, float, float, float]] = None,
         frame: Optional[dict] = None) -> Tuple[List[dict], List[str]]:
    """Which elements a snapshot draws (mirror of web/src/features/jobs/pick.js). -> (elements, missing ids).
    ids: those plus bound text / containers / frame children; region (x, y, w, h): everything overlapping it; frame: that page; none: all."""
    live = [e for e in elements or [] if isinstance(e, dict) and not e.get("isDeleted") and isinstance(e.get("id"), str)]
    if ids is not None:   # an empty list draws nothing (it must never widen to the whole board)
        by = {e["id"]: e for e in live}
        want = {i for i in ids if i in by}
        for i in list(want):
            cid = by[i].get("containerId")
            if isinstance(cid, str) and cid in by:
                want.add(cid)
        frames = {i for i in want if by[i].get("type") in ("frame", "magicframe")}
        grew = True
        while grew:
            grew = False
            for e in live:
                if e["id"] in want:
                    continue
                if e.get("containerId") in want or (e.get("frameId") in frames and e.get("frameId")):
                    want.add(e["id"])
                    grew = True
        return [e for e in live if e["id"] in want], [i for i in ids if i not in by]
    if region is not None:   # a page frame only when the region covers the whole page (else the picture would grow to the page)
        def inside(b):
            return b[0] >= region[0] and b[1] >= region[1] and b[0] + b[2] <= region[0] + region[2] and b[1] + b[3] <= region[1] + region[3]
        return [e for e in live if (lambda b: b is not None and _overlaps(b, region) and (not is_page(e) or inside(b)))(element_box(e))], []
    if frame is not None:
        r = (_f(frame.get("x")), _f(frame.get("y")), _f(frame.get("width")), _f(frame.get("height")))
        out = []
        for e in live:
            if e["id"] == frame["id"]:
                out.append(e)
            elif not is_page(e) and (not e.get("frameId") or e.get("frameId") == frame["id"]):
                b = element_box(e)
                if e.get("frameId") == frame["id"] or (b is not None and _overlaps(b, r)):
                    out.append(e)
        return out, []
    return live, []


# ------------------------------------------------------------------ geometry → strokes
def _rot(pts: List[Pt], cx: float, cy: float, a: float) -> List[Pt]:
    if not a:
        return pts
    c, s = math.cos(a), math.sin(a)
    return [(cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c) for px, py in pts]


def _head(tip: Pt, prev: Pt, kind, sw: float) -> List[List[Pt]]:
    """Arrowhead polylines at tip, pointing away from prev. kind = Excalidraw arrowhead name."""
    dx, dy = tip[0] - prev[0], tip[1] - prev[1]
    seg = math.hypot(dx, dy)
    if seg < 1e-6:
        return []
    ux, uy = dx / seg, dy / seg
    ln = min(15 + 3 * sw, seg * 0.75)
    if kind in ("dot", "circle", "circle_outline"):
        r = max(3.0, 2 + sw * 1.5)
        return [[(tip[0] + r * math.cos(2 * math.pi * k / 16), tip[1] + r * math.sin(2 * math.pi * k / 16)) for k in range(17)]]
    if kind == "bar":
        return [[(tip[0] - uy * ln / 2, tip[1] + ux * ln / 2), (tip[0] + uy * ln / 2, tip[1] - ux * ln / 2)]]
    wings = []
    for sgn in (1, -1):
        a = math.radians(25) * sgn
        bx, by = -ux, -uy
        wx, wy = bx * math.cos(a) - by * math.sin(a), bx * math.sin(a) + by * math.cos(a)
        wings.append((tip[0] + wx * ln, tip[1] + wy * ln))
    if kind and str(kind).startswith("triangle") or kind == "diamond":
        return [[wings[0], tip, wings[1], wings[0]]]
    return [[wings[0], tip, wings[1]]]


def shapes_of(e, mono: bool) -> List[tuple]:
    """Drawing ops for one element: ("line", pts, gray, radius_board) · ("fill", polygon, gray)."""
    t = e.get("type")
    x, y, w, h = _f(e.get("x")), _f(e.get("y")), _f(e.get("width")), _f(e.get("height"))
    a = _f(e.get("angle"))
    sw = min(MAX_STROKE, max(0.5, _f(e.get("strokeWidth"), 1)))
    g = gray_of(e.get("strokeColor", "#1e1e1e"), mono)
    ops: List[tuple] = []
    if t in ("freedraw", "line", "arrow"):
        if g is None:
            return ops
        pts = [(x + px, y + py) for px, py in _pts(e)]
        if not pts:
            return ops
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        pts = _rot(pts, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, a)
        r = sw * 1.5 if t == "freedraw" else sw / 2   # Excalidraw pen: perfect-freehand size = strokeWidth * 4.25 with thinning -> about 3x the width on average
        ops.append(("line", pts, g, r))
        if t == "arrow" and len(pts) >= 2:
            for kind, tip, rest in ((e.get("endArrowhead"), pts[-1], pts[-2::-1]), (e.get("startArrowhead"), pts[0], pts[1:])):
                if not kind:
                    continue
                prev = next((p for p in rest if math.hypot(p[0] - tip[0], p[1] - tip[1]) > 1e-6), None)
                if prev is not None:
                    for pl in _head(tip, prev, kind, sw):
                        ops.append(("line", pl, g, sw / 2))
        return ops
    cx, cy = x + w / 2, y + h / 2
    if t == "text":
        txt = e.get("text") if isinstance(e.get("text"), str) else ""
        rows = txt.split("\n") or [""]
        longest = max(1, max(len(r_) for r_ in rows))
        lh = h / max(1, len(rows))
        gray = TEXT_GRAY if mono else min(220, (g if g is not None else INK) + 120)
        for k, row in enumerate(rows):
            if not row.strip():
                continue
            bw = w * len(row.rstrip()) / longest
            align = e.get("textAlign")
            bx = x + (w - bw) / 2 if align == "center" else (x + w - bw if align == "right" else x)
            y0, y1 = y + lh * k + lh * 0.25, y + lh * k + lh * 0.75
            ops.append(("fill", _rot([(bx, y0), (bx + bw, y0), (bx + bw, y1), (bx, y1)], cx, cy, a), gray))
        return ops
    if t == "rectangle" or t in ("frame", "magicframe", "image", "embeddable", "iframe"):
        poly = _rot([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], cx, cy, a)
        if t == "image":
            ops.append(("fill", poly[:4], IMAGE_FILL))
        if t in ("frame", "magicframe"):
            ops.append(("line", poly, FRAME_GRAY, 1.0))
        elif g is not None or t != "rectangle":
            ops.append(("line", poly, g if g is not None else INK, sw / 2))
        return ops
    if g is None:
        return ops
    if t == "diamond":
        ops.append(("line", _rot([(cx, y), (x + w, cy), (cx, y + h), (x, cy), (cx, y)], cx, cy, a), g, sw / 2))
    elif t == "ellipse":
        n = 64
        ops.append(("line", _rot([(cx + w / 2 * math.cos(2 * math.pi * k / n), cy + h / 2 * math.sin(2 * math.pi * k / n)) for k in range(n + 1)],
                                 cx, cy, a), g, sw / 2))
    return ops


# ------------------------------------------------------------------ rasterizer
_DARKEN: Dict[int, bytes] = {}


def _thin(pts: List[Pt], step: float = 1.0) -> List[Pt]:
    """Drop points closer than `step` pixels to the last kept one (keeps the last point)."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        q = out[-1]
        if abs(p[0] - q[0]) + abs(p[1] - q[1]) >= step:
            out.append(p)
    out.append(pts[-1])
    return out


class Canvas:
    def __init__(self, w: int, h: int, tick=None):
        self.w, self.h = w, h
        self.buf = bytearray(b"\xff" * (w * h))
        self.tick = tick or (lambda: None)   # called before every span-filling shape (render's time budget / cancel check); may raise

    def _span(self, j: int, lo: float, hi: float, g: int):
        i0, i1 = max(0, int(math.floor(lo + 0.5))), min(self.w, int(math.floor(hi + 0.5)))
        if i1 <= i0:
            i = min(self.w - 1, max(0, int(round((lo + hi) / 2 - 0.5))))   # a sliver still leaves one pixel (thin pens must not vanish)
            if 0 <= (lo + hi) / 2 < self.w:
                i0, i1 = i, i + 1
            else:
                return
        base = j * self.w
        seg = self.buf[base + i0:base + i1]
        if g <= min(seg):
            self.buf[base + i0:base + i1] = bytes((g,)) * (i1 - i0)
        else:   # keep darker pixels: per-gray lookup table, applied in C
            t = _DARKEN.get(g)
            if t is None:
                t = _DARKEN[g] = bytes(min(v, g) for v in range(256))
            self.buf[base + i0:base + i1] = seg.translate(t)

    def poly(self, pts: Sequence[Pt], g: int):
        """Fill a convex polygon."""
        if len(pts) < 3:
            return
        self.tick()
        ys = [p[1] for p in pts]
        j0, j1 = max(0, int(math.floor(min(ys)))), min(self.h - 1, int(math.ceil(max(ys))))
        n = len(pts)
        for j in range(j0, j1 + 1):
            yc = j + 0.5
            lo, hi = math.inf, -math.inf
            for k in range(n):
                (ax, ay), (bx, by) = pts[k], pts[(k + 1) % n]
                if (ay <= yc <= by) or (by <= yc <= ay):
                    if ay == by:
                        lo, hi = min(lo, ax, bx), max(hi, ax, bx)
                    else:
                        xx = ax + (yc - ay) * (bx - ax) / (by - ay)
                        lo, hi = min(lo, xx), max(hi, xx)
            if lo <= hi:
                self._span(j, lo, hi, g)

    def capsule(self, a: Pt, b: Pt, r: float, g: int):
        """Segment a-b with round caps, radius r (pixels). Row by row: the capsule is convex, so each row is one span."""
        self.tick()
        (ax, ay), (bx, by) = a, b
        j0 = max(0, int(math.floor(min(ay, by) - r)))
        j1 = min(self.h - 1, int(math.ceil(max(ay, by) + r)))
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        nx, ny = (-dy / L * r, dx / L * r) if L > 1e-9 else (0.0, 0.0)
        quad = [(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)]
        rr = r * r
        for j in range(j0, j1 + 1):
            yc = j + 0.5
            lo, hi = math.inf, -math.inf
            for cx, cy in (a, b):
                d = yc - cy
                if d * d <= rr:
                    q = math.sqrt(rr - d * d)
                    lo, hi = min(lo, cx - q), max(hi, cx + q)
            if L > 1e-9:
                for k in range(4):
                    (px, py), (qx, qy) = quad[k], quad[(k + 1) % 4]
                    if (py <= yc <= qy) or (qy <= yc <= py):
                        xx = px if py == qy else px + (yc - py) * (qx - px) / (qy - py)
                        lo, hi = min(lo, xx), max(hi, xx)
            if lo <= hi:
                self._span(j, lo, hi, g)

    def polyline(self, pts: Sequence[Pt], r: float, g: int):
        if len(pts) == 1:
            self.capsule(pts[0], pts[0], r, g)
        for p, q in zip(pts, pts[1:]):
            self.capsule(p, q, r, g)


def bounds(elements: Iterable[dict], mono: bool = True) -> Optional[Tuple[float, float, float, float]]:  # noqa: C901
    """Board bounds (x0, y0, x1, y1) of what would be drawn, including stroke thickness."""
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    for e in elements:
        for op in shapes_of(e, mono):
            r = op[3] if op[0] == "line" else 0.0
            for px, py in op[1]:
                x0, y0, x1, y1 = min(x0, px - r), min(y0, py - r), max(x1, px + r), max(y1, py + r)
    return None if x0 == math.inf else (x0, y0, x1, y1)


def fit_scale(w: float, h: float, max_edge: int, want: Optional[float] = None) -> float:
    """Same rule as the tab renderer (jobs.js renderScale): fit the longest side into max_edge, never above `want` (default 4)."""
    return min(want or MAX_SCALE, MAX_SCALE, max_edge / max(1.0, w, h) * 0.999)


def render(elements: Sequence[dict], max_edge: int = 1024, scale: Optional[float] = None, pad: float = 16, mono: bool = True,
           frame: Optional[dict] = None, crop: Optional[Tuple[float, float, float, float]] = None, budget_s: float = BUDGET_S,
           cancelled=lambda: False) -> dict:
    """-> {png, width, height, scale, x0, y0, count}. frame: crop to that page (no padding), as the tab renderer's exportingFrame.
    crop (x, y, w, h): crop to that region (intersected with the drawing + pad), as the tab's regionPlan. Raises RasterTimeout past budget_s or when cancelled()."""
    t_end = time.monotonic() + budget_s
    els = [e for e in elements if isinstance(e, dict) and not e.get("isDeleted")]
    if frame is not None:
        x0, y0 = _f(frame.get("x")), _f(frame.get("y"))
        bw, bh = max(1.0, _f(frame.get("width"))), max(1.0, _f(frame.get("height")))
    else:
        b = bounds(els, mono)
        if b is None:
            raise ValueError("nothing to draw")
        x0, y0, x1, y1 = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
        if crop is not None:
            x0, y0 = max(x0, crop[0]), max(y0, crop[1])
            x1, y1 = min(x1, crop[0] + crop[2]), min(y1, crop[1] + crop[3])
            if not (x1 > x0 and y1 > y0):
                raise ValueError("nothing to draw in the region")
        bw, bh = x1 - x0, y1 - y0
    s = fit_scale(bw, bh, max_edge, scale)
    W, H = max(1, min(max_edge, int(round(bw * s)))), max(1, min(max_edge, int(round(bh * s))))
    if W * H > MAX_PIXELS:
        raise ValueError("image too large")
    def check():
        if time.monotonic() > t_end or cancelled():
            raise RasterTimeout("render took longer than %.1f s" % budget_s if not cancelled() else "cancelled")
    cv = Canvas(W, H, tick=check)
    ordered = [e for e in els if e.get("type") in ("frame", "magicframe", "image")] + \
              [e for e in els if e.get("type") not in ("frame", "magicframe", "image")]
    for e in ordered:   # time-based: check() runs before every element, every op and (Canvas.tick) every filled shape
        check()
        for op in shapes_of(e, mono):
            check()
            pts = [((px - x0) * s, (py - y0) * s) for px, py in op[1]]
            if op[0] == "line":
                pts = _thin(pts)
            if op[0] == "fill":
                cv.poly(pts, op[2])
            else:
                cv.polyline(pts, max(0.8, op[3] * s), op[2])
    check()
    png = png_bytes(W, H, cv.buf)
    check()   # the encoding itself counts too: past the budget nothing is returned
    return {"png": png, "width": W, "height": H, "scale": s, "x0": x0, "y0": y0, "count": len(els)}


def char_height(elements: Iterable[dict]) -> Optional[float]:
    """Typical letter height in board units: median font size of text, else the median height of small pen strokes (handwritten letters).
    board_snapshot scales so this becomes about 56 px (plan 09 B4)."""
    fs = sorted(_f(e.get("fontSize")) for e in elements if e.get("type") == "text" and _f(e.get("fontSize")) > 0)
    if fs:
        return fs[len(fs) // 2]
    hs = []
    for e in elements:
        if e.get("type") == "freedraw":
            b = element_box(e)
            if b and 4 < max(b[2], b[3]) < 150:
                hs.append(max(b[3], b[2] * 0.6))
    hs.sort()
    return hs[len(hs) // 2] if len(hs) >= 3 else None
