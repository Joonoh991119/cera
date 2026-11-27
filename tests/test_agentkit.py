"""Unit tests for the element factory (server/agentkit.py) and fractional index (server/fracindex.py).

Live checks on screen (sending to an open board, reload, dragging) are in tests/live/live_agent_write.py (Playwright).
"""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["CERA_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import importlib.util
import json
import math
import random
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / "server" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fi = _load("tb_fracindex_t", "fracindex.py")
ak = _load("tb_agentkit_t", "agentkit.py")
FI_JS = HERE / "web" / "node_modules" / "fractional-indexing" / "src" / "index.js"

# Results of JS fractional-indexing 3.2.0 generateKeyBetween (captured with node; None = error)
VECTORS = [
    (None, None, "a0"), (None, "a0", "Zz"), (None, "Zz", "Zy"), ("a0", None, "a1"), ("a1", None, "a2"),
    ("a0", "a1", "a0V"), ("a1", "a2", "a1V"), ("a0V", "a1", "a0l"), ("Zz", "a0", "ZzV"), ("Zz", "a1", "a0"),
    (None, "Y00", "Xzzz"), ("bzz", None, "c000"), ("a0", "a0V", "a0G"), ("a0", "a0G", "a08"), ("b125", "b129", "b127"),
    ("a0", "a1V", "a1"), ("Zz", "a01", "a0"), (None, "a0V", "a0"), (None, "b999", "b99"),
    (None, "A00000000000000000000000000", None), (None, "A000000000000000000000000001", "A000000000000000000000000000V"),
    ("zzzzzzzzzzzzzzzzzzzzzzzzzzy", None, "zzzzzzzzzzzzzzzzzzzzzzzzzzz"),
    ("zzzzzzzzzzzzzzzzzzzzzzzzzzz", None, "zzzzzzzzzzzzzzzzzzzzzzzzzzzV"),
    ("a00", None, None), ("a00", "a1", None), ("0", "1", None), ("a1", "a0", None), ("a5", None, "a6"),
    ("az", None, "b00"), ("a9", "aA", "a9V"), ("aZ", "aa", "aZV"), ("ay", "az", "ayV"), ("Zy", "Zz", "ZyV"),
    ("a0", "a01", "a00V"), ("a0001", None, "a1"), ("b00", None, "b01"), ("Yzzz", None, "Z0"), (None, "a", None),
    ("a0x", "a0y", "a0xV"), ("a0xV", "a0y", "a0xl"), ("a1", "a10V", "a10G"), ("ZzV", "a0", "Zzl"), ("Zu", "Zz", "Zv"),
    ("a0", "c000", "a1"),
]
N_VECTORS = [
    (None, None, 5, ["a0", "a1", "a2", "a3", "a4"]), ("a0", None, 4, ["a1", "a2", "a3", "a4"]),
    (None, "a0", 4, ["Zw", "Zx", "Zy", "Zz"]), ("a0", "a1", 7, ["a08", "a0G", "a0O", "a0V", "a0d", "a0l", "a0t"]),
    ("a0", "a0V", 3, ["a08", "a0G", "a0O"]),
]

BASE_FIELDS = {"id", "type", "x", "y", "width", "height", "angle", "strokeColor", "backgroundColor", "fillStyle",
               "strokeWidth", "strokeStyle", "roughness", "opacity", "groupIds", "frameId", "index", "roundness", "seed",
               "version", "versionNonce", "isDeleted", "boundElements", "updated", "link", "locked"}
TYPE_FIELDS = {
    "text": {"text", "originalText", "fontSize", "fontFamily", "textAlign", "verticalAlign", "containerId", "autoResize",
             "lineHeight"},
    "arrow": {"points", "lastCommittedPoint", "startBinding", "endBinding", "startArrowhead", "endArrowhead", "elbowed"},
    "image": {"fileId", "status", "scale", "crop"},
    "frame": {"name"},
}


def _finite(*vals):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def _inside(inner, outer, tol=0.01):
    return (inner["x"] >= outer["x"] - tol and inner["y"] >= outer["y"] - tol
            and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + tol
            and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + tol)


class FracIndex(unittest.TestCase):
    def test_fixed_vectors_match_js(self):
        self.assertGreaterEqual(len(VECTORS), 30)
        for a, b, want in VECTORS:
            if want is None:
                with self.assertRaises(ValueError, msg=(a, b)):
                    fi.generate_key_between(a, b)
            else:
                self.assertEqual(fi.generate_key_between(a, b), want, (a, b))
                self.assertEqual(ak.index_between(a, b), want, (a, b))
        for a, b, n, want in N_VECTORS:
            self.assertEqual(fi.generate_n_keys_between(a, b, n), want, (a, b, n))

    def test_keys_sorted_between(self):
        rnd = random.Random(7)
        keys = ["a0"]
        for _ in range(400):
            i = rnd.randrange(len(keys) + 1)
            a = keys[i - 1] if i > 0 else None
            b = keys[i] if i < len(keys) else None
            k = fi.generate_key_between(a, b)
            self.assertTrue((a is None or a < k) and (b is None or k < b), (a, k, b))
            keys.insert(i, k)
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(keys))

    def test_random_pairs_match_node(self):
        node = shutil.which("node")
        if not node or not FI_JS.is_file():
            self.skipTest("node 또는 web/node_modules/fractional-indexing 없음 — node 비교 건너뜀")
        rnd = random.Random(20261126)
        pool = ["a0"]
        while len(pool) < 300:   # random insertions produce keys of varied lengths, including keys at the head
            i = rnd.randrange(len(pool) + 1)
            pool.insert(i, fi.generate_key_between(pool[i - 1] if i else None, pool[i] if i < len(pool) else None))
        pool += ["Zz", "Y00", "bzz", "zzzzzzzzzzzzzzzzzzzzzzzzzzz", "A000000000000000000000000001", "c000", "b125"]
        pool = sorted(set(pool))
        cases = []
        while len(cases) < 200:
            x, y = sorted(rnd.sample(pool, 2))
            mode = rnd.randrange(4)
            cases.append([x if mode != 1 else None, y if mode != 2 else None] if mode != 3 else [x, None])
        cases += [[None, None], [None, pool[0]], [pool[-1], None]]
        ns = [[c[0], c[1], rnd.randrange(2, 6)] for c in cases[:40]]
        script = (
            "import { generateKeyBetween as g, generateNKeysBetween as gn } from %s;\n"
            "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const q=JSON.parse(s);"
            "const f=(fn)=>{try{return fn()}catch(e){return null}};"
            "console.log(JSON.stringify({one:q.one.map(([a,b])=>f(()=>g(a,b))),n:q.n.map(([a,b,k])=>f(()=>gn(a,b,k)))}))});"
        ) % json.dumps(FI_JS.as_uri())
        r = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps({"one": cases, "n": ns}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)

        def py(fn):
            try:
                return fn()
            except ValueError:
                return None

        for (a, b), want in zip(cases, js["one"]):
            self.assertEqual(py(lambda: fi.generate_key_between(a, b)), want, (a, b))
        for (a, b, k), want in zip(ns, js["n"]):
            self.assertEqual(py(lambda: fi.generate_n_keys_between(a, b, k)), want, (a, b, k))

    def test_valid_key(self):
        for k in ("a0", "a0V", "Zz", "b125"):
            self.assertTrue(fi.is_valid_key(k), k)
        for k in (None, "", "a00", "a", "0", 5, "a0 ", "a0-"):
            self.assertFalse(fi.is_valid_key(k), k)


