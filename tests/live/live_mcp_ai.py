"""Live test: what is drawn through MCP (`bin/tempboard mcp`) appears on the human's screen immediately, and the human's chip shows 'AI'.

The name (live_*.py) keeps it out of unittest discover. It needs Playwright (Python).
    TEMPBOARD_DIST=<built dist> python3 tests/live/live_mcp_ai.py --shots DIR [--skip-30s]
It opens a site with no tunnel in a temporary TEMPBOARD_HOME (--home, default: a new folder under $TMPDIR) via MCP site_open and closes it when done.
~/.tempboard is never touched. The result goes to stdout as one JSON document; the exit code is 1 if anything failed.

Desktop Chromium human view: elements are visible within 1.5 seconds after board_add, a 'Claude' + 'AI' badge appears in the chip row within 1 second,
and disappears 30 seconds after the last action. On a phone (WebKit iPhone 13) the human window shows 'AI' and the top bar does not overflow (390px screenshot).
Two Chromium human views: changing a box label with board_update changes it on both screens within 1 second. If a human moves the box first,
board_update(base_version=old value) gets conflict and the human's change is kept.
"""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["TEMPBOARD_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "bin" / "tempboard"


class Mcp:
    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, str(CLI), "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env)
        self.q, self.n = queue.Queue(), 0
        threading.Thread(target=lambda: [self.q.put(json.loads(l)) for l in self.p.stdout], daemon=True).start()

    def call(self, method, params=None, timeout=120):
        self.n += 1
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n").encode())
        self.p.stdin.flush()
        while True:
            m = self.q.get(timeout=timeout)
            if m.get("id") == self.n:
                return m

    def tool(self, name, args=None):
        r = self.call("tools/call", {"name": name, "arguments": args or {}})["result"]
        if r.get("isError"):
            raise RuntimeError(f"{name}: {r['content'][0]['text']}")
        return r

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:   # noqa: BLE001
            self.p.kill()


RECTS = """(ids) => { const a = window.__wbapi; const st = a.getAppState(); const z = st.zoom.value;
  const r = (el) => { const b = el && el.getBoundingClientRect(); return b && b.width ? [b.left, b.top, b.right, b.bottom] : null; };
  const els = a.getSceneElements().filter((e) => ids.includes(e.id)).map((e) => [e.id,
    [(e.x + st.scrollX) * z + st.offsetLeft, (e.y + st.scrollY) * z + st.offsetTop, (e.x + e.width + st.scrollX) * z + st.offsetLeft,
     (e.y + e.height + st.scrollY) * z + st.offsetTop]]);
  return {els, ui: [r(document.querySelector('.wb-mtop')), r(document.querySelector('.excalidraw .dropdown-menu-button')),
                    r(document.querySelector('.wb-pages-top'))].filter(Boolean), w: innerWidth}; }"""
OVERLAP = lambda a, b: a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]   # noqa: E731

SCENE = """() => { const a = window.__wbapi; if (!a) return {}; const o = {};
  for (const e of a.getSceneElementsIncludingDeleted()) o[e.id] = {d: !!e.isDeleted, t: e.originalText || e.text || null, x: e.x, v: e.version, w: e.width, h: e.height};
  return o; }"""


def wait_until(fn, limit, step=0.02):
    t0 = time.time()
    while time.time() - t0 < limit:
        v = fn()
        if v:
            return time.time() - t0
        time.sleep(step)
    return None


