// Proposal cards over the board (WP-6): one card above each proposal anchor (the locked dashed box an agent put around its result).
// Desktop: 제안 · Claude [바꾸기] [둘 다 두기] [버리기] [다시] + note + '?' (readings the agent was unsure of) + '읽은 뒤 바뀜 N'.
// Phone: the card is one 'choose' button that opens a bottom sheet with three big buttons ('다시' under '…'). View links: state only.
// After apply the card says 바꿈 [원본 ↺] [확정]. Every press goes to the server, which acts under its lock and tells everyone; each screen
// shows a 3 s toast (the apply toast has [원래대로]). Drawn as HTML, never as elements, so exports and undo never see it.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { anchorsOf, proposalOf, cardModel, cardSize, actionBody, toastModel, retryNote, retryIds, label, TITLE, driftText, stateText, TOAST_MS } from "./proposal.js";
import { toScreen, place } from "../ask/requests.js";
import { screenObstacles, viewSize, afterLayout } from "../ask/dom.js";
import { call } from "../ask/useRequests.js";
import { tr } from "../../i18n.js";
import "../ask/ask.css";
import "./proposal.css";

function why(r) {
  if (r.status === 409) return tr("그사이 다른 사람이 먼저 눌렀어요", "Someone else got there first");
  if (r.status === 403) return tr("이 링크로는 고를 수 없어요", "This link can't choose");
  if (r.status === 404) return tr("제안이 이미 없어요", "The proposal is gone");
  return (r.data && r.data.error) || `HTTP ${r.status}`;
}

function Sheet({ p, m, busy, onAct, onClose }) {
  const [more, setMore] = useState(false);
  const box = useRef(null), first = useRef(null);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => { first.current && first.current.focus({ preventScroll: true }); }, []);
  useEffect(() => {   // keys never reach the board below (Delete would erase the selection for everyone); Esc closes
    const guard = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeRef.current(); return; }
      if (box.current && box.current.contains(e.target)) { e.stopPropagation(); return; }
      e.stopPropagation(); e.preventDefault();
      if (e.key === "Tab") first.current && first.current.focus();
    };
    window.addEventListener("keydown", guard, true);
    return () => window.removeEventListener("keydown", guard, true);
  }, []);
  return (
    <div className="wb-ask-back" onPointerDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={box} className="wb-ask-sheet wb-ask-bottom wb-ask-touch wb-prop-sheet" role="dialog" aria-modal="true" aria-label={m.title} tabIndex={-1}>
        <p className="wb-ask-head">{m.title}{m.drift > 0 && <span className="wb-prop-drift">{driftText(m.drift)}</span>}</p>
        {m.note && <p className="wb-prop-sheetnote">{m.note}</p>}
        {m.uncertain.length > 0 && <Unsure list={m.uncertain} />}
        <div className="wb-prop-big">
          {m.sheet.map((a, i) => (
            <button key={a} ref={i === 0 ? first : undefined} type="button" className={a === "apply" ? "wb-primary" : "wb-btn"} disabled={busy}
              title={TITLE[a] && TITLE[a]()} onClick={() => onAct(p, a)}>{label(a)}</button>
          ))}
        </div>
        <div className="wb-ask-actions">
          {more ? m.more.map((a) => <button key={a} type="button" className="wb-btn" disabled={busy} onClick={() => onAct(p, a)}>{label(a)}</button>)
            : <button type="button" className="wb-btn" aria-label={tr("더 보기", "More")} onClick={() => setMore(true)}>…</button>}
          <button type="button" className="wb-btn" onClick={onClose}>{tr("닫기", "Close")}</button>
        </div>
      </div>
    </div>
  );
}

function Unsure({ list }) {
  return (
    <ul className="wb-prop-unsure">
      {list.map((u, i) => (
        <li key={i}><b>{u.text}</b>{Array.isArray(u.alts) && u.alts.length > 0 && <span> → {u.alts.join(" · ")}</span>}</li>
      ))}
    </ul>
  );
}