class Factory(unittest.TestCase):
    def check_full(self, e):
        self.assertTrue(BASE_FIELDS <= set(e), BASE_FIELDS - set(e))
        self.assertTrue(TYPE_FIELDS.get(e["type"], set()) <= set(e), e["type"])
        self.assertTrue(_finite(e["x"], e["y"], e["width"], e["height"]), e)
        self.assertEqual(e["roughness"], 0)
        self.assertTrue(0 <= e["seed"] < 2 ** 31 and 0 <= e["versionNonce"] < 2 ** 31)
        self.assertGreater(e["updated"], 1_600_000_000_000)   # ms
        self.assertLessEqual(len(e["id"]), 64)
        json.dumps(e)   # can be sent as-is

    def test_full_fields_every_kind(self):
        a = ak.make_box("rectangle", "입력", by="Claude")
        b = ak.make_box("ellipse", "판단 단계", by="Claude")
        d = ak.make_box("diamond", "예?")
        arr = ak.make_arrow(a[0], b[0], "예", by="Claude")
        free = ak.make_text("메모 note", 10, 20)
        fr = ak.make_frame(page=3)
        img = ak.make_image("f" * 40, 300, 200, latex="x^2")
        for e in a + b + d + arr + [free, fr, img]:
            self.check_full(e)
        self.assertEqual(a[0]["customData"], {"tbBy": "agent:Claude"})
        self.assertEqual(ak.make_box("rectangle", "x", by="agent:Codex")[0]["customData"]["tbBy"], "agent:Codex")
        self.assertNotIn("customData", free)   # a human (no `by`) gets no customData
        self.assertEqual(free["fontFamily"], 6)   # Board.jsx default 'gothic' = the Nunito slot
        self.assertEqual(free["lineHeight"], 1.35)   # Excalidraw FONT_METADATA[Nunito]
        self.assertEqual(fr["id"], "tbpage-3")
        self.assertEqual(fr["customData"], {"tbPage": 3})
        self.assertEqual(fr["name"], "장 3")
        self.assertEqual(img["customData"], {"latex": "x^2"})
        self.assertEqual((img["status"], img["scale"], img["crop"]), ("saved", [1, 1], None))

    def test_box_label_pairing_and_fit(self):
        for shape in ak.SHAPES:
            for text in ("A", "아주 긴 한국어 라벨이 들어가면 상자가 넓어지고 줄도 바뀐다", "Mixed 한글 and English WWW"):
                box, lab = ak.make_box(shape, text)
                self.assertEqual(lab["id"], box["id"] + "-label")
                self.assertEqual(lab["containerId"], box["id"])
                self.assertIn({"type": "text", "id": lab["id"]}, box["boundElements"])
                self.assertEqual((lab["textAlign"], lab["verticalAlign"]), ("center", "middle"))
                self.assertEqual(lab["originalText"], text)
                self.assertTrue(_inside(lab, box), (shape, text))
                # stays within the text width Excalidraw allows for that box (no overflow)
                self.assertLessEqual(lab["width"], ak._inner_max_width(box["width"], shape) + 0.01, (shape, text))
                self.assertLessEqual(lab["width"], ak.LABEL_MAX_WIDTH + 1)
                tw, th = ak.measure_text(lab["text"], lab["fontSize"], lab["fontFamily"])
                self.assertEqual((lab["width"], lab["height"]), (tw, th))
        box, lab = ak.make_box("rectangle", "가나다라마바사아자차카타파하", w=120)
        self.assertEqual(box["width"], 120)   # wraps lines within the given width
        self.assertIn("\n", lab["text"])
        self.assertTrue(_inside(lab, box))
        self.assertEqual(ak.make_box("rectangle", "x", w=300, h=200)[0]["height"], 200)
        self.assertEqual(len(ak.make_box("rectangle")), 1)
        with self.assertRaises(ValueError):
            ak.make_box("hexagon", "x")

    def test_arrow_binding_pairs(self):
        a = ak.make_box("rectangle", "A", 0, 0)
        b = ak.make_box("rectangle", "B", 400, 300)
        va = a[0]["version"]
        arr, lab = ak.make_arrow(a[0], b[0], "예")
        self.assertFalse(arr["elbowed"])
        self.assertEqual(arr["startBinding"]["elementId"], a[0]["id"])
        self.assertEqual(arr["endBinding"]["elementId"], b[0]["id"])
        self.assertIn({"type": "arrow", "id": arr["id"]}, a[0]["boundElements"])
        self.assertIn({"type": "arrow", "id": arr["id"]}, b[0]["boundElements"])
        self.assertIn({"type": "text", "id": a[0]["id"] + "-label"}, a[0]["boundElements"])   # the label pairing is untouched
        self.assertEqual(a[0]["version"], va + 1)   # the end box is re-rendered too
        self.assertEqual((lab["id"], lab["containerId"]), (arr["id"] + "-label", arr["id"]))
        self.assertIn({"type": "text", "id": lab["id"]}, arr["boundElements"])
        (x0, y0), (x1, y1) = arr["points"]
        self.assertEqual((x0, y0), (0.0, 0.0))
        self.assertTrue(_finite(x1, y1) and x1 > 0 and y1 > 0)
        self.assertAlmostEqual(arr["width"], abs(x1))
        self.assertAlmostEqual(arr["height"], abs(y1))
        # start is outside A's border, end is outside B's border (gap)
        sx, sy, ex, ey = arr["x"], arr["y"], arr["x"] + x1, arr["y"] + y1
        self.assertFalse(0 < sx < a[0]["width"] and 0 < sy < a[0]["height"])
        self.assertFalse(b[0]["x"] < ex < b[0]["x"] + b[0]["width"] and b[0]["y"] < ey < b[0]["y"] + b[0]["height"])
        # adding the same arrow twice still yields one pair
        ak._add_bound(a[0], "arrow", arr["id"])
        self.assertEqual(sum(1 for x in a[0]["boundElements"] if x["id"] == arr["id"]), 1)
        # point to point (no binding)
        free = ak.make_arrow((0, 0), (100, 0))[0]
        self.assertIsNone(free["startBinding"])
        self.assertEqual(free["points"][1], [100.0, 0.0])

    def test_shapes_edge_points(self):
        for shape in ak.SHAPES:
            a = ak.make_box(shape, None, 0, 0, 200, 100)[0]
            b = ak.make_box(shape, None, 600, 0, 200, 100)[0]
            arr = ak.make_arrow(a, b)[0]
            self.assertAlmostEqual(arr["x"], 200 + ak.BIND_GAP, places=6)
            self.assertAlmostEqual(arr["x"] + arr["points"][1][0], 600 - ak.BIND_GAP, places=6)

    def test_measure_text(self):
        self.assertEqual(ak.measure_text("가나다", 20, 6), (60, 20 * 1.35))
        w, h = ak.measure_text("ab\ncd", 10, 5)
        self.assertEqual(h, 2 * 10 * 1.25)
        self.assertEqual(ak.measure_text("aaaa", 10, 6)[0], 23)   # measured Nunito 'a' = 0.56em
        self.assertEqual(ak.line_height(23), 1.25)   # added slot (TB Do Hyeon) -- Excalidraw falls back to the Excalifont value
        self.assertEqual(ak.line_height(7), 1.15)

    def test_wrap_text(self):
        s = ak.wrap_text("one two three four five six seven", 80, 20, 6)
        for line in s.split("\n"):
            self.assertLessEqual(ak.measure_text(line, 20, 6)[0], 80)
        self.assertEqual(s.replace("\n", " "), "one two three four five six seven")
        long = ak.wrap_text("가" * 30, 100, 20, 6)
        self.assertTrue(all(len(x) <= 5 for x in long.split("\n")))
        self.assertEqual(long.replace("\n", ""), "가" * 30)


