// Pen stroke correction: 'hold for a shape'. A closed hand-drawn loop becomes a clean rectangle, diamond or ellipse. Pure logic (tests: web/tests/shapes.test.mjs).
// Original code, written from the published idea only (resample, find corners by the ShortStraw 'straw' distance, compare to template outlines).
// The $1/$P/ShortStraw reference implementations were neither read nor copied (same clean-room rule as straighten.js).
//
// Order: size gate -> closed? (trim a short overshoot, keep a long tail = open) -> resample -> corners -> outline fit to rectangle · diamond · ellipse
// on the loop's bounds -> no side may be missing (an open U is not a box). When unsure, return null: the stroke stays as drawn (Canva's rule).
// Writing guard (writingNearby): a loop drawn right after letter-sized strokes in a row is a letter (ㅇ ㅁ), whatever its size; labels and arrows are not writing.

export const SHAPE_MIN_PX = 60;   // Screen px: loops with a smaller diagonal are left alone (plan rule; zoom-aware)
export const CLOSE_FRAC = 0.2;    // Closed when the start-end gap (after trimming an overshoot) is under this share of the perimeter
export const TAIL_FRAC = 0.2;     // ... and the trimmed overshoot is at most this share (a longer tail means an open stroke: keep it)
export const MAX_POINTS = 2048;   // Longer strokes are decimated first (time stays bounded)
export const WRITE_MS = 1500;     // Strokes finished this long before the loop started count as 'just written'
const N = 64;                     // Resampled points on the loop
const W = 3;                      // ShortStraw window (points on each side)
const FIT_MAX = { rectangle: 0.1, diamond: 0.07, ellipse: 0.08 };   // Accept only when the outline error (share of the diagonal) is below this
const MARGIN = 1.3;               // ... and the runner-up's error is at least this many times larger
const MISSING_SIDE = 0.7;         // A box or diamond side whose middle run this long (share of the side) is not drawn = an open U
const TYPES = ["rectangle", "diamond", "ellipse"];

const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);

function pathLength(p) { let s = 0; for (let i = 1; i < p.length; i++) s += dist(p[i - 1], p[i]); return s; }

function bounds(p) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of p) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  return { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0 };
}

/** Evenly spaced points along a polyline (n points, the first is the start). closed: also walk the closing segment back to the start */
export function resample(p, n = N, closed = false) {
  const pts = closed ? [...p, p[0]] : p;
  const total = pathLength(pts);
  if (!total) return Array.from({ length: n }, () => [pts[0][0], pts[0][1]]);
  const step = total / (closed ? n : n - 1);
  const out = [[pts[0][0], pts[0][1]]];
  let acc = 0;
  for (let i = 1; i < pts.length && out.length < n; i++) {
    let a = pts[i - 1]; const b = pts[i];
    let d = dist(a, b);
    while (acc + d >= step && out.length < n) {
      const t = (step - acc) / d;
      a = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      out.push(a);
      d = dist(a, b); acc = 0;
    }
    acc += d;
  }
  while (out.length < n) out.push([pts[pts.length - 1][0], pts[pts.length - 1][1]]);
  return out;
}

/** Closed loop or not. A short overshoot past the start is trimmed at the tail point nearest the start; a long tail means the stroke is open. Returns the trimmed points or null */
export function closeLoop(p, frac = CLOSE_FRAC, tailFrac = TAIL_FRAC) {
  const cum = [0];
  for (let i = 1; i < p.length; i++) cum.push(cum[i - 1] + dist(p[i - 1], p[i]));
  const L = cum[cum.length - 1];
  if (!L) return null;
  let best = -1, bd = Infinity;
  for (let i = 1; i < p.length; i++) {
    if (cum[i] < 0.6 * L) continue;   // only the last 40% can close the loop
    const d = dist(p[i], p[0]);
    if (d < bd) { bd = d; best = i; }
  }
  if (best < 0 || bd >= frac * (cum[best] + bd) || L - cum[best] > tailFrac * L) return null;   // gap vs the loop's perimeter (drawn part + gap), as the plan says
  return p.slice(0, best + 1);
}

