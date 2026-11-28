# SPDX-License-Identifier: MIT
"""Element factory — builds Excalidraw 0.18 elements from Python with ALL fields.

Why: the live receive path of an open screen (Board.jsx onElements → reconcileElements → updateScene) does not go through restoreElements.
If only minimal fields (a skeleton) are sent, text and arrows end up with NaN size and text vanishes on refresh
So every agent write and import merge goes through this factory.

Field reference: web/node_modules/@excalidraw/excalidraw/dist/types/excalidraw/element/types.d.ts
  (_ExcalidrawElementBase · ExcalidrawTextElement · ExcalidrawLinearElement · ExcalidrawArrowElement ·
   ExcalidrawImageElement · ExcalidrawFrameElement)
Text line height and in-shape text padding reference: FONT_METADATA, BOUND_TEXT_PADDING,
  computeContainerDimensionForBoundText · getBoundTextMaxWidth.

Usage (adding to a board):
        els = board_elements                      # elements from GET /boards/:id
        a = make_box("rectangle", "Input", 0, 0, by="Claude")
        b = make_box("rectangle", "Decision", 260, 0, by="Claude")   # positions inside a group are given explicitly (place moves the whole group)
        arr = make_arrow(a[0], b[0], "yes", by="Claude")   # fixes boundElements of a[0] and b[0] in place
        items = place(els, a + b + arr, at="right")       # positioning + index (z: above existing elements)
    POST /elements {elements: items}
    To connect to a box `old` already on the board: arr = make_arrow(old, new[0]) → old also gets its version bumped (modified).
    Passing old as well, as in place(els, new + arr + [old]), is fine — ids already on the board are neither moved nor
    re-indexed. Send old along with the rest when submitting.
    To create a frame, use make_frame(page=next_page(els), existing=els).

Standard library only; importable on Python 3.9.
"""
from __future__ import annotations

import importlib.util
import json
import math
import secrets
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union


