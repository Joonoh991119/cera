// Makes the synthetic stroke fixtures for shapes.test.mjs:  node tests/fixtures/strokes/gen.mjs   (from web/)
// Seeded, so the output is the same every run. These are synthetic hand strokes, not real handwriting:
// an ideal outline + rotation + low-frequency wobble + tremor + uneven point spacing + a pause jitter at the end,
// and per group an overshoot past the start (3–15% of the path, some exactly 15%) or a gap before it (same), at zoom 0.5 / 1 / 1.5 / 2.
// Hangul: ㅇ ㅁ ㅂ ㅎ strokes inside written words (jamo 40–160 px) with the strokes written before them as context; the writing pace
// (0.2–0.9 s between strokes) and the layout are assumptions, not measurements.
// Real strokes (DIDI, CC BY 4.0; or recorded boards) should replace or extend these before trusting the numbers for real use.
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SEED = +(process.env.GEN_SEED || 20261128);   // GEN_SEED / GEN_OUT: a held-out set with another seed, written elsewhere
const here = process.env.GEN_OUT || dirname(fileURLToPath(import.meta.url));
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const R = rng(SEED);
const U = (a, b) => a + (b - a) * R();
const r1 = (v) => Math.round(v * 10) / 10;

// Ideal closed outlines as f(t), t in [0,1)
const rectF = (w, h, rad) => {   // rounded corners of radius rad
  const segs = [];
  const add = (len, f) => segs.push({ len, f });
  const sx = w - 2 * rad, sy = h - 2 * rad, q = (Math.PI / 2) * rad;
  add(sx, (u) => [rad + sx * u, 0]); add(q, (u) => { const a = -Math.PI / 2 + u * Math.PI / 2; return [w - rad + rad * Math.cos(a), rad + rad * Math.sin(a)]; });
  add(sy, (u) => [w, rad + sy * u]); add(q, (u) => { const a = u * Math.PI / 2; return [w - rad + rad * Math.cos(a), h - rad + rad * Math.sin(a)]; });
  add(sx, (u) => [w - rad - sx * u, h]); add(q, (u) => { const a = Math.PI / 2 + u * Math.PI / 2; return [rad + rad * Math.cos(a), h - rad + rad * Math.sin(a)]; });
  add(sy, (u) => [0, h - rad - sy * u]); add(q, (u) => { const a = Math.PI + u * Math.PI / 2; return [rad + rad * Math.cos(a), rad + rad * Math.sin(a)]; });
  const total = segs.reduce((s, x) => s + x.len, 0);
  return (t) => { let d = (((t % 1) + 1) % 1) * total; for (const s of segs) { if (d <= s.len || s === segs[segs.length - 1]) return s.f(s.len ? Math.min(1, d / s.len) : 0); d -= s.len; } return [0, 0]; };
};
const polyF = (v) => {   // closed polygon through v
  const L = v.map((p, i) => Math.hypot(v[(i + 1) % v.length][0] - p[0], v[(i + 1) % v.length][1] - p[1]));
  const total = L.reduce((a, b) => a + b, 0);
  return (t) => { let d = (((t % 1) + 1) % 1) * total; for (let i = 0; i < v.length; i++) { if (d <= L[i] || i === v.length - 1) { const u = L[i] ? Math.min(1, d / L[i]) : 0, a = v[i], b = v[(i + 1) % v.length]; return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]; } d -= L[i]; } return v[0]; };
};
const ellF = (w, h, drift = 0) => (t) => { const a = t * 2 * Math.PI, k = 1 + drift * t; return [w / 2 + (w / 2) * k * Math.cos(a), h / 2 + (h / 2) * k * Math.sin(a)]; };