/** Corner indexes on a closed resampled loop (ShortStraw idea: a short 'straw' across a point means the path turns there) */
export function corners(loop, w = W) {
  const n = loop.length;
  const straw = loop.map((_, i) => dist(loop[(i - w + n) % n], loop[(i + w) % n]));
  const sorted = [...straw].sort((a, b) => a - b);
  const t = sorted[n >> 1] * 0.9;
  const out = [];
  for (let i = 0; i < n; i++) {
    if (straw[i] >= t) continue;
    let isMin = true;   // the smallest straw within the window
    for (let k = -w; k <= w && isMin; k++) if (k && straw[(i + k + n) % n] < straw[i]) isMin = false;
    if (isMin && !(out.length && i - out[out.length - 1] <= w)) out.push(i);
  }
  if (out.length > 1 && out[0] + n - out[out.length - 1] <= w) out.pop();
  return out;
}

function outline(type, b, m = 96) {
  const cx = b.x0 + b.w / 2, cy = b.y0 + b.h / 2, rx = b.w / 2, ry = b.h / 2;
  if (type === "ellipse") return Array.from({ length: m }, (_, i) => { const a = (i / m) * 2 * Math.PI; return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]; });
  const v = type === "rectangle"
    ? [[b.x0, b.y0], [b.x1, b.y0], [b.x1, b.y1], [b.x0, b.y1]]
    : [[cx, b.y0], [b.x1, cy], [cx, b.y1], [b.x0, cy]];
  const out = [];
  for (let s = 0; s < 4; s++) {
    const a = v[s], c = v[(s + 1) % 4];
    for (let k = 0; k < m / 4; k++) { const t = k / (m / 4); out.push([a[0] + (c[0] - a[0]) * t, a[1] + (c[1] - a[1]) * t]); }
  }
  return out;
}

const segDist = (p, a, b) => {
  const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
  return Math.hypot(p[0] - a[0] - dx * t, p[1] - a[1] - dy * t);
};
function toPolygon(p, poly) { let d = Infinity; for (let i = 0; i < poly.length; i++) d = Math.min(d, segDist(p, poly[i], poly[(i + 1) % poly.length])); return d; }

/** Two-way outline error as a share of the diagonal: the 90th percentile of the distances stroke->outline and outline->stroke.
  * A high percentile (not the mean) so a missed corner counts: a box against an ellipse is off only near its 4 corners. */
export function fitError(loop, type, b = bounds(loop)) {
  const diag = Math.hypot(b.w, b.h) || 1;
  const o = outline(type, b);
  const d = [];
  for (const p of loop) d.push(toPolygon(p, o));
  for (const q of o) d.push(toPolygon(q, loop));
  d.sort((x, y) => x - y);
  return d[Math.floor(d.length * 0.9)] / diag;
}

/** Longest run (share of one side) of a box or diamond side that the drawn path does not come near. The closing segment from the end back to the start does not count as drawn */
export function missingSide(path, type, b, tol) {
  const o = outline(type, b, 96), per = 24;
  let worst = 0;
  for (let side = 0; side < 4; side++) {
    let run = 0, best = 0;
    for (let k = 0; k <= per; k++) {
      const q = o[(side * per + k) % o.length];
      let d = Infinity;
      for (let i = 1; i < path.length && d > tol; i++) d = Math.min(d, segDist(q, path[i - 1], path[i]));
      run = d > tol ? run + 1 : 0; best = Math.max(best, run);
    }
    worst = Math.max(worst, best / per);
  }
  return worst;
}

const decimate = (p, max = MAX_POINTS) => {
  if (p.length <= max) return p;
  const step = p.length / (max - 1), out = [];
  for (let i = 0; i < max - 1; i++) out.push(p[Math.floor(i * step)]);
  out.push(p[p.length - 1]);
  return out;
};

/**
  * points: freedraw points (relative to the element). opts: { zoom } (the size gate is in screen px).
  * Returns { type: 'rectangle'|'diamond'|'ellipse', x, y, width, height, err, corners } in the same coordinates, or null to leave the stroke unchanged.
 */