def _load_i18n():
    name = "cera_i18n"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name("i18n.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def T(ko: str, en: str) -> str:
    return _load_i18n().T(ko, en)


def _load_fracindex():
    name = "cera_fracindex"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name("fracindex.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_fi = _load_fracindex()
generate_key_between = _fi.generate_key_between
generate_n_keys_between = _fi.generate_n_keys_between
is_valid_key = _fi.is_valid_key

# ── Constants ─────────────────────────────────────────────────────────
# Excalidraw FONT_FAMILY integer slots. The default is the Board.jsx FONTS default "meet" (gothic) = Nunito (6).
FONT_NUNITO = 6
DEFAULT_FONT = FONT_NUNITO
# Excalidraw FONT_METADATA[...].metrics.lineHeight. Unregistered slots (21–24, added by Cera)
# fall back to the Excalifont value (1.25) in getLineHeight.
LINE_HEIGHT = {1: 1.25, 2: 1.15, 3: 1.2, 5: 1.25, 6: 1.35, 7: 1.15, 8: 1.25, 9: 1.15}
LINE_HEIGHT_FALLBACK = 1.25
MONO_FONTS = {3, 8}          # Cascadia · Comic Shanns — Latin glyphs have uniform width
BOUND_TEXT_PADDING = 5       # Excalidraw BOUND_TEXT_PADDING
ARROW_LABEL_WIDTH_FRACTION = 0.7
ARROW_LABEL_MIN_WIDTH_RATIO = 11
BIND_GAP = 6                 # gap between arrow end and box
INK = "#1e1e1e"              # Board.jsx default ink
BOX_MIN = (160, 80)          # minimum size even for a short label
LABEL_MAX_WIDTH = 280        # wrap the label if it is longer than this
LABEL_SLACK = 1.08           # box slack in case the text width estimate falls short (8% of the estimated width)
SHAPES = ("rectangle", "ellipse", "diamond")
ROUNDNESS = {"rectangle": {"type": 3}, "diamond": {"type": 2}, "ellipse": None}   # Excalidraw ROUNDNESS ADAPTIVE / PROPORTIONAL

Number = Union[int, float]
Element = Dict
Point = Tuple[Number, Number]


def new_id() -> str:
    """Random id similar to Excalidraw's (21-char nanoid) — accepted by the server (string ≤ 64)."""
    return secrets.token_urlsafe(15)


def _nonce() -> int:
    return secrets.randbelow(2 ** 31)


def _now_ms() -> int:
    return int(time.time() * 1000)


def tb_by(by: Optional[str]) -> Optional[str]:
    """Value for customData.tbBy. Omitted when the author is a human (None)."""
    if not by:
        return None
    by = str(by)
    return by if by.startswith("agent:") else "agent:" + by


def _base(kind: str, x: Number, y: Number, w: Number, h: Number, *, by: Optional[str] = None, id: Optional[str] = None,
          frame_id: Optional[str] = None, stroke: str = INK, fill: str = "transparent", stroke_width: Number = 2,
          roundness=None, custom: Optional[dict] = None) -> Element:
    if id is not None and (not isinstance(id, str) or not id or len(id) > 64):
        raise ValueError(T("id 는 1–64자 문자열(서버 규칙)", "id must be a string of 1-64 characters (server rule)"))
    e = {
        "id": id or new_id(), "type": kind, "x": float(x), "y": float(y), "width": float(w), "height": float(h), "angle": 0,
        "strokeColor": stroke, "backgroundColor": fill, "fillStyle": "solid", "strokeWidth": stroke_width,
        "strokeStyle": "solid", "roughness": 0, "opacity": 100, "groupIds": [], "frameId": frame_id, "index": None,
        "roundness": roundness, "seed": _nonce() or 1, "version": 1, "versionNonce": _nonce(), "isDeleted": False,
        "boundElements": None, "updated": _now_ms(), "link": None, "locked": False,
    }
    cd = dict(custom or {})
    who = tb_by(by)
    if who:
        cd["tbBy"] = who
    if cd:
        e["customData"] = cd
    return e


def _num(v, default: float = 0.0) -> float:
    """A number from an element sent by someone else — default if it is not a number or not finite (NaN, null, "abc")."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    v = float(v)
    return v if math.isfinite(v) else default


def _ok_num(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _sid(v) -> Optional[str]:
    """Only values usable as an id (strings)."""
    return v if isinstance(v, str) and v else None


def _points(e: Element) -> List[Tuple[float, float]]:
    """Only finite (x, y) points."""
    out = []
    pts = e.get("points")
    if isinstance(pts, (list, tuple)):
        for p in pts:
            if isinstance(p, (list, tuple)) and len(p) >= 2 and _ok_num(p[0]) and _ok_num(p[1]):
                out.append((float(p[0]), float(p[1])))
    return out


def touch(el: Element) -> Element:
    """Make a modified element propagate again — version+1, new versionNonce, updated (in place)."""
    el["version"] = int(_num(el.get("version"), 0)) + 1
    el["versionNonce"] = _nonce()
    el["updated"] = _now_ms()
    return el


def line_height(font_family: int) -> float:
    return LINE_HEIGHT.get(int(_num(font_family, DEFAULT_FONT)), LINE_HEIGHT_FALLBACK)


# ── Text size estimation ─────────────────────────────────────────────
# The screen does not re-measure text sent by others, so this estimate becomes the size of the selection box and of the in-shape
# area. Too small makes text overflow, so we err on the generous side. The table is em width × 100 measured on screen
# (Chromium canvas measureText, 100px), in printable ASCII order 0x20–0x7E. Nunito (default slot 6) uses its own font values; other
# proportional-font slots use the widest value among Nunito, Excalifont, Virgil, Lilita One and Liberation Sans; monospace
# slots (Cascadia, Comic Shanns) use 0.6em. Outside ASCII: full-width and Hangul (W/F) 1.0em, emoji 1.15em, ambiguous-width (A) symbols and
# punctuation (→ ※ ★ √ ≈ …) 1.0em (rendered with a Hangul font), accented letters take the base letter width, everything else (Greek, Cyrillic, etc.) 0.8em.
_W_NUNITO = (
    22, 32, 47, 56, 56, 92, 68, 28, 34, 34, 47, 56, 28, 35, 28, 39, 56, 56, 56, 56, 56, 56, 56, 56, 56, 56, 28, 28,
    56, 56, 56, 47, 95, 61, 66, 64, 69, 59, 55, 69, 73, 29, 54, 65, 54, 81, 72, 74, 63, 74, 64, 60, 60, 72, 58, 88,
    57, 53, 60, 34, 39, 34, 56, 56, 61, 56, 62, 51, 62, 55, 33, 56, 61, 27, 28, 55, 28, 93, 61, 61, 62, 62, 39, 47,
    38, 61, 52, 80, 50, 52, 47, 34, 27, 34, 56,
)
_W_PROP_MAX = (
    50, 32, 47, 83, 80, 94, 72, 30, 44, 42, 56, 63, 31, 53, 28, 57, 69, 56, 71, 68, 64, 62, 64, 56, 77, 63, 28, 30,
    61, 62, 58, 58, 102, 68, 76, 72, 78, 71, 66, 79, 73, 55, 63, 67, 60, 91, 72, 78, 70, 78, 74, 67, 86, 73, 67, 95,
    68, 67, 83, 49, 59, 50, 69, 83, 61, 67, 62, 51, 62, 56, 50, 56, 61, 27, 33, 55, 28, 93, 61, 61, 62, 62, 43, 54,
    57, 61, 53, 80, 59, 54, 57, 57, 63, 54, 84,
)


def _char_em(ch: str, font_family: int) -> float:
    if ch == "\t":
        return 1.1
    o = ord(ch)
    fam = int(_num(font_family, DEFAULT_FONT))
    if 0x20 <= o < 0x7F:
        if fam in MONO_FONTS:
            return 0.6
        return (_W_NUNITO if fam == FONT_NUNITO else _W_PROP_MAX)[o - 0x20] / 100.0
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Cf", "Mn", "Me"):
        return 0.0
    eaw = unicodedata.east_asian_width(ch)
    cat = unicodedata.category(ch)
    if eaw in ("W", "F"):
        return 1.15 if cat == "So" else 1.0
    if eaw == "A" and cat[0] in ("S", "P"):
        return 1.0
    base = unicodedata.normalize("NFD", ch)[0]
    if base != ch and 0x20 <= ord(base) < 0x7F:
        return _char_em(base, fam)
    return 0.8


def _line_width(line: str, font_size: Number, font_family: int) -> float:
    return sum(_char_em(c, font_family) for c in line) * _num(font_size, 20)


def measure_text(text: str, font_size: Number = 20, font_family: int = DEFAULT_FONT) -> Tuple[float, float]:
    """Estimated (width, height). height = line count × fontSize × lineHeight (same formula as Excalidraw)."""
    lines = str(text).replace("\r\n", "\n").split("\n")
    w = max(_line_width(l, font_size, font_family) for l in lines)
    h = len(lines) * _num(font_size, 20) * line_height(font_family)
    return math.ceil(w), h


def wrap_text(text: str, max_width: Number, font_size: Number = 20, font_family: int = DEFAULT_FONT) -> str:
    """Wrap lines so the estimated width does not exceed max_width (at spaces; within a word if a single word is too long)."""
    out: List[str] = []
    for para in str(text).replace("\r\n", "\n").split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = word if not cur else cur + " " + word
            if _line_width(cand, font_size, font_family) <= max_width:
                cur = cand
                continue
            if cur:
                out.append(cur)
                cur = ""
            while word and _line_width(word, font_size, font_family) > max_width:   # split a long word at the character level
                n = 1
                while n < len(word) and _line_width(word[:n + 1], font_size, font_family) <= max_width:
                    n += 1
                out.append(word[:n])
                word = word[n:]
            cur = word
        out.append(cur)
    return "\n".join(out)


# ── Creating elements ────────────────────────────────────────────────
def _text_el(text: str, x: Number, y: Number, *, font_size: Number, font_family: int, color: str, align: str,
             valign: str, container_id: Optional[str], original: Optional[str] = None, **kw) -> Element:
    w, h = measure_text(text, font_size, font_family)
    e = _base("text", x, y, w, h, stroke=color, **kw)
    e.update({
        "text": text, "originalText": text if original is None else original, "fontSize": font_size,
        "fontFamily": int(font_family), "textAlign": align, "verticalAlign": valign, "containerId": container_id,
        "autoResize": True, "lineHeight": line_height(font_family),
    })
    return e


def make_text(text: str, x: Number = 0, y: Number = 0, *, font_size: Number = 20, font_family: int = DEFAULT_FONT,
              color: str = INK, align: str = "left", max_width: Optional[Number] = None, by: Optional[str] = None,
              id: Optional[str] = None, frame_id: Optional[str] = None) -> Element:
    """A single free-standing text. (x, y) is the top-left. If max_width is given, lines wrap at that width.
    If wrapped, it is created as an Excalidraw fixed-width text (autoResize:false, width=max_width) — with autoResize:true,
    when a person edits it the editor flattens originalText to one line and re-measures (Excalidraw refreshTextDimensions)."""
    raw = str(text)
    shown = wrap_text(raw, max_width, font_size, font_family) if max_width else raw
    e = _text_el(shown, x, y, font_size=font_size, font_family=font_family, color=color, align=align, valign="top",
                 container_id=None, original=raw, by=by, id=id, frame_id=frame_id)
    if shown != raw:
        e["autoResize"] = False
        e["width"] = float(max(_num(max_width), e["width"]))
    return e


def _container_dim(inner: Number, shape: str) -> float:
    """Same formula as Excalidraw computeContainerDimensionForBoundText."""
    inner = math.ceil(inner)
    pad = BOUND_TEXT_PADDING * 2
    if shape == "ellipse":
        return round((inner + pad) / math.sqrt(2) * 2)
    if shape == "arrow":
        return inner + pad * 8
    if shape == "diamond":
        return 2 * (inner + pad)
    return inner + pad


def _inner_max_width(w: Number, shape: str) -> float:
    """Same formula as Excalidraw getBoundTextMaxWidth (the width available to text inside a shape)."""
    if shape == "ellipse":
        return round(w / 2 * math.sqrt(2)) - BOUND_TEXT_PADDING * 2
    if shape == "diamond":
        return round(w / 2) - BOUND_TEXT_PADDING * 2
    return w - BOUND_TEXT_PADDING * 2


def _center_label(box: Element, label: Element) -> None:
    label["x"] = box["x"] + (box["width"] - label["width"]) / 2
    label["y"] = box["y"] + (box["height"] - label["height"]) / 2


def _add_bound(el: Element, kind: str, ref_id: str) -> None:
    old = el.get("boundElements")
    be = [b for b in (old if isinstance(old, list) else []) if isinstance(b, dict) and b.get("id") != ref_id]
    be.append({"type": kind, "id": ref_id})
    el["boundElements"] = be


MAX_ID = 64                              # id length the server accepts
MAX_CONTAINER_ID = MAX_ID - len("-label")


def label_id(container_id: str) -> str:
    """Id of the text inside a box / arrow — derived from the target (so only one remains even if two people create it at the same time)."""
    if len(container_id) > MAX_CONTAINER_ID:
        raise ValueError(T("글을 담는 요소의 id 는 %d자 이하(라벨 id `<id>-label` 이 %d자를 넘으면 서버가 버린다)",
                           "The id of an element that holds text must be at most %d characters "
                           "(the server drops a label id `<id>-label` longer than %d)") % (MAX_CONTAINER_ID, MAX_ID))
    return container_id + "-label"


def _check_container_id(id: Optional[str]) -> None:
    if id is not None:
        label_id(id)


def make_box(shape: str = "rectangle", label: Optional[str] = None, x: Number = 0, y: Number = 0,
             w: Optional[Number] = None, h: Optional[Number] = None, *, font_size: Number = 20,
             font_family: int = DEFAULT_FONT, color: str = INK, fill: str = "transparent",
             max_label_width: Number = LABEL_MAX_WIDTH, by: Optional[str] = None, id: Optional[str] = None,
             frame_id: Optional[str] = None) -> List[Element]:
    """Shape (rectangle, ellipse, diamond) + in-shape text (if any). Returns [box] or [box, text].
    Box size is the larger of w/h and "estimated text size + padding". If w is given, text wraps within it."""
    if shape not in SHAPES:
        raise ValueError(T("shape 는 %s 중 하나", "shape must be one of %s") % (", ".join(SHAPES),))
    _check_container_id(id)
    bw = float(w) if w else float(BOX_MIN[0])
    bh = float(h) if h else float(BOX_MIN[1])
    box = _base(shape, x, y, bw, bh, by=by, id=id, frame_id=frame_id, stroke=color, fill=fill, roundness=ROUNDNESS[shape])
    if label is None or str(label) == "":
        return [box]
    raw = str(label)
    limit = (_inner_max_width(bw, shape) / LABEL_SLACK) if w else max_label_width
    shown = wrap_text(raw, max(limit, font_size * 1.0), font_size, font_family)
    tw, th = measure_text(shown, font_size, font_family)
    box["width"] = max(bw, float(_container_dim(tw * LABEL_SLACK, shape)))
    box["height"] = max(bh, float(_container_dim(th, shape)) + (0 if shape != "rectangle" else 2 * BOUND_TEXT_PADDING))
    t = _text_el(shown, 0, 0, font_size=font_size, font_family=font_family, color=color, align="center", valign="middle",
                 container_id=box["id"], original=raw, by=by, id=label_id(box["id"]), frame_id=frame_id)
    _center_label(box, t)
    _add_bound(box, "text", t["id"])
    return [box, t]


def _center(e: Element) -> Tuple[float, float]:
    return _num(e.get("x")) + _num(e.get("width")) / 2, _num(e.get("y")) + _num(e.get("height")) / 2


def _edge_point(e: Element, dx: float, dy: float) -> Tuple[float, float]:
    """Point where the border meets a ray from the shape center toward (dx, dy)."""
    cx, cy = _center(e)
    hw, hh = max(_num(e.get("width")) / 2, 1e-6), max(_num(e.get("height")) / 2, 1e-6)
    if dx == 0 and dy == 0:
        return cx, cy
    kind = e.get("type")
    if kind == "ellipse":
        t = 1 / math.sqrt((dx / hw) ** 2 + (dy / hh) ** 2)
    elif kind == "diamond":
        t = 1 / (abs(dx) / hw + abs(dy) / hh)
    else:
        t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


def _route(arrow: Element, a: Optional[Element], b: Optional[Element], pa: Optional[Point] = None,
           pb: Optional[Point] = None, gap: Number = BIND_GAP) -> None:
    """Re-anchor both arrow ends (a straight line joining the centers, gap away from the border)."""
    ca = _center(a) if a is not None else (_num(pa[0]), _num(pa[1]))
    cb = _center(b) if b is not None else (_num(pb[0]), _num(pb[1]))
    dx, dy = cb[0] - ca[0], cb[1] - ca[1]
    if dx == 0 and dy == 0:
        dx = 1.0
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    if a is not None:
        sx, sy = _edge_point(a, dx, dy)
        sx, sy = sx + ux * gap, sy + uy * gap
    else:
        sx, sy = ca
    if b is not None:
        ex, ey = _edge_point(b, -dx, -dy)
        ex, ey = ex - ux * gap, ey - uy * gap
    else:
        ex, ey = cb
    if (ex - sx) * ux + (ey - sy) * uy <= 1:   # if the boxes overlap or are too close, connect center to center
        sx, sy, ex, ey = ca[0], ca[1], cb[0], cb[1]
    px, py = ex - sx, ey - sy
    arrow.update({"x": sx, "y": sy, "width": abs(px), "height": abs(py), "points": [[0.0, 0.0], [px, py]]})


def _label_on_arrow(arrow: Element, label: Element) -> None:
    pts = _points(arrow) or [(0.0, 0.0)]
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    mx, my = _num(arrow.get("x")) + (x0 + x1) / 2, _num(arrow.get("y")) + (y0 + y1) / 2
    label["x"] = mx - _num(label.get("width")) / 2
    label["y"] = my - _num(label.get("height")) / 2


def make_arrow(from_el: Union[Element, Point], to_el: Union[Element, Point], label: Optional[str] = None, *,
               font_size: Number = 16, font_family: int = DEFAULT_FONT, color: str = INK, dashed: bool = False,
               start_head: Optional[str] = None, end_head: Optional[str] = "arrow", by: Optional[str] = None,
               id: Optional[str] = None, frame_id: Optional[str] = None) -> List[Element]:
    """Straight arrow (elbowed:false) joining two elements. If an end is an (x, y) point, the arrow starts / ends at that point without binding.
    If from_el / to_el are elements, the arrow is added to their boundElements and version is bumped (in place) — send them along.
    Returns [arrow] or [arrow, label text]."""
    _check_container_id(id)
    a = from_el if isinstance(from_el, dict) else None
    b = to_el if isinstance(to_el, dict) else None
    for el in (a, b):
        if el is not None and not _sid(el.get("id")):
            raise ValueError(T("화살표 끝 요소에 id 가 없다", "An arrow endpoint element has no id"))
    arrow = _base("arrow", 0, 0, 0, 0, by=by, id=id, frame_id=frame_id, stroke=color, roundness={"type": 2})
    arrow.update({"points": [[0.0, 0.0], [0.0, 0.0]], "lastCommittedPoint": None, "startBinding": None, "endBinding": None,
                  "startArrowhead": start_head, "endArrowhead": end_head, "elbowed": False})
    if dashed:
        arrow["strokeStyle"] = "dashed"
    _route(arrow, a, b, None if a is not None else from_el, None if b is not None else to_el)
    for end, el in (("startBinding", a), ("endBinding", b)):
        if el is not None:
            arrow[end] = {"elementId": el["id"], "focus": 0, "gap": BIND_GAP}
            _add_bound(el, "arrow", arrow["id"])
            touch(el)
    out = [arrow]
    if label is not None and str(label) != "":
        raw = str(label)
        limit = max(ARROW_LABEL_WIDTH_FRACTION * arrow["width"], font_size * ARROW_LABEL_MIN_WIDTH_RATIO)
        shown = wrap_text(raw, limit, font_size, font_family)
        t = _text_el(shown, 0, 0, font_size=font_size, font_family=font_family, color=color, align="center",
                     valign="middle", container_id=arrow["id"], original=raw, by=by, id=label_id(arrow["id"]),
                     frame_id=frame_id)
        _label_on_arrow(arrow, t)
        _add_bound(arrow, "text", t["id"])
        out.append(t)
    return out


def route_via(arrow: Element, a: Element, b: Element, via: Sequence[Point], label: Optional[Element] = None,
              gap: Number = BIND_GAP) -> Element:
    """Re-route a bound arrow a → b through absolute bend points (in place; the bindings stay, so dragging a box still drags the ends).
    Used by flow layout for edges that would otherwise cut through boxes. A label goes to the middle of the polyline."""
    if not via:
        return reroute(arrow, a, b)
    ca, cb = _center(a), _center(b)
    pts = [(float(p[0]), float(p[1])) for p in via]

    def end(e, c, toward):
        dx, dy = toward[0] - c[0], toward[1] - c[1]
        if dx == 0 and dy == 0:
            dx = 1.0
        n = math.hypot(dx, dy)
        x, y = _edge_point(e, dx, dy)
        return x + dx / n * gap, y + dy / n * gap
    path = [end(a, ca, pts[0])] + pts + [end(b, cb, pts[-1])]
    x0, y0 = path[0]
    rel = [[px - x0, py - y0] for px, py in path]
    xs, ys = [p[0] for p in rel], [p[1] for p in rel]
    arrow.update({"x": x0, "y": y0, "points": rel, "width": max(xs) - min(xs), "height": max(ys) - min(ys)})
    if label is not None:
        m = len(path) // 2
        (px, py), (qx, qy) = (path[m - 1], path[m]) if len(path) % 2 == 0 else (path[m], path[m])
        label["x"] = (px + qx) / 2 - _num(label.get("width")) / 2
        label["y"] = (py + qy) / 2 - _num(label.get("height")) / 2
    return arrow


ARROW_HEADS = {"end": (None, "arrow"), "start": ("arrow", None), "both": ("arrow", "arrow"), "none": (None, None)}   # head → (start, end)


def make_line(points: Sequence[Point], *, color: str = INK, dashed: bool = False, by: Optional[str] = None,
              id: Optional[str] = None, frame_id: Optional[str] = None) -> List[Element]:
    """A free polyline (Excalidraw type "line") through absolute points — no bindings, no arrowheads. Needs 2+ points."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 2:
        raise ValueError(T("선은 점이 2개 이상", "A line needs 2 or more points"))
    x0, y0 = pts[0]
    rel = [[px - x0, py - y0] for px, py in pts]
    xs, ys = [p[0] for p in rel], [p[1] for p in rel]
    line = _base("line", x0, y0, max(xs) - min(xs), max(ys) - min(ys), by=by, id=id, frame_id=frame_id, stroke=color)
    line.update({"points": rel, "lastCommittedPoint": None, "startBinding": None, "endBinding": None,
                 "startArrowhead": None, "endArrowhead": None, "polygon": False})
    if dashed:
        line["strokeStyle"] = "dashed"
    return [line]


PAGE_SEED = 7919   # seed in web/src/features/pages/pages.js pageElementsFor = 7919 × k


def page_id(k: int) -> str:
    return "tbpage-%d" % int(k)


def next_page(elements: Iterable[Element]) -> int:
    """New page number — same rule as pages.js nextKey (max live page number + 1, skipping any number whose id is used as a frameId by an element)."""
    els = [e for e in elements or [] if isinstance(e, dict) and not e.get("isDeleted")]
    keys = [_page_key(e) for e in els if e.get("type") == "frame"]
    keys = [k for k in keys if k is not None]
    k = (int(math.floor(max(keys))) if keys else 0) + 1
    used = {_sid(e.get("frameId")) for e in els} | {_sid(e.get("id")) for e in els}
    while page_id(k) in used:
        k += 1
    return k


def make_frame(name: Optional[str] = None, x: Number = 0, y: Number = 0, w: Number = 1600, h: Number = 900, *,
               page: Optional[int] = None, existing: Optional[Iterable[Element]] = None, by: Optional[str] = None,
               id: Optional[str] = None) -> Element:
    """frame. With page=k it is a whiteboard page (same id `tbpage-<k>` as web/src/features/pages/pages.js, customData.tbPage,
    seed 7919×k, name 'page k' (Korean label)). When creating a page, pass existing=board elements:
        - If the same page is alive, ValueError (so a person's page is not overwritten) → choose a number with next_page(elements).
        - If the same id exists but is deleted, revive it by bumping version (index unchanged) — otherwise the server merge discards it."""
    custom = {"tbPage": int(page)} if page is not None else None
    fid = id or (page_id(page) if page is not None else None)
    e = _base("frame", x, y, w, h, by=by, id=fid, stroke="#bbb", custom=custom)
    e["name"] = name if name is not None else ("장 %d" % int(page) if page is not None else None)
    if page is not None:
        e["seed"] = PAGE_SEED * int(page)
    old = None
    for o in existing or []:
        if isinstance(o, dict) and o.get("id") == e["id"]:
            old = o
    if old is not None:
        if not old.get("isDeleted"):
            raise ValueError(T("%s 가 이미 보드에 있다 — next_page(elements) 로 빈 번호를 고르거나 그 장을 고친다",
                               "%s is already on the board — pick a free number with next_page(elements) or edit that page") % e["id"])
        e["version"] = int(_num(old.get("version"), 0)) + 1
        if is_valid_key(old.get("index")):
            e["index"] = old["index"]
    return e


PROPOSAL_PREFIX = "prop-"   # server.py PROP_PREFIX: the anchor of proposal <pid> is element prop-<pid>
PROPOSAL_PAD = 12           # the anchor's dashed box sits this far outside the result
PROPOSAL_COLOR = "#1d8a5b"  # web/src/features/ask/ask.css .wb-ask-proposed


def proposal_of(e: Element) -> Optional[dict]:
    """customData.cera.proposal of a proposal anchor (WP-6), or None."""
    p = _dict(_dict(_dict(e).get("customData")).get("cera")).get("proposal") if isinstance(e, dict) else None
    return p if isinstance(p, dict) and isinstance(p.get("pid"), str) else None


def result_fp(e: Element, typeset: Optional[set] = None) -> str:
    """What a person can change on a proposal's result element, as one string (server.py result_fp is the same): where it is and its text (a formula: its
    LaTeX and its turn); for an arrow, what it joins. A formula being typeset changes none of these (only its file, size and render state)."""
    def r(v):
        return round(float(v), 1) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
    if e.get("type") == "arrow":
        ends = [(e.get(k) or {}).get("elementId") if isinstance(e.get(k), dict) else None for k in ("startBinding", "endBinding")]
        if any(isinstance(i, str) and i in (typeset or ()) for i in ends):   # the typeset moves arrows bound to a formula: only what it joins
            return json.dumps(["a"] + ends, ensure_ascii=False)
        return json.dumps(["v", e.get("version")], ensure_ascii=False)
    if is_typeset(e):   # a formula: the tab's typeset changes size, file and version later - where it is, its LaTeX and its turn only
        cd = e.get("customData") if isinstance(e.get("customData"), dict) else {}
        return json.dumps([r(e.get("x")), r(e.get("y")), cd.get("latex") if isinstance(cd.get("latex"), str) else None, r(e.get("angle") or 0)],
                          ensure_ascii=False)
    return json.dumps(["v", e.get("version")], ensure_ascii=False)   # anything else: any change (size too) is a person's


def is_typeset(e) -> bool:
    """A result a tab typesets after it is placed (a formula image): its version is not the agent's to keep."""
    cd = e.get("customData") if isinstance(e.get("customData"), dict) else {}
    return e.get("type") == "image" and isinstance(cd.get("latex"), str)


def make_anchor(pid: str, result_box: Tuple[float, float, float, float], *, rid: Optional[str], sources: Sequence[str],
                results: Sequence[str], src_box: Optional[Tuple[float, float, float, float]], note: str = "",
                uncertain: Optional[list] = None, drift: int = 0, agent: str = "", by: Optional[str] = None,
                result_els: Optional[Sequence[Element]] = None) -> Element:
    """The proposal anchor: a locked dashed box around the result (people cannot grab or erase it by accident). The card on screen is drawn
    over it; exports leave it out (web/src/features/export/exporters.js). Its record is what the server's apply / revert act on."""
    x0, y0, x1, y1 = result_box
    pad = PROPOSAL_PAD
    rec = {"pid": pid, "rid": rid, "sources": list(sources), "results": list(results), "status": "proposed",
           "home": [x0 - pad, y0 - pad], "src_box": list(src_box) if src_box else None, "deleted": [],
           "note": note, "uncertain": list(uncertain or []), "drift": int(drift), "agent": agent}
    if result_els is not None:   # what the result looked like when placed: the agent may withdraw it only while nobody changed it
        ts = {r["id"] for r in result_els if isinstance(r, dict) and is_typeset(r)}
        rec["fp"] = {r["id"]: result_fp(r, ts) for r in result_els if isinstance(r, dict) and isinstance(r.get("id"), str)}
    e = _base("rectangle", x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad, by=by, id=PROPOSAL_PREFIX + pid,
              stroke=PROPOSAL_COLOR, roundness={"type": 3}, custom={"cera": {"proposal": rec}})
    e.update(strokeStyle="dashed", locked=True, opacity=60)
    return e


def make_image(file_id: str, w: Number, h: Number, x: Number = 0, y: Number = 0, *, latex: Optional[str] = None,
               by: Optional[str] = None, id: Optional[str] = None, frame_id: Optional[str] = None) -> Element:
    """Image element. The file (data URL) is uploaded separately via POST /files — before the element. With latex, it is displayed as a formula."""
    e = _base("image", x, y, w, h, by=by, id=id, frame_id=frame_id, stroke="transparent",
              custom={"latex": latex} if latex else None)
    e.update({"fileId": file_id, "status": "saved", "scale": [1, 1], "crop": None})
    return e


MATH_LINES = 8          # placeholder shows at most this many source lines
MATH_LINE_CHARS = 120   # ... each clipped to this many characters


def math_placeholder_svg(latex: str, size: Number = 32, color: str = INK) -> Tuple[str, float, float]:
    """Stand-in picture for a formula until a person's tab typesets it (09 plan B5): the LaTeX source in a monospace font inside a dashed
    frame, so the board shows the formula even with no tab open. -> (svg text, width, height). color must already be a checked #hex."""
    import re
    from xml.sax.saxutils import escape
    clean = re.sub("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", str(latex).replace("\r", "").replace("\t", " "))
    lines = [ln[:MATH_LINE_CHARS] for ln in clean.split("\n")][:MATH_LINES] or [""]
    fs = max(10.0, min(28.0, float(size) * 0.55))
    pad = 8.0
    w = max(len(ln) for ln in lines) * fs * 0.62 + 2 * pad + 4
    h = len(lines) * fs * 1.3 + 2 * pad
    rows = "".join('<text x="%g" y="%g">%s</text>' % (pad + 2, pad + fs * (1.3 * i + 1.0), escape(ln)) for i, ln in enumerate(lines))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g" viewBox="0 0 %g %g">'
           '<rect x="1" y="1" width="%g" height="%g" rx="6" fill="none" stroke="#9a9a9a" stroke-dasharray="5 4"/>'
           '<g font-family="ui-monospace, Menlo, Consolas, monospace" font-size="%g" fill="%s">%s</g></svg>'
           % (w, h, w, h, w - 2, h - 2, fs, color, rows))
    return svg, round(w, 2), round(h, 2)


def tag_graph(el: Element, g: str, k: str, direction: Optional[str] = None) -> Element:
    """Mark a flow node / edge as part of graph g (customData.cera.graph = {g, k[, d]}) — mermaid / draw.io export find the graph by it."""
    cd = dict(el.get("customData") or {})
    cera = dict(cd.get("cera") or {})
    cera["graph"] = {"g": g, "k": k, **({"d": direction} if direction else {})}
    cd["cera"] = cera
    el["customData"] = cd
    return el


# ── z-order (index) ─────────────────────────────────────────────────
def index_between(a: Optional[str], b: Optional[str]) -> str:
    """Key between a and b (same result as JS fractional-indexing generateKeyBetween)."""
    return generate_key_between(a, b)


def _top_key(elements: Iterable[Element]) -> Optional[str]:
    """Largest index on the board. If m elements have a missing or broken index, Excalidraw (syncInvalidIndices) assigns them
    after the end; accounting for that worst case, use the last of "m keys after the largest key" (deleted elements count too)."""
    top, missing = None, 0
    for e in elements or []:
        k = e.get("index") if isinstance(e, dict) else None
        if is_valid_key(k):
            if top is None or k > top:
                top = k
        else:
            missing += 1
    if missing:
        top = generate_n_keys_between(top, None, missing)[-1]
    return top


def next_index(elements: Iterable[Element]) -> str:
    """The key after the board's largest index — a new element lands above existing drawings (z)."""
    return generate_key_between(_top_key(elements), None)


def assign_indices(elements: Iterable[Element], items: Sequence[Element]) -> List[Element]:
    """Assign items indexes larger than existing elements, in items order (in place)."""
    keys = generate_n_keys_between(_top_key(elements), None, len(items))
    for e, k in zip(items, keys):
        e["index"] = k
    return list(items)


# ── Positioning ─────────────────────────────────────────────────────
LINEAR = ("arrow", "line", "freedraw")


def element_bbox(e: Element) -> Optional[Tuple[float, float, float, float]]:
    """(x0, y0, x1, y1). Lines, arrows and handwriting use points. None if x / y are not finite numbers (broken element).
    Broken values among size / points are treated as 0 / skipped."""
    if not isinstance(e, dict) or not _ok_num(e.get("x")) or not _ok_num(e.get("y")):
        return None
    x, y = float(e["x"]), float(e["y"])
    pts = _points(e) if e.get("type") in LINEAR else []
    if pts:
        corners = [(x + px, y + py) for px, py in pts]
    else:
        w, h = _num(e.get("width")), _num(e.get("height"))
        corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
    x0, y0 = min(p[0] for p in corners), min(p[1] for p in corners)
    x1, y1 = max(p[0] for p in corners), max(p[1] for p in corners)
    ang = _num(e.get("angle"))
    if ang and math.isfinite(x1 - x0) and math.isfinite(y1 - y0):   # Excalidraw rotates around the center of the unrotated bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ca, sa = math.cos(ang), math.sin(ang)
        rot = [(cx + (px - cx) * ca - (py - cy) * sa, cy + (px - cx) * sa + (py - cy) * ca) for px, py in corners]
        x0, y0 = min(p[0] for p in rot), min(p[1] for p in rot)
        x1, y1 = max(p[0] for p in rot), max(p[1] for p in rot)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1, x1 - x0, y1 - y0)):   # e.g. x = width = 1e308 → treated as broken
        return None
    return x0, y0, x1, y1


