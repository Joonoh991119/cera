// Page bar [< 2/5 > | + New page]. Floats separately from the tool dock: at 1280px the dock already uses the full screen width.
// Desktop: top center. Phone: flip buttons only, above the dock on the right ('New page' is in the menu). View-only: flip buttons only (top on desktop, bottom center on phone).
import React from "react";
import "./pages.css";
import { tr } from "../../i18n.js";

const Arrow = ({ d }) => <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;

export default function PageBar({ pages, viewOnly, mobile }) {
  const { count, cur, go, add } = pages;
  const showNew = !viewOnly && !mobile;
  if (!count && !showNew) return null;
  const keep = (e) => e.preventDefault();   // pressing a button must not steal focus (drawing shortcuts, text being typed)
  return (
    <div className={`wb-pages ${mobile ? "wb-pages-bottom" : "wb-pages-top"}${viewOnly ? " wb-pages-view" : ""}`} role="toolbar" aria-label={tr("판서 장", "Pages")} onMouseDown={keep}>
      {count > 0 && <>
        <button type="button" className="wb-page-btn" aria-label={tr("앞 장", "Previous page")} title={tr("앞 장 (PageUp · ←)", "Previous page (PageUp · ←)")} disabled={cur === 0} onClick={() => go(-1)}><Arrow d="M15 5l-7 7 7 7" /></button>
        <span className="wb-page-num" aria-live="polite" aria-label={cur >= 0 ? tr(`장 ${cur + 1} / ${count}`, `Page ${cur + 1} / ${count}`) : tr(`장 ${count}개 — 화면에 장 없음`, `${count} page(s) — none in view`)}>{cur >= 0 ? cur + 1 : "–"}<small>/{count}</small></span>
        <button type="button" className="wb-page-btn" aria-label={tr("다음 장", "Next page")} title={tr("다음 장 (PageDown · →)", "Next page (PageDown · →)")} disabled={cur === count - 1} onClick={() => go(1)}><Arrow d="M9 5l7 7-7 7" /></button>
      </>}
      {showNew && <button type="button" className="wb-page-new" title={tr("끝에 새 빈 장(16:9)을 만들고 그리로 가요", "Add a blank 16:9 page at the end and go there")} onClick={add}>{tr("+ 새 장", "+ New page")}</button>}
    </div>
  );
}
