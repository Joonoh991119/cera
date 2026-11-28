// Image export: takes scope, background and scale and builds a PNG Blob.
// The functions near the top are pure logic (tests: web/tests/exporters.test.mjs). Only buildExport runs in the browser.
import { pageElements } from "../pages/pages.js";
import { tr } from "../../i18n.js";
import { isAnchor } from "../proposal/proposal.js";
//
// scope is an open set: "all", "selection", "frame" (a single page, chosen by frameId). The all-pages PDF in features/pages/pagesPdf.js
// calls buildExport("frame", JPEG) from this file for each page and bundles the results.
// SVG export is not included: Excalidraw's SVG embeds only registered fonts and not our Korean fonts (CSS and runtime slots),
// so Korean text would fall back to a default font on the recipient's screen.

// Canvas limit: iOS Safari turns a canvas over about 16.7 M pixels (4096^2) into a blank image. Desktop uses the same value
// (anything bigger is too large to paste or email). No side exceeds 16384 px.
export const LIMITS = { maxArea: 16_777_216, maxSide: 16_384 };
export const SCALES = [1, 2, 3];
export const BACKGROUNDS = [["white", tr("흰 배경", "White")], ["transparent", tr("투명", "Transparent")], ["screen", tr("화면 그대로", "As on screen")]];

// The picked elements plus their dependents: text inside containers (containerId) and arrow labels; if only text is picked, its container; if a frame is picked, its contents.
export function expandSelection(elements, selectedIds) {
  const live = elements.filter((e) => !e.isDeleted);
  const ids = new Set(Object.entries(selectedIds || {}).filter(([, v]) => v).map(([k]) => k));
  if (!ids.size) return [];
  const byId = new Map(live.map((e) => [e.id, e]));
  for (const id of [...ids]) {
    const e = byId.get(id);
    if (e && e.containerId && byId.has(e.containerId)) ids.add(e.containerId);
  }
  const frames = new Set(live.filter((e) => ids.has(e.id) && (e.type === "frame" || e.type === "magicframe")).map((e) => e.id));
  let grew = true;
  while (grew) {   // adding a container brings in its text, adding a frame brings in its children (including the children's text)
    grew = false;
    for (const e of live) {
      if (ids.has(e.id)) continue;
      if ((e.containerId && ids.has(e.containerId)) || (e.frameId && frames.has(e.frameId))) { ids.add(e.id); grew = true; }
    }
  }
  return live.filter((e) => ids.has(e.id));
}

// Scope to elements to draw. An empty selection falls back to the whole board (fellBack).
// frame: elements overlapping that page (generously; Excalidraw's exportingFrame re-picks what is actually drawn and crops to the page boundary).
export function resolveScope(scope, elements, selectedIds, { frameId } = {}) {
  const live = elements.filter((e) => !e.isDeleted && !isAnchor(e));   // a proposal's dashed anchor box is screen-only (WP-6)
  if (scope === "frame") {
    const frame = live.find((e) => e.id === frameId && e.type === "frame");
    return { elements: frame ? pageElements(live, frame) : [], frame: frame || null, scope: "frame", fellBack: false };
  }
  if (scope === "selection") {
    const sel = expandSelection(live, selectedIds);
    if (sel.length) return { elements: sel, scope: "selection", fellBack: false };
    return { elements: live, scope: "all", fellBack: true };
  }
  return { elements: live, scope: "all", fellBack: false };
}

// Scale cap: if the canvas at 1x size (w, h) times the scale exceeds the limit, reduce the scale. A side under 1 px is not allowed.
export function capScale(w, h, scale, limits = LIMITS) {
  const want = Math.max(0.01, scale);
  const byArea = Math.sqrt(limits.maxArea / Math.max(1, w * h));
  const bySide = limits.maxSide / Math.max(1, w, h);
  const s = Math.min(want, byArea, bySide);
  const capped = s < want;
  const out = capped ? s * 0.999 : want;   // slightly below the limit so rounding does not exceed it
  return { scale: out, capped, width: Math.max(1, Math.round(w * out)), height: Math.max(1, Math.round(h * out)) };
}

