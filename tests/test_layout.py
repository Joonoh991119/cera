"""Tests for server/layout.py (board_add kind:"flow" layout, plan 09 WP-4 / B6). Standard library only; runs on /usr/bin/python3 (3.9)."""
from __future__ import annotations

import importlib.util
import itertools
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cera_layout_t", HERE / "server" / "layout.py")
lay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lay)

# the 07 eval flowchart: start → load → ok? —yes→ fit → end; ok? —no→ clean → load (back edge)
FLOW_IDS = ["start", "load", "ok", "fit", "end", "clean"]
FLOW_EDGES = [("start", "load"), ("load", "ok"), ("ok", "fit"), ("fit", "end"), ("ok", "clean"), ("clean", "load")]
SIZES = {"start": (160, 80), "load": (180, 80), "ok": (200, 120), "fit": (180, 80), "end": (160, 80), "clean": (180, 80)}


def rects(centres, sizes):
    return {k: (cx - sizes[k][0] / 2, cy - sizes[k][1] / 2, cx + sizes[k][0] / 2, cy + sizes[k][1] / 2) for k, (cx, cy) in centres.items()}


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class Layered(unittest.TestCase):
    def test_flow_layers_and_no_overlap(self):
        """Criterion 1 (layout part): longest-path layers with the back edge broken, no two boxes overlap, TD and LR."""
        self.assertEqual(lay.layers(FLOW_IDS, FLOW_EDGES), {"start": 0, "load": 1, "ok": 2, "fit": 3, "clean": 3, "end": 4})
        for d in ("TD", "LR"):
            c = lay.layered(FLOW_IDS, FLOW_EDGES, SIZES, d)
            self.assertEqual(set(c), set(FLOW_IDS))
            r = rects(c, SIZES)
            for a, b in itertools.combinations(FLOW_IDS, 2):
                self.assertFalse(overlap(r[a], r[b]), (d, a, b))
            axis = 1 if d == "TD" else 0
            self.assertLess(c["start"][axis], c["load"][axis])
            self.assertLess(c["ok"][axis], c["fit"][axis])
            self.assertEqual(c["fit"][axis], c["clean"][axis])

    def test_cycles_and_isolated(self):
        ids = ["a", "b", "c", "d"]
        lv = lay.layers(ids, [("a", "b"), ("b", "c"), ("c", "a")])
        self.assertEqual(sorted(lv.values()), [0, 0, 1, 2])
        self.assertEqual(lv["d"], 0)
        c = lay.layered(ids, [("a", "b"), ("b", "c"), ("c", "a")], {k: (100, 60) for k in ids})
        r = rects(c, {k: (100, 60) for k in ids})
        for a, b in itertools.combinations(ids, 2):
            self.assertFalse(overlap(r[a], r[b]))

    def test_fifty_nodes_fast(self):
        ids = ["n%d" % i for i in range(lay.MAX_NODES)]
        edges = [(ids[i], ids[j]) for i in range(50) for j in (i * 2 + 1, i * 3 + 2) if j < 50]
        t = time.time()
        c = lay.layered(ids, edges, {k: (160, 80) for k in ids})
        self.assertLess(time.time() - t, 0.2)
        r = rects(c, {k: (160, 80) for k in ids})
        for a, b in itertools.combinations(ids, 2):
            self.assertFalse(overlap(r[a], r[b]))


class Keep(unittest.TestCase):
    def test_within_20px_and_rows(self):
        """Criterion 2 (layout part): every centre within 20 px of its at; y closer than 30 % of the height → same row y."""
        at = {"a": (103, 207), "b": (391, 222), "c": (705, 199), "d": (250, 420), "e": (517, 455)}
        sizes = {k: (160, 80) for k in at}
        out = lay.keep(at, sizes)
        for k in at:
            self.assertLessEqual(((out[k][0] - at[k][0]) ** 2 + (out[k][1] - at[k][1]) ** 2) ** 0.5, 20, k)
            self.assertEqual(out[k][0] % 20, 0)
        self.assertEqual(out["a"][1], out["b"][1])
        self.assertEqual(out["a"][1], out["c"][1])
        self.assertNotEqual(out["d"][1], out["e"][1])   # 35 px apart, 30 % of 80 = 24
        for a, b in itertools.combinations(at, 2):
            if abs(at[a][1] - at[b][1]) < 0.3 * 80:
                self.assertEqual(out[a][1], out[b][1], (a, b))

    def test_row_grid_does_not_push_past_20(self):
        at = {"a": (0, 0), "b": (200, 23)}   # mean 11.5 → grid 20 would put a 20 px off: falls back to the mean
        out = lay.keep(at, {k: (160, 80) for k in at})
        self.assertEqual(out["a"][1], out["b"][1])
        for k in at:
            self.assertLessEqual(abs(out[k][1] - at[k][1]), 20)


