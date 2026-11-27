// node --test tests/shapes.test.mjs -- 'hold for a shape': closed hand loops -> rectangle · diamond · ellipse, everything else unchanged
// Fixtures are synthetic strokes from tests/fixtures/strokes/gen.mjs (seeded; see the limits written there).
import "./_lang-ko.mjs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { recognizeShape, closeLoop, writingNearby, shapeForStroke, shapeSpec, snapScene, keepScene, SHAPE_MIN_PX } from "../src/features/ink/shapes.js";

const load = (f) => JSON.parse(readFileSync(new URL(`./fixtures/strokes/${f}.json`, import.meta.url))).strokes;
const share = (xs, ok) => xs.filter(ok).length / xs.length;
const pct = (r) => `${(r * 100).toFixed(1)}%`;
/** What the board does with a fixture stroke: shape type, or null (left as drawn) */
const outcome = (x) => {
  const r = recognizeShape(x.points, { zoom: x.zoom });
  if (!r || writingNearby({ x: x.x + r.x, y: x.y + r.y, w: r.width, h: r.height }, x.others || [], 0)) return null;
  return r.type;
};
/** Polyline through corners, n points per segment */
const poly = (V, n = 20) => { const o = []; for (let i = 1; i < V.length; i++) for (let k = 0; k < n; k++) o.push([V[i - 1][0] + (V[i][0] - V[i - 1][0]) * k / n, V[i - 1][1] + (V[i][1] - V[i - 1][1]) * k / n]); o.push(V[V.length - 1]); return o; };
const ring = (r, n, cx = r, cy = r) => Array.from({ length: n }, (_, i) => [cx + r * Math.cos(i / (n - 1) * 2 * Math.PI), cy + r * Math.sin(i / (n - 1) * 2 * Math.PI)]);

for (const type of ["rectangle", "diamond", "ellipse"]) {
  test(`${type}: 40 px 부터, zoom 0.5–2, 흔들림 · 겹침 · 틈(정확히 15% 포함) 에서 맞힘 ≥ 90%`, () => {
    const s = load(type);
    assert.ok(s.length >= 60);
    const groups = [
      ...["wobble", "overlap", "gap"].map((c) => [c, s.filter((x) => x.cond === c)]),
      ...[0.5, 1, 1.5, 2].map((z) => [`zoom ${z}`, s.filter((x) => x.zoom === z)]),
      ["diagram context", s.filter((x) => x.others.length)],
      ["exactly 15%", s.filter((x) => x.edge)],
    ];
    for (const [name, g] of groups) {
      assert.ok(g.length >= 10, name);
      const r = share(g, (x) => outcome(x) === type);
      assert.ok(r >= 0.9, `${type}/${name}: ${pct(r)}`);
    }
    assert.ok(Math.min(...s.map((x) => x.short)) < 45);   // the set really starts near 40 px (shorter side)
  });
}

test("열린 곡선 · 밑줄 · S · 지그재그 · U · 체크 · 화살표는 도형이 되지 않는다(≤ 5%)", () => {
  const s = load("open");
  assert.ok(s.length >= 60);
  assert.ok(share(s, (x) => recognizeShape(x.points)) <= 0.05);
});

test("한글 ㅇ ㅁ ㅂ ㅎ — 글자 40–160 px, zoom 0.5–2, 글 속에서 도형 오변환 ≤ 5%", () => {
  const s = load("hangul"), ctx = s.filter((x) => x.ctx);
  for (const j of ["ieung", "mieum", "bieup", "hieut"]) {
    const g = ctx.filter((x) => x.letter === j);
    assert.ok(g.length >= 40, j);
    const r = share(g, (x) => outcome(x));
    assert.ok(r <= 0.05, `${j}: ${pct(r)}`);
  }
  for (const z of [0.5, 1, 1.5, 2]) assert.ok(share(ctx.filter((x) => x.zoom === z), (x) => outcome(x)) <= 0.05, `zoom ${z}`);
  for (const [a, b] of [[40, 60], [60, 100], [100, 161]]) assert.ok(share(ctx.filter((x) => x.size >= a && x.size < b), (x) => outcome(x)) <= 0.05, `${a}-${b}px`);
  // Without context (the first stroke on a line) a big ㅇ / one-stroke ㅁ is a circle / box: shape alone cannot tell. Reported, and capped against regressions
  const iso = s.filter((x) => !x.ctx), shapeOnly = share(s, (x) => recognizeShape(x.points, { zoom: x.zoom }));
  console.log(`# hangul in words ${pct(share(ctx, (x) => outcome(x)))} (n=${ctx.length}), first on a line ${pct(share(iso, (x) => outcome(x)))} (n=${iso.length}), shape test alone ${pct(shapeOnly)}`);
  assert.ok(share(iso, (x) => outcome(x)) <= 0.5);
});