// Background to Excalidraw appState fields.
// - white: white background, saved colors unchanged (how it looks on a light screen).
// - transparent: no background, saved colors unchanged (for pasting into documents or slides).
// - screen: exactly as on screen; on a dark screen, turn on Excalidraw's dark rendering (exportWithDarkMode).
//   Text written in light colors on a dark screen is invisible on a white background, so the default on a dark screen is screen (defaultBackground).
export function exportAppState(background, theme) {
  const dark = theme === "dark";
  if (background === "transparent") return { exportBackground: false, exportWithDarkMode: false, viewBackgroundColor: "#ffffff" };
  if (background === "screen") return { exportBackground: true, exportWithDarkMode: dark, viewBackgroundColor: "#ffffff" };
  return { exportBackground: true, exportWithDarkMode: false, viewBackgroundColor: "#ffffff" };
}
export const defaultBackground = (theme) => (theme === "dark" ? "screen" : "white");

// Image files not yet received (someone who just joined a view-only link, math images).
export function missingFileIds(elements, files) {
  const out = new Set();
  for (const e of elements) if (e.type === "image" && !e.isDeleted && e.fileId && !(files && files[e.fileId])) out.add(e.fileId);
  return [...out];
}

// File name: strip characters not allowed in file names from the board title. If tag is given, append it (single page '-page3', all-pages PDF '-board').
export function fileName(title, scope, ext = "png", tag) {
  const base = String(title || "board").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 80) || "board";
  const t = tag != null ? tag : scope === "selection" ? tr("선택", "selection") : "";
  return `${base}${t ? `-${t}` : ""}.${ext}`;
}

// Display of the reduced scale: it rounds down so that something like '3.0x' never looks as if it was not reduced. Below 0.1, show two decimals.
export function fmtScale(s) {
  const d = s >= 0.1 ? 10 : 100;
  return String(Math.max(1 / d, Math.floor(s * d) / d));
}

// Message after export: reduced scale, fonts not loaded, and image files not received are gathered into one toast.
export function exportNotices(r) {
  const out = [];
  if (r.capped) out.push(tr(`보드가 커서 ${fmtScale(r.scale)}배로 줄였어요`, `The board is large, so it was reduced to ${fmtScale(r.scale)}×`));
  if (r.fontsFailed && r.fontsFailed.length) out.push(tr("일부 글꼴을 다 받지 못했어요", "Some fonts didn't finish loading"));
  if (r.filesMissing) out.push(tr(`그림 ${r.filesMissing}개를 아직 못 받아 빠졌어요`, `${r.filesMissing} image(s) weren't loaded yet and were left out`));
  if (r.pagesSkipped) out.push(tr(`장이 많아 앞 ${r.pages}장만 넣었어요`, `Too many pages — only the first ${r.pages} were included`));
  return out;
}

// Dark rendering by hand: apply Excalidraw's THEME_FILTER "invert(93%) hue-rotate(180deg)" directly to pixels.
// Why: dark export draws with the canvas ctx.filter, but in WebKit, which does not support it, the filter is silently ignored and a light image
// comes out (verified 2026-11-20 with Playwright WebKit). Only then do we draw light and convert with this function. Alpha is left untouched.
// Limitation: Excalidraw does not invert photos (raster images) on a dark screen, but on this path photos get inverted too.
// The hue-rotate(180 deg) matrix follows the CSS Filter Effects 1 feColorMatrix hueRotate definition (cos = -1, sin = 0).
export function darkenPixels(data) {
  const a = 0.93, k = 1 - 2 * a, off = 255 * a;
  for (let i = 0; i < data.length; i += 4) {
    const r = off + data[i] * k, g = off + data[i + 1] * k, b = off + data[i + 2] * k;
    data[i] = -0.574 * r + 1.43 * g + 0.144 * b;       // Uint8ClampedArray clamps to 0-255 and rounds
    data[i + 1] = 0.426 * r + 0.43 * g + 0.144 * b;
    data[i + 2] = 0.426 * r + 1.43 * g - 0.856 * b;
  }
  return data;
}

