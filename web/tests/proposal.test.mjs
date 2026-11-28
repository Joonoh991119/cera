// Proposal cards (WP-6) - card model per screen (desktop · phone · view link), the press body, toasts, retry, exports leave the anchor out
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load
import test from "node:test";
import assert from "node:assert/strict";
import {
  proposalOf, isAnchor, anchorsOf, pidKey, hideCarded, cardModel, cardSize, actionBody, toastModel, retryNote, retryIds, sceneHash, label, driftText, ACTION,
  onScreen, shownKey, resultBoxes, cardSpot, edgeSpot, layoutCards, MINI,
} from "../src/features/proposal/proposal.js";
import { resolveScope, expandSelection } from "../src/features/export/exporters.js";
import { place } from "../src/features/ask/requests.js";

const rec = (o = {}) => ({ pid: "a1b2c3d4e5", rid: "0123456789", sources: ["s1", "s2"], results: ["r1"], status: "proposed", home: [300, 0], src_box: [0, 0, 100, 50],
  deleted: [], note: "", uncertain: [], drift: 0, agent: "Claude", ...o });
const anchor = (o = {}, p = {}) => ({ id: "prop-a1b2c3d4e5", type: "rectangle", x: 300, y: 0, width: 200, height: 100, version: 1, isDeleted: false, locked: true,
  strokeStyle: "dashed", customData: { cera: { proposal: rec(p) } }, ...o });
const el = (id, o = {}) => ({ id, type: "freedraw", x: 0, y: 0, width: 10, height: 10, version: 3, isDeleted: false, ...o });

test("anchors are recognised by customData.cera.proposal only", () => {
  assert.equal(isAnchor(anchor()), true);
  assert.equal(isAnchor(el("x")), false);
  assert.equal(isAnchor({ id: "y", customData: { cera: { graph: { g: 1 } } } }), false, "flow nodes are not anchors");
  assert.equal(proposalOf({ customData: { cera: { proposal: { pid: 3 } } } }), null);
  const els = [el("s1"), anchor({ id: "prop-b", y: 200 }, { pid: "b" }), anchor(), anchor({ id: "prop-gone", isDeleted: true }, { pid: "gone" })];
  assert.deepEqual(anchorsOf(els).map((e) => proposalOf(e).pid), ["a1b2c3d4e5", "b"]);
  assert.equal(pidKey(els), "a1b2c3d4e5,b");
});

test("criterion 1: desktop card has the four buttons, note, '?' and drift badges", () => {
  const m = cardModel(rec({ note: "C 는 마름모", uncertain: [{ text: "yes", alts: ["ves"] }], drift: 2 }));
  assert.equal(m.title, "제안 · Claude");
  assert.deepEqual(m.actions, ["apply", "keepBoth", "dismiss", "retry"]);
  assert.deepEqual(m.actions.map(label), ["바꾸기", "둘 다 두기", "버리기", "다시"]);
  assert.equal(m.note, "C 는 마름모");
  assert.equal(m.uncertain.length, 1);
  assert.equal(driftText(m.drift), "읽은 뒤 바뀜 2");
  const { w, h } = cardSize(m, false);
  assert.ok(w <= 420 && w >= 280, `w=${w}`);
  assert.ok(h >= 80 && h <= 110, `h=${h}`);
});

test("criterion 8: drift 0 shows no badge; only whole positive numbers count", () => {
  assert.equal(cardModel(rec({ drift: 0 })).drift, 0);
  assert.equal(cardModel(rec({ drift: "2" })).drift, 0);
  assert.equal(cardModel(rec({ drift: 2 })).drift, 2);
});

test("criterion 2: after apply the card offers [원본 ↺] and [확정] everywhere (phone too)", () => {
  for (const touch of [false, true]) {
    const m = cardModel(rec({ status: "applied" }), { touch });
    assert.equal(m.title, "바꿈 · Claude");
    assert.deepEqual(m.actions.map(label), ["원본 ↺", "확정"]);
  }
});