test("크기 문턱은 계획대로 화면 대각선 60 px(확대 반영)", () => {
  const box = (s) => poly([[0, 0], [s, 0], [s, s], [0, s], [0, 0]], 21);
  assert.equal(recognizeShape(box(64))?.type, "rectangle");   // 64×64 (diagonal 90) — the reviewer's repro
  assert.equal(recognizeShape(box(40)), null);                 // diagonal 57 < 60
  assert.equal(recognizeShape(box(40), { zoom: 2 })?.type, "rectangle");
  assert.equal(recognizeShape(box(64), { zoom: 0.5 }), null);
  assert.equal(SHAPE_MIN_PX, 60);
});

test("열린 획의 꼬리는 자르지 않는다 — 사각형을 닫고 200 px 더 그으면 그대로", () => {
  const tail = poly([[0, 0], [100, 0], [100, 100], [0, 100], [0, 0], [-200, 0]]);
  assert.equal(recognizeShape(tail), null);
  assert.equal(closeLoop(tail), null);
  const arc = (sweep) => Array.from({ length: 100 }, (_, i) => [100 * Math.cos(i / 99 * sweep), 100 * Math.sin(i / 99 * sweep)]);
  assert.ok(closeLoop(arc(2.25 * Math.PI)).length < 100);   // a 12% overshoot is still trimmed
  assert.equal(closeLoop(arc(1.4 * Math.PI)), null);
});

test("세 변만 그린 U 는 열린 채로 — 닫는 가상 선분을 변으로 치지 않는다", () => {
  for (const [w, h] of [[80, 200], [60, 200], [200, 80]]) assert.equal(recognizeShape(poly([[0, 0], [0, h], [w, h], [w, 0]])), null, `${w}x${h}`);
});

test("빠른 터치처럼 점이 드문 획도 인식(꼭짓점 + 시작점 6점)", () => {
  assert.equal(recognizeShape([[0, 0], [100, 0], [100, 100], [0, 100], [0, 0], [0, 0]])?.type, "rectangle");
  assert.equal(recognizeShape([[0, 0], [100, 0], [100, 100], [0, 100], [0, 2]])?.type, "rectangle");
});

test("한 획 판정 < 5 ms — 100,000 점 원(긴 획은 줄여서 본다)", () => {
  const big = ring(100, 100000);
  for (let i = 0; i < 12; i++) recognizeShape(big);
  const ts = [];
  for (let i = 0; i < 31; i++) { const t0 = performance.now(); recognizeShape(big); ts.push(performance.now() - t0); }
  ts.sort((a, b) => a - b);
  assert.ok(ts[15] < 5, `median ${ts[15].toFixed(2)} ms`);
  assert.equal(recognizeShape(big)?.type, "ellipse");
});

test("글쓰기 판정 — 옆 · 아래 최근 획은 글, 안의 글 · 닿는 화살표 · 오래된 획은 아님", () => {
  const box = { x: 100, y: 100, w: 60, h: 60 };
  const beside = { x: 175, y: 95, w: 2, h: 70, t: -400 };
  assert.ok(writingNearby(box, [beside], 0));
  assert.ok(!writingNearby(box, [{ ...beside, t: -3000 }], 0));                       // long ago
  assert.ok(!writingNearby(box, [{ x: 115, y: 125, w: 30, h: 8, t: -300 }], 0));      // a label inside: a box drawn around it
  assert.ok(!writingNearby(box, [{ x: 0, y: 125, w: 100, h: 12, t: -300 }], 0));      // an arrow ending at the left side
  assert.ok(writingNearby(box, [{ x: 95, y: 90, w: 70, h: 2, t: -300 }], 0));         // a bar just above (ㅎ): lies across, not along
});

