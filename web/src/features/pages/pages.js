// Board pages (frames): pure logic. Tests: web/tests/pages.test.mjs
// A page = an Excalidraw frame element carrying customData.tbPage (its number). A frame without this marker (pasted, or from an old board) is not a page but
// just drawing. (Otherwise a name like '2024 plan' could make the number jump.) Page order comes only from element data (tbPage -> x -> id),
// so every user sees the same order and the same 'n/N' (no local information such as the order in which pages were made on my screen is used).
//
// Concurrent creation: a new page's id is derived from its number, not random (`tbpage-<k>`), and its position is derived only from scene elements (pages and drawings).
// If two people press 'New page' at the same moment, both create page k with the same id at the same position, and the server merge (version, versionNonce)
// keeps just one, so two overlapping pages never appear. If a deleted element with the same id exists, its version is bumped to revive it.

export const PAGE = { w: 1600, h: 900, gap: 160, pad: 48 };   // new page 16:9, gap between pages, margin around the first page 1 (scene units)
export const pageId = (k) => `tbpage-${k}`;

export function pageKey(e) {
  const k = e && e.customData ? Number(e.customData.tbPage) : NaN;
  return Number.isFinite(k) ? k : NaN;
}
export const isPage = (e) => !!e && !e.isDeleted && e.type === "frame" && Number.isFinite(pageKey(e));
const cmp = (a, b) => (pageKey(a) - pageKey(b)) || (a.x - b.x) || (a.y - b.y) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
// List of pages (excluding deleted ones), in the same order for everyone
export function listPages(elements) {
  const out = [];
  for (const e of elements || []) if (isPage(e)) out.push(e);
  return out.sort(cmp);
}

// current viewport (scene coordinates)
export function viewRect(st) {
  const z = (st && st.zoom && st.zoom.value) || 1;
  return { x: -st.scrollX, y: -st.scrollY, w: (st.width || 0) / z, h: (st.height || 0) / z };
}
const overlap = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
export const rectOf = (e) => ({ x: e.x, y: e.y, w: e.width, h: e.height });

// Current page: the page overlapping the viewport most (the earlier page on a tie). -1 if no page is in the viewport.
export function currentPage(pages, view) {
  let best = -1, bestA = 0;
  pages.forEach((p, i) => { const a = overlap(rectOf(p), view); if (a > bestA) { best = i; bestA = a; } });
  return best;
}
// Page closest to the viewport center (where < > go when no page is in the viewport)
export function nearestPage(pages, view) {
  const cx = view.x + view.w / 2, cy = view.y + view.h / 2;
  let best = -1, bestD = Infinity;
  pages.forEach((p, i) => { const d = Math.hypot(p.x + p.width / 2 - cx, p.y + p.height / 2 - cy); if (d < bestD) { best = i; bestD = d; } });
  return best;
}
// Index (0-based) of the page to go to with < (-1) or > (+1). -1 if there is nowhere to go.
export function stepPage(pages, cur, dir, view) {
  if (!pages.length) return -1;
  if (cur < 0) return nearestPage(pages, view);
  const next = cur + dir;
  return next >= 0 && next < pages.length ? next : -1;
}

// Bounding box of an element (scene coordinates): for lines and pen strokes from their points, for rotated elements the box around the rotated corners. An approximation for overlap tests (erring on the generous side).
export function elementBox(e) {
  if (Array.isArray(e.points) && e.points.length) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [px, py] of e.points) { x0 = Math.min(x0, px); y0 = Math.min(y0, py); x1 = Math.max(x1, px); y1 = Math.max(y1, py); }
    const pad = (e.strokeWidth || 1) * 2;
    return { x: e.x + x0 - pad, y: e.y + y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };
  }
  const w = e.width || 0, h = e.height || 0;
  if (!e.angle) return { x: e.x, y: e.y, w, h };
  const cx = e.x + w / 2, cy = e.y + h / 2, c = Math.abs(Math.cos(e.angle)), s = Math.abs(Math.sin(e.angle));
  const W = w * c + h * s, H = w * s + h * c;
  return { x: cx - W / 2, y: cy - H / 2, w: W, h: H };
}
// Elements overlapping a page (including the page itself, excluding other pages and children of other pages): used to wait for fonts and image files before saving.
// The elements actually drawn are picked again by Excalidraw (exportingFrame). This function only needs to be more generous than that.
export function pageElements(elements, page) {
  const r = rectOf(page);
  return (elements || []).filter((e) => !e.isDeleted && (e.id === page.id || (!isPage(e)
    && (!e.frameId || e.frameId === page.id) && (e.frameId === page.id || overlap(elementBox(e), r) > 0))));
}

// Expand a box to aspect ratio aspect (width/height) around its center, with minimum size min.
export function fitAspect(b, aspect, min) {
  let w = Math.max(b.w, min.w), h = Math.max(b.h, min.h);
  if (w / h > aspect) h = w / aspect; else w = h * aspect;
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
  return { x: Math.round(cx - w / 2), y: Math.round(cy - h / 2), w: Math.round(w), h: Math.round(h) };
}