if __name__ == "__main__":
    unittest.main()


def seg_hits_rect(p, q, r, shrink=2.0):
    """Does segment p-q pass through rectangle r (shrunk a little so touching the border does not count)?"""
    x0, y0, x1, y1 = r[0] + shrink, r[1] + shrink, r[2] - shrink, r[3] - shrink
    for i in range(201):
        t = i / 200
        x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        if x0 < x < x1 and y0 < y < y1:
            return True
    return False


class R1(unittest.TestCase):
    """Review r1 (neon 1 · opus 3): keep never moves a centre more than 20 px; rows chain (neighbours closer than 30 % share a y when
    that is possible within 20 px). Opus 2: layered routes long and reversed edges around boxes (dummy points)."""

    def check_keep(self, at, sizes, same=()):
        out = lay.keep(at, sizes)
        for k in at:
            d = ((out[k][0] - at[k][0]) ** 2 + (out[k][1] - at[k][1]) ** 2) ** 0.5
            self.assertLessEqual(d, 20 + 1e-9, (k, at[k], out[k]))
        for a, b in same:
            self.assertEqual(out[a][1], out[b][1], (a, b, out))
        return out

    def test_keep_20px_cases(self):
        self.check_keep({"a": (9.5, 0), "b": (200, 23)}, {"a": (160, 80), "b": (160, 80)}, same=[("a", "b")])
        at = {"n%d" % i: (i * 200, 0) for i in range(9)}
        at["z"] = (2000, 23)
        self.check_keep(at, {k: (160, 80) for k in at}, same=[("n0", "z")])
        self.check_keep({"a": (0, 0), "b": (300, 48)}, {"a": (200, 236), "b": (200, 236)})   # 48 apart: one y cannot be within 20 of both
        self.check_keep({"a": (0, 0), "b": (200, 20), "c": (400, 40)}, {k: (160, 70) for k in "abc"}, same=[("a", "b"), ("b", "c")])
        self.check_keep({"a": (0, 0), "b": (300, 59)}, {"a": (160, 200), "b": (160, 200)})

    def check_routes(self, ids, edges, sizes, d="TD"):
        c, via = lay.layered_routes(ids, edges, sizes, d)
        r = rects(c, sizes)
        for (a, b), pts in zip(edges, via):
            path = [c[a]] + list(pts) + [c[b]]
            for p, q in zip(path, path[1:]):
                for k in ids:
                    if k in (a, b):
                        continue
                    self.assertFalse(seg_hits_rect(p, q, r[k]), (d, a, b, k, path))
            for pt in pts:   # bend points sit outside every box
                for k in ids:
                    self.assertFalse(r[k][0] < pt[0] < r[k][2] and r[k][1] < pt[1] < r[k][3], (pt, k))
        return c, via

    def test_routes_flow07_and_skip(self):
        for d in ("TD", "LR"):
            c, via = self.check_routes(FLOW_IDS, FLOW_EDGES, SIZES, d)
            self.assertEqual(len(via[FLOW_EDGES.index(("clean", "load"))]), 1)   # the back edge bends once (layer 2)
            self.assertEqual(via[0], [])
        ids = list("abcd")
        self.check_routes(ids, [("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")], {k: (160, 80) for k in ids})
        self.assertEqual(lay.layered(FLOW_IDS, FLOW_EDGES, SIZES), lay.layered_routes(FLOW_IDS, FLOW_EDGES, SIZES)[0])


