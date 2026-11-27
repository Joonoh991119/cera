// Page flipping, new page and keys for board pages. Board.jsx only wires up this hook and PageBar.
// The page count and current page are recounted from the scene every time (pages made by others arrive via onChange too). Flipping is per user (follow mode comes later).
import { useCallback, useEffect, useRef, useState } from "react";
import { CaptureUpdateAction } from "@excalidraw/excalidraw";
import { listPages, currentPage, viewRect, stepPage, planNewPage, pageElementsFor, pageId, fitView, rectOf } from "./pages.js";
import { tr } from "../../i18n.js";

// Usable viewport: what is left after removing the top bars (phone top row, page bar, follow banner) and the bottom bars (tool dock, phone page bar). Follow mode uses it too.
export function safeRect() {
  const W = window.innerWidth, H = window.innerHeight;
  let top = 0, bottom = H;
  for (const el of document.querySelectorAll(".wb-mtop, .wb-pages-top, .wb-follow-bar")) { const r = el.getBoundingClientRect(); if (r.height && r.top < H / 2) top = Math.max(top, r.bottom); }
  for (const el of document.querySelectorAll(".wb-dock, .wb-pages-bottom")) { const r = el.getBoundingClientRect(); if (r.height && r.bottom > H / 2) bottom = Math.min(bottom, r.top); }
  const pad = 8;
  return { x: pad, y: top + pad, w: W - 2 * pad, h: Math.max(80, bottom - top - 2 * pad) };
}
const typing = (t) => t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable);
// Open dialogs: ours + Excalidraw's own dialogs (help, default export, clear confirm, mermaid), plus the sidebar and any open menu
const OVERLAY = ".wb-modal, .wb-follow-menu, .wb-export-back, .wb-msheet-back, .wb-sheet.open, .wb-pop, .wb-deskwrap.open, .excalidraw .Modal, .excalidraw .Dialog, .excalidraw .sidebar, .excalidraw .dropdown-menu";
const KEYS = { PageUp: -1, PageDown: 1, ArrowLeft: -1, ArrowRight: 1 };

export default function usePages({ excal, viewOnly }) {
  const [st, setSt] = useState({ count: 0, cur: -1 });
  const anim = useRef(0);
  const target = useRef(-1);   // target page index while flipping: if pressed again meanwhile, keep counting from here

  useEffect(() => {
    if (!excal) return undefined;
    let raf = 0;
    const update = () => {
      raf = 0;
      const list = listPages(excal.getSceneElements());
      const cur = currentPage(list, viewRect(excal.getAppState()));
      setSt((p) => (p.count === list.length && p.cur === cur ? p : { count: list.length, cur }));
    };
    const off = excal.onChange(() => { if (!raf) raf = requestAnimationFrame(update); });
    update();
    return () => { if (typeof off === "function") off(); cancelAnimationFrame(raf); };
  }, [excal]);

  // fit one page to the usable viewport, animating smoothly for 0.28 s (instantly if reduced motion is set)
  const show = useCallback((rect) => {
    if (!excal || !rect) return;
    cancelAnimationFrame(anim.current);
    const s0 = excal.getAppState();
    const safe = safeRect(), off = { left: s0.offsetLeft || 0, top: s0.offsetTop || 0 };
    const goal = fitView(rect, safe, off);
    const vx = safe.x + safe.w / 2 - off.left, vy = safe.y + safe.h / 2 - off.top;
    const z0 = s0.zoom.value, c0 = { x: vx / z0 - s0.scrollX, y: vy / z0 - s0.scrollY }, c1 = { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 };
    let reduce = false;
    try { reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { /* unavailable */ }
    const t0 = performance.now(), D = 280;
    const frame = (now) => {
      const t = reduce ? 1 : Math.min(1, (now - t0) / D), e = 1 - (1 - t) ** 3;
      const z = z0 * (goal.zoom / z0) ** e, cx = c0.x + (c1.x - c0.x) * e, cy = c0.y + (c1.y - c0.y) * e;
      excal.updateScene({ appState: { zoom: { value: z }, scrollX: vx / z - cx, scrollY: vy / z - cy }, captureUpdate: CaptureUpdateAction.NEVER });
      if (t < 1) anim.current = requestAnimationFrame(frame);
      else { anim.current = 0; target.current = -1; }
    };
    anim.current = requestAnimationFrame(frame);
  }, [excal]);
  // stop immediately if the user presses or wheels during the flip: changing zoom while a stroke is being drawn distorts strokes and shapes
  useEffect(() => {
    const stop = (e) => {
      if (e && e.target && e.target.closest && e.target.closest(".wb-pages")) return;   // pressing the page bar quickly several times keeps counting on
      cancelAnimationFrame(anim.current); anim.current = 0; target.current = -1;
    };
    window.addEventListener("pointerdown", stop, true);
    window.addEventListener("wheel", stop, { capture: true, passive: true });
    return () => { stop(null); window.removeEventListener("pointerdown", stop, true); window.removeEventListener("wheel", stop, { capture: true, passive: true }); };
  }, []);

  const go = useCallback((dir) => {
    if (!excal) return;
    const list = listPages(excal.getSceneElements());
    const view = viewRect(excal.getAppState());
    const from = anim.current && target.current >= 0 && target.current < list.length ? target.current : currentPage(list, view);
    const i = stepPage(list, from, dir, view);
    if (i >= 0) { show(rectOf(list[i])); target.current = i; }
  }, [excal, show]);

  // New page: append at the end with an id derived from the number (tbpage-<k>), see the header comment in pages.js. One undo removes it.
  const add = useCallback(() => {
    if (!excal || viewOnly) return;
    const all = excal.getSceneElementsIncludingDeleted();
    const plan = planNewPage(excal.getSceneElements(), viewRect(excal.getAppState()));
    const made = pageElementsFor(plan, all);
    const byId = new Map(made.map((e) => [e.id, e]));
    const next = all.map((e) => byId.get(e.id) || e);
    for (const e of made) if (!all.some((o) => o.id === e.id)) next.push(e);
    excal.updateScene({ elements: next, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    const goal = made.find((e) => e.id === pageId(plan.goto));
    show(rectOf(goal));
    if (made.length > 1) excal.setToast({ message: tr("지금 그림을 장 1 로 묶고, 새 빈 장 2 를 만들었어요", "Your drawing is now page 1, and a blank page 2 was added"), duration: 3000 });
  }, [excal, viewOnly, show]);

  // PageUp/PageDown always work; arrow keys only when nothing is selected (they move the selection otherwise). Uses e.code so it works during Korean IME input.
  useEffect(() => {
    if (!excal) return undefined;
    const onKey = (e) => {
      const dir = KEYS[e.code];
      if (!dir || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || typing(e.target)) return;
      // Don't flip pages while a dialog is open. Excalidraw scrolls the board behind the dialog by a screen on PageUp/PageDown (index.js
      // translateCanvas), so we don't forward those keys, but we don't call preventDefault, so scrolling inside the dialog (help etc.) still works.
      if (document.querySelector(OVERLAY)) { if (e.code.startsWith("Page")) e.stopPropagation(); return; }
      const s = excal.getAppState();
      if (s.editingTextElement || s.newElement || s.multiElement) return;
      if (e.code.startsWith("Arrow") && Object.values(s.selectedElementIds || {}).some(Boolean)) return;
      if (!listPages(excal.getSceneElements()).length) return;
      e.preventDefault(); e.stopPropagation();
      go(dir);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [excal, go]);

  return { count: st.count, cur: st.cur, go, add };
}