class Index(unittest.TestCase):
    def test_next_index_above_board(self):
        self.assertEqual(ak.next_index([]), "a0")
        self.assertEqual(ak.next_index([{"index": "a0"}, {"index": "a5"}, {"index": "a2V"}]), "a6")
        self.assertEqual(ak.next_index([{"index": "b00", "isDeleted": True}, {"index": "a1"}]), "b01")   # deleted elements count too
        self.assertEqual(ak.next_index([{"index": "Zz"}]), "a0")
        # 3 old elements without an index -- above where Excalidraw would assign a0 a1 a2
        self.assertEqual(ak.next_index([{"index": None}, {}, {"index": "a00"}]), "a3")
        # Excalidraw may assign a broken index after the last valid key (syncInvalidIndices) -- go that much higher
        self.assertEqual(ak.next_index([{"index": None}, {"index": "b10"}]), "b12")
        self.assertEqual(ak.next_index([{"index": "a5"}, {"index": None}, {"index": None}]), "a8")

    def test_place_right_below_point(self):
        board = ak.make_box("rectangle", "있던 것", 0, 0, 200, 100)
        ak.assign_indices([], board)
        a = ak.make_box("rectangle", "A")
        b = ak.make_box("rectangle", "B", 0, 200)
        arr = ak.make_arrow(a[0], b[0])
        items = ak.place(board, a + b + arr)
        self.assertEqual(min(e["x"] for e in items), 200 + 80)
        self.assertEqual(min(e["y"] for e in items), 0)
        keys = [e["index"] for e in items]
        self.assertEqual(keys, sorted(keys))
        self.assertTrue(all(k > board[-1]["index"] for k in keys))   # above the existing elements (z-order)
        self.assertEqual(len(set(keys)), len(keys))
        self.assertTrue(_inside(a[1], a[0]) and _inside(b[1], b[0]))   # the label moved along with its box
        self.assertAlmostEqual(arr[0]["x"], a[0]["x"] + 80, places=6)   # the arrow too (bottom centre)
        items2 = ak.place(board, [ak.make_text("아래")], at="below")
        self.assertEqual((items2[0]["x"], items2[0]["y"]), (0, 100 + 80))
        items3 = ak.place(board, [ak.make_text("점")], at=(-50, 30))
        self.assertEqual((items3[0]["x"], items3[0]["y"]), (-50, 30))
        self.assertEqual(ak.place([], [ak.make_text("첫")])[0]["index"], "a0")

    def test_r2_empty_board_origin_clears_top_bar(self):
        """An empty board (or one with only deleted elements) starts its first group at EMPTY_ORIGIN, not (0, 0)
        (below the phone's top button row, the desktop menu button, and the '+ new page' button). With existing drawings, 'right' still goes to their right."""
        for board in ([], [dict(ak.make_text("지운 것", 0, 0), isDeleted=True)]):
            a = ak.make_box("rectangle", "입력", 0, 0)
            b = ak.make_box("rectangle", "판단", 260, 0)
            arr = ak.make_arrow(a[0], b[0])
            t = ak.make_text("메모", 0, 200)
            items = ak.place(board, a + b + arr + [t])
            bb = ak.scene_bbox(items)
            self.assertEqual((bb[0], bb[1]), ak.EMPTY_ORIGIN)
            self.assertGreaterEqual(min(e["y"] for e in items), 100, "위 단추 줄(약 62px)보다 아래")
            self.assertEqual((a[1]["x"] - a[0]["x"], a[1]["y"] - a[0]["y"]),
                             ((a[0]["width"] - a[1]["width"]) / 2, (a[0]["height"] - a[1]["height"]) / 2), "라벨은 상자 가운데 그대로")
        old = ak.make_box("rectangle", "있던 것", -500, -500, 200, 100)
        new = ak.make_box("rectangle", "새", 0, 0)
        ak.place(old, new)
        self.assertEqual((new[0]["x"], new[0]["y"]), (-500 + 200 + 80, -500), "그림이 있으면 'right' 는 그대로")

    def test_place_reroutes_arrow_to_existing_box(self):
        old = ak.make_box("rectangle", "있던 것", 0, 0, 200, 100)
        ak.assign_indices([], old)
        new = ak.make_box("rectangle", "새것", w=200)
        arr = ak.make_arrow(old[0], new[0])
        ak.place(old, new + arr, at=(0, 400))
        (x0, y0), (x1, y1) = arr[0]["points"]
        sx, sy = arr[0]["x"], arr[0]["y"]
        self.assertAlmostEqual(sy, 100 + ak.BIND_GAP, places=6)   # leaves from the bottom edge of the existing box
        self.assertAlmostEqual(sy + y1, 400 - ak.BIND_GAP, places=6)
        self.assertEqual(old[0]["version"], 2)   # the existing box's boundElements changed so its version went up -> it is sent along


