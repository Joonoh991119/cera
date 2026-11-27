// Presenter following and 'bring everyone to me': pure logic. Tests: web/tests/follow.test.mjs
//
// What is exchanged: presence's view = the scene range [x1, y1, x2, y2] I am 'unobstructed' viewing (excluding the top and bottom bars, in scene coordinates).
// A follower fits that range to the usable area of their own screen (fitView). If the presenter is viewing one whole page,
// we fit that page instead of the range, so even when a phone follows a desktop presenter (or the reverse) the page fills the screen and the page number matches.
// summon = timestamp { at } (ms) attached by the server, only ever increasing per board. The largest at wins (whoever summoned last).
import { currentPage, rectOf, fitView } from "../pages/pages.js";
import { tr } from "../../i18n.js";

export const VIEW_MS = 150;   // The view range is sent only when it changes, and not more often than this interval (piggybacked on the 70ms cursor send)

const r1 = (n) => Math.round(n * 10) / 10;
// Scene range of the part of the screen that is not obstructed (safe, in screen coordinates)
export function sceneView(st, safe) {
  const z = (st && st.zoom && st.zoom.value) || 1;
  const x1 = (safe.x - (st.offsetLeft || 0)) / z - st.scrollX, y1 = (safe.y - (st.offsetTop || 0)) / z - st.scrollY;
  return [r1(x1), r1(y1), r1(x1 + safe.w / z), r1(y1 + safe.h / z)];
}
export const viewToRect = (v) => ({ x: v[0], y: v[1], w: v[2] - v[0], h: v[3] - v[1] });
export const validView = (v) => Array.isArray(v) && v.length === 4 && v.every(Number.isFinite) && v[2] > v[0] && v[3] > v[1];

// Whether two ranges are nearly equal: within 0.2% of the width counts as equal (so rounding and tiny jitter don't trigger a resend)
export function sameView(a, b) {
  if (!a || !b) return a === b;
  const tol = Math.max(0.05, (a[2] - a[0]) * 0.002);
  return a.every((n, i) => Math.abs(n - b[i]) <= tol);
}

const area = (r) => Math.max(0, r.w) * Math.max(0, r.h);
const overlap = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
// Where to follow: if the presenter is viewing one whole page (the page nearly fits, other pages show only slightly, and the page is at least 20% of the screen) that page, otherwise the range as is
export function followTarget(view, pages) {
  const rect = viewToRect(view);
  const i = currentPage(pages || [], rect);
  if (i < 0) return { rect, page: -1 };
  const p = rectOf(pages[i]);
  const whole = overlap(p, rect) >= 0.85 * area(p) && area(p) >= 0.2 * area(rect);
  const others = pages.some((q, j) => j !== i && overlap(rectOf(q), rect) > 0.25 * area(rectOf(q)));
  return whole && !others ? { rect: p, page: i } : { rect, page: -1 };
}
// Screen to fit (scrollX, scrollY, zoom). A page uses 95%, same as page-flipping; a range uses 100% (so everything the presenter saw fits)
export function followViewport(view, pages, safe, offset) {
  const t = followTarget(view, pages);
  return { ...fitView(t.rect, safe, offset, t.page >= 0 ? 0.95 : 1), page: t.page };
}

// Did I move the view? If the value differs from the one set by following (applied), the user zoomed or panned themselves
export function movedFrom(st, applied) {
  if (!applied) return false;
  const z = st.zoom.value;
  return Math.abs(z - applied.zoom) > applied.zoom * 1e-4 || Math.abs(st.scrollX - applied.scrollX) * z > 1 || Math.abs(st.scrollY - applied.scrollY) * z > 1;
}

// Movement that releases following: when it differs from the applied value and also from the pre-fit value (from, before Excalidraw has reflected the new value)
export function userMoved(st, applied) {
  if (!applied || !movedFrom(st, applied)) return false;
  const f = applied.from;
  return !f || movedFrom(st, { ...f });
}

