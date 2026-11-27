// Pen and stroke correction: a hook wired into Board. The UI is in InkOptions.jsx.
//
// What it does
// 1) When a stroke ends (Excalidraw's newElement goes from freedraw to none), rewrite that element once: a straight line if the pen paused, otherwise smoothed, and for the highlighter the chosen thickness.
//    Same id, version +1, new versionNonce. Board's onChange sends the changed scene, and the server and other clients pick the higher version (the corrected one).
//    The rewrite uses CaptureUpdateAction.NEVER, so it is not recorded separately in undo history. One undo therefore removes the corrected stroke entirely
//    (there is no step back to the shaky pre-correction stroke; if it was straightened wrongly, erase it and redraw without pausing).
// 2) Hold detection uses window pointer events (capture, passive), in screen px, with one distance computation per event.
// 3) Highlighter: the same freedraw tool with opacity, color and thickness presets. The highlighter values go into appState only 'the moment the pen touches the canvas'
//    and are restored to the pen values when the stroke ends, so highlighter color and opacity do not leak into pasted content, other tools or new elements.
//    Switching only changes the 'next pen to draw with'; it does not recolor selected elements (apply=false in setInkColor/setStroke).
// 4) Pen-only drawing: Excalidraw penMode. Excalidraw turns it on by itself the first time a pen touches the screen, so we notify once, and turn it back off if the user had turned it off before.
//    In penMode with the pen tool, Excalidraw ignores a single finger; we pick that up here so one finger pans the canvas (two fingers are still handled by Excalidraw).
import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { CaptureUpdateAction } from "@excalidraw/excalidraw";
import { holdStep, isHeld, canStraighten, HOLD_MS, HOLD_PX, HOLD_PX_TOUCH, MIN_LEN_PX, TAIL_PX } from "./straighten.js";
import { correctStroke, HL_OPACITY } from "./correct.js";
import { LEVELS } from "./smooth.js";

export { HL_COLORS } from "./hlcolor.js";   // Colors that read well on both light and dark themes
import { HL_COLORS, migrateHl } from "./hlcolor.js";
import { tr } from "../../i18n.js";
const HL_WIDTH = 5;
const PEN_QUIET_MS = 500;   // After a pen lifts, don't start a finger pan for this long
const PALM_PX = 40;         // A contact area (px) larger than this is treated as a palm

const store = {
  get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* storage blocked */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch (_) { /* storage blocked */ } },
};
const onCanvas = (e) => e.target instanceof HTMLCanvasElement && !!e.target.closest(".excalidraw");

