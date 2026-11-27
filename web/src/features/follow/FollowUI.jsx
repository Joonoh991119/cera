// Follow-mode UI pieces: the people chips in the top bar, the people popover (follow / bring everyone to me), and the 'Following · Stop' banner.
// To avoid widening the bar, no new button was added: clicking someone else's chip follows them, and clicking my own chip (▾) opens the people popover.
// At widths where chips are hidden (other people's chips at <= 900px, my chip at <= 640px), my chip, the 'n people' chip in the phone's top row, and the menu item 'People · Follow' open the popover.
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fitChips, chipOrder, peopleTitle } from "./follow.js";
import "./follow.css";
import { tr } from "../../i18n.js";

const name = (u) => (u ? u.short || u.user : "");   // chip (narrow spaces)
const full = (u) => (u ? u.user || u.short : "");   // banner and people popover: the full name
// Agents: the server sets agent:true only on connections that came in with a token, and names them '<name> · AI' (a name prefix alone cannot turn it on).
// Chips and the people popover show an 'AI' tag next to the name. The cursor name tag shows the server's short ('Claude AI') as is.
const isAI = (u) => !!(u && u.agent);
const aiBase = (u) => String((u && u.user) || "").replace(/ · AI$/, "") || "AI";
const AiTag = () => <span className="wb-ai" aria-hidden="true">AI</span>;
// Rows in the people popover announce 'AI agent' to screen readers via text instead of the (aria-hidden) tag (chips use the button's aria-label)
const Who = ({ u, chip }) => (isAI(u)
  ? <>{chip ? aiBase(u).slice(0, 8) : aiBase(u)}<AiTag />{!chip && <span className="wb-sr-only">{tr(" (AI 에이전트)", " (AI agent)")}</span>}</>
  : <>{chip ? name(u) : full(u)}</>);

// The chip row grows only within 'page bar's right end + 12px to the screen's right edge' (at 1024px with 9 people it slid under the page bar and hid my chip).
// Other people's chips that don't fit fold into a single '+k' chip, which opens the people popover when clicked. My chip, the remaining time and view-only are never folded. The chip of the person being followed comes first.
// Widths are measured in an invisible measuring layer (.wb-chip-measure): the folded chip widths are needed too, so chips can be expanded again when there is more room.
export function FollowChips({ f, me, others }) {
  const on = f.target && f.target.client;
  const ordered = chipOrder(others, on);
  const meRef = useRef(null);
  const measRef = useRef(null);
  const [fit, setFit] = useState(ordered.length);
  const measure = useRef(null);
  measure.current = () => {
    const meEl = meRef.current, meas = measRef.current, row = meEl && meEl.closest(".wb-people");
    if (!row || !meas) return;
    const right = row.getBoundingClientRect().right;
    const pb = document.querySelector(".wb-pages-top");
    const mb = document.querySelector(".excalidraw .dropdown-menu-button");
    const left = pb ? pb.getBoundingClientRect().right + 12 : mb ? mb.getBoundingClientRect().right + 12 : 12;
    const gap = parseFloat(getComputedStyle(row).columnGap) || 6;
    let fixed = 0;
    for (const c of row.children) {
      if (c.dataset && c.dataset.chip) continue;   /* other people's chips, '+k', measuring layer */
      const w = c.getBoundingClientRect().width;
      if (w) fixed += w + gap;
    }
    const ws = [...meas.children].map((c) => c.getBoundingClientRect().width);
    const plusW = ws.pop() || 0;
    const k = fitChips(right - left - fixed, ws, plusW, gap);
    setFit((p) => (p === k ? p : k));
  };
  useLayoutEffect(() => { measure.current(); });
  useEffect(() => {
    const run = () => measure.current();
    window.addEventListener("resize", run);
    let ro = null;
    if ("ResizeObserver" in window) {
      ro = new ResizeObserver(run);
      const pb = document.querySelector(".wb-pages-top"); if (pb) ro.observe(pb);
      const row = meRef.current && meRef.current.closest(".wb-people"); if (row) ro.observe(row);
    }
    const t = setInterval(() => { if (ro) { const pb = document.querySelector(".wb-pages-top"); if (pb) ro.observe(pb); } }, 2000);   /* the page bar may appear later */
    return () => { window.removeEventListener("resize", run); if (ro) ro.disconnect(); clearInterval(t); };
  }, []);
  const shown = ordered.slice(0, fit), hidden = ordered.length - shown.length;
  const chip = (u, measureOnly) => (
    <button key={u.client} type="button" data-chip={measureOnly ? undefined : "other"} tabIndex={measureOnly ? -1 : undefined}
      className={`wb-chip wb-chip-btn${on === u.client ? " on" : ""}`} style={{ borderColor: u.color, ...(on === u.client ? { background: `${u.color}40`, borderStyle: "double", borderWidth: 3 } : null) }}
      title={(isAI(u) ? tr(`${aiBase(u)} — AI 에이전트. `, `${aiBase(u)} — AI agent. `) : `${u.user} — `) + (on === u.client ? tr("따라가는 중(누르면 그만)", "Following (click to stop)") : tr("누르면 이 화면을 따라가요", "Click to follow this view"))}
      aria-label={isAI(u) ? tr(`${aiBase(u)} (AI 에이전트)`, `${aiBase(u)} (AI agent)`) : undefined} aria-pressed={on === u.client} onClick={measureOnly ? undefined : () => f.toggle(u)}>
      {on === u.client ? "◉ " : ""}<Who u={u} chip />
    </button>
  );
  return <>
    <button ref={meRef} type="button" className="wb-chip wb-chip-me wb-chip-btn" style={{ borderColor: me.color }} title={tr("나 · 커서 색 — 누르면 사람 목록(따라가기 · 모두 나에게)", "You · cursor color — click for people (follow · bring everyone to me)")}
      aria-haspopup="dialog" aria-expanded={f.menu} onClick={() => f.setMenu((v) => !v)}>{me.short || me.name} ▾</button>
    {shown.map((u) => chip(u, false))}
    {hidden > 0 && (
      <button type="button" data-chip="plus" className="wb-chip wb-chip-btn wb-chip-more" title={tr(`${ordered.slice(fit).map((u) => u.user).join(", ")} — 누르면 사람 목록`, `${ordered.slice(fit).map((u) => u.user).join(", ")} — click for people`)}
        aria-label={tr(`사람 ${hidden}명 더 — 사람 목록 열기`, `${hidden} more ${hidden === 1 ? "person" : "people"} — open people list`)} aria-haspopup="dialog" onClick={() => f.setMenu(true)}>+{hidden}</button>
    )}
    <span ref={measRef} className="wb-chip-measure" data-chip="measure" aria-hidden="true">
      {ordered.map((u) => chip(u, true))}
      <span className="wb-chip wb-chip-btn wb-chip-more">+{ordered.length}</span>
    </span>
  </>;
}

