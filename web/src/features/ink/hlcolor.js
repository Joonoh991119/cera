// Highlighter colors: pure logic (tests: web/tests/hlcolor.test.mjs).
import { tr } from "../../i18n.js";
// Colors are the same for everyone (element values). On the dark theme Excalidraw draws the whole canvas with invert(93%) hue-rotate(180deg), so
// a bright yellow inverts to a dark brown almost identical to the background (18,18,18) (old #fcc419 at 40% gave 1.3:1). So we pick mid-brightness colors that work on both themes
// and raise opacity to 50%: contrast with black text over the highlight is >= 4.5 on light, and contrast of band vs. background is >= 1.8 on dark.
export const HL_OPACITY = 50;   // Highlighter opacity (%), raised from 40
export const HL_COLORS = [["#cc8f00", tr("노랑", "Yellow")], ["#66a80f", tr("연두", "Lime")], ["#f06595", tr("분홍", "Pink")]];
// Old highlighter color -> new color (only migrates the stored 'last highlighter color'; strokes already drawn are unchanged)
export const HL_OLD = { "#fcc419": "#cc8f00", "#94d82d": "#66a80f", "#f783ac": "#f06595" };
export const migrateHl = (c) => HL_OLD[String(c || "").toLowerCase()] || c;

const hex = (h) => { const n = parseInt(String(h).replace("#", ""), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
const clamp = (v) => Math.max(0, Math.min(1, v));
/** Color c at opacity a (0–1) over a white background, as 0–255 */
export const over = (c, a, bg = [255, 255, 255]) => { const x = hex(c); return x.map((v, i) => a * v + (1 - a) * bg[i]); };
/** CSS invert(p) hue-rotate(180deg) — sRGB 0–255 */
export function darkFilter(rgb, p = 0.93) {
  const v = rgb.map((x) => clamp((x / 255) * (1 - p) + (1 - x / 255) * p));
  const M = [[-0.574, 1.43, 0.144], [0.426, 0.43, 0.144], [0.426, 1.43, -0.856]];   /* hue-rotate(180°) matrix */
  return M.map((r) => clamp(r[0] * v[0] + r[1] * v[1] + r[2] * v[2]) * 255);
}
const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
export const luminance = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
export function contrast(a, b) { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
/** Both theme values for highlighter color c. light: contrast against black text; dark: contrast of band vs. background; band: the band color as drawn on dark */
export function hlScores(c, a = HL_OPACITY / 100) {
  const m = over(c, a), band = darkFilter(m), bg = darkFilter([255, 255, 255]);
  return { light: contrast(m, [0, 0, 0]), dark: contrast(band, bg), band: band.map(Math.round), bg: bg.map(Math.round), lightBand: m.map(Math.round) };
}