/** Walk f from t0 over `extent` turns (dir ±1), with hand noise. size: rough scale in px for the noise amplitudes */
function hand(f, { t0 = 0, dir = 1, extent = 1, size = 100, rot = 0, wob = 0.02, trem = 0.8, dense = 2048 } = {}) {
  const ph = [U(0, 6.28), U(0, 6.28), U(0, 6.28), U(0, 6.28)], fq = [U(1, 3), U(2, 5)];
  const c = Math.cos(rot), s = Math.sin(rot);
  const fine = [];
  for (let i = 0; i <= dense; i++) {
    const u = i / dense, t = t0 + dir * extent * u;
    let [x, y] = f(t);
    x += size * wob * (Math.sin(fq[0] * 6.28 * u + ph[0]) + 0.5 * Math.sin(fq[1] * 6.28 * u + ph[1]));
    y += size * wob * (Math.sin(fq[0] * 6.28 * u + ph[2]) + 0.5 * Math.sin(fq[1] * 6.28 * u + ph[3]));
    fine.push([x * c - y * s, x * s + y * c]);
  }
  // uneven spacing (pen speed changes): keep a point every 1.5–8 px of travel, plus tremor
  const out = [[0, 0]]; let acc = 0, gap = U(1.5, 8);
  const o = fine[0];
  for (let i = 1; i < fine.length; i++) {
    acc += Math.hypot(fine[i][0] - fine[i - 1][0], fine[i][1] - fine[i - 1][1]);
    if (acc >= gap) { out.push([r1(fine[i][0] - o[0] + U(-trem, trem)), r1(fine[i][1] - o[1] + U(-trem, trem))]); acc = 0; gap = U(1.5, 8); }
  }
  const last = out[out.length - 1];
  for (let k = 0; k < 4; k++) out.push([r1(last[0] + U(-0.6, 0.6)), r1(last[1] + U(-0.6, 0.6))]);   // pause jitter
  out.origin = o;   // where the first point is (screen px), for context fixtures
  return out;
}

const cond = (i) => ["wobble", "overlap", "gap"][i % 3];
// overlap / gap: 3–15% of the path, and every 5th one exactly at the 15% boundary
const extentOf = (c, i) => (c === "overlap" ? 1 + (i % 5 === 1 ? 0.15 : U(0.03, 0.15)) : c === "gap" ? 1 - (i % 5 === 2 ? 0.15 : U(0.03, 0.15)) : 1 + U(-0.02, 0.02));
const PER = 90;   // per shape: 30 of each condition
const ZOOMS = [0.5, 1, 1.5, 2];
const MS = 1000;

/** Screen-px stroke -> scene fixture at zoom z: points / z, origin / z */
const atZoom = (pts, z, origin = pts.origin || [0, 0]) => ({ zoom: z, x: r1(origin[0] / z), y: r1(origin[1] / z), points: pts.map(([x, y]) => [r1(x / z), r1(y / z)]) });
const boxOf = (pts, z, t) => { const o = pts.origin || [0, 0]; let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { x0 = Math.min(x0, x + o[0]); x1 = Math.max(x1, x + o[0]); y0 = Math.min(y0, y + o[1]); y1 = Math.max(y1, y + o[1]); }
  return { x: r1(x0 / z), y: r1(y0 / z), w: r1((x1 - x0) / z), h: r1((y1 - y0) / z), t: Math.round(t) }; };

function positives(type) {
  const out = [];
  for (let i = 0; i < PER; i++) {
    const c = cond(i), z = ZOOMS[i % 4];
    const short = Math.exp(U(Math.log(40), Math.log(220))), asp = Math.exp(U(0, Math.log(2.5)));   // shorter side 40–220 screen px, aspect 1–2.5
    const size = short * asp, w = R() < 0.5 ? size : short, h = w === size ? short : size;
    let f, t0 = U(0, 1);
    if (type === "rectangle") { f = rectF(w, h, U(0, 0.08) * Math.min(w, h)); if (R() < 0.6) t0 = 0; }   // most people start at a corner
    else if (type === "diamond") { f = polyF([[w / 2, 0], [w, h / 2], [w / 2, h], [0, h / 2]]); if (R() < 0.6) t0 = 0; }
    else f = ellF(w, h, U(-0.06, 0.06));
    const pts = hand(f, { t0, dir: R() < 0.5 ? 1 : -1, extent: extentOf(c, i), size: Math.min(w, h), rot: U(-7, 7) * Math.PI / 180, wob: U(0.005, 0.03), trem: U(0.3, 1.5) });
    pts.origin = [500 + pts.origin[0], 400 + pts.origin[1]];
    // diagram context for half of them: a label written inside just before, and an arrow ending at the left side
    const others = [];
    if (i % 2) {
      const lab = polyline([[500 + w * 0.3, 400 + h * 0.45], [500 + w * 0.7, 400 + h * 0.5]], 2); others.push(boxOf(lab, z, -U(1.5, 4) * MS));
      const L = U(0.8, 2) * Math.max(w, h), arr = polyline([[500 - L, 400 + h / 2], [500, 400 + h / 2], [500 - 14, 400 + h / 2 - 10]], 3); others.push(boxOf(arr, z, -U(0.3, 1.2) * MS));
    }
    const edge = (c === "overlap" && i % 5 === 1) || (c === "gap" && i % 5 === 2);   // exactly 15%
    out.push({ name: `${type}-${c}-${i}`, cond: c, edge, expect: type, size: Math.round(size), short: Math.round(short), ...atZoom(pts, z), others });
  }
  return out;
}