test("criterion 7: phone card is one 'choose' button; the sheet has 3 big buttons and retry under '…'; view link has none", () => {
  const t = cardModel(rec(), { touch: true });
  assert.deepEqual(t.actions, ["open"]);
  assert.deepEqual(t.sheet.map(label), ["바꾸기", "둘 다 두기", "버리기"]);
  assert.deepEqual(t.more.map(label), ["다시"]);
  const { w, h } = cardSize(t, true);
  assert.ok(w <= 374, "fits a 390 px phone");
  assert.ok(h >= 44 + 16, "44 px tap target");
  for (const st of ["proposed", "applied"]) {
    for (const touch of [false, true]) assert.deepEqual(cardModel(rec({ status: st }), { touch, viewOnly: true }).actions, [], `${st} ${touch}`);
  }
});

test("criterion 7: a phone card above the anchor stays on a 390 px screen and off the dock", () => {
  const m = cardModel(rec(), { touch: true }), { w, h } = cardSize(m, true);
  const view = { w: 390, h: 844 }, dock = { x: 0, y: 760, w: 390, h: 84 }, top = { x: 0, y: 0, w: 390, h: 52 };
  for (const a of [{ x: 300, y: 60, w: 200, h: 100 }, { x: -50, y: 700, w: 120, h: 80 }, { x: 20, y: 300, w: 340, h: 200 }]) {
    const p = place(a, w, h, [dock, top], view);
    assert.ok(p, JSON.stringify(a));
    assert.ok(p.x >= 8 && p.x + w <= 382 && p.y >= 8 && p.y + h <= view.h - 8);
    const hit = (o) => p.x < o.x + o.w && o.x < p.x + w && p.y < o.y + o.h && o.y < p.y + h;
    assert.ok(!hit(dock) && !hit(top), JSON.stringify({ a, p }));
  }
});

test("criterion 4: each press sends the status the card showed (the server answers 409 if it moved on)", () => {
  assert.deepEqual(actionBody(rec(), "apply", "c1"), { client: "c1", action: "apply", from_status: "proposed" });
  assert.deepEqual(actionBody(rec(), "keepBoth", "c1").action, "keep");
  assert.deepEqual(actionBody(rec(), "dismiss", "c1").action, "dismiss");
  assert.deepEqual(actionBody(rec({ status: "applied" }), "revert", "c1"), { client: "c1", action: "revert", from_status: "applied" });
  assert.deepEqual(actionBody(rec({ status: "applied" }), "keepDone", "c1"), { client: "c1", action: "keep", from_status: "applied" });
  assert.equal(actionBody(rec(), "retry", "c1"), null, "retry makes a new request instead");
  assert.deepEqual(Object.values(ACTION).sort(), ["apply", "dismiss", "keep", "keep", "revert"]);
});

test("toast on every screen: '민수가 바꿈 · [원래대로]', other actions without undo, view links never get the undo button", () => {
  assert.deepEqual(toastModel({ pid: "p", action: "apply", by: "민수", status: "applied" }), { text: "민수가 바꿈", undo: "revert", pid: "p" });
  assert.equal(toastModel({ pid: "p", action: "apply", by: "길동", status: "applied" }).text, "길동이 바꿈");
  assert.equal(toastModel({ pid: "p", action: "apply", by: "민수" }, true).undo, null);
  assert.equal(toastModel({ action: "revert", by: "민수" }).text, "민수가 원래대로 되돌림");
  assert.equal(toastModel({ action: "dismiss", by: "민수" }).text, "민수가 제안을 버림");
  assert.equal(toastModel({ action: "keep", by: "민수", from: "proposed", status: "gone" }).text, "민수가 둘 다 둠");
  assert.equal(toastModel({ action: "keep", by: "민수", from: "applied", status: "gone" }).text, "민수가 확정함");
  assert.equal(toastModel({ action: "rm -rf", by: "x" }), null);
});

test("retry: same originals that are still on the board, note links the earlier pid (<= 200 chars)", () => {
  assert.deepEqual(retryIds(rec(), [el("s1"), el("s2", { isDeleted: true })]), ["s1"]);
  assert.equal(retryNote("p1", ""), "다시(앞 제안 p1)");
  assert.equal(retryNote("p1", "흐름도로"), "다시(앞 제안 p1) · 흐름도로");
  assert.equal(retryNote("p1", "가".repeat(200)).length, 200);
});

