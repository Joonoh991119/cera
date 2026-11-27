// node --test tests/hlcolor.test.mjs -- is the highlighter colour visible on both light and dark backgrounds (contrast calculation)
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { HL_COLORS, HL_OPACITY, HL_OLD, migrateHl, hlScores, darkFilter, contrast } from "../src/features/ink/hlcolor.js";

test("어두운 화면 필터 — 흰 바탕은 (18,18,18) 로 그려진다(Excalidraw 어두운 바탕)", () => {
  assert.deepEqual(darkFilter([255, 255, 255]).map(Math.round), [18, 18, 18]);
});
test("옛 노랑(#fcc419 · 40%)은 어두운 화면 대비가 1.3 정도 — 고치기 전 상태 재현(r1 U5)", () => {
  const s = hlScores("#fcc419", 0.4);
  assert.ok(s.dark < 1.4, `dark ${s.dark}`);
});
test("새 형광 색 셋 — 어두운 화면 띠 대비 ≥ 1.8, 밝은 화면 형광 위 검은 글 대비 ≥ 4.5", () => {
  assert.equal(HL_OPACITY, 50);
  for (const [c, label] of HL_COLORS) {
    const s = hlScores(c);
    assert.ok(s.dark >= 1.8, `${label} ${c} dark ${s.dark.toFixed(2)}`);
    assert.ok(s.light >= 4.5, `${label} ${c} light ${s.light.toFixed(2)}`);
  }
});
test("밝은 화면에서 이름대로 읽힌다 — 노랑은 R≈G>B, 연두는 G 가 가장 큼, 분홍은 R 이 가장 크고 B>G", () => {
  const [y, g, p] = HL_COLORS.map(([c]) => hlScores(c).lightBand);
  assert.ok(y[0] > y[2] + 60 && y[1] > y[2] + 40, `노랑 ${y}`);
  assert.ok(g[1] > g[0] && g[1] > g[2], `연두 ${g}`);
  assert.ok(p[0] > p[1] && p[2] > p[1], `분홍 ${p}`);
});
test("저장해 둔 옛 형광 색은 새 색으로, 그 밖은 그대로", () => {
  for (const [o, n] of Object.entries(HL_OLD)) assert.equal(migrateHl(o.toUpperCase()), n);
  assert.equal(migrateHl("#123456"), "#123456");
  assert.ok(contrast([0, 0, 0], [255, 255, 255]) > 20);
});
