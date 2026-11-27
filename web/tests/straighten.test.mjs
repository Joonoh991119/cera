// node --test tests/straighten.test.mjs -- stopping at the end makes a straight line, and merging stroke corrections
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { holdStep, isHeld, canStraighten, straighten, snapEnd, sizeOf, medianStep, HOLD_MS, HOLD_PX } from "../src/features/ink/straighten.js";
import { correctStroke, HL_PRESSURE } from "../src/features/ink/correct.js";

const wobbly = (n = 60, len = 200, amp = 3) => Array.from({ length: n }, (_, i) => [i * len / (n - 1), i === 0 ? 0 : amp * Math.sin(i * 1.7)]);
const free = (o = {}) => ({ id: "f", type: "freedraw", x: 10, y: 20, points: wobbly(), pressures: [], simulatePressure: true, version: 7, versionNonce: 5, isDeleted: false, ...o });

test("머무름 — 문턱 안의 흔들림은 머문 것, 넘으면 기준점을 새로", () => {
  let s = holdStep(null, 100, 100, 0);
  s = holdStep(s, 101, 102, 100);                  // 2.2px -- dwelling
  assert.equal(s.at, 0);
  assert.ok(!isHeld(s, HOLD_MS - 1)); assert.ok(isHeld(s, HOLD_MS));
  s = holdStep(s, 100 + HOLD_PX + 1, 100, 300);    // exceeded -- a new reference
  assert.equal(s.at, 300); assert.ok(!isHeld(s, 300 + HOLD_MS - 1));
});

test("직선 후보 — 흔들린 선은 된다, ㄱ · 동그라미 · 짧은 획은 안 된다", () => {
  assert.ok(canStraighten(wobbly()));
  const giyeok = [[0, 0], [50, 0], [100, 0], [100, 50], [100, 100]];
  assert.ok(!canStraighten(giyeok));
  const circle = Array.from({ length: 40 }, (_, i) => [50 * Math.cos(i / 39 * 2 * Math.PI), 50 * Math.sin(i / 39 * 2 * Math.PI)]);
  assert.ok(!canStraighten(circle));
  assert.ok(!canStraighten([[0, 0], [3, 0.3], [6, 0]]));
  assert.ok(canStraighten([[0, 0], [3, 0.3], [6, 0]], { minLen: 4 }));   // when zoomed in (the scene-unit threshold gets smaller) it triggers
});

// a polyline with 2px spacing -- draws by chaining [ [dx, dy, length], ... ]
const poly = (...legs) => { const p = [[0, 0]]; for (const [dx, dy, L] of legs) for (let d = 2; d <= L; d += 2) { const q = p[p.length - 1]; p.push([q[0] + dx * 2, q[1] + dy * 2]); } return p; };
const sz = { minLen: 28 };   // screen threshold (zoom 1)

test("리뷰 r1 F3 — 짧은 다리 ㄱ · ㄴ, 끝 삐친 ㅡ, 완만한 호는 펴지지 않는다", () => {
  for (const leg of [4, 6]) assert.ok(!canStraighten(poly([1, 0, 30], [0, 1, leg]), sz), `ㄱ 다리 ${leg}`);
  for (const leg of [4, 6]) assert.ok(!canStraighten(poly([0, 1, leg], [1, 0, 30]), sz), `ㄴ 세로 ${leg}`);
  for (const hook of [5, 6]) assert.ok(!canStraighten(poly([1, 0, 40], [0, 1, hook]), sz), `ㅡ 삐침 ${hook}`);
  assert.ok(!canStraighten(poly([1, 0, 40], [-1, 1, 6]), sz), "ㅡ 끝 왼쪽 아래 삐침");
  const arc = Array.from({ length: 19 }, (_, i) => { const t = i / 18; return [36 * t, 24 * t * (1 - t)]; });   // length 36, middle 6
  assert.ok(!canStraighten(arc, sz), "호 36/6");
  assert.ok(!canStraighten(poly([1, 0, 20]), sz), "글자 크기(20px) ㅡ 는 짧아서 아님");
});

test("리뷰 r1 F3 — 손으로 그은 직선은 여전히 펴진다(끝에서 멈춘 동안의 떨림 포함)", () => {
  assert.ok(canStraighten(wobbly(), sz));
  const line = Array.from({ length: 80 }, (_, i) => [i * 3, 0.8 * Math.sin(i * 0.9)]);
  const jitterEnd = [...line, [237.5, 1.5], [238.5, -1], [237, 0.5]];   // the pen tip wobbles 1-2px while stopped
  assert.ok(canStraighten(jitterEnd, sz));
  assert.ok(canStraighten(poly([1, 1, 60]), sz), "사선");
});

