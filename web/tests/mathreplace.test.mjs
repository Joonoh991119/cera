// node --test tests/mathreplace.test.mjs -- editing a formula keeps the same element (size, colour, group, and arrows are kept)
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { replaceMath, naturalSize, mapPoint, adjustArrow, colorFromSvg, dataUrlText } from "../src/features/math/replace.js";
import { splitImageSwaps } from "../src/features/math/hold.js";

const rand = () => 0.5;
const math = (o = {}) => ({ id: "m", type: "image", x: 100, y: 100, width: 200, height: 80, angle: 0, version: 3, versionNonce: 1, isDeleted: false,
  fileId: "f0", status: "saved", groupIds: ["g1"], frameId: null, boundElements: [{ id: "a", type: "arrow" }],
  customData: { latex: "x", size: 32, color: "#E31A1C", w0: 100, h0: 40 }, ...o });
const arrow = (o = {}) => ({ id: "a", type: "arrow", x: 0, y: 140, width: 98, height: 0, points: [[0, 0], [98, 0]], version: 2, versionNonce: 5, isDeleted: false,
  startBinding: null, endBinding: { elementId: "m", focus: 0, gap: 2 }, ...o });

test("같은 id · 늘린 비율 · 색 · 그룹 · boundElements 유지, version 올림", () => {
  const out = replaceMath([math(), arrow()], "m", { fileId: "f1", latex: "y", size: 32, color: "#E31A1C", w: 120, h: 40 }, null, rand);
  const m = out.find((e) => e.id === "m");
  assert.equal(out.length, 2, "새 요소를 만들지 않는다");
  assert.equal(m.fileId, "f1");
  assert.equal(m.width, 240, "두 배 늘린 비율(200/100)을 새 식에도");
  assert.equal(m.height, 80);
  assert.deepEqual([m.x, m.y], [100, 100]);
  assert.deepEqual(m.groupIds, ["g1"]);
  assert.deepEqual(m.boundElements, [{ id: "a", type: "arrow" }]);
  assert.equal(m.version, 4);
  assert.deepEqual(m.customData, { latex: "y", size: 32, color: "#E31A1C", w0: 120, h0: 40 });
  assert.deepEqual(naturalSize(m), { w: 120, h: 40 });
});

test("옛 요소(w0 없음) — 밖에서 잰 조판 크기로 비율을, 그것도 없으면 1배", () => {
  const old = math({ customData: { latex: "x", size: 32 } });
  let m = replaceMath([old], "m", { fileId: "f1", latex: "y", size: 32, color: "#000000", w: 50, h: 20 }, { w: 100, h: 40 }, rand)[0];
  assert.deepEqual([m.width, m.height], [100, 40]);
  m = replaceMath([old], "m", { fileId: "f1", latex: "y", size: 32, color: "#000000", w: 50, h: 20 }, null, rand)[0];
  assert.deepEqual([m.width, m.height], [50, 20]);
});

test("지워진 · 없는 요소는 null", () => {
  assert.equal(replaceMath([math({ isDeleted: true })], "m", { fileId: "f", w: 1, h: 1 }), null);
  assert.equal(replaceMath([], "m", { fileId: "f", w: 1, h: 1 }), null);
});

test("붙은 화살표 끝이 새 상자를 따라간다(틈 유지) — 왼쪽에서 오는 화살표", () => {
  // the arrow ends at the left of the box (x=98). Even if the box gets wider, the left end stays put.
  let out = replaceMath([math(), arrow()], "m", { fileId: "f1", latex: "y", size: 32, color: "#E31A1C", w: 150, h: 40 }, null, rand);
  assert.equal(out[1].version, 2, "왼쪽 틈에 끝난 화살표는 그대로(상자 왼쪽은 안 움직인다)");
  // an arrow coming from the right: end x=302 (box right 300 + gap 2) -> if the width goes 200->300, it becomes 402
  const right = arrow({ x: 500, points: [[0, 0], [-198, 0]] });
  out = replaceMath([math(), right], "m", { fileId: "f1", latex: "y", size: 32, color: "#E31A1C", w: 150, h: 40 }, null, rand);
  const a = out[1];
  assert.deepEqual(a.points[1], [-98, 0]);
  assert.equal(a.version, 3, "옮긴 화살표도 version 을 올린다");
  // from below to the top centre: end (200, 182) -> box bottom 180 + 2 -> height 80->120 (w0 40 -> h 60 x2)
  const below = arrow({ x: 200, y: 300, points: [[0, 0], [0, -118]], endBinding: { elementId: "m", focus: 0, gap: 2 } });
  out = replaceMath([math(), below], "m", { fileId: "f1", latex: "y", size: 32, color: "#E31A1C", w: 100, h: 60 }, null, rand);
  assert.deepEqual(out[1].points[1], [0, -78], "가운데 x 는 비율대로(200 그대로), y 는 늘어난 만큼 아래로");
});

