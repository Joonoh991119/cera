"""Live test: when elements made by the element factory (server/agentkit.py) are sent over HTTP to an open board,
do they appear on the human's screen immediately, without breaking, above the existing drawings (z-order), survive a reload, and does an arrow follow when its box is dragged?

The name (live_*.py) keeps it out of unittest discover. It needs Playwright (Python).
    python3 tests/live/live_agent_write.py                    # Chromium, checks 3-5
    python3 tests/live/live_agent_write.py --browser webkit --only-live   # WebKit, check 3
    python3 tests/live/live_agent_write.py --url 'http://127.0.0.1:P/?k=KEY#b=BOARD'   # against an already open board
Without --url, it opens a board with no tunnel in a temporary TEMPBOARD_HOME (--home, default: a new folder under $TMPDIR) and closes it when done.
~/.tempboard is never touched. The result goes to stdout as one JSON document; the exit code is 1 if anything failed.
"""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["TEMPBOARD_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import argparse
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tb_agentkit_live", ROOT / "server" / "agentkit.py")
ak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ak)

VISIBLE_LIMIT = 1.5   # VISIBLE_LIMIT = 1.5   # seconds -- check 3


class Board:
    def __init__(self, url: str):
        self.url = url
        base, rest = url.split("/?k=", 1)
        self.base = base
        self.key, _, self.bid = rest.partition("#b=")
        if not self.bid:
            self.bid = self.get("/api/whiteboard/boards")[0]["id"]

    def _req(self, path, data=None, method="GET"):
        h = {"X-Whiteboard": "1", "X-Tb-Key": self.key, "X-Tb-Name": urllib.parse.quote("에이전트 시험")}
        if data is not None:
            h["Content-Type"] = "application/json"
            data = json.dumps(data).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())

    def get(self, path):
        return self._req(path)

    def elements(self):
        d = self.get("/api/whiteboard/boards/%s" % self.bid)
        return d.get("elements", d.get("board", {}).get("elements", []))

    def post(self, els):
        return self._req("/api/whiteboard/boards/%s/elements" % self.bid, {"client": "agent-live", "elements": els}, "POST")


def open_site(home: Path):
    env = dict(os.environ, TEMPBOARD_HOME=str(home), TEMPBOARD_DIST=str(ROOT / "web" / "dist"))
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "tempboard"), "start", "--tunnel", "none", "--ttl", "1h",
                          "--title", "에이전트 쓰기 시험", "--json"], env=env, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise SystemExit("보드를 못 열었다: " + out.stderr[-800:])
    st = json.loads(out.stdout)
    ln = st["links"]
    return st["id"], ln.get("local") or ln["edit"], env


def close_site(sid, env):
    subprocess.run([sys.executable, str(ROOT / "bin" / "tempboard"), "stop", sid], env=env, capture_output=True, timeout=60)


JS_SCENE = """() => window.__wbapi.getSceneElements().map(e => ({id: e.id, type: e.type, x: e.x, y: e.y, w: e.width,
  h: e.height, index: e.index, text: e.type === 'text' ? e.text : null, containerId: e.containerId || null,
  points: e.points || null, version: e.version}))"""

# actual text width inside the box (measured with the on-screen font) -- via canvas measureText once the font is loaded
JS_MEASURE = """async (ids) => {
  const api = window.__wbapi, els = api.getSceneElements(), byId = new Map(els.map(e => [e.id, e]));
  const fam = (id) => ({1: 'Virgil', 2: 'Helvetica', 3: 'Cascadia', 5: 'Excalifont', 6: 'Nunito', 7: 'Lilita One',
                        8: 'Comic Shanns', 9: 'Liberation Sans'})[id] || 'Nunito';
  const ctx = document.createElement('canvas').getContext('2d'), out = [];
  for (const id of ids) {
    const t = byId.get(id); if (!t) { out.push({id, missing: true}); continue; }
    const font = `${t.fontSize}px "${fam(t.fontFamily)}", "Segoe UI Emoji"`;
    try { await document.fonts.load(font, t.text); } catch (_) {}
    ctx.font = font;
    const real = Math.max(...t.text.split('\\n').map(l => ctx.measureText(l).width));
    const c = t.containerId ? byId.get(t.containerId) : null;
    out.push({id, text: t.text, est: t.width, real, box: c ? {type: c.type, x: c.x, y: c.y, w: c.width, h: c.height} : null,
              el: {x: t.x, y: t.y, w: t.width, h: t.height}});
  }
  return out;
}"""


