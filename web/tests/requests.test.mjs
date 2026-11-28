// 'Ask the AI' requests (WP-5) - chip text and buttons, merging events, targets, layout that never covers the tool bar / zoom / '?'
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import {
  chipModel, mergeRequest, replaceAll, visibleRequests, copyText, targetIds, noteOk, toScreen, selectionBox, place, layoutChips, askSpot,
  COPY_AFTER_MS, NOTE_MAX, KINDS, kindLabel, ACTION_STATUS, CHIP_H,
} from "../src/features/ask/requests.js";

const T0 = 1_700_000_000_000;
const req = (o = {}) => ({ rid: "a1b2c3d4e5", n: 1, by: "민수", kind: "diagram", note: "", targets: [{ id: "s1", v: 3 }], bbox: { x: 100, y: 100, w: 200, h: 120 },
  page: null, status: "open", agent: null, reply: "", pid: null, created: T0, updated: T0, ...o });
const ST = { zoom: { value: 1 }, scrollX: 0, scrollY: 0, offsetLeft: 0, offsetTop: 0 };
const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test("criterion 1 · 3 · 4: chip text per state (name · kind · waiting -> agent working -> ready / failed / interrupted)", () => {
  assert.equal(chipModel(req(), T0).text, "✦ 민수 · 도식 · 대기 중");
  assert.deepEqual(chipModel(req(), T0).actions, ["cancel"]);
  assert.equal(chipModel(req({ status: "working", agent: "Claude" }), T0).text, "✦ Claude 작업 중…");
  assert.equal(chipModel(req({ status: "proposed", reply: "둘째 줄 확인" }), T0).text, "✦ 제안 도착");
  assert.equal(chipModel(req({ status: "proposed", reply: "둘째 줄 확인" }), T0).sub, "둘째 줄 확인");
  const f = chipModel(req({ status: "failed", reply: "2번째 줄을 못 읽음" }), T0);
  assert.equal(f.text, "✦ 실패: 2번째 줄을 못 읽음");
  assert.deepEqual(f.actions, ["retry", "close"]);
  const i = chipModel(req({ status: "interrupted" }), T0);
  assert.equal(i.text, "✦ 중단됨");
  assert.ok(i.actions.includes("retry"));
  assert.equal(chipModel(req({ status: "declined", reply: "터미널에서 직접 요청해 주세요" }), T0).text, "✦ 거절: 터미널에서 직접 요청해 주세요");
  assert.deepEqual(ACTION_STATUS, { cancel: "cancelled", close: "done", retry: "done" });
  assert.equal(kindLabel("math"), "수식");
  assert.deepEqual([...KINDS].sort(), ["auto", "diagram", "graph", "math", "text", "tidy"]);
});

test("criterion 5: a view-only screen shows the chip but no buttons", () => {
  for (const status of ["open", "working", "proposed", "failed", "declined", "interrupted"]) {
    assert.deepEqual(chipModel(req({ status }), T0 + COPY_AFTER_MS, true).actions, [], status);
  }
});

test("criterion 8: after 20 s with no agent the copy button appears; the sentence has the board id and rid, never a key or token", () => {
  assert.ok(!chipModel(req(), T0 + COPY_AFTER_MS - 1).actions.includes("copy"));
  const m = chipModel(req(), T0 + COPY_AFTER_MS);
  assert.ok(m.actions.includes("copy"));
  assert.equal(m.sub, "에이전트가 아직 안 가져감");
  const s = copyText("b0a1c2d3e4f5", "a1b2c3d4e5");
  assert.equal(s, "cera 보드 b0a1c2d3e4f5 의 요청 a1b2c3d4e5 를 처리해 줘");
  assert.ok(!s.includes("?k=") && !s.includes("tb_ag_"));
});