function polyline(v, step = 3, trem = 0.7) {   // open polyline with tremor
  const out = [];
  for (let i = 0; i < v.length - 1; i++) {
    const a = v[i], b = v[i + 1], n = Math.max(1, Math.round(Math.hypot(b[0] - a[0], b[1] - a[1]) / step));
    for (let k = 0; k < n; k++) out.push([r1(a[0] + (b[0] - a[0]) * k / n + U(-trem, trem)), r1(a[1] + (b[1] - a[1]) * k / n + U(-trem, trem))]);
  }
  out.push(v[v.length - 1].map(r1));
  const rel = out.map((p) => [r1(p[0] - out[0][0]), r1(p[1] - out[0][1])]);
  rel.origin = out[0];
  return rel;
}
const arc = (r, a0, a1, n = 60) => Array.from({ length: n + 1 }, (_, i) => { const a = a0 + (a1 - a0) * i / n; return [r * Math.cos(a), r * Math.sin(a)]; });

function negatives() {
  const out = [];
  const push = (name, points) => out.push({ name, expect: null, points });
  for (let i = 0; i < 16; i++) { const r = U(40, 200), sweep = U(60, 250) * Math.PI / 180, a0 = U(0, 6.28); push(`arc-${i}`, polyline(arc(r, a0, a0 + sweep), 3, 0.8)); }
  for (let i = 0; i < 12; i++) { const L = U(100, 500), amp = U(0, 6); push(`underline-${i}`, polyline(Array.from({ length: 30 }, (_, k) => [L * k / 29, amp * Math.sin(k * U(0.3, 0.9))]), 4, 0.8)); }
  for (let i = 0; i < 10; i++) { const L = U(80, 300), H = U(20, 80); push(`s-curve-${i}`, polyline(Array.from({ length: 40 }, (_, k) => [L * k / 39, H * Math.sin(k / 39 * 2 * Math.PI)]), 3)); }
  for (let i = 0; i < 8; i++) { const n = 3 + (i % 4), L = U(100, 300), H = U(30, 90); push(`zigzag-${i}`, polyline(Array.from({ length: n + 1 }, (_, k) => [L * k / n, k % 2 ? H : 0]), 3)); }
  for (let i = 0; i < 8; i++) { const s = U(60, 200); push(`u-shape-${i}`, polyline([[0, 0], [0, s], [s * U(0.6, 1.4), s], [s * U(0.6, 1.4), 0]], 3)); }
  for (let i = 0; i < 6; i++) { const s = U(60, 200); push(`check-${i}`, polyline([[0, 0], [s * 0.35, s * 0.4], [s, -s * 0.5]], 3)); }
  for (let i = 0; i < 6; i++) { const L = U(120, 400); push(`arrow-${i}`, polyline([[0, 0], [L, 0], [L - 20, -14], [L, 0], [L - 20, 14]], 3)); }
  return out;
}

/** Hangul words. Each syllable = initial + vowel (ㅏ to the right or ㅡ/ㅗ below) + sometimes a final; the targets are the ㅇ ㅁ ㅂ ㅎ strokes.
  * j = target jamo box side in screen px (40–160). Strokes written before it on the line are its context (gap to the previous stroke 0.2–0.9 s). */