test("WP-5 chip: a proposed request whose card is on the board shows only the card", () => {
  const list = [{ rid: "a", status: "proposed", pid: "p1" }, { rid: "b", status: "proposed", pid: "p9" }, { rid: "c", status: "working", pid: null }];
  assert.deepEqual(hideCarded(list, "p1,p2").map((r) => r.rid), ["b", "c"]);
  assert.equal(hideCarded(list, ""), list);
});

test("Fable r2 #1: a card off screen does not hide its '제안 도착' chip — only drawn cards do", () => {
  const view = { w: 390, h: 844 };
  assert.equal(onScreen({ x: 400, y: 100, w: 200, h: 80 }, view), false, "오른쪽 밖");
  assert.equal(onScreen({ x: 380, y: 100, w: 200, h: 80 }, view), true, "조금이라도 보이면");
  assert.equal(onScreen({ x: -300, y: 100, w: 200, h: 80 }, view), false);
  const list = [{ rid: "a", status: "proposed", pid: "p1" }, { rid: "b", status: "proposed", pid: "p2" }];
  const drawn = [{ p: { pid: "p2" } }];   // p1's anchor is off screen: no card
  assert.equal(shownKey(drawn), "p2");
  assert.deepEqual(hideCarded(list, shownKey(drawn)).map((r) => r.rid), ["a"], "p1 의 칩은 남는다");
  assert.deepEqual(hideCarded(list, shownKey([])).map((r) => r.rid), ["a", "b"]);
});

test("scene hash: sorted id:version of live elements", () => {
  const a = [el("b", { version: 2 }), el("a"), el("z", { isDeleted: true })];
  assert.equal(sceneHash(a), "a:3|b:2");
  assert.equal(sceneHash([...a].reverse()), sceneHash(a));
});

test("criterion 6: PNG / PDF / selection-only exports leave the anchor out; .excalidraw keeps the scene as is", () => {
  const frame = { id: "tbpage-1", type: "frame", x: -10, y: -10, width: 1000, height: 600, version: 1, isDeleted: false, customData: { tbPage: 1 } };
  const els = [el("s1"), el("r1", { x: 310, y: 10 }), anchor(), frame];
  for (const scope of ["all", "selection", "frame"]) {
    const sel = scope === "selection" ? { r1: true, "prop-a1b2c3d4e5": true } : {};
    const r = resolveScope(scope, els, sel, { frameId: "tbpage-1" });
    assert.ok(!r.elements.some(isAnchor), scope);
    assert.ok(r.elements.some((e) => e.id === "r1"), scope);
  }
  assert.ok(!resolveScope("selection", els, { "prop-a1b2c3d4e5": true }).elements.some(isAnchor), "selecting only the anchor falls back without it");
  assert.equal(expandSelection(els, { r1: true }).length, 1);
});

test("neon r1 #1: a stale phone sheet's 'keep both' says what it meant (proposed), so the server refuses it after someone applied", () => {
  const after = rec({ status: "applied" });   // the card changed while the sheet was open
  assert.equal(actionBody(after, "keepBoth", "c1").from_status, "proposed");
  assert.equal(actionBody(after, "apply", "c1").from_status, "proposed");
  assert.equal(actionBody(after, "dismiss", "c1").from_status, "proposed");
  assert.equal(actionBody(rec(), "revert", "c1").from_status, "applied");
  assert.equal(actionBody(rec(), "keepDone", "c1").from_status, "applied");
});