const free = (o = {}) => ({ id: "s", type: "freedraw", x: 10, y: 20, points: poly([[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]), strokeColor: "#e03131", strokeWidth: 2, opacity: 80,
  frameId: "f1", version: 5, versionNonce: 1, isDeleted: false, updated: 1000, ...o });
const opts = { rand: () => 0.5, now: 5000 };

test("도형 요소 — 색 · 불투명도 · 굵기(그대로) · frameId 를 획에서, roughness 0", () => {
  const r = recognizeShape(free().points);
  const sp = shapeSpec(free(), r, { round: true });
  assert.deepEqual([sp.type, sp.x, sp.y, sp.width, sp.height], ["rectangle", 10, 20, 100, 100]);
  assert.deepEqual([sp.strokeColor, sp.opacity, sp.strokeWidth, sp.roughness, sp.frameId], ["#e03131", 80, 2, 0, "f1"]);
  assert.deepEqual(sp.roundness, { type: 3 });
  assert.equal(shapeSpec(free(), { ...r, type: "ellipse" }, { round: true }).roundness, null);
});

test("바꾸기 — 한 번의 장면 변경: 획 isDeleted + version+1, 도형 추가", () => {
  const other = { id: "o", version: 3 };
  const next = snapScene([other, free()], "s", { id: "R", type: "rectangle", version: 1, isDeleted: false }, opts);
  assert.equal(next.length, 3);
  assert.equal(next[0], other);
  assert.deepEqual([next[1].isDeleted, next[1].version, next[1].updated], [true, 6, 5000]);
  assert.notEqual(next[1].versionNonce, 1);
  assert.equal(next[2].id, "R");
});

test("손그림으로 두기 — 둘 다 version+1, 누가 그 뒤에 지웠거나 옮겼거나 되돌렸으면 아무것도 안 함", () => {
  const shape = { id: "R", type: "rectangle", version: 1, versionNonce: 9, isDeleted: false };
  const after = snapScene([free()], "s", shape, opts);
  const snap = { stroke: "s", shape: "R", strokeV: 6, shapeV: 1 };
  const back = keepScene(after, snap, opts);
  assert.deepEqual(back.map((e) => [e.id, e.isDeleted, e.version]), [["s", false, 7], ["R", true, 2]]);
  // B deleted the shape: A's button must not bring the stroke back
  const bDeleted = after.map((e) => (e.id === "R" ? { ...e, isDeleted: true, version: 2 } : e));
  assert.equal(keepScene(bDeleted, snap, opts), null);
  // B moved the shape
  assert.equal(keepScene(after.map((e) => (e.id === "R" ? { ...e, x: 50, version: 2 } : e)), snap, opts), null);
  // Ctrl+Z already brought the stroke back
  assert.equal(keepScene(after.map((e) => (e.id === "s" ? { ...e, isDeleted: false, version: 7 } : e.id === "R" ? { ...e, isDeleted: true, version: 2 } : e)), snap, opts), null);
  assert.equal(keepScene(back, snap, opts), null);   // a second press does nothing
});

test("판정 전체(shapeForStroke) — 방금 쓴 글자 옆 고리는 그대로, 혼자면 도형", () => {
  const ring1 = free({ id: "o", points: ring(40, 60, 0, 0), x: 300, y: 300, updated: 10000 });
  const letter = free({ id: "a", points: poly([[0, -40], [0, 40]]), x: 355, y: 300, updated: 9600 });
  assert.equal(shapeForStroke(ring1, [ring1], { t0: 9800 })?.type, "ellipse");
  assert.equal(shapeForStroke(ring1, [ring1, letter], { t0: 9800 }), null);
  assert.equal(shapeForStroke(ring1, [ring1, { ...letter, isDeleted: true }], { t0: 9800 })?.type, "ellipse");
});

test("순서도 — 다른 상자 안의 라벨 · 상자 앞 25 px 에서 끝난 화살표 · 상자 옆 작은 'A' 뒤에도 도형이 된다", () => {
  const t0 = 20000;
  const box2 = free({ id: "b2", x: 300, y: 100, points: poly([[0, 0], [160, 0], [160, 100], [0, 100], [0, 0]]), updated: t0 + 500 });
  const box1 = { id: "b1", type: "rectangle", x: 100, y: 100, width: 160, height: 100, isDeleted: false };
  const label = free({ id: "l", x: 140, y: 140, points: poly([[0, 0], [60, 4], [80, 20]]), updated: t0 - 300 });     // (a) inside box1, 40 px from box2
  const arrow = free({ id: "a", x: 175, y: 150, points: poly([[0, 0], [100, 0], [88, -8], [100, 0], [88, 8]]), updated: t0 - 300 });   // (b) ends 25 px before box2
  const letterA = free({ id: "A", x: 270, y: 60, points: poly([[0, 20], [8, 0], [16, 20], [12, 12], [4, 12]]), updated: t0 - 300 });  // (c) a small 'A' up-left
  for (const near of [[box1, label], [arrow], [letterA]]) assert.equal(shapeForStroke(box2, [box2, ...near], { t0 })?.type, "rectangle", near.map((e) => e.id).join("+"));
  // a label written in the box2 spot's own row at letter size, but not inside a shape, still counts as writing
  const word = free({ id: "w", x: 250, y: 110, points: poly([[0, 0], [0, 80]]), updated: t0 - 300 });
  assert.equal(shapeForStroke(box2, [box2, word], { t0 }), null);
  assert.equal(shapeForStroke(box2, [box2, box1, { ...word, x: 150 }], { t0 })?.type, "rectangle");   // the same stroke inside box1 is a label
});

test("상자 안에 쓴 한글(ㅇ ㅁ ㅂ ㅎ, 맥락 있음 · 없음 모두)은 도형이 되지 않는다 — 0%", () => {
  const s = load("hangul");
  let snapped = 0;
  for (const x of s) {
    const el = free({ id: "k", x: x.x, y: x.y, points: x.points, updated: 0 });
    const r = recognizeShape(x.points, { zoom: x.zoom });
    const b = r ? { x: x.x + r.x, y: x.y + r.y, w: r.width, h: r.height } : { x: x.x, y: x.y, w: 1, h: 1 };
    const pad = Math.max(b.w, b.h) * 0.6;
    const around = { id: "box", type: ["rectangle", "ellipse", "diamond"][snapped % 3], x: b.x - pad, y: b.y - pad * 0.5, width: b.w + 2 * pad, height: b.h + pad, isDeleted: false };
    if (shapeForStroke(el, [el, around], { zoom: x.zoom, t0: 0 })) snapped++;
  }
  assert.equal(snapped, 0, `${snapped}/${s.length}`);
});

test("라벨을 먼저 쓰고 둘레에 상자 · 화살표 끝에 상자는 여전히 도형", () => {
  const t0 = 30000;
  const box = free({ id: "bx", x: 300, y: 100, points: poly([[0, 0], [160, 0], [160, 100], [0, 100], [0, 0]]), updated: t0 + 500 });
  const label = free({ id: "lb", x: 340, y: 140, points: poly([[0, 0], [50, 6], [70, 20]]), updated: t0 - 300 });
  const arrow = free({ id: "ar", x: 200, y: 150, points: poly([[0, 0], [100, 0], [88, -8], [100, 0], [88, 8]]), updated: t0 - 300 });
  assert.equal(shapeForStroke(box, [box, label], { t0 })?.type, "rectangle");
  assert.equal(shapeForStroke(box, [box, arrow], { t0 })?.type, "rectangle");
  assert.equal(shapeForStroke(box, [box, label, arrow], { t0 })?.type, "rectangle");
  // the same box drawn inside a bigger existing box is a label frame inside it: left alone
  const outer = { id: "o", type: "rectangle", x: 250, y: 60, width: 300, height: 200, isDeleted: false };
  assert.equal(shapeForStroke(box, [box, outer], { t0 }), null);
});