def run(args) -> dict:
    from playwright.sync_api import sync_playwright

    rep = {"checks": {}, "timings": {}, "errors": []}
    T0 = time.time()
    ck, tm = rep["checks"], rep["timings"]
    home = Path(args.home) if args.home else Path(tempfile.mkdtemp(prefix="tbmcp."))
    env = {**os.environ, "TEMPBOARD_HOME": str(home)}
    env.pop("TEMPBOARD_URL", None)
    env.pop("TEMPBOARD_TOKEN", None)
    env.pop("TEMPBOARD_AGENT_NAME", None)
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    m = Mcp(env)
    sid = None
    try:
        m.call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "2"}})
        o = m.tool("site_open", {"tunnel": "none", "title": "AI 시험"})["structuredContent"]
        sid, url = o["site"], o["links"]["edit"]
        with sync_playwright() as p:
            cb = p.chromium.launch()
            wk = p.webkit.launch()

            def person(browser, name, **ctx):
                pg = browser.new_context(**ctx).new_page()
                named = [False]   # a /me 401 before the name is entered is expected (the name screen) -- only errors after that are counted

                def err(msg):
                    # WebKit screenshot noise: the CSP (style-src 'self') blocks the <style> that Playwright injects when taking a screenshot --
                    # not the app's fault (same on the base branch). It is only counted separately.
                    if "Refused to apply a stylesheet" in msg:
                        rep.setdefault("screenshot_csp_noise", 0)
                        rep["screenshot_csp_noise"] += 1
                        return
                    if named[0] or "401" not in msg:
                        rep["errors"].append(f"{name} t+{time.time() - T0:.1f}s {msg}")
                pg.on("pageerror", lambda e: err(f"pageerror: {str(e)[:200]}"))
                pg.on("console", lambda msg: err(f"console: {msg.text[:200]}") if msg.type == "error" else None)
                pg.goto(url)
                pg.fill("input[aria-label='이름']", name)
                pg.click("button.wb-primary")
                named[0] = True
                pg.wait_for_selector(".excalidraw canvas", timeout=30000)
                return pg
            A = person(cb, "사람A", viewport={"width": 1280, "height": 800})
            B = person(cb, "사람B", viewport={"width": 1280, "height": 800})
            C = person(wk, "폰", **p.devices["iPhone 13"])   # open an empty board on the phone first (the order that reproduced the bug)
            time.sleep(1.5)

            # draw -> visible on screen within 1.5 s, AI chip within 1 s
            t0 = time.time()
            add = m.tool("board_add", {"items": [{"kind": "box", "ref": "a", "label": "입력"}, {"kind": "box", "ref": "b", "label": "판단"},
                                                 {"kind": "arrow", "from": "a", "to": "b", "label": "예"}]})["structuredContent"]
            t_call = time.time() - t0
            ids = add["ids"]
            seen = wait_until(lambda: all(i in A.evaluate(SCENE) for i in ids), 5)
            chip = wait_until(lambda: A.locator(".wb-people .wb-chip-btn:not([tabindex='-1']) .wb-ai").count() > 0, 5)
            tm["board_add_call_s"] = round(t_call, 3)
            tm["visible_after_add_s"] = None if seen is None else round(seen, 3)
            tm["ai_chip_after_add_s"] = None if chip is None else round(chip, 3)
            ck["2_visible_within_1.5s"] = seen is not None and seen <= 1.5
            ck["2_ai_chip_within_1s"] = chip is not None and chip <= 1.0
            txt = A.locator(".wb-people .wb-chip-btn:not([tabindex='-1'])").all_inner_texts()
            rep["chips"] = txt
            ck["2_chip_text_is_claude_ai"] = any("Claude" in t and "AI" in t for t in txt)
            # the first group on an empty board must not overlap the desktop menu button and '+ new page'
            ra = A.evaluate(RECTS, ids)
            ck["U1a_desktop_no_overlap_with_top_ui"] = bool(ra["ui"]) and not any(OVERLAP(r, u) for _, r in ra["els"] for u in ra["ui"])
            ck["2_finite_sizes"] = all(isinstance(v["w"], (int, float)) and isinstance(v["h"], (int, float)) for v in A.evaluate(SCENE).values())
            if shots:
                A.screenshot(path=str(shots / "desktop-1280-ai-chip.png"))
                A.set_viewport_size({"width": 1024, "height": 768})
                time.sleep(0.4)
                A.screenshot(path=str(shots / "desktop-1024-ai-chip.png"))
                A.set_viewport_size({"width": 1280, "height": 800})

            # phone: AI in the human window, no overflow of the top bar
            time.sleep(1.0)
            # phone (390px): the first box and its label are fully visible below the top button row
            first = [add["created"]["a"], add["created"]["a"] + "-label"]
            rc = C.evaluate(RECTS, first)
            bar = rc["ui"][0] if rc["ui"] else None
            rep["phone_first_box"] = {"bar": bar, "els": rc["els"]}
            ck["U1a_phone_first_box_below_bar"] = bool(bar) and len(rc["els"]) == 2 and all(
                r[1] >= bar[3] and r[0] >= 0 and r[2] <= rc["w"] for _, r in rc["els"])
            if shots:
                C.screenshot(path=str(shots / "phone-390-after-add.png"))
            m.tool("board_update", {"changes": [{"id": add["created"]["b"], "dx": 0}]})   # send the cursor again so the AI stays alive
            C.click("button.wb-mchip-btn[aria-label^='사람 · 따라가기']")
            ai_phone = wait_until(lambda: C.locator(".wb-follow-menu .wb-ai").count() > 0, 3)
            ck["2_phone_ai_in_people_sheet"] = ai_phone is not None
            rep["phone_rows"] = C.locator(".wb-follow-menu .wb-follow-row").all_inner_texts()
            rep["phone_title"] = C.locator(".wb-follow-menu .wb-follow-title").inner_text()
            ck["U1c_people_title_counts_ai"] = rep["phone_title"] == "접속한 사람 3명 · AI 1"
            # a screen reader reads the AI row in the human window as 'AI agent' (the badge is aria-hidden, with hidden text)
            sr = C.locator(".wb-follow-row .wb-sr-only").all_inner_texts()
            ck["r3_people_row_ai_for_screen_reader"] = any("AI 에이전트" in t for t in sr)
            ov = C.evaluate("""() => { const t = document.querySelector('.wb-mtop'); return {doc: document.documentElement.scrollWidth,
                 win: innerWidth, bar: t ? t.scrollWidth : 0, barw: t ? t.clientWidth : 0}; }""")
            rep["phone_overflow"] = ov
            ck["2_phone_no_overflow"] = ov["doc"] <= ov["win"] and ov["bar"] <= ov["barw"] + 1
            if shots:
                C.screenshot(path=str(shots / "phone-390-people-sheet.png"))
            C.keyboard.press("Escape")

            # changing the label within 1 s on both human screens
            a_id = add["created"]["a"]
            lab = a_id + "-label"
            read = m.tool("board_read", {})["structuredContent"]
            item = next(i for i in read["items"] if i["id"] == a_id)
            t0 = time.time()
            r = m.tool("board_update", {"changes": [{"id": a_id, "base_version": item["version"], "label_base_version": item["label_version"],
                                                     "text": "바뀐 라벨"}]})["structuredContent"]
            t_last = time.time()   # the agent's last presence (cursor) -- the later conflict does not send a cursor
            ok = r["results"][0]["status"] == "ok"
            both = wait_until(lambda: A.evaluate(SCENE).get(lab, {}).get("t") == "바뀐 라벨" and B.evaluate(SCENE).get(lab, {}).get("t") == "바뀐 라벨", 5)
            tm["update_both_screens_s"] = None if both is None else round(both, 3)
            ck["3_update_both_within_1s"] = ok and both is not None and both <= 1.0

            # human A moves the box first (screen -> server) -> the agent's old base_version gets conflict
            v_before = A.evaluate(SCENE)[a_id]["v"]
            A.evaluate("""(id) => { const a = window.__wbapi; const els = a.getSceneElementsIncludingDeleted().map((e) =>
                e.id === id ? {...e, x: e.x + 333, version: e.version + 1, versionNonce: 7, updated: Date.now()} : e);
                a.updateScene({elements: els, captureUpdate: 'IMMEDIATELY'}); }""", a_id)
            hx = A.evaluate(SCENE)[a_id]["x"]
            synced = wait_until(lambda: abs(B.evaluate(SCENE)[a_id]["x"] - hx) < 0.01, 5)
            ck["3_human_move_synced"] = synced is not None
            lv = A.evaluate(SCENE)[lab]["v"]
            r = m.tool("board_update", {"changes": [{"id": a_id, "base_version": v_before, "label_base_version": lv,
                                                     "text": "AI 가 덮으려 함", "x": -500}]})
            res = r["structuredContent"]["results"][0]
            ck["3_conflict_reported"] = res["status"] == "conflict"
            time.sleep(1.0)
            sa, sb = A.evaluate(SCENE), B.evaluate(SCENE)
            ck["3_human_change_kept"] = abs(sa[a_id]["x"] - hx) < 0.01 and abs(sb[a_id]["x"] - hx) < 0.01 and sa[lab]["t"] == "바뀐 라벨"
            if shots:
                B.screenshot(path=str(shots / "desktop-B-after-conflict.png"))

            # the AI chip disappears 30 seconds after the last action
            if not args.skip_30s:
                gone = wait_until(lambda: A.locator(".wb-people .wb-chip-btn:not([tabindex='-1']) .wb-ai").count() == 0, 45, 0.25)
                tm["ai_chip_gone_after_last_action_s"] = None if gone is None else round(time.time() - t_last, 2)
                ck["2_ai_chip_gone_after_30s"] = gone is not None and 29 <= gone <= 33
            ck["no_console_errors"] = not rep["errors"]
            cb.close()
            wk.close()
    finally:
        try:
            if sid:
                m.tool("site_close", {"site": sid})
        except Exception as e:   # noqa: BLE001
            rep["errors"].append(f"close: {e}")
        m.close()
        if not args.home:
            shutil.rmtree(home, ignore_errors=True)
    rep["ok"] = all(ck.values())
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=None)
    ap.add_argument("--shots", default=None)
    ap.add_argument("--skip-30s", action="store_true")
    a = ap.parse_args()
    rep = run(a)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    sys.exit(0 if rep["ok"] else 1)


if __name__ == "__main__":
    main()