def inner_width(box):
    return ak._inner_max_width(box["w"], box["type"])


def run(args) -> dict:
    from playwright.sync_api import sync_playwright

    report = {"browser": args.browser, "checks": {}, "notes": []}
    checks = report["checks"]
    home = sid = env = None
    if args.url:
        url = args.url
    else:
        home = Path(args.home) if args.home else Path(tempfile.mkdtemp(prefix="tblive."))
        home.mkdir(parents=True, exist_ok=True)
        sid, url, env = open_site(home)
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    board = Board(url)
    try:
        with sync_playwright() as p:
            br = getattr(p, args.browser).launch()
            pg = br.new_context(viewport={"width": 1280, "height": 800}).new_page()
            errs = []
            t_start = time.time()
            stamp = lambda: "t+%.2fs " % (time.time() - t_start)
            pg.on("pageerror", lambda e: errs.append(stamp() + "pageerror: " + str(e)[:300]))
            pg.on("console", lambda m: errs.append(stamp() + "console.error: " + m.text[:300]) if m.type == "error" else None)
            bad = []
            pg.on("response", lambda r: bad.append("%d %s" % (r.status, r.url.split("?")[0])) if r.status >= 400 else None)
            pg.goto(url)
            pg.fill("input[aria-label='이름']", "사람")
            pg.click("button.wb-primary")
            pg.wait_for_selector(".excalidraw canvas", timeout=30000)
            time.sleep(1.5)

            # drawn by a human first -- when added on screen, Excalidraw assigns the index and sends it to the server
            human = ak.make_box("rectangle", None, -400, -300, 200, 120, fill="#ffc9c9", id="humanbox01")[0]
            human["index"] = None
            pg.evaluate("""(el) => { const a = window.__wbapi;
                a.updateScene({elements: [...a.getSceneElementsIncludingDeleted(), el], captureUpdate: 'IMMEDIATELY'}); }""", human)
            t0 = time.time()
            while time.time() - t0 < 5 and not any(e["id"] == "humanbox01" for e in board.elements()):
                time.sleep(0.2)
            existing = board.elements()
            report["human_index_on_server"] = next((e.get("index") for e in existing if e["id"] == "humanbox01"), "missing")

            # the agent's part -- 2 boxes (with labels), an arrow (with a label), and free text inside one frame
            fr = ak.make_frame("에이전트 틀", 0, 0, 1000, 900, by="Claude")
            a = ak.make_box("rectangle", "입력 데이터 (input)", 40, 60, fill="#a5d8ff", by="Claude", frame_id=fr["id"])
            b = ak.make_box("ellipse", "판단 단계", 460, 60, by="Claude", frame_id=fr["id"])
            arr = ak.make_arrow(a[0], b[0], "예", by="Claude", frame_id=fr["id"])
            note = ak.make_text("에이전트 메모 — note 123", 40, 300, by="Claude", frame_id=fr["id"])
            # long labels that wrap, a label containing '->', a diamond, and a fixed-width free text (regression cases)
            c = ak.make_box("diamond", "예 / 아니오?", 40, 420, by="Claude", frame_id=fr["id"])
            d = ak.make_box("rectangle", "수집 → 정리 → 분석 → 검토 → 보고 → 공유 → 보관 → 폐기", 360, 420, by="Claude",
                            frame_id=fr["id"])
            f = ak.make_box("rectangle", "A→B→C→D→E→F→G→H→I→J→K→L→M→N", 40, 680, by="Claude", frame_id=fr["id"])
            wrapped = ak.make_text("one two three four five six seven", 700, 300, max_width=80, by="Claude",
                                   frame_id=fr["id"])
            items = ak.place(existing, [fr] + a + b + arr + [note] + c + d + f + [wrapped], at="right")
            new_ids = [e["id"] for e in items]
            n_pre = len(errs)
            report["agent_write_at"] = stamp().strip()
            report["errors_before_agent_write"] = list(errs)   # a 401 etc. before the name is entered -- not what this test is about
            report["http_errors_before_agent_write"] = list(bad)
            r = board.post(items)
            t_post = time.time()
            seen_at = None
            while time.time() - t_post < 5:
                ids = set(pg.evaluate("() => window.__wbapi.getSceneElements().map(e => e.id)"))
                if all(i in ids for i in new_ids):
                    seen_at = time.time() - t_post
                    break
                time.sleep(0.05)
            report["post"] = {"accepted": r.get("accepted"), "n": len(items)}
            report["visible_after_s"] = None if seen_at is None else round(seen_at, 3)
            checks["3_visible_within_1.5s"] = seen_at is not None and seen_at <= VISIBLE_LIMIT
            time.sleep(0.6)
            scene = pg.evaluate(JS_SCENE)
            byid = {e["id"]: e for e in scene}
            checks["3_finite_sizes"] = all(isinstance(byid[i]["w"], (int, float)) and math.isfinite(byid[i]["w"])
                                           and math.isfinite(byid[i]["h"]) for i in new_ids if i in byid)
            order = [e["id"] for e in scene]
            pos_h = order.index("humanbox01") if "humanbox01" in order else -1
            checks["3_new_above_existing_z"] = pos_h >= 0 and all(order.index(i) > pos_h for i in new_ids if i in order)
            report["scene_order"] = [(e["id"][:10], e["index"]) for e in scene]
            pg.evaluate("window.__wbapi.scrollToContent()")
            time.sleep(0.6)
            if shots:
                pg.screenshot(path=str(shots / ("%s-1-live.png" % args.browser)))
            checks["3_console_errors_0"] = len(errs) == n_pre
            report["errors_after_agent_write"] = errs[n_pre:]

            labels = [a[1]["id"], b[1]["id"], c[1]["id"], d[1]["id"], f[1]["id"]]
            meas = pg.evaluate(JS_MEASURE, labels + [arr[1]["id"], note["id"], wrapped["id"]])
            report["text_measure"] = [{"text": m.get("text"), "est": m.get("est"), "real": round(m.get("real") or 0, 1),
                                       "ratio_real_over_est": round((m.get("real") or 0) / max(m.get("est") or 1, 1), 3)}
                                      for m in meas]
            if args.only_live:
                return report

            def label_fits():
                ms = pg.evaluate(JS_MEASURE, labels)
                ok = True
                for m in ms:
                    bx, el = m["box"], m["el"]
                    inside = (el["x"] >= bx["x"] - .5 and el["y"] >= bx["y"] - .5 and el["x"] + el["w"] <= bx["x"] + bx["w"] + .5
                              and el["y"] + el["h"] <= bx["y"] + bx["h"] + .5)
                    glyphs = m["real"] <= inner_width(bx) + .5
                    ok = ok and inside and glyphs
                return ok, ms

            # check 5 -- dragging box A moves the arrow's start along with it
            fits_before, ms_before = label_fits()
            report["label_fit_before_drag"] = [{"text": m["text"], "real": round(m["real"], 1), "inner": inner_width(m["box"]), "shape": m["box"]["type"]} for m in ms_before]
            pg.evaluate("() => window.__wbapi.setActiveTool({type: 'selection'})")
            time.sleep(0.3)
            st = pg.evaluate("""() => { const s = window.__wbapi.getAppState();
                return {sx: s.scrollX, sy: s.scrollY, z: s.zoom.value, ol: s.offsetLeft, ot: s.offsetTop}; }""")
            A = byid[a[0]["id"]]
            cx, cy = A["x"] + A["w"] / 2, A["y"] + 12   # grab a painted spot on the upper part of the box, avoiding the label
            px, py = (cx + st["sx"]) * st["z"] + st["ol"], (cy + st["sy"]) * st["z"] + st["ot"]
            arr_before = byid[arr[0]["id"]]
            DX, DY = 0, 160   # screen pixels
            pg.mouse.move(px, py)
            pg.mouse.down()
            for k in range(1, 11):
                pg.mouse.move(px + DX * k / 10, py + DY * k / 10)
                time.sleep(0.03)
            pg.mouse.up()
            time.sleep(0.8)
            scene2 = {e["id"]: e for e in pg.evaluate(JS_SCENE)}
            A2, ar2 = scene2[a[0]["id"]], scene2[arr[0]["id"]]
            moved = (round(A2["x"] - A["x"], 1), round(A2["y"] - A["y"], 1))
            sx, sy = ar2["x"] + ar2["points"][0][0], ar2["y"] + ar2["points"][0][1]
            near = (A2["x"] - 20 <= sx <= A2["x"] + A2["w"] + 20) and (A2["y"] - 20 <= sy <= A2["y"] + A2["h"] + 20)
            report["drag"] = {"box_moved": moved, "arrow_start_before": [round(arr_before["x"], 1), round(arr_before["y"], 1)],
                              "arrow_start_after": [round(sx, 1), round(sy, 1)], "arrow_start_near_box": near}
            checks["5_box_moved"] = abs(moved[1]) > 50
            checks["5_arrow_follows_box"] = checks["5_box_moved"] and near and abs(sy - arr_before["y"]) > 50
            fits_after, ms_after = label_fits()
            checks["5_label_inside_box"] = fits_before and fits_after
            report["label_fit_after_drag"] = ms_after
            if shots:
                pg.screenshot(path=str(shots / ("%s-2-dragged.png" % args.browser)))
            time.sleep(1.0)   # lets the drag result reach the server

            # check 4 -- after a reload, the same element count and the same text
            before = {e["id"]: e for e in pg.evaluate(JS_SCENE)}
            n0 = len(errs)
            pg.reload()
            pg.wait_for_selector(".excalidraw canvas", timeout=30000)
            time.sleep(2.0)
            after = {e["id"]: e for e in pg.evaluate(JS_SCENE)}
            checks["4_same_count_after_reload"] = len(before) == len(after) and set(before) == set(after)
            wr = pg.evaluate("(id) => { const e = window.__wbapi.getSceneElements().find(x => x.id === id);"
                             " return e && {autoResize: e.autoResize, w: e.width, text: e.text}; }", wrapped["id"])
            report["wrapped_after_reload"] = wr
            checks["4_wrapped_text_kept"] = bool(wr) and wr["autoResize"] is False and wr["text"] == wrapped["text"]
            checks["4_same_text_after_reload"] = all(after.get(i, {}).get("text") == before[i]["text"] for i in before
                                                     if before[i]["type"] == "text")
            checks["4_reload_errors_0"] = len(errs) == n0
            report["reload"] = {"before": len(before), "after": len(after)}
            if shots:
                pg.evaluate("window.__wbapi.scrollToContent()")
                time.sleep(0.5)
                pg.screenshot(path=str(shots / ("%s-3-reloaded.png" % args.browser)))
            br.close()
    finally:
        if sid:
            close_site(sid, env)
            if home and not args.home and home.name.startswith("tblive."):
                shutil.rmtree(home, ignore_errors=True)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url")
    ap.add_argument("--home")
    ap.add_argument("--browser", choices=["chromium", "webkit", "firefox"], default="chromium")
    ap.add_argument("--only-live", action="store_true", help="기준 3(보이기)만")
    ap.add_argument("--shots")
    args = ap.parse_args()
    rep = run(args)
    rep["ok"] = all(rep["checks"].values())
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    sys.exit(0 if rep["ok"] else 1)


if __name__ == "__main__":
    main()