test("직선 — 시작점 그대로, 끝점까지 균등, 모든 점이 한 직선 위", () => {
  const pts = wobbly();
  const { points, pressures } = straighten(pts, []);
  assert.deepEqual(points[0], pts[0]);
  assert.deepEqual(points.at(-1), pts.at(-1));
  assert.deepEqual(pressures, []);
  const [a, b] = [points[0], points.at(-1)];
  for (const p of points) assert.ok(Math.abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) < 1e-6);
  const steps = points.slice(1).map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]));
  assert.ok(Math.max(...steps) - Math.min(...steps) < 1e-6);
  // the spacing follows the original stroke spacing (the middle value) -- so the pen-thickness imitation draws at the same thickness
  assert.ok(Math.abs(steps[0] - medianStep(pts)) < medianStep(pts) * 0.2);
});

test("직선 — 진짜 필압은 points 와 같은 길이, 굵기 흐름을 옮긴다", () => {
  const pts = wobbly(30);
  const pr = pts.map((_, i) => 0.2 + 0.6 * i / 29);
  const out = straighten(pts, pr);
  assert.equal(out.pressures.length, out.points.length);
  assert.ok(Math.abs(out.pressures[0] - 0.2) < 1e-6 && Math.abs(out.pressures.at(-1) - 0.8) < 0.02);
});

test("Shift — 15° 단위, 길이 유지", () => {
  const e = snapEnd([0, 0], [100, 8]);          // 4.6° → 0°
  assert.ok(Math.abs(e[0] - Math.hypot(100, 8)) < 1e-5 && Math.abs(e[1]) < 1e-9);
  const f = snapEnd([0, 0], [100, 95]);          // 43.5° → 45°
  assert.ok(Math.abs(Math.atan2(f[1], f[0]) * 180 / Math.PI - 45) < 1e-6);
});

test("보정 합치기 — 멈춤이면 직선, 같은 id · version +1 · 크기 다시 잼", () => {
  const el = free();
  const r = correctStroke(el, { held: true, smooth: "low", rand: () => 0.25 });
  assert.deepEqual(r.what, ["straight"]);
  assert.equal(r.el.id, "f"); assert.equal(r.el.version, 8); assert.equal(r.el.versionNonce, Math.floor(0.25 * 2 ** 31));
  assert.deepEqual({ width: r.el.width, height: r.el.height }, sizeOf(r.el.points));
  assert.equal(r.el.x, 10); assert.equal(r.el.y, 20);
  assert.equal(el.version, 7, "원래 요소는 그대로(새 객체)");
});

test("보정 합치기 — 멈추지 않았으면 매끄럽게만, 끔이면 null", () => {
  assert.deepEqual(correctStroke(free(), { held: false, smooth: "low" }).what, ["smooth"]);
  assert.equal(correctStroke(free(), { held: false, smooth: "off" }), null);
  // a curved stroke (corner shape) is smoothed rather than straightened even when stopped
  const g = free({ points: [[0, 0], [50, 0], [100, 0], [100, 50], [100, 100]] });
  assert.deepEqual(correctStroke(g, { held: true, smooth: "low" }).what, ["smooth"]);
  assert.equal(correctStroke(free({ isDeleted: true }), { held: true }), null);
  assert.equal(correctStroke({ ...free(), type: "line" }, { held: true }), null);
});

test("형광펜 — 필압 흉내 끔 · 고른 필압, 길이는 points 와 같다", () => {
  const r = correctStroke(free(), { smooth: "off", hl: true });
  assert.deepEqual(r.what, ["hl"]);
  assert.equal(r.el.simulatePressure, false);
  assert.equal(r.el.pressures.length, r.el.points.length);
  assert.ok(r.el.pressures.every((p) => p === HL_PRESSURE));
  const s = correctStroke(free(), { held: true, hl: true });
  assert.deepEqual(s.what, ["straight", "hl"]);
  assert.equal(s.el.pressures.length, s.el.points.length);
});

test("빠르기 — 2000점 획 보정이 4ms 안", () => {
  const big = free({ points: wobbly(2000, 3000, 2), pressures: Array(2000).fill(0.4), simulatePressure: false });
  const t0 = performance.now();
  for (let i = 0; i < 10; i++) { correctStroke(big, { held: false, smooth: "high" }); correctStroke(big, { held: true }); }
  const per = (performance.now() - t0) / 20;
  assert.ok(per < 4, `획 하나 ${per.toFixed(2)}ms`);
});