export default function useInkCorrection({ excal, tool, viewOnly, ink, width, setInkColor, setStroke }) {
  const [smooth, setSmoothRaw] = useState(() => { const v = store.get("wb-ink-smooth", "low"); return LEVELS.includes(v) ? v : "low"; });
  const [straight, setStraightRaw] = useState(() => store.get("wb-ink-straight", "on") !== "off");
  const [mode, setModeRaw] = useState("pen");     // 'pen' | 'hl'; always starts as pen when reopened
  const [open, setOpen] = useState(false);        // Pen settings popover
  const [pen, setPen] = useState({ detected: false, on: false });   // Excalidraw penDetected · penMode
  const [badge, setBadge] = useState(null);       // Hold indicator { x, y }
  const cfg = useRef({});
  cfg.current = { smooth, straight, mode };
  const hl = useRef({ color: migrateHl(store.get("wb-hl-color", HL_COLORS[0][0])), width: +store.get("wb-hl-width", HL_WIDTH) || HL_WIDTH });
  const track = useRef(null);      // Pointer being drawn { id, s (hold state), px, shift, type }
  const release = useRef(null);    // { held, shift } of the stroke just released
  const timer = useRef(0);
  const penBack = useRef(null);    // Pen values to restore when entering highlighter { ink, width }; storage is only for restoring after a reload
  const penState = () => ({ currentItemStrokeColor: penBack.current ? penBack.current.ink : ink, currentItemStrokeWidth: penBack.current ? penBack.current.width : width, currentItemOpacity: 100 });
  const penStateRef = useRef(penState); penStateRef.current = penState;

  const setSmooth = (v) => { setSmoothRaw(v); store.set("wb-ink-smooth", v); };
  const setStraight = (v) => { setStraightRaw(v); store.set("wb-ink-straight", v ? "on" : "off"); };

  // ---------------- Highlighter <-> pen
  const setMode = useCallback((m) => {
    if (!excal || m === cfg.current.mode) return;
    if (m === "hl") {
      penBack.current = { ink, width };
      store.set("wb-pen-back", JSON.stringify(penBack.current));   /* so the pen color and thickness come back after a reload */
      setModeRaw("hl");
      setInkColor(hl.current.color, false); setStroke(hl.current.width, false);   /* only the values shown in the bar; the effect below leaves appState at the pen values */
    } else {
      let back = penBack.current;
      if (!back) { try { back = JSON.parse(store.get("wb-pen-back", "null")); } catch (_) { /* corrupt value */ } }
      penBack.current = null;
      store.del("wb-pen-back");
      setModeRaw("pen");
      excal.updateScene({ appState: { currentItemOpacity: 100 }, captureUpdate: CaptureUpdateAction.NEVER });
      if (back) { setInkColor(back.ink, false); setStroke(back.width, false); }
    }
  }, [excal, ink, width, setInkColor, setStroke]);
  // While in highlighter mode, remember the chosen color and thickness for the highlighter
  // appState holds the pen values (highlighter values only at the moment the pen touches)
  useEffect(() => {
    if (mode !== "hl") return;
    hl.current = { color: ink, width };
    store.set("wb-hl-color", ink); store.set("wb-hl-width", String(width));
    if (excal) excal.updateScene({ appState: penState(), captureUpdate: CaptureUpdateAction.NEVER });
  }, [mode, ink, width, excal]);   // eslint-disable-line react-hooks/exhaustive-deps
  // Leaving the pen tool also ends the highlighter (so text and shapes don't turn translucent)
  useEffect(() => { if (tool !== "freedraw" && mode === "hl") setMode("pen"); }, [tool, mode, setMode]);
  useEffect(() => { if (tool !== "freedraw") setOpen(false); }, [tool]);
  // A tab that was closed while in highlighter mode: restore the pen color and thickness
  const restored = useRef(false);
  useEffect(() => {
    if (!excal || restored.current) return;
    restored.current = true;
    let back = null;
    try { back = JSON.parse(store.get("wb-pen-back", "null")); } catch (_) { /* corrupt value */ }
    if (back) { store.del("wb-pen-back"); setInkColor(back.ink, false); setStroke(back.width, false); }
  }, [excal]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------- Rewrite once when the stroke ends
  useEffect(() => {
    if (!excal || viewOnly) return undefined;
    let active = null;   // freedraw id being drawn
    const finish = (id, rel) => {
      const t0 = performance.now();
      const all = excal.getSceneElementsIncludingDeleted();
      const el = all.find((e) => e.id === id);
      const c = cfg.current;
      const res = correctStroke(el, { held: c.straight && rel.held, shift: rel.shift, smooth: c.smooth, hl: rel.hl, zoom: excal.getAppState().zoom.value });
      const ms = performance.now() - t0;
      if (res) excal.updateScene({ elements: all.map((e) => (e.id === id ? res.el : e)), captureUpdate: CaptureUpdateAction.NEVER });
      window.__wbink = { id, what: res ? res.what : [], ms, total: performance.now() - t0, n: el ? el.points.length : 0, held: rel.held };   /* test hook */
    };
    const off = excal.onChange((_els, st) => {
      setPen((p) => (p.detected === !!st.penDetected && p.on === !!st.penMode ? p : { detected: !!st.penDetected, on: !!st.penMode }));
      const ne = st.newElement;
      if (ne && ne.type === "freedraw") { if (!active) active = { id: ne.id, hl: cfg.current.mode === "hl" }; return; }
      if (!active) return;
      const done = active; active = null;
      if (done.hl) excal.updateScene({ appState: penStateRef.current(), captureUpdate: CaptureUpdateAction.NEVER });   /* highlighter values apply to this stroke only */
      const rel = { ...(release.current || { held: false, shift: false }), hl: done.hl };
      release.current = null;
      setTimeout(() => finish(done.id, rel), 0);   // after Excalidraw has finished finalizing the stroke (actionFinalize)
    });
    return off;
  }, [excal, viewOnly]);

  // ---------------- Hold detection and finger panning in pen-only mode
  useEffect(() => {
    if (!excal || viewOnly) return undefined;
    const pan = { id: null, x: 0, y: 0, touches: new Set(), raf: 0, dx: 0, dy: 0 };
    const pen = { down: new Set(), upAt: -1e9 };   // No finger panning while a pen is down or has just lifted (palm)
    const penBusy = (t) => pen.down.size > 0 || t - pen.upAt < PEN_QUIET_MS;
    const hideBadge = () => { clearTimeout(timer.current); setBadge(null); };
    const arm = (t) => {   // Once held for HOLD_MS, show the indicator, but only if the current stroke could become a straight line
      clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        const tr = track.current; if (!tr || tr.s !== t || !cfg.current.straight) return;
        const ne = excal.getAppState().newElement;
        if (!ne || ne.type !== "freedraw" || !canStraighten(ne.points, { minLen: MIN_LEN_PX / excal.getAppState().zoom.value, tail: TAIL_PX / excal.getAppState().zoom.value })) return;
        setBadge({ x: tr.s.ax, y: tr.s.ay });
        try { if (navigator.vibrate) navigator.vibrate(12); } catch (_) { /* blocked */ }
      }, HOLD_MS + 5);
    };
    const down = (e) => {
      if (e.pointerType === "pen") { pen.down.add(e.pointerId); pan.id = null; }
      if (!onCanvas(e)) return;
      const st = excal.getAppState();
      if (e.pointerType === "touch") pan.touches.add(e.pointerId);
      if (st.activeTool.type !== "freedraw") return;
      setBadge(null);
      if (st.penMode && e.pointerType === "touch") {   // Pen-only: a finger pans the canvas (one finger only; with two, Excalidraw's two-finger pan takes over)
        const palm = Math.max(e.width || 0, e.height || 0) > PALM_PX;   // A large contact area means a palm
        pan.id = pan.touches.size === 1 && !palm && !penBusy(e.timeStamp) ? e.pointerId : null; pan.x = e.clientX; pan.y = e.clientY;
        return;
      }
      if (!e.isPrimary) return;
      if (cfg.current.mode === "hl") {   // Highlighter values apply to this stroke only: set before Excalidraw creates the element (within the same event)
        try { flushSync(() => excal.updateScene({ appState: { currentItemStrokeColor: hl.current.color, currentItemStrokeWidth: hl.current.width, currentItemOpacity: HL_OPACITY }, captureUpdate: CaptureUpdateAction.NEVER })); } catch (_) { /* during render */ }
      }
      const s = holdStep(null, e.clientX, e.clientY, e.timeStamp);
      track.current = { id: e.pointerId, s, px: e.pointerType === "touch" ? HOLD_PX_TOUCH : HOLD_PX, shift: e.shiftKey };
      release.current = null;
      arm(s);
    };
    const move = (e) => {
      if (pan.id !== null && e.pointerId === pan.id) {
        if (pan.touches.size !== 1 || penBusy(e.timeStamp)) { pan.id = null; return; }
        pan.dx += e.clientX - pan.x; pan.dy += e.clientY - pan.y; pan.x = e.clientX; pan.y = e.clientY;
        if (!pan.raf) pan.raf = requestAnimationFrame(() => {
          pan.raf = 0;
          const st = excal.getAppState(), z = st.zoom.value;
          excal.updateScene({ appState: { scrollX: st.scrollX + pan.dx / z, scrollY: st.scrollY + pan.dy / z }, captureUpdate: CaptureUpdateAction.NEVER });
          pan.dx = 0; pan.dy = 0;
        });
        return;
      }
      const tr = track.current; if (!tr || e.pointerId !== tr.id) return;
      tr.shift = e.shiftKey;
      const s = holdStep(tr.s, e.clientX, e.clientY, e.timeStamp, tr.px);
      if (s !== tr.s) { tr.s = s; setBadge((b) => (b ? null : b)); arm(s); }
    };
    const up = (e) => {
      if (e.pointerType === "pen" && pen.down.delete(e.pointerId)) pen.upAt = e.timeStamp;
      pan.touches.delete(e.pointerId);
      if (e.pointerId === pan.id) pan.id = null;
      const tr = track.current; if (!tr || e.pointerId !== tr.id) return;
      track.current = null;
      const s = holdStep(tr.s, e.clientX, e.clientY, e.timeStamp, tr.px);
      release.current = { held: e.type === "pointerup" && s === tr.s && isHeld(s, e.timeStamp), shift: e.shiftKey || tr.shift };
      hideBadge();
    };
    window.addEventListener("pointerdown", down, { capture: true, passive: true });
    window.addEventListener("pointermove", move, { capture: true, passive: true });
    window.addEventListener("pointerup", up, { capture: true, passive: true });
    window.addEventListener("pointercancel", up, { capture: true, passive: true });
    return () => {
      hideBadge(); cancelAnimationFrame(pan.raf);
      window.removeEventListener("pointerdown", down, { capture: true });
      window.removeEventListener("pointermove", move, { capture: true });
      window.removeEventListener("pointerup", up, { capture: true });
      window.removeEventListener("pointercancel", up, { capture: true });
    };
  }, [excal, viewOnly]);

  // ---------------- Pen-only drawing: notify once when a pen first touches; turn it back off for users who had turned it off before
  const told = useRef(false);
  useEffect(() => {
    if (!excal || !pen.detected || told.current) return;
    told.current = true;
    if (store.get("wb-penmode", "") === "off") { excal.updateScene({ appState: { penMode: false }, captureUpdate: CaptureUpdateAction.NEVER }); return; }
    if (store.get("wb-pen-told", "") !== "1") {
      store.set("wb-pen-told", "1");
      excal.setToast({ message: tr("펜을 찾았어요 — '펜만 그리기' 를 켰어요. 손가락은 화면 이동이에요(펜 설정에서 끌 수 있어요)", "Pen detected — 'Pen-only drawing' is on. A finger pans the board (turn it off in Pen settings)"), duration: 6000 });
    }
  }, [excal, pen.detected]);
  const setPenOnly = (v) => {
    if (!excal) return;
    store.set("wb-penmode", v ? "on" : "off");
    excal.updateScene({ appState: { penMode: v, penDetected: true }, captureUpdate: CaptureUpdateAction.NEVER });
  };

  const setHlColor = (c) => { if (cfg.current.mode === "hl") setInkColor(c, false); };
  /** When the pen tool button is pressed again: switch highlighter back to pen; for pen, toggle the settings popover */
  const again = () => { if (cfg.current.mode === "hl") setMode("pen"); else setOpen((v) => !v); };

  return { smooth, setSmooth, straight, setStraight, mode, setMode, setHlColor, again, open, setOpen, pen, setPenOnly, badge };
}

/** Placeholder for the text sheet: handwriting input hints available on this device (iPad Scribble, Android handwriting keyboard) */
export function handwritingHint() {
  const ua = navigator.userAgent || "";
  const ipad = /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  if (ipad) return tr("여기에 글을 쓰세요 · Apple Pencil 로 써도 글자가 돼요", "Type here · Apple Pencil handwriting works too");
  if (/Android/.test(ua)) return tr("여기에 글을 쓰세요 · 자판의 손글씨 입력도 돼요", "Type here · keyboard handwriting input works too");
  return tr("여기에 글을 쓰세요", "Type here");
}