export function recognizeShape(points, { zoom = 1, minPx = SHAPE_MIN_PX } = {}) {
  if (!points || points.length < 4) return null;   // a fast touch box can arrive as 5–6 points
  const pts = decimate(points);
  const raw = bounds(pts);
  if (Math.hypot(raw.w, raw.h) * (zoom || 1) < minPx) return null;
  const closed = closeLoop(pts);
  if (!closed) return null;
  const loop = resample(closed, N, true);
  const b = bounds(loop);
  if (Math.min(b.w, b.h) < 0.15 * Math.max(b.w, b.h)) return null;   // a flat loop (scribble on a line) is not a shape
  const k = corners(loop).length;
  // Corners pick the family: a box has 3–6 corners, an ellipse at most 2. A diamond's wide corners are often missed, so 2–6.
  // A box against an ellipse outline is off only near its corners, so the outline error alone does not separate them.
  // One spurious corner on a wobbly circle is forgiven when the ellipse fits far better than either polygon.
  const all = TYPES.map((t) => ({ t, e: fitError(loop, t, b) }));
  const eE = all[2].e, ePoly = Math.min(all[0].e, all[1].e);
  const fits = all.filter(({ t }) => (t === "ellipse" ? k <= 2 || (k === 3 && eE * 2.5 < ePoly) : t === "rectangle" ? k >= 3 && k <= 6 : k >= 2 && k <= 6))
    .sort((x, y) => x.e - y.e);
  if (!fits.length || !(fits[0].e < FIT_MAX[fits[0].t]) || (fits[1] && fits[1].e < fits[0].e * MARGIN)) return null;
  const { t: type, e } = fits[0];
  if (type !== "ellipse" && missingSide(resample(closed, N, false), type, b, 0.12 * Math.min(b.w, b.h)) >= MISSING_SIDE) return null;
  return { type, x: b.x0, y: b.y0, width: b.w, height: b.h, err: e, corners: k };
}

/**
  * Writing guard: was this loop written as a letter? box = the loop's scene bounds { x, y, w, h }; others = other live freedraw strokes { x, y, w, h, t }
  * (t = when last changed, ms); t0 = when the loop started; parts = bounds of existing boxes · ellipses · diamonds.
  * Writing = a letter-sized stroke finished within WRITE_MS before it, within 1.2 × the loop's size and at least 0.2 × its size
  * (letters next to a ㅇ are about its size; a small label 'A' beside a big box is not). Not counted: strokes inside the loop or inside an existing
  * shape (labels), strokes much bigger than it, and arrows ending at or just before it (long, thin, lying along their own long axis, gap < 0.25 × size).
 */
export function writingNearby(box, others, t0, { ms = WRITE_MS, parts = [] } = {}) {
  const S = Math.max(box.w, box.h) || 1;
  const overlap = (a0, a1, b0, b1) => Math.max(0, Math.min(a1, b1) - Math.max(a0, b0));
  const inside = (x, y, b) => x > b.x && x < b.x + b.w && y > b.y && y < b.y + b.h;
  for (const o of others) {
    if (!(o.t >= t0 - ms && o.t <= t0 + 50)) continue;
    const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
    if (inside(cx, cy, box) || parts.some((p) => inside(cx, cy, p))) continue;   // a label in this loop or in another box
    if (Math.max(o.w, o.h) > 2 * S || Math.max(o.w, o.h) < 0.2 * S) continue;   // a big drawing, or a small mark next to a big shape
    const gx = Math.max(0, Math.max(box.x, o.x) - Math.min(box.x + box.w, o.x + o.w));
    const gy = Math.max(0, Math.max(box.y, o.y) - Math.min(box.y + box.h, o.y + o.h));
    if (Math.hypot(gx, gy) > 1.2 * S) continue;
    const xo = overlap(box.x, box.x + box.w, o.x, o.x + o.w), yo = overlap(box.y, box.y + box.h, o.y, o.y + o.h);
    const sameLine = yo >= 0.3 * Math.min(box.h, o.h || 1), stacked = xo >= 0.3 * Math.min(box.w, o.w || 1);
    const horiz = o.w >= o.h, long = Math.max(o.w, o.h) >= 0.4 * S && Math.min(o.w, o.h) < 0.25 * Math.max(o.w, o.h);
    if (long && gx + gy < 0.25 * S && (horiz ? !stacked : !sameLine)) continue;   // an arrow pointing at the loop
    return true;
  }
  return false;
}