function PeopleMenu({ f, me, others }) {
  const box = useRef(null);
  useEffect(() => {   /* when opened, focus the popover (so keys don't leak to the board below, where Delete would erase the selection) */
    const b = box.current && box.current.querySelector("button");
    if (b) b.focus({ preventScroll: true });
  }, []);
  const close = () => f.setMenu(false);
  const on = f.target && f.target.client;
  return (
    <div className="wb-follow-back" onClick={close}>
      <div className="wb-follow-menu" role="dialog" aria-label={tr("사람 · 따라가기", "People · Follow")} ref={box} onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Escape") close(); }}>
        <p className="wb-follow-title">{peopleTitle(others)}</p>
        <div className="wb-follow-row wb-follow-self"><i style={{ background: me.color }} /><span>{me.name || me.short}{tr(" (나)", " (you)")}{me.role === "view" ? tr(" · 보기 전용", " · view only") : ""}</span></div>
        {others.map((u) => (
          <div key={u.client} className="wb-follow-row">
            <i style={{ background: u.color }} /><span title={isAI(u) ? tr(`${aiBase(u)} — AI 에이전트`, `${aiBase(u)} — AI agent`) : u.user}><Who u={u} />{u.role === "view" ? tr(" · 보기 전용", " · view only") : ""}</span>
            <button type="button" className={on === u.client ? "on" : ""} onClick={() => f.toggle(u)}>{on === u.client ? tr("그만", "Stop") : tr("따라가기", "Follow")}</button>
          </div>
        ))}
        {!others.length && <p className="wb-follow-note">{tr("아직 다른 사람이 없어요", "No one else is here yet")}</p>}
        {f.canSummon && <>
          <button type="button" className="wb-follow-summon" onClick={f.summon} disabled={!others.length}>{tr("모두 나에게 — 모두의 화면을 내 화면으로", "Bring everyone to my view")}</button>
          <p className="wb-follow-note">{tr("따라가다가 화면을 직접 움직이면 따라가기가 풀려요", "Moving the view yourself stops following")}</p>
        </>}
      </div>
    </div>
  );
}

// Banner: while following, 'Following OO's screen · Stop'; if a view-only user had released it, 'Follow OO's screen'
function Bar({ f }) {
  const keep = (e) => e.preventDefault();
  if (f.target) {
    const who = full(f.leader) || f.target.user;
    return (
      <div className="wb-follow-bar" role="status" onMouseDown={keep} style={{ "--fc": f.target.color }}>
        <i aria-hidden="true" />
        <span>{f.target.how === "summon" ? tr(`${who} 님이 화면을 모았어요 · 따라가는 중`, `${who} brought everyone here · following`) : tr(`${who} 님 화면 따라가는 중`, `Following ${who}'s view`)}</span>
        <button type="button" onClick={() => f.stop("button")}>{tr("그만", "Stop")}</button>
      </div>
    );
  }
  if (!f.suggest) return null;
  return (
    <div className="wb-follow-bar wb-follow-pill" onMouseDown={keep}>
      <button type="button" onClick={() => f.follow(f.suggest, "auto")}>{tr(`${full(f.suggest)} 님 화면 따라가기`, `Follow ${full(f.suggest)}'s view`)}</button>
    </div>
  );
}

export default function FollowLayer({ f, me, others }) {
  return <>
    {f.target && <div className="wb-follow-frame" style={{ "--fc": f.target.color }} aria-hidden="true" />}
    <Bar f={f} />
    {f.menu && <PeopleMenu f={f} me={me} others={others} />}
  </>;
}
