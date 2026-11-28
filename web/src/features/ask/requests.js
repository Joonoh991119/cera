// 'Ask the AI' requests (WP-5): pure logic for the chips, the ask sheet and where things go on screen. No DOM, no React.
// A request lives in the board server's meta (not an element), so it never shows up in exports, undo, dragging or erasing.
// Shape (server.py request_create): {rid, n, by, kind, note, targets:[{id,v}], bbox:{x,y,w,h}, page, status, agent, reply, pid, created, updated}
import { tr } from "../../i18n.js";

export const NOTE_MAX = 200;            // server REQ_NOTE_MAX (longer -> 400)
export const TARGETS_MAX = 500;         // server REQ_TARGETS_MAX
export const COPY_AFTER_MS = 20000;     // an open request nobody took for this long shows '[copy request]'
export const KINDS = ["auto", "diagram", "math", "text", "graph", "tidy"];   // order of the chips in the sheet; the server's enum
const KIND_LABEL = { auto: ["알아서", "Auto"], text: ["글", "Text"], math: ["수식", "Math"], diagram: ["도식", "Diagram"], graph: ["그래프", "Graph"], tidy: ["정리", "Tidy"] };
const SHOWN = new Set(["open", "working", "proposed", "failed", "declined", "interrupted"]);   // done and cancelled have no chip

export const kindLabel = (k) => (KIND_LABEL[k] ? tr(...KIND_LABEL[k]) : String(k || ""));

/** Merge one request (event or POST answer) into the list: same rid -> keep the newer by `updated`. Sorted by n (request number). */
export function mergeRequest(list, r) {
  if (!r || typeof r.rid !== "string") return list;
  const i = list.findIndex((x) => x.rid === r.rid);
  if (i >= 0 && (list[i].updated || 0) > (r.updated || 0)) return list;
  const out = i >= 0 ? [...list.slice(0, i), r, ...list.slice(i + 1)] : [...list, r];
  return out.sort((a, b) => (a.n || 0) - (b.n || 0));
}

/** The full list from GET /requests replaces ours, but an entry we already hold with a newer `updated` wins (an event raced the GET). */
export function replaceAll(list, fresh) {
  let out = (fresh || []).filter((r) => r && typeof r.rid === "string");
  for (const r of list) {
    const j = out.findIndex((x) => x.rid === r.rid);
    if (j >= 0 && (r.updated || 0) > (out[j].updated || 0)) out = [...out.slice(0, j), r, ...out.slice(j + 1)];
  }
  return out.sort((a, b) => (a.n || 0) - (b.n || 0));
}

export const visibleRequests = (list) => (list || []).filter((r) => SHOWN.has(r.status) && r.bbox);

/** Chip text and buttons for one request. now in ms. viewOnly hides every button.
 * -> {text, sub, tone, actions:[cancel|copy|retry|close]} */
export function chipModel(r, now, viewOnly = false) {
  const who = r.by || "?", agent = r.agent || "AI", kind = kindLabel(r.kind);
  let text, sub = "", tone = r.status, actions;
  switch (r.status) {
    case "open":
      text = tr(`✦ ${who} · ${kind} · 대기 중`, `✦ ${who} · ${kind} · waiting`);
      if (now - (r.created || now) >= COPY_AFTER_MS) { sub = tr("에이전트가 아직 안 가져감", "No agent has taken it yet"); actions = ["copy", "cancel"]; } else actions = ["cancel"];
      break;
    case "working": text = tr(`✦ ${agent} 작업 중…`, `✦ ${agent} working…`); actions = ["cancel"]; break;
    case "proposed": text = tr("✦ 제안 도착", "✦ Proposal ready"); sub = r.reply || ""; actions = ["close"]; break;
    case "failed": text = tr(`✦ 실패: ${r.reply || "이유 없음"}`, `✦ Failed: ${r.reply || "no reason given"}`); actions = ["retry", "close"]; break;
    case "declined": text = tr(`✦ 거절: ${r.reply || "이유 없음"}`, `✦ Declined: ${r.reply || "no reason given"}`); actions = ["close"]; break;
    case "interrupted": text = tr("✦ 중단됨", "✦ Interrupted"); actions = ["retry", "close"]; break;
    default: text = `✦ ${r.status}`; actions = [];
  }
  return { text, sub, tone, actions: viewOnly ? [] : actions };
}