test("events: same rid keeps the newer `updated`, list sorted by number; a GET does not undo a newer event", () => {
  let l = mergeRequest([], req({ rid: "b", n: 2 }));
  l = mergeRequest(l, req({ rid: "a", n: 1 }));
  assert.deepEqual(l.map((r) => r.rid), ["a", "b"]);
  l = mergeRequest(l, req({ rid: "a", n: 1, status: "working", updated: T0 + 5 }));
  l = mergeRequest(l, req({ rid: "a", n: 1, status: "open", updated: T0 + 1 }));   // late, older event
  assert.equal(l[0].status, "working");
  const fresh = replaceAll(l, [req({ rid: "a", n: 1, status: "open", updated: T0 }), req({ rid: "c", n: 3 })]);
  assert.deepEqual(fresh.map((r) => [r.rid, r.status]), [["a", "working"], ["c", "open"]]);
  assert.equal(mergeRequest(l, null), l);
  assert.deepEqual(visibleRequests([req({ status: "done" }), req({ status: "cancelled" }), req({ rid: "z" })]).map((r) => r.rid), ["z"]);
});

test("targets: live selected elements plus the text inside a selected box; deleted ones dropped; at most 500", () => {
  const els = [{ id: "box", x: 0, y: 0, width: 10, height: 10 }, { id: "lab", containerId: "box", x: 1, y: 1, width: 5, height: 5 },
    { id: "gone", isDeleted: true, x: 0, y: 0 }, { id: "other", x: 0, y: 0 }];
  assert.deepEqual(targetIds(els, { box: true, gone: true }), { ids: ["box", "lab"], over: false });
  const many = Array.from({ length: 501 }, (_, i) => ({ id: `e${i}`, x: 0, y: 0 }));
  const r = targetIds(many, Object.fromEntries(many.map((e) => [e.id, true])));
  assert.equal(r.ids.length, 500);
  assert.ok(r.over);
  assert.ok(noteOk("가".repeat(NOTE_MAX)) && !noteOk("가".repeat(NOTE_MAX + 1)));
});

test("selection box uses stroke points; scene -> screen follows zoom and scroll", () => {
  const els = [{ id: "s", x: 10, y: 10, width: 50, height: 5, points: [[0, 0], [50, -20]] }, { id: "t", x: 100, y: 40, width: 20, height: 10 }];
  assert.deepEqual(selectionBox(els, { s: true, t: true }), { x: 10, y: -10, w: 110, h: 60 });
  assert.equal(selectionBox(els, {}), null);
  assert.deepEqual(toScreen({ x: 10, y: 20, w: 30, h: 40 }, { zoom: { value: 2 }, scrollX: -5, scrollY: 0, offsetLeft: 0, offsetTop: 0 }), { x: 10, y: 40, w: 60, h: 80 });
});

// obstacles measured from the real layout: phone (390x844) top bar + two-row dock, desktop zoom (bottom left) + '?' (bottom right) above the dock
const PHONE = { view: { w: 390, h: 844 }, obs: [{ x: 8, y: 8, w: 374, h: 44 }, { x: 6, y: 706, w: 378, h: 132 }, { x: 290, y: 650, w: 92, h: 48 }] };
const DESK = (w) => ({ view: { w, h: 800 }, obs: [{ x: (w - 900) / 2, y: 706, w: 900, h: 80 }, { x: 16, y: 650, w: 160, h: 40 }, { x: w - 56, y: 650, w: 40, h: 40 },
  { x: 12, y: 12, w: 40, h: 40 }, { x: w - 300, y: 12, w: 288, h: 40 }] });

test("criterion 7: chips and the ask button never cover the tool bar, zoom, '?' or the top bar (390 · 1024 · 1280)", () => {
  for (const [name, L] of [["phone", PHONE], ["1024", DESK(1024)], ["1280", DESK(1280)]]) {
    const touch = name === "phone";
    // areas everywhere on screen, including right on top of the bottom controls and in the corners
    for (const [x, y] of [[20, 20], [100, 300], [10, 640], [L.view.w - 120, 660], [L.view.w / 2, 720], [L.view.w - 60, 30]]) {
      const r = req({ bbox: { x, y, w: 100, h: 60 } });
      const [it] = layoutChips([r], ST, L.obs, L.view, T0 + COPY_AFTER_MS, { touch });
      assert.ok(it.chip, `${name} chip at ${x},${y}`);
      for (const o of L.obs) assert.ok(!hit(it.chip, o), `${name} chip at ${x},${y} covers ${JSON.stringify(o)}`);
      assert.ok(it.chip.x >= 8 && it.chip.y >= 8 && it.chip.x + it.chip.w <= L.view.w - 8 && it.chip.y + it.chip.h <= L.view.h - 8, `${name} on screen`);
      if (touch) assert.ok(it.chip.h >= 52, "phone chip leaves room for 44 px buttons");
      const bw = touch ? 112 : 96, bh = touch ? 44 : 34;
      const b = askSpot({ x, y, w: 100, h: 60 }, bw, bh, L.obs, L.view);
      assert.ok(b, `${name} button at ${x},${y}`);
      for (const o of L.obs) assert.ok(!hit({ ...b, w: bw, h: bh }, o), `${name} button covers ${JSON.stringify(o)}`);
    }
  }
});

