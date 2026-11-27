"""Import tests: the importer (server/importers.py), the server /import endpoint, the `tempboard import` CLI, and MCP board_import.

Covered: merging twice = original x 3 with zero orphans and a new-board round trip; the CLI against a running and a stopped site; MCP path and extension rejection; 30 images > 12 MB;
permissions (view-only key and view token get 403); input validation (MIME, header, path escape, type, broken elements); the library; page numbers.
Everything uses temporary folders and a temporary TEMPBOARD_HOME (~/.tempboard is never touched), and no real tunnel."""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["TEMPBOARD_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import base64
import copy
import hashlib
import http.client
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "tempboard"
PY39 = Path("/usr/bin/python3")


def _load(name):
    key = "tempboard_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "server" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


srv = _load("server")
ak = _load("agentkit")
imp = _load("importers")

PNG1 = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a5b6d8b10000000049454e44ae426082")
GIF1 = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
SVG1 = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20"/></svg>'
EK, VK = "edit-key-123", "view-key-456"
TOK_E, TOK_V = "tb_ag_" + "e" * 32, "tb_ag_" + "v" * 32


def durl(raw, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def sample_doc():
    """2 boxes (with labels), an arrow (with a label, bound at both ends), a group, 1 drawing page + its text, 1 image, 1 freehand stroke."""
    a = ak.make_box("rectangle", "A", 0, 0, id="boxA")
    b = ak.make_box("ellipse", "B", 300, 0, id="boxB")
    arr = ak.make_arrow(a[0], b[0], "예", id="arrAB")
    g = "grp1"
    a[0]["groupIds"] = [g]
    b[0]["groupIds"] = [g]
    page = ak.make_frame(page=1, x=0, y=300, w=800, h=450)
    note = ak.make_text("장 안 글", 40, 360, id="note1", frame_id=page["id"])
    img = ak.make_image("oldfileid0001", 50, 50, 600, 0, id="img1")
    ink = dict(ak._base("freedraw", 700, 100, 30, 30, id="ink1"), points=[[0, 0], [10, 10], [30, 5]], pressures=[],
               simulatePressure=True, lastCommittedPoint=None)
    els = a + b + arr + [page, note, img, ink]
    ak.assign_indices([], els)
    return {"type": "excalidraw", "version": 2, "source": "tempboard", "elements": els, "appState": {},
            "files": {"oldfileid0001": {"id": "oldfileid0001", "mimeType": "image/png", "dataURL": durl(PNG1), "created": 1}}}


def refs_ok(els):
    """Checks whether every reference inside a group points inside the group -> list of orphans."""
    ids = {e["id"] for e in els}
    bad = []
    for e in els:
        if e.get("containerId") and e["containerId"] not in ids:
            bad.append(("containerId", e["id"]))
        if e.get("frameId") and e["frameId"] not in ids:
            bad.append(("frameId", e["id"]))
        for k in ("startBinding", "endBinding"):
            if e.get(k) and e[k]["elementId"] not in ids:
                bad.append((k, e["id"]))
        for be in e.get("boundElements") or []:
            if be["id"] not in ids:
                bad.append(("boundElements", e["id"]))
    return bad


class Parse(unittest.TestCase):
    def test_excalidraw_full_fields_and_file_id_is_content_hash(self):
        p = imp.parse("회의.excalidraw", json.dumps(sample_doc()).encode())
        self.assertEqual(p["kind"], "excalidraw")
        self.assertEqual(p["title"], "회의")
        sha = hashlib.sha1(PNG1).hexdigest()
        self.assertEqual(list(p["files"]), [sha])
        img = next(e for e in p["elements"] if e["type"] == "image")
        self.assertEqual(img["fileId"], sha)
        self.assertEqual(refs_ok(p["elements"]), [])
        for e in p["elements"]:
            for k in ("x", "y", "width", "height", "angle", "strokeColor", "opacity", "groupIds", "roundness", "seed", "version",
                      "versionNonce", "isDeleted", "boundElements", "updated", "link", "locked", "frameId", "index"):
                self.assertIn(k, e, (e["type"], k))

    def test_filters_types_broken_and_unsafe(self):
        doc = sample_doc()
        doc["elements"] += [
            {"id": "emb", "type": "embeddable", "x": 0, "y": 0, "width": 10, "height": 10, "link": "https://evil.example"},
            {"id": "nanx", "type": "rectangle", "x": float("nan"), "y": 0},
            {"id": "notext", "type": "text", "x": 0, "y": 0},
            {"id": "line1", "type": "line", "x": 0, "y": 0, "points": [[0, 0]]},
            {"id": "boxA", "type": "rectangle", "x": 5, "y": 5, "width": 5, "height": 5},     # duplicate id
            {"id": "gone", "type": "rectangle", "x": 0, "y": 0, "isDeleted": True},
            "not-an-element",
            {"id": "js", "type": "rectangle", "x": 0, "y": 0, "width": 1, "height": 1, "link": "javascript:alert(1)",
             "strokeColor": "red;background:url(x)", "customData": {"big": "x" * 10000}},
        ]
        raw = json.dumps(doc, allow_nan=True).encode()
        p = imp.parse("x.excalidraw", raw)
        sk = p["skipped"]
        self.assertEqual(sk.get("type"), 1)
        self.assertEqual(sk.get("broken"), 4)    # nan, a text with no text, a one-point line, a string
        self.assertEqual(sk.get("dup_id"), 1)
        self.assertNotIn("deleted", sk)
        js = next(e for e in p["elements"] if e["id"] == "js")
        self.assertIsNone(js["link"])
        self.assertEqual(js["strokeColor"], ak.INK)
        self.assertNotIn("customData", js)

    def test_image_mime_must_match_header(self):
        doc = sample_doc()
        doc["files"]["oldfileid0001"]["dataURL"] = durl(GIF1, "image/png")   # declared png but it is actually gif
        p = imp.parse("x.excalidraw", json.dumps(doc).encode())
        self.assertEqual(p["files"], {})
        self.assertEqual(p["skipped"].get("file"), 1)
        self.assertEqual(p["skipped"].get("image_no_file"), 1)
        self.assertFalse(any(e["type"] == "image" for e in p["elements"]))
        doc["files"]["oldfileid0001"]["dataURL"] = "data:text/html;base64," + base64.b64encode(b"<script>").decode()
        p = imp.parse("x.excalidraw", json.dumps(doc).encode())
        self.assertEqual(p["skipped"].get("file"), 1)
        # importing an image file directly: rejected if the extension and header differ; for svg the size is read
        with self.assertRaises(imp.ImportFailed):
            imp.parse("a.png", GIF1)
        with self.assertRaises(imp.ImportFailed):
            imp.parse("a.png", b"not an image at all")
        p = imp.parse("a.svg", SVG1)
        self.assertEqual(p["kind"], "image")
        self.assertEqual((p["elements"][0]["width"], p["elements"][0]["height"]), (40.0, 20.0))
        self.assertEqual(list(p["files"].values())[0]["mimeType"], "image/svg+xml")
        with self.assertRaises(imp.ImportFailed):
            imp.parse("a.txt", b"hello")
        with self.assertRaises(imp.ImportFailed):
            imp.parse("a.json", b'{"type":"other"}')

    def test_library_v1_v2_grid_new_ids_groups(self):
        box = ak.make_box("rectangle", "L", 0, 0, id="libbox")
        v2 = {"type": "excalidrawlib", "version": 2, "libraryItems": [
            {"id": "i1", "status": "published", "elements": box}, {"id": "i2", "status": "published", "elements": box},
            {"id": "i3", "elements": []}, {"id": "i4", "elements": [ak.make_text("t", 0, 0, id="libt")]}]}
        p = imp.parse("my.excalidrawlib", json.dumps(v2).encode())
        self.assertEqual(p["kind"], "library")
        self.assertEqual(len(p["elements"]), 5)
        ids = [e["id"] for e in p["elements"]]
        self.assertEqual(len(set(ids)), 5)
        self.assertNotIn("libbox", ids)
        self.assertEqual(p["skipped"].get("lib_item"), 1)
        self.assertEqual(refs_ok(p["elements"]), [])
        boxes = [e for e in p["elements"] if e["type"] == "rectangle"]
        self.assertNotEqual((boxes[0]["x"], boxes[0]["y"]), (boxes[1]["x"], boxes[1]["y"]))   # spread out on a grid
        self.assertTrue(boxes[0]["groupIds"] and boxes[0]["groupIds"] != boxes[1]["groupIds"])
        v1 = {"type": "excalidrawlib", "version": 1, "library": [box, box]}
        self.assertEqual(len(imp.parse("old.excalidrawlib", json.dumps(v1).encode())["elements"]), 4)


class Prepare(unittest.TestCase):
    def setUp(self):
        self.parsed = imp.parse("s.excalidraw", json.dumps(sample_doc()).encode())
        self.board = copy.deepcopy(self.parsed["elements"])   # the board already has the original (same ids)

    def test_merge_twice_is_three_times_no_overlap_no_orphans(self):
        """Merging twice into the same board: original x 3, no id collisions, and each group's references stay inside that group (0 orphans)."""
        n = len(self.board)
        batches = []
        for _ in range(2):
            items = imp.prepare(self.parsed["elements"], self.board, "merge")
            self.assertEqual(refs_ok(items), [])
            self.board += items
            batches.append(items)
        self.assertEqual(len(self.board), n * 3)
        self.assertEqual(len({e["id"] for e in self.board}), n * 3)
        g0 = {g for e in self.parsed["elements"] for g in e["groupIds"]}
        g1 = {g for e in batches[0] for g in e["groupIds"]}
        g2 = {g for e in batches[1] for g in e["groupIds"]}
        self.assertTrue(g1 and not (g0 & g1) and not (g1 & g2))
        # the box text id convention (<box>-label) is kept, and the page number is the board's next page
        for items in batches:
            for t in items:
                if t["type"] == "text" and t.get("containerId") and next(c for c in items if c["id"] == t["containerId"])["type"] != "arrow":
                    self.assertEqual(t["id"], t["containerId"] + "-label")
        pages = sorted(e["customData"]["tbPage"] for e in self.board if e["type"] == "frame")
        self.assertEqual(pages, [1, 2, 3])
        p2 = next(e for e in batches[0] if e["type"] == "frame")
        self.assertEqual(p2["id"], "tbpage-2")
        self.assertEqual(p2["name"], "장 2")
        self.assertTrue(any(e.get("frameId") == "tbpage-2" for e in batches[0]))

    def test_merge_index_above_board_and_order_kept(self):
        items = imp.prepare(self.parsed["elements"], self.board, "merge")
        top = max(e["index"] for e in self.board)
        self.assertTrue(all(e["index"] > top for e in items))
        order_src = [e["type"] for e in sorted(self.parsed["elements"], key=lambda e: e["index"])]
        order_new = [e["type"] for e in sorted(items, key=lambda e: e["index"])]
        self.assertEqual(order_src, order_new)
        self.assertTrue(all(e["version"] == 1 for e in items if not e["id"].startswith("tbpage-")))

    def test_merge_position_right_and_center(self):
        bb = ak.scene_bbox(self.board)
        items = imp.prepare(self.parsed["elements"], self.board, "merge")
        mine = ak.scene_bbox(items)
        self.assertAlmostEqual(mine[0], bb[2] + 80)
        self.assertAlmostEqual(mine[1], bb[1])
        no_pages = [e for e in self.board if e["type"] != "frame"]   # if the board already has a drawing page, new items go to its right (see the regression test below)
        items = imp.prepare(self.parsed["elements"], no_pages, "merge", {"x": 5000, "y": -300})
        mine = ak.scene_bbox(items)
        self.assertAlmostEqual((mine[0] + mine[2]) / 2, 5000)
        self.assertAlmostEqual((mine[1] + mine[3]) / 2, -300)
        with self.assertRaises(imp.ImportFailed):
            imp.prepare(self.parsed["elements"], self.board, "merge", {"x": "a", "y": 1})
        # an empty board keeps the original position
        items = imp.prepare(self.parsed["elements"], [], "merge")
        self.assertEqual(ak.scene_bbox(items), ak.scene_bbox(self.parsed["elements"]))

    def test_new_keeps_ids(self):
        items = imp.prepare(self.parsed["elements"], [], "new")
        self.assertEqual([(e["id"], e["x"], e["y"], e.get("text")) for e in items],
                         [(e["id"], e["x"], e["y"], e.get("text")) for e in self.parsed["elements"]])


class ReadPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_traversal_extension_symlink_dir_size(self):
        cases = ["../../etc/passwd", "/etc/passwd", str(self.d), str(self.d / "nope.excalidraw"), "", "a\x00b"]
        (self.d / "notes.txt").write_text("x")
        cases.append(str(self.d / "notes.txt"))
        os.symlink("/etc/passwd", self.d / "evil.excalidraw")   # named .excalidraw only -- the resolved target is outside the allowed area
        cases.append(str(self.d / "evil.excalidraw"))
        os.mkdir(self.d / "dir.excalidraw")
        cases.append(str(self.d / "dir.excalidraw"))
        cases.append(str(self.d / "sub" / ".." / "x.excalidraw"))
        for c in cases:
            with self.assertRaises(imp.ImportFailed, msg=c):
                imp.read_path(c)
        ok = self.d / "ok.excalidraw"
        ok.write_text(json.dumps(sample_doc()))
        name, data = imp.read_path(str(ok))
        self.assertEqual(name, "ok.excalidraw")
        with self.assertRaises(imp.ImportFailed):
            imp.read_path(str(ok), max_bytes=10)


def raw(port, method, path, headers, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers={"Host": f"127.0.0.1:{port}", **headers})
    r = c.getresponse()
    d = r.read()
    c.close()
    try:
        return r.status, json.loads(d) if d else None
    except ValueError:
        return r.status, d


def keyh(key=EK, name="사람"):
    return {"X-Tb-Key": key, "X-Tb-Name": urllib.parse.quote(name), "X-Whiteboard": "1", "Content-Type": "application/json"}


def bearer(tok):
    return {"Authorization": "Bearer " + tok, "X-Whiteboard": "1", "Content-Type": "application/json"}


class Endpoint(unittest.TestCase):
    """Server /import -- only editors (key / edit token) may import; viewers (key / view token) get 403. Merge broadcast and new board."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        site = Path(self.tmp.name) / "s1"
        (site / "data").mkdir(parents=True)
        now = time.time()
        st = {"edit_key": EK, "view_key": VK, "expires_at": 0, "agents": [
            {"id": "e1", "name": "Claude", "sha256": hashlib.sha256(TOK_E.encode()).hexdigest(), "role": "edit", "expires_at": now + 3600, "created_at": now, "by": "cli"},
            {"id": "v1", "name": "Viewer", "sha256": hashlib.sha256(TOK_V.encode()).hexdigest(), "role": "view", "expires_at": now + 3600, "created_at": now, "by": "cli"}]}
        (site / "state.json").write_text(json.dumps(st))
        self.httpd, self.store = srv.build(site / "data", HERE / "web" / "dist", access=srv.Access(EK, VK, site="s1"), save_interval=0.2)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        code, d = raw(self.port, "POST", "/api/whiteboard/boards", keyh(), {"title": "b"})
        self.bid = d["id"]
        self.doc = json.dumps(sample_doc())

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_permissions(self):
        body = {"name": "x.excalidraw", "content": self.doc}
        for h in (keyh(VK), bearer(TOK_V)):
            self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", h, body)[0], 403)
            self.assertEqual(raw(self.port, "POST", "/api/whiteboard/boards/import", h, body)[0], 403)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", {"X-Whiteboard": "1"}, body)[0], 403)
        n0 = len(self.store.list())
        code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", bearer(TOK_E), body)
        self.assertEqual(code, 200, d)
        self.assertEqual(d["added"], len(sample_doc()["elements"]))
        self.assertEqual(d["files"], 1)
        self.assertEqual(d["missing_files"], [])
        code, d = raw(self.port, "POST", "/api/whiteboard/boards/import", keyh(), {**body, "title": "지난주"})
        self.assertEqual(code, 201, d)
        self.assertEqual(d["meta"]["title"], "지난주")
        self.assertEqual(len(self.store.list()), n0 + 1)
        b = self.store.get(d["board"])
        self.assertEqual(sorted(b.elements), sorted(e["id"] for e in sample_doc()["elements"]))   # new board = ids unchanged
        self.assertTrue((b.root / "files" / hashlib.sha1(PNG1).hexdigest()).is_file())

    def test_bad_input_is_400_and_board_unchanged(self):
        before = dict(self.store.get(self.bid).elements)
        for body in ({"name": "x.excalidraw", "content": "{not json"}, {"name": "x.excalidraw"}, {"name": "x.txt", "content": "hi"},
                     {"name": "x.excalidraw", "content": self.doc, "mode": "new"},
                     {"name": "x.excalidraw", "content": self.doc, "at": {"x": "a"}},
                     {"name": "x.png", "content": durl(GIF1, "image/png")}):
            code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", keyh(), body)
            self.assertEqual(code, 400, (body.get("name"), d))
        self.assertEqual(self.store.get(self.bid).elements, before)

    def test_merge_is_broadcast_to_other_screens_including_importer(self):
        b = self.store.get(self.bid)
        q = __import__("queue").Queue()
        b.subs.append((q, "screenA"))
        code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", keyh(),
                      {"name": "x.excalidraw", "content": self.doc, "client": "imp-xyz", "at": {"x": 100, "y": 100}})
        self.assertEqual(code, 200, d)
        got, files = [], 0
        while not q.empty():
            m = q.get().decode()
            if m.startswith("event: elements"):
                got += json.loads(m.split("data: ", 1)[1])["elements"]
            elif m.startswith("event: file"):
                files += 1
                self.assertEqual(got, [], "그림이 요소보다 먼저")
        self.assertEqual(files, 1)
        self.assertEqual(sorted(e["id"] for e in got), sorted(b.elements))
        # image data URLs live outside the document (uploaded first via /files) -- nothing missing
        stripped = imp.stripped(imp.parse("x.excalidraw", self.doc.encode()))
        code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", keyh(), {"name": "x.excalidraw", "content": stripped})
        self.assertEqual(d["missing_files"], [])   # already on this board
        code, d = raw(self.port, "POST", "/api/whiteboard/boards/import", keyh(), {"name": "x.excalidraw", "content": stripped})
        self.assertEqual(d["missing_files"], [hashlib.sha1(PNG1).hexdigest()])   # new board -- these must be uploaded


class Cli(unittest.TestCase):
    """export -> merge twice and new-board round trip, against a running and a stopped site, and 30 images > 12 MB -- the real `tempboard` CLI."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        dist = t / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>tb</title>")
        self.t = t
        self.home = t / "home"
        self.env = {**os.environ, "TEMPBOARD_HOME": str(self.home), "TEMPBOARD_DIST": str(dist)}
        out = self.run_cli("start", "--tunnel", "none", "--json", "--title", "시험")
        self.sid = json.loads(out)["id"]

    def tearDown(self):
        subprocess.run([sys.executable, str(CLI), "stop", "--all"], env=self.env, capture_output=True)
        self.tmp.cleanup()

    def run_cli(self, *args, ok=True):
        p = subprocess.run([sys.executable, str(CLI), *args], env=self.env, capture_output=True, text=True, timeout=120)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return p.stdout if ok else p

    def st(self):
        return json.loads((self.home / "sites" / self.sid / "state.json").read_text())

    def board_json(self, bid):
        d = json.loads((self.home / "sites" / self.sid / "data" / "boards" / bid / "board.json").read_text())
        return [e for e in d["elements"] if not e.get("isDeleted")]

    def wait_saved(self, bid, n, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if len(self.board_json(bid)) >= n:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.1)

    def test_export_merge_twice_new_roundtrip_running_and_stopped(self):
        st = self.st()
        port, bid = st["port"], st["board"]
        doc = sample_doc()
        h = keyh(st["edit_key"])
        raw(port, "POST", f"/api/whiteboard/boards/{bid}/files", h, {"client": "t", "id": "oldfileid0001", "mimeType": "image/png", "dataURL": durl(PNG1)})
        code, _ = raw(port, "POST", f"/api/whiteboard/boards/{bid}/elements", h, {"client": "t", "elements": doc["elements"]})
        self.assertEqual(code, 200)
        n = len(doc["elements"])
        self.wait_saved(bid, n)
        out = self.t / "exp"
        self.run_cli("export", self.sid, "--out", str(out))
        f = next(out.glob("*.excalidraw"))
        orig = json.loads(f.read_text())["elements"]
        self.assertEqual(len(orig), n)
        # running site (HTTP) -- merge twice into the same board -> original x 3
        for _ in range(2):
            r = json.loads(self.run_cli("import", self.sid, str(f), "--json"))
            self.assertEqual((r["added"], r["files"]), (n, 1))
        self.wait_saved(bid, n * 3)
        els = self.board_json(bid)
        self.assertEqual(len(els), n * 3)
        self.assertEqual(len({e["id"] for e in els}), n * 3)
        ids0 = {e["id"] for e in orig}
        rest = sorted((e for e in els if e["id"] not in ids0), key=lambda e: e["index"])   # insertion order = index order
        self.assertEqual(len(rest), 2 * n)
        self.assertEqual(refs_ok(rest[:n]), [])
        self.assertEqual(refs_ok(rest[n:]), [])
        # --new on a running site -> appears immediately in the board list (the GET /boards the open page reads)
        t0 = time.time()
        r3 = json.loads(self.run_cli("import", self.sid, str(f), "--new", "--title", "지난주", "--json"))
        code, bl = raw(port, "GET", "/api/whiteboard/boards", h)
        self.assertIn(r3["board"], [b["id"] for b in bl])
        self.assertIn("지난주", [b["title"] for b in bl])
        self.assertLess(time.time() - t0, 5)
        self.wait_saved(r3["board"], n)
        key = lambda e: (e["id"], e.get("text"), round(e["x"], 3), round(e["y"], 3))
        self.assertEqual(sorted(map(key, self.board_json(r3["board"]))), sorted(map(key, orig)))
        # after closing: exporting again gives the same element set (id, text, coordinates)
        self.run_cli("stop", self.sid)
        out3 = self.t / "exp2"
        self.run_cli("export", self.sid, "--out", str(out3))
        again = json.loads(next(p for p in out3.glob("*.excalidraw") if r3["board"] in p.name).read_text())["elements"]
        self.assertEqual(sorted(map(key, again)), sorted(map(key, orig)))
        # stopped site (direct repository access) -- merge and --new
        r4 = json.loads(self.run_cli("import", self.sid, str(f), "--json"))
        self.assertEqual(r4["added"], n)
        self.assertEqual(len(self.board_json(bid)), n * 4)
        r5 = json.loads(self.run_cli("import", self.sid, str(f), "--new", "--json"))
        self.assertEqual(len(self.board_json(r5["board"])), n)
        meta = json.loads((self.home / "sites" / self.sid / "data" / "boards" / r5["board"] / "board.json").read_text())["meta"]
        self.assertEqual(meta["title"], f.name[:-len(".excalidraw")][:80])
        self.assertTrue((self.home / "sites" / self.sid / "data" / "boards" / r5["board"] / "files" / hashlib.sha1(PNG1).hexdigest()).is_file())

    def test_thirty_images_over_12mb(self):
        """A .excalidraw holding 30 images (total > 12 MB) is also uploaded in chunks and succeeds (running site, HTTP)."""
        files, els = {}, []
        for i in range(30):
            raw_img = PNG1[:16] + os.urandom(450_000)   # PNG header + random bytes (only the header is inspected)
            fid = "f%07d" % i
            files[fid] = {"id": fid, "mimeType": "image/png", "dataURL": durl(raw_img)}
            els.append(ak.make_image(fid, 100, 100, (i % 6) * 120, (i // 6) * 120, id="im%d" % i))
        doc = {"type": "excalidraw", "version": 2, "elements": els, "files": files}
        f = self.t / "big.excalidraw"
        f.write_text(json.dumps(doc))
        self.assertGreater(sum(len(v["dataURL"]) for v in files.values()), 12 * 1024 * 1024)
        r = json.loads(self.run_cli("import", self.sid, str(f), "--json"))
        self.assertEqual((r["added"], r["files"], r["missing_files"]), (30, 30, []))
        bid = self.st()["board"]
        self.assertEqual(len(list((self.home / "sites" / self.sid / "data" / "boards" / bid / "files").iterdir())), 30)
        r = json.loads(self.run_cli("import", self.sid, str(f), "--new", "--json"))
        self.assertEqual((r["added"], r["files"]), (30, 30))
        self.assertEqual(len(list((self.home / "sites" / self.sid / "data" / "boards" / r["board"] / "files").iterdir())), 30)

    def test_cli_rejects(self):
        p = self.run_cli("import", self.sid, "/etc/passwd", ok=False)
        self.assertNotEqual(p.returncode, 0)
        p = self.run_cli("import", self.sid, str(self.t / "nope.excalidraw"), ok=False)
        self.assertNotEqual(p.returncode, 0)
        p = self.run_cli("import", self.sid, "x.excalidraw", "--title", "t", ok=False)
        self.assertNotEqual(p.returncode, 0)


class Mcp(unittest.TestCase):
    """board_import: path escape and disallowed extensions give isError; .excalidraw returns {board, added, skipped}."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        dist = t / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>tb</title>")
        self.t = t
        self.env = {**os.environ, "TEMPBOARD_HOME": str(t / "home"), "TEMPBOARD_DIST": str(dist)}
        for k in ("TEMPBOARD_URL", "TEMPBOARD_TOKEN", "TEMPBOARD_AGENT_NAME"):
            self.env.pop(k, None)
        self.p = subprocess.Popen([sys.executable, str(CLI), "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=self.env, cwd=str(t))
        self.n = 0

    def tearDown(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:   # noqa: BLE001
            self.p.kill()
        self.p.stdout.close()
        subprocess.run([sys.executable, str(CLI), "stop", "--all"], env=self.env, capture_output=True)
        self.tmp.cleanup()

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n").encode())
        self.p.stdin.flush()
        while True:
            m = json.loads(self.p.stdout.readline())
            if m.get("id") == self.n:
                return m

    def tool(self, name, args):
        return self.call("tools/call", {"name": name, "arguments": args})["result"]

    def test_board_import(self):
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "1"}})
        self.p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.p.stdin.flush()
        tools = {t["name"]: t for t in self.call("tools/list")["result"]["tools"]}
        self.assertIn("board_import", tools)
        self.assertEqual(tools["board_import"]["inputSchema"]["required"], ["path"])
        r = self.tool("site_open", {"tunnel": "none"})
        self.assertFalse(r.get("isError"), r)
        f = self.t / "w.excalidraw"
        f.write_text(json.dumps(sample_doc()))
        (self.t / "n.txt").write_text("x")
        for bad in ("../../etc/passwd", "/etc/passwd", str(self.t / "n.txt"), str(self.t / "missing.excalidraw"), "https://example.com/a.excalidraw"):
            r = self.tool("board_import", {"path": bad})
            self.assertTrue(r.get("isError"), (bad, r))
        r = self.tool("board_import", {"path": str(f), "mode": "bogus"})
        self.assertTrue(r.get("isError"))
        r = self.tool("board_import", {"path": str(f)})
        self.assertFalse(r.get("isError"), r)
        sc = r["structuredContent"]
        self.assertEqual(sc["added"], len(sample_doc()["elements"]))
        self.assertEqual(sc["skipped"], {})
        self.assertTrue(sc["board"])
        r = self.tool("board_import", {"path": str(f), "mode": "new", "title": "새로"})
        self.assertFalse(r.get("isError"), r)
        self.assertNotEqual(r["structuredContent"]["board"], sc["board"])
        bl = self.tool("board_list", {})["structuredContent"]["boards"]
        self.assertIn("새로", [b["title"] for b in bl])
        # a single image file
        g = self.t / "p.png"
        g.write_bytes(PNG1)
        r = self.tool("board_import", {"path": str(g), "at": {"x": 0, "y": 0}})
        self.assertFalse(r.get("isError"), r)
        self.assertEqual((r["structuredContent"]["added"], r["structuredContent"]["files"]), (1, 1))


# ───────────────────────────── edge cases
def scene_doc(eid="embedded1"):
    return {"type": "excalidraw", "version": 2, "source": "x", "elements": ak.make_box("rectangle", "품은 장면", 0, 0, id=eid), "files": {}}


def excal_payload(scene, compress=True):
    """Excalidraw encode({text, compress}) -- {version:"1", encoding:"bstring", compressed, encoded (byte string)}."""
    text = json.dumps(scene, ensure_ascii=False).encode("utf-8")
    raw = zlib.compress(text) if compress else text
    return json.dumps({"version": "1", "encoding": "bstring", "compressed": compress, "encoded": raw.decode("latin-1")})


def png_with_scene(scene, keyword=imp.SCENE_MIME):
    """Insert a PNG tEXt chunk (keyword, payload) before IEND (the same place as Excalidraw's encodePngMetadata)."""
    data = (keyword + "\0" + excal_payload(scene)).encode("latin-1")
    chunk = struct.pack(">I", len(data)) + b"tEXt" + data + struct.pack(">I", zlib.crc32(b"tEXt" + data) & 0xFFFFFFFF)
    return PNG1[:-12] + chunk + PNG1[-12:]


def svg_with_scene(scene, version=2):
    if version == 2:
        b64 = base64.b64encode(excal_payload(scene).encode("latin-1")).decode()
    else:   # version 1 style: the scene JSON as-is
        b64 = base64.b64encode(json.dumps(scene).encode("utf-8")).decode()
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><!-- svg-source:excalidraw --><metadata>'
            f'<!-- payload-type:{imp.SCENE_MIME} --><!-- payload-version:{version} --><!-- payload-start -->{b64}<!-- payload-end -->'
            '</metadata><rect width="100" height="50"/></svg>').encode()


class R1Embedded(unittest.TestCase):
    """A PNG or SVG that embeds a scene imports that scene rather than an image (as Excalidraw does). An ordinary image stays an image."""

    def test_png_svg_with_scene(self):
        for name, data in (("d.excalidraw.png", png_with_scene(scene_doc())), ("d.excalidraw.svg", svg_with_scene(scene_doc(), 2)),
                           ("d.svg", svg_with_scene(scene_doc(), 1))):
            self.assertTrue(imp.has_embedded_scene(data), name)
            p = imp.parse(name, data)
            self.assertEqual(p["kind"], "embedded", name)
            self.assertEqual(sorted(e["id"] for e in p["elements"]), ["embedded1", "embedded1-label"], name)
            self.assertEqual(p["title"], "d", name)
        for name, data in (("a.png", PNG1), ("a.svg", SVG1), ("b.png", png_with_scene(scene_doc(), keyword="Comment"))):
            self.assertFalse(imp.has_embedded_scene(data), name)
            self.assertEqual(imp.parse(name, data)["kind"], "image", name)
        bad = svg_with_scene(scene_doc()).replace(b"payload-start -->", b"payload-start -->!!!")
        with self.assertRaises(imp.ImportFailed):
            imp.parse("bad.svg", bad)


class R1Robust(unittest.TestCase):
    def test_f1_deep_nesting_is_import_failed(self):
        for data in (("[" * 100000 + "]" * 100000).encode(),
                     ('{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":0,"y":0,"width":1,"height":1,"customData":{"a":%s}}]}'
                      % ("[" * 1500 + "]" * 1500)).encode()):
            with self.assertRaises(imp.ImportFailed):
                imp.parse("a.excalidraw", data)
        # nesting within the limit (500) passes parse but customData and fixedSegments are dropped, so it reaches deepcopy
        el = {"id": "a2", "type": "arrow", "x": 0, "y": 0, "points": [[0, 0], [1, 1]], "elbowed": True,
              "fixedSegments": json.loads("[" * 500 + "]" * 500), "customData": {"a": json.loads("[" * 500 + "]" * 500)}}
        p = imp.parse("a.excalidraw", json.dumps({"type": "excalidraw", "elements": [el]}).encode())
        self.assertIsNone(p["elements"][0]["fixedSegments"])
        self.assertNotIn("customData", p["elements"][0])
        imp.prepare(p["elements"], [], "merge")

    def test_s3_s6_custom_segments_crop(self):
        els = [dict(ak.make_box("rectangle", None, 0, 0, id="c1")[0],
                    customData={"tbBy": "agent:Evil", "latex": "x^2", "size": 28, "color": "#ff0000", "w0": 10, "evil": {"x": 1}, "tbPage": "2"}),
               {"id": "ar", "type": "arrow", "x": 0, "y": 0, "points": [[0, 0], [10, 0]], "elbowed": True,
                "fixedSegments": [{"start": [0, 0], "end": [5, 0], "index": 1}]},
               {"id": "ar2", "type": "arrow", "x": 0, "y": 0, "points": [[0, 0], [10, 0]], "elbowed": True, "fixedSegments": [{"x": 1}]},
               {"id": "im", "type": "image", "x": 0, "y": 0, "width": 5, "height": 5, "fileId": "abcdefgh12", "crop": {"x": 1}},
               {"id": "im2", "type": "image", "x": 0, "y": 0, "width": 5, "height": 5, "fileId": "abcdefgh12",
                "crop": {"x": 0, "y": 0, "width": 5, "height": 5, "naturalWidth": 10, "naturalHeight": 10}}]
        p = {e["id"]: e for e in imp.parse("a.excalidraw", json.dumps({"type": "excalidraw", "elements": els}).encode())["elements"]}
        self.assertEqual(p["c1"]["customData"], {"latex": "x^2", "size": 28.0, "color": "#ff0000", "w0": 10.0})
        self.assertEqual(p["ar"]["fixedSegments"], [{"start": [0.0, 0.0], "end": [5.0, 0.0], "index": 1}])
        self.assertIsNone(p["ar2"]["fixedSegments"])
        self.assertIsNone(p["im"]["crop"])
        self.assertEqual(p["im2"]["crop"]["naturalWidth"], 10.0)
        for bad in ("//evil.example", "/\\evil.example"):
            self.assertIsNone(imp.clean_element(dict(ak.make_box()[0], link=bad))[0]["link"])

    def test_f2_arrow_bound_to_page_frame(self):
        doc = sample_doc()
        page = next(e for e in doc["elements"] if e["type"] == "frame")
        box = next(e for e in doc["elements"] if e["id"] == "boxA")
        doc["elements"] += ak.make_arrow(box, page, "장으로", id="arrToPage")
        ak.assign_indices([], doc["elements"])
        p = imp.parse("a.excalidraw", json.dumps(doc).encode())
        board = [ak.make_frame(page=1, x=0, y=0, w=800, h=450)]
        for _ in range(2):
            items = imp.prepare(p["elements"], board, "merge")
            self.assertEqual(refs_ok(items), [])
            arr = next(e for e in items if e["type"] == "arrow" and e["endBinding"] and e["endBinding"]["elementId"].startswith("tbpage-"))
            fr = next(e for e in items if e["id"] == arr["endBinding"]["elementId"])
            self.assertIn(arr["id"], [b["id"] for b in fr["boundElements"]])
            board += items

    def test_s2_pages_not_placed_over_existing_pages(self):
        p = imp.parse("a.excalidraw", json.dumps(sample_doc()).encode())
        board = [ak.make_frame(page=1, x=0, y=0, w=1600, h=900)]
        items = imp.prepare(p["elements"], board, "merge", {"x": 800, "y": 450})   # screen centre = the centre of the existing page
        mine = ak.scene_bbox(items)
        self.assertGreaterEqual(mine[0], 1600 + 80 - 1e-6)
        # a group with no page stays centred as is
        q = imp.parse("b.excalidraw", json.dumps({"type": "excalidraw", "elements": ak.make_box("rectangle", "x", 0, 0)}).encode())
        mine = ak.scene_bbox(imp.prepare(q["elements"], board, "merge", {"x": 800, "y": 450}))
        self.assertAlmostEqual((mine[0] + mine[2]) / 2, 800)

    def test_f3_push_measures_real_body_before_upload(self):
        els = []
        for i in range(1000):   # the content is within 12 MB, but quotes and Korean text push the body past 12 MB
            t = ("\"따옴표\" 한글 " * 260)[: 2600]
            els.append(ak.make_text(t, i * 10, 0, id="t%05d" % i))
        p = imp.parse("a.excalidraw", json.dumps({"type": "excalidraw", "elements": els}, ensure_ascii=False).encode())
        p["files"] = {hashlib.sha1(PNG1).hexdigest(): imp.file_record(PNG1)}
        content = imp.stripped(p)
        body = imp.body_bytes({"name": "a.excalidraw", "content": content})
        calls = []
        req = lambda m, path, b: calls.append(path) or (200, {})
        self.assertLessEqual(len(content.encode()), imp.MAX_BODY, "옛 검사(내용만 잼)는 통과하는 크기")
        if body > imp.MAX_BODY:
            with self.assertRaises(imp.ImportFailed) as cm:
                imp.push(req, p, "merge", "abcdefgh")
            self.assertIn("12 MB", str(cm.exception))
            self.assertEqual(calls, [], "그림을 올리기 전에 거부")
        else:
            self.skipTest(f"본문 {body} 이 상한 안 — 요소 수를 늘린다")


class R1Server(Endpoint):
    def test_f1_new_board_not_left_on_bad_doc(self):
        n0 = len(self.store.list())
        deep = '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":0,"y":0,"width":1,"height":1,"customData":{"a":%s}}]}' % ("[" * 1500 + "]" * 1500)
        for content in (deep, '{"type":"excalidraw","elements":"x"}'):
            code, d = raw(self.port, "POST", "/api/whiteboard/boards/import", keyh(), {"name": "bomb.excalidraw", "content": content})
            self.assertEqual(code, 400, d)
            self.assertIn("error", d)
        self.assertEqual(len(self.store.list()), n0)
        self.assertEqual(len(list((self.store.data / "boards").iterdir())), n0)

    def test_f3_oversize_body_gets_413_with_message(self):
        big = "x" * (srv.MAX_BODY + 1000)
        code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", keyh(), {"name": "a.excalidraw", "content": big})
        self.assertEqual(code, 413)
        self.assertIn("12 MB", d["error"])

    def test_s3_agent_import_stamps_tbby(self):
        code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", bearer(TOK_E), {"name": "x.excalidraw", "content": self.doc})
        self.assertEqual(code, 200, d)
        b = self.store.get(self.bid)
        self.assertTrue(all((e.get("customData") or {}).get("tbBy") == "agent:Claude" for e in b.elements.values()))
        code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", keyh(), {"name": "x.excalidraw", "content": self.doc})
        new = [e for e in b.elements.values() if not (e.get("customData") or {}).get("tbBy")]
        self.assertEqual(len(new), d["added"])

    def test_b1_embedded_png_via_endpoint(self):
        code, d = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/import", keyh(),
                      {"name": "d.excalidraw.png", "content": durl(png_with_scene(scene_doc()))})
        self.assertEqual(code, 200, d)
        self.assertEqual((d["added"], d["kind"]), (2, "embedded"))


class R1CliError(Cli):
    def test_f1_cli_one_line_error(self):
        f = self.t / "bomb.excalidraw"
        f.write_text('{"type":"excalidraw","elements":' + "[" * 5000 + "]" * 5000 + "}")
        n0 = len(list((self.home / "sites" / self.sid / "data" / "boards").iterdir()))
        for args in (("--new",), ()):
            p = self.run_cli("import", self.sid, str(f), *args, ok=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertNotIn("Traceback", p.stderr)
            self.assertEqual(len(p.stderr.strip().splitlines()), 1, p.stderr)
        self.run_cli("stop", self.sid)
        p = self.run_cli("import", self.sid, str(f), "--new", ok=False)
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(len(list((self.home / "sites" / self.sid / "data" / "boards").iterdir())), n0)

    test_export_merge_twice_new_roundtrip_running_and_stopped = None
    test_thirty_images_over_12mb = None
    test_cli_rejects = None



@unittest.skipUnless(PY39.exists(), "/usr/bin/python3 없음")
class Py39(unittest.TestCase):
    def test_import_and_parse_on_39(self):
        code = ("import importlib.util,sys,json;"
                f"spec=importlib.util.spec_from_file_location('tempboard_importers',{str(HERE / 'server' / 'importers.py')!r});"
                "m=importlib.util.module_from_spec(spec);sys.modules['tempboard_importers']=m;spec.loader.exec_module(m);"
                "p=m.parse('a.excalidraw',json.dumps({'type':'excalidraw','elements':[{'id':'a1','type':'rectangle','x':0,'y':0,'width':5,'height':5}]}).encode());"
                "print(len(m.prepare(p['elements'],[],'merge')))")
        p = subprocess.run([str(PY39), "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