def _stroke(i, x, y):
    return {"id": "ink%d" % i, "type": "freedraw", "x": x, "y": y, "width": 100, "height": 20, "isDeleted": False,
            "points": [[0, 0], [50, 10], [100, 20]], "pressures": [], "simulatePressure": True, "index": None}


class Describe(unittest.TestCase):
    def scene(self):
        p1 = ak.make_frame(page=1, x=0, y=0, w=1600, h=900)
        p2 = ak.make_frame(page=2, x=1760, y=0, w=1600, h=900)
        a = ak.make_box("rectangle", "A", 100, 100, id="boxA", frame_id="tbpage-1")
        b = ak.make_box("rectangle", "B", 500, 100, id="boxB", frame_id="tbpage-1")
        c = ak.make_box("rectangle", "C", 1860, 100, id="boxC", frame_id="tbpage-2")
        ab = ak.make_arrow(a[0], b[0], "예", id="arrAB")
        bc = ak.make_arrow(b[0], c[0], id="arrBC")
        m = ak.make_image("m" * 40, 200, 60, 100, 400, latex=r"\hat\theta = x", id="math1", frame_id="tbpage-1")
        memo = ak.make_text("메모\n둘째 줄", 1860, 300, id="memo1")
        ink = [_stroke(i, 800, 500 + i * 20) for i in range(5)]
        gone = ak.make_text("지운 글", 0, 0, id="gone")
        gone["isDeleted"] = True
        els = [p2, memo] + c + ink + bc + [m] + ab + b + a + [p1, gone]   # deliberately shuffled
        return els

    def test_summary_text(self):
        text, st = ak.describe(self.scene())
        want = "\n".join([
            "보드 요약 — 장 2 · 상자 3 · 글 1 · 수식 1 · 그림 0 · 연결 2 · 손글씨 획 5",
            '[장 1] "장 1" (tbpage-1)',
            '  [boxA] 상자 "A"',
            '  [boxB] 상자 "B"',
            "  [math1] 수식 $\\hat\\theta = x$",
            '  [arrAB] 연결: "A" → "B" (라벨 "예")',
            '  [arrBC] 연결: "B" → "C"',
            "  손글씨 획 5개(영역 x 800..900, y 500..600) — 글로 읽을 수 없음, 그림으로 볼 것",
            '[장 2] "장 2" (tbpage-2)',
            '  [boxC] 상자 "C"',
            '  [memo1] 글 "메모 / 둘째 줄"',
        ])
        self.assertEqual(text, want)
        self.assertIn('"A" → "B"', text)
        self.assertIn("손글씨 획 5개", text)
        self.assertEqual(st["counts"]["strokes"], 5)
        self.assertEqual([p["id"] for p in st["pages"]], ["tbpage-1", "tbpage-2"])
        self.assertEqual([i["id"] for i in st["items"]], ["boxA", "boxB", "math1", "boxC", "memo1"])
        self.assertEqual(st["items"][0]["label_id"], "boxA-label")
        self.assertEqual(st["items"][2]["latex"], r"\hat\theta = x")
        link = st["links"][0]
        self.assertEqual((link["from"], link["to"], link["label"]), ("boxA", "boxB", "예"))
        self.assertEqual(st["strokes"][0]["count"], 5)
        json.dumps(st)

    def test_page_filter_and_no_pages(self):
        text, _ = ak.describe(self.scene(), page=2)
        self.assertNotIn("boxA", text)
        self.assertIn('[boxC] 상자 "C"', text)
        a = ak.make_box("rectangle", "X", 0, 0, id="bx")
        t = ak.make_text("아래 글", 0, 300, id="tx")
        line = ak.make_arrow(a[0], (500, 500), id="ar")
        text, st = ak.describe(a + [t] + line)
        self.assertEqual(text.split("\n")[1:], ['[bx] 상자 "X"', '[tx] 글 "아래 글"', '[ar] 연결: "X" → (빈 곳)'])
        self.assertEqual(ak.describe([])[0], "보드가 비어 있다.")

    def test_reading_order_rows(self):
        # two elements of different heights on the same row are ordered left to right
        tall = ak.make_box("rectangle", "왼쪽", 0, 0, 100, 300, id="L")
        short = ak.make_box("rectangle", "오른쪽", 300, 40, 100, 60, id="R")
        below = ak.make_text("아래", 0, 400, id="D")
        _, st = ak.describe([below] + short + tall)
        self.assertEqual([i["id"] for i in st["items"]], ["L", "R", "D"])


