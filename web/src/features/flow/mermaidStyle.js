// Tidy mermaid -> flowchart results: pure logic (tests: web/tests/mermaidstyle.test.mjs).
// Excalidraw's mermaid dialog (TTDDialog) inserts results with hand-drawn lines (roughness 1) and handwriting (Excalifont), which clash with shapes drawn by the shape tools (roughness 0 and
// the app body font). Pick only the new elements right after insertion, make lines straight and text use the app body font, and re-measure bound text
// with the new font width (the label of convertToExcalidrawElements: centered in the box or on the arrow, line wrapping, and the box height grows if the text is long).
const NODE = new Set(["rectangle", "diamond", "ellipse"]);
const bump = (e, patch, rand) => ({ ...e, ...patch, version: e.version + 1, versionNonce: Math.floor(rand() * 2 ** 31) });

/**
 * elements: the whole scene (including deleted), fresh: set of new element ids, fontFamily: app body font, convert: Excalidraw convertToExcalidrawElements.
 * Returns: the changed scene (null if nothing changed).
 */
export function restyleMermaid(elements, fresh, fontFamily, convert, rand = Math.random) {
  const ids = fresh instanceof Set ? fresh : new Set(fresh);
  const out = new Map(elements.map((e) => [e.id, e]));
  let changed = false;
  for (const e of elements) {
    if (!ids.has(e.id) || e.isDeleted || e.type === "text") continue;
    if (e.roughness !== 0) { out.set(e.id, bump(e, { roughness: 0 }, rand)); changed = true; }
  }
  for (const t of elements) {
    if (!ids.has(t.id) || t.isDeleted || t.type !== "text") continue;
    const text = t.originalText ?? t.text;
    const c = t.containerId && out.get(t.containerId);
    if (!c) {   // unbound text: font only (Excalidraw measures the width on the next draw)
      if (t.fontFamily !== fontFamily) { out.set(t.id, bump(t, { fontFamily }, rand)); changed = true; }
      continue;
    }
    const lines = text.split("\n").length;
    const fit = (size) => {
      const sk = { type: c.type, id: c.id, x: c.x, y: c.y, width: c.width, height: c.height, angle: c.angle || 0,
        ...(c.points ? { points: c.points } : null),
        label: { text, fontSize: size, fontFamily, strokeColor: t.strokeColor, textAlign: t.textAlign, verticalAlign: t.verticalAlign } };
      const r = convert([sk], { regenerateIds: false });
      return { made: r.find((e) => e.type === "text"), box: r.find((e) => e.id === c.id) };
    };
    let res = null;
    try {
      res = fit(t.fontSize);
      // If the new font is wider and a word breaks (e.g. '판단' in a diamond -> '판\n단'), shrink the text by 2px at a time (minimum 12px).
      // Why we don't widen the box: arrow ends attached to the box do not follow with updateScene
      for (let size = t.fontSize - 2; NODE.has(c.type) && res.made && res.made.text.split("\n").length > lines && size >= 12; size -= 2) {
        const r2 = fit(size); if (r2.made) res = r2;
      }
    } catch (_) { res = null; }
    const made = res && res.made, box = res && res.box;
    if (!made) { out.set(t.id, bump(t, { fontFamily }, rand)); changed = true; continue; }
    out.set(t.id, bump(t, { text: made.text, originalText: text, fontFamily: made.fontFamily, fontSize: made.fontSize ?? t.fontSize, lineHeight: made.lineHeight,
      x: made.x, y: made.y, width: made.width, height: made.height }, rand));
    if (NODE.has(c.type) && box && box.height > c.height) out.set(c.id, bump(out.get(c.id), { height: box.height }, rand));
    changed = true;
  }
  return changed ? elements.map((e) => out.get(e.id)) : null;
}
