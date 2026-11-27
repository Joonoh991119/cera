// Text inside flowchart boxes on a phone (touch). Pure logic (tests: web/tests/flowlabel.test.mjs).
//
// Excalidraw's in-box text editor (wysiwyg) sets focus inside a setTimeout, so iOS does not show the keyboard.
// So on touch we take the input through the app's text sheet (Board.jsx openSheet/commitSheet) and insert the result following Excalidraw's 'bound text' rules:
// the text element's containerId = box id, and the box's boundElements gets { type: "text", id }. Size, position and line wrapping are
// delegated to the label of convertToExcalidrawElements (= bindTextToContainer -> redrawTextBoundingBox); if the text is long the box height grows too.

const NODE = new Set(["rectangle", "diamond", "ellipse"]);
const bumpWith = (e, patch, rand) => ({ ...e, ...patch, version: e.version + 1, versionNonce: Math.floor(rand() * 2 ** 31) });

/** The topmost flowchart box covering scene coordinates (x, y) (rotation ignored, judged by the box extent) */
export function hitNode(elements, x, y, pad = 4) {
  for (let i = elements.length - 1; i >= 0; i--) {
    const e = elements[i];
    if (e.isDeleted || e.locked || !NODE.has(e.type)) continue;
    if (x >= e.x - pad && x <= e.x + e.width + pad && y >= e.y - pad && y <= e.y + e.height + pad) return e;
  }
  return null;
}

/**
 * The box at a double-click position (x, y) on desktop: the box if it is time to edit its bound text, otherwise null (leave it to Excalidraw).
 * Excalidraw finds the topmost element at the double-click position by bounding box, but a frame sits above its inner elements (z-order) so the frame is hit first,
 * and since a frame cannot hold text, an unbound text got created at the clicked spot. Here we skip frames and look for a flowchart box.
 * If the click is on unbound text (editing that text), or a single arrow or line is selected (arrow text), leave it to Excalidraw.
 */
export function dblclickNode(elements, selectedIds, x, y) {
  const sel = elements.filter((e) => selectedIds && selectedIds[e.id] && !e.isDeleted);
  if (sel.length === 1 && (sel[0].type === "arrow" || sel[0].type === "line")) return null;
  const freeText = elements.some((e) => !e.isDeleted && e.type === "text" && !e.containerId && x >= e.x && x <= e.x + e.width && y >= e.y && y <= e.y + e.height);
  if (freeText) return null;
  return hitNode(elements, x, y);
}

/** The text bound to a box (null if none) */
export function labelOf(elements, node) {
  if (!node) return null;
  return elements.find((e) => e.type === "text" && !e.isDeleted && e.containerId === node.id) || null;
}

/**
 * Set the text of box nodeId to text (an empty text deletes it). Returns the whole scene (including deleted). Null if nothing changed.
 * style = { fontSize, fontFamily, strokeColor }. convert = Excalidraw convertToExcalidrawElements (a fake in tests).
 */
export function setLabel(elements, nodeId, text, style, convert, rand = Math.random) {
  const node = elements.find((e) => e.id === nodeId && !e.isDeleted);
  if (!node) return null;
  const old = labelOf(elements, node);
  if (!text) {
    if (!old) return null;
    return elements.map((e) => (e.id === old.id ? bumpWith(e, { isDeleted: true }, rand)
      : e.id === node.id ? bumpWith(e, { boundElements: (e.boundElements || []).filter((b) => b.id !== old.id) }, rand) : e));
  }
  const out = convert([{ type: node.type, id: node.id, x: node.x, y: node.y, width: node.width, height: node.height, angle: node.angle || 0,
    label: { text, fontSize: style.fontSize, fontFamily: style.fontFamily, strokeColor: style.strokeColor } }], { regenerateIds: false });
  const made = out.find((e) => e.type === "text");
  const box = out.find((e) => e.id === node.id) || node;
  if (!made) return null;
  const fit = { text: made.text, originalText: made.originalText ?? text, fontSize: made.fontSize, fontFamily: made.fontFamily, strokeColor: made.strokeColor,
    lineHeight: made.lineHeight, textAlign: made.textAlign, verticalAlign: made.verticalAlign, x: made.x, y: made.y, width: made.width, height: made.height, angle: made.angle };
  let label;
  if (old) label = bumpWith(old, fit, rand);
  else {
    // The new text id is derived from the box: if two people insert at the same moment into the same empty box, it is the same element and only one remains.
    // If a deleted text with the same id exists, revive it: its version must be raised above that, or the deleted state would win on others' screens and on the server.
    const id = labelId(node.id);
    const dead = elements.find((e) => e.id === id);
    const base = { ...made, id, groupIds: node.groupIds || [], frameId: node.frameId ?? null, containerId: node.id, isDeleted: false };
    label = dead ? bumpWith(dead, { ...base, version: dead.version, versionNonce: dead.versionNonce }, rand) : base;
  }
  // Bump the box's version only when it changes: bumping it when only the text is edited would conflict with another user dragging the box.
  const refs = node.boundElements || [];
  const textRefs = refs.filter((b) => b.type === "text");
  const nodePatch = {};
  if (box.height > node.height) nodePatch.height = box.height;
  // Keep only this one text reference. For new text (no old one), no live text exists, so drop stale text references whether they equal this text id or there are several.
  if (textRefs.length !== 1 || textRefs[0].id !== label.id) nodePatch.boundElements = [...refs.filter((b) => b.type !== "text"), { type: "text", id: label.id }];
  const touch = Object.keys(nodePatch).length > 0;
  const next = elements.map((e) => (e.id === node.id ? (touch ? bumpWith(e, nodePatch, rand) : e) : e.id === label.id ? label : e));
  return next.some((e) => e.id === label.id) ? next : [...next, label];
}

/** Box text id: derived from the box id (server id length limit is 64) */
export function labelId(nodeId) {
  return `${String(nodeId).slice(0, 56)}-label`;
}
