// Pen settings: a single button ('Pen ▾' / 'Highlighter ▾') before the pen thickness cell in the toolbar, plus its popover. The toolbar does not grow.
// The popover is rendered outside the toolbar (in .wb-board), because the desktop toolbar has overflow and would clip it (same as ShapeTools).
import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { HL_COLORS } from "./useInkCorrection.js";
import { LEVELS, LEVEL_LABEL } from "./smooth.js";
import "./ink.css";
import { tr } from "../../i18n.js";

const DARK_FILTER = "invert(93%) hue-rotate(180deg)";   // Same as Board: use the color actually drawn on the dark theme
const norm = (c) => String(c || "").toLowerCase();

function Seg({ on, onClick, children, label }) {
  return <button type="button" className={`wb-seg wb-ink-seg ${on ? "on" : ""}`} aria-pressed={on} aria-label={label} onClick={onClick}>{children}</button>;
}

export default function InkOptions({ x, ink, theme }) {
  const btn = useRef(null);
  const pop = useRef(null);
  const { open, setOpen, mode } = x;

  useEffect(() => {   // Close on an outside press or Esc
    if (!open) return undefined;
    const away = (e) => { if (!(pop.current && pop.current.contains(e.target)) && !(btn.current && btn.current.contains(e.target)) && !e.target.closest?.(".wb-tool-pen")) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); } };   /* If Esc leaks to the board, the pen tool gets deselected */
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", esc, true);
    return () => { window.removeEventListener("pointerdown", away, true); window.removeEventListener("keydown", esc, true); };
  }, [open, setOpen]);

  const root = btn.current && btn.current.closest(".wb-board");
  const filter = theme === "dark" ? DARK_FILTER : undefined;
  return (
    <>
      <button ref={btn} type="button" className={`wb-seg wb-ink-btn ${open ? "on" : ""}`} aria-haspopup="dialog" aria-expanded={open}
        title={tr("펜 설정 — 형광펜 · 매끄럽게 · 멈추면 직선", "Pen settings — marker · smoothing · hold for a straight line")} onClick={() => setOpen((v) => !v)}>
        {mode === "hl" ? tr("형광", "Marker") : tr("펜", "Pen")} ▾
      </button>
      {open && root && createPortal(
        <div ref={pop} className="wb-pop wb-ink-pop" role="dialog" aria-label={tr("펜 설정", "Pen settings")} onMouseDown={(e) => e.preventDefault()}>
          <div className="wb-ink-row">
            <b>{tr("종류", "Type")}</b>
            <div className="wb-ink-segs">
              <Seg on={mode === "pen"} onClick={() => x.setMode("pen")}>{tr("펜", "Pen")}</Seg>
              <Seg on={mode === "hl"} onClick={() => x.setMode("hl")}>{tr("형광", "Marker")}</Seg>
            </div>
          </div>
          {mode === "hl" && (
            <div className="wb-ink-row">
              <b>{tr("형광 색", "Marker color")}</b>
              <div className="wb-ink-segs">
                {HL_COLORS.map(([c, label]) => (
                  <button key={c} type="button" className={`wb-swatch ${norm(ink) === norm(c) ? "on" : ""}`} title={label} aria-label={tr(`형광 ${label}`, `Marker ${label}`)} onClick={() => x.setHlColor(c)}>
                    <i style={{ background: c, filter }} />
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="wb-ink-row">
            <b>{tr("매끄럽게", "Smoothing")}</b>
            <div className="wb-ink-segs">
              {LEVELS.map((lv) => <Seg key={lv} on={x.smooth === lv} label={tr(`매끄럽게 ${LEVEL_LABEL[lv]}`, `Smoothing ${LEVEL_LABEL[lv]}`)} onClick={() => x.setSmooth(lv)}>{LEVEL_LABEL[lv]}</Seg>)}
            </div>
          </div>
          <div className="wb-ink-row">
            <b>{tr("멈추면 직선", "Hold to straighten")}</b>
            <div className="wb-ink-segs">
              <Seg on={x.straight} label={tr("멈추면 직선 켬", "Hold to straighten on")} onClick={() => x.setStraight(true)}>{tr("켬", "On")}</Seg>
              <Seg on={!x.straight} label={tr("멈추면 직선 끔", "Hold to straighten off")} onClick={() => x.setStraight(false)}>{tr("끔", "Off")}</Seg>
            </div>
          </div>
          <p className="wb-ink-note">{tr("선 끝에서 0.5초 멈췄다 떼면 곧은 선이 돼요.", "Pause 0.5 s at the end of a stroke, then lift, to make it straight.")}<span className="wb-ink-shift">{tr(" Shift 를 누르면 15° 단위.", " Hold Shift for 15° steps.")}</span></p>
          {x.pen.detected && <>
            <div className="wb-ink-row">
              <b>{tr("펜만 그리기", "Pen-only drawing")}</b>
              <div className="wb-ink-segs">
                <Seg on={x.pen.on} label={tr("펜만 그리기 켬", "Pen-only drawing on")} onClick={() => x.setPenOnly(true)}>{tr("켬", "On")}</Seg>
                <Seg on={!x.pen.on} label={tr("펜만 그리기 끔", "Pen-only drawing off")} onClick={() => x.setPenOnly(false)}>{tr("끔", "Off")}</Seg>
              </div>
            </div>
            <p className="wb-ink-note">{tr("켜면 손바닥이 닿아도 그려지지 않고, 손가락으로는 화면을 옮겨요.", "When on, your palm won't draw and a finger pans the board.")}</p>
          </>}
        </div>, root)}
      {x.badge && root && createPortal(<div className="wb-ink-badge" style={{ left: x.badge.x, top: x.badge.y }} aria-live="polite">{tr("직선", "Straight")}</div>, root)}
    </>
  );
}