test("leader r4: the card never covers a result — above the anchor, else below / right / left, else no card (the chip shows)", () => {
  const hitAny = (c, list) => list.some((o) => c.x < o.x + o.w && o.x < c.x + c.w && c.y < o.y + o.h && o.y < c.y + c.h);
  const p = rec({ results: ["n1", "n2", "a1"] });
  const els = [el("n1", { type: "ellipse", x: 312, y: 12, width: 160, height: 80 }), el("n2", { type: "rectangle", x: 312, y: 150, width: 160, height: 80 }),
    el("a1", { type: "arrow", x: 392, y: 92, width: 0, height: 58, points: [[0, 0], [0, 58]] }), el("n1-label", { type: "text", x: 370, y: 40, width: 40, height: 20, containerId: "n1" }),
    el("gone", { x: 312, y: 300, width: 9, height: 9, isDeleted: true })];
  const boxes = resultBoxes(p, els);
  assert.equal(boxes.length, 4, "결과 셋 + 그 안 글");
  assert.deepEqual(boxes.find((b) => b.h === 58), { x: 392, y: 92, w: 0, h: 58 });
  const view = { w: 1280, h: 800 }, w = 330, h = 80;
  // room above the anchor: there
  let a = { x: 300, y: 200, w: 184, h: 254 };
  let at = cardSpot(a, w, h, boxes.map((b) => ({ ...b, y: b.y + 200 })), [], view);
  assert.deepEqual(at, { x: 300, y: 200 - h - 6 });
  // the docs figure: anchor at the top of the screen, so 'above' would be clamped onto the first node — it goes beside instead
  a = { x: 300, y: 0, w: 184, h: 254 };
  at = cardSpot(a, w, h, boxes, [], view);
  assert.ok(at && !hitAny({ ...at, w, h }, boxes), JSON.stringify(at));
  // phone, zoomed in: the result fills the screen — no spot, so no card
  const big = [{ x: 0, y: 0, w: 390, h: 844 }];
  assert.equal(cardSpot({ x: -10, y: -10, w: 410, h: 870 }, 300, 60, big, [], { w: 390, h: 844 }), null);
  // other cards / chips are avoided too
  a = { x: 300, y: 300, w: 184, h: 254 };
  at = cardSpot(a, w, h, [], [{ x: 0, y: 200, w: 1280, h: 100 }], view);
  assert.ok(at && !hitAny({ ...at, w, h }, [{ x: 0, y: 200, w: 1280, h: 100 }]));
});

test("leader r4: on a phone with no room for one row, the card can go in two rows (narrower)", () => {
  for (const st of ["proposed", "applied"]) {
    const m = cardModel(rec({ status: st }), { touch: true });
    const wide = cardSize(m, true), narrow = cardSize(m, true, true);
    assert.ok(narrow.w < wide.w && narrow.w <= 180, `${st}: ${narrow.w} < ${wide.w}`);
    assert.ok(narrow.h > wide.h);
  }
});

test("leader r4: an applied card's phone sheet (from the small ✦ button) offers 원본 ↺ and 확정 only", () => {
  const m = cardModel(rec({ status: "applied" }), { touch: true });
  assert.deepEqual(m.sheet.map(label), ["원본 ↺", "확정"]);
  assert.deepEqual(m.more, []);
  assert.deepEqual(actionBody(rec({ status: "applied" }), "revert", "c1"), { client: "c1", action: "revert", from_status: "applied" });
});

test("leader r4: beside the anchor the card slides down past the top bar", () => {
  const bar = { x: 0, y: 0, w: 390, h: 52 }, res = [{ x: 100, y: 26, w: 140, h: 600 }];
  const at = cardSpot({ x: 94, y: 20, w: 152, h: 620 }, 44, 44, res, [bar], { w: 390, h: 664 });
  assert.deepEqual(at, { x: 252, y: 58 });
});