export default function ProposalLayer({ excal, boardId, client, touch, viewOnly, requests, create, onPids }) {
  const [frame, setFrame] = useState(0);
  const [busy, setBusy] = useState({});          // pid -> true while its press is on the way
  const [sheet, setSheet] = useState(null);      // phone: pid whose sheet is open
  const [unsure, setUnsure] = useState(null);    // desktop: pid whose '?' list is open
  const [toast, setToast] = useState(null);      // {text, undo, pid, key}
  const env = useRef({});
  env.current = { boardId, client, viewOnly, requests, create };

  useEffect(() => {
    if (!excal) return undefined;
    let raf = 0, cancel = null, had = false;
    const bump = () => setFrame((f) => f + 1);
    const later = (els) => {
      const has = anchorsOf(Array.isArray(els) ? els : excal.getSceneElements()).length > 0;
      if (!has && !had) return;   // no proposals on the board: nothing to redo on every stroke
      had = has;
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; bump(); });
      if (cancel) cancel();
      cancel = afterLayout(() => { cancel = null; bump(); });   // once more after the request chips and '+' buttons have moved
    };
    const offs = [excal.onChange((els) => later(els)), excal.onScrollChange(() => later())];
    const onResize = () => later();
    window.addEventListener("resize", onResize);
    window.addEventListener("wb-ask-layout", onResize);
    later();
    return () => { offs.forEach((f) => f()); window.removeEventListener("resize", onResize); window.removeEventListener("wb-ask-layout", onResize); cancelAnimationFrame(raf); if (cancel) cancel(); };
  }, [excal]);

  // `proposal` events from the server (Board.jsx forwards them as a window event): a toast on every screen
  useEffect(() => {
    let t = null;
    const on = (e) => {
      const m = toastModel(e.detail, env.current.viewOnly);
      if (!m) return;
      clearTimeout(t);
      // my own press just got 409 for this card: keep saying so (the winner's toast must not hide it), but offer its undo
      setToast((cur) => (cur && cur.conflict && cur.pid === m.pid && Date.now() - cur.key < TOAST_MS ? { ...cur, undo: m.undo } : { ...m, key: Date.now() }));
      t = setTimeout(() => setToast(null), TOAST_MS);
    };
    window.addEventListener("wb-proposal", on);
    return () => { window.removeEventListener("wb-proposal", on); clearTimeout(t); };
  }, []);

  const els = excal ? excal.getSceneElements() : [];
  const anchors = anchorsOf(els);
  const key = anchors.map((a) => `${proposalOf(a).pid}`).join(",");
  useEffect(() => { onPids && onPids(key); }, [key, onPids]);
  const sheetState = sheet ? (anchors.find((a) => proposalOf(a).pid === sheet) ? proposalOf(anchors.find((a) => proposalOf(a).pid === sheet)).status : "gone") : "";
  useEffect(() => { if (sheet && sheetState !== "proposed") setSheet(null); }, [sheet, sheetState]);   // someone else decided meanwhile: the sheet closes

  const say = (text) => { setToast({ text, undo: null, key: Date.now() }); setTimeout(() => setToast((t) => (t && t.text === text ? null : t)), TOAST_MS); };

  async function act(p, button) {
    const { boardId: bid, client: cl } = env.current;
    if (env.current.viewOnly || busy[p.pid]) return;
    if (button === "open") { setSheet(p.pid); return; }
    if (button === "retry") {
      const ids = retryIds(p, excal.getSceneElements());
      if (!ids.length) { say(tr("원본이 보드에 없어서 다시 맡길 수 없어요", "The original is gone, so it can't be sent again")); return; }
      const r0 = (env.current.requests || []).find((r) => r.rid === p.rid);
      try { await env.current.create({ kind: (r0 && r0.kind) || "auto", note: retryNote(p.pid, (r0 && r0.note) || ""), ids }); say(tr("다시 맡겼어요", "Sent to the AI again")); setSheet(null); }
      catch (e) { say(tr(`다시 보내지 못했어요: ${e.message}`, `Couldn't send it again: ${e.message}`)); }
      return;
    }
    const body = actionBody(p, button, cl);
    if (!body) return;
    setBusy((b) => ({ ...b, [p.pid]: true }));
    try {
      const r = await call("POST", `/api/whiteboard/boards/${bid}/proposals/${encodeURIComponent(p.pid)}`, body);
      if (r.status === 409) {
        const key = Date.now();
        setToast((cur) => ({ text: why(r), undo: cur && cur.pid === p.pid ? cur.undo : null, pid: p.pid, conflict: true, key }));
        setTimeout(() => setToast((cur) => (cur && cur.key === key ? null : cur)), TOAST_MS);
      } else if (r.status !== 200) say(why(r));
      else setSheet(null);
    } catch (_) { say(tr("보내지 못했어요 — 연결을 확인해 주세요", "Couldn't send — check your connection")); }
    finally { setBusy((b) => { const n = { ...b }; delete n[p.pid]; return n; }); }
  }

  const undo = (t) => {
    const a = anchorsOf(excal.getSceneElements()).find((e) => proposalOf(e).pid === t.pid);
    setToast(null);
    if (a) act(proposalOf(a), "revert"); else say(tr("제안이 이미 없어요", "The proposal is gone"));
  };

  const st = excal ? excal.getAppState() : null;
  const items = useMemo(() => {
    if (!excal || !anchors.length) return [];
    const view = viewSize(), placed = screenObstacles([".wb-ask-chip"]), out = [];
    for (const a of anchors) {
      const p = proposalOf(a), m = cardModel(p, { touch, viewOnly });
      const rect = toScreen({ x: a.x, y: a.y, w: a.width, h: a.height }, st);
      if (!(rect.x < view.w && rect.y < view.h && rect.x + rect.w > 0 && rect.y + rect.h > 0)) continue;
      const { w, h } = cardSize(m, touch);
      const at = place(rect, w, h, placed, view);
      if (!at) continue;
      const card = { x: at.x, y: at.y, w, h };
      placed.push(card);
      out.push({ p, m, card });
    }
    return out;
  }, [excal, frame, key, touch, viewOnly, st && st.scrollX, st && st.scrollY, st && st.zoom && st.zoom.value]);   // eslint-disable-line react-hooks/exhaustive-deps

  const open = sheet ? items.find((it) => it.p.pid === sheet) || anchors.map((a) => ({ p: proposalOf(a) })).find((it) => it.p.pid === sheet) : null;
  return (
    <>
      {items.length > 0 && (
        <div className="wb-ask-layer wb-prop-layer" aria-label={tr("AI 제안", "AI proposals")}>
          {items.map(({ p, m, card }) => (
            <div key={p.pid} className={`wb-prop-card wb-prop-${m.state}${touch ? " wb-ask-touch" : ""}`} style={{ left: card.x, top: card.y, width: card.w, height: card.h }}
              role="group" aria-label={m.title} onPointerDown={(e) => e.stopPropagation()}>
              <div className="wb-prop-head">
                <span className="wb-prop-title" title={m.title}>{m.title}</span>
                {m.uncertain.length > 0 && <button type="button" className="wb-prop-q" aria-expanded={unsure === p.pid}
                  title={tr("AI 가 자신 없는 곳", "Where the AI is unsure")} onClick={() => setUnsure((u) => (u === p.pid ? null : p.pid))}>?</button>}
                {m.drift > 0 && <span className="wb-prop-drift" title={tr("요청한 뒤 원본 쪽이 바뀌었어요", "The original changed after the request")}>{driftText(m.drift)}</span>}
                {touch && m.actions.map((a) => (
                  <button key={a} type="button" className="wb-ask-act" disabled={!!busy[p.pid]} title={TITLE[a] && TITLE[a]()} onClick={() => act(p, a)}>{label(a)}</button>
                ))}
              </div>
              {!touch && m.note && <small className="wb-prop-note" title={m.note}>{m.note}</small>}
              {!touch && (m.actions.length ? (
                <div className="wb-prop-row">
                  {m.actions.map((a) => (
                    <button key={a} type="button" className={`wb-ask-act${a === "apply" || a === "revert" ? " wb-prop-main" : ""}`} disabled={!!busy[p.pid]}
                      title={TITLE[a] && TITLE[a]()} onClick={() => act(p, a)}>{label(a)}</button>
                  ))}
                </div>
              ) : <small className="wb-prop-note">{stateText(m.state)}</small>)}
              {unsure === p.pid && (
                <div className="wb-prop-pop" role="dialog" aria-label={tr("자신 없는 곳", "Unsure readings")}><Unsure list={m.uncertain} /></div>
              )}
            </div>
          ))}
        </div>
      )}
      {open && !viewOnly && touch && <Sheet p={open.p} m={cardModel(open.p, { touch })} busy={!!busy[open.p.pid]} onAct={act} onClose={() => setSheet(null)} />}
      {toast && (
        <div key={toast.key} className="wb-prop-toast" role="status" aria-live="polite">
          <span>{toast.text}</span>
          {toast.undo && <button type="button" className="wb-ask-act" onClick={() => undo(toast)}>{tr("원래대로", "Undo")}</button>}
        </div>
      )}
    </>
  );
}