test("two requests on overlapping areas: both chips, numbered 1 and 2, not on top of each other", () => {
  const a = req({ rid: "a", n: 4, bbox: { x: 100, y: 200, w: 200, h: 100 } }), b = req({ rid: "b", n: 5, by: "지수", bbox: { x: 150, y: 220, w: 200, h: 100 } });
  const c = req({ rid: "c", n: 6, bbox: { x: 600, y: 200, w: 50, h: 50 } });
  const out = layoutChips([b, c, a], ST, [], { w: 1280, h: 800 }, T0);
  const by = Object.fromEntries(out.map((o) => [o.rid, o]));
  assert.equal(by.a.order, 1);
  assert.equal(by.b.order, 2);
  assert.equal(by.c.order, null);
  assert.ok(!hit(by.a.chip, by.b.chip));
});

test("an area entirely off screen gets no chip; place gives up rather than cover", () => {
  const [it] = layoutChips([req({ bbox: { x: -900, y: 100, w: 100, h: 100 } })], ST, [], { w: 800, h: 600 }, T0);
  assert.equal(it.chip, null);
  assert.equal(place({ x: 0, y: 0, w: 10, h: 10 }, 50, 30, [{ x: 0, y: 0, w: 800, h: 600 }], { w: 800, h: 600 }), null);
});

test("criterion 1 (export): the ask feature never writes board elements - chips are an HTML layer, so PNG / PDF exports cannot contain them", () => {
  const dir = new URL("../src/features/ask/", import.meta.url);
  for (const f of readdirSync(dir)) {
    const src = readFileSync(new URL(f, dir), "utf8");
    for (const bad of ["updateScene", "convertToExcalidrawElements", "addFiles", "/elements"]) assert.ok(!src.includes(bad), `${f} uses ${bad}`);
  }
});

test("a '+' button above the selected box pushes the chip further up, not down onto the drawing", () => {
  const box = { x: 85, y: 360, w: 220, h: 120 }, plus = { x: 173, y: 282, w: 44, h: 44 };
  const [it] = layoutChips([req({ bbox: box })], ST, [plus], { w: 390, h: 844 }, T0, { touch: true });
  assert.ok(!hit(it.chip, plus));
  assert.ok(it.chip.y + it.chip.h <= box.y, "chip stays above the area");
  const b = askSpot(box, 112, 44, [plus, it.chip], { w: 390, h: 844 });
  assert.ok(!hit({ ...b, w: 112, h: 44 }, plus) && !hit({ ...b, w: 112, h: 44 }, it.chip) && b.y + 44 <= box.y);
});

test("review r1: chip heights are the drawn ones (the layer sets height/width from the layout) - with the 20 s line two stacked chips near the dock never touch it or each other", () => {
  const L = DESK(1024);
  const a = req({ rid: "a", n: 1, bbox: { x: 300, y: 556, w: 160, h: 60 } }), b = req({ rid: "b", n: 2, bbox: { x: 330, y: 566, w: 160, h: 60 } });
  for (const touch of [false, true]) {
    const out = layoutChips([a, b], ST, L.obs, L.view, T0 + COPY_AFTER_MS, { touch });
    for (const o of out) assert.equal(o.chip.h, (touch ? CHIP_H.touch : CHIP_H.desk) + CHIP_H.sub);
    assert.ok(!hit(out[0].chip, out[1].chip));
    for (const o of out) for (const ob of L.obs) assert.ok(!hit(o.chip, ob));
  }
  const css = readFileSync(new URL("../src/features/ask/RequestLayer.jsx", import.meta.url), "utf8");
  assert.ok(css.includes("width: chip.w, height: chip.h"), "the layer draws the chip at the laid-out size");
});