def _bbox0(e: Element) -> Tuple[float, float, float, float]:
    return element_bbox(e) or (0.0, 0.0, 0.0, 0.0)


def scene_bbox(elements: Iterable[Element]) -> Optional[Tuple[float, float, float, float]]:
    """Bounds of the non-deleted elements. Broken elements are skipped."""
    boxes = [element_bbox(e) for e in elements or [] if isinstance(e, dict) and not e.get("isDeleted")]
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _bound_ids(e: Element) -> List[Optional[str]]:
    return [_sid(_dict(e.get("startBinding")).get("elementId")), _sid(_dict(e.get("endBinding")).get("elementId"))]


# Top-left of the first group on an empty board. A human screen initially puts scene (0, 0) at the top-left — placing at (0, 0) would hide it under the top button row on a phone (390px)
# (☰, undo, trash, expiry, 'n people'; bottom edge ~52px) and under the desktop ☰ and '+ new page' pill (bottom edge ~62px)
# (found in a UI test). So offset by the margin below and to the left of that. Always the same value (deterministic).
EMPTY_ORIGIN = (40.0, 120.0)


def place(elements: Sequence[Element], items: Sequence[Element], at: Union[str, Point] = "right",
          gap: Number = 80) -> List[Element]:
    """Move items (a new group of elements) to their place on the board and assign index (in place). Returns items as is.
    at: "right" (right of existing drawings) · "below" · (x, y) (top-left of the group). On an empty board, EMPTY_ORIGIN (below the top button row).
    - Ids in items that are already on the board (e.g. an existing box modified by make_arrow) are neither moved nor re-indexed.
    - The group bounds (reference point) are measured excluding arrows bound to elements outside the group and their labels (those arrows are still at their old position).
    - After moving, bound arrows get both ends re-anchored and their labels are moved to the center."""
    items = list(items)
    board_ids = {_sid(e.get("id")) for e in elements or [] if isinstance(e, dict)}
    movable = [e for e in items if e.get("id") not in board_ids]
    mids = {e["id"] for e in movable}
    outward = {e["id"] for e in movable
               if e.get("type") in ("arrow", "line") and any(i is not None and i not in mids for i in _bound_ids(e))}
    anchor = [e for e in movable if e["id"] not in outward and _sid(e.get("containerId")) not in outward]
    mine = scene_bbox(anchor)
    rest = [e for e in elements or [] if isinstance(e, dict) and _sid(e.get("id")) not in mids]
    if mine is not None:
        board = scene_bbox(rest)
        if isinstance(at, (tuple, list)):
            tx, ty = _num(at[0]), _num(at[1])
        elif board is None:
            tx, ty = EMPTY_ORIGIN
        elif at == "below":
            tx, ty = board[0], board[3] + gap
        elif at == "right":
            tx, ty = board[2] + gap, board[1]
        else:
            raise ValueError(T('at 은 "right" · "below" · (x, y)', 'at must be "right", "below" or (x, y)'))
        dx, dy = tx - mine[0], ty - mine[1]
        for e in movable:
            e["x"] = _num(e.get("x")) + dx
            e["y"] = _num(e.get("y")) + dy
    by_id = {e["id"]: e for e in rest if _sid(e.get("id"))}
    by_id.update({e["id"]: e for e in items if _sid(e.get("id"))})
    for e in movable:
        if e.get("type") != "arrow":
            continue
        sa, sb = _bound_ids(e)
        a, b = by_id.get(sa) if sa else None, by_id.get(sb) if sb else None
        if a is not None or b is not None:
            reroute(e, a, b)
            for t in movable:
                if t.get("containerId") == e["id"]:
                    _label_on_arrow(e, t)
    assign_indices(elements, movable)
    return items