const editor = (u) => u.role === "edit";   /* a missing field or another value (expired key, etc.) is not treated as an editor */
export const isCreator = (u, createdBy) => !!(u && createdBy && editor(u) && u.user === createdBy);
// The default person for a view-only user to follow: the board's creator (an edit connection), or if none, the editor who joined first (server list = join order).
// If one name has several connections (projector laptop + handwriting iPad), prefer a connection that is following nobody
export function defaultLeader(users, me, createdBy) {
  const eds = (users || []).filter((u) => u.client !== me && editor(u));
  const pick = (list) => list.find((u) => !u.follow) || list[0] || null;
  return pick(eds.filter((u) => isCreator(u, createdBy))) || pick(eds);
}
// Find the followed person in the list: if the same connection is gone, another connection with the same name (when that person reloaded)
export function findLeader(users, target, me) {
  if (!target) return null;
  const list = (users || []).filter((u) => u.client !== me);
  return list.find((u) => u.client === target.client) || list.find((u) => u.user === target.user && editor(u)) || null;   /* do not switch to a view-only connection that merely has the same name */
}
// Range to follow: a follower doesn't send a range back, only follow (whom they follow). So we
// walk that chain to its end and use the original presenter's range. If the chain loops back to me (mutual following) or breaks, null: nobody moves.
export function resolveView(users, leader, me, depth = 8) {
  const byId = new Map((users || []).map((u) => [u.client, u]));
  const seenIds = new Set([me]);
  let u = leader;
  for (let i = 0; u && i < depth; i++) {
    if (seenIds.has(u.client)) return null;
    seenIds.add(u.client);
    if (!u.follow) return validView(u.view) ? u.view : null;
    u = byId.get(u.follow);
  }
  return null;
}
// A newly arrived 'bring everyone to me': the latest one later than seen. If mine is the latest, { mine: true }
export function latestSummon(users, me, seen) {
  let best = null;
  for (const u of users || []) {
    const at = u.summon && Number(u.summon.at);
    if (Number.isFinite(at) && at > seen && (!best || at > best.at)) best = { at, user: u, mine: u.client === me };
  }
  return best;
}
export const maxSummon = (users) => (users || []).reduce((m, u) => Math.max(m, Number(u.summon && u.summon.at) || 0), 0);

// ---- Folding the people-chip row: only as many chips as fit between the page bar's right end + 12px and the screen's right edge, with the rest as a single '+k'
/**
  * budget = width available for other people's chips (px, excluding my chip, remaining time and view-only), widths = widths of other people's chips (in display order),
  * plusW = width of the '+k' chip, gap = spacing between chips. Returns: number of chips to show k (the remaining n−k go into '+k').
 */
export function fitChips(budget, widths, plusW, gap = 6) {
  const n = widths.length;
  let used = 0;
  for (let k = 0; k < n; k++) {
    used += widths[k] + gap;
    const rest = k + 1 < n ? plusW + gap : 0;
    if (used + rest > budget) return k;
  }
  return n;
}

/** Put the followed person first (so their chip stays visible even when folded); keep the rest in join order */
export function chipOrder(others, onClient) {
  const i = others.findIndex((u) => u.client === onClient);
  return i <= 0 ? others : [others[i], ...others.slice(0, i), ...others.slice(i + 1)];
}

// People popover header: agents (connections where the server set agent:true) are excluded from the people count and counted separately: 'N people connected · AI 1'.
// others is the list without me, so add 1 (me) to the people count.
export function peopleTitle(others) {
  const list = others || [];
  const ai = list.filter((u) => !!(u && u.agent)).length;
  const n = list.length - ai + 1;
  return tr(`접속한 사람 ${n}명${ai ? ` · AI ${ai}` : ""}`, `${n} ${n === 1 ? "person" : "people"} here${ai ? ` · AI ${ai}` : ""}`);
}
