// Import (browser side). The conversion and validation live on the server (server/importers.py); the browser does just two things:
//   1) decide whether a dropped or chosen file is an import target (images are still inserted directly by Excalidraw)
//   2) detach images from an .excalidraw document and upload them first to /files (image id = SHA-1 of content, same as the server),
//      then send the image-free document to /import, so one request stays under the server limit (12 MB) even with many images.
// The pure functions (isImageFile, shouldIntercept, sniffMime, stripDoc, viewCenter) are tested by web/tests/importfile.test.mjs.
import { sha1js } from "../../math.js";   // same SHA-1 even over http (LAN), where crypto.subtle is unavailable
import { tr } from "../../i18n.js";

export const IMAGE_MIMES = ["image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"];
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg)$/i;
export const MAX_IMPORT_BYTES = 50 * 1024 * 1024;   // same as the server, CLI and MCP
export const MAX_FILE = 10 * 1024 * 1024;           // one image (length of the data URL): the server's /files limit
export const MAX_BODY = 12 * 1024 * 1024;            // server MAX_BODY (one request): measured as the UTF-8 bytes of the whole body to be sent
export const SCENE_MIME = "application/vnd.excalidraw+json";   // marker that an image embeds a scene (PNG tEXt keyword, SVG comment)
export const ACCEPT = ".excalidraw,.excalidrawlib,.json,application/json,image/png,image/jpeg,image/gif,image/webp,image/svg+xml";

export class ImportError extends Error {}

/** Whether this is an image file (by MIME, or by extension if the MIME is empty). */
export function isImageFile(f) {
  const t = String((f && f.type) || "").toLowerCase();
  const n = String((f && f.name) || "");
  if (IMAGE_MIMES.includes(t)) return true;
  return IMAGE_EXT.test(n) && (!t || t.startsWith("image/"));
}

/** Images that can embed a scene (PNG, SVG): when dropped, Excalidraw replaces the local view wholesale with the embedded scene. */
export function mayEmbedScene(f) {
  const t = String((f && f.type) || "").toLowerCase();
  const n = String((f && f.name) || "");
  return t === "image/png" || t === "image/svg+xml" || (!t && /\.(png|svg)$/i.test(n));
}

/** Whether the image bytes carry an Excalidraw scene marker: PNG tEXt/iTXt keyword, or SVG payload-type comment (same as server has_embedded_scene). */
export function hasEmbeddedScene(b) {
  const mime = sniffMime(b);
  if (mime === "image/png") {
    const key = new TextEncoder().encode(SCENE_MIME);
    for (let i = 8; i + 12 <= b.length;) {
      const n = ((b[i] << 24) >>> 0) + (b[i + 1] << 16) + (b[i + 2] << 8) + b[i + 3];
      const kind = String.fromCharCode(b[i + 4], b[i + 5], b[i + 6], b[i + 7]);
      if (kind === "IEND" || i + 8 + n > b.length) return false;
      if ((kind === "tEXt" || kind === "iTXt") && n > key.length && b[i + 8 + key.length] === 0 && key.every((c, j) => b[i + 8 + j] === c)) return true;
      i += 12 + n;
    }
    return false;
  }
  if (mime === "image/svg+xml") return new TextDecoder().decode(b).includes(`payload-type:${SCENE_MIME}`);
  return false;
}

/** Splits dropped files: docs go to the sheet (non-images), maybe are images that need checking for an embedded scene (PNG, SVG), plain go straight to Excalidraw. */
export function classifyDrop(files) {
  const out = { docs: [], maybe: [], plain: [] };
  for (const f of Array.from(files || [])) {
    if (!isImageFile(f)) out.docs.push(f);
    else if (mayEmbedScene(f)) out.maybe.push(f);
    else out.plain.push(f);
  }
  return out;
}

/** Actual bytes of the body to send (JSON to UTF-8): a " inside content becomes \" and each Korean character is 3 bytes. */
export function bodyBytes(body) { return new TextEncoder().encode(JSON.stringify(body)).length; }
export function tooBigMessage(n) {   // message read by a guest who arrived via a link: guests do not have tempboard
  return tr(`파일이 너무 커서 화면에서 넣을 수 없어요(${(n / 1024 / 1024).toFixed(1)} MB · 한도 ${MAX_BODY / 1024 / 1024} MB). 보드를 연 사람에게 파일을 보내 주세요 — 그 사람은 컴퓨터에서 tempboard import 로 넣을 수 있어요.`,
    `This file is too big to add here (${(n / 1024 / 1024).toFixed(1)} MB · limit ${MAX_BODY / 1024 / 1024} MB). Send it to the person who opened the board — they can add it on their computer with tempboard import.`);
}

/** Toast to show after moving to a new board: the board view is redrawn, so the next view picks it up and shows it. */
let pending = null;
export function setPendingToast(t) { pending = t; }
export function takePendingToast() { const t = pending; pending = null; return t; }

