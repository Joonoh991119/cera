// Flowchart pure logic: shape table, shortcut keys and '+' position computation. Tests: web/tests/flowconnect.test.mjs
import { tr } from "../../i18n.js";
//
// Creating the next box with '+' reuses Excalidraw 0.18's FlowChartCreator (the Cmd/Ctrl+arrow engine) as is.
// That engine places a box of the same shape, size and color where it does not overlap existing branches, attaches an elbow arrow to both ends (fixedPoint),
// and a single undo removes both. FlowChartCreator is not a public API, so it is invoked with a pair of key events (arrow keydown -> Cmd/Ctrl keyup).
// Only the idea of 'quick connect' was taken from drawio (no code).

/** Items in the shape popup: type is the Excalidraw tool, arrowType is the arrow style (elbow = bent) */
export const SHAPES = [
  { id: "rectangle", type: "rectangle", label: tr("사각형", "Rectangle"), key: "R", icon: "M4 6h16v12H4z" },
  { id: "diamond", type: "diamond", label: tr("마름모", "Diamond"), key: "D", icon: "M12 3l9 9-9 9-9-9z" },
  { id: "ellipse", type: "ellipse", label: tr("타원", "Ellipse"), key: "O", icon: "M12 5c5 0 9 3.1 9 7s-4 7-9 7-9-3.1-9-7 4-7 9-7z" },
  { id: "elbow", type: "arrow", arrowType: "elbow", label: tr("꺾인 화살표", "Elbow arrow"), short: tr("꺾인", "Elbow"), key: "A", icon: "M4 6h8v12h8M17 15l3 3-3 3" },
  { id: "straight", type: "arrow", arrowType: "sharp", label: tr("곧은 화살표", "Straight arrow"), short: tr("곧은", "Straight"), icon: "M4 20L20 4M12 4h8v8" },
  { id: "line", type: "line", label: tr("선", "Line"), key: "L", icon: "M4 20L20 4" },
];
export const SHAPE_TYPES = new Set(SHAPES.map((s) => s.type));

/** A single-letter key (lowercase English read from e.code) -> shape. A keeps the currently chosen arrow style (no arrowType). */
const KEYS = { r: "rectangle", d: "diamond", o: "ellipse", a: "arrow", l: "line" };
export function shapeForKey(k) {
  const type = KEYS[String(k || "").toLowerCase()];
  if (!type) return null;
  return type === "arrow" ? { id: "arrow", type } : SHAPES.find((s) => s.type === type);
}

/** Popup items matching the current tool and arrow style */
export function currentShape(tool, arrowType, lastId) {
  if (SHAPE_TYPES.has(tool)) {
    const hit = SHAPES.find((s) => s.type === tool && (!s.arrowType || s.arrowType === (arrowType === "elbow" ? "elbow" : "sharp")));
    if (hit) return hit;
  }
  return SHAPES.find((s) => s.id === lastId) || SHAPES[0];
}

const FLOW_NODE = new Set(["rectangle", "diamond", "ellipse"]);   // same as Excalidraw isFlowchartNodeElement

/** The element if the selection is exactly one flowchart box, otherwise null */
export function selectedNode(elements, selectedIds) {
  const ids = Object.keys(selectedIds || {}).filter((k) => selectedIds[k]);
  if (ids.length !== 1) return null;
  const el = elements.find((e) => e.id === ids[0]);
  return el && !el.isDeleted && !el.locked && FLOW_NODE.has(el.type) ? el : null;
}

export const DIRS = ["up", "right", "down", "left"];
export const arrowKey = (dir) => ({ up: "ArrowUp", right: "ArrowRight", down: "ArrowDown", left: "ArrowLeft" })[dir];

/**
 * Centers of the four '+' buttons (screen coordinates). box = screen-space box { x, y, w, h }, size = button diameter.
 * Buttons are always outside the box border: spaced by gap to clear Excalidraw's resize handles (just outside the border).
 * The top one is spaced further because the rotation handle sits at the top center of the border.
 */
export function plusSpots(box, size, gap = 14, topExtra = 22) {
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2, r = size / 2;
  return [
    { dir: "up", x: cx, y: box.y - gap - topExtra - r },
    { dir: "right", x: box.x + box.w + gap + r, y: cy },
    { dir: "down", x: cx, y: box.y + box.h + gap + r },
    { dir: "left", x: box.x - gap - r, y: cy },
  ];
}

/** Scene box -> screen box (same formula as Excalidraw sceneCoordsToViewportCoords) */
export function toViewport(el, st) {
  const z = (st.zoom && st.zoom.value) || 1;
  return { x: (el.x + st.scrollX) * z + (st.offsetLeft || 0), y: (el.y + st.scrollY) * z + (st.offsetTop || 0), w: el.width * z, h: el.height * z };
}

/** When to show '+': only with the selection tool and no dragging, resizing, text editing or dialog */
export function canShowPlus(st) {
  return !!st && st.activeTool && st.activeTool.type === "selection" && !st.viewModeEnabled && !st.editingTextElement && !st.newElement
    && !st.selectedElementsAreBeingDragged && !st.resizingElement && !st.isResizing && !st.isRotating && !st.multiElement
    && !st.selectionElement && !st.openDialog && !st.editingLinearElement && !st.croppingElementId;
}

/** Same formula as Excalidraw's Cmd/Ctrl check (KEYS.CTRL_OR_CMD): if it differs, the engine won't recognize the key */
export const isDarwin = (platform) => /Mac|iPod|iPhone|iPad/.test(platform || "");

/** A pair of key events that invoke FlowChartCreator (create -> release = insert) */
export function flowKeyEvents(dir, darwin) {
  const mod = darwin ? { metaKey: true } : { ctrlKey: true };
  const k = arrowKey(dir);
  return [
    ["keydown", { key: k, code: k, bubbles: true, cancelable: true, ...mod }],
    ["keyup", { key: darwin ? "Meta" : "Control", code: darwin ? "MetaLeft" : "ControlLeft", bubbles: true, cancelable: true }],
  ];
}

/**
 * Cleanup when '+' fails: on keydown FlowChartCreator first writes the new arrow id into the original box's boundElements.
 * If the insertion on keyup then fails (the board changed), an entry pointing at a nonexistent element is left and spreads to other users. Returns the box with only that entry removed (null if none).
 */
export function pruneBound(el, existingIds, rand = Math.random) {
  if (!el || !Array.isArray(el.boundElements)) return null;
  const kept = el.boundElements.filter((b) => existingIds.has(b.id));
  if (kept.length === el.boundElements.length) return null;
  return { ...el, boundElements: kept, version: el.version + 1, versionNonce: Math.floor(rand() * 2 ** 31) };
}