// ---------------------------------------------------------------- browser
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Whether this browser's canvas actually applies ctx.filter: draw one white dot with invert and read it back (once only).
let filterWorks = null;
export function canvasFilterWorks() {
  if (filterWorks !== null) return filterWorks;
  try {
    const c = document.createElement("canvas"); c.width = c.height = 1;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.filter = "invert(100%)"; ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, 1, 1);
    filterWorks = ctx.getImageData(0, 0, 1, 1).data[0] < 128;
  } catch { filterWorks = false; }
  return filterWorks;
}

const toBlob = (canvas, mime = "image/png", quality) => new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error("그림을 만들지 못했어요"))), mime, quality));
const yieldToUI = () => new Promise((r) => setTimeout(r, 0));
const cancelled = () => Object.assign(new Error("취소됨"), { code: "cancelled" });
const check = (signal) => { if (signal && signal.aborted) throw cancelled(); };

// Rows per band: group the ImageData copy read at once to a few MB (at 5000 px width, 256 rows is about 5 MB).
export const bandRows = (width, budget = 4 * 1024 * 1024) => Math.max(16, Math.min(512, Math.floor(budget / (4 * Math.max(1, width)))));

// Dark pixel correction band by band: yield to the screen between bands and stop if cancelled.
async function darkenCanvas(canvas, signal) {
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("캔버스를 못 만들었어요");
  const step = bandRows(canvas.width);
  for (let y = 0; y < canvas.height; y += step) {
    check(signal);
    const h = Math.min(step, canvas.height - y);
    const img = ctx.getImageData(0, y, canvas.width, h);
    darkenPixels(img.data); ctx.putImageData(img, 0, y);
    await yieldToUI();
  }
}

// Builds the image Blob. Waits for fonts and image files first. Returns: { blob, width, height, scale, capped, scope, fellBack, fontsFailed, filesMissing, darkFallback }
// exportToCanvas (Excalidraw) and ensureSceneFonts (font table) are passed in by the caller (ExportSheet), so a node test can
// import this file as-is, and to avoid dynamically importing all of Excalidraw, which would break tree-shaking.
// If signal (AbortSignal) is aborted, it ends at the next yield point with code "cancelled" and releases the canvas immediately.
// scope "frame" + frameId (a single page, cropped to the page boundary with no margin), mime "image/jpeg" + quality (PDF pages; no transparency).
export async function buildExport(excal, { exportToCanvas, ensureSceneFonts }, { scope = "all", frameId, background = "white", scale = 1, theme = "light", padding = 16, waitFilesMs = 3000, signal, mime = "image/png", quality } = {}) {
  const picked = resolveScope(scope, excal.getSceneElements(), excal.getAppState().selectedElementIds, { frameId });
  if (!picked.elements.length) throw Object.assign(new Error("저장할 그림이 없어요"), { code: "empty" });
  const fonts = await ensureSceneFonts(picked.elements);
  check(signal);
  let files = excal.getFiles();
  for (let t = 0; missingFileIds(picked.elements, files).length && t < waitFilesMs; t += 150) { await sleep(150); check(signal); files = excal.getFiles(); }
  const look = exportAppState(background, theme);
  const darkFallback = look.exportWithDarkMode && !canvasFilterWorks();
  let dims = null, canvas = null;
  try {
    canvas = await exportToCanvas({
      elements: picked.elements,
      files,
      appState: { ...excal.getAppState(), ...look, exportWithDarkMode: look.exportWithDarkMode && !darkFallback, exportScale: scale, exportEmbedScene: false },
      exportPadding: padding,
      exportingFrame: picked.frame || undefined,   // a single page: Excalidraw draws only what overlaps the page, at the page size
      // exportToCanvas ignores appState.exportScale (without getDimensions it is always 1x), so the scale is passed here.
      getDimensions: (w, h) => { dims = capScale(w, h, scale); return dims; },
    });
    check(signal);
    if (darkFallback) await darkenCanvas(canvas, signal);
    check(signal);
    const blob = await toBlob(canvas, mime, quality);
    return { blob, ...dims, scope: picked.scope, fellBack: picked.fellBack, fontsFailed: fonts.failed, filesMissing: missingFileIds(picked.elements, files).length, darkFallback };
  } finally {
    if (canvas) canvas.width = canvas.height = 0;   // success, failure and cancel all release the large canvas memory immediately (iOS)
  }
}