/** New elements arriving by broadcast that belong to the merged batch (inside the response bbox): used on the merging user's screen to select and fit them. */
export function freshInBox(elements, before, bb) {
  const tol = 2;
  return elements.filter((e) => !e.isDeleted && !before.has(e.id)
    && (!bb || (e.x >= bb[0] - tol && e.y >= bb[1] - tol && e.x <= bb[2] + tol && e.y <= bb[3] + tol)));
}
function checkBody(body) {
  const n = bodyBytes(body);
  if (n > MAX_BODY - 4096) throw new ImportError(tooBigMessage(n));
}

/** Whether to intercept the drop: there are files and at least one is not an image (.excalidraw, .excalidrawlib, JSON, anything else).
  *  If only images, Excalidraw inserts them as before. Drags without files (library items, text) are left alone. */
export function shouldIntercept(files) {
  const list = Array.from(files || []);
  return list.length > 0 && list.some((f) => !isImageFile(f));
}

/** MIME of an image judged by its file header (magic), same as server importers.sniff_mime. */
export function sniffMime(b) {
  const at = (i) => b[i];
  if (b.length >= 8 && at(0) === 0x89 && at(1) === 0x50 && at(2) === 0x4e && at(3) === 0x47) return "image/png";
  if (b.length >= 3 && at(0) === 0xff && at(1) === 0xd8 && at(2) === 0xff) return "image/jpeg";
  const head = String.fromCharCode(...b.subarray(0, 12));
  if (head.startsWith("GIF87a") || head.startsWith("GIF89a")) return "image/gif";
  if (head.startsWith("RIFF") && head.slice(8, 12) === "WEBP") return "image/webp";
  const txt = new TextDecoder().decode(b.subarray(0, 8192)).replace(/^﻿/, "").trimStart().toLowerCase();
  if (txt.startsWith("<svg") || ((txt.startsWith("<?xml") || txt.startsWith("<!--") || txt.startsWith("<!doctype svg")) && txt.includes("<svg"))) return "image/svg+xml";
  return null;
}