class R2(unittest.TestCase):
    """Review r2 (opus): a back edge between neighbouring layers and a repeated edge must not lie exactly on the forward edge."""

    def test_neighbour_back_edge_and_duplicate_bend(self):
        ids = ["a", "b", "c"]
        edges = [("a", "b"), ("b", "c"), ("c", "b"), ("b", "c")]
        sizes = {k: (160, 80) for k in ids}
        for d in ("TD", "LR"):
            c, via = lay.layered_routes(ids, edges, sizes, d)
            self.assertEqual(via[0], [])
            self.assertEqual(via[1], [])   # the first b→c stays straight
            self.assertEqual(len(via[2]), 1)
            self.assertEqual(len(via[3]), 1)
            self.assertNotEqual(via[2], via[3])
            r = rects(c, sizes)
            for pt in via[2] + via[3]:
                for k in ids:
                    self.assertFalse(r[k][0] < pt[0] < r[k][2] and r[k][1] < pt[1] < r[k][3], (d, pt, k))


class LabelRoom(unittest.TestCase):
    """Leader r4: a labelled edge between stacked nodes must be long enough to show its line and arrowhead around the label."""
    def test_r4_short_labelled_edge_pushes_the_far_side_away(self):
        sizes = {"top": (160, 80), "ok": (160, 80), "fit": (160, 80), "end": (160, 80), "side": (160, 80)}
        at = {"top": (0, -120), "ok": (0, 0), "fit": (0, 120), "end": (0, 240), "side": (300, 0)}
        c, via = lay.make_room(at, sizes, [("ok", "fit", 30, 21.6)], [[(150, 200)]])
        need = 21.6 + lay.LABEL_EDGE_ROOM
        self.assertGreaterEqual(c["fit"][1] - 40 - (c["ok"][1] + 40), need - 0.01, "라벨 높이 + 화살촉 둘 + 여유")
        self.assertEqual((c["top"], c["ok"], c["side"]), (at["top"], at["ok"], at["side"]), "위쪽은 그대로")
        self.assertEqual(c["end"][1] - c["fit"][1], 120, "아래쪽은 같이 밀린다(사이는 그대로)")
        self.assertEqual(via[0][0][1], 200 + c["fit"][1] - 120, "꺾인 점도 같이")
        c2, _ = lay.make_room(at, sizes, [("fit", "ok", 30, 21.6)], [])   # drawn upwards: the side it points to moves (up)
        self.assertEqual((c2["fit"], c2["end"]), (at["fit"], at["end"]))
        self.assertGreaterEqual(c2["fit"][1] - 40 - (c2["ok"][1] + 40), need - 0.01)
        self.assertEqual(lay.make_room(c, sizes, [("ok", "fit", 30, 21.6)], [])[0], c, "이미 넉넉하면 그대로")

    def test_r4_flow_keep_yes_edge_shows_its_arrow(self):
        """The docs figure: ok? and fit model 42 px apart (layout keep) - the 'yes' arrow was hidden under its label."""
        import sys
        sys.path.insert(0, str(HERE / "server"))
        sp = importlib.util.spec_from_file_location("cera_tb_mcp_r4", HERE / "server" / "tb_mcp.py")
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        it = {"kind": "flow", "layout": "keep", "nodes": [
            {"id": "ok", "label": "ok?", "shape": "diamond", "at": {"x": 0, "y": 0}},
            {"id": "fit", "label": "fit model", "at": {"x": 0, "y": 120}},
            {"id": "clean", "label": "clean", "at": {"x": 240, "y": 0}}],
            "edges": [{"from": "ok", "to": "fit", "label": "yes"}, {"from": "ok", "to": "clean", "label": "no"}]}
        out, boxes, _ = mod.Tools._flow(None, it, 0, "Claude", None, 0, 0)
        by = {e["id"]: e for e in out}
        for e in out:
            if e["type"] != "arrow":
                continue
            lab = next(t for t in out if t.get("containerId") == e["id"])
            span = max(abs(p[1]) for p in e["points"]) if e["width"] < 1 else max(abs(p[0]) for p in e["points"])
            size = lab["height"] if e["width"] < 1 else lab["width"]
            self.assertGreaterEqual(span, size + 2 * 16 + 4 - 0.01, (lab["text"], span, size))
        self.assertLess(abs(boxes["ok"]["y"] - boxes["clean"]["y"]), 1, "옆 간선은 그대로")
        del by
