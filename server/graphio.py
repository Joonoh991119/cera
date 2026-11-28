# SPDX-License-Identifier: MIT
"""Graphs in and out of a board (09 plan WP-4) — Python standard library only (runs on 3.9).

graph_from_elements  board elements → {dir, nodes, edges}: nodes are flow nodes (customData.cera.graph) and every shape that an arrow binds at
                     both ends; edges are those both-ends-bound arrows (head from the arrowheads)
to_mermaid           that graph → mermaid `flowchart` text (labels always quoted)
parse_mermaid        a small flowchart parser (what to_mermaid writes, plus the common hand-written forms) → the same graph shape
to_drawio            board elements → uncompressed draw.io (mxGraph) XML in board coordinates; plain-text labels (no html=1)
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape, quoteattr

SHAPES = ("rectangle", "ellipse", "diamond")
FLOW_SHAPES = ("box", "round", "ellipse", "diamond")
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")   # not allowed anywhere in XML 1.0


def xml_text(s) -> str:
    """Text with the characters XML 1.0 forbids removed (a label with a form feed must not break the file)."""
    return _XML_BAD.sub("", str(s))


_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
_RESERVED = {"end", "graph", "subgraph", "flowchart", "style", "class", "classdef", "click", "linkstyle", "direction", "default"}


def _cd(e) -> dict:
    c = e.get("customData")
    return c if isinstance(c, dict) else {}


def _tag(e) -> Optional[dict]:
    g = (_cd(e).get("cera") or {}) if isinstance(_cd(e).get("cera"), dict) else {}
    t = g.get("graph")
    return t if isinstance(t, dict) else None


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and abs(f) != float("inf") else 0.0


def flow_shape(e) -> str:
    """Element → flow shape word (box | round | ellipse | diamond)."""
    t = e.get("type")
    if t == "rectangle":
        return "round" if e.get("roundness") else "box"
    return t if t in ("ellipse", "diamond") else "box"


def _labels(els) -> Dict[str, str]:
    out = {}
    for e in els:
        if e.get("type") == "text" and isinstance(e.get("containerId"), str):
            t = e.get("originalText") if isinstance(e.get("originalText"), str) else e.get("text")
            out[e["containerId"]] = str(t or "")
    return out


def _live(els) -> List[dict]:
    return [e for e in els or [] if isinstance(e, dict) and not e.get("isDeleted") and isinstance(e.get("id"), str)]


def _ends(a) -> Tuple[Optional[str], Optional[str]]:
    sb, eb = a.get("startBinding"), a.get("endBinding")
    s = sb.get("elementId") if isinstance(sb, dict) else None
    t = eb.get("elementId") if isinstance(eb, dict) else None
    return (s if isinstance(s, str) else None), (t if isinstance(t, str) else None)


def _head(a) -> str:
    s, e = bool(a.get("startArrowhead")), bool(a.get("endArrowhead"))
    return "both" if s and e else "end" if e else "start" if s else "none"


def graph_from_elements(els, ids=None) -> dict:
    """-> {dir, nodes:[{id, key, label, shape, x, y, w, h}], edges:[{id, from, to, label, head}]} (from/to are node keys).
    ids: only these node elements (and the edges among them)."""
    live = _live(els)
    by_id = {e["id"]: e for e in live}
    labels = _labels(live)
    want = set(ids) if ids is not None else None   # [] = nothing (an empty page), None = no filter
    arrows = []
    for a in live:
        if a.get("type") != "arrow":
            continue
        s, t = _ends(a)
        if s in by_id and t in by_id and by_id[s].get("type") in SHAPES and by_id[t].get("type") in SHAPES and s != t:
            arrows.append((a, s, t))
    node_ids = {e["id"] for e in live if e.get("type") in SHAPES and _tag(e)}
    node_ids |= {s for _, s, _ in arrows} | {t for _, _, t in arrows}
    if want is not None:
        node_ids &= want
    nodes = sorted((by_id[i] for i in node_ids), key=lambda e: (round(_num(e.get("y")) / 20), _num(e.get("x")), e["id"]))
    keys: Dict[str, str] = {}
    used = set()
    for n, e in enumerate(nodes, 1):
        k = (_tag(e) or {}).get("k")
        if not (isinstance(k, str) and _KEY.match(k) and k.lower() not in _RESERVED and k not in used):
            k = "n%d" % n
            while k in used:
                k += "_"
        used.add(k)
        keys[e["id"]] = k
    direction = "TD"
    for e in nodes:
        d = (_tag(e) or {}).get("d")
        if d in ("TD", "LR"):
            direction = d
            break
    out_nodes = [{"id": e["id"], "key": keys[e["id"]], "label": labels.get(e["id"], ""), "shape": flow_shape(e),
                  "x": _num(e.get("x")), "y": _num(e.get("y")), "w": _num(e.get("width")), "h": _num(e.get("height"))} for e in nodes]
    edges = []
    for a, s, t in arrows:
        if s not in keys or t not in keys:
            continue
        h = _head(a)
        if h == "start":   # an arrow drawn backwards: same meaning as an end-headed arrow the other way
            s, t, h = t, s, "end"
        edges.append({"id": a["id"], "from": keys[s], "to": keys[t], "label": labels.get(a["id"], ""), "head": h})
    return {"dir": direction, "nodes": out_nodes, "edges": edges}


# ------------------------------------------------------------------ mermaid out
def _mq(s: str) -> str:
    """A label inside mermaid double quotes."""
    s = str(s).replace("\r", "")
    return '"' + s.replace("&", "#amp;").replace('"', "#quot;").replace("|", "#124;").replace("\n", "<br>") + '"'


_OPEN = {"box": ("[", "]"), "round": ("(", ")"), "ellipse": ("((", "))"), "diamond": ("{", "}")}
_LINK = {"end": "-->", "none": "---", "both": "<-->"}


def to_mermaid(g: dict) -> str:
    lines = ["flowchart " + (g.get("dir") if g.get("dir") in ("TD", "LR") else "TD")]
    for n in g["nodes"]:
        o, c = _OPEN.get(n.get("shape"), _OPEN["box"])
        lines.append("  %s%s%s%s" % (n["key"], o, _mq(n.get("label") or n["key"]), c))
    for e in g["edges"]:
        link = _LINK.get(e.get("head"), "-->")
        lab = "|%s|" % _mq(e["label"]) if e.get("label") else ""
        lines.append("  %s %s%s %s" % (e["from"], link, lab, e["to"]))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ mermaid in
class MermaidError(ValueError):
    pass


_NODE = re.compile(r"""\s*([A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)\s*
    (?:\(\((?P<e>"[^"]*"|[^)]*)\)\)      # ((ellipse))
     |\(\[(?P<s>"[^"]*"|[^\]]*)\]\)      # ([stadium]) → round
     |\[\[(?P<sub>"[^"]*"|[^\]]*)\]\]    # [[subroutine]] → box
     |\[(?P<b>"[^"]*"|[^\]]*)\]          # [box]
     |\((?P<r>"[^"]*"|[^)]*)\)           # (round)
     |\{(?P<d>"[^"]*"|[^}]*)\}           # {diamond}
    )?""", re.X)
_EDGE = re.compile(r"""\s*(?P<l1><)?(?P<op>-->|---|==>|===|-\.->|-\.-)(?:\|(?P<lab>"[^"]*"|[^|]*)\|)?""")
_EDGE_TEXT = re.compile(r"\s*(<)?(--|==)(?![->=.])")   # start of "-- text -->": the end is found with str.find (no regex backtracking)


def _edge(s: str, pos: int, blank: Optional[str] = None):
    """-> (back, op, label or None, end) or None. Linear in the statement length (review r1: a lazy regex here took seconds on 2000 spaces).
    blank: s with quoted text blanked (_blank_quotes) — operators and '|' are looked for there, so a quoted label may contain them."""
    b = blank if blank is not None else _blank_quotes(s)
    m = _EDGE.match(s, pos)
    if m:
        return bool(m.group("l1")), m.group("op"), m.group("lab"), m.end()
    m = _EDGE_TEXT.match(s, pos)
    if not m:
        return None
    ends = [(j, op) for op in ("-->", "---", "==>", "===") for j in (b.find(op, m.end()),) if j >= 0]
    if not ends:
        return None
    j, op = min(ends)
    lab = s[m.end():j].strip()
    if not lab or "|" in b[m.end():j]:
        return None
    return bool(m.group(1)), op, lab, j + len(op)


def _blank_quotes(s: str) -> str:
    """The statement with everything inside double quotes replaced by spaces (to look for syntax outside labels)."""
    out, q = [], False
    for ch in s:
        if ch == '"':
            q = not q
            out.append(ch)
        else:
            out.append(" " if q else ch)
    return "".join(out)
_HEADER = re.compile(r"^\s*(?:flowchart|graph)(?:\s+(TD|TB|LR|RL|BT))?\s*;?\s*$", re.I)
_SKIP = re.compile(r"^\s*(?:subgraph\b|end\s*$|style\b|classDef\b|class\b|click\b|linkStyle\b|direction\b)", re.I)


def _unq(s: Optional[str]) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1]
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    return s.replace("#quot;", '"').replace("#124;", "|").replace("#amp;", "&").strip()


def _statements(src: str):
    for no, line in enumerate(src.splitlines(), 1):
        part, q = [], False
        i, n = 0, len(line)
        while i < n:
            ch = line[i]
            if ch == '"':
                q = not q
            if not q and ch == "%" and line.startswith("%%", i):   # a comment, only outside a quoted label
                break
            if ch == ";" and not q:
                yield no, "".join(part)
                part = []
            else:
                part.append(ch)
            i += 1
        yield no, "".join(part)


def parse_mermaid(src: str) -> dict:
    """mermaid flowchart text → {dir, nodes:[{key, label, shape}], edges:[{from, to, label, head}]}. MermaidError(line) otherwise."""
    if not isinstance(src, str) or not src.strip():
        raise MermaidError("empty")
    stm = [(no, s) for no, s in _statements(src) if s.strip()]
    if not stm or not _HEADER.match(stm[0][1]):
        raise MermaidError("line %d: expected 'flowchart TD' or 'graph LR'" % (stm[0][0] if stm else 1))
    d = (_HEADER.match(stm[0][1]).group(1) or "TD").upper()
    direction = "LR" if d in ("LR", "RL") else "TD"
    nodes: Dict[str, dict] = {}
    edges: List[dict] = []

    def node(s, pos, no):
        m = _NODE.match(s, pos)
        if not m or m.end() == pos:
            raise MermaidError("line %d: expected a node at %r" % (no, s[pos:pos + 20]))
        key = m.group(1)
        shape = "ellipse" if m.group("e") is not None else "round" if m.group("s") is not None or m.group("r") is not None else \
            "diamond" if m.group("d") is not None else "box"
        lab = next((m.group(g) for g in ("e", "s", "sub", "b", "r", "d") if m.group(g) is not None), None)
        n = nodes.setdefault(key, {"key": key, "label": key, "shape": "box"})
        if lab is not None:
            n["label"], n["shape"] = _unq(lab), shape
        return key, m.end()

    for no, s in stm[1:]:
        if _SKIP.match(s):
            continue
        if "&" in _blank_quotes(s).replace("#amp;", ""):
            raise MermaidError("line %d: '&' is not supported — one edge per statement" % no)
        a, pos = node(s, 0, no)
        stop = len(s.rstrip())
        blank = _blank_quotes(s)
        while pos < stop:
            m = _edge(s, pos, blank)
            if not m:
                raise MermaidError("line %d: expected a link (-->, ---, <-->) at %r" % (no, s[pos:pos + 20]))
            back, op, lab, end = m
            head = ("both" if back else "end") if op.endswith(">") else "none"
            b, pos = node(s, end, no)
            edges.append({"from": a, "to": b, "label": _unq(lab) if lab else "", "head": head})
            a = b
    return {"dir": direction, "nodes": list(nodes.values()), "edges": edges}


# ------------------------------------------------------------------ draw.io out
def _style_color(c) -> Optional[str]:
    return c if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{3,8}", c) else None


def to_drawio(els, title: str = "Cera") -> Tuple[str, dict]:
    """-> (XML text, counts {vertices, edges, skipped:{type: n}}). Shapes and free text become vertices, arrows and lines edges
    (bound ends → source/target, free ends → points). Ink, images and page frames are left out (counted)."""
    live = _live(els)
    labels = _labels(live)
    ids: Dict[str, str] = {}
    cells: List[str] = []
    skipped: Dict[str, int] = {}
    nv = ne = 0
    for e in live:
        t = e.get("type")
        x, y, w, h = _num(e.get("x")), _num(e.get("y")), _num(e.get("width")), _num(e.get("height"))
        geo = '<mxGeometry x="%g" y="%g" width="%g" height="%g" as="geometry"/>' % (x, y, w, h)
        stroke, fill = _style_color(e.get("strokeColor")), _style_color(e.get("backgroundColor"))
        col = ("strokeColor=%s;" % stroke if stroke else "") + ("fillColor=%s;" % fill if fill else "fillColor=none;")
        if t in SHAPES:
            nv += 1
            ids[e["id"]] = "v%d" % nv
            st = {"rectangle": "rounded=%d;" % (1 if e.get("roundness") else 0), "ellipse": "ellipse;", "diamond": "rhombus;"}[t]
            if e.get("strokeStyle") == "dashed":
                st += "dashed=1;"
            cells.append('<mxCell id="%s" value=%s style=%s vertex="1" parent="1">%s</mxCell>'
                         % (ids[e["id"]], quoteattr(xml_text(labels.get(e["id"], ""))), quoteattr(st + col), geo))
        elif t == "text" and not isinstance(e.get("containerId"), str):
            nv += 1
            ids[e["id"]] = "v%d" % nv
            txt = e.get("originalText") if isinstance(e.get("originalText"), str) else e.get("text")
            st = "text;align=left;verticalAlign=top;" + ("fontColor=%s;" % stroke if stroke else "") + "fontSize=%g;" % (_num(e.get("fontSize")) or 20)
            cells.append('<mxCell id="%s" value=%s style=%s vertex="1" parent="1">%s</mxCell>' % (ids[e["id"]], quoteattr(xml_text(txt or "")), quoteattr(st), geo))
        elif t == "text":
            pass   # a label — carried as its container's value
        elif t not in ("arrow", "line"):
            skipped[t] = skipped.get(t, 0) + 1
    for e in live:
        t = e.get("type")
        if t not in ("arrow", "line"):
            continue
        pts = e.get("points") if isinstance(e.get("points"), list) else []
        pts = [(_num(p[0]), _num(p[1])) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(pts) < 2:
            skipped[t] = skipped.get(t, 0) + 1
            continue
        ne += 1
        x, y = _num(e.get("x")), _num(e.get("y"))
        s, tg = _ends(e) if t == "arrow" else (None, None)
        sh, eh = bool(e.get("startArrowhead")), bool(e.get("endArrowhead"))
        st = "endArrow=%s;startArrow=%s;" % ("classic" if eh else "none", "classic" if sh else "none")
        stroke = _style_color(e.get("strokeColor"))
        st += ("strokeColor=%s;" % stroke if stroke else "") + ("dashed=1;" if e.get("strokeStyle") == "dashed" else "")
        attrs = ""
        if s in ids:
            attrs += ' source="%s"' % ids[s]
        if tg in ids:
            attrs += ' target="%s"' % ids[tg]
        inner = ""
        if s not in ids:
            inner += '<mxPoint x="%g" y="%g" as="sourcePoint"/>' % (x + pts[0][0], y + pts[0][1])
        if tg not in ids:
            inner += '<mxPoint x="%g" y="%g" as="targetPoint"/>' % (x + pts[-1][0], y + pts[-1][1])
        if len(pts) > 2:
            inner += '<Array as="points">%s</Array>' % "".join('<mxPoint x="%g" y="%g"/>' % (x + px, y + py) for px, py in pts[1:-1])
        cells.append('<mxCell id="e%d" value=%s style=%s edge="1" parent="1"%s><mxGeometry relative="1" as="geometry">%s</mxGeometry></mxCell>'
                     % (ne, quoteattr(xml_text(labels.get(e["id"], ""))), quoteattr(st), attrs, inner))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="Cera"><diagram id="cera" name=%s><mxGraphModel grid="1" gridSize="10" '
           'page="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>\n%s\n</root></mxGraphModel></diagram></mxfile>\n'
           % (quoteattr(xml_text(title)[:80]), "\n".join(cells)))
    return xml, {"vertices": nv, "edges": ne, "skipped": skipped}


__all__ = ["graph_from_elements", "to_mermaid", "parse_mermaid", "to_drawio", "MermaidError", "flow_shape", "escape"]