export const ACTION_LABEL = { cancel: () => tr("취소", "Cancel"), copy: () => tr("요청 문장 복사", "Copy request"), retry: () => tr("다시", "Retry"), close: () => tr("닫기", "Close") };
// what each button sends to POST /requests/<rid> (retry also creates a new request with the same targets first)
export const ACTION_STATUS = { cancel: "cancelled", close: "done", retry: "done" };

/** The sentence to paste into an agent session: board id and rid only - never an invite key (?k=) or an agent token (tb_ag_). */
export const copyText = (board, rid) => tr(`cera 보드 ${board} 의 요청 ${rid} 를 처리해 줘`, `Handle request ${rid} on cera board ${board}`);

/** Ids to send for the current selection: live selected elements plus the text bound inside a selected box (the agent reads labels too). */
export function targetIds(elements, selected) {
  const sel = selected || {};
  const live = (elements || []).filter((e) => e && !e.isDeleted);
  const pick = new Set(live.filter((e) => sel[e.id]).map((e) => e.id));
  for (const e of live) if (e.containerId && pick.has(e.containerId)) pick.add(e.id);
  const ids = live.filter((e) => pick.has(e.id)).map((e) => e.id);
  return { ids: ids.slice(0, TARGETS_MAX), over: ids.length > TARGETS_MAX };
}

/** A note the server will accept: trimmed, at most NOTE_MAX characters. */
export const noteOk = (note) => typeof note === "string" && note.trim().length <= NOTE_MAX;

/** Scene box {x,y,w,h} -> viewport rect, Excalidraw's formula (same as features/flow/connect.js toViewport). */
export function toScreen(b, st) {
  const z = (st && st.zoom && st.zoom.value) || 1;
  return { x: (b.x + st.scrollX) * z + (st.offsetLeft || 0), y: (b.y + st.scrollY) * z + (st.offsetTop || 0), w: b.w * z, h: b.h * z };
}

/** Bounding box (scene) of live selected elements, ignoring rotation; lines and strokes by their points. null if nothing. */
export function selectionBox(elements, selected) {
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  for (const e of elements || []) {
    if (!e || e.isDeleted || !(selected || {})[e.id]) continue;
    let bx1 = e.x, by1 = e.y, bx2 = e.x + (e.width || 0), by2 = e.y + (e.height || 0);
    if (Array.isArray(e.points) && e.points.length) {
      const xs = e.points.map((p) => p[0]), ys = e.points.map((p) => p[1]);
      bx1 = e.x + Math.min(...xs); bx2 = e.x + Math.max(...xs); by1 = e.y + Math.min(...ys); by2 = e.y + Math.max(...ys);
    }
    x1 = Math.min(x1, bx1, bx2); y1 = Math.min(y1, by1, by2); x2 = Math.max(x2, bx1, bx2); y2 = Math.max(y2, by1, by2);
  }
  return Number.isFinite(x1) ? { x: x1, y: y1, w: x2 - x1, h: y2 - y1 } : null;
}

const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Put a w x h box near `anchor` (a viewport rect) without covering any obstacle (tool bar, zoom, '?', page bar, top bar, '+' buttons, other chips).
 * Tries above-left (pushed further up past what it hits), below-left (pushed further down), then inside top-left (pushed away from the
 * nearer screen edge); each is clamped to the screen (margin 8). -> {x, y} or null when every spot is blocked. view = {w, h}. */
