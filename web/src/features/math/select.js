// Which formula to edit: small pure logic on the entry side (tests: web/tests/mathreplace.test.mjs).
// Pressing a grouped formula selects the whole group, so if 'exactly one of the selected is a formula', that one is edited.
export const isMath = (e) => !!(e && !e.isDeleted && e.type === "image" && e.customData && e.customData.latex);

/** The element if exactly one formula is among the selection. If at = scene coordinates {x, y} is given, the selected formula at that spot (double press) */
export function selectedMath(elements, selected, at = null) {
  const sel = (elements || []).filter((e) => selected && selected[e.id] && isMath(e));
  if (at) {
    return sel.slice().reverse().find((e) => at.x >= e.x && at.x <= e.x + e.width && at.y >= e.y && at.y <= e.y + e.height) || null;
  }
  return sel.length === 1 ? sel[0] : null;
}