class ReviewR1(unittest.TestCase):
    """One regression test per review finding."""

    def board(self):
        old = ak.make_box("rectangle", "있던 것", -1000, -1000, 200, 100)
        ak.assign_indices([], old)
        other = ak.make_box("rectangle", "오른쪽 끝", 500, 0, 200, 100)
        ak.assign_indices(old, other)
        return old, other

    # an arrow attached to an existing box must not drag the group's anchor point
    def test_f1_place_ignores_arrow_to_existing_for_anchor(self):
        old, other = self.board()
        els = old + other
        new = ak.make_box("rectangle", "새것")
        arr = ak.make_arrow(old[0], new[0])
        ak.place(els, new + arr, at=(0, 0))
        self.assertEqual((new[0]["x"], new[0]["y"]), (0, 0))
        new2 = ak.make_box("rectangle", "새것2")
        arr2 = ak.make_arrow(old[0], new2[0])
        ak.place(els, new2 + arr2, at="right")
        self.assertEqual((new2[0]["x"], new2[0]["y"]), (700 + 80, -1000))
        # after the move the arrow is reconnected -- it starts near the existing box and ends near the new box
        a = arr2[0]
        sx, sy = a["x"], a["y"]
        ex, ey = sx + a["points"][1][0], sy + a["points"][1][1]
        self.assertTrue(-1000 - 10 <= sx <= -800 + 10 and -1000 - 10 <= sy <= -900 + 10, (sx, sy))
        b = new2[0]
        self.assertTrue(b["x"] - 10 <= ex <= b["x"] + b["width"] + 10 and b["y"] - 10 <= ey <= b["y"] + b["height"] + 10)

    def test_f1_existing_items_not_moved_or_reindexed(self):
        old, other = self.board()
        els = old + other
        before = (old[0]["x"], old[0]["y"], old[0]["index"])
        new = ak.make_box("rectangle", "새것")
        arr = ak.make_arrow(old[0], new[0])
        items = ak.place(els, new + arr + [old[0]], at=(0, 0))   # even when the modified existing box is included
        self.assertIn(old[0], items)
        self.assertEqual((old[0]["x"], old[0]["y"], old[0]["index"]), before)
        self.assertEqual((new[0]["x"], new[0]["y"]), (0, 0))
        self.assertTrue(all(e["index"] > other[-1]["index"] for e in new + arr))
        # an arrow connecting only existing boxes -- nothing to move; just reconnect and set the index
        link = ak.make_arrow(old[0], other[0])
        ak.place(els, link + [old[0], other[0]])
        self.assertTrue(-1000 <= link[0]["x"] <= -800 + 10)
        self.assertIsNotNone(link[0]["index"])

    # page numbers, deleted pages, seed
    def test_f2_make_frame_page_rules(self):
        gone = dict(ak.make_frame(page=2), isDeleted=True, version=3, index="a7")
        live1 = ak.make_frame(page=1)
        fr = ak.make_frame(page=2, existing=[live1, gone])
        self.assertEqual(fr["id"], "tbpage-2")
        self.assertEqual(fr["version"], 4)   # server wins (higher version) -- it is not discarded
        self.assertEqual(fr["index"], "a7")
        self.assertFalse(fr["isDeleted"])
        self.assertEqual(fr["seed"], 7919 * 2)   # same as pages.js pageElementsFor
        with self.assertRaises(ValueError):
            ak.make_frame(page=1, existing=[live1])   # never overwrites a human's page
        self.assertEqual(ak.next_page([]), 1)
        self.assertEqual(ak.next_page([live1, gone]), 2)
        child = dict(ak.make_text("x"), frameId="tbpage-2")
        self.assertEqual(ak.next_page([live1, child]), 3)   # skipped if an element holds that id (pages.js nextKey)
        self.assertEqual(ak.next_page([live1, ak.make_frame(page=4)]), 5)

    # one malformed element must not crash anything, and NaN must not spread
    def test_f3_malformed_elements(self):
        good = ak.make_box("rectangle", "정상", 0, 0, id="good")
        broken = [
            {"id": "b1", "type": "arrow"},                                    # no x
            {"id": "b2", "type": "arrow", "x": None, "y": 0, "points": [[0, 0], [1, 1]]},
            {"id": "b3", "type": "arrow", "x": 0, "y": 0, "points": [[0, 0], [None, None]]},
            {"id": "b4", "type": "frame", "customData": {"tbPage": 9}},         # a page (frame) without x/y
            {"id": "b5", "type": "image", "x": 0, "y": 0, "width": "abc", "height": 5},
            {"id": "b6", "type": "arrow", "x": 0, "y": 0, "points": [[0, 0], [5, 5]], "startBinding": "x",
             "endBinding": ["good"]},
            {"id": "b7", "type": "text", "x": 0, "y": 0, "text": "t", "containerId": ["r"]},
            {"id": "b8", "type": "rectangle", "x": float("nan"), "y": 0, "width": 10, "height": 10},
            {"id": "b9", "type": ["list"], "x": 0, "y": 0, "frameId": {"a": 1}},
            {"id": ["no"], "type": "text"},
            "not a dict",
            {"id": "b10", "type": "freedraw", "x": 0, "y": 0, "points": "zzz"},
            {"id": "b11", "type": "text", "x": 1, "y": 1, "text": None, "originalText": 5,
             "customData": "x", "boundElements": "y"},
        ]
        for bad in broken:
            els = [bad] + good
            text, st = ak.describe(els)
            self.assertIn('[good] 상자 "정상"', text, bad)
            json.dumps(st)
            items = ak.place(els, [ak.make_text("새 글")])
            self.assertTrue(_finite(items[0]["x"], items[0]["y"]), (bad, items[0]))
        text, _ = ak.describe(broken + good)
        self.assertIn("정상", text)
        # even with a NaN box first, the new element's position is finite
        items = ak.place([broken[7]] + good, [ak.make_text("새 글")], at="right")
        self.assertEqual((items[0]["x"], items[0]["y"]), (160 + 80, 0))
        # two texts that contain each other + an arrow bound to one of them -- must not loop forever
        ta = {"id": "ta", "type": "text", "x": 0, "y": 0, "text": "A", "containerId": "tb"}
        tb = {"id": "tb", "type": "text", "x": 0, "y": 0, "text": "B", "containerId": "ta"}
        ar = {"id": "ar", "type": "arrow", "x": 0, "y": 0, "points": [[0, 0], [9, 9]], "startBinding": {"elementId": "ta"}}
        ak.describe([ta, tb, ar, ak.make_frame(page=1)])
        # connecting to / reconnecting an existing broken box must not crash either
        weird = {"id": "w1", "type": "rectangle", "x": "1", "y": None, "boundElements": [None, 3], "version": "v"}
        arr = ak.make_arrow(weird, good[0])
        self.assertEqual(weird["version"], 1)
        self.assertTrue(_finite(arr[0]["x"], arr[0]["y"], *arr[0]["points"][1]))

    # ambiguous-width symbols, emoji, accents
    def test_f4_ambiguous_width_glyphs(self):
        for ch in "→←↔※★√≈∫∝×…±":
            self.assertGreaterEqual(ak._char_em(ch, 6), 1.0, ch)   # rendered up to 1.0em wide on screen (Korean font), as measured
        self.assertGreaterEqual(ak._char_em("😀", 6), 1.1)
        self.assertEqual(ak._char_em("é", 6), ak._char_em("e", 6))
        # never estimate narrower than the on-screen measurement (Chromium measureText, Nunito)
        real_nunito = {"d": 0.62, "o": 0.61, "m": 0.93, "W": 0.88, "@": 0.95, "가": 0.92, "→": 1.0}
        for ch, real in real_nunito.items():
            self.assertGreaterEqual(ak._char_em(ch, 6), real, ch)
        box, lab = ak.make_box("rectangle", "A→B→C→D→E→F→G→H→I→J→K→L→M→N")
        for line in lab["text"].split("\n"):
            real = sum(NUNITO_REAL.get(c, 1.0) for c in line) * 20   # measured on-screen width (-> is 1.0em)
            self.assertLessEqual(real, ak._inner_max_width(box["width"], "rectangle"), line)

    # the summary uses the original text (originalText)
    def test_f5_describe_uses_original_text(self):
        raw = "수집 → 정리 → 분석 → 검토 → 보고 → 공유 → 보관 → 폐기"
        box = ak.make_box("rectangle", raw, id="bx")
        self.assertIn("\n", box[1]["text"])   # the on-screen text is wrapped
        text, st = ak.describe(box)
        self.assertIn('[bx] 상자 "%s"' % raw, text)
        self.assertEqual(st["items"][0]["text"], raw)
        wrapped = ak.make_text("one two three four five six seven", 0, 200, max_width=80, id="tw")
        text, _ = ak.describe([wrapped])
        self.assertIn('[tw] 글 "one two three four five six seven"', text)
        a, b = ak.make_box("rectangle", "A", id="ba"), ak.make_box("rectangle", "B", 400, 0, id="bb")
        ln = ak.make_arrow(a[0], b[0], "아주 긴 라벨 " * 8, id="ln")
        text, st = ak.describe(a + b + ln)
        self.assertIn('(라벨 "%s")' % _clip_like(("아주 긴 라벨 " * 8).strip(), 60), text)

    # a free text that was wrapped is a fixed-width text (autoResize:false)
    def test_f6_wrapped_free_text_fixed_width(self):
        t = ak.make_text("one two three four five six seven", max_width=80)
        self.assertIn("\n", t["text"])
        self.assertEqual(t["originalText"], "one two three four five six seven")
        self.assertFalse(t["autoResize"])
        self.assertGreaterEqual(t["width"], 80)
        for line in t["text"].split("\n"):
            self.assertLessEqual(ak.measure_text(line, 20, 6)[0], t["width"])
        short = ak.make_text("짧다", max_width=300)
        self.assertTrue(short["autoResize"])
        self.assertEqual(short["text"], short["originalText"])
        self.assertTrue(ak.make_text("보통")["autoResize"])

    # the label id must not exceed 64 characters
    def test_s3_label_id_length(self):
        ak.make_box("rectangle", "x", id="a" * 58)
        with self.assertRaises(ValueError):
            ak.make_box("rectangle", "x", id="a" * 59)
        with self.assertRaises(ValueError):
            ak.make_arrow((0, 0), (1, 1), id="a" * 59)
        with self.assertRaises(ValueError):
            ak.make_text("x", id="a" * 65)

    # a bound text whose container was deleted
    def test_s6_orphan_label(self):
        box, lab = ak.make_box("rectangle", "남은 라벨", id="bz")
        box["isDeleted"] = True
        text, st = ak.describe([box, lab])
        self.assertIn('[bz-label] 떨어진 라벨 "남은 라벨"', text)
        self.assertEqual(st["counts"]["orphan_labels"], 1)
        self.assertEqual(st["counts"]["texts"], 0)


# Nunito em widths measured with Chromium canvas measureText (2026-11-26; the glyphs used to reproduce the width bug)
NUNITO_REAL = {"A": 0.61, "B": 0.66, "C": 0.64, "D": 0.69, "E": 0.59, "F": 0.55, "G": 0.69, "H": 0.73, "I": 0.29,
               "J": 0.54, "K": 0.65, "L": 0.54, "M": 0.81, "N": 0.72, "→": 1.0}


def _clip_like(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


class Py39(unittest.TestCase):
    def test_import_with_system_python(self):
        py = Path("/usr/bin/python3")
        if not py.exists():
            self.skipTest("/usr/bin/python3 없음")
        code = ("import sys; sys.path.insert(0, %r); import agentkit, fracindex; "
                "b = agentkit.make_box('rectangle', 'A'); print(sys.version_info[:2], agentkit.next_index(b))") % str(HERE / "server")
        r = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
