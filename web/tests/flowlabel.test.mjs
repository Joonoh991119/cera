// node --test tests/flowlabel.test.mjs -- adding, editing, and removing a box's inner (bound) text from the phone text sheet, and cleanup when '+' fails
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { hitNode, labelOf, setLabel, labelId, dblclickNode } from "../src/features/flow/label.js";
import { pruneBound } from "../src/features/flow/connect.js";

const rand = () => 0.5;
const box = (o = {}) => ({ id: "b", type: "rectangle", x: 0, y: 0, width: 100, height: 40, angle: 0, version: 1, versionNonce: 1, isDeleted: false,
  groupIds: ["g"], frameId: null, boundElements: [{ type: "arrow", id: "a1" }], ...o });
// fake convertToExcalidrawElements -- creates a text element when there is a label, and grows the box if the text is long (imitates Excalidraw's redrawTextBoundingBox)
const convert = (sk, opts) => {
  assert.equal(opts.regenerateIds, false);
  const c = sk[0]; const lines = c.label.text.split("\n").length; const h = Math.max(c.height, lines * 30 + 10);
  return [{ ...c, height: h, label: undefined, boundElements: [{ type: "text", id: "new" }] },
    { id: "new", type: "text", x: c.x + 5, y: c.y + (h - lines * 30) / 2, width: 90, height: lines * 30, text: c.label.text, originalText: c.label.text,
      fontSize: c.label.fontSize, fontFamily: c.label.fontFamily, strokeColor: c.label.strokeColor, lineHeight: 1.25, textAlign: "center", verticalAlign: "middle",
      containerId: c.id, angle: 0, version: 1, versionNonce: 2, isDeleted: false, groupIds: [], frameId: null }];
};
const st = { fontSize: 20, fontFamily: 6, strokeColor: "#E31A1C" };

test("상자 찾기 — 맨 위 흐름도 칸, 글 · 지운 것 · 잠근 것은 빼고", () => {
  const els = [box(), box({ id: "c", x: 50 }), { id: "t", type: "text", x: 0, y: 0, width: 200, height: 200 }, box({ id: "d", isDeleted: true }), box({ id: "l", locked: true })];
  assert.equal(hitNode(els, 60, 20).id, "c");
  assert.equal(hitNode(els, 10, 20).id, "b");
  assert.equal(hitNode(els, 500, 500), null);
});

test("새 글 — containerId · boundElements 양쪽, 상자 그룹 · 화살표 참조 유지", () => {
  const out = setLabel([box()], "b", "입력", st, convert, rand);
  const b = out.find((e) => e.id === "b"), t = labelOf(out, b);
  assert.equal(t.containerId, "b"); assert.equal(t.text, "입력"); assert.equal(t.strokeColor, "#E31A1C"); assert.deepEqual(t.groupIds, ["g"]);
  assert.equal(t.id, "b-label", "글 id 는 상자에서 정해진다");
  assert.deepEqual(b.boundElements, [{ type: "arrow", id: "a1" }, { type: "text", id: "b-label" }]);
  assert.equal(b.version, 2); assert.equal(out.length, 2);
});

test("고치기 — 같은 글 요소(id) 를 바꾸고, 길어지면 상자 높이만 늘린다", () => {
  const first = setLabel([box()], "b", "입력", st, convert, rand);
  const out = setLabel(first, "b", "가\n나\n다", { ...st, fontSize: 28 }, convert, rand);
  const b = out.find((e) => e.id === "b"), t = labelOf(out, b);
  assert.equal(out.length, 2, "새 글을 더 만들지 않는다");
  assert.equal(t.id, "b-label"); assert.equal(t.text, "가\n나\n다"); assert.equal(t.fontSize, 28); assert.ok(t.version > 1);
  assert.equal(b.height, 100); assert.equal(b.width, 100);
  assert.equal(b.boundElements.filter((x) => x.type === "text").length, 1, "글 참조는 하나");
});

test("빈 글 — 묶인 글을 지우고 상자의 참조도 걷는다, 글이 없던 상자는 그대로(null)", () => {
  const first = setLabel([box()], "b", "입력", st, convert, rand);
  const out = setLabel(first, "b", "", st, convert, rand);
  assert.equal(out.find((e) => e.id === "b-label").isDeleted, true);
  assert.deepEqual(out.find((e) => e.id === "b").boundElements, [{ type: "arrow", id: "a1" }]);
  assert.equal(setLabel([box()], "b", "", st, convert, rand), null);
  assert.equal(setLabel([box()], "zz", "x", st, convert, rand), null);
});

// same merge as the server and Excalidraw (higher version wins; if equal, the lower versionNonce)
const merge = (...scenes) => { const m = new Map(); for (const sc of scenes) for (const e of sc) { const o = m.get(e.id); if (!o || e.version > o.version || (e.version === o.version && e.versionNonce < o.versionNonce)) m.set(e.id, e); } return [...m.values()]; };

test("r2 F1 — 두 사람이 같은 빈 상자에 동시에 처음 글: 합치면 글 하나, 상자 참조와 맞는다", () => {
  const A = setLabel([box()], "b", "가", st, convert, () => 0.1);
  const B = setLabel([box()], "b", "나", st, convert, () => 0.9);
  const m = merge(A, B);
  const texts = m.filter((e) => e.type === "text" && !e.isDeleted);
  assert.equal(texts.length, 1);
  const b = m.find((e) => e.id === "b");
  assert.deepEqual(b.boundElements.filter((x) => x.type === "text").map((x) => x.id), [texts[0].id]);
  assert.equal(texts[0].containerId, "b");
});

