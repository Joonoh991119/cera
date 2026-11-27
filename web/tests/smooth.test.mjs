// node --test tests/smooth.test.mjs -- stroke smoothing (off, weak, strong)
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { smoothStroke, roughness } from "../src/features/ink/smooth.js";

// a shaky curve -- hand tremor (about +-2.5px) with a period of 2-3 points on an arc of radius 80
const shaky = (n = 80) => Array.from({ length: n }, (_, i) => {
  const t = (i / (n - 1)) * Math.PI, r = 80 + 1.6 * Math.sin(i * 2.1) + 1.0 * Math.sin(i * 1.3 + 1);
  return [r * Math.cos(t) - 80, r * Math.sin(t)];
});

test("끔 — 그대로(같은 배열)", () => {
  const p = shaky();
  const r = smoothStroke(p, [], "off");
  assert.equal(r.points, p); assert.equal(r.changed, false);
});

test("약 < 강 — 떨림이 줄어든다, 강이 더", () => {
  const p = shaky();
  const r0 = roughness(p), r1 = roughness(smoothStroke(p, [], "low").points), r2 = roughness(smoothStroke(p, [], "high").points);
  assert.ok(r1 < r0 * 0.6, `약 ${r1} / 원래 ${r0}`);
  assert.ok(r2 < r1 * 0.5, `강 ${r2} / 약 ${r1}`);
});

test("양 끝 점 · 점 수 · 필압 길이를 지킨다", () => {
  const p = shaky(50), pr = p.map((_, i) => (i % 3) / 3);
  for (const lv of ["low", "high"]) {
    const r = smoothStroke(p, pr, lv);
    assert.equal(r.points.length, p.length);
    assert.deepEqual(r.points[0], p[0]); assert.deepEqual(r.points.at(-1), p.at(-1));
    assert.equal(r.pressures.length, p.length);
  }
  assert.deepEqual(smoothStroke(p, [], "high").pressures, [], "펜 굵기 흉내(빈 필압)는 빈 채로");
});

test("약 — 한글 꺾임(ㄱ)이 크게 무너지지 않는다", () => {
  // a corner shape drawn at 2px spacing -- the corner point moves inward by only 1px
  const g = [];
  for (let x = 0; x <= 60; x += 2) g.push([x, 0]);
  for (let y = 2; y <= 60; y += 2) g.push([60, y]);
  const k = g.findIndex((q) => q[0] === 60 && q[1] === 0);
  const r = smoothStroke(g, [], "low").points;
  assert.ok(Math.hypot(r[k][0] - 60, r[k][1]) < 1.0, `꺾인 점 이동 ${Math.hypot(r[k][0] - 60, r[k][1])}`);
});

test("점이 둘 이하면 그대로", () => {
  const p = [[0, 0], [5, 5]];
  assert.equal(smoothStroke(p, [], "high").changed, false);
});