test("시작 끝이 붙은 화살표 — 원점을 옮기고 나머지 점은 제자리", () => {
  const a = arrow({ x: 302, y: 140, points: [[0, 0], [100, 0]], startBinding: { elementId: "m", focus: 0, gap: 2 }, endBinding: null });
  const p = adjustArrow(a, "m", { x: 100, y: 100, width: 200, height: 80 }, { x: 100, y: 100, width: 300, height: 80 });
  assert.equal(p.x, 402); assert.equal(p.y, 140);
  assert.deepEqual(p.points, [[0, 0], [0, 0]]);
});

test("꺾인 화살표 — 끝을 옮기면 이웃 점도 직각을 지킨다", () => {
  const a = arrow({ elbowed: true, x: 0, y: 0, points: [[0, 0], [50, 0], [50, 150], [98, 150]] });
  // the end (98,150) is in the gap at the box's left. Box height 80->160: y=150 is inside the box (100..180) -> 100+50*2=200
  const p = adjustArrow(a, "m", { x: 100, y: 100, width: 200, height: 80 }, { x: 100, y: 100, width: 200, height: 160 });
  assert.deepEqual(p.points[3], [98, 200]);
  assert.deepEqual(p.points[2], [50, 200], "가로 마지막 칸이 가로로 남는다");
});

test("꺾인 화살표 — 붙은 끝의 fixedPoint 를 새 상자 기준으로 다시 잰다(틈이 커지지 않게)", () => {
  // an end attached in the 5px gap at the left of a small formula (20x16) -- keeping the old ratio -0.25 would grow it to 244px and leave the end 61px away
  const a = arrow({ elbowed: true, x: 600, y: 308, points: [[0, 0], [95, 0]], endBinding: { elementId: "m", focus: 0, gap: 5, fixedPoint: [-0.25, 0.5] } });
  const p = adjustArrow(a, "m", { x: 700, y: 300, width: 20, height: 16 }, { x: 700, y: 300, width: 244, height: 100 });
  const [fx, fy] = p.endBinding.fixedPoint;
  assert.ok(Math.abs(700 + fx * 244 - 695) < 1e-9, "끝 x = 상자 왼쪽 5px 앞 그대로");
  assert.ok(Math.abs(300 + fy * 100 - (p.y + p.points[1][1])) < 1e-9, "끝 y = 옮긴 끝과 같다(비율 0.5 → 가운데)");
  assert.equal(p.endBinding.elementId, "m"); assert.equal(p.endBinding.gap, 5);
  const straight = adjustArrow(arrow({ x: 500, points: [[0, 0], [-198, 0]] }), "m", { x: 100, y: 100, width: 200, height: 80 }, { x: 100, y: 100, width: 300, height: 80 });
  assert.equal(straight.endBinding.fixedPoint, undefined, "곧은 화살표(fixedPoint 없음)는 바인딩을 그대로");
});

test("안 붙은 화살표 · 돌린 수식은 건드리지 않는다", () => {
  const free = arrow({ endBinding: null });
  let out = replaceMath([math(), free], "m", { fileId: "f1", w: 150, h: 40 }, null, rand);
  assert.equal(out[1], free);
  out = replaceMath([math({ angle: 0.5 }), arrow()], "m", { fileId: "f1", w: 150, h: 40 }, null, rand);
  assert.equal(out[1].version, 2);
});

test("mapPoint — 상자 앞 · 안 · 뒤", () => {
  const b0 = { x: 0, y: 0, width: 100, height: 100 }, b1 = { x: 0, y: 0, width: 200, height: 50 };
  assert.deepEqual(mapPoint([-5, -5], b0, b1), [-5, -5]);
  assert.deepEqual(mapPoint([50, 50], b0, b1), [100, 25]);
  assert.deepEqual(mapPoint([105, 105], b0, b1), [205, 55]);
});

