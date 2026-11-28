// Editing a formula: replaces the same element. Pure logic only (tests: web/tests/mathreplace.test.mjs).
//
// Previously, editing deleted the old element and created a new image with a new id, which lost enlarged size, color, group and attached arrows.
// Now only fileId, customData and size change on the same id, and version is bumped. The initial typeset size is kept in customData.w0/h0.

const bumpWith = (e, patch, rand) => ({ ...e, ...patch, version: e.version + 1, versionNonce: Math.floor(rand() * 2 ** 31) });
const pos = (v) => Number.isFinite(v) && v > 0;

/** Initial typeset size of a formula element, or null if absent (old element) */
export function naturalSize(el) {
  const c = el && el.customData;
  return c && pos(c.w0) && pos(c.h0) ? { w: c.w0, h: c.h0 } : null;
}

/** Move along one axis: before the box unchanged, inside the box proportionally, after the box by the amount it grew (preserves the gap between arrow and box) */
function mapAxis(p, a, len0, len1) {
  if (p <= a) return p;
  if (p >= a + len0) return p + (len1 - len0);
  return a + ((p - a) * len1) / len0;
}
export function mapPoint([px, py], box0, box1) {
  return [mapAxis(px, box0.x, box0.width, box1.width), mapAxis(py, box0.y, box0.height, box1.height)];
}

const close = (a, b) => Math.abs(a - b) < 1e-6;

/** One arrow: moves the end attached to target to fit the new box. For an elbow arrow, neighboring points move too to keep right angles. */
export function adjustArrow(arrow, targetId, box0, box1) {
  const pts = arrow.points.map((p) => [p[0], p[1]]);
  const n = pts.length;
  if (n < 2) return null;
  let x = arrow.x, y = arrow.y, changed = false;
  const abs = {};   // scene coordinates of the moved end: used to re-measure the elbow arrow's fixedPoint against the new box
  const moveEnd = (i, j) => {   // i = end point, j = neighbor
    const old = [pts[i][0], pts[i][1]];
    const [nx, ny] = mapPoint([x + old[0], y + old[1]], box0, box1);
    pts[i] = [nx - x, ny - y];
    abs[i] = [nx, ny];
    if (arrow.elbowed && j >= 0 && j < n && n > 2) {
      if (close(pts[j][1], old[1])) pts[j][1] = pts[i][1];
      else if (close(pts[j][0], old[0])) pts[j][0] = pts[i][0];
    }
    changed = changed || !close(pts[i][0], old[0]) || !close(pts[i][1], old[1]);
  };
  if (arrow.endBinding && arrow.endBinding.elementId === targetId) moveEnd(n - 1, n - 2);
  if (arrow.startBinding && arrow.startBinding.elementId === targetId) {
    moveEnd(0, 1);
    const [dx, dy] = pts[0];   // the first point is always (0,0): move the origin and shift the others back
    if (dx || dy) { x += dx; y += dy; for (let i = 0; i < n; i++) pts[i] = [pts[i][0] - dx, pts[i][1] - dy]; }
  }
  if (!changed) return null;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const out = { x, y, points: pts, width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
  // box0 and box1 are assumed to share the same origin (x, y) (replaceMath always passes them that way; if used elsewhere, also account for origin movement).
  // An elbow arrow (0.18) determines its end by fixedPoint (a ratio relative to the box) the next time its path is rebuilt.
  // The gap is baked into the ratio, so keeping the old ratio would leave the end detached from the box by however much the box grew (we saw a 54px jump).
  const refit = (b, p) => (b && Array.isArray(b.fixedPoint) && p && box1.width > 0 && box1.height > 0
    ? { ...b, fixedPoint: [(p[0] - box1.x) / box1.width, (p[1] - box1.y) / box1.height] } : b);
  if (arrow.endBinding && arrow.endBinding.elementId === targetId && abs[n - 1]) out.endBinding = refit(arrow.endBinding, abs[n - 1]);
  if (arrow.startBinding && arrow.startBinding.elementId === targetId && abs[0]) out.startBinding = refit(arrow.startBinding, abs[0]);
  return out;
}

/** customData of a re-typeset formula: the new latex / size / colour / typeset size; an MCP formula's render state (cera.render · message,
 *  WP-4) is dropped — it described the old LaTeX (mathJob sets "done" again after this) */
function freshCustom(cd, next) {
  const out = { ...(cd || {}), latex: next.latex, size: next.size, color: next.color, w0: next.w, h0: next.h };
  if (out.cera && typeof out.cera === "object") {
    const { render, message, ...rest } = out.cera;   // eslint-disable-line no-unused-vars
    if (Object.keys(rest).length) out.cera = rest; else delete out.cera;
  }
  return out;
}

/**
  * Returns the scene (everything, including deleted elements) with the same formula element replaced by the new formula.
  * next = { fileId, latex, size, color, w, h }, where w/h is the new formula's typeset size.
  * natural = the old formula's typeset size { w, h } (old elements don't have it in customData, so the caller re-measures it). If absent, the current size is taken as the typeset size.
  * The ratio the user enlarged by (width / w0) is also applied to the new formula. Position (x, y), angle, groupIds, frameId, boundElements and lock stay as is.
 */
export function replaceMath(elements, id, next, natural = null, rand = Math.random) {
  const old = elements.find((e) => e.id === id);
  if (!old || old.isDeleted) return null;
  const nat = naturalSize(old) || natural || { w: old.width, h: old.height };
  const sx = pos(nat.w) && pos(old.width) ? old.width / nat.w : 1;
  const sy = pos(nat.h) && pos(old.height) ? old.height / nat.h : 1;
  const width = next.w * sx, height = next.h * sy;
  const el = bumpWith(old, {
    fileId: next.fileId, width, height, status: "saved",
    customData: freshCustom(old.customData, next),
  }, rand);
  const box0 = { x: old.x, y: old.y, width: old.width, height: old.height };
  const box1 = { x: old.x, y: old.y, width, height };
  const rotated = !!old.angle;   // a rotated formula leaves arrow ends as they are (Excalidraw re-fits them the next time it is moved)
  return elements.map((e) => {
    if (e.id === id) return el;
    if (rotated || e.isDeleted || e.type !== "arrow") return e;
    if (!((e.startBinding && e.startBinding.elementId === id) || (e.endBinding && e.endBinding.elementId === id))) return e;
    const patch = adjustArrow(e, id, box0, box1);
    return patch ? bumpWith(e, patch, rand) : e;
  });
}

/** Color from the formula's SVG text: to preserve the original color when editing an old element (no customData.color) */
export function colorFromSvg(svg) {
  const m = /(?:fill|stroke)="(#[0-9a-fA-F]{6})"/.exec(String(svg || ""));
  return m ? m[1] : null;
}

/** data URL -> text (svg) */
export function dataUrlText(url) {
  const s = String(url || "");
  const i = s.indexOf(",");
  if (i < 0) return "";
  const body = s.slice(i + 1);
  try {
    if (/;base64/.test(s.slice(0, i))) return decodeURIComponent(escape(atob(body)));
    return decodeURIComponent(body);
  } catch (_) { return ""; }
}