/** True when box lies inside one of parts (a small overhang, 5% of the part's size, still counts as inside) */
export function insideAny(box, parts) {
  return parts.some((p) => { const m = 0.05 * Math.max(p.w, p.h);
    return box.x >= p.x - m && box.y >= p.y - m && box.x + box.w <= p.x + p.w + m && box.y + box.h <= p.y + p.h + m; });
}

/** Scene bounds of a freedraw element (its points can go left of or above x, y) */
export function inkBox(e) {
  const b = bounds(e.points && e.points.length ? e.points : [[0, 0]]);
  return { x: e.x + b.x0, y: e.y + b.y0, w: b.w, h: b.h, t: e.updated || 0 };
}
/** Other live freedraw strokes changed within ms before t0, as inkBox()es */
export const recentInk = (all, exceptId, t0, ms = WRITE_MS) =>
  all.filter((e) => e.type === "freedraw" && !e.isDeleted && e.id !== exceptId && (e.updated || 0) >= t0 - ms).map(inkBox);

/**
  * The whole decision for a held stroke, in scene units: the recognized shape, or null when it is not a shape or looks like writing.
  * all = scene elements including deleted; t0 = when the stroke started (ms, same clock as element.updated).
 */
export function shapeForStroke(el, all, { zoom = 1, t0 = Date.now() } = {}) {
  if (!el || el.type !== "freedraw" || el.isDeleted) return null;
  const r = recognizeShape(el.points, { zoom });
  if (!r) return null;
  const parts = all.filter((e) => !e.isDeleted && (e.type === "rectangle" || e.type === "ellipse" || e.type === "diamond"))
    .map((e) => ({ x: Math.min(e.x, e.x + e.width), y: Math.min(e.y, e.y + e.height), w: Math.abs(e.width), h: Math.abs(e.height) }));
  const box = { x: el.x + r.x, y: el.y + r.y, w: r.width, h: r.height };
  if (insideAny(box, parts)) return null;   // drawn inside an existing box / ellipse / diamond: a label (ㅇ ㅁ in a box), never snapped
  if (writingNearby(box, recentInk(all, el.id, t0), t0, { parts })) return null;
  return r;
}

const nonce = (rand) => Math.floor(rand() * 2 ** 31);
const bump = (e, o, rand, now) => ({ ...e, ...o, version: e.version + 1, versionNonce: nonce(rand), updated: now });

/** Skeleton for convertToExcalidrawElements: color, opacity and stroke width carried over from the stroke, clean lines (roughness 0) */
export function shapeSpec(el, r, { round = false } = {}) {
  return {
    type: r.type, x: el.x + r.x, y: el.y + r.y, width: Math.max(1, r.width), height: Math.max(1, r.height),
    strokeColor: el.strokeColor, backgroundColor: "transparent", fillStyle: "solid", strokeStyle: "solid", opacity: el.opacity,
    strokeWidth: el.strokeWidth, roughness: 0,
    roundness: round && r.type !== "ellipse" ? { type: r.type === "rectangle" ? 3 : 2 } : null,
    frameId: el.frameId || null, groupIds: [],
  };
}

/** The scene with the stroke deleted (version +1) and the shape added: one change, one undo step */
export function snapScene(all, strokeId, shape, { rand = Math.random, now = Date.now() } = {}) {
  return [...all.map((e) => (e.id === strokeId ? bump(e, { isDeleted: true }, rand, now) : e)), shape];
}

/**
  * 'Keep as drawn': the stroke back and the shape deleted (both version +1), or null to do nothing.
  * Nothing happens unless the shape is still there, untouched since the snap (same version), and the stroke is still deleted at its snap version:
  * a collaborator who deleted or changed the shape, or an undo, wins.
 */
export function keepScene(all, snap, { rand = Math.random, now = Date.now() } = {}) {
  const st = all.find((e) => e.id === snap.stroke), sh = all.find((e) => e.id === snap.shape);
  if (!st || !sh || !st.isDeleted || sh.isDeleted || st.version !== snap.strokeV || sh.version !== snap.shapeV) return null;
  return all.map((e) => (e === st ? bump(e, { isDeleted: false }, rand, now) : e === sh ? bump(e, { isDeleted: true }, rand, now) : e));
}