export function place(anchor, w, h, obstacles, view, gap = 6) {
  const m = 8, obs = obstacles || [];
  const tries = [[anchor.x, anchor.y - h - gap, -1], [anchor.x, anchor.y + anchor.h + gap, 1], [anchor.x + gap, anchor.y + gap, 0]];
  for (const [x0, y0, dir] of tries) {
    let x = clamp(x0, m, view.w - w - m), y = clamp(y0, m, view.h - h - m);
    for (let k = 0; k < 8; k++) {
      const box = { x, y, w, h };
      const ob = obs.find((o) => hit(box, o));
      if (!ob) return { x, y };
      const up = dir < 0 || (dir === 0 && ob.y + ob.h / 2 > view.h / 2);   // inside: bottom-half things (dock, zoom, '?') push up, top-half ones down
      y = up ? ob.y - h - gap : ob.y + ob.h + gap;
      if (y < m || y > view.h - h - m) break;
    }
  }
  return null;
}

/** Chip width (px) for its text and buttons: wide letters (Hangul, CJK, symbols) count 1 em, others 0.6 em; capped (then the text ends in "…"). */
const em = (t) => [...String(t || "")].reduce((n, c) => n + (c.charCodeAt(0) > 0x2000 ? 1 : 0.6), 0);
export function chipWidth(model, touch) {
  const f = touch ? 14.5 : 13.5, bf = touch ? 14 : 13, pad = touch ? 24 : 20;
  const btns = model.actions.reduce((s, a) => s + em(ACTION_LABEL[a]()) * bf + pad + 6, 0);
  const text = Math.max(em(model.text) * f, em(model.sub) * 12 - btns);
  return Math.min(touch ? 374 : 420, Math.ceil(28 + text + btns));
}

// Chip box sizes the CSS draws (border 2 + padding 4 each side + the button row; the 20 s line adds CHIP_H.sub). RequestLayer sets
// height and width from the layout, so what is drawn is what was placed.
export const CHIP_H = { desk: 38, touch: 56, sub: 20 };

/** Where each visible request's dashed box and chip go. Chips placed earlier (lower n) count as obstacles for later ones, so two
 * requests on overlapping areas get stacked chips; `order` (1, 2, …) is shown when a request's area overlaps another visible one.
 * A request whose area is entirely off screen gets no chip. -> [{rid, rect, chip:{x,y,w,h}|null, order|null, model}] */
export function layoutChips(reqs, st, obstacles, view, now, { touch = false, viewOnly = false } = {}) {
  const items = visibleRequests(reqs).map((r) => ({ r, rect: toScreen(r.bbox, st) }));
  const onScreen = (rc) => rc.x < view.w && rc.y < view.h && rc.x + rc.w > 0 && rc.y + rc.h > 0;
  const placed = [...(obstacles || [])], out = [];
  for (const it of items) {
    const model = chipModel(it.r, now, viewOnly);
    const h = touch ? CHIP_H.touch : CHIP_H.desk, w = chipWidth(model, touch);   // the chip is drawn with exactly this height and width
    const overl = items.filter((o) => o !== it && hit(o.rect, it.rect)).map((o) => o.r.n);
    const order = overl.length ? 1 + overl.filter((n) => n < it.r.n).length : null;
    let chip = null;
    if (onScreen(it.rect)) {
      const hh = model.sub ? h + CHIP_H.sub : h;
      const p = place(it.rect, w, hh, placed, view);
      if (p) { chip = { x: p.x, y: p.y, w, h: hh }; placed.push(chip); }
    }
    out.push({ rid: it.r.rid, rect: it.rect, chip, order, model, r: it.r });
  }
  return out;
}

/** Where the '✦ AI' button goes for a selection rect (viewport): above its right end, else below, avoiding the obstacles. */
export function askSpot(sel, w, h, obstacles, view) {
  if (!sel) return null;
  const right = { x: sel.x + sel.w - w, y: sel.y, w, h: sel.h };   // anchor so the button's right edge lines up with the selection's
  return place(right, w, h, obstacles, view, 10);
}
