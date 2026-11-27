// node --test tests/mermaidstyle.test.mjs -- mermaid output with straight lines and the app's body font, with bound text re-measured
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { restyleMermaid } from "../src/features/flow/mermaidStyle.js";

const rand = () => 0.5;
const base = { version: 1, versionNonce: 1, isDeleted: false, angle: 0 };
const rect = { ...base, id: "A", type: "rectangle", x: 0, y: 0, width: 100, height: 40, roughness: 1, boundElements: [{ type: "text", id: "tA" }] };
const arrow = { ...base, id: "e", type: "arrow", x: 50, y: 40, width: 0, height: 60, roughness: 1, points: [[0, 0], [0, 60]], boundElements: [{ type: "text", id: "tE" }] };
const tA = { ...base, id: "tA", type: "text", containerId: "A", text: "입력", originalText: "입력", fontSize: 16, fontFamily: 5, strokeColor: "#1e1e1e", textAlign: "center", verticalAlign: "middle", x: 30, y: 10, width: 40, height: 20 };
const tE = { ...tA, id: "tE", containerId: "e", text: "예", originalText: "예" };
const old = { ...base, id: "old", type: "rectangle", x: 500, y: 0, width: 10, height: 10, roughness: 1 };
// fake convert -- with the new font (6), text width = character count x 20, centred in the box. If taller than the box, the box grows
const convert = (sk, opts) => {
  assert.equal(opts.regenerateIds, false);
  const c = sk[0], lines = c.label.text.length > 6 ? 3 : 1, h = lines * 20, w = Math.min(c.label.text.length * 20, 80);
  const H = c.points ? c.height : Math.max(c.height, h + 10);
  return [{ ...c, height: H, label: undefined }, { id: "x", type: "text", text: lines > 1 ? "wrapped" : c.label.text, fontFamily: c.label.fontFamily, lineHeight: 1.25,
    x: c.x + (c.width - w) / 2, y: c.y + (H - h) / 2, width: w, height: h }];
};

test("새 요소만 — 선은 roughness 0, 글은 본문 글꼴(6)로 다시 재고 상자 가운데, 옛 요소는 그대로", () => {
  const out = restyleMermaid([old, rect, arrow, tA, tE], ["A", "e", "tA", "tE"], 6, convert, rand);
  const m = new Map(out.map((e) => [e.id, e]));
  assert.equal(m.get("A").roughness, 0); assert.equal(m.get("e").roughness, 0); assert.equal(m.get("old").roughness, 1);
  assert.equal(m.get("old"), old, "옛 요소는 같은 객체");
  const a = m.get("tA");
  assert.equal(a.fontFamily, 6); assert.equal(a.id, "tA"); assert.equal(a.containerId, "A"); assert.ok(a.version > 1);
  assert.equal(a.x + a.width / 2, 50, "상자 가운데(가로)"); assert.equal(a.y + a.height / 2, 20, "상자 가운데(세로)");
  assert.equal(m.get("tE").fontFamily, 6);
});
test("글이 길어 줄이 늘면 상자 높이를 늘린다 — 글은 상자 안", () => {
  const long = { ...tA, text: "아주 긴 판단 문장", originalText: "아주 긴 판단 문장" };
  const out = restyleMermaid([rect, long], ["A", "tA"], 6, convert, rand);
  const b = out.find((e) => e.id === "A"), t = out.find((e) => e.id === "tA");
  assert.ok(b.height >= t.height, `${b.height} ≥ ${t.height}`);
  assert.ok(t.y >= b.y && t.y + t.height <= b.y + b.height, "글이 상자 안");
  assert.ok(t.x >= b.x && t.x + t.width <= b.x + b.width);
});
test("바꿀 것이 없으면 null, convert 가 실패해도 글꼴은 바꾼다", () => {
  assert.equal(restyleMermaid([old], ["zz"], 6, convert, rand), null);
  const out = restyleMermaid([rect, tA], ["tA"], 6, () => { throw new Error("x"); }, rand);
  assert.equal(out.find((e) => e.id === "tA").fontFamily, 6);
});

test("새 글꼴로 낱말이 쪼개지면(마름모 '판단' → '판\\n단') 글자를 줄여 한 줄로 — 칸 크기 · 자리는 그대로(붙은 화살표 유지)", () => {
  const dia = { ...base, id: "D", type: "diamond", x: 0, y: 0, width: 60, height: 60, roughness: 1 };
  const t = { ...tA, id: "tD", containerId: "D", text: "판단", originalText: "판단", fontSize: 20 };
  // fake convert -- text width = character count x size; if wider than the width inside the diamond (width/2 = 30), it is split one character at a time
  const cv = (sk) => { const c = sk[0], fs = c.label.fontSize, w0 = 2 * fs; const split = w0 > c.width / 2; const h = split ? 2 * fs : fs, w = split ? fs : w0;
    return [{ ...c, label: undefined }, { id: "x", type: "text", text: split ? "판\n단" : "판단", fontSize: fs, fontFamily: 6, lineHeight: 1.25, x: c.x + (c.width - w) / 2, y: c.y + (c.height - h) / 2, width: w, height: h }]; };
  const out = restyleMermaid([dia, t], ["D", "tD"], 6, cv, rand);
  const d = out.find((e) => e.id === "D"), x = out.find((e) => e.id === "tD");
  assert.equal(x.text, "판단", "한 줄");
  assert.equal(x.fontSize, 14, "20 → 18 → 16 → 14 에서 처음 들어감");
  assert.equal(d.width, 60); assert.equal(d.x, 0);
});
