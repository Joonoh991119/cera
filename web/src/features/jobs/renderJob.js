// Render job (lazy chunk): draws board elements to a PNG exactly as people see them (same fonts, formulas, images), on an off-screen canvas.
// It never touches the scene or the undo history. Result: { png (base64), width, height, scale, x0, y0, count, missing, missing_files }.
// Board coordinates of pixel (px, py) = (x0 + px / scale, y0 + py / scale).
import { exportToCanvas, getCommonBounds } from "@excalidraw/excalidraw";
import { ensureSceneFonts } from "../fonts/registry.js";
import { exportAppState, missingFileIds } from "../export/exporters.js";
import { renderArgs, monoElements, renderScale, pngBase64 } from "./jobs.js";
import { pickElements } from "./pick.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export default async function renderJob(excal, rawArgs) {
  const a = renderArgs(rawArgs);
  const picked = pickElements(excal.getSceneElements(), a);
  if (!picked.elements.length) return { ok: false, error: picked.missing.length ? `not on the board: ${picked.missing.slice(0, 10).join(", ")}` : "nothing to draw" };
  await ensureSceneFonts(picked.elements, { timeout: 4000 });
  let files = excal.getFiles();
  for (let t = 0; missingFileIds(picked.elements, files).length && t < 4000; t += 150) { await sleep(150); files = excal.getFiles(); }
  const missingFiles = missingFileIds(picked.elements, files);   // still not here: drawn without them, and said so in the result
  const els = a.mono ? monoElements(picked.elements) : picked.elements;
  const pad = picked.frame ? 0 : a.pad;
  let dims = null, canvas = null;
  try {
    canvas = await exportToCanvas({
      elements: els, files,
      appState: { ...excal.getAppState(), ...exportAppState("white", "light"), exportEmbedScene: false },
      exportPadding: pad,
      exportingFrame: picked.frame || undefined,
      getDimensions: (w, h) => { dims = renderScale(w, h, a.maxEdge, a.scale); return dims; },   // exportToCanvas ignores exportScale without this (see exporters.js)
    });
    let x0, y0;
    if (picked.frame) { x0 = picked.frame.x; y0 = picked.frame.y; }
    else { const [minX, minY] = getCommonBounds(els); x0 = minX - pad; y0 = minY - pad; }
    const png = pngBase64(canvas.toDataURL("image/png"));
    if (!png) return { ok: false, error: "could not encode PNG" };
    return { ok: true, data: { png, width: canvas.width, height: canvas.height, scale: dims ? dims.scale : 1, x0, y0, count: picked.elements.length,
      missing: picked.missing, missing_files: missingFiles, renderer: "tab" } };
  } finally {
    if (canvas) canvas.width = canvas.height = 0;
  }
}
