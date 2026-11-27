// node --test tests/mathpalette.test.mjs -- the symbol palette, recent formulas, and this board's formulas
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { insertSnippet, nextSlot, pushRecent, loadRecent, saveRecent, boardFormulas, TEMPLATES, SYMBOLS, RECENT_KEY } from "../src/features/math/palette.js";

const frac = TEMPLATES.find((t) => t.tex === "\\frac{□}{□}");
const sup = TEMPLATES.find((t) => t.tex === "^{□}");

test("분수 — 커서 자리에 넣고 첫 □ 를 고른다, 바로 치면 \\frac{a}{□}", () => {
  const r = insertSnippet("x = ", 4, 4, frac);
  assert.equal(r.text, "x = \\frac{□}{□}");
  assert.equal(r.text.slice(r.start, r.end), "□");
  assert.equal(r.start, 10);
  const typed = r.text.slice(0, r.start) + "a" + r.text.slice(r.end);
  assert.equal(typed, "x = \\frac{a}{□}");
  assert.equal(nextSlot(typed, r.start + 1), 13);
});

test("고른 글은 첫 □ 에 감싼다", () => {
  const r = insertSnippet("1+x", 2, 3, frac);
  assert.equal(r.text, "1+\\frac{x}{□}");
  assert.equal(r.text.slice(r.start, r.end), "□");
});

test("첨자 — 앞 글자에 붙고, 앞이 비면 밑 칸도", () => {
  let r = insertSnippet("x", 1, 1, sup);
  assert.equal(r.text, "x^{□}"); assert.equal(r.start, 3);
  r = insertSnippet("", 0, 0, sup);
  assert.equal(r.text, "□^{□}"); assert.equal(r.start, 0);
  r = insertSnippet("a = ", 4, 4, sup);
  assert.equal(r.text, "a = □^{□}");
});

test("기호 — 뒤에 빈칸을 붙여 다음 글자와 섞이지 않게, 커서는 끝", () => {
  const alpha = SYMBOLS.find((s) => s.label === "α");
  const r = insertSnippet("x+", 2, 2, alpha);
  assert.equal(r.text, "x+\\alpha ");
  assert.equal(r.start, r.end); assert.equal(r.start, 9);
});

test("다음 □ — 뒤에 없으면 처음부터, 아예 없으면 -1", () => {
  assert.equal(nextSlot("□+□", 1), 2);
  assert.equal(nextSlot("□+x", 1), 0);
  assert.equal(nextSlot("x", 0), -1);
});

test("최근 식 8개 — 겹치면 앞으로, 저장소가 막혀도 오류 없음", () => {
  let l = [];
  for (let i = 0; i < 10; i++) l = pushRecent(l, `x_${i}`);
  assert.equal(l.length, 8); assert.equal(l[0], "x_9");
  l = pushRecent(l, "x_5"); assert.equal(l[0], "x_5"); assert.equal(l.filter((x) => x === "x_5").length, 1);
  const mem = new Map(); const st = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  saveRecent(st, l); assert.deepEqual(loadRecent(st), l);
  mem.set(RECENT_KEY, "{망가짐"); assert.deepEqual(loadRecent(st), []);
  const broken = { getItem: () => { throw new Error("막힘"); }, setItem: () => { throw new Error("막힘"); } };
  assert.deepEqual(loadRecent(broken), []); saveRecent(broken, l);
});

test("이 보드의 식 — 지운 것 빼고, 최근 바뀐 것부터, 겹치지 않게", () => {
  const els = [
    { type: "image", customData: { latex: "a" }, updated: 1 },
    { type: "image", customData: { latex: "b" }, updated: 3 },
    { type: "image", customData: { latex: "a" }, updated: 5 },
    { type: "image", customData: { latex: "c" }, updated: 9, isDeleted: true },
    { type: "text", text: "d" },
  ];
  assert.deepEqual(boardFormulas(els), ["a", "b"]);
});
