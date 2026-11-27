// Applies inserting and editing a formula to the scene. Board.jsx calls only these two functions.
import { CaptureUpdateAction, convertToExcalidrawElements, viewportCoordsToSceneCoords } from "@excalidraw/excalidraw";
import { texToSvg, svgDataURL, sha1 } from "../../math.js";
import { replaceMath, naturalSize, colorFromSvg, dataUrlText } from "./replace.js";

const UPLOAD_WAIT_MS = 800;   // Maximum time to wait for uploading the file first; if it's late, the receiver waits for the 'file' event (hold.js)
export const isHex = (c) => typeof c === "string" && /^#[0-9a-fA-F]{3,8}$/.test(c);   // before putting a customData.color sent by someone else into the SVG or style
const decode = (url) => new Promise((res) => { try { const i = new Image(); i.onload = i.onerror = () => res(); i.src = url; } catch (_) { res(); } });
const within = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(r, ms))]);

/** Initial values for the edit dialog: if the selection is a formula, its formula, size and color (for old elements the color is read from the image) */
export function mathInitial(excal, sel, ink) {
  if (!(sel && sel.type === "image" && sel.customData && sel.customData.latex)) return null;
  let color = isHex(sel.customData.color) ? sel.customData.color : null;
  if (!color) {
    const f = (excal.getFiles() || {})[sel.fileId];
    color = (f && colorFromSvg(dataUrlText(f.dataURL))) || (isHex(ink) ? ink : "#1e1e1e");
  }
  return { ...sel.customData, color, id: sel.id, x: sel.x, y: sel.y };
}

/**
  * Inserts the typeset formula r({svg, width, height}). If old is given, it replaces that same element.
  * upload(file): uploads the image file to the server first (so a receiver can get the file before the element). Continues even on failure (onChange uploads it again).
  * select(): called right before applying. If true, the new formula is selected (false if the user changed tool or selection while waiting).
  * Returns: the id of the inserted or replaced element. If typesetting or hashing fails, it throws (the caller reports it with the dialog still open).
 */
export async function commitMath(excal, { old, tex, size, color, r, upload, select = () => true }) {
  if (!isHex(color)) color = "#1e1e1e";
  const fid = (await sha1(r.svg)).slice(0, 40);
  const file = { id: fid, mimeType: "image/svg+xml", dataURL: svgDataURL(r.svg), created: Date.now() };
  excal.addFiles([file]);
  await within(Promise.all([upload ? upload(file).catch(() => null) : null, decode(file.dataURL)]), UPLOAD_WAIT_MS);

  const scene = excal.getSceneElementsIncludingDeleted();
  const cur = old ? scene.find((e) => e.id === old.id && !e.isDeleted) : null;
  let els, id;
  if (cur) {
    let nat = naturalSize(cur);
    if (!nat && cur.customData && cur.customData.latex) {   // old element: re-typeset the old formula to measure its original size (to preserve the enlargement ratio)
      try { const o = await texToSvg(cur.customData.latex, { size: cur.customData.size || 32, color: "#000000" }); nat = { w: o.width, h: o.height }; } catch (_) { nat = null; }
    }
    els = replaceMath(excal.getSceneElementsIncludingDeleted(), cur.id, { fileId: fid, latex: tex, size, color, w: r.width, h: r.height }, nat);
    id = cur.id;
  }
  if (!els) {
    const st = excal.getAppState();
    const c = old ? { x: old.x, y: old.y } : viewportCoordsToSceneCoords({ clientX: window.innerWidth / 2 - r.width / 2, clientY: window.innerHeight / 2 - r.height / 2 }, st);
    const [el] = convertToExcalidrawElements([{ type: "image", fileId: fid, x: c.x, y: c.y, width: r.width, height: r.height, status: "saved",
      customData: { latex: tex, size, color, w0: r.width, h0: r.height } }]);
    els = [...excal.getSceneElementsIncludingDeleted(), el];
    id = el.id;
  }
  const pick = select();
  excal.updateScene({ elements: els, ...(pick ? { appState: { selectedElementIds: { [id]: true } } } : {}), captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  excal.addFiles([file]);   // fill the image cache right after the element points to the new file (without this, a gray placeholder shows for up to 0.5s)
  return { id, picked: pick };
}
