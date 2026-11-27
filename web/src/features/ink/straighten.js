// Pen stroke correction: straighten when the pen pauses at the end. Pure logic (tests: web/tests/straighten.test.mjs).
// The stroke stays a freedraw element (not converted to a line, so thickness, eraser and color behave the same). Only points are replaced with points on the start-to-end line.
// Original code. The $1/$P recognizers (New BSD) were neither read nor copied.

export const HOLD_MS = 450;        // Holding this long counts as a pause
export const HOLD_PX = 3;          // Screen px: jitter within this counts as holding still (mouse and pen)
export const HOLD_PX_TOUCH = 5;    // Fingers jitter more
export const SNAP_DEG = 15;        // Shift snaps to 15° steps
export const MIN_LEN_PX = 28;      // Screen px: strokes shorter than this (letter-sized strokes, flicks) are left unchanged
export const MAX_DEV = 0.09;       // Farthest point's distance from the start-end line / length: a stroke curved more than this (like ㄱ, ㅇ, an arc) is not treated as straight
export const MAX_TURN_DEG = 50;    // If the direction turns by more than this within the stroke (like ㄱ, ㄴ, a flick at the end), it is not treated as straight
export const TAIL_PX = 4;          // Scene px: points within this of either end are excluded from the turn check (pen-tip jitter while paused)

/** Hold detection: if the pointer moves more than px from the anchor, take a new anchor and time. State is { ax, ay, at } */
export function holdStep(s, x, y, t, px = HOLD_PX) {
  if (!s || Math.hypot(x - s.ax, y - s.ay) > px) return { ax: x, ay: y, at: t };
  return s;
}
export const isHeld = (s, t, ms = HOLD_MS) => !!s && t - s.at >= ms;

const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);

/** Distance to the farthest point from the start-end line, and the line's length */
export function deviation(points) {
  const a = points[0], b = points[points.length - 1];
  const len = dist(a, b);
  if (!len) return { len: 0, dev: Infinity };
  const ux = (b[0] - a[0]) / len, uy = (b[1] - a[1]) / len;
  let dev = 0;
  for (const p of points) dev = Math.max(dev, Math.abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux));
  return { len, dev };
}

/** Largest turning angle (°) within the stroke, measured on points resampled evenly along its length (less sensitive to point spacing and hand tremor). Points within tail of either end are excluded */
export function maxTurn(points, { tail = TAIL_PX, samples = 12 } = {}) {
  const a = points[0], b = points[points.length - 1];
  let i0 = 0, i1 = points.length - 1;
  while (i0 < i1 && dist(points[i0], a) <= tail) i0++;
  while (i1 > i0 && dist(points[i1], b) <= tail) i1--;
  const core = [a, ...points.slice(i0, i1 + 1), b];
  const cum = [0];
  for (let i = 1; i < core.length; i++) cum.push(cum[i - 1] + dist(core[i - 1], core[i]));
  const total = cum[cum.length - 1];
  if (!total) return 0;
  const pts = [];
  let j = 1;
  for (let k = 0; k <= samples; k++) {
    const at = (k / samples) * total;
    while (j < core.length - 1 && cum[j] < at) j++;
    const seg = cum[j] - cum[j - 1] || 1, t = Math.min(1, Math.max(0, (at - cum[j - 1]) / seg));
    pts.push([core[j - 1][0] + (core[j][0] - core[j - 1][0]) * t, core[j - 1][1] + (core[j][1] - core[j - 1][1]) * t]);
  }
  let worst = 0;
  for (let k = 1; k < pts.length - 1; k++) {
    const u = [pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]], v = [pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]];
    const nu = Math.hypot(...u), nv = Math.hypot(...v);
    if (!nu || !nv) continue;
    const c = Math.max(-1, Math.min(1, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)));
    worst = Math.max(worst, (Math.acos(c) * 180) / Math.PI);
  }
  return worst;
}

/** Whether the stroke should become a straight line: long enough, not strongly curved, and no turns (not ㄱ, ㄴ, ㅇ, an arc or an end flick). minLen and tail are in scene units */
export function canStraighten(points, { minLen = MIN_LEN_PX, maxDev = MAX_DEV, maxTurnDeg = MAX_TURN_DEG, tail = TAIL_PX } = {}) {
  if (!points || points.length < 2) return false;
  const { len, dev } = deviation(points);
  if (!(len >= minLen && dev / len <= maxDev)) return false;
  return maxTurn(points, { tail }) <= maxTurnDeg;
}

/** Snap the angle to step° increments, keeping the length and rotating about the start point */
export function snapEnd(a, b, stepDeg = SNAP_DEG) {
  const len = dist(a, b);
  const step = (stepDeg * Math.PI) / 180;
  const ang = Math.round(Math.atan2(b[1] - a[1], b[0] - a[0]) / step) * step;
  const r = (v) => Math.round(v * 1e6) / 1e6;
  return [r(a[0] + len * Math.cos(ang)), r(a[1] + len * Math.sin(ang))];
}

/** Median spacing between stroke points. Simulated pen thickness (simulatePressure) is derived from point spacing, so keep the original spacing */
export function medianStep(points) {
  const d = [];
  for (let i = 1; i < points.length; i++) { const s = dist(points[i - 1], points[i]); if (s > 0) d.push(s); }
  if (!d.length) return 2;
  d.sort((x, y) => x - y);
  return d[d.length >> 1];
}

/** Pressure value at each cumulative-length ratio (0–1): carries the original stroke's thickness profile over to the line (one forward sweep, O(n + m)) */
function pressuresAlong(points, pressures, n) {
  const cum = [0];
  for (let i = 1; i < points.length; i++) cum.push(cum[i - 1] + dist(points[i - 1], points[i]));
  const total = cum[cum.length - 1];
  const out = [];
  let i = 1;
  for (let k = 0; k < n; k++) {
    if (!total) { out.push(pressures[0]); continue; }
    const at = (k / (n - 1)) * total;
    while (i < cum.length - 1 && cum[i] < at) i++;
    const seg = cum[i] - cum[i - 1] || 1;
    const t = Math.min(1, Math.max(0, (at - cum[i - 1]) / seg));
    out.push(pressures[i - 1] + (pressures[i] - pressures[i - 1]) * t);
  }
  return out;
}

/**
  * To a straight line: the start point stays, and points are spaced evenly up to the end point (angle-snapped with Shift). pressures has the same length as points (or is returned as an empty array).
  * Returns: { points, pressures }
 */
export function straighten(points, pressures = [], { snap = false, maxPoints = 400 } = {}) {
  const a = points[0];
  const b = snap ? snapEnd(a, points[points.length - 1]) : points[points.length - 1];
  const len = dist(a, b);
  const step = Math.min(8, Math.max(0.5, medianStep(points)));
  const n = Math.max(2, Math.min(maxPoints, Math.ceil(len / step) + 1));
  const out = [];
  const real = pressures.length === points.length && pressures.length > 0;
  for (let i = 0; i < n; i++) {
    const f = i / (n - 1);
    out.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
  }
  out[0] = [a[0], a[1]];
  return { points: out, pressures: real ? pressuresAlong(points, pressures, n) : [] };
}

/** Bounds of points: same as Excalidraw's getSizeFromPoints (the freedraw width/height) */
export function sizeOf(points) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of points) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  return { width: x1 - x0, height: y1 - y0 };
}