// Finding free space: if the candidate box overlaps pages or drawings (all live elements in the scene, including strokes sticking out of a page) within the gap,
// push it to the right edge of the overlapping elements + gap and check again. Since it pushes to the maximum over ALL overlapping elements, the result does not depend on element array order:
// the same scene gives the same position (two people pressing at the same moment get the same position and id).
export function freeSpot(rect, boxes, gap = PAGE.gap) {
  const r = { ...rect };
  for (let n = 0; n < 10000; n++) {
    const g = { x: r.x - gap, y: r.y - gap, w: r.w + 2 * gap, h: r.h + 2 * gap };
    let push = -Infinity;
    for (const b of boxes) if (overlap(g, b) > 0 || (b.w === 0 || b.h === 0) && b.x >= g.x && b.x <= g.x + g.w && b.y >= g.y && b.y <= g.y + g.h) push = Math.max(push, b.x + b.w);
    if (push === -Infinity) return { ...r, x: Math.round(r.x), y: Math.round(r.y) };
    r.x = push + gap;
  }
  return r;
}
const obstacles = (elements) => (elements || []).filter((e) => !e.isDeleted).map((e) => (e.type === "frame" || e.type === "magicframe" ? rectOf(e) : elementBox(e)));

// Next number: max of our page numbers + 1. If a live element has that id as its frameId (someone else's stroke left in a deleted page),
// skip that number (so a revived 'new empty page' does not take that stroke back as a child).
function nextKey(elements, pages) {
  let k = (pages.length ? Math.max(...pages.map(pageKey)) : 0) + 1;
  const used = new Set((elements || []).filter((e) => !e.isDeleted && e.frameId).map((e) => e.frameId));
  while (used.has(pageId(k))) k += 1;
  return k;
}

// Plan for '+ New page': the pages to create and the page number to move to. If pages exist, it goes right of the last page (same height and size); if
// there is drawing at that spot, push right to free space (freeSpot). If there are no pages: if the viewport has drawing, create page 1 enclosing it + new empty page 2 (and go to page 2);
// if there is no drawing, an empty page 1 at the viewport center (pushed aside if off-screen drawing overlaps that spot).
// (An earlier design said 'right of the current page', but a next page already sits right of a middle page, so we always append at the end. That way simultaneous presses give the same position.)
export function planNewPage(elements, view) {
  const pages = listPages(elements);
  const boxes = obstacles(elements);
  if (pages.length) {
    const last = pages[pages.length - 1];
    const k = nextKey(elements, pages);
    const right = Math.max(...pages.map((p) => p.x + p.width));
    const r = freeSpot({ x: right + PAGE.gap, y: last.y, w: Math.round(last.width), h: Math.round(last.height) }, boxes);
    return { create: [{ k, ...r }], goto: k };
  }
  const inView = (elements || []).filter((e) => !e.isDeleted && overlap(e.type === "frame" ? rectOf(e) : elementBox(e), view) > 0);
  const k1 = nextKey(elements, []);
  if (!inView.length) {
    const r = freeSpot(fitAspect({ x: view.x + view.w / 2, y: view.y + view.h / 2, w: 0, h: 0 }, PAGE.w / PAGE.h, PAGE), boxes);
    return { create: [{ k: k1, ...r }], goto: k1 };
  }
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const e of inView) { const b = e.type === "frame" ? rectOf(e) : elementBox(e); x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h); }
  const one = fitAspect({ x: x0 - PAGE.pad, y: y0 - PAGE.pad, w: x1 - x0 + 2 * PAGE.pad, h: y1 - y0 + 2 * PAGE.pad }, PAGE.w / PAGE.h, PAGE);
  let k2 = k1 + 1;
  while ((elements || []).some((e) => !e.isDeleted && e.frameId === pageId(k2))) k2 += 1;
  const two = freeSpot({ x: one.x + one.w + PAGE.gap, y: one.y, w: one.w, h: one.h }, [...boxes, rectOf({ x: one.x, y: one.y, width: one.w, height: one.h })]);
  return { create: [{ k: k1, ...one }, { k: k2, ...two }], goto: k2 };
}

// Plan -> frame elements to insert. existing is the scene including deleted elements (if the same id is deleted, its version is bumped to revive it).
// The seed is also derived from the number: two pages created concurrently with the same version differ only in versionNonce and look identical.
export function pageElementsFor(plan, existing, now = Date.now()) {
  const byId = new Map((existing || []).map((e) => [e.id, e]));
  return plan.create.map((s) => {
    const id = pageId(s.k), old = byId.get(id);
    const base = {
      id, type: "frame", x: s.x, y: s.y, width: s.w, height: s.h, angle: 0,
      strokeColor: "#bbb", backgroundColor: "transparent", fillStyle: "solid", strokeWidth: 2, strokeStyle: "solid", roughness: 0, opacity: 100,
      groupIds: [], frameId: null, roundness: null, seed: 7919 * s.k, boundElements: null, link: null, locked: false,
      name: `장 ${s.k}`, customData: { tbPage: s.k }, isDeleted: false, updated: now,
      version: old ? old.version + 1 : 1, versionNonce: Math.floor(Math.random() * 2 ** 31),
    };
    return old ? { ...old, ...base, index: old.index } : { ...base, index: null };
  });
}

// Fit a page to the center of the 'usable viewport' (excluding the top and bottom bars): scrollX, scrollY, zoom.
// Excalidraw coordinates: screenX = (sceneX + scrollX) x zoom + offsetLeft.
export function fitView(page, safe, offset = { left: 0, top: 0 }, factor = 0.95, zoomRange = [0.1, 10]) {
  const z = Math.min(zoomRange[1], Math.max(zoomRange[0], Math.min(safe.w / page.w, safe.h / page.h) * factor));
  const cx = page.x + page.w / 2, cy = page.y + page.h / 2;
  const vx = safe.x + safe.w / 2 - offset.left, vy = safe.y + safe.h / 2 - offset.top;
  return { zoom: z, scrollX: vx / z - cx, scrollY: vy / z - cy };
}
