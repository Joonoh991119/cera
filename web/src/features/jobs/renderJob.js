// Render job (lazy chunk): draws board elements to a PNG exactly as people see them (same fonts, formulas, images), on an off-screen canvas.
// It never touches the scene or the undo history. Result: { png (base64), width, height, scale, x0, y0, count, tag, missing, missing_files }.
// tag = sceneTag of the drawn elements (the MCP describes the picture only when it matches its own copy). A region render is cropped to the region.
// Board coordinates of pixel (px, py) = (x0 + px / scale, y0 + py / scale).
import { exportToCanvas, getCommonBounds } from "@excalidraw/excalidraw";
import { ensureSceneFonts } from "../fonts/registry.js";
import { exportAppState, missingFileIds } from "../export/exporters.js";
import { renderArgs, monoElements, renderScale, pngBase64, sceneTag, regionPlan } from "./jobs.js";
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
  let plan = null;
  if (a.region) {
    const [minX, minY, maxX, maxY] = getCommonBounds(els);
    plan = regionPlan([minX, minY, maxX, maxY], a.region, pad, a.maxEdge, a.scale);
    if (!plan) return { ok: false, error: "nothing to draw in the region" };
  }
  let dims = null, canvas = null, out = null;
  try {
    canvas = await exportToCanvas({
      elements: els, files,
      appState: { ...excal.getAppState(), ...exportAppState("white", "light"), exportEmbedScene: false },
      exportPadding: pad,
      exportingFrame: picked.frame || undefined,
      // exportToCanvas ignores exportScale without this (see exporters.js). Region: the scale fits the crop, not the whole export.
      getDimensions: (w, h) => {
        dims = plan ? { scale: plan.scale, width: Math.max(1, Math.round(w * plan.scale)), height: Math.max(1, Math.round(h * plan.scale)) }
          : renderScale(w, h, a.maxEdge, a.scale);
        return dims;
      },
    });
    let x0, y0, s = dims ? dims.scale : 1;
    if (picked.frame) { x0 = picked.frame.x; y0 = picked.frame.y; }
    else { const [minX, minY] = getCommonBounds(els); x0 = minX - pad; y0 = minY - pad; }
    out = canvas;
    if (plan) {   // copy the region out of the full export
      const sx = Math.max(0, Math.floor((plan.crop.x - x0) * s)), sy = Math.max(0, Math.floor((plan.crop.y - y0) * s));
      const sw = Math.max(1, Math.min(canvas.width - sx, Math.round(plan.crop.w * s), a.maxEdge));
      const sh = Math.max(1, Math.min(canvas.height - sy, Math.round(plan.crop.h * s), a.maxEdge));
      out = document.createElement("canvas");
      out.width = sw; out.height = sh;
      const g = out.getContext("2d");
      g.fillStyle = "#fff"; g.fillRect(0, 0, sw, sh);
      g.drawImage(canvas, sx, sy, sw, sh, 0, 0, sw, sh);
      x0 += sx / s; y0 += sy / s;
    }
    const png = pngBase64(out.toDataURL("image/png"));
    if (!png) return { ok: false, error: "could not encode PNG" };
    return { ok: true, data: { png, width: out.width, height: out.height, scale: s, x0, y0, count: picked.elements.length, tag,
      missing: picked.missing, missing_files: missingFiles, renderer: "tab" } };
  } finally {
    if (canvas) canvas.width = canvas.height = 0;
    if (out && out !== canvas) out.width = out.height = 0;
  }
}