const T = ["ieung", "mieum", "bieup", "hieut"];
function jamo(kind, x, y, w, h) {   // strokes of one jamo in a box, screen px, each with .origin
  const H = (f, o = {}) => { const p = hand(f, { size: Math.min(w, h), rot: U(-6, 6) * Math.PI / 180, wob: U(0.01, 0.04), trem: U(0.2, 0.8), ...o }); p.origin = [x + p.origin[0], y + p.origin[1]]; return p; };
  const P = (v) => polyline(v.map(([a, b]) => [x + a, y + b]), 2);
  if (kind === "ieung") return [{ t: true, p: H(ellF(w, h * U(0.85, 1), U(-0.08, 0.08)), { t0: 0.75, dir: -1, extent: 1 + U(-0.12, 0.12) }) }];
  if (kind === "mieum") return R() < 0.5 ? [{ t: true, p: H(rectF(w, h, U(0, 0.12) * w), { t0: 0, dir: -1, extent: 1 + U(-0.1, 0.1) }) }]
    : [{ t: true, p: P([[0, 0], [0, h]]) }, { t: true, p: P([[0, 0], [w, 0], [w, h]]) }, { t: true, p: P([[0, h], [w, h]]) }];
  if (kind === "bieup") return R() < 0.5 ? [{ t: true, p: P([[0, 0], [0, h], [w, h], [w, 0], [w, h * 0.5], [0, h * 0.5]]) }]
    : [{ t: true, p: P([[0, 0], [0, h]]) }, { t: true, p: P([[w, 0], [w, h]]) }, { t: true, p: P([[0, h * 0.5], [w, h * 0.5]]) }, { t: true, p: P([[0, h], [w, h]]) }];
  if (kind === "hieut") {
    const loop = Array.from({ length: 40 }, (_, k) => { const q = ellF(w * 0.7, h * 0.5)(0.75 - k / 39); return [w * 0.15 + q[0], h * 0.45 + q[1]]; });
    return R() < 0.5 ? [{ t: true, p: P([[w * 0.35, 0], [w * 0.65, 0], [0, h * 0.25], [w, h * 0.25], [w * 0.5, h * 0.45], ...loop]) }]
      : [{ t: true, p: P([[w * 0.35, 0], [w * 0.65, 0]]) }, { t: true, p: P([[0, h * 0.25], [w, h * 0.25]]) }, { t: true, p: H((t) => { const q = ellF(w * 0.7, h * 0.5)(t); return [w * 0.15 + q[0], h * 0.45 + q[1]]; }, { t0: 0.75, dir: -1, extent: 1 + U(-0.1, 0.1) }) }];
  }
  if (kind === "a") return [{ p: P([[w * 0.5, -h * 0.3], [w * 0.5, h * 1.3]]) }, { p: P([[w * 0.5, h * 0.5], [w * 0.9, h * 0.5]]) }];
  if (kind === "eu") return [{ p: P([[-w * 0.2, 0], [w * 1.2, 0]]) }];
  if (kind === "n") return [{ p: P([[0, 0], [0, h], [w, h]]) }];
  return [{ p: P([[0, 0], [w, 0], [w, h]]) }];   // ㄱ
}
function hangul(n, tag) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const j = Math.exp(U(Math.log(40), Math.log(160))), z = ZOOMS[Math.floor(i / 4) % 4], gap = j * U(0.25, 0.6);
    const strokes = [];
    let x = 100;
    const nSyl = 2 + (i % 3), target = T[i % 4];
    for (let k = 0; k < nSyl; k++) {
      const init = k === nSyl - 1 || R() < 0.5 ? target : ["n", "g"][k % 2];
      const below = R() < 0.4;
      strokes.push(...jamo(init, x, 300, j, j).map((s) => ({ ...s, t: s.t && init === target })));
      if (below) strokes.push(...jamo("eu", x, 300 + j * 1.25, j, j)); else strokes.push(...jamo("a", x + j * 0.9, 300, j * 0.6, j));
      if (R() < 0.35) strokes.push(...jamo(R() < 0.5 ? target : "n", x + j * 0.2, 300 + j * (below ? 1.5 : 1.35), j * 0.8, j * 0.6).map((s) => ({ ...s, t: s.t && true })));
      x += j * (below ? 1.2 : 1.8) + gap;
    }
    // time: each stroke starts 0.2–0.9 s after the previous one ended (assumed writing pace, not measured)
    let clock = 0; const times = strokes.map(() => (clock += U(0.2, 0.9) * MS));
    strokes.forEach((s, k) => {
      if (!s.t) return;
      const others = strokes.slice(0, k).map((o, q) => boxOf(o.p, z, times[q] - times[k] + U(0.05, 0.15) * MS));   // t relative to this stroke's start (0)
      out.push({ name: `${tag}-${target}-${i}-${k}`, letter: target, size: Math.round(j), ctx: k > 0, expect: null, ...atZoom(s.p, z), others });
    });
  }
  return out;
}

const files = {
  "rectangle.json": positives("rectangle"),
  "diamond.json": positives("diamond"),
  "ellipse.json": positives("ellipse"),
  "open.json": negatives(),
  "hangul.json": hangul(160, "word"),   // ㅇ ㅁ ㅂ ㅎ strokes, jamo 40–160 px, zoom 0.5/1/1.5/2, in words (ctx) or first in the line (no ctx)
};
for (const [f, v] of Object.entries(files)) writeFileSync(join(here, f), JSON.stringify({ generator: "gen.mjs", seed: SEED, strokes: v }) + "\n");
console.log(Object.entries(files).map(([f, v]) => `${f}: ${v.length}`).join(", "));
