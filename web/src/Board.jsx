import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  Excalidraw, MainMenu, CaptureUpdateAction, reconcileElements, restoreElements,
  convertToExcalidrawElements, viewportCoordsToSceneCoords, FONT_FAMILY,
} from "@excalidraw/excalidraw";
import { selectedTexts, selectedStyle, restyleText, selectedLayout } from "./textstyle.js";
import { FONTS, preloadFont, loadTextFonts } from "./features/fonts/registry.js";   // font table and slot extension (loading adds to FONT_FAMILY)
import FontPicker from "./features/fonts/FontPicker.jsx";
import TextLayout from "./features/fonts/TextLayout.jsx";
import ExportSheet from "./features/export/ExportSheetLoader.jsx";   // export dialog (also for view-only users); the dialog is fetched only when opened
import { api } from "./api.js";
import { connect } from "./transport.js";
import MathDialog, { loadMath, mathModule, preloadMath } from "./features/math/MathLoader.jsx";
import { selectedMath } from "./features/math/select.js";   // math dialog and symbol palette: lazy chunk
import { createSwapHold } from "./features/math/hold.js";   // someone else edited the formula: keep the old image until the new one arrives
import { copyInvite, left } from "./invite.js";
import ShapeTools, { chooseShape } from "./features/flow/ShapeTools.jsx";   // flowcharts: shape expansion, '+' to continue building, mermaid
import QuickConnect from "./features/flow/QuickConnect.jsx";
import { SHAPE_TYPES, shapeForKey } from "./features/flow/connect.js";
import { openMermaid } from "./features/flow/mermaid.js";
import { hitNode, labelOf, setLabel, dblclickNode } from "./features/flow/label.js";   // phone: text inside a box also opens in the text sheet
import useInkCorrection, { handwritingHint } from "./features/ink/useInkCorrection.js";   // pen and stroke correction: snap to a straight line / smooth when you pause, highlighter, pen-only drawing
import InkOptions from "./features/ink/InkOptions.jsx";
import usePages from "./features/pages/usePages.js";   // drawing pages: next, new page, PageUp/PageDown; saving is via 'This page' and 'All pages PDF' in the export dialog
import PageBar from "./features/pages/PageBar.jsx";
import useFollow from "./features/follow/useFollow.js";   // presenter follow and 'everyone to me': people chips, people dialog, banner
import FollowLayer, { FollowChips } from "./features/follow/FollowUI.jsx";
import ImportSheet from "./features/import/ImportLoader.jsx";   // import: drop interception and 'Import file...' open a merge / new board sheet (lazy chunk)
import useImportDrop from "./features/import/useImportDrop.js";
import { ACCEPT as IMPORT_ACCEPT, takePendingToast } from "./features/import/importFile.js";
import { tr, lang, setLang, excalidrawLang } from "./i18n.js";   // screen language (Korean / English)
import useAgentJobs from "./features/jobs/useAgentJobs.js";   // tab render queue: agents borrow this tab for render / MathJax / mermaid (runners are lazy chunks)
import useRequests from "./features/ask/useRequests.js";   // 'Ask the AI': selection -> request (server meta), chips over the board for everyone
import AskButton from "./features/ask/AskButton.jsx";
import AskSheet from "./features/ask/AskSheet.jsx";
import RequestLayer from "./features/ask/RequestLayer.jsx";


const PUSH_MS = 80;        // interval for batching and sending changed elements
const PUSH_DRAW_MS = 160;  // slightly longer while drawing with the pen (avoids sending lines with many points too often)
const POINTER_MS = 70;     // interval for sending the cursor position

// Tools: only these are used (fill and layers were removed; shapes and arrows are grouped under the single 'Shapes' button in features/flow)
// Pen and text colors: anyone can pick any color (a person's color is used only for cursor and name tag). Yellow was removed because it is invisible on a white background.
// Black and gray are stored as the same value regardless of theme: dark mode renders the whole canvas inverted (Excalidraw's invert filter), so black looks light in dark mode.
const PALETTE = [
  ["#1e1e1e", tr("검정", "Black")], ["#868e96", tr("회색", "Gray")], ["#E31A1C", tr("빨강", "Red")], ["#D55E00", tr("주황", "Orange")],
  ["#009E73", tr("초록", "Green")], ["#0072B2", tr("파랑", "Blue")], ["#7B61FF", tr("보라", "Purple")], ["#CC79A7", tr("분홍", "Pink")],
];
// 'More' colors: two shades each (only ones visible in both light and dark mode). Anything else goes through 'Pick your own'.
const MORE_COLORS = [
  "#7f1d1d", "#b91c1c", "#f87171", "#9a3412", "#ea580c", "#fb923c",
  "#854d0e", "#ca8a04", "#eab308", "#14532d", "#16a34a", "#4ade80",
  "#134e4a", "#0d9488", "#2dd4bf", "#1e3a8a", "#2563eb", "#60a5fa",
  "#312e81", "#4f46e5", "#a78bfa", "#831843", "#db2777", "#f472b6",
  "#78350f", "#a16207", "#57534e", "#374151", "#6b7280", "#9ca3af",
];
const DARK_FILTER = "invert(93%) hue-rotate(180deg)";   // the filter Excalidraw applies to the canvas in dark mode, so color buttons show the color actually drawn
const TOOLS = [
  { id: "selection", label: tr("선택·옮기기", "Select"), key: "V", icon: "M5 3l6 16 2.5-6.5L20 10z" },
  { id: "freedraw", label: tr("펜", "Pen"), key: "P", icon: "M4 20l4-1 11-11-3-3L5 16zM14 6l3 3" },
  { id: "eraser", label: tr("지우개", "Eraser"), key: "E", icon: "M7 20h11M4 15l8-8 6 6-5 5H8z" },
  { id: "text", label: tr("글", "Text"), key: "T", icon: "M5 5h14M12 5v14M9 19h6" },
  { id: "math", label: tr("수식", "Math"), key: "M", icon: "M6 5h12l-7 7 7 7H6" },
  { id: "laser", label: tr("레이저", "Laser"), key: "K", icon: "M12 12m-2 0a2 2 0 104 0 2 2 0 10-4 0M4 20l6-6M14 4l2 3M20 10l-3 1M17 4l-2 3" },
  { id: "hand", label: tr("화면 이동", "Pan"), key: "H", icon: "M8 12V6a1.5 1.5 0 013 0v5V4.5a1.5 1.5 0 013 0V11V6a1.5 1.5 0 013 0v7a6 6 0 01-6 6h-1a6 6 0 01-5-3l-2-4a1.5 1.5 0 012.5-1.5z" },
];
const SIZES = [12, 16, 20, 24, 28, 36, 48, 64, 96];
const stepSize = (s, d) => {
  const i = SIZES.findIndex((x) => x >= (s || 24));
  const at = i < 0 ? SIZES.length - 1 : i;
  return SIZES[Math.max(0, Math.min(SIZES.length - 1, SIZES[at] === s || d < 0 ? at + d : at + d - 1))];
};
const bump = (e) => ({ ...e, version: e.version + 1, versionNonce: Math.floor(Math.random() * 2 ** 31) });
// Shortcuts that may be pressed: other single-letter shortcuts are blocked. The shape keys r, d, o, a, l are enabled directly by onKey below via e.code (also while a Korean IME is active)
const ALLOWED_KEYS = new Set(["v", "p", "e", "t", "k", "h", "m", "r", "d", "o", "a", "l", "1", "2", "3", "4", "5", "6", "7", "8", "0", "escape", "delete", "backspace", "enter", " ",
  "arrowup", "arrowdown", "arrowleft", "arrowright", "shift", "control", "meta", "alt", "tab"]);

const norm = (c) => String(c || "").toLowerCase();