def reroute(arrow: Element, a: Optional[Element], b: Optional[Element]) -> Element:
    """After the bound end elements (a, b; None if absent) have moved, re-anchor both arrow ends (in place, version unchanged).
    Ends without a binding stay where they are. Label text is moved separately (label_on_arrow)."""
    pts = _points(arrow) or [(0.0, 0.0)]
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    ax, ay = _num(arrow.get("x")), _num(arrow.get("y"))
    _route(arrow, a, b, (ax + x0, ay + y0), (ax + x1, ay + y1))
    return arrow


def label_on_arrow(arrow: Element, label: Element) -> Element:
    """Move an arrow label text to the arrow center (in place)."""
    _label_on_arrow(arrow, label)
    return label


# ── Board summary (describe) ────────────────────────────────────────
SHAPE_WORD = {"rectangle": "상자", "ellipse": "타원", "diamond": "마름모"}   # keys = shape types; words come from shape_word()
_SHAPE_EN = {"rectangle": "box", "ellipse": "ellipse", "diamond": "diamond"}


def shape_word(t):
    return T(SHAPE_WORD[t], _SHAPE_EN[t])


def _clip(s, n: int = 120) -> str:
    s = " / ".join(x.strip() for x in str(s if s is not None else "").replace("\r\n", "\n").split("\n"))
    return s if len(s) <= n else s[: n - 1] + "…"


