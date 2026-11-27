// Formula symbol palette, recent formulas, and this board's formulas. Pure logic only (tests: web/tests/mathpalette.test.mjs).
import { tr } from "../../i18n.js";
// □ is a slot to fill. After insertion the first □ is selected, so typing fills that slot. Tab moves to the next □.

export const SLOT = "□";

// [visible text, TeX to insert, description]
export const SYMBOLS = [
  ["α", "\\alpha "], ["β", "\\beta "], ["γ", "\\gamma "], ["θ", "\\theta "], ["λ", "\\lambda "], ["μ", "\\mu "], ["σ", "\\sigma "],
  ["∑", "\\sum "], ["∫", "\\int "], ["∂", "\\partial "], ["∞", "\\infty "],
  ["≤", "\\le "], ["≥", "\\ge "], ["≠", "\\ne "], ["→", "\\to "],
].map(([label, tex]) => ({ label, tex, title: tex.trim() }));

// attach: forms that attach to the preceding character (superscript, subscript); if nothing precedes, the base slot is also given as □
export const TEMPLATES = [
  { label: "a⁄b", tex: "\\frac{□}{□}", title: tr("분수 \\frac{□}{□}", "Fraction \\frac{□}{□}") },
  { label: "x²", tex: "^{□}", attach: true, title: tr("위첨자 x^{□}", "Superscript x^{□}") },
  { label: "x₂", tex: "_{□}", attach: true, title: tr("아래첨자 x_{□}", "Subscript x_{□}") },
  { label: "√", tex: "\\sqrt{□}", title: tr("제곱근 \\sqrt{□}", "Square root \\sqrt{□}") },
  { label: "𝔼[ ]", tex: "\\mathbb{E}[□]", title: tr("기댓값 \\mathbb{E}[□]", "Expectation \\mathbb{E}[□]") },
  { label: "( 2×2 )", tex: "\\begin{pmatrix} □ & □ \\\\ □ & □ \\end{pmatrix}", title: tr("행렬 2×2", "Matrix 2×2") },
];

export const EXAMPLES = ["\\frac{dS}{dt} = -\\beta S I", "\\hat\\theta = \\arg\\max_\\theta \\, p(y\\mid\\theta)", "\\sum_{i=1}^{n} x_i", "R_0 = \\beta / \\gamma"];

/**
  * Inserts a snippet at [start, end) of text. If text is selected and the snippet has a □, the selected text goes into the first □ (wrapping).
  * Returns { text, start, end }: the new selection (the next □, or the cursor at the end of the snippet if none).
 */
export function insertSnippet(text, start, end, item) {
  const t = String(text ?? "");
  const a = Math.max(0, Math.min(start ?? t.length, t.length)), b = Math.max(a, Math.min(end ?? a, t.length));
  const picked = t.slice(a, b);
  let s = typeof item === "string" ? item : item.tex;
  if (item && item.attach && !picked) {
    const prev = t.slice(0, a);
    if (!prev || /[\s{(&=+\-,]$/.test(prev)) s = SLOT + s;   // no base: □^{□}
  }
  let from = 0;
  if (picked && s.includes(SLOT)) { const i = s.indexOf(SLOT); s = s.slice(0, i) + picked + s.slice(i + 1); from = i + picked.length; }
  const out = t.slice(0, a) + s + t.slice(b);
  const k = s.indexOf(SLOT, from);
  if (k >= 0) return { text: out, start: a + k, end: a + k + 1 };
  return { text: out, start: a + s.length, end: a + s.length };
}

/** Position of the next □ after from (from the start if from is absent). -1 if none */
export function nextSlot(text, from = 0) {
  const t = String(text ?? "");
  const i = t.indexOf(SLOT, from);
  return i >= 0 ? i : t.indexOf(SLOT);
}

// ---- Recent formulas (per person, this browser): localStorage can be blocked (try/catch)
export const RECENT_KEY = "wb-math-recent";
export const RECENT_MAX = 8;
export function pushRecent(list, tex, max = RECENT_MAX) {
  const s = String(tex ?? "").trim();
  if (!s) return list.slice(0, max);
  return [s, ...list.filter((x) => x !== s)].slice(0, max);
}
export function loadRecent(storage) {
  try {
    const v = JSON.parse(storage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string" && x.trim()).slice(0, RECENT_MAX) : [];
  } catch (_) { return []; }
}
export function saveRecent(storage, list) {
  try { storage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX))); } catch (_) { /* storage blocked */ }
}

/** This board's formulas: customData.latex of the scene's formula elements, most recently changed first, without duplicates */
export function boardFormulas(elements, max = 12) {
  const seen = new Set(), out = [];
  const list = (elements || []).filter((e) => e && !e.isDeleted && e.type === "image" && e.customData && typeof e.customData.latex === "string" && e.customData.latex.trim());
  list.sort((x, y) => (y.updated || 0) - (x.updated || 0));
  for (const e of list) {
    const s = e.customData.latex.trim();
    if (seen.has(s)) continue;
    seen.add(s); out.push(s);
    if (out.length >= max) break;
  }
  return out;
}