// colors are 'the same' if they have the same hex, or if both are saturated and their hues are within 18 degrees (same as the server's similar())
function hsl(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16), r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
  if (d === 0) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  const h = mx === r ? 60 * (((g - b) / d) % 6) : mx === g ? 60 * ((b - r) / d + 2) : 60 * ((r - g) / d + 4);
  return { h: (h + 360) % 360, s, l };
}
export function sameColor(a, b) {
  if (norm(a) === norm(b)) return true;
  const x = hsl(a), y = hsl(b);
  if (!x || !y || x.s < 0.35 || y.s < 0.35 || x.l < 0.18 || y.l < 0.18 || x.l > 0.85 || y.l > 0.85) return false;
  const dh = Math.abs(x.h - y.h);
  return Math.min(dh, 360 - dh) <= 18;
}

// Excalidraw derives cursor and laser colors from a hash of the person's id (hsl(hash % 37 x 10deg, 100%, 83%)); the color we pass in is ignored.
// So we pass an id whose hashed hue is closest to the hue of that person's fixed color (the same person always gets the same id).
function excalHue(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h << 5) - h + id.charCodeAt(i);
  return (Math.abs(h) % 37) * 10;
}
const hueIdCache = new Map();
function idForColor(user, hex) {
  const key = user + hex;
  if (hueIdCache.has(key)) return hueIdCache.get(key);
  const target = (hsl(hex) || { h: 0 }).h;
  let best = user, bestD = 999;
  for (let n = 0; n < 400 && bestD > 0; n++) {
    const id = `${user}~${n}`, d = Math.abs(excalHue(id) - target), dd = Math.min(d, 360 - d);
    if (dd < bestD) { best = id; bestD = dd; }
  }
  hueIdCache.set(key, best);
  return best;
}

// same merge rule as the server (higher version wins; on a tie, lower versionNonce wins): returns true if mine wins
export function localWins(mine, server) {
  if (!server) return true;
  if (mine.version !== server.version) return mine.version > server.version;
  return mine.versionNonce < server.versionNonce;
}

const store = {
  get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* storage blocked */ } },
};
const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

function Icon({ d }) {
  return <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
}

