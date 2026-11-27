// Once after a stroke ends: combine straightening, smoothing and the highlighter's chosen thickness into 'the next version of the same element' (pure logic, tests: straighten.test.mjs).
// Same id with version +1 and a new versionNonce, so even if the original stroke reaches other clients first, the corrected one wins (server wins(): higher version).
import { canStraighten, straighten, sizeOf, MIN_LEN_PX, TAIL_PX } from "./straighten.js";
import { smoothStroke } from "./smooth.js";

export { HL_OPACITY } from "./hlcolor.js";   // Highlighter opacity (%): 50
import { HL_OPACITY } from "./hlcolor.js";
export const HL_PRESSURE = 0.5; // Highlighter: the chosen thickness instead of pen pressure

/**
  * el: the freedraw element that just ended. opts: { held, shift, smooth: 'off'|'low'|'high', hl, zoom, rand }
  * Returns: { el: changed element (same id, version +1), what: ['straight' | 'smooth' | 'hl'] }, or null if there is nothing to change.
 */
export function correctStroke(el, { held = false, shift = false, smooth = "low", hl = false, zoom = 1, rand = Math.random } = {}) {
  if (!el || el.type !== "freedraw" || el.isDeleted || !el.points || el.points.length < 2) return null;
  let points = el.points, pressures = el.pressures || [];
  const what = [];
  if (held && canStraighten(points, { minLen: MIN_LEN_PX / (zoom || 1), tail: TAIL_PX / (zoom || 1) })) {
    ({ points, pressures } = straighten(points, pressures, { snap: shift }));
    what.push("straight");
  } else if (smooth !== "off") {
    const s = smoothStroke(points, pressures, smooth);
    if (s.changed) { points = s.points; pressures = s.pressures; what.push("smooth"); }
  }
  let simulatePressure = el.simulatePressure;
  if (hl) {   /* highlighter: even out the thickness (turn off pen-thickness simulation) */
    simulatePressure = false;   // Opacity was already set to HL_OPACITY when drawing (appState at the moment the pen touches); match it here too
    pressures = points.map(() => HL_PRESSURE);
    what.push("hl");
  }
  if (!what.length) return null;
  if (pressures.length && pressures.length !== points.length) pressures = points.map((_, i) => pressures[Math.min(i, pressures.length - 1)]);
  return {
    el: { ...el, ...(hl ? { opacity: HL_OPACITY } : null), points, pressures, simulatePressure, ...sizeOf(points), version: el.version + 1, versionNonce: Math.floor(rand() * 2 ** 31), updated: Date.now() },
    what,
  };
}