def _text_of(t: Optional[Element]) -> str:
    """The original text written by the person — originalText, not the lines (text) wrapped by the screen to fit the width. Only line breaks from the original remain."""
    if not t:
        return ""
    o = t.get("originalText")
    if isinstance(o, str) and o:
        return o
    v = t.get("text")
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _type(e: Element) -> str:
    t = e.get("type")
    return t if isinstance(t, str) else "?"


def _page_key(e: Element) -> Optional[float]:
    cd = _dict(e.get("customData"))
    if "tbPage" not in cd or isinstance(cd["tbPage"], bool):
        return None
    try:
        k = float(cd["tbPage"])
    except (TypeError, ValueError):
        return None
    return k if math.isfinite(k) else None


def _rows(items: List[Tuple[Element, Tuple[float, float, float, float]]]) -> List[Element]:
    """Top→bottom, left→right. Ordered by top edge; starts on the same row if it begins above the center of the first element of the previous row."""
    items = sorted(items, key=lambda p: (p[1][1], p[1][0]))
    rows: List[List[Tuple[Element, Tuple[float, float, float, float]]]] = []
    for it in items:
        if rows:
            first = rows[-1][0][1]
            if it[1][1] < (first[1] + first[3]) / 2:
                rows[-1].append(it)
                continue
        rows.append([it])
    out: List[Element] = []
    for r in rows:
        out.extend(e for e, _ in sorted(r, key=lambda p: (p[1][0], p[1][1])))
    return out