// ---------------------------------------------------------------- board
export default function Board({ id, me }) {
  const client = useMemo(() => Math.random().toString(36).slice(2, 10), []);
  const [excal, setExcalRaw] = useState(null);
  const setExcal = useCallback((a) => { setExcalRaw(a); window.__wbapi = a; }, []);
  const [theme, setTheme] = useState(() => store.get("wb-theme", "light"));
  const [tool, setTool] = useState("freedraw");
  const [width, setWidth] = useState(() => { const w = +store.get("wb-width2", 1.5); return w >= 0.5 && w <= 8 ? w : 1.5; });
  const [font, setFont] = useState(() => { const f = store.get("wb-font", "meet"); return FONTS.some((x) => x.id === f && x.family) ? f : "meet"; });
  const [fontSize, setFontSize] = useState(() => +store.get("wb-fontsize", 24));
  const [ink, setInk] = useState(() => { const c = store.get("wb-ink", ""); return /^#[0-9a-f]{6}$/i.test(c) ? c : "#1e1e1e"; });   // default is black (a person's color may be yellow, which is invisible on a white background)
  const [math, setMath] = useState(null);        // math dialog { initial }
  const [textSel, setTextSel] = useState(false);   // whether any text is selected: show the font and size controls and change it right away
  const [moreInk, setMoreInk] = useState(false);   // 'More colors' dialog
  const [mathSel, setMathSel] = useState(false); // whether a single formula is selected: the button becomes 'Edit formula'
  const [editing, setEditing] = useState(false);   // whether a text box is being edited (leave the font and size controls as they are)
  const [fontPop, setFontPop] = useState(false);   // font picker dialog
  const [layout, setLayout] = useState({ lineHeight: null, textAlign: null });   // line spacing and alignment of the selected text
  const [menu, setMenu] = useState(false);         // phone menu
  const [saving, setSaving] = useState(false);     // export dialog
  const [importQ, setImportQ] = useState([]);     // import queue: [{key, file}], the first one is the sheet shown (multiple files are asked one at a time)
  const importing = importQ[0] || null;
  const importKey = useRef(0);
  const importInput = useRef(null);
  const [grid, setGrid] = useState(() => store.get("wb-grid", "0") === "1");   // grid: a per-user view setting (not board state)
  const [deskOpen, setDeskOpen] = useState(false); // narrow desktop: collapsed panel with undo, redo, delete, theme, invite
  const [sheet, setSheet] = useState(null);        // phone text-entry sheet { x, y, editId }
  const [sheetText, setSheetText] = useState("");
  const sheetRef = useRef(null);
  const tap = useRef(null);                        // phone: where the text tool was tapped, and the text that was double-tapped
  const lastTap = useRef({ t: 0, x: 0, y: 0 });
  const dockRef = useRef(null);
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 760px)").matches);
  const coarse = useMemo(() => window.matchMedia("(pointer: coarse)").matches || navigator.maxTouchPoints > 0 && !window.matchMedia("(pointer: fine)").matches, []);
  const [meta, setMeta] = useState(null);
  const [initial, setInitial] = useState(null);
  const [users, setUsers] = useState([]);
  const [status, setStatus] = useState(tr("연결 중…", "Connecting…"));
  const sent = useRef(new Map());        // element id -> version in sync with the server
  const pending = useRef(new Map());     // elements to send
  const uploaded = useRef(new Set());    // file ids already on the server
  const fetching = useRef(new Set());
  const timer = useRef(null);
  const lastPtr = useRef(0);
  const sync = useRef({ seq: 0, pver: -1, epoch: "" });
  const drawing = useRef(false);
  const holdRef = useRef(null);          // holds a formula that someone else is editing (features/math/hold.js)
  const agentJobs = useAgentJobs({ excal, id, client, viewOnly: me.role === "view", coarse });
  const ask = useRequests({ id, client, viewOnly: me.role === "view", toast: (message) => excal && excal.setToast({ message, duration: 3000 }) });
  const [asking, setAsking] = useState(null);   // ask window {ids, over, at}


  // Initial load: if it fails, retry after a moment (never leave the screen blank)
  useEffect(() => {
    let alive = true, tries = 0;
    const load = () => api.board(id).then(async (d) => {
      if (!alive) return;
      setMeta(d.meta);
      sync.current = { seq: d.seq || 0, pver: -1, epoch: d.epoch || "" };
      d.files.forEach((f) => uploaded.current.add(f));
      const els = restoreElements(d.elements, null);
      els.forEach((e) => sent.current.set(e.id, e.version));
      const need = [...new Set(els.filter((e) => e.type === "image" && e.fileId && !e.isDeleted).map((e) => e.fileId))];
      const files = (await Promise.all(need.map((fid) => api.getFile(id, fid).catch(() => null)))).filter(Boolean);
      setStatus("");
      setInitial({
        elements: els, files, scrollToContent: true,
        appState: { currentItemStrokeColor: ink, currentItemStrokeWidth: width, currentItemFontFamily: FONTS.find((f) => f.id === font)?.family || FONT_FAMILY.Nunito,
          currentItemFontSize: fontSize, currentItemRoughness: 0, currentItemBackgroundColor: "transparent", currentItemArrowType: "elbow", objectsSnapModeEnabled: true, activeTool: { type: "freedraw", customType: null, locked: true, lastActiveTool: null },
          frameRendering: { enabled: true, name: false, outline: true, clip: false } },   // so overflowing strokes are not clipped; page numbers come from the single page bar (frame name display is off)
      });
    }, (e) => {
      if (!alive) return;
      tries += 1;
      setStatus(tr(`불러오는 중… (${e.message}, 다시 시도 ${tries})`, `Loading… (${e.message}, retry ${tries})`));
      setTimeout(load, Math.min(8000, 1000 * tries));
    });
    load();
    return () => { alive = false; };
  }, [id]);   // eslint-disable-line react-hooks/exhaustive-deps

  const fetchMissingFiles = useCallback(async (api_, elements) => {
    const have = api_.getFiles();
    const need = [...new Set(elements.filter((e) => e.type === "image" && e.fileId && !have[e.fileId] && !fetching.current.has(e.fileId)).map((e) => e.fileId))];
    if (!need.length) return;
    need.forEach((f) => fetching.current.add(f));
    const files = (await Promise.all(need.map((fid) => api.getFile(id, fid).catch(() => null)))).filter(Boolean);
    need.forEach((f) => fetching.current.delete(f));
    if (files.length) { files.forEach((f) => uploaded.current.add(f.id)); api_.addFiles(files); }
  }, [id]);

  // Realtime: WebSocket (quietly falls back to HTTP if that fails). Changes from others stay outside my undo history; when the server restarts they are merged in rather than wiping local state.
  const link = useRef(null);
  useEffect(() => {
    if (!excal) return;
    loadTextFonts(excal, initial ? initial.elements : excal.getSceneElements());   // also load fonts for the initially received scene (at this moment the scene may still be empty, so use the received data)
    const hold = createSwapHold({ excal, getFile: (fid) => api.getFile(id, fid), release: (els) => onElements(els, true), rebased: (els) => fetchMissingFiles(excal, els) });
    holdRef.current = hold;
    const onElements = (incoming, released) => {
      const elements = incoming && !released ? hold.take(incoming) : incoming;
      if (!elements || !elements.length) return;
      const merged = reconcileElements(excal.getSceneElementsIncludingDeleted(), elements, excal.getAppState());
      elements.forEach((e) => { if ((sent.current.get(e.id) ?? -1) <= e.version) sent.current.set(e.id, e.version); });
      excal.updateScene({ elements: merged, captureUpdate: CaptureUpdateAction.NEVER });
      fetchMissingFiles(excal, elements);
      loadTextFonts(excal, elements);   // font subsets for text written by others (WebKit does not fetch them on its own)
    };
    const onPresence = (list) => {
      setUsers(list);
      const collaborators = new Map();
      list.filter((p) => p.client !== client).forEach((p) => collaborators.set(p.client, {
        username: p.short || p.user, pointer: p.pointer || undefined, button: p.button, selectedElementIds: p.selected || {},
        color: { background: p.color + "33", stroke: p.color }, id: idForColor(p.user, p.color), socketId: p.client,
      }));
      excal.updateScene({ collaborators, captureUpdate: CaptureUpdateAction.NEVER });
    };
    const onEvent = (event, data) => {
      if (event === "elements") onElements(data.elements);
      else if (event === "file") { uploaded.current.add(data.id); hold.onFile(data.id); fetchMissingFiles(excal, excal.getSceneElements()); }
      else if (event === "meta") setMeta(data);
      else if (event === "job") agentJobs.notify(data);
      else if (event === "request") ask.notify(data);
    };
    const onReset = async (given) => {   // the server restarted or its log was trimmed: fetch the full state and merge, and resend my elements that the server lacks
      const d = given ? { elements: given } : await api.board(id);
      ask.refresh();   // requests are not in the event replay after a reset
      onElements(d.elements);
      // Resend elements that the server lacks, or where mine wins (is newer) than the server's, both edits and deletions (the case where the server died before saving)
      const server = new Map(d.elements.map((e) => [e.id, e]));
      const mine = excal.getSceneElementsIncludingDeleted().filter((e) => localWins(e, server.get(e.id)));
      if (mine.length && link.current) link.current.sendElements(mine);
      if (!given) { sync.current.seq = d.seq || 0; sync.current.epoch = d.epoch || sync.current.epoch; }
    };
    link.current = connect({ id, client, sync: sync.current, onEvent, onPresence, onReset, onStatus: setStatus });
    window.__wblink = link.current;
    return () => { hold.close(); holdRef.current = null; link.current && link.current.close(); link.current = null; };
  }, [excal, id, client, fetchMissingFiles]);

  // Sending: batch through transport (hold until ack, and resend on reconnect)
  const flush = useCallback(() => {
    timer.current = null;
    const batch = [...pending.current.values()];
    pending.current.clear();
    if (batch.length && link.current) link.current.sendElements(batch);
  }, []);

  const onChange = useCallback((elements, appState, files) => {
    if (!excal) return;
    // keep the tool in sync with the toolbar (when changed via a shortcut)
    const t = appState.activeTool?.type;
    if (t && t !== tool && (TOOLS.some((x) => x.id === t) || SHAPE_TYPES.has(t))) setTool(t);
    drawing.current = !!appState.newElement && appState.newElement.type === "freedraw";
    const selIds = Object.keys(appState.selectedElementIds || {}).filter((k) => appState.selectedElementIds[k]);
    const ms = !!selectedMath(elements, appState.selectedElementIds);
    if (ms !== mathSel) setMathSel(ms);
    const ed = !!appState.editingTextElement;
    if (ed !== editing) setEditing(ed);
    const texts = selectedTexts(elements, appState.selectedElementIds || {});
    const ts = texts.length > 0;
    if (ts) {
      const current = selectedStyle(texts, FONTS);
      setFont(current.font); setFontSize(current.fontSize);
    }
    if (ts !== textSel) setTextSel(ts);
    const none = !ts && !appState.editingTextElement;   // no text selected: the alignment shows the value for the next new text
    const lay = none ? { lineHeight: null, textAlign: appState.currentItemTextAlign, none } : selectedLayout(ts ? texts : [appState.editingTextElement]);
    setLayout((p) => (p.lineHeight === lay.lineHeight && p.textAlign === lay.textAlign && p.none === lay.none ? p : lay));
    for (const e of elements) {
      if ((sent.current.get(e.id) ?? -1) !== e.version) {
        if (holdRef.current && holdRef.current.rebase(e)) continue;   // moves a formula someone else is editing: stack it on top of their edit and send (hold.js)
        pending.current.set(e.id, e);
        sent.current.set(e.id, e.version);
      }
    }
    for (const f of Object.values(files || {})) {
      if (!uploaded.current.has(f.id) && f.dataURL) {
        uploaded.current.add(f.id);
        api.putFile(id, client, f).catch((e) => { uploaded.current.delete(f.id); setStatus(tr(`이미지 올리기 실패: ${e.message}`, `Image upload failed: ${e.message}`)); });
      }
    }
    if (pending.current.size && !timer.current) timer.current = setTimeout(flush, drawing.current ? PUSH_DRAW_MS : PUSH_MS);
  }, [excal, tool, me, id, client, flush, mathSel, textSel, editing]);

  const onPointerUpdate = useCallback(({ pointer, button }) => {
    const now = Date.now();
    if (now - lastPtr.current < POINTER_MS) return;
    lastPtr.current = now;
    const sel = excal ? excal.getAppState().selectedElementIds : {};
    link.current && link.current.sendPresence({ pointer, button, selected: sel, ...follow.take() });   // include the changed viewport range if there is one
  }, [excal]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------- toolbar actions
  const pick = useCallback((t) => {
    if (!excal) return;
    if (t === "math") { openMath(); return; }   // eslint-disable-line no-use-before-define
    excal.setActiveTool({ type: t, locked: t === "freedraw" || t === "eraser" || t === "laser" });
    setTool(t);
  }, [excal]);   // eslint-disable-line react-hooks/exhaustive-deps
  const pickRef = useRef(pick); pickRef.current = pick;

  const selectedIds = () => Object.keys(excal.getAppState().selectedElementIds || {}).filter((k) => excal.getAppState().selectedElementIds[k]);

  const applyToSelection = useCallback((patch, pred) => {
    if (!excal) return;
    const ids = new Set(selectedIds());
    if (!ids.size) return;
    const els = excal.getSceneElementsIncludingDeleted().map((e) => (ids.has(e.id) && (!pred || pred(e)) ? { ...e, ...patch, version: e.version + 1, versionNonce: Math.floor(Math.random() * 2 ** 31) } : e));
    excal.updateScene({ elements: els, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  }, [excal]);   // eslint-disable-line react-hooks/exhaustive-deps

  const setStroke = (w, apply = true) => {   // apply=false: only the next pen to draw (highlighter <-> pen switch; selected elements are left alone)
    setWidth(w); store.set("wb-width2", w);
    excal?.updateScene({ appState: { currentItemStrokeWidth: w }, captureUpdate: CaptureUpdateAction.NEVER });
    if (apply) applyToSelection({ strokeWidth: w }, (e) => e.type === "freedraw");
  };
  const setInkColor = (c, apply = true) => {
    setInk(c); store.set("wb-ink", c);
    excal?.updateScene({ appState: { currentItemStrokeColor: c }, captureUpdate: CaptureUpdateAction.NEVER });
    if (sheet || !apply) return;   // color of the text-entry sheet: used when the text is inserted
    applyToSelection({ strokeColor: c }, (e) => e.type !== "image");
  };
  // Re-measure the selected text with the new font and size (changing only the font leaves the box sized for the old font)
  const restyleSelectedText = (patch) => {
    if (!excal || sheet) return false;
    const st = excal.getAppState();
    const sel = { ...(st.selectedElementIds || {}) };
    if (st.editingTextElement) sel[st.editingTextElement.id] = true;   // text being edited is changed right away too
    const els = restyleText(excal.getSceneElementsIncludingDeleted(), sel, patch, restoreElements);
    if (!els) return false;
    excal.updateScene({ elements: els, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    return true;
  };
  const setFontChoice = (f) => {
    setFontPop(false);
    if (f === "math") { openMath(); return; }   // eslint-disable-line no-use-before-define
    setFont(f); store.set("wb-font", f);
    const fam = FONTS.find((x) => x.id === f).family;
    preloadFont(FONTS.find((x) => x.id === f));   // start downloading the moment it is chosen, so measurement from the first character uses that font's widths
    excal?.updateScene({ appState: { currentItemFontFamily: fam }, captureUpdate: CaptureUpdateAction.NEVER });
    if (restyleSelectedText({ fontFamily: fam })) return;   // if text is selected, change that text and leave the tool as is
    if (tool !== "text" && !sheet) pick("text");
  };
  const setTextLayout = (patch) => {
    if (patch.textAlign) excal?.updateScene({ appState: { currentItemTextAlign: patch.textAlign }, captureUpdate: CaptureUpdateAction.NEVER });
    restyleSelectedText(patch);
  };
  const setTextSize = (s) => {
    if (!s) return;
    setFontSize(s); store.set("wb-fontsize", s);
    excal?.updateScene({ appState: { currentItemFontSize: s }, captureUpdate: CaptureUpdateAction.NEVER });
    restyleSelectedText({ fontSize: s });
  };
  // Keep focus on the canvas when the toolbar is pressed, so paste (Cmd+V) and shortcuts keep working
  const refocus = () => {
    const a = document.activeElement;
    if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA") && !a.closest(".wb-dock")) return;
    const c = document.querySelector(".excalidraw");
    if (c && !c.contains(document.activeElement)) { c.setAttribute("tabindex", "-1"); c.focus({ preventScroll: true }); }
  };
  const toggleTheme = () => { const t = theme === "dark" ? "light" : "dark"; setTheme(t); store.set("wb-theme", t); };
  const key = (k, shift = false) => {   // Excalidraw's undo and redo are open only via the keyboard
    const ev = new KeyboardEvent("keydown", { key: shift ? k.toUpperCase() : k, code: "Key" + k.toUpperCase(), bubbles: true, cancelable: true, shiftKey: shift, ctrlKey: !isMac, metaKey: isMac });
    document.dispatchEvent(ev);
  };
  const deleteSelected = () => {
    if (!excal) return;
    const ids = new Set(selectedIds());
    if (!ids.size) { excal.setToast({ message: tr("지울 것을 먼저 선택하세요(선택 도구로 끌어서 영역 선택)", "Select something to delete first (drag with the Select tool)"), duration: 2500 }); return; }
    const els = excal.getSceneElementsIncludingDeleted().map((e) => (ids.has(e.id) || (e.containerId && ids.has(e.containerId)) ? { ...e, isDeleted: true, version: e.version + 1, versionNonce: Math.floor(Math.random() * 2 ** 31) } : e));
    excal.updateScene({ elements: els, appState: { selectedElementIds: {} }, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  };

  const savePng = () => { if (excal) setSaving(true); };
  const inkx = useInkCorrection({ excal, tool, viewOnly: me.role === "view", ink, width, setInkColor, setStroke });
  const pages = usePages({ excal, viewOnly: me.role === "view" });
  const follow = useFollow({ excal, link, client, me, meta, users });
  const toggleGrid = () => setGrid((g) => { store.set("wb-grid", g ? "0" : "1"); return !g; });
  // Import: stop a dropped .excalidraw from silently replacing only my scene; show a sheet instead. Image drops are still handled by Excalidraw.
  const queueImport = (files) => {
    const add = files.map((file) => ({ key: ++importKey.current, file }));
    const had = importQ.length, n = had + add.length;   // if more files are dropped while the sheet is open, they queue behind the current one instead of replacing it
    if (excal && n > 1) excal.setToast({ message: had ? tr(`가져올 파일을 차례에 넣었어요 — 지금 것 다음에 ${n - 1}개를 물어요`, `Queued — after this one, ${n - 1} more file(s) to ask about`) : tr(`파일 ${n}개 — 하나씩 물어볼게요`, `${n} files — I'll ask about each one`), duration: 3000 });
    setImportQ((q) => [...q, ...add]);
  };
  useImportDrop({ enabled: !!excal, viewOnly: me.role === "view", onFiles: queueImport,
    onDenied: () => excal && excal.setToast({ message: tr("보기 전용이라 파일을 넣을 수 없어요", "View only — you can't add files"), duration: 3000 }) });
  useEffect(() => {   // after 'Open as new board': when the new board screen appears, show the toast left by the previous screen
    if (!excal) return undefined;
    const t = takePendingToast();
    if (!t) return undefined;
    const h = setTimeout(() => excal.setToast(t), 400);
    return () => clearTimeout(h);
  }, [excal]);
  const pickImport = () => { const i = importInput.current; if (i) { i.value = ""; i.click(); } };

  // ---------------- phone: text is entered in the sheet below
  // Excalidraw's text box gets focus from a timer after the tap, so on phones (especially iOS) the keyboard does not appear and an empty box is left behind.
  // On phones, give focus at the moment of the tap to an always-attached input so the keyboard shows right away.
  const hitText = (sx, sy) => excal.getSceneElements().filter((e) => e.type === "text" && !e.containerId)
    .reverse().find((e) => sx >= e.x - 10 && sx <= e.x + e.width + 10 && sy >= e.y - 10 && sy <= e.y + e.height + 10);
  const openSheet = (cx, cy, hit) => {
    const st = excal.getAppState();
    const sc = viewportCoordsToSceneCoords({ clientX: cx, clientY: cy }, st);
    const hitEl = hit === undefined ? (hitText(sc.x, sc.y) || hitNode(excal.getSceneElements(), sc.x, sc.y)) : hit;
    const node = hitEl && hitEl.type !== "text" ? hitEl : null;   // a flowchart cell: the text is bound into that box
    const el = node ? labelOf(excal.getSceneElements(), node) : hitEl;
    if (sheetRef.current) sheetRef.current.focus({ preventScroll: true });   // at the moment of the tap: the keyboard appears
    setSheetText(el ? (el.originalText ?? el.text) : "");
    if (el) { const f = FONTS.find((x) => x.family === el.fontFamily); if (f) setFont(f.id); setFontSize(el.fontSize); if (el.strokeColor) setInk(el.strokeColor); }
    setSheet({ x: sc.x, y: sc.y, editId: node ? null : el ? el.id : null, nodeId: node ? node.id : null, had: !!el });
  };
  const closeSheet = () => { setSheet(null); setSheetText(""); if (sheetRef.current) sheetRef.current.blur(); };
  const commitSheet = () => {
    if (!excal || !sheet) return;
    const text = sheetText.replace(/\s+$/, "");
    const fam = (FONTS.find((x) => x.id === font) || FONTS[0]).family || FONT_FAMILY.Nunito;
    const size = fontSize || 24;
    let els = excal.getSceneElementsIncludingDeleted();
    let select = {};
    if (sheet.nodeId) {
      els = setLabel(els, sheet.nodeId, text, { fontSize: size, fontFamily: fam, strokeColor: ink }, convertToExcalidrawElements) || els;
      select = { [sheet.nodeId]: true };
    } else if (sheet.editId) {
      if (!text) els = els.map((e) => (e.id === sheet.editId ? bump({ ...e, isDeleted: true }) : e));
      else {
        const input = els.map((e) => (e.id === sheet.editId ? { ...e, text, originalText: text, fontFamily: fam, fontSize: size, strokeColor: ink } : e));
        const m = new Map(restoreElements(input, null, { repairBindings: true, refreshDimensions: true }).map((e) => [e.id, e]));
        els = els.map((e) => (e.id === sheet.editId ? bump({ ...e, ...m.get(e.id) }) : e));
        select = { [sheet.editId]: true };
      }
    } else if (text) {
      const [el] = convertToExcalidrawElements([{ type: "text", x: sheet.x, y: sheet.y - size * 0.6, text, fontSize: size, fontFamily: fam, strokeColor: ink, roughness: 0 }]);
      els = [...els, el];
      select = { [el.id]: true };
    }
    excal.updateScene({ elements: els, appState: { selectedElementIds: select }, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    excal.setActiveTool({ type: "selection" }); setTool("selection");
    closeSheet();
  };
  useEffect(() => {
    if (!excal || !coarse || me.role === "view") return;
    const onCanvas = (e) => e.target instanceof HTMLCanvasElement && e.target.closest(".excalidraw");
    const down = (e) => {
      if (!onCanvas(e) || !e.isPrimary) return;
      const t = excal.getAppState().activeTool.type;
      if (t === "text") {   // text tool: stop Excalidraw from creating a text box, and open the sheet when the finger lifts
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        tap.current = { x: e.clientX, y: e.clientY, touch: e.pointerType === "touch", hit: undefined };
        return;
      }
      if (t === "selection") {   // tapping the same text twice quickly edits it
        const now = Date.now(), L = lastTap.current;
        lastTap.current = { t: now, x: e.clientX, y: e.clientY };
        if (now - L.t < 400 && Math.hypot(e.clientX - L.x, e.clientY - L.y) < 24) {
          const sc = viewportCoordsToSceneCoords({ clientX: e.clientX, clientY: e.clientY }, excal.getAppState());
          const hit = hitText(sc.x, sc.y) || hitNode(excal.getSceneElements(), sc.x, sc.y);   // double-tapping a box also opens the sheet (Excalidraw's text box does not bring up the keyboard on iOS)
          if (hit) { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); tap.current = { x: e.clientX, y: e.clientY, touch: e.pointerType === "touch", hit }; lastTap.current = { t: 0, x: 0, y: 0 }; }
        }
      }
    };
    const stopTouch = (e) => {   // also block Excalidraw's double-tap (opening a text box)
      if (tap.current && onCanvas(e)) { e.stopPropagation(); e.stopImmediatePropagation(); }
    };
    const finish = (e) => {
      const t = tap.current; if (!t) return;
      tap.current = null;
      e.stopPropagation();
      if (Math.hypot((e.clientX ?? e.changedTouches?.[0]?.clientX ?? t.x) - t.x, (e.clientY ?? e.changedTouches?.[0]?.clientY ?? t.y) - t.y) > 16) return;
      openSheet(t.x, t.y, t.hit);
    };
    const up = (e) => { if (tap.current && !tap.current.touch) finish(e); else if (tap.current) e.stopPropagation(); };
    const end = (e) => { if (tap.current && tap.current.touch) { if (e.cancelable) e.preventDefault(); finish(e); } };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("touchstart", stopTouch, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("touchend", end, { capture: true, passive: false });
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("touchstart", stopTouch, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("touchend", end, { capture: true, passive: false });
    };
  }, [excal, coarse, me.role]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const q = window.matchMedia("(max-width: 760px)");
    const f = () => setMobile(q.matches);
    q.addEventListener ? q.addEventListener("change", f) : q.addListener(f);
    return () => (q.removeEventListener ? q.removeEventListener("change", f) : q.removeListener(f));
  }, []);
  // Floating panels (color, font) sit right above the toolbar; on phones the toolbar has two rows, so measure its height
  useEffect(() => {
    const d = dockRef.current; if (!d || !("ResizeObserver" in window)) return;
    const ro = new ResizeObserver(() => d.parentElement && d.parentElement.style.setProperty("--dock-h", `${d.offsetHeight}px`));
    ro.observe(d);
    return () => ro.disconnect();
  });

  // ---------------- math (features/math): editing changes the same element (size, color, arrows, grouping are kept)
  const openMath = useCallback((target) => {
    if (!excal || me.role === "view") return;   // view-only: neither the M key nor double-tap opens it
    const sel = target && target.id ? target : selectedMath(excal.getSceneElements(), excal.getAppState().selectedElementIds);
    const open = (m) => {
      flushSync(() => setMath({ initial: m.mathInitial(excal, sel, ink), board: m.boardFormulas(excal.getSceneElements()) }));
      if (coarse) { const t = document.querySelector(".wb-math-input"); if (t) t.focus({ preventScroll: true }); }   // at the moment of the tap: the phone keyboard appears
    };
    if (mathModule()) open(mathModule());
    else loadMath().then(open, () => excal.setToast({ message: tr("수식 창을 못 불러왔어요 — 연결을 확인하고 다시 눌러 주세요", "Couldn't load the math editor — check your connection and try again"), duration: 4500 }));
  }, [excal, ink, coarse, me]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (excal && me.role !== "view") preloadMath(); }, [excal, me]);

  // The dialog stays open until applying finishes (on failure it stays open and reports the error). If the tool or selection changed while waiting, leave them alone.
  const insertMath = useCallback(async (tex, size, color, r) => {
    if (!excal) return false;
    const old = math?.initial;
    const ui = () => { const s = excal.getAppState(); return `${s.activeTool.type}|${Object.keys(s.selectedElementIds || {}).filter((k) => s.selectedElementIds[k]).sort().join(",")}`; };
    const before = ui();
    const upload = (f) => { uploaded.current.add(f.id); return api.putFile(id, client, f).catch((e) => { uploaded.current.delete(f.id); throw e; }); };
    let res;
    try { res = await mathModule().commitMath(excal, { old, tex, size, color, r, upload, select: () => ui() === before }); }
    catch (e) { excal.setToast({ message: tr(`수식을 넣지 못했어요 — ${e.message || e}`, `Couldn't insert the formula — ${e.message || e}`), duration: 5000 }); return false; }
    setMath(null);
    setTimeout(refocus, 0);
    if (res.picked) { excal.setActiveTool({ type: "selection" }); setTool("selection"); }
    return true;
  }, [excal, math, id, client]);

  // Double-tapping a formula opens the edit dialog (instead of cropping the image).
  // Desktop: double-tapping a box edits the text inside it. Inside a frame, Excalidraw grabbed the frame first, leaving text not bound to the box.
  // Pick that box and open it through Excalidraw's 'select -> Enter' edit path (on phones the pointerdown handler above routes to the text sheet, unchanged).
  useEffect(() => {
    const onDbl = (e) => {
      if (!excal || !e.target || e.target.tagName !== "CANVAS") return;
      const st = excal.getAppState();
      const sc = viewportCoordsToSceneCoords({ clientX: e.clientX, clientY: e.clientY }, st);
      const el = selectedMath(excal.getSceneElements(), st.selectedElementIds, sc);
      if (el) { e.preventDefault(); e.stopPropagation(); openMath(el); return; }
      if (coarse || me.role === "view" || e.ctrlKey || e.metaKey || e.altKey || st.activeTool.type !== "selection" || st.editingTextElement || st.multiElement) return;
      const node = dblclickNode(excal.getSceneElements(), st.selectedElementIds, sc.x, sc.y);
      if (!node) return;
      e.preventDefault(); e.stopPropagation();
      flushSync(() => excal.updateScene({ appState: { selectedElementIds: { [node.id]: true }, selectedGroupIds: {}, editingGroupId: null }, captureUpdate: CaptureUpdateAction.NEVER }));
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    };
    window.addEventListener("dblclick", onDbl, true);
    return () => window.removeEventListener("dblclick", onDbl, true);
  }, [excal, openMath, coarse, me]);

  // Block the single-letter shortcuts of blocked tools (while typing text, leave them as is). Shape keys are enabled directly here
  useEffect(() => {
    const onKey = (e) => {
      const tgt = e.target;
      if (document.querySelector(".wb-export-back, .wb-import-back, .wb-ask-back")) return;   // when the export, import or ask dialog is open, the dialog receives the keys
      if (tgt && (tgt.tagName === "TEXTAREA" || tgt.tagName === "INPUT" || tgt.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // with a Korean IME active, e.key comes as e.g. 'ㅅ', so use the physical key position (e.code)
      const k = /^Key[A-Z]$/.test(e.code || "") ? e.code.slice(3).toLowerCase() : String(e.key || "").toLowerCase();
      if (k === "m") { e.preventDefault(); e.stopPropagation(); openMath(); return; }
      const shape = !e.shiftKey && shapeForKey(k);   // shape key: Excalidraw looks at e.key, so it cannot recognize it while a Korean IME is active
      if (shape) {
        e.preventDefault(); e.stopPropagation();
        const st = excal && excal.getAppState();   // do not change tools while drawing or dragging (same condition as Excalidraw)
        if (me.role !== "view" && st && !st.newElement && !st.selectionElement && !st.selectedElementsAreBeingDragged && !st.multiElement) chooseShape(excal, shape, pickRef.current);
        return;
      }
      if (k.length === 1 && !ALLOWED_KEYS.has(k)) { e.preventDefault(); e.stopPropagation(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [openMath, me, excal]);

  const invite = useCallback(async (kind) => {
    if (!excal) return;
    if (!(me.invite && me.invite[kind])) { excal.setToast({ message: tr("이 보드는 초대 키 없이 열려 있어요(로컬 모드)", "This board is open without an invite key (local mode)"), duration: 3000 }); return; }
    await copyInvite(me, kind, id);
    excal.setToast({ message: kind === "edit" ? tr("초대 링크를 복사했어요 — 받은 사람은 같이 그릴 수 있어요", "Invite link copied — anyone with it can draw with you") : tr("보기 전용 링크를 복사했어요", "View-only link copied"), duration: 3000 });
  }, [excal, me, id]);

  if (!initial) return <div className="wb-page wb-muted">{status}</div>;
  const others = users.filter((u) => u.client !== client);
  const showInk = tool === "freedraw" || tool === "text" || tool === "selection" || SHAPE_TYPES.has(tool) || !!sheet;
  const viewOnly = me.role === "view";
  const otherLang = lang() === "ko" ? { code: "en", label: "English" } : { code: "ko", label: "한국어" };   // the switch is labelled in the language it switches to
  const switchLang = () => setLang(otherLang.code);
  const showText = tool === "text" || textSel || editing || !!sheet;
  const curFont = FONTS.find((f) => f.id === font);
  const keep = (e) => { if (!(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) e.preventDefault(); };   // pressing does not steal focus (the text being written)
  const fontButton = (
    <button type="button" className="wb-seg wb-fontbtn" title={tr("글꼴", "Font")} aria-haspopup="true" aria-expanded={fontPop} onClick={() => setFontPop((v) => !v)}>
      <span style={{ fontFamily: curFont ? `"${curFont.css}"` : undefined }}>{curFont ? curFont.label : tr("글꼴 섞임", "Mixed fonts")}</span> ▾
    </button>
  );
  const sizeControl = (
    <div className="wb-size" role="group" aria-label={tr("글자 크기", "Text size")}>
      <button type="button" className="wb-seg" aria-label={tr("글자 작게", "Smaller text")} onClick={() => setTextSize(stepSize(fontSize, -1))}>{tr("가−", "A−")}</button>
      <b title={tr("글자 크기", "Text size")}>{fontSize ?? tr("섞임", "mixed")}</b>
      <button type="button" className="wb-seg" aria-label={tr("글자 크게", "Larger text")} onClick={() => setTextSize(stepSize(fontSize, 1))}>{tr("가+", "A+")}</button>
    </div>
  );
  const inks = (
    <div className="wb-group wb-inks" aria-label={tr("색", "Color")}>
      {PALETTE.map(([c, label]) => (
        <button key={c} type="button" className={`wb-swatch ${norm(ink) === norm(c) ? "on" : ""}`} title={label} aria-label={tr(`색 ${label}`, `Color ${label}`)} onClick={() => setInkColor(c)}>
          <i style={{ background: c, filter: theme === "dark" ? DARK_FILTER : undefined }} />
        </button>
      ))}
      <button type="button" className={`wb-swatch wb-more ${PALETTE.some(([c]) => norm(c) === norm(ink)) ? "" : "on"}`} title={tr("색 더 보기", "More colors")} aria-label={tr("색 더 보기", "More colors")} aria-expanded={moreInk}
        onClick={() => setMoreInk((v) => !v)}>
        <i style={PALETTE.some(([c]) => norm(c) === norm(ink)) ? undefined : { background: ink, filter: theme === "dark" ? DARK_FILTER : undefined }}>{PALETTE.some(([c]) => norm(c) === norm(ink)) ? "+" : ""}</i>
      </button>
    </div>
  );
  return (
    <div className={`wb-board wb-theme-${theme}${mobile ? " wb-mobile" : ""}${sheet ? " wb-sheeting" : ""}`}>
      <Excalidraw
        excalidrawAPI={setExcal}
        initialData={initial}
        langCode={excalidrawLang()}
        theme={theme}
        handleKeyboardGlobally
        isCollaborating
        viewModeEnabled={viewOnly}
        gridModeEnabled={grid}
        onChange={onChange}
        onPointerUpdate={onPointerUpdate}
        name={meta ? meta.title : tr("보드", "Board")}
        UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, saveAsImage: false, export: { saveFileToDisk: true }, toggleTheme: false, changeViewBackgroundColor: false } }}
        renderTopRightUI={() => (
          <div className="wb-people" aria-label={tr("접속한 사람", "People here")}>
            {status && <span className="wb-status">{status}</span>}
            {me.expires_at && <span className="wb-status" title={new Date(me.expires_at * 1000).toLocaleString()}>{left(me.expires_at)}</span>}
            {viewOnly && <span className="wb-status">{tr("보기 전용", "View only")}</span>}
            <FollowChips f={follow} me={me} others={others} />
          </div>
        )}
      >
        <MainMenu>
          <MainMenu.Item onSelect={() => { location.hash = ""; }}>{tr("← 보드 목록", "← Boards")}</MainMenu.Item>
          {!viewOnly && <MainMenu.Item onSelect={() => invite("edit")}>{tr("초대 링크 복사(같이 그리기)", "Copy invite link (draw together)")}</MainMenu.Item>}
          {me.invite && me.invite.view && <MainMenu.Item onSelect={() => invite("view")}>{tr("보기 전용 링크 복사", "Copy view-only link")}</MainMenu.Item>}
          {!viewOnly && <MainMenu.Item onSelect={async () => {
            const t = window.prompt ? window.prompt(tr("보드 이름", "Board name"), meta ? meta.title : "") : null;
            if (t) setMeta(await api.rename(id, t));
          }}>{tr("보드 이름 바꾸기", "Rename board")}</MainMenu.Item>}
          <MainMenu.Separator />
          <MainMenu.Item onSelect={savePng}>{tr("그림 저장…", "Save image…")}</MainMenu.Item>
          {!viewOnly && <MainMenu.Item onSelect={pickImport}>{tr("파일 가져오기…", "Import file…")}</MainMenu.Item>}
          <MainMenu.Item onSelect={toggleGrid}>{grid ? tr("모눈 끄기", "Hide grid") : tr("모눈 보기", "Show grid")}</MainMenu.Item>
          <MainMenu.Item onSelect={toggleTheme}>{theme === "dark" ? tr("밝은 화면", "Light mode") : tr("어두운 화면", "Dark mode")}</MainMenu.Item>{/* a view-only desktop has no dock */}
          <MainMenu.Item onSelect={() => follow.setMenu(true)}>{tr("사람 · 따라가기…", "People · Follow…")}</MainMenu.Item>
          {!viewOnly && <MainMenu.Item onSelect={follow.summon}>{tr("모두 나에게(화면 모으기)", "Bring everyone to me")}</MainMenu.Item>}
          {!viewOnly && <MainMenu.Item onSelect={() => openMermaid(excal)}>{tr("글로 흐름도 만들기(mermaid)…", "Flowchart from text (mermaid)…")}</MainMenu.Item>}
          <MainMenu.DefaultItems.Export />
          {!viewOnly && <MainMenu.DefaultItems.ClearCanvas />}
          <MainMenu.DefaultItems.Help />
          <MainMenu.Separator />
          <MainMenu.Item onSelect={switchLang} lang={otherLang.code}>{otherLang.label}</MainMenu.Item>{/* screen language: saved per browser, reloads */}
        </MainMenu>
      </Excalidraw>

      {mobile && (
        <div className="wb-mtop" onMouseDown={keep}>
          <button type="button" className="wb-mbtn" aria-label={tr("메뉴", "Menu")} aria-expanded={menu} onClick={() => setMenu((v) => !v)}><Icon d="M4 7h16M4 12h16M4 17h16" /></button>
          {!viewOnly && <>
            <button type="button" className="wb-mbtn" aria-label={tr("실행취소", "Undo")} onClick={() => key("z")}><Icon d="M9 14L4 9l5-5M4 9h10a6 6 0 010 12h-3" /></button>
            <button type="button" className="wb-mbtn" aria-label={tr("다시 실행", "Redo")} onClick={() => key("z", true)}><Icon d="M15 14l5-5-5-5M20 9H10a6 6 0 000 12h3" /></button>
            <button type="button" className="wb-mbtn" aria-label={tr("선택한 것 지우기", "Delete selected")} onClick={deleteSelected}><Icon d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13" /></button>
          </>}
          <span className="wb-mfill" />
          {status && <span className="wb-mchip">{status}</span>}
          {me.expires_at && <span className="wb-mchip">{left(me.expires_at)}</span>}
          {viewOnly && <span className="wb-mchip">{tr("보기 전용", "View only")}</span>}
          {(() => {   // collapsed chip: show an 'AI' badge if an agent is attached (not counted in the number of people). It disappears with presence 30 seconds after leaving
            const ai = others.filter((u) => u.agent).length, people = others.length - ai + 1;
            return (
              <button type="button" className="wb-mchip wb-mchip-btn" aria-label={tr(`사람 · 따라가기 — ${people}명${ai ? `, AI 에이전트 ${ai}개 포함` : ""}`, `People · Follow — ${people} ${people === 1 ? "person" : "people"}${ai ? `, plus ${ai} AI agent${ai === 1 ? "" : "s"}` : ""}`)} onClick={() => follow.setMenu(true)}
                title={[me.short || me.name, ...others.map((u) => u.short || u.user)].join(", ")}>
                <i className="wb-mdot" style={{ background: me.color }} />{people > 1 ? tr(`${people}명`, `${people} people`) : (me.short || me.name)}{ai > 0 && <b className="wb-ai" aria-hidden="true">AI{ai > 1 ? ` ${ai}` : ""}</b>} ▾
              </button>
            );
          })()}
        </div>
      )}
      {mobile && menu && (
        <div className="wb-msheet-back" onClick={() => setMenu(false)}>
          <div className="wb-mmenu" role="menu" onClick={(e) => e.stopPropagation()}>
            <p className="wb-mmenu-who">{[me.short || me.name, ...others.map((u) => u.short || u.user)].join(" · ")}</p>
            <button type="button" role="menuitem" onClick={() => { setMenu(false); location.hash = ""; }}>{tr("← 보드 목록", "← Boards")}</button>
            {!viewOnly && <button type="button" role="menuitem" onClick={() => { setMenu(false); invite("edit"); }}>{tr("초대 링크 복사(같이 그리기)", "Copy invite link (draw together)")}</button>}
            {me.invite && me.invite.view && <button type="button" role="menuitem" onClick={() => { setMenu(false); invite("view"); }}>{tr("보기 전용 링크 복사", "Copy view-only link")}</button>}
            <button type="button" role="menuitem" onClick={() => { setMenu(false); savePng(); }}>{tr("그림 저장…", "Save image…")}</button>
            {!viewOnly && <button type="button" role="menuitem" onClick={() => { setMenu(false); pickImport(); }}>{tr("파일 가져오기…", "Import file…")}</button>}
            {!viewOnly && <button type="button" role="menuitem" onClick={() => { setMenu(false); pages.add(); }}>{tr("+ 새 장(판서 넘기기)", "+ New page")}</button>}
            <button type="button" role="menuitem" onClick={() => { setMenu(false); toggleGrid(); }}>{grid ? tr("모눈 끄기", "Hide grid") : tr("모눈 보기", "Show grid")}</button>
            <button type="button" role="menuitem" onClick={() => { setMenu(false); follow.setMenu(true); }}>{tr("사람 · 따라가기…", "People · Follow…")}</button>
            {!viewOnly && <button type="button" role="menuitem" onClick={() => { setMenu(false); follow.summon(); }}>{tr("모두 나에게(화면 모으기)", "Bring everyone to me")}</button>}
            {!viewOnly && <button type="button" role="menuitem" onClick={() => { setMenu(false); openMermaid(excal); }}>{tr("글로 흐름도 만들기(mermaid)…", "Flowchart from text (mermaid)…")}</button>}
            {!viewOnly && <button type="button" role="menuitem" onClick={async () => {
              setMenu(false);
              const t = window.prompt ? window.prompt(tr("보드 이름", "Board name"), meta ? meta.title : "") : null;
              if (t) setMeta(await api.rename(id, t));
            }}>{tr("보드 이름 바꾸기", "Rename board")}</button>}
            <button type="button" role="menuitem" onClick={() => { setMenu(false); toggleTheme(); }}>{theme === "dark" ? tr("밝은 화면", "Light mode") : tr("어두운 화면", "Dark mode")}</button>
            <button type="button" role="menuitem" lang={otherLang.code} onClick={() => { setMenu(false); switchLang(); }}>{otherLang.label}</button>
          </div>
        </div>
      )}

      {!viewOnly && <div className="wb-dock" role="toolbar" aria-label={tr("도구", "Tools")} ref={dockRef}
        onMouseDown={keep}
        onClick={() => setTimeout(refocus, 0)}>
        <div className="wb-group wb-tools">
          {TOOLS.map((t) => (
            <React.Fragment key={t.id}><button type="button" className={`wb-tool ${tool === t.id ? "on" : ""}${t.id === "freedraw" ? " wb-tool-pen" : ""}`} aria-pressed={tool === t.id} title={`${t.label} (${t.key})`}
              onClick={() => (t.id === "freedraw" && tool === "freedraw" ? inkx.again() : pick(t.id))}>
              <Icon d={t.icon} /><span>{t.id === "math" && mathSel ? tr("수식 고침", "Edit math") : (mobile && t.id === "selection" ? tr("선택", "Select") : mobile && t.id === "hand" ? tr("이동", "Pan") : t.id === "freedraw" && inkx.mode === "hl" ? tr("형광", "Marker") : t.label)}</span>
            </button>{t.id === "math" && <ShapeTools excal={excal} tool={tool} pick={pick} mobile={mobile} />}</React.Fragment>
          ))}
        </div>
        {showInk && !sheet && inks}
        {tool === "freedraw" && (
          <div className="wb-group wb-slider">
            <InkOptions x={inkx} ink={ink} theme={theme} />
            <input type="range" min="0.5" max="8" step="0.5" value={width} onChange={(e) => setStroke(+e.target.value)} aria-label={tr("펜 굵기", "Pen width")} title={tr("펜 굵기", "Pen width")} />
            <i className="wb-dot" style={{ width: Math.max(3, width * 4), height: Math.max(3, width * 4), background: ink, opacity: inkx.mode === "hl" ? 0.5 : 1 }} />
          </div>
        )}
        {showText && !sheet && <div className="wb-group wb-textctl" aria-label={tr("글꼴 · 크기", "Font · size")}>{fontButton}{sizeControl}</div>}
        {tool === "text" && coarse && !sheet && <div className="wb-group wb-hint">{tr("글을 넣을 자리를 누르세요", "Tap where the text should go")}</div>}
        {tool === "laser" && <div className="wb-group wb-hint">{tr("끌면 잠깐 빛나는 선이 모두에게 보여요", "Drag to show everyone a brief glowing trail")}</div>}
        {SHAPE_TYPES.has(tool) && !mobile && <div className="wb-group wb-hint">{tr("상자를 두 번 누르면 글", "Double-click a box to add text")}</div>}
        <button type="button" className={`wb-tool wb-deskmore ${deskOpen ? "on" : ""}`} aria-expanded={deskOpen} title={tr("취소 · 다시 · 지우기 · 화면 · 초대", "Undo · Redo · Delete · Theme · Invite")} onClick={() => setDeskOpen((v) => !v)}><Icon d="M5 12h.01M12 12h.01M19 12h.01" /><span>{tr("더 보기", "More")}</span></button>
        <div className={`wb-deskwrap${deskOpen ? " open" : ""}`}>
        <div className="wb-group wb-desk">
          <button type="button" className="wb-tool" title={tr("실행취소(내 작업만)", "Undo (your changes only)")} onClick={() => key("z")}><Icon d="M9 14L4 9l5-5M4 9h10a6 6 0 010 12h-3" /><span>{tr("취소", "Undo")}</span></button>
          <button type="button" className="wb-tool" title={tr("다시 실행", "Redo")} onClick={() => key("z", true)}><Icon d="M15 14l5-5-5-5M20 9H10a6 6 0 000 12h3" /><span>{tr("다시", "Redo")}</span></button>
          <button type="button" className="wb-tool" title={tr("선택한 것 지우기", "Delete selected")} onClick={deleteSelected}><Icon d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13" /><span>{tr("선택 삭제", "Delete")}</span></button>
        </div>
        <div className="wb-group wb-desk">
          <button type="button" className="wb-tool" title={theme === "dark" ? tr("밝은 화면", "Light mode") : tr("어두운 화면", "Dark mode")} onClick={toggleTheme}>
            <Icon d={theme === "dark" ? "M12 4V2M12 22v-2M4 12H2M22 12h-2M5 5L4 4M20 20l-1-1M5 19l-1 1M20 4l-1 1M12 8a4 4 0 100 8 4 4 0 000-8z" : "M20 14A8 8 0 1110 4a6 6 0 0010 10z"} />
            <span>{theme === "dark" ? tr("밝게", "Light") : tr("어둡게", "Dark")}</span>
          </button>
          <button type="button" className="wb-tool" title={tr("이 보드로 바로 들어오는 링크를 복사해요", "Copy a link that opens this board")} onClick={() => invite("edit")}><Icon d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1" /><span>{tr("초대", "Invite")}</span></button>
        </div>
        </div>
      </div>}

      {/* phone text-entry sheet: always attached so focus (keyboard) can be given at the moment of the tap */}
      {coarse && !viewOnly && (
        <div className={`wb-sheet ${sheet ? "open" : ""}`} aria-hidden={!sheet} onMouseDown={keep}>
          <textarea ref={sheetRef} value={sheetText} rows={3} placeholder={handwritingHint()} aria-label={tr("글", "Text")} autoCapitalize="off"
            tabIndex={sheet ? 0 : -1}
            style={{ fontFamily: curFont && curFont.family ? `"${curFont.css}", system-ui, sans-serif` : undefined, color: theme === "dark" ? undefined : ink }}
            onChange={(e) => setSheetText(e.target.value)}
            onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); commitSheet(); } if (e.key === "Escape") closeSheet(); }} />
          <div className="wb-sheet-row">
            {fontButton}{sizeControl}
          </div>
          <div className="wb-sheet-row wb-sheet-inks">{inks}</div>
          <div className="wb-sheet-row wb-sheet-actions">
            <button type="button" className="wb-btn" onClick={closeSheet}>{tr("취소", "Cancel")}</button>
            <button type="button" className="wb-primary" onClick={commitSheet}>{sheet && sheet.had ? (sheetText.trim() ? tr("고치기", "Update") : tr("지우기", "Delete")) : tr("넣기", "Insert")}</button>
          </div>
        </div>
      )}

      {fontPop && <FontPicker font={font} sheet={sheet} onPick={setFontChoice} keep={keep}>{!sheet && <TextLayout layout={layout} onPatch={setTextLayout} />}</FontPicker>}
      {moreInk && (
        <div className={`wb-pop wb-more-pop${sheet ? " wb-pop-top" : ""}`} role="dialog" aria-label={tr("색 더 보기", "More colors")} onMouseDown={keep}>
          <div className="wb-more-grid">
            {MORE_COLORS.map((c, i) => (
              <button key={c + i} type="button" className={`wb-swatch ${norm(ink) === norm(c) ? "on" : ""}`} aria-label={tr(`색 ${c}`, `Color ${c}`)} title={c} onClick={() => { setInkColor(c); setMoreInk(false); }}>
                <i style={{ background: c, filter: theme === "dark" ? DARK_FILTER : undefined }} />
              </button>
            ))}
          </div>
          <label className="wb-more-custom">{tr("직접 고르기", "Pick your own")}
            <input type="color" value={/^#[0-9a-f]{6}$/i.test(ink) ? ink : "#1e1e1e"} onChange={(e) => setInkColor(e.target.value)} aria-label={tr("색 직접 고르기", "Pick a custom color")} />
          </label>
        </div>
      )}
      {excal && <PageBar pages={pages} viewOnly={viewOnly} mobile={mobile} />}
      {excal && <FollowLayer f={follow} me={me} others={others} />}
      {!viewOnly && excal && <QuickConnect excal={excal} touch={coarse} blocked={!!sheet || !!math || saving || !!importing || menu || fontPop || moreInk || deskOpen || follow.menu || !!asking} />}
      {excal && <RequestLayer excal={excal} list={ask.list} touch={coarse} viewOnly={viewOnly} onAction={ask.act} />}
      {!viewOnly && excal && <AskButton excal={excal} touch={coarse} onOpen={setAsking} blocked={!!sheet || !!math || saving || !!importing || menu || fontPop || moreInk || deskOpen || follow.menu || !!asking} />}
      {asking && <AskSheet mobile={mobile} touch={coarse} at={asking.at} count={asking.ids.length} over={asking.over}
        onClose={() => { setAsking(null); setTimeout(refocus, 0); }}
        onSend={async ({ kind, note }) => { await ask.create({ kind, note, ids: asking.ids }); setAsking(null); setTimeout(refocus, 0); }} />}
      {saving && excal && <ExportSheet excal={excal} theme={theme} title={meta && meta.title} viewOnly={viewOnly} onClose={() => { setSaving(false); setTimeout(refocus, 0); }} />}
      {!viewOnly && <input ref={importInput} type="file" multiple hidden accept={coarse ? undefined : IMPORT_ACCEPT} aria-hidden="true" tabIndex={-1}
        onChange={(e) => { const fs = Array.from(e.target.files || []); if (fs.length) queueImport(fs); }} />}{/* phones (iOS) do not let you pick unfamiliar extensions in accept, so it is omitted; validate after picking */}
      {importing && excal && <ImportSheet key={importing.key} file={importing.file} rest={importQ.slice(1).map((x) => x.file.name)} excal={excal} boardId={id}
        onClose={() => { setImportQ((q) => q.slice(1)); setTimeout(refocus, 0); }} />}
      {math && <MathDialog initial={math.initial} board={math.board} ink={ink} mobile={mobile} touch={coarse} onCancel={() => { setMath(null); setTimeout(refocus, 0); }} onInsert={insertMath} />}
    </div>
  );
}