test("옛 수식 그림에서 색 읽기", () => {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg"><g stroke="#E31A1C" fill="#E31A1C"><path d=""/></g></svg>';
  const url = "data:image/svg+xml;base64," + Buffer.from(svg).toString("base64");
  assert.equal(colorFromSvg(dataUrlText(url)), "#E31A1C");
  assert.equal(colorFromSvg("<svg/>"), null);
  assert.equal(dataUrlText("data:image/svg+xml," + encodeURIComponent(svg)), svg);
});

test("받는 쪽 — 파일 없는 '바꿔 끼우기' 만 붙잡는다", () => {
  const local = new Map([["m", math()], ["n", math({ id: "n", fileId: "zz" })]]);
  const have = (f) => f === "f0";
  const swap = math({ fileId: "f1", version: 4 });
  const fresh = math({ id: "new", fileId: "f9", version: 1 });
  const moved = math({ version: 4 });               // same file, only the position
  const older = math({ fileId: "f1", version: 2 });   // mine is newer -- reconcile discards it
  const noOld = math({ id: "n", fileId: "f1", version: 9 });   // the old file is gone too -- holding on to it is useless
  const { now, held } = splitImageSwaps([swap, fresh, moved, older, noOld, arrow()], local, have);
  assert.deepEqual(held.map((e) => e.id), ["m"]);
  assert.equal(now.length, 5);
});

test("고칠 수식 고르기 — 그룹째 선택돼도 수식이 하나면 그것, 두 번 누르면 누른 자리의 것", async () => {
  const { selectedMath } = await import("../src/features/math/select.js");
  const m1 = math({ id: "m1" }), m2 = math({ id: "m2", x: 400 }), a = arrow();
  assert.equal(selectedMath([m1, a], { m1: true, a: true }).id, "m1");
  assert.equal(selectedMath([m1, m2], { m1: true, m2: true }), null, "둘이면 새로 넣기");
  assert.equal(selectedMath([m1, m2], { m1: true, m2: true }, { x: 450, y: 120 }).id, "m2");
  assert.equal(selectedMath([m1], {}, { x: 150, y: 120 }), null, "선택 안 된 것은 아님");
});

test("리뷰 F2 — 붙잡힌 고침 위에 이쪽 옮김을 다시 쌓는다: 새 fileId · 식 + 이쪽 자리 · 크기, version 은 둘보다 크게", async () => {
  const { rebaseHeld } = await import("../src/features/math/hold.js");
  const held = math({ fileId: "f2", version: 6, customData: { latex: "y", size: 32, color: "#E31A1C", w0: 120, h0: 40 } });
  const base = math({ fileId: "f1", version: 5 });   // this side's version 200x80 at the time of grabbing
  const mine = math({ fileId: "f1", version: 9, x: 500, y: 300, width: 300, height: 120 });   // this side dragged it and scaled it 1.5x (v5->v9)
  const held2 = { ...held, width: 240, height: 80 };   // the new size after the fix
  const r = rebaseHeld(mine, held2, base, () => 0.25);
  assert.equal(r.fileId, "f2"); assert.equal(r.customData.latex, "y");
  assert.deepEqual([r.x, r.y, r.width, r.height], [500, 300, 360, 120], "자리는 이쪽, 크기는 고침 × 이쪽 비율");
  const onlyMoved = rebaseHeld(math({ fileId: "f1", version: 7, x: 9 }), held2, base);
  assert.deepEqual([onlyMoved.x, onlyMoved.width, onlyMoved.height], [9, 240, 80], "옮기기만 — 고침의 크기 그대로");
  assert.equal(r.version, 10);
  assert.equal(rebaseHeld(math({ version: 3 }), held).version, 7, "이쪽이 더 낮아도 고침보다 크게");
});

test("리뷰 S2 — color 는 #hex 만", async () => {
  // insert.js imports Excalidraw, so the same regular expression is checked separately
  const isHex = (c) => typeof c === "string" && /^#[0-9a-fA-F]{3,8}$/.test(c);
  for (const ok of ["#fff", "#E31A1C", "#11223344"]) assert.ok(isHex(ok), ok);
  for (const bad of ['"/><foreignObject>', "url(https://x)", "red", "#12", null, undefined, 3]) assert.ok(!isHex(bad), String(bad));
  const src = (await import("node:fs")).readFileSync(new URL("../src/features/math/insert.js", import.meta.url), "utf8");
  assert.ok(src.includes('/^#[0-9a-fA-F]{3,8}$/'), "insert.js 의 isHex 가 같은 규칙");
});
