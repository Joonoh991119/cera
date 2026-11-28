// Render job (lazy chunk): draws board elements to a PNG exactly as people see them (same fonts, formulas, images), on an off-screen canvas.
// It never touches the scene or the undo history. Result: { png (base64), width, height, scale, x0, y0, count, tag, missing, missing_files }.
// tag = sceneTag of the drawn elements (the MCP describes the picture only when it matches its own copy). A region render is cropped to the region.
// Board coordinates of pixel (px, py) = (x0 + px / scale, y0 + py / scale).
import { exportToCanvas, getCommonBounds } from "@excalidraw/excalidraw";
import { ensureSceneFonts } from "../fonts/registry.js";
import { exportAppState, missingFileIds } from "../export/exporters.js";
import { renderArgs, monoElements, renderScale, pngBase64, sceneTag, regionPlan, inkMargin, growBounds, cropFrame, blankPixels } from "./jobs.js";
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
  const tag = sceneTag(picked.elements);
  let plan = null, cf = null;
  if (a.region) {
    // Region: draw only the crop. A crop frame (not on the board) as exportingFrame makes Excalidraw size the canvas from it and draw what overlaps it.
    // Excalidraw's overlap test uses geometric boxes (no stroke width, no arrowheads), so the frame is grown by inkMargin and the crop copied out.
    // frameId is dropped on the copies so children of a page are not filtered out (Excalidraw keeps only the exporting frame's own children).
    const m = inkMargin(els);
    const [minX, minY, maxX, maxY] = getCommonBounds(els);
    plan = regionPlan(growBounds([minX, minY, maxX, maxY], m), a.region, pad, a.maxEdge, a.scale);
    if (!plan) return { ok: false, error: "nothing to draw in the region" };
    cf = cropFrame(plan, m);
  }
  let dims = null, canvas = null, out = null;
  try {
    canvas = await exportToCanvas({
      elements: cf ? els.map((e) => (e.frameId ? { ...e, frameId: null } : e)) : els, files,
      appState: { ...excal.getAppState(), ...exportAppState("white", "light"), exportEmbedScene: false },
      exportPadding: pad,
      exportingFrame: cf ? cf.frame : picked.frame || undefined,
      // exportToCanvas ignores exportScale without this (see exporters.js). Region: margin + crop at the crop's scale.
      getDimensions: (w, h) => {
        dims = plan ? { scale: plan.scale, width: cf.width, height: cf.height } : renderScale(w, h, a.maxEdge, a.scale);
        return dims;
      },
    });
    let x0, y0, s = dims ? dims.scale : 1;
    if (picked.frame && !cf) { x0 = picked.frame.x; y0 = picked.frame.y; }
    else if (!cf) { const [minX, minY] = getCommonBounds(els); x0 = minX - pad; y0 = minY - pad; }
    out = canvas;
    if (cf) {   // copy the crop out (the margin was only there so Excalidraw draws strokes reaching in)
      out = document.createElement("canvas");
      out.width = plan.width; out.height = plan.height;
      const g = out.getContext("2d");
      g.fillStyle = "#fff"; g.fillRect(0, 0, out.width, out.height);
      g.drawImage(canvas, cf.sx, cf.sy, out.width, out.height, 0, 0, out.width, out.height);
      x0 = cf.frame.x + cf.sx / s; y0 = cf.frame.y + cf.sy / s;
    }
    // something was to be drawn: an all-white picture is a failure (the MCP falls back to the server raster), never an ok result
    if (blankPixels(out.getContext("2d").getImageData(0, 0, out.width, out.height).data)) return { ok: false, error: "blank picture" };
    const png = pngBase64(out.toDataURL("image/png"));
    if (!png) return { ok: false, error: "could not encode PNG" };
    return { ok: true, data: { png, width: out.width, height: out.height, scale: s, x0, y0, count: picked.elements.length, tag,
      missing: picked.missing, missing_files: missingFiles, renderer: "tab" } };
  } finally {
    if (canvas) canvas.width = canvas.height = 0;
    if (out && out !== canvas) out.width = out.height = 0;
  }
}