test("r5 (Fable #2): a card never comes out wider than the screen — too wide gives null, so the layer falls back to two rows, then ✦", () => {
  assert.equal(cardSpot({ x: 68, y: 163, w: 224, h: 954 }, 374, 60, [], [], { w: 360, h: 640 }), null, "place() 자리도 폭 검사");
  const wide = cardModel(rec({ status: "applied", drift: 12, uncertain: [{ text: "a", alts: [] }] }), { touch: true });
  for (const vw of [360, 390]) {
    const one = cardSize(wide, true, false, vw - 16);
    if (one) assert.ok(one.w <= vw - 16, `${vw}: ${one.w}`);
    const two = cardSize(wide, true, true, vw - 16);
    assert.ok(two && two.w <= vw - 16, `${vw}: two rows ${two && two.w}`);
  }
  assert.equal(cardSize(wide, true, false, 374), null, "배지 둘 + 단추 둘은 390 폰 한 줄에 안 들어간다");
  const plain = cardModel(rec(), { touch: true });
  assert.ok(cardSize(plain, true, false, 344), "제안 상태의 고르기는 360 에서도 한 줄");
  assert.deepEqual(cardSize(plain, true), cardSize(plain, true, false, Infinity));
});

test("r5 (Fable minor): when no spot keeps off the result, the ✦ still shows at the screen edge, off the bars", () => {
  const view = { w: 390, h: 664 }, bars = [{ x: 0, y: 0, w: 390, h: 52 }, { x: 0, y: 528, w: 390, h: 136 }];
  const at = edgeSpot(48, bars, view);
  assert.deepEqual(at, { x: 390 - 48 - 8, y: 58 });
  assert.equal(edgeSpot(48, [{ x: 0, y: 0, w: 390, h: 664 }], view), null);
});

test("r5: a result with a huge freedraw does not throw (no spread into Math.min)", () => {
  const big = { id: "huge", type: "freedraw", x: 0, y: 0, width: 10, height: 10, points: Array.from({ length: 150000 }, (_, k) => [k % 500, k % 300]) };
  assert.deepEqual(resultBoxes({ results: ["huge"] }, [big]), [{ x: 0, y: 0, w: 499, h: 299 }]);
});

test("r5 (Fable #3): with two proposals a card keeps off the other's results and the other's chip, not off its own chip", () => {
  const st = { scrollX: 0, scrollY: 0, zoom: { value: 1 } }, view = { w: 1280, h: 800 };
  const hit = (c, o) => c.x < o.x + o.w && o.x < c.x + c.w && c.y < o.y + o.h && o.y < c.y + c.h;
  const A = anchor({ id: "prop-A", x: 300, y: 200, width: 200, height: 300 }, { pid: "A", rid: "ra", results: ["a1"] });
  const B = anchor({ id: "prop-B", x: 300, y: 560, width: 200, height: 200 }, { pid: "B", rid: "rb", results: ["b1"] });
  const els = [A, B, el("a1", { type: "rectangle", x: 312, y: 212, width: 176, height: 276 }), el("b1", { type: "rectangle", x: 312, y: 572, width: 176, height: 176 })];
  const chips = [{ rid: "ra", x: 520, y: 400, w: 700, h: 400 }, { rid: "rb", x: 300, y: 100, w: 400, h: 80 }];   // A's chip fills the right; B's chip sits above B
  const out = layoutCards(anchorsOf(els), els, st, view, { chips, bars: [] });
  const b = out.find((it) => it.p.pid === "B").card;
  assert.ok(!hit(b, { x: 312, y: 212, w: 176, h: 276 }), "A 의 결과를 안 가린다 " + JSON.stringify(b));
  assert.ok(!hit(b, chips[0]), "A 의 칩을 안 가린다");
  const a = out.find((it) => it.p.pid === "A").card;
  assert.ok(!hit(a, b) && !hit(a, { x: 312, y: 572, w: 176, h: 176 }), "카드끼리 · B 결과");
  // the result fills a phone screen: the ✦ still shows on the edge
  const big = anchor({ id: "prop-C", x: -20, y: -20, width: 440, height: 900 }, { pid: "C", rid: "rc", results: ["c1"] });
  const e2 = [big, el("c1", { type: "rectangle", x: -8, y: -8, width: 416, height: 876 })];
  const o2 = layoutCards(anchorsOf(e2), e2, st, { w: 390, h: 664 }, { touch: true, bars: [{ x: 0, y: 0, w: 390, h: 52 }] });
  assert.deepEqual(o2.map((it) => [it.mini, it.card.w, it.card.y >= 52]), [[true, MINI, true]]);
});
