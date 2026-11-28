"""Live end-to-end plumbing test for 'rough -> concrete' (WP-8 criterion 4): people in real browsers, a scripted agent over the stdio MCP.

The agent here is a script: it knows what was drawn, so this checks the plumbing (request -> working -> snapshot -> proposal -> card ->
apply / revert / dismiss on every screen), not recognition. Recognition is measured separately with a fresh agent that only calls MCP tools.

    python3 tests/live/live_rough.py                 # E1 steps 1-5 (+ apply / revert / apply), E6, E7 (20 rounds)
    python3 tests/live/live_rough.py --only e1 --shots docs/images   # also save the three guide pictures (rough-*.png)
    python3 tests/live/live_rough.py --rounds 5 --keep

Screens: A and B are desktop Chromium (1280x800), C is a phone (390x844, touch). The board opens with no tunnel in a temporary CERA_HOME
(--home; default a new folder under $TMPDIR) and is closed at the end; ~/.cera is never touched. Needs Playwright (Python).
Prints one JSON document (timings, tool calls, each check); exit code 1 if a check failed. The name (live_*.py) keeps it out of unittest discover.

Scenarios (the plan's (d) table):
  E1 1-5  A draws a small flowchart by hand (6 shapes, 6 arrows, typed labels), selects it, '✦ Ask AI' -> Diagram. Agent: board_wait
          kinds:request -> board_request_update working -> board_snapshot request -> board_add flow (layout keep) + proposal. The card shows
          on A, B and C; the originals keep their versions; scene hashes agree within 2 s. Then A applies, B reverts, A applies (hashes agree after each).
  E6      The agent answers failed with a reason: no card, the reason on every chip, the targets' versions unchanged.
  E7      A presses Replace and B presses Discard on the same card within 100 ms, N rounds: each round exactly one press wins (the other gets
          409 'Someone else got there first'), hashes agree 2 s later, and the originals and the result are never both gone.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R = random.Random(7)
RESULT = {"checks": [], "timings": {}, "tool_calls": {}}


def check(name, ok, detail=""):
    RESULT["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), file=sys.stderr, flush=True)
    return ok


# ---------------------------------------------------------------- the agent: a small stdio MCP client
class MCP:
    def __init__(self, home, client="claude-code"):
        env = {**os.environ, "CERA_HOME": home, "CERA_LANG": "en"}
        self.p = subprocess.Popen([sys.executable, str(ROOT / "bin" / "cera"), "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        self.i, self.err, self.calls = 0, [], []
        threading.Thread(target=lambda: [self.err.append(x) for x in self.p.stderr], daemon=True).start()
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": client, "version": "1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params=None):
        self.i += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.i, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("MCP closed: " + "".join(self.err[-5:]))
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == self.i:
                return m

    def tool(self, name, **args):
        self.calls.append(name)
        r = self.rpc("tools/call", {"name": name, "arguments": args}).get("result") or {}
        content = r.get("content") or []
        return {"error": bool(r.get("isError")), "text": "\n".join(c.get("text", "") for c in content if c.get("type") == "text"),
                "images": [c for c in content if c.get("type") == "image"], "st": r.get("structuredContent") or {}}

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(5)
        except Exception:   # noqa: BLE001
            self.p.kill()


# ---------------------------------------------------------------- a person: hand-drawn strokes with the mouse
def cr(pts, n=10):
    """Catmull-Rom through control points."""
    P = [pts[0]] + pts + [pts[-1]]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for k in range(n):
            t = k / n
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)
                             for j in (0, 1)))
    return out + [P[-2]]


def wob(a, b, n=14, amp=2.5):
    ph = R.uniform(0, 6.28)
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1
    nx, ny = -dy / L, dx / L
    return [(a[0] + dx * t + nx * amp * math.sin(ph + t * 3.1) + R.uniform(-.7, .7), a[1] + dy * t + ny * amp * math.sin(ph + t * 3.1) + R.uniform(-.7, .7))
            for t in [k / n for k in range(n + 1)]]


def rough_rect(x0, y0, x1, y1):
    c = [(x0 + R.uniform(-4, 4), y0 + R.uniform(-4, 4)), (x1 + R.uniform(-4, 4), y0 + R.uniform(-4, 4)), (x1 + R.uniform(-4, 4), y1 + R.uniform(-4, 4)),
         (x0 + R.uniform(-4, 4), y1 + R.uniform(-4, 4))]
    end = (c[0][0] + R.uniform(3, 10), c[0][1] + R.uniform(-6, 2))   # overshoots, not closed
    pts = []
    for a, b in zip(c, c[1:] + [end]):
        pts += wob(a, b)[:-1]
    return [pts + [end]]


def rough_ellipse(cx, cy, rx, ry):
    a0, n = R.uniform(-2.5, -1.5), 48
    return [[(cx + rx * (1 + 0.05 * math.sin(3 * t + 1)) * math.cos(t), cy + ry * (1 + 0.05 * math.sin(3 * t + 1)) * math.sin(t))
             for t in (a0 + 2 * math.pi * k / n for k in range(n + 5))]]


def rough_diamond(cx, cy, hw, hh):
    c = [(px + R.uniform(-4, 4), py + R.uniform(-4, 4)) for px, py in [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]]
    pts = []
    for a, b in zip(c, c[1:] + [(c[0][0] + R.uniform(2, 7), c[0][1] + R.uniform(-2, 4))]):
        pts += wob(a, b, amp=1.5)[:-1]
    return [pts + [c[0]]]


def rough_arrow(a, b, via=None):
    m = ((a[0] + b[0]) / 2 + R.uniform(-5, 5), (a[1] + b[1]) / 2 + R.uniform(-5, 5))
    pts = cr([a] + (via or [m]) + [b], 12)
    t, q = pts[-1], pts[-4]
    ang = math.atan2(t[1] - q[1], t[0] - q[0])
    L = R.uniform(11, 15)
    head = [(t[0] - L * math.cos(ang - 0.5), t[1] - L * math.sin(ang - 0.5)), t, (t[0] - L * math.cos(ang + 0.5), t[1] - L * math.sin(ang + 0.5))]
    return [pts, head]


def strokes(pg, sts):
    for pts in sts:
        pg.mouse.move(*pts[0])
        pg.mouse.down()
        for p in pts[1:]:
            pg.mouse.move(p[0], p[1])
        pg.mouse.up()
        time.sleep(0.05)


def type_text(pg, x, y, text):
    pg.keyboard.press("t")
    time.sleep(0.15)
    pg.mouse.click(x, y)
    time.sleep(0.3)
    pg.keyboard.type(text, delay=15)
    pg.keyboard.press("Escape")
    time.sleep(0.3)
    pg.keyboard.press("p")
    time.sleep(0.15)


# Screen positions at zoom 1 (A's view). Node centres are what the scripted agent 'reads' (it knows the drawing).
NODES = [("a", "start", "ellipse", (195, 105)), ("b", "load data", "box", (195, 212)), ("c", "ok?", "diamond", (195, 350)),
         ("d", "fit model", "box", (195, 487)), ("e", "end", "ellipse", (195, 595)), ("f", "clean", "box", (431, 351))]
EDGES = [("a", "b", ""), ("b", "c", ""), ("c", "d", "yes"), ("d", "e", ""), ("c", "f", "no"), ("f", "b", "")]


def draw_flow(pg, dx=0, dy=0):
    def o(p):
        return (p[0] + dx, p[1] + dy)
    pg.keyboard.press("p")
    strokes(pg, rough_ellipse(*o((195, 105)), 65, 28)); type_text(pg, *o((172, 95)), "start")
    strokes(pg, rough_arrow(o((195, 136)), o((196, 180))))
    strokes(pg, rough_rect(*o((115, 185)), *o((275, 240)))); type_text(pg, *o((150, 200)), "load data")
    strokes(pg, rough_arrow(o((195, 243)), o((194, 290))))
    strokes(pg, rough_diamond(*o((195, 350)), 78, 56)); type_text(pg, *o((180, 338)), "ok?")
    strokes(pg, rough_arrow(o((195, 408)), o((196, 455)))); type_text(pg, *o((205, 420)), "yes")
    strokes(pg, rough_rect(*o((115, 460)), *o((275, 515)))); type_text(pg, *o((150, 475)), "fit model")
    strokes(pg, rough_arrow(o((195, 518)), o((195, 560))))
    strokes(pg, rough_ellipse(*o((195, 595)), 58, 27)); type_text(pg, *o((177, 584)), "end")
    strokes(pg, rough_arrow(o((275, 350)), o((358, 350)))); type_text(pg, *o((300, 322)), "no")
    strokes(pg, rough_rect(*o((362, 325)), *o((500, 378)))); type_text(pg, *o((400, 340)), "clean")
    strokes(pg, rough_arrow(o((432, 322)), o((280, 212)), via=[o((440, 250)), o((380, 215))]))
    pg.keyboard.press("v")


# ---------------------------------------------------------------- browser helpers
HASH = "(() => window.__wbapi.getSceneElements().filter(e => !e.isDeleted).map(e => e.id + ':' + e.version).sort().join('|'))()"


def join(br, url, name, **ctx):
    pg = br.new_context(locale="en-US", **ctx).new_page()
    pg.goto(url)
    try:
        pg.wait_for_selector("input[aria-label='Name']", timeout=8000)
        pg.fill("input[aria-label='Name']", name)
        pg.click("button.wb-primary")
    except Exception:   # noqa: BLE001 — the name may already be known
        pass
    pg.wait_for_selector(".excalidraw canvas", timeout=30000)
    time.sleep(1.5)
    return pg


def poll(fn, timeout=5.0, step=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v, time.time() - t0
        time.sleep(step)
    return None, time.time() - t0


def same_hash(pages, timeout=2.0):
    v, dt = poll(lambda: len({pg.evaluate(HASH) for pg in pages}) == 1, timeout)
    return bool(v), dt


def chips(pg):
    return pg.evaluate("[...document.querySelectorAll('.wb-ask-chip')].map(c => c.innerText.replace(/\\n/g, ' | '))")


def cards(pg):
    return pg.evaluate("[...document.querySelectorAll('.wb-prop-card')].map(c => c.innerText.replace(/\\n/g, ' | '))")


def versions(pg, ids):
    return pg.evaluate("(ids) => Object.fromEntries(window.__wbapi.getSceneElementsIncludingDeleted().filter(e => ids.includes(e.id)).map(e => [e.id, [e.version, !!e.isDeleted]]))", ids)


def live_ids(pg):
    return pg.evaluate("window.__wbapi.getSceneElements().filter(e => !e.isDeleted).map(e => e.id)")


def show_all(pg):
    pg.evaluate("window.__wbapi.scrollToContent(window.__wbapi.getSceneElements().filter(e => !e.isDeleted), {fitToContent: true})")
    time.sleep(0.6)


STATUS = """(pid) => { const a = window.__wbapi.getSceneElements().find(e => e.customData && e.customData.cera && e.customData.cera.proposal
    && e.customData.cera.proposal.pid === pid); return a ? String(a.customData.cera.proposal.status || 'proposed') : 'gone'; }"""


PROPS_FOR = """(rid) => window.__wbapi.getSceneElementsIncludingDeleted().filter(e => !e.isDeleted && e.customData && e.customData.cera
    && e.customData.cera.proposal && e.customData.cera.proposal.rid === rid).length"""


def status(pg, pid):
    return pg.evaluate(STATUS, pid)


def press(pg, label):
    """Press a card button by its text (desktop card; the phone opens its sheet first)."""
    return pg.evaluate("""(label) => { const b = [...document.querySelectorAll('.wb-prop-card button, .wb-prop-sheet button')]
        .find(x => x.innerText.trim() === label && !x.disabled); if (!b) return false; b.click(); return true; }""", label)


def ask(pg, ids, kind_label="Diagram", note=""):
    pg.evaluate("(ids) => window.__wbapi.updateScene({appState: {selectedElementIds: Object.fromEntries(ids.map(i => [i, true]))}})", ids)
    pg.wait_for_selector(".wb-ask-btn", timeout=5000)
    pg.click(".wb-ask-btn")
    pg.wait_for_selector(".wb-ask-sheet", timeout=5000)
    pg.click(f".wb-ask-kind:has-text('{kind_label}')")
    if note:
        pg.fill(".wb-ask-note", note)
    t = time.time()
    pg.click(".wb-ask-sheet .wb-primary")
    pg.wait_for_selector(".wb-ask-sheet", state="detached", timeout=5000)
    return t


def scrub(pg):
    """Before a guide picture: no names, paths or addresses on screen (people list, page tabs, URL bits)."""
    pg.evaluate("""document.querySelectorAll('.wb-people, .wb-presence, .wb-follow-bar, .excalidraw .collab-button, .wb-title')
        .forEach(e => e.style.visibility = 'hidden')""")


CLIP = {"x": 120, "y": 60, "width": 1150, "height": 650}   # the drawing, without the toolbars


def shot(pg, path, clip=None):
    pg.evaluate("window.__wbapi.updateScene({appState: {selectedElementIds: {}}})")
    time.sleep(0.3)
    scrub(pg)
    pg.screenshot(path=str(path), clip=clip, type="png")
    return Path(path).stat().st_size


# ---------------------------------------------------------------- scenarios
def e1(m, A, B, C, url, shots):
    origin = A.evaluate("(() => { const s = window.__wbapi.getAppState(); return [s.scrollX, s.scrollY, s.zoom.value]; })()")
    t0 = time.time()
    draw_flow(A)
    RESULT["timings"]["e1_draw_s"] = round(time.time() - t0, 1)
    src = live_ids(A)
    check("E1 hand drawing on the board", len(src) >= 20, f"{len(src)} elements")
    n_calls = len(m.calls)
    seq = m.tool("board_read")["st"].get("seq")
    t_ask = ask(A, src, "Diagram", "make it a clean flowchart")
    # agent step 1: wait for the request
    w = m.tool("board_wait", kinds=["request"], timeout_s=30, **({"since_seq": seq} if seq is not None else {}))
    reqs = m.tool("board_requests")["st"].get("requests") or []
    rid = reqs[0]["rid"] if reqs else None
    RESULT["timings"]["e1_request_seen_s"] = round(time.time() - t_ask, 2)
    check("E1.1 board_wait kinds:request wakes on the request (no timeout, names that rid)",
          rid and not w["error"] and not w["st"].get("timeout") and rid in w["text"], w["text"][:120])
    # step 2: take it
    u = m.tool("board_request_update", rid=rid, status="working")
    ok, _ = poll(lambda: all(any("Working" in c or "working" in c for c in chips(p)) for p in (A, B, C)), 3)
    RESULT["timings"]["e1_working_s"] = round(time.time() - t_ask, 2)
    check("E1.2 working: every screen shows the chip", not u["error"] and ok, str(chips(B)))
    if shots:
        show_all(A)
        RESULT.setdefault("shots", {})["rough-request.png"] = shot(A, Path(shots) / "rough-request.png", CLIP)
    v_before = versions(A, src)
    # step 3: look
    s = m.tool("board_snapshot", request=rid)
    png = [ln for ln in s["text"].splitlines() if ".png" in ln]
    check("E1.3 board_snapshot request: an image block and a PNG path", not s["error"] and s["images"] and png, (png or [""])[0][-80:])
    # step 4: one board_add — the flow beside the originals, as a proposal (originals untouched)
    sx, sy, z = origin
    nodes = [{"id": k, "label": lab, "shape": shp, "at": {"x": x / z - sx, "y": y / z - sy}} for k, lab, shp, (x, y) in NODES]
    edges = [{"from": a, "to": b, **({"label": lab} if lab else {})} for a, b, lab in EDGES]
    add = m.tool("board_add", items=[{"kind": "flow", "layout": "keep", "nodes": nodes, "edges": edges}],
                 proposal={"request": rid, "note": "6 steps; 'clean' loops back to 'load data'",
                           "uncertain": [{"text": "clean", "alts": ["dean"]}]})
    agent_calls = m.calls[n_calls:]   # what the agent itself called, up to its proposal (the checks below call tools too)
    pid = (add["st"].get("proposal") or {}).get("pid")
    check("E1.4 board_add flow + proposal places a proposal", not add["error"] and pid, add["text"][:160])
    # step 5: the request is 'proposal ready' and the card shows on every screen; originals unchanged
    for pg in (B, C):
        show_all(pg)
    ok, dt = poll(lambda: all(cards(p) for p in (A, B, C)), 5)
    RESULT["timings"]["e1_card_s"] = round(time.time() - t_ask, 2)
    r2 = m.tool("board_requests", all=True)["st"].get("requests") or []
    check("E1.5 proposed: card on A, B and C", ok and any(r["rid"] == rid and r.get("status") == "proposed" for r in r2),
          f"A{cards(A)[:1]} B{len(cards(B))} C{cards(C)} status {[r.get('status') for r in r2 if r['rid'] == rid]}")
    check("E1.5 originals keep their versions until someone applies", versions(A, src) == v_before)
    check("E1.5 the proposal names its request on every screen (the E6 check relies on this)", [pg.evaluate(PROPS_FOR, rid) for pg in (A, B, C)] == [1, 1, 1])
    ok, dt = same_hash([A, B, C])
    check("E1.5 scene hashes agree (A, B, C)", ok, f"{dt:.2f} s")
    RESULT["timings"]["e1_ask_to_card_s"] = RESULT["timings"]["e1_card_s"]
    check("E1 the agent needed <= 6 tool calls (read, wait, requests, working, snapshot, add)", len(agent_calls) <= 6, str(agent_calls))
    check("E1 ✦ to card within 90 s", RESULT["timings"]["e1_card_s"] <= 90, f"{RESULT['timings']['e1_card_s']} s")
    if shots:
        show_all(A)
        RESULT["shots"]["rough-card.png"] = shot(A, Path(shots) / "rough-card.png", CLIP)
    # beyond 1-5: A applies, B reverts, A applies again
    for who, pg, label, want in (("A", A, "Replace", "applied"), ("B", B, "Original ↺", "proposed"), ("A", A, "Replace", "applied")):
        hit, _ = poll(lambda: press(pg, label), 3)
        t = time.time()
        seen, _ = poll(lambda: all(status(p, pid) == want for p in (A, B, C)), 3)
        ok, dt = same_hash([A, B, C], 2)
        check(f"E1 {who} presses {label}: '{want}' on every screen, hashes agree", hit and seen and ok, f"{time.time() - t:.2f} s")
    left = [k for k, v in versions(A, src).items() if not v[1]]
    check("E1 after apply the originals are deleted (kept for revert)", not left, A.evaluate(
        "(ids) => window.__wbapi.getSceneElements().filter(e => ids.includes(e.id)).map(e => e.type + ':' + (e.frameId || '') + ':' + (e.containerId || ''))", left))
    if shots:
        time.sleep(3.2)   # the toast fades
        show_all(A)
        RESULT["shots"]["rough-applied.png"] = shot(A, Path(shots) / "rough-applied.png", CLIP)
    return pid


def e6(m, A, B, C):
    A.evaluate("window.__wbapi.updateScene({appState: {scrollX: 0, scrollY: 0, zoom: {value: 1}}})")
    seq = m.tool("board_read")["st"].get("seq")
    A.keyboard.press("p")
    strokes(A, rough_rect(700, 200, 820, 260) + rough_arrow((830, 230), (900, 230)))
    A.keyboard.press("v")
    time.sleep(0.8)
    tgt = [i for i in live_ids(A) if i not in m.seen]
    v0 = versions(A, tgt)
    ask(A, tgt, "Math")
    w = m.tool("board_wait", kinds=["request"], timeout_s=15, **({"since_seq": seq} if seq is not None else {}))
    rid = next((r["rid"] for r in m.tool("board_requests")["st"].get("requests") or [] if r.get("status") == "open"), None)
    check("E6 board_wait wakes on the new request", rid and not w["error"] and not w["st"].get("timeout") and rid in w["text"], w["text"][:120])
    m.tool("board_request_update", rid=rid, status="working")
    f = m.tool("board_request_update", rid=rid, status="failed", reply="could not read the 2nd line")
    for pg in (B, C):   # people look at the same spot
        pg.evaluate("(ids) => window.__wbapi.scrollToContent(window.__wbapi.getSceneElements().filter(e => ids.includes(e.id)), {fitToContent: true})", tgt)
    ok, dt = poll(lambda: all(any("could not read the 2nd line" in c for c in chips(p)) for p in (A, B, C)), 3)
    check("E6 failed: the reason on every chip (A, B, C)", not f["error"] and ok, f"{dt:.2f} s {f['text'][:80]} A{chips(A)} B{chips(B)} C{chips(C)}")
    time.sleep(1.0)   # give a wrongly placed proposal time to reach every screen
    with_rid = [pg.evaluate(PROPS_FOR, rid) for pg in (A, B, C)]
    check("E6 no proposal for the failed request on A, B or C", with_rid == [0, 0, 0], str(with_rid))
    check("E6 target versions unchanged", versions(A, tgt) == v0)
    for pg in (A,):   # close it so later chips do not pile up
        pg.evaluate("""[...document.querySelectorAll('.wb-ask-chip button')].filter(b => b.innerText.trim() === 'Close').forEach(b => b.click())""")


def e7(m, A, B, C, rounds):
    wins, bad, slow = {"apply": 0, "dismiss": 0}, [], []
    for k in range(rounds):
        A.evaluate("window.__wbapi.updateScene({appState: {scrollX: -%d, scrollY: -1500, zoom: {value: 1}}})" % (k * 600))
        before = set(live_ids(A))
        A.keyboard.press("p")
        strokes(A, rough_ellipse(300, 300, 40, 25))
        A.keyboard.press("v")
        time.sleep(0.5)
        src = [i for i in live_ids(A) if i not in before]
        add = m.tool("board_add", items=[{"kind": "box", "shape": "ellipse", "label": f"r{k}"}], proposal={"sources": src})
        pid = (add["st"].get("proposal") or {}).get("pid")
        res = set(add["st"].get("ids") or [])
        for pg in (A, B):
            pg.evaluate("(ids) => window.__wbapi.scrollToContent(window.__wbapi.getSceneElements().filter(e => ids.includes(e.id)), {fitToContent: true})",
                        list(src) + list(res))
        if not poll(lambda: cards(A) and cards(B), 4)[0]:
            bad.append((k, "no card"))
            continue
        statuses = []
        # both screens press at the same wall-clock moment (same machine clock): each arms a timer, then the button is clicked by the page itself
        arm = """([label, at]) => { window.__pressed = null; setTimeout(() => { const b = [...document.querySelectorAll('.wb-prop-card button')]
            .find(x => x.innerText.trim() === label && !x.disabled); window.__pressed = b ? Date.now() : -1; if (b) b.click(); }, Math.max(0, at - Date.now())); }"""
        at = int(time.time() * 1000) + 300
        A.evaluate(arm, ["Replace", at])
        B.evaluate(arm, ["Discard", at])
        pr, _ = poll(lambda: (lambda a_, b_: (a_, b_) if a_ and b_ else None)(A.evaluate("window.__pressed"), B.evaluate("window.__pressed")), 3)
        if not pr or -1 in pr:
            bad.append((k, f"button missing {pr}"))
            continue
        slow.append(abs(pr[0] - pr[1]))
        toasts, _ = poll(lambda: [t for pg in (A, B) for t in pg.evaluate("[...document.querySelectorAll('.wb-prop-toast')].map(t => t.innerText)")
                                  if "Someone else" in t], 2.5)
        toasts = toasts or []
        time.sleep(2.0)
        ok, dt = same_hash([A, B, C], 0.5)
        alive = set(live_ids(A))
        st = status(A, pid)
        lost = not (set(src) & alive) and not (res & alive)
        if st == "applied":
            wins["apply"] += 1
        elif st == "gone" or (not (res & alive) and set(src) <= alive):
            wins["dismiss"] += 1
        else:
            bad.append((k, f"status {st}"))
        if lost or not ok or not same_hash([A, B, C], 0.1)[0]:
            bad.append((k, f"lost={lost} hash={ok}"))
        statuses.append(toasts)
        if not any("Someone else got there first" in t for t in toasts):
            bad.append((k, f"no 409 toast: {toasts}"))
        if st == "applied":   # close the card so the next round starts clean
            poll(lambda: press(A, "Done"), 2)
    RESULT["timings"]["e7_press_gap_ms_max"] = round(max(slow or [0]), 1)
    check(f"E7 {rounds} rounds: exactly one press wins each time, nothing lost, hashes agree", not bad and sum(wins.values()) == rounds,
          f"wins {wins} problems {bad[:5]}")
    check("E7 the two presses were within 100 ms", max(slow or [999]) <= 100, f"max {max(slow or [0]):.0f} ms")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home")
    ap.add_argument("--keep", action="store_true", help="leave the board open and the folder in place")
    ap.add_argument("--only", default="e1,e6,e7")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--shots", help="save rough-request.png, rough-card.png and rough-applied.png here")
    ap.add_argument("--browser", default="chromium", choices=["chromium", "webkit"])
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    home = a.home or tempfile.mkdtemp(prefix="cera-rough-")
    env = {**os.environ, "CERA_HOME": home, "CERA_LANG": "en"}
    if (ROOT / "web" / "dist" / "index.html").is_file():
        env["CERA_DIST"] = str(ROOT / "web" / "dist")
    out = json.loads(subprocess.run([sys.executable, str(ROOT / "bin" / "cera"), "start", "--tunnel", "none", "--view-link", "--title", "rough",
                                     "--name", "민수", "--json"], env=env, capture_output=True, text=True, check=True, timeout=300).stdout)
    url = out["links"]["edit"]
    only = set(a.only.split(","))
    m = MCP(home)
    m.seen = set()
    try:
        with sync_playwright() as p:
            br = getattr(p, a.browser).launch()
            A = join(br, url, "민수", viewport={"width": 1280, "height": 800})
            B = join(br, url, "지아", viewport={"width": 1280, "height": 800})
            C = join(br, url, "하늘", viewport={"width": 390, "height": 844}, is_mobile=a.browser == "chromium", has_touch=True, device_scale_factor=2)
            if a.shots:
                Path(a.shots).mkdir(parents=True, exist_ok=True)
            if "e1" in only:
                e1(m, A, B, C, url, a.shots)
            m.seen = set(live_ids(A)) | set(A.evaluate("window.__wbapi.getSceneElementsIncludingDeleted().map(e => e.id)"))
            if "e6" in only:
                e6(m, A, B, C)
            if "e7" in only:
                e7(m, A, B, C, a.rounds)
            br.close()
    finally:
        RESULT["tool_calls"] = {k: m.calls.count(k) for k in sorted(set(m.calls))}
        m.close()
        if not a.keep:
            subprocess.run([sys.executable, str(ROOT / "bin" / "cera"), "stop", out["id"]], env=env, capture_output=True, timeout=60)
            if not a.home:
                shutil.rmtree(home, ignore_errors=True)
    RESULT["ok"] = all(c["ok"] for c in RESULT["checks"])
    print(json.dumps(RESULT, ensure_ascii=False, indent=1))
    return 0 if RESULT["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