function b64bytes(s) {
  const bin = atob(s.replace(/\s+/g, ""));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Validates an image data URL: returns {id (SHA-1), mimeType, dataURL}, or null (corrupt, wrong format, header mismatch, too large). */
export function checkDataURL(url, sha1 = sha1js) {
  if (typeof url !== "string" || url.length > MAX_FILE) return null;
  const m = /^data:(image\/(?:png|jpeg|gif|webp|svg\+xml));base64,([A-Za-z0-9+/=\s]*)$/.exec(url);
  if (!m) return null;
  let bytes;
  try { bytes = b64bytes(m[2]); } catch { return null; }
  if (sniffMime(bytes) !== m[1]) return null;
  return { id: sha1(bytes), mimeType: m[1], dataURL: url };
}

/** .excalidraw document to an image-free document plus images to upload. Image ids become the content SHA-1 and the elements' fileId is changed to match.
  *  Corrupt images and their image elements are dropped (the server follows the same rule). Returns { doc, files:[{id,mimeType,dataURL}], dropped } */
export function stripDoc(doc, sha1 = sha1js) {
  const remap = new Map(), bad = new Set(), files = new Map();
  for (const [fid, rec] of Object.entries((doc && doc.files) || {})) {
    if (!rec || typeof rec !== "object") { bad.add(fid); continue; }
    if (rec.dataURL == null) { remap.set(fid, fid); continue; }   // already detached document
    const r = checkDataURL(rec.dataURL, sha1);
    if (!r) { bad.add(fid); continue; }
    remap.set(fid, r.id);
    files.set(r.id, r);
  }
  let dropped = 0;
  const elements = [];
  for (const e of Array.isArray(doc && doc.elements) ? doc.elements : []) {
    if (e && e.type === "image" && !e.isDeleted && typeof e.fileId === "string") {
      if (bad.has(e.fileId)) { dropped++; continue; }
      if (remap.has(e.fileId)) { elements.push({ ...e, fileId: remap.get(e.fileId) }); continue; }
    }
    elements.push(e);
  }
  const outFiles = {};
  for (const [fid, nid] of remap) outFiles[nid] = { id: nid, mimeType: files.has(nid) ? files.get(nid).mimeType : ((doc.files[fid] || {}).mimeType || "") };
  return { doc: { type: "excalidraw", version: 2, source: "tempboard-import", elements, files: outFiles }, files: [...files.values()], dropped };
}

/** Center of the currently visible view (scene coordinates): where to merge. */
export function viewCenter(st) {
  const z = (st && st.zoom && st.zoom.value) || 1;
  return { x: Math.round(-st.scrollX + st.width / 2 / z), y: Math.round(-st.scrollY + st.height / 2 / z) };
}

const asDocName = (n) => (/\.(excalidraw|excalidrawlib|json)$/i.test(n) ? n : `${String(n || "import").replace(/\.[^.]*$/, "")}.excalidraw`);
const readAs = (file, how) => new Promise((ok, no) => {
  const r = new FileReader();
  r.onload = () => ok(r.result);
  r.onerror = () => no(new ImportError(tr("파일을 읽지 못했어요", "Couldn't read the file")));
  r[how](file);
});
async function imageSize(url) {
  return new Promise((ok) => {
    const img = new Image();
    img.onload = () => ok([img.naturalWidth || 400, img.naturalHeight || 300]);
    img.onerror = () => ok([400, 300]);
    img.src = url;
  });
}

/** Chosen or dropped file to send-ready { name, title, content, files, kind, count } (nothing is sent to the server). */
export async function prepareFile(file) {
  if (!file) throw new ImportError(tr("파일이 없어요", "No file"));
  if (file.size > MAX_IMPORT_BYTES) throw new ImportError(tr("파일이 너무 커요(50 MB 까지)", "The file is too big (up to 50 MB)"));
  const title = String(file.name || tr("가져온 보드", "Imported board")).replace(/\.(excalidraw|excalidrawlib|json|png|jpe?g|gif|webp|svg)$/i, "").slice(0, 80) || tr("가져온 보드", "Imported board");
  if (isImageFile(file)) {
    const url = await readAs(file, "readAsDataURL");
    if (mayEmbedScene(file)) {
      const bytes = new Uint8Array(await readAs(file, "readAsArrayBuffer"));
      if (hasEmbeddedScene(bytes)) {   // image embedding a scene: the server unpacks it and imports that scene (the image itself is not inserted, same as Excalidraw)
        if (url.length > MAX_FILE) throw new ImportError(tr("장면이 든 그림이 너무 커요(약 7.5 MB 까지)", "The image with an embedded scene is too big (up to about 7.5 MB)"));
        const prep = { name: String(file.name || "scene.png").slice(0, 120), title: title.replace(/\.excalidraw$/i, "") || title, content: url, files: [], kind: "embedded", count: null };
        checkBody({ name: prep.name, content: prep.content, title: prep.title, mode: "merge", at: { x: 0, y: 0 }, client: "imp-00000000" });
        return prep;
      }
    }
    const r = checkDataURL(url);
    if (!r) throw new ImportError(url.length > MAX_FILE ? tr("그림이 너무 커요(약 7.5 MB 까지)", "The image is too big (up to about 7.5 MB)") : tr("그림 파일이 아니에요(png · jpg · gif · webp · svg)", "Not an image file (png · jpg · gif · webp · svg)"));
    let [w, h] = await imageSize(url);
    const s = Math.min(1, 1000 / Math.max(w, h));
    w *= s; h *= s;
    const el = { id: `img-${Math.random().toString(36).slice(2, 12)}`, type: "image", x: 0, y: 0, width: w, height: h, fileId: r.id };
    const doc = { type: "excalidraw", version: 2, elements: [el], files: { [r.id]: { id: r.id, mimeType: r.mimeType } } };
    return { name: asDocName(file.name), title, content: JSON.stringify(doc), files: [r], kind: "image", count: 1 };
  }
  const text = await readAs(file, "readAsText");
  let doc;
  try { doc = JSON.parse(String(text).replace(/^﻿/, "")); } catch { throw new ImportError(tr("가져올 수 없는 파일이에요(.excalidraw · .excalidrawlib · 그림만)", "This file can't be imported (.excalidraw · .excalidrawlib · images only)")); }
  if (doc && (doc.type === "excalidraw" || doc.type === "excalidraw/clipboard")) {
    const s = stripDoc(doc);
    const content = JSON.stringify(s.doc);
    checkBody({ name: asDocName(file.name), content, title, mode: "merge", at: { x: 0, y: 0 }, client: "imp-00000000" });
    return { name: asDocName(file.name), title, content, files: s.files, kind: "excalidraw", count: s.doc.elements.filter((e) => e && !e.isDeleted).length, dropped: s.dropped };
  }
  if (doc && doc.type === "excalidrawlib") {
    const items = Array.isArray(doc.libraryItems) ? doc.libraryItems.length : Array.isArray(doc.library) ? doc.library.length : 0;
    checkBody({ name: asDocName(file.name), content: String(text), title, mode: "merge", at: { x: 0, y: 0 }, client: "imp-00000000" });
    return { name: asDocName(file.name), title, content: String(text), files: [], kind: "library", count: items };
  }
  throw new ImportError(tr("Excalidraw 파일이 아니에요", "Not an Excalidraw file"));
}

/** Send to the server. merge: images first, then /boards/:id/import. new: /boards/import, then the images go to the new board.
  *  client differs from this view's connection id (so this view also receives the broadcast). Returns the server response {board, added, skipped, ...} */
export async function runImport(api, { prepared, mode, boardId, at, client, onProgress }) {
  checkBody(mode === "new" ? { name: prepared.name, content: prepared.content, title: prepared.title, client }
    : { name: prepared.name, content: prepared.content, mode: "merge", at, client });   // before uploading images
  const up = async (bid) => {
    for (let i = 0; i < prepared.files.length; i++) {
      await api.putFile(bid, client, prepared.files[i]);
      if (onProgress) onProgress(i + 1, prepared.files.length);
    }
  };
  if (mode === "new") {
    const res = await api.importNew({ name: prepared.name, content: prepared.content, title: prepared.title, client });
    await up(res.board);
    return res;
  }
  await up(boardId);
  return api.importInto(boardId, { name: prepared.name, content: prepared.content, mode: "merge", at, client });
}
