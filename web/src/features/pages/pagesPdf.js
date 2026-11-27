// All-pages PDF: build a white-background (or 'as on screen') JPEG for each page in turn and bundle them with pdf.js. Browser only.
// Memory: only one canvas is alive at a time (buildExport releases each page's canvas right away); only the JPEG bytes remain.
//   Desktop: long side 2000 px (a 16:9 page is 2000x1125 = 2.25 M pixels, canvas ~9 MB), JPEG quality 0.85 -> usually 0.1-0.5 MB per page.
//   Phone (coarse pointer, width <= 760): long side 1600 px (1.44 M pixels, canvas ~5.8 MB).
//   Page limit 100: beyond that only the first 100 pages are included and the user is told (keeps PDF chunks + the Blob copy from exceeding tens of MB on a phone).
import { buildExport } from "../export/exporters.js";
import { listPages } from "./pages.js";
import { pdfParts } from "./pdf.js";

export const PDF_LIMITS = { longSide: 2000, longSidePhone: 1600, quality: 0.85, maxPages: 100 };
const yieldToUI = () => new Promise((r) => setTimeout(r, 0));

// deps = { exportToCanvas, ensureSceneFonts } (same as buildExport). onProgress(i, n) is called before each page is built.
export async function buildPagesPdf(excal, deps, { background = "white", theme = "light", phone = false, title = "", signal, onProgress } = {}) {
  // Freeze the scene at the moment of the click, so the page count and content don't get mixed up if others draw or delete pages while we build.
  // Use the live getFiles so we can still wait for image files that arrive late.
  const els = excal.getSceneElements(), st = excal.getAppState();
  const snap = { getSceneElements: () => els, getAppState: () => st, getFiles: () => excal.getFiles() };
  const all = listPages(els);
  if (!all.length) throw Object.assign(new Error("장이 없어요"), { code: "empty" });
  const pages = all.slice(0, PDF_LIMITS.maxPages);
  const longSide = phone ? PDF_LIMITS.longSidePhone : PDF_LIMITS.longSide;
  const bg = background === "transparent" ? "white" : background;   // JPEG has no transparency
  const out = [];
  const fontsFailed = new Set();
  let filesMissing = 0, capped = false, darkFallback = false;
  for (let i = 0; i < pages.length; i++) {
    if (signal && signal.aborted) throw Object.assign(new Error("취소됨"), { code: "cancelled" });
    if (onProgress) onProgress(i, pages.length);
    const p = pages[i];
    const r = await buildExport(snap, deps, {
      scope: "frame", frameId: p.id, background: bg, theme, padding: 0, signal,
      scale: longSide / Math.max(1, p.width, p.height), mime: "image/jpeg", quality: PDF_LIMITS.quality,
      waitFilesMs: i === 0 ? 3000 : 600,   // wait long enough for image files on the first page, so it doesn't add 3 s to every page
    });
    out.push({ jpeg: new Uint8Array(await r.blob.arrayBuffer()), width: r.width, height: r.height });
    (r.fontsFailed || []).forEach((f) => fontsFailed.add(f));
    filesMissing += r.filesMissing || 0; capped = capped || r.capped; darkFallback = darkFallback || r.darkFallback;
    await yieldToUI();
  }
  const blob = new Blob(pdfParts(out, { title }), { type: "application/pdf" });
  return { blob, scope: "pdf", pages: out.length, pagesSkipped: all.length - pages.length, width: out[0].width, height: out[0].height,
    capped, fontsFailed: [...fontsFailed], filesMissing, darkFallback };
}
