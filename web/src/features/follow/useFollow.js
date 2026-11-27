// Presenter following and 'bring everyone to me': Board.jsx connects only this hook and FollowUI. The computation is in follow.js.
//
// Sending: my view range (view) is sent only when it changes, and not more often than VIEW_MS (150ms). A range waiting when the cursor is sent (70ms)
//   is attached via take() and sent together; if the cursor doesn't move (wheel, page flipping), a separate timer sends it. While following, no range is sent
//   (view: null) and only whom I follow (follow) is sent once. When 30-60 lecture attendees echoed their range back on every presenter step, broadcast traffic
//   grew with the square of the head count. If someone follows a follower, the receiver walks the follow chain to the end and follows the original presenter (follow.js resolveView).
// Receiving: when the followed person's view changes, fit my screen. If I zoom or pan myself (differing from the applied value), following is released.
//   A plain press (tap) does not release it. Excalidraw's userToFollow releases on any press on the canvas, so we don't use it.
// A view-only user, on joining, follows the board's creator (or if none, the first editor) (auto). If they release it by moving, it stays released until they press follow again.
import { useCallback, useEffect, useRef, useState } from "react";
import { CaptureUpdateAction } from "@excalidraw/excalidraw";
import { listPages } from "../pages/pages.js";
import { safeRect } from "../pages/usePages.js";
import { VIEW_MS, sceneView, sameView, validView, followViewport, movedFrom, userMoved, defaultLeader, findLeader, latestSummon, maxSummon, resolveView, isCreator } from "./follow.js";
import { tr } from "../../i18n.js";

// In Excalidraw 0.18, a toast's timer restarts at every redraw (it never disappears while others' cursors move), so we close it ourselves
function toast(excal, message, ms) {
  if (!excal) return;
  excal.setToast({ message, duration: ms });
  setTimeout(() => { const t = excal.getAppState().toast; if (t && t.message === message) excal.setToast(null); }, ms);
}

const GONE_MS = 5000;   // If the followed person is missing from the list for this long, stop (we wait through a brief drop and reconnect)