test("r2 F1 — 지운 뒤 다시 넣으면 같은 id 를 되살리고 version 은 계속 오른다", () => {
  let sc = setLabel([box()], "b", "가", st, convert, rand);
  const v1 = sc.find((e) => e.id === "b-label").version;
  sc = setLabel(sc, "b", "", st, convert, rand);
  const v2 = sc.find((e) => e.id === "b-label").version;
  sc = setLabel(sc, "b", "다시", st, convert, rand);
  const t = sc.find((e) => e.id === "b-label");
  assert.ok(v2 > v1 && t.version > v2, `${v1} < ${v2} < ${t.version}`);
  assert.equal(t.isDeleted, false); assert.equal(t.text, "다시");
  assert.equal(sc.filter((e) => e.id === "b-label").length, 1, "같은 id 요소를 둘 만들지 않는다");
  // even when merged with someone else's view (which holds the deleted text), the revived text wins
  const other = setLabel(setLabel([box()], "b", "가", st, convert, rand), "b", "", st, convert, rand);
  assert.equal(merge(other, sc).find((e) => e.id === "b-label").isDeleted, false);
});

test("r2 S1 — 새 글을 넣을 때 상자의 낡은 글 참조는 모두 걷는다", () => {
  const out = setLabel([box({ boundElements: [{ type: "text", id: "dead" }, { type: "arrow", id: "a1" }] })], "b", "가", st, convert, rand);
  assert.deepEqual(out.find((e) => e.id === "b").boundElements, [{ type: "arrow", id: "a1" }, { type: "text", id: "b-label" }]);
});

test("r2 S2 — 글만 고치고 상자 높이가 그대로면 상자 version 은 그대로", () => {
  const first = setLabel([box()], "b", "가", st, convert, rand);
  const v = first.find((e) => e.id === "b").version;
  const out = setLabel(first, "b", "나", st, convert, rand);
  assert.equal(out.find((e) => e.id === "b").version, v);
  const tall = setLabel(first, "b", "1\n2\n3", st, convert, rand);
  assert.ok(tall.find((e) => e.id === "b").version > v, "높이가 늘면 올린다");
});

test("글 id 는 서버 상한(64자) 안", () => {
  assert.ok(labelId("x".repeat(64)).length <= 64);
});

test("'+' 실패 뒷정리 — 없는 요소를 가리키는 boundElements 만 걷는다", () => {
  const b = box({ boundElements: [{ type: "arrow", id: "a1" }, { type: "arrow", id: "ghost" }] });
  const fixed = pruneBound(b, new Set(["b", "a1"]), rand);
  assert.deepEqual(fixed.boundElements, [{ type: "arrow", id: "a1" }]); assert.equal(fixed.version, 2);
  assert.equal(pruneBound(box(), new Set(["b", "a1"]), rand), null, "걷을 것이 없으면 null");
  assert.equal(pruneBound(box({ boundElements: null }), new Set(), rand), null);
});

test("새 글: 상자에 낡은 글 참조가 여럿(이 글 id 포함)이어도 새 것 하나만 남긴다", () => {
  const stale = box({ boundElements: [{ type: "text", id: "b-label" }, { type: "arrow", id: "a1" }, { type: "text", id: "ghost" }] });
  const out = setLabel([stale], "b", "새 글", st, convert, rand);
  const b = out.find((e) => e.id === "b");
  assert.deepEqual(b.boundElements, [{ type: "arrow", id: "a1" }, { type: "text", id: "b-label" }]);
  assert.equal(b.version, 2, "참조를 고쳤으니 상자 version 이 오른다");
  // if the references are already clean, editing only the text does not touch the box
  const again = setLabel(out, "b", "고침", st, convert, rand);
  assert.equal(again.find((e) => e.id === "b").version, 2);
});

test("장(frame) 안 상자를 두 번 누름: 장이 위(z 순서)에 있어도 상자를 찾는다", () => {
  const frame = { id: "tbpage-2", type: "frame", x: -500, y: -500, width: 2000, height: 1200, isDeleted: false };
  const rect = box({ id: "r", x: 100, y: 100, width: 160, height: 80, frameId: "tbpage-2" });
  const els = [rect, frame];   // Excalidraw places a page (frame) after (above) the elements inside it
  assert.equal(dblclickNode(els, {}, 180, 140).id, "r");
  assert.equal(dblclickNode(els, { r: true }, 180, 140).id, "r", "방금 그려 골라진 상자도");
  assert.equal(dblclickNode(els, { "tbpage-2": true }, 180, 140).id, "r", "장이 골라져 있어도");
  assert.equal(dblclickNode(els, {}, 900, 600), null, "장 안 빈 자리 — Excalidraw 에 맡김(자유 글)");
  const free = { id: "t", type: "text", x: 150, y: 120, width: 60, height: 30, isDeleted: false, containerId: null };
  assert.equal(dblclickNode([...els, free], {}, 160, 130), null, "상자 위 자유 글을 누르면 그 글 고치기");
  const arrow = { id: "a", type: "arrow", x: 0, y: 0, width: 300, height: 300, isDeleted: false };
  assert.equal(dblclickNode([...els, arrow], { a: true }, 180, 140), null, "골라진 화살표 — 화살표 글");
  assert.equal(dblclickNode([box({ id: "o", x: 100, y: 100, width: 160, height: 80 })], {}, 180, 140).id, "o", "장 밖 상자도 같다");
});