def _fmt_box(b: Tuple[float, float, float, float]) -> List[int]:
    """[x, y, w, h] in whole units that cover b (floor the start, ceil the end — rounding each could leave drawn points outside)."""
    x0, y0 = int(math.floor(b[0] + 1e-6)), int(math.floor(b[1] + 1e-6))
    x1, y1 = int(math.ceil(b[2] - 1e-6)), int(math.ceil(b[3] - 1e-6))
    return [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]


def at_text(b: Optional[Tuple[float, float, float, float]]) -> str:
    """'@(x,y w×h)' in whole board units — so a text-only client can pair labels with drawings by position."""
    if not b:
        return ""
    x, y, w, h = _fmt_box(b)
    return "@(%d,%d %d×%d)" % (x, y, w, h)


INK_GAP = 24          # strokes whose points come within about this many px of each other are one group
INK_SAMPLES = 128     # points looked at per stroke
INK_LINES = 12        # group lines per page in the summary; the rest are folded into one line


def ink_groups(strokes: Sequence[Element], gap: Number = INK_GAP) -> List[List[Element]]:
    """Group handwriting strokes by where they are only (points within ~gap px, grid cells) — not by time: slow drawing or a later
    edit must not split one sketch. Order: reading order of the group bounds. Broken strokes (no coordinates) form no group."""
    ok = [e for e in strokes if element_bbox(e) is not None]
    sessions: List[List[Element]] = [ok] if ok else []
    g = float(gap) if gap and gap > 0 else float(INK_GAP)
    out: List[List[Element]] = []
    for ses in sessions:
        parent = list(range(len(ses)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        cell: Dict[Tuple[int, int], int] = {}
        for i, e in enumerate(ses):
            bb = element_bbox(e)
            x, y = float(e["x"]), float(e["y"])
            pts = _points(e) or [(0.0, 0.0)]
            step = max(1, len(pts) // INK_SAMPLES)
            seen = set()
            ang = _num(e.get("angle"))
            if ang:   # rotated stroke: use its rotated bounds' corners and center (rare — strokes are drawn unrotated)
                samples = [(bb[0], bb[1]), (bb[2], bb[1]), (bb[0], bb[3]), (bb[2], bb[3]), ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)]
            else:
                samples = [(x + px, y + py) for px, py in pts[::step] + [pts[-1]]]
            for ax, ay in samples:
                if abs(ax) > 1e12 or abs(ay) > 1e12:
                    continue
                c = (int(math.floor(ax / g)), int(math.floor(ay / g)))
                if c in seen:
                    continue
                seen.add(c)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        j = cell.get((c[0] + dx, c[1] + dy))
                        if j is not None:
                            parent[find(j)] = find(i)
                cell.setdefault(c, i)
        groups: Dict[int, List[Element]] = {}
        for i, e in enumerate(ses):
            groups.setdefault(find(i), []).append(e)
        out.extend(groups.values())
    boxes = [(grp, scene_bbox(grp) or (0.0, 0.0, 0.0, 0.0)) for grp in out]
    order = _rows([({"id": str(n)}, b) for n, (_, b) in enumerate(boxes)])
    return [boxes[int(o["id"])][0] for o in order]


def describe(elements: Iterable[Element], page: Optional[int] = None) -> Tuple[str, dict]:
    """Human-readable board summary (text) and a structure for tools (structured). Deleted elements are skipped.
    In page order (frame customData.tbPage), top→bottom and left→right within a page. With page=k, only that page.
    Text is written as the original (originalText). Does not crash on broken elements (no coordinates, NaN, odd types)."""
    live = [e for e in elements or [] if isinstance(e, dict) and not e.get("isDeleted") and _sid(e.get("id"))]
    props = [e for e in live if proposal_of(e)]   # proposal anchors (WP-6): one '제안' line each at the end, not a box
    live = [e for e in live if not proposal_of(e)]
    by_id = {e["id"]: e for e in live}
    labels: Dict[str, Element] = {}
    orphans = set()   # bound text whose container element is gone (deleted)
    for e in live:
        cid = _sid(e.get("containerId"))
        if _type(e) == "text" and cid:
            if cid in by_id:
                labels[cid] = e
            else:
                orphans.add(e["id"])
    pages = sorted((e for e in live if _type(e) == "frame" and _page_key(e) is not None),
                   key=lambda e: (_page_key(e), _num(e.get("x")), _num(e.get("y")), e["id"]))
    page_ids = {p["id"] for p in pages}

    def page_of(e: Element, follow: bool = True) -> Optional[str]:
        fid = _sid(e.get("frameId"))
        if fid in page_ids:
            return fid
        if follow:   # follow only one step (so broken elements pointing at each other do not loop forever)
            cid = _sid(e.get("containerId"))
            if _type(e) == "text" and cid in by_id:
                return page_of(by_id[cid], False)
            if _type(e) == "arrow":
                a = by_id.get(_bound_ids(e)[0] or "")
                if a is not None:
                    return page_of(a, False)
        b = element_bbox(e)
        if b is None:
            return None
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        for p in pages:
            pb = element_bbox(p)
            if pb is not None and pb[0] <= cx <= pb[2] and pb[1] <= cy <= pb[3]:
                return p["id"]
        return None

    def name_of(e: Optional[Element]) -> str:
        if e is None:
            return T("(빈 곳)", "(empty spot)")
        if e["id"] in labels:
            return '"%s"' % _clip(_text_of(labels[e["id"]]), 60)
        if _type(e) == "text":
            return '"%s"' % _clip(_text_of(e), 60)
        cd = _dict(e.get("customData"))
        if _type(e) == "image" and cd.get("latex"):
            return "$%s$" % _clip(cd["latex"], 60)
        return "[%s] %s" % (e["id"], shape_word(_type(e)) if _type(e) in SHAPE_WORD else _type(e))

    groups: Dict[Optional[str], dict] = {p["id"]: {"items": [], "links": [], "strokes": []} for p in pages}
    groups[None] = {"items": [], "links": [], "strokes": []}
    counts = {"pages": len(pages), "boxes": 0, "texts": 0, "math": 0, "images": 0, "links": 0, "lines": 0, "strokes": 0,
              "orphan_labels": 0, "other": 0}
    for e in live:
        t = _type(e)
        if t == "frame" and e["id"] in page_ids:
            continue
        if t == "text" and _sid(e.get("containerId")) in labels and labels[_sid(e.get("containerId"))] is e:
            continue   # label — attached to the box / arrow line
        g = groups[page_of(e)]
        if t == "arrow":
            g["links"].append(e)
            counts["links"] += 1
        elif t == "freedraw":
            g["strokes"].append(e)
            counts["strokes"] += 1
        else:
            g["items"].append(e)
            cd = _dict(e.get("customData"))
            if t in SHAPE_WORD:
                counts["boxes"] += 1
            elif e["id"] in orphans:
                counts["orphan_labels"] += 1
            elif t == "text":
                counts["texts"] += 1
            elif t == "image" and cd.get("latex"):
                counts["math"] += 1
            elif t == "image":
                counts["images"] += 1
            elif t == "line":
                counts["lines"] += 1
            else:
                counts["other"] += 1

    s_items: List[dict] = []
    s_links: List[dict] = []
    s_strokes: List[dict] = []
    s_pages: List[dict] = []
    lines: List[str] = []
    if not live:
        lines.append(T("보드가 비어 있다.", "The board is empty."))
    else:
        lines.append(T("보드 요약 — 장 %d · 상자 %d · 글 %d · 수식 %d · 그림 %d · 연결 %d · 손글씨 획 %d",
                       "Board summary — pages %d · boxes %d · text %d · math %d · images %d · arrows %d · ink strokes %d") % (
            counts["pages"], counts["boxes"], counts["texts"], counts["math"], counts["images"], counts["links"],
            counts["strokes"]))
    order: List[Tuple[Optional[str], str]] = []
    for p in pages:
        k = _page_key(p)
        kk = int(k) if k is not None and float(k).is_integer() else k
        order.append((p["id"], T('[장 %s] "%s" (%s)', '[page %s] "%s" (%s)') % (kk, _clip(p.get("name") or "", 40), p["id"])))
        s_pages.append({"id": p["id"], "k": kk, "name": p.get("name") if isinstance(p.get("name"), str) else None,
                        "bbox": _fmt_box(_bbox0(p))})
    if any(groups[None].values()):
        order.append((None, T("[장 밖]", "[outside pages]") if pages else ""))
    want = None
    if page is not None:
        want = next((p["id"] for p in pages if _page_key(p) == float(page)), "__none__")
    for pid, head in order:
        if want is not None and pid != want:
            continue
        g = groups[pid]
        ind = "  " if head else ""
        if head:
            lines.append(head)
        if not any(g.values()):
            lines.append(ind + T("(비어 있음)", "(empty)"))
        seq = _rows([(e, _bbox0(e)) for e in g["items"]])
        for e in seq:
            t = _type(e)
            cd = _dict(e.get("customData"))
            item = {"id": e["id"], "type": t, "page": pid, "bbox": _fmt_box(_bbox0(e))}
            if t in SHAPE_WORD:
                lab = labels.get(e["id"])
                item.update(kind="box", shape=t, text=_text_of(lab) if lab else None)
                item["label_id"] = lab["id"] if lab else None
                line = ind + "[%s] %s%s" % (e["id"], shape_word(t), ' "%s"' % _clip(_text_of(lab)) if lab else "")
            elif e["id"] in orphans:
                item.update(kind="orphan_label", text=_text_of(e), container=_sid(e.get("containerId")))
                line = ind + T('[%s] 떨어진 라벨 "%s"(담던 요소가 없다)', '[%s] detached label "%s" (its container is gone)') \
                    % (e["id"], _clip(_text_of(e)))
            elif t == "text":
                item.update(kind="text", text=_text_of(e))
                line = ind + T('[%s] 글 "%s"', '[%s] text "%s"') % (e["id"], _clip(_text_of(e)))
            elif t == "image" and cd.get("latex"):
                item.update(kind="math", latex=str(cd["latex"]))
                line = ind + T("[%s] 수식 $%s$", "[%s] math $%s$") % (e["id"], _clip(cd["latex"], 200))
                render = _dict(cd.get("cera")).get("render")
                if render in ("pending", "error"):   # MCP formula (B5): not typeset yet, or MathJax refused it
                    item["render"] = render
                    line += T(" (렌더 대기)", " (render pending)") if render == "pending" else \
                        T(" (LaTeX 오류: %s)", " (LaTeX error: %s)") % _clip(str(_dict(cd.get("cera")).get("message") or ""), 120)
            elif t == "image":
                item.update(kind="image")
                line = ind + T("[%s] 그림", "[%s] image") % e["id"]
            elif t == "line":
                item.update(kind="line")
                line = ind + T("[%s] 선", "[%s] line") % e["id"]
            elif t == "frame":
                item.update(kind="frame", text=e.get("name") if isinstance(e.get("name"), str) else None)
                line = ind + T('[%s] 틀 "%s"', '[%s] frame "%s"') % (e["id"], _clip(e.get("name") or "", 40))
            else:
                item.update(kind="other")
                line = ind + "[%s] %s%s" % (e["id"], t, " " + str(e["link"]) if e.get("link") else "")
            lines.append(line + (" " + at_text(element_bbox(e)) if element_bbox(e) else ""))
            s_items.append(item)
        pos = {e["id"]: i for i, e in enumerate(seq)}

        def link_key(a: Element):
            b = _bbox0(a)
            return (pos.get(_bound_ids(a)[0] or "", len(pos)), b[1], b[0])

        for a in sorted(g["links"], key=link_key):
            sid, eid = _bound_ids(a)
            src, dst = by_id.get(sid or ""), by_id.get(eid or "")
            lab = labels.get(a["id"])
            hs, he = bool(a.get("startArrowhead")), bool(a.get("endArrowhead"))
            arrow_word = "↔" if hs and he else ("→" if he else ("←" if hs else "—"))
            lines.append(ind + T("[%s] 연결: %s %s %s%s", "[%s] arrow: %s %s %s%s")
                         % (a["id"], name_of(src), arrow_word, name_of(dst),
                            T(' (라벨 "%s")', ' (label "%s")') % _clip(_text_of(lab), 60) if lab else "") + (" " + at_text(element_bbox(a)) if element_bbox(a) else ""))
            s_links.append({"id": a["id"], "from": src["id"] if src else None, "to": dst["id"] if dst else None,
                            "from_name": name_of(src), "to_name": name_of(dst),
                            "label": _text_of(lab) if lab else None,
                            "label_id": lab["id"] if lab else None, "page": pid})
        if g["strokes"]:
            grps = ink_groups(g["strokes"])
            grouped = {s["id"] for grp in grps for s in grp}
            loose = [s for s in g["strokes"] if s["id"] not in grouped]   # broken strokes (no coordinates)
            show, rest = grps[:INK_LINES], grps[INK_LINES:]
            for grp in show:
                bb = scene_bbox(grp)
                lines.append(ind + T("손글씨 획 %d개 %s — 글로 읽을 수 없음, board_snapshot 으로 본다",
                                     "%d ink stroke(s) %s — not readable as text, look with board_snapshot") % (len(grp), at_text(bb)))
                s_strokes.append({"page": pid, "count": len(grp), "bbox": _fmt_box(bb) if bb else None,
                                  "ids": [s["id"] for s in grp]})
            more = [s for grp in rest for s in grp] + loose
            if more:
                bb = scene_bbox(more)
                if rest:
                    lines.append(ind + T("그 밖 손글씨 획 %d개(묶음 %d)", "%d more ink stroke(s) (%d group(s))") % (len(more), len(rest))
                                 + (" " + at_text(bb) if bb else ""))
                else:   # only strokes with broken coordinates
                    lines.append(ind + T("손글씨 획 %d개(위치를 알 수 없음)", "%d ink stroke(s) (position unknown)") % len(loose))
                s_strokes.append({"page": pid, "count": len(more), "bbox": _fmt_box(bb) if bb else None,
                                  "ids": [s["id"] for s in more], "rest": True})
    s_props = []
    for e in sorted(props, key=lambda e: (_num(e.get("y")), _num(e.get("x")))):
        p = proposal_of(e)
        st = "applied" if p.get("status") == "applied" else "proposed"
        src, res = p.get("sources"), p.get("results")
        n_src, n_res = len(src) if isinstance(src, list) else 0, len(res) if isinstance(res, list) else 0
        line = T("제안 [%s] %s · 원본 %d개 → 결과 %d개", "proposal [%s] %s · %d original(s) → %d result element(s)") % (
            _clip(p["pid"], 64), T("바꿈(원래대로 가능)", "applied (can be reverted)") if st == "applied" else T("사람이 고르기를 기다림", "waiting for a person"),
            n_src, n_res)
        if isinstance(p.get("rid"), str):
            line += T(" · 요청 [%s]", " · request [%s]") % _clip(p["rid"], 40)
        if isinstance(p.get("drift"), int) and p["drift"] > 0:
            line += T(" · 읽은 뒤 바뀜 %d", " · changed after reading: %d") % p["drift"]
        if isinstance(p.get("note"), str) and p["note"]:
            line += T(" · 메모 %s", " · note %s") % json.dumps(_clip(p["note"], 200), ensure_ascii=False)
        b = element_bbox(e)
        lines.append(line + (" " + at_text(b) if b else ""))
        s_props.append({"pid": p["pid"], "id": e["id"], "status": st, "request": p.get("rid") if isinstance(p.get("rid"), str) else None,
                        "sources": n_src, "results": n_res, "bbox": _fmt_box(b) if b else None})
    whole = scene_bbox(live)
    structured = {"counts": counts, "pages": s_pages, "items": s_items, "links": s_links, "strokes": s_strokes,
                  "bbox": _fmt_box(whole) if whole else None}
    if s_props:
        structured["proposals"] = s_props
    return "\n".join(lines), structured