export default function useFollow({ excal, link, client, me, meta, users }) {
  const viewOnly = me.role === "view";
  const [target, setTarget] = useState(null);   // { client, user, how: pick | summon | auto }
  const [menu, setMenu] = useState(false);
  const auto = useRef(viewOnly);                // view-only: whether we are automatically following the default presenter
  const seen = useRef(null);                    // latest summon time already seen (the one from the first list received is not obeyed)
  const applied = useRef(null);                 // last fitted view { scrollX, scrollY, zoom }
  const leaderView = useRef(null);
  const out = useRef({ sent: null, at: 0, pending: null, timer: 0 });
  const tgt = useRef(null); tgt.current = target;
  const usersRef = useRef(users); usersRef.current = users;

  // ---------------- Sending
  const flushView = useCallback(() => {
    const o = out.current;
    clearTimeout(o.timer); o.timer = 0;
    if (!o.pending || !link.current) return;
    link.current.sendPresence({ view: o.pending });
    o.sent = o.pending; o.pending = null; o.at = Date.now();
  }, [link]);
  const queueView = useCallback((v) => {
    const o = out.current;
    if (!validView(v) || sameView(v, o.sent)) { o.pending = null; return; }
    o.pending = v;
    if (!o.timer) o.timer = setTimeout(flushView, Math.max(0, VIEW_MS - (Date.now() - o.at)));
  }, [flushView]);
  // Field to attach when sending the cursor: if a range is waiting and the interval has passed
  const take = useCallback(() => {
    const o = out.current;
    if (!o.pending || Date.now() - o.at < VIEW_MS) return {};
    clearTimeout(o.timer); o.timer = 0;
    const view = o.pending; o.sent = view; o.pending = null; o.at = Date.now();
    return { view };
  }, []);
  const myView = useCallback(() => (excal ? sceneView(excal.getAppState(), safeRect()) : null), [excal]);

  // ---------------- Fitting
  const apply = useCallback(() => {
    const v = leaderView.current;
    if (!excal || !tgt.current || !validView(v)) return;
    const st = excal.getAppState();
    const goal = followViewport(v, listPages(excal.getSceneElements()), safeRect(), { left: st.offsetLeft || 0, top: st.offsetTop || 0 });
    // Before updateScene, so onChange in between doesn't see it as 'the user moved'. from = value before fitting: Excalidraw's getAppState only gets the new value on the next draw,
    // so the old value in that window (same frame) is not treated as movement (userMoved)
    const next = { scrollX: goal.scrollX, scrollY: goal.scrollY, zoom: goal.zoom };
    if (!movedFrom(st, next)) { applied.current = { ...next, from: applied.current && applied.current.from }; return; }
    applied.current = { ...next, from: { scrollX: st.scrollX, scrollY: st.scrollY, zoom: st.zoom.value } };
    excal.updateScene({ appState: { zoom: { value: goal.zoom }, scrollX: goal.scrollX, scrollY: goal.scrollY }, captureUpdate: CaptureUpdateAction.NEVER });
  }, [excal]);

  // Announce the start and end of following (while following, only follow is sent instead of a range). When it ends, send my range again.
  const announce = useCallback((who) => {
    const o = out.current; clearTimeout(o.timer); o.timer = 0; o.pending = null; o.sent = null;
    if (link.current) link.current.sendPresence(who ? { view: null, follow: who } : { follow: null });
  }, [link]);
  const drop = useCallback(() => {   /* drop quietly (when the auto-followed presenter left and we pick again) */
    applied.current = null; leaderView.current = null;
    setTarget(null);
  }, []);
  const stop = useCallback((why) => {
    if (tgt.current && tgt.current.how === "auto") auto.current = false;
    drop();
    announce(null);
    queueView(myView());
    if (why === "moved") toast(excal, tr("화면을 직접 움직여서 따라가기를 멈췄어요", "You moved the view, so following stopped"), 2500);
    if (why === "gone") toast(excal, tr("따라가던 사람이 나가서 따라가기를 멈췄어요", "The person you were following left, so following stopped"), 3000);
  }, [excal, drop, announce, queueView, myView]);
  const follow = useCallback((u, how = "pick") => {
    if (!u || u.client === client) return;
    auto.current = how === "auto";   /* choosing manually or being summoned means we don't automatically fall back to the default presenter */
    applied.current = null;
    leaderView.current = resolveView(usersRef.current, u, client);
    if (!tgt.current || tgt.current.client !== u.client) announce(u.client);
    setTarget({ client: u.client, user: u.user, short: u.short, color: u.color, how });
    setMenu(false);
    requestAnimationFrame(() => requestAnimationFrame(apply));   /* fit after the banner is drawn (once the usable screen area is settled) */
  }, [client, apply, announce]);
  const toggle = useCallback((u) => { setMenu(false); if (tgt.current && tgt.current.client === u.client) stop("button"); else follow(u, "pick"); }, [follow, stop]);

  // 'Bring everyone to me': editors only (the server blocks it too). Attach my range so recipients can fit immediately
  const summon = useCallback(() => {
    if (viewOnly || !link.current || !excal) return;
    if (tgt.current) stop("summon");
    const v = myView();
    const o = out.current; clearTimeout(o.timer); o.timer = 0; o.pending = null; o.sent = v; o.at = Date.now();
    const r = link.current.sendPresence(validView(v) ? { summon: true, view: v } : { summon: true });
    setMenu(false);
    // Say 'gathered' only if it was actually sent: WebSocket returns true/false immediately, HTTP gives the POST result
    const said = (ok) => toast(excal, ok ? tr("모두의 화면을 내 화면으로 모았어요", "Everyone now sees your view") : tr("연결이 끊겨 모으지 못했어요 — 잠시 뒤 다시 눌러 주세요", "Disconnected, couldn't bring everyone — try again in a moment"), ok ? 2500 : 4000);
    if (r && typeof r.then === "function") r.then(said, () => said(false)); else said(!!r);
  }, [viewOnly, link, excal, myView, stop]);

  // ---------------- When the scene or view changes: check whether I moved it myself, and send my range (or the range to relay). If a new page arrives, fit again.
  useEffect(() => {
    if (!excal) return undefined;
    let raf = 0, pages = -1, last = "";
    const tick = () => {
      raf = 0;
      const st = excal.getAppState();
      if (tgt.current && userMoved(st, applied.current)) stop("moved");
      if (tgt.current) {
        const n = listPages(excal.getSceneElements()).length;
        if (n !== pages) { pages = n; apply(); }
        last = "";
        return;
      }
      const key = `${st.scrollX}|${st.scrollY}|${st.zoom.value}|${st.width}|${st.height}`;   /* don't re-measure while drawing (view unchanged) */
      if (key !== last) { last = key; queueView(sceneView(st, safeRect())); }
    };
    const later = () => { if (!raf) raf = requestAnimationFrame(tick); };
    const offs = [excal.onChange(later), excal.onScrollChange(later)];
    const resize = () => { apply(); later(); };
    window.addEventListener("resize", resize);
    later();
    return () => { offs.forEach((f) => typeof f === "function" && f()); window.removeEventListener("resize", resize); cancelAnimationFrame(raf); clearTimeout(out.current.timer); out.current.timer = 0; };
  }, [excal, apply, stop, queueView]);

  // ---------------- When the user list arrives: summon, default presenter, and the followed person's new range
  const gone = useRef(0);
  useEffect(() => {
    if (!excal || !users || !users.length) return;   /* the initial empty list ([]) is not the server's list */
    if (seen.current === null) { seen.current = maxSummon(users); }   /* a 'bring everyone to me' pressed before joining is not obeyed */
    else {
      const s = latestSummon(users, client, seen.current);
      if (s) {
        seen.current = s.at;
        if (!s.mine) { auto.current = false; follow(s.user, "summon"); return; }
      }
    }
    let t = tgt.current;
    const owner = meta && meta.created_by;
    const u = t ? findLeader(users, t, client) : null;
    // Auto-follow: the first pick, and if the creator joins late, only an immediate 'move up' to them. If the followed person disappears,
    // pick again only after waiting GONE_MS, so that during the 2 seconds a professor reloads, a student's view doesn't jump to the TA and back
    if (auto.current && (!t || t.how === "auto")) {
      const d = defaultLeader(users, client, owner);
      if (d && (!t || (u && u.client !== d.client && isCreator(d, owner) && !isCreator(u, owner)))) { follow(d, "auto"); return; }
    }
    if (!t) return;
    if (!u) {
      if (!gone.current) gone.current = setTimeout(() => {
        gone.current = 0;
        const c = tgt.current;
        if (!c || findLeader(usersRef.current, c, client)) return;
        if (c.how === "auto" && auto.current) { drop(); announce(null); }   /* the next list will pick the default presenter again */
        else stop("gone");
      }, GONE_MS);
      return;
    }
    clearTimeout(gone.current); gone.current = 0;
    if (u.client !== t.client) { t = { ...t, client: u.client }; setTarget(t); announce(u.client); }   /* the same person after a reload */
    const v = resolveView(users, u, client);
    if (v && !sameView(v, leaderView.current)) { leaderView.current = v; apply(); }
  }, [excal, users, client, meta, follow, stop, apply, drop, announce]);
  useEffect(() => () => clearTimeout(gone.current), []);

  const leader = target ? findLeader(users, target, client) : null;
  const suggest = viewOnly && !target ? defaultLeader(users, client, meta && meta.created_by) : null;   /* view-only: if released, offer 'follow again' */
  return { target, leader, suggest, menu, setMenu, follow, toggle, stop, summon, take, canSummon: !viewOnly };
}
