// Pen stroke correction: smoothing (off / low / high). Pure logic (tests: web/tests/smooth.test.mjs).
import { tr } from "../../i18n.js";
// Excalidraw's perfect-freehand settings (thinning, smoothing, streamline) are hardcoded and cannot be changed, so we rewrite the points after the stroke ends.
//
// No point reduction (RDP): mouse and finger strokes simulate thickness from 'point spacing' (simulatePressure), so removing points would change stroke thickness.
// The number and order of points stay the same; only positions move, so the thickness profile is preserved.

export const LEVELS = ["off", "low", "high"];
export const LEVEL_LABEL = { off: tr("끔", "Off"), low: tr("약", "Low"), high: tr("강", "High") };
const PASSES = { off: 0, low: 1, high: 4 };   // How many passes of [1,2,1]/4: 'low' is just enough not to blur the corners of Hangul strokes

/** One pass of the [1,2,1]/4 moving average. Both end points stay fixed */
function pass(points) {
  const n = points.length;
  const out = new Array(n);
  out[0] = points[0];
  out[n - 1] = points[n - 1];
  for (let i = 1; i < n - 1; i++) {
    const a = points[i - 1], b = points[i], c = points[i + 1];
    out[i] = [(a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4];
  }
  return out;
}
function passScalar(v) {
  const n = v.length, out = v.slice();
  for (let i = 1; i < n - 1; i++) out[i] = (v[i - 1] + 2 * v[i] + v[i + 1]) / 4;
  return out;
}

/**
  * Smoothing: level 'off' | 'low' | 'high'. Returns { points, pressures, changed }.
  * pressures keeps its incoming length (empty stays empty, otherwise same length as points).
 */
export function smoothStroke(points, pressures = [], level = "low") {
  const k = PASSES[level] || 0;
  if (!k || !points || points.length < 3) return { points, pressures, changed: false };
  let p = points;
  for (let i = 0; i < k; i++) p = pass(p);
  let pr = pressures;
  if (pressures.length === points.length && pressures.length > 2) for (let i = 0; i < Math.min(k, 2); i++) pr = passScalar(pr);
  return { points: p, pressures: pr, changed: true };
}

/** Roughness of a stroke: mean magnitude of the turn (second difference) made by three neighboring points. For tests and measurement */
export function roughness(points) {
  if (points.length < 3) return 0;
  let s = 0;
  for (let i = 1; i < points.length - 1; i++) {
    const a = points[i - 1], b = points[i], c = points[i + 1];
    s += Math.hypot(a[0] - 2 * b[0] + c[0], a[1] - 2 * b[1] + c[1]);
  }
  return s / (points.length - 2);
}
