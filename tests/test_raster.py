"""Tests for server/raster.py (board_snapshot's standard-library raster, plan 09 WP-3 / B4).
The fixture is the 07 eval board (Playwright hand-drawn flow, graph, formulas). Runs on /usr/bin/python3 (3.9)."""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import struct
import time
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cera_raster_t", HERE / "server" / "raster.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)
FIX = json.loads((HERE / "tests" / "fixtures" / "rough07.json").read_text(encoding="utf-8"))


def decode(png):
    """-> (w, h, rows) for the 8-bit grayscale, filter-0 PNGs raster.py writes."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, color = struct.unpack(">IIBB", png[16:26])
    assert (depth, color) == (8, 0)
    i, idat = 8, b""
    while i < len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        t = png[i + 4:i + 8]
        if t == b"IDAT":
            idat += png[i + 8:i + 8 + n]
        i += 12 + n
    raw = zlib.decompress(idat)
    rows = [raw[y * (w + 1) + 1:(y + 1) * (w + 1)] for y in range(h)]
    assert all(raw[y * (w + 1)] == 0 for y in range(h))
    return w, h, rows


def darkest(rows, r, x0, y0, s):
    """Darkest pixel inside board box r = (x, y, w, h)."""
    a0, b0 = max(0, int((r[0] - x0) * s)), max(0, int((r[1] - y0) * s))
    a1, b1 = min(len(rows[0]), int((r[0] + r[2] - x0) * s) + 1), min(len(rows), int((r[1] + r[3] - y0) * s) + 1)
    return min((min(rows[y][a0:a1]) for y in range(b0, b1) if a1 > a0), default=255)


def el(**k):
    base = {"id": k.pop("id"), "x": 0, "y": 0, "width": 0, "height": 0, "angle": 0, "strokeColor": "#1e1e1e", "strokeWidth": 2,
            "isDeleted": False}
    base.update(k)
    return base


class Raster(unittest.TestCase):
    def test_every_stroke_leaves_ink(self):
        """Criterion 2: every stroke of the fixture has dark pixels inside its box; long edge within max_edge."""
        els = FIX["elements"]
        for edge in (1024, 1568, 256):
            r = rs.render(els, max_edge=edge)
            w, h, rows = decode(r["png"])
            self.assertEqual((w, h), (r["width"], r["height"]))
            self.assertLessEqual(max(w, h), edge)
            for e in els:
                if e["type"] == "freedraw":
                    self.assertLess(darkest(rows, rs.element_box(e), r["x0"], r["y0"], r["scale"]), 100, (edge, e["id"]))

    def test_text_is_a_gray_bar_not_ink(self):
        t = el(id="t", type="text", x=0, y=0, width=200, height=30, text="hello", fontSize=24)
        b = el(id="b", type="rectangle", x=-50, y=-50, width=300, height=130)
        r = rs.render([t, b], max_edge=600)
        _, _, rows = decode(r["png"])
        d = darkest(rows, (20, 10, 160, 10), r["x0"], r["y0"], r["scale"])
        self.assertEqual(d, rs.TEXT_GRAY)
        self.assertLess(darkest(rows, (-52, -52, 4, 130), r["x0"], r["y0"], r["scale"]), 100)

    def test_arrowheads(self):
        def wing_ink(head):
            a = el(id="a", type="arrow", x=0, y=0, points=[[0, 0], [200, 0]], endArrowhead=head, startArrowhead=None, width=200)
            r = rs.render([a], max_edge=800, scale=2, pad=30)
            _, _, rows = decode(r["png"])
            return darkest(rows, (180, -12, 10, 6), r["x0"], r["y0"], r["scale"])   # above the shaft, near the tip
        self.assertLess(wing_ink("arrow"), 100)
        self.assertLess(wing_ink("triangle"), 100)
        self.assertEqual(wing_ink(None), 255)

    def test_mono_and_color(self):
        hi = el(id="h", type="freedraw", x=0, y=0, points=[[0, 0], [100, 0]], strokeColor="#ffec99", strokeWidth=8)
        r = rs.render([hi], mono=True)
        self.assertLess(min(min(row) for row in decode(r["png"])[2]), 50)
        r = rs.render([hi], mono=False)
        self.assertGreater(min(min(row) for row in decode(r["png"])[2]), 150)
        self.assertIsNone(rs.gray_of("transparent", False))

    def test_pick(self):
        box = el(id="box", type="rectangle", x=0, y=0, width=100, height=50, boundElements=[{"id": "lab", "type": "text"}])
        lab = el(id="lab", type="text", x=10, y=10, width=50, height=20, containerId="box", text="A")
        fr = el(id="tbpage-1", type="frame", x=500, y=0, width=400, height=300, customData={"tbPage": 1})
        kid = el(id="kid", type="freedraw", x=550, y=50, points=[[0, 0], [10, 10]], frameId="tbpage-1")
        out = el(id="out", type="freedraw", x=2000, y=2000, points=[[0, 0], [10, 10]])
        gone = el(id="gone", type="rectangle", x=0, y=0, width=5, height=5, isDeleted=True)
        allv = [box, lab, fr, kid, out, gone]
        got, missing = rs.pick(allv, ids=["lab", "nope"])
        self.assertEqual({e["id"] for e in got}, {"box", "lab"})
        self.assertEqual(missing, ["nope"])
        self.assertEqual({e["id"] for e in rs.pick(allv, ids=["tbpage-1"])[0]}, {"tbpage-1", "kid"})
        self.assertEqual({e["id"] for e in rs.pick(allv, region=(1990, 1990, 30, 30))[0]}, {"out"})
        self.assertEqual({e["id"] for e in rs.pick(allv, frame=fr)[0]}, {"tbpage-1", "kid"})
        self.assertEqual(len(rs.pick(allv)[0]), 5)
        r = rs.render(rs.pick(allv, frame=fr)[0], frame=fr, max_edge=400)
        self.assertEqual((r["x0"], r["y0"], r["width"]), (500, 0, 400))

    def test_scale_rule_and_char_height(self):
        """B4: scale toward ~56 px letters but never past max_edge or 4x; same rule as the tab (jobs.js renderScale)."""
        f1 = rs.pick(FIX["elements"], ids=FIX["regions"]["F1"])[0]
        ch = rs.char_height(f1)
        self.assertTrue(10 < ch < 80, ch)
        r = rs.render(f1, max_edge=1024, scale=56 / ch)
        self.assertAlmostEqual(r["scale"], 56 / ch, places=6)
        self.assertLess(r["width"], 1024)
        r = rs.render(FIX["elements"], max_edge=512, scale=4)
        self.assertLessEqual(max(r["width"], r["height"]), 512)
        self.assertEqual(rs.fit_scale(10, 10, 1568, None), rs.MAX_SCALE)
        self.assertEqual(rs.char_height([el(id="t", type="text", fontSize=36), el(id="u", type="text", fontSize=20), el(id="v", type="text", fontSize=28)]), 28)
        self.assertIsNone(rs.char_height([]))

    def test_big_board_under_a_second(self):
        """Criterion 2: 1,410 strokes in under 1 s."""
        base = [e for e in FIX["elements"] if e["type"] == "freedraw"]
        big = []
        for i in range(1410):
            e = copy.deepcopy(base[i % len(base)])
            e["id"] = "b%d" % i
            e["x"] += (i // len(base)) * 900
            big.append(e)
        t = time.time()
        r = rs.render(big, max_edge=1568)
        self.assertLess(time.time() - t, 1.0)
        self.assertEqual(r["count"], 1410)

    def test_broken_elements_do_not_crash(self):
        junk = [{"id": "a", "type": "freedraw", "x": "x", "points": [[None, 1], "z", [1e308, 2]]},
                {"id": "b", "type": "ellipse", "x": float("nan"), "width": 10, "height": 10},
                {"id": "c", "type": "text", "text": None, "width": 5, "height": 5},
                {"id": "d", "type": "weird"}, "notadict",
                el(id="e", type="rectangle", x=0, y=0, width=50, height=50, angle=0.5)]
        r = rs.render(junk, max_edge=256)
        self.assertTrue(rs.png_size(r["png"]))
        with self.assertRaises(ValueError):
            rs.render([], max_edge=256)

class RasterR1(unittest.TestCase):
    """Review r1 regressions (04_review_WP-3_*_r1)."""

    def test_png_size_needs_a_complete_ihdr(self):
        good = rs.png_bytes(3, 2, bytes([255]) * 6)
        self.assertEqual(rs.png_size(good), (3, 2))
        self.assertIsNone(rs.png_size(good[:24]))                       # truncated IHDR
        bad = bytearray(good); bad[30] ^= 1
        self.assertIsNone(rs.png_size(bytes(bad)))                      # CRC broken
        self.assertIsNone(rs.png_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40))

    def test_empty_id_list_draws_nothing(self):
        self.assertEqual(rs.pick(FIX["elements"], ids=[]), ([], []))

    def test_rotated_line_region(self):
        line = el(id="l", type="line", x=0, y=40, points=[[0, 0], [100, 0]], angle=math.pi / 2, width=100)
        self.assertEqual([e["id"] for e in rs.pick([line], region=(49, 39, 2, 2))[0]], ["l"])   # drawn vertically through (50, 40)
        self.assertEqual(rs.pick([line], region=(5, 39, 2, 2))[0], [])

    def test_region_in_a_page_crops_to_the_region(self):
        page = el(id="tbpage-1", type="frame", x=0, y=0, width=4000, height=3000, customData={"tbPage": 1})
        s = el(id="s", type="freedraw", x=100, y=100, points=[[0, 0], [5, 40]], frameId="tbpage-1", strokeWidth=1)
        far = el(id="far", type="freedraw", x=95, y=100, points=[[0, 0], [3000, 2000]], frameId="tbpage-1", strokeWidth=1)
        picked = rs.pick([page, s], region=(90, 90, 50, 50))[0]
        self.assertEqual([e["id"] for e in picked], ["s"])
        self.assertEqual([e["id"] for e in rs.pick([page, s], region=(-1, -1, 5000, 5000))[0]], ["tbpage-1", "s"])
        r = rs.render(rs.pick([page, s, far], region=(90, 90, 50, 50))[0], max_edge=1024, scale=4, crop=(90, 90, 50, 50))
        self.assertGreaterEqual(r["x0"], 90)
        self.assertLessEqual(r["width"] / r["scale"], 50 + 1e-6)           # the picture is the region, not the long stroke's reach
        self.assertEqual(r["scale"], 4)
        with self.assertRaises(ValueError):
            rs.render([s], crop=(900, 900, 5, 5))

    def test_time_budget_and_cancel(self):
        heavy = [el(id="h%d" % i, type="freedraw", x=0, y=i * 3, strokeWidth=2,
                    points=[[k * 2.0, (k % 7) * 3.0] for k in range(2000)]) for i in range(200)]
        t = time.time()
        with self.assertRaises(rs.RasterTimeout):
            rs.render(heavy, max_edge=1568, scale=4, budget_s=0.3)
        self.assertLess(time.time() - t, 1.5)
        with self.assertRaises(rs.RasterTimeout):
            rs.render(heavy, max_edge=1568, cancelled=lambda: True)

    def test_absurd_stroke_width_does_not_blank_the_picture(self):
        a = el(id="a", type="freedraw", x=0, y=0, points=[[0, 0], [100, 0]], strokeWidth=1)
        big = el(id="b", type="rectangle", x=200, y=0, width=50, height=50, strokeWidth=1e9)
        r = rs.render([a, big], max_edge=512)
        self.assertGreater(r["scale"], 0.5)

    def test_scene_tag_matches_js(self):
        """web/tests/jobs.test.mjs computes the same value."""
        self.assertEqual(rs.scene_tag([{"id": "b", "version": 3, "versionNonce": 9}, {"id": "a", "version": 12}, {"id": "c"}, {"id": "한", "version": 2.0}]),
                         "4-2db59f12")


class RasterR2(unittest.TestCase):
    """Review r2 (04_review_WP-3_neon_r2, written by Codex) — each one reproduced first."""

    def test_r2_truncated_png_is_not_complete(self):
        """R1-01: the first 33 bytes of a good PNG (IHDR only, no IDAT/IEND) must not count as a picture."""
        good = rs.png_bytes(3, 2, bytes([255]) * 6)
        self.assertTrue(rs.png_complete(good))
        self.assertFalse(rs.png_complete(good[:33]))
        self.assertFalse(rs.png_complete(good[:-1]))                     # IEND cut
        bad = bytearray(good); bad[40] ^= 1
        self.assertFalse(rs.png_complete(bytes(bad)))                    # IDAT CRC broken

    def test_r2_scene_tag_sees_version_nonce(self):
        """R1-02: same id and version, different versionNonce (two people edited at once) -> different tags."""
        a = [{"id": "x", "version": 5, "versionNonce": 111, "text": "A"}]
        b = [{"id": "x", "version": 5, "versionNonce": 222, "text": "B"}]
        self.assertNotEqual(rs.scene_tag(a), rs.scene_tag(b))

    def test_r2_budget_is_checked_inside_one_stroke(self):
        """new: one 60-point line with a 3 ms budget must time out, not return a PNG after ~15 ms."""
        line = el(id="l", type="line", x=0, y=0, strokeWidth=4, points=[[k * 20.0, (k % 2) * 300.0] for k in range(60)])
        with self.assertRaises(rs.RasterTimeout):
            rs.render([line], max_edge=1568, scale=4, budget_s=0.003)


class RasterR3(unittest.TestCase):
    """Review r3 (04_review_WP-3_neon_r3): the budget is checked by time, not by element / segment counts."""

    def test_r3_budget_inside_text_bars(self):
        """one rotated text with 5000 lines: past the budget it stops soon, it does not draw every bar first."""
        t = el(id="t", type="text", x=0, y=0, width=1500, height=1500, angle=math.pi / 4, text="a\n" * 5000, fontSize=20)
        t0 = time.time()
        with self.assertRaises(rs.RasterTimeout):
            rs.render([t], max_edge=1568, scale=4, budget_s=0.05)
        self.assertLess(time.time() - t0, 0.5)

    def test_r3_budget_with_few_short_heavy_strokes(self):
        """15 thick strokes of 15 segments each (no count-based check ever fires): still stops near the budget."""
        heavy = [el(id="h%d" % i, type="freedraw", x=0, y=i * 2, strokeWidth=64, points=[[k * 4.0, (k % 2) * 60.0] for k in range(16)])
                 for i in range(15)]
        t0 = time.time()
        with self.assertRaises(rs.RasterTimeout):
            rs.render(heavy, max_edge=1568, scale=4, budget_s=0.05)
        self.assertLess(time.time() - t0, 0.5)


if __name__ == "__main__":
    unittest.main()
