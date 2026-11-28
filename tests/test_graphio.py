"""Tests for server/graphio.py (mermaid out / small parser in, draw.io XML out; plan 09 WP-4). Runs on /usr/bin/python3 (3.9)."""
from __future__ import annotations

import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _load(name):
    key = "cera_" + name
    if key not in sys.modules:   # agentkit loads its siblings (i18n, fracindex) the same way
        spec = importlib.util.spec_from_file_location(key, HERE / "server" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


gio = _load("graphio")
ak = _load("agentkit")


def sets(g):
    return ({(n["key"], n["label"], n["shape"]) for n in g["nodes"]},
            {(e["from"], e["to"], e["label"], e["head"]) for e in g["edges"]})


class Mermaid(unittest.TestCase):
    def test_roundtrip_with_awkward_labels(self):
        g = {"dir": "LR", "nodes": [{"key": "a", "label": '말 "따옴" | 파이프', "shape": "box"},
                                    {"key": "b", "label": "둘째\n줄 & <b>", "shape": "round"},
                                    {"key": "c", "label": "C", "shape": "ellipse"}, {"key": "ok", "label": "ok?", "shape": "diamond"}],
             "edges": [{"from": "a", "to": "b", "label": "yes|no", "head": "end"}, {"from": "b", "to": "c", "label": "", "head": "none"},
                       {"from": "c", "to": "ok", "label": "", "head": "both"}, {"from": "ok", "to": "a", "label": "다시", "head": "end"}]}
        src = gio.to_mermaid(g)
        self.assertTrue(src.startswith("flowchart LR\n"))
        back = gio.parse_mermaid(src)
        self.assertEqual(back["dir"], "LR")
        self.assertEqual(sets(back), sets(g))

    def test_hand_written_forms(self):
        src = """graph TD
        %% comment
        A[Start] --> B{ok?}
        B -- yes --> C(Fit)
        B -->|no| D((Clean)); D --> A
        C --- E([End]) --> F
        X <--> Y
        P-->Q---R
        a-b-->c
        style A fill:#f9f
        """
        g = gio.parse_mermaid(src)
        n = {x["key"]: (x["label"], x["shape"]) for x in g["nodes"]}
        self.assertEqual(n["A"], ("Start", "box"))
        self.assertEqual(n["B"], ("ok?", "diamond"))
        self.assertEqual(n["C"], ("Fit", "round"))
        self.assertEqual(n["D"], ("Clean", "ellipse"))
        self.assertEqual(n["E"], ("End", "round"))
        self.assertEqual(n["F"], ("F", "box"))
        e = {(x["from"], x["to"]): (x["label"], x["head"]) for x in g["edges"]}
        self.assertEqual(e[("B", "C")], ("yes", "end"))
        self.assertEqual(e[("B", "D")], ("no", "end"))
        self.assertEqual(e[("D", "A")], ("", "end"))
        self.assertEqual(e[("C", "E")], ("", "none"))
        self.assertEqual(e[("E", "F")], ("", "end"))
        self.assertEqual(e[("X", "Y")], ("", "both"))
        self.assertEqual(e[("P", "Q")], ("", "end"))
        self.assertEqual(e[("Q", "R")], ("", "none"))
        self.assertEqual(e[("a-b", "c")], ("", "end"))

    def test_errors(self):
        for bad in ("", "A --> B", "flowchart TD\nA & B --> C", "flowchart TD\nA --> ", "sequenceDiagram\nA->>B: hi"):
            with self.assertRaises(gio.MermaidError, msg=bad):
                gio.parse_mermaid(bad)

    def test_graph_from_board_elements(self):
        """Tagged flow nodes keep their keys; untagged human boxes joined by bound arrows count too; a start-headed arrow is turned around;
        reserved words never become mermaid ids."""
        a = ak.make_box("ellipse", "시작")
        b = ak.make_box("rectangle", "끝")
        b[0]["roundness"] = None
        ak.tag_graph(a[0], "g1", "start", "LR")
        ak.tag_graph(b[0], "g1", "end", "LR")
        h = ak.make_box("diamond", "사람")   # untagged, a person's
        b[0]["y"] = 200
        h[0]["y"] = 400
        arr1 = ak.make_arrow(a[0], b[0], "가자")
        arr2 = ak.make_arrow(h[0], b[0], start_head="arrow", end_head=None)   # drawn backwards: b → h
        loose = ak.make_arrow((0, 0), (50, 50))
        lone = ak.make_box("rectangle", "혼자")   # untagged and not connected: not a node
        lone[0]["y"] = 900
        ink = {"id": "ink1", "type": "freedraw", "x": 0, "y": 0, "width": 10, "height": 10, "points": [[0, 0], [10, 10]]}
        els = a + b + h + arr1 + arr2 + loose + lone + [ink]
        g = gio.graph_from_elements(els)
        self.assertEqual(g["dir"], "LR")
        keys = {n["label"]: (n["key"], n["shape"]) for n in g["nodes"]}
        self.assertEqual(keys["시작"], ("start", "ellipse"))
        self.assertNotEqual(keys["끝"][0], "end")   # 'end' breaks mermaid
        self.assertEqual(keys["끝"][1], "box")
        self.assertIn("사람", keys)
        self.assertNotIn("혼자", keys)
        e = {(x["from"], x["to"]): (x["label"], x["head"]) for x in g["edges"]}
        self.assertEqual(e[("start", keys["끝"][0])], ("가자", "end"))
        self.assertEqual(e[(keys["끝"][0], keys["사람"][0])], ("", "end"))
        self.assertEqual(len(g["edges"]), 2)
        back = gio.parse_mermaid(gio.to_mermaid(g))
        self.assertEqual(sets(back), sets(g))
        only = gio.graph_from_elements(els, ids=[a[0]["id"], b[0]["id"]])
        self.assertEqual(len(only["nodes"]), 2)
        self.assertEqual(len(only["edges"]), 1)


class Drawio(unittest.TestCase):
    def test_cells_counts_and_escaping(self):
        a = ak.make_box("rectangle", 'A <b>&"x"', 0, 0)
        b = ak.make_box("diamond", "B\n둘째", 300, 0)
        arr = ak.make_arrow(a[0], b[0], "yes", start_head=None, end_head=None)
        free = ak.make_text("메모 <script>", 0, 300)
        loose = ak.make_line([(0, 400), (100, 450), (200, 400)])
        ink = {"id": "ink1", "type": "freedraw", "x": 0, "y": 0, "width": 10, "height": 10, "points": [[0, 0], [10, 10]]}
        img = ak.make_image("f" * 40, 100, 50)
        dead = ak.make_box("ellipse", "지움")
        dead[0]["isDeleted"] = True
        xml, cnt = gio.to_drawio(a + b + arr + [free] + loose + [ink, img] + dead, title='보드 "1"')
        self.assertEqual((cnt["vertices"], cnt["edges"]), (3, 2))
        self.assertEqual(cnt["skipped"], {"freedraw": 1, "image": 1})
        root = ET.fromstring(xml)   # well-formed
        cells = root.iter("mxCell")
        vs = [c for c in root.iter("mxCell") if c.get("vertex") == "1"]
        es = [c for c in root.iter("mxCell") if c.get("edge") == "1"]
        self.assertEqual((len(vs), len(es)), (3, 2))
        self.assertTrue(any(c is not None for c in cells))
        vals = {c.get("value") for c in vs}
        self.assertIn('A <b>&"x"', vals)   # plain text after XML unescaping …
        self.assertIn("B\n둘째", vals)
        self.assertNotIn("html=1", xml)   # … and never read as HTML by draw.io
        edge = next(e for e in es if e.get("source"))
        self.assertEqual(edge.get("value"), "yes")
        self.assertIn("endArrow=none", edge.get("style"))
        src = next(v for v in vs if v.get("id") == edge.get("source"))
        self.assertEqual(src.get("value"), 'A <b>&"x"')
        geo = src.find("mxGeometry")
        self.assertEqual((float(geo.get("x")), float(geo.get("width"))), (a[0]["x"], a[0]["width"]))   # board coordinates
        line = next(e for e in es if not e.get("source"))
        self.assertEqual(len(line.find("mxGeometry").find("Array")), 1)   # middle point kept
        self.assertIn("rhombus", next(v for v in vs if v.get("value") == "B\n둘째").get("style"))


class R1(unittest.TestCase):
    """Review r1: XML 1.0 control characters (neon 2 · opus), '%%' and '&' inside quoted labels (neon 7), linear parsing (opus 4)."""

    def test_control_characters_never_break_xml(self):
        svg = ak.math_placeholder_svg("\x0crac{a}{b} + \x08eta\x00")[0]
        ET.fromstring(svg)
        self.assertIn("rac{a}{b} + eta", svg)
        a = ak.make_box("rectangle", "bad\x0cchar\x1b", 0, 0)
        t = ak.make_text("x\x01y", 0, 200)
        xml, _ = gio.to_drawio(a + [t], title="t\x0b")
        root = ET.fromstring(xml)
        self.assertEqual({c.get("value") for c in root.iter("mxCell") if c.get("vertex")}, {"badchar", "xy"})

    def test_percent_and_ampersand_in_labels(self):
        g = {"dir": "TD", "nodes": [{"key": "a", "label": "50%% done", "shape": "box"}, {"key": "b", "label": "R&D", "shape": "box"}],
             "edges": [{"from": "a", "to": "b", "label": "%% x & y", "head": "end"}]}
        self.assertEqual(sets(gio.parse_mermaid(gio.to_mermaid(g))), sets(g))
        g2 = gio.parse_mermaid('flowchart TD\nA["R&D"] --> B %% trailing comment')
        self.assertEqual([n["label"] for n in g2["nodes"]], ["R&D", "B"])
        with self.assertRaises(gio.MermaidError):
            gio.parse_mermaid("flowchart TD\nA & B --> C")

    def test_linear_time_on_20kb(self):
        import time
        for body in ("A -- " + " " * 20000 + "x", "A " + "-" * 20000, "A[" + "x" * 20000, "A -- " + "y " * 9000 + "-->",
                     "\n".join("n%d --> n%d" % (i, i + 1) for i in range(1500))):
            t = time.time()
            try:
                gio.parse_mermaid("flowchart TD\n" + body)
            except gio.MermaidError:
                pass
            self.assertLess(time.time() - t, 0.5, body[:30])


if __name__ == "__main__":
    unittest.main()


class R2(unittest.TestCase):
    def test_quoted_pipe_and_arrow_in_text_labels(self):
        """neon r2: "-- "label" -->" with | or --> inside the quotes is a label, not an operator."""
        for lab in ("a | b", "a --> b", "x --- y"):
            g = gio.parse_mermaid('flowchart TD\n  A[x] -- "%s" --> B[y]' % lab)
            self.assertEqual([(e["from"], e["to"], e["label"]) for e in g["edges"]], [("A", "B", lab)])

    def test_empty_ids_means_nothing(self):
        a = ak.make_box("rectangle", "A")
        b = ak.make_box("rectangle", "B", 300, 0)
        arr = ak.make_arrow(a[0], b[0])
        g = gio.graph_from_elements(a + b + arr, ids=[])
        self.assertEqual((g["nodes"], g["edges"]), ([], []))
        self.assertEqual(len(gio.graph_from_elements(a + b + arr)["nodes"]), 2)
