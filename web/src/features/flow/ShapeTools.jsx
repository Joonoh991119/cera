// One 'Shapes' button on the tool bar + its popup (rectangle, diamond, ellipse, elbow arrow, straight arrow, line).
// Listing every shape on the bar would overflow the 1280px desktop bar and the two-row phone bar, so they are grouped under one button.
// The popup is rendered outside the bar (in .wb-board): the desktop bar has transform and overflow, so it would be clipped inside.
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CaptureUpdateAction } from "@excalidraw/excalidraw";
import { SHAPES, SHAPE_TYPES, currentShape } from "./connect.js";
import "./flow.css";
import { tr } from "../../i18n.js";

function Icon({ d, size = 26 }) {
  return <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
}

/** Activate a shape tool. For arrows, the style (elbow or straight) is chosen first. Activating via the key (A) keeps the current style */
export function chooseShape(excal, shape, pick) {
  if (!excal || !shape) return;
  if (shape.arrowType) excal.updateScene({ appState: { currentItemArrowType: shape.arrowType }, captureUpdate: CaptureUpdateAction.NEVER });
  pick(shape.type);
}

export default function ShapeTools({ excal, tool, pick, mobile }) {
  const [open, setOpen] = useState(false);
  const [last, setLast] = useState("rectangle");
  const btn = useRef(null);
  const pop = useRef(null);
  const on = SHAPE_TYPES.has(tool);
  const cur = currentShape(tool, excal && excal.getAppState().currentItemArrowType, last);

  useEffect(() => {   // close on an outside click or Esc
    if (!open) return undefined;
    const away = (e) => { if (!(pop.current && pop.current.contains(e.target)) && !(btn.current && btn.current.contains(e.target))) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", esc, true);
    return () => { window.removeEventListener("pointerdown", away, true); window.removeEventListener("keydown", esc, true); };
  }, [open]);

  const choose = (s) => { setLast(s.id); setOpen(false); chooseShape(excal, s, pick); };
  const root = btn.current && btn.current.closest(".wb-board");
  return (
    <>
      <button ref={btn} type="button" className={`wb-tool ${on ? "on" : ""}`} aria-pressed={on} aria-haspopup="menu" aria-expanded={open}
        title={tr("도형 · 화살표 (R D O A L)", "Shapes · arrows (R D O A L)")} onClick={() => setOpen((v) => !v)}>
        <Icon d={cur.icon} /><span>{tr("도형", "Shapes")}</span>
      </button>
      {open && root && createPortal(
        <div ref={pop} className="wb-pop wb-shape-pop" role="menu" aria-label={tr("도형", "Shapes")} onMouseDown={(e) => e.preventDefault()}>
          {SHAPES.map((s) => (
            <button key={s.id} type="button" role="menuitemradio" aria-checked={on && cur.id === s.id} className={`wb-shape-item ${on && cur.id === s.id ? "on" : ""}`}
              title={s.key ? `${s.label} (${s.key})` : s.label} onClick={() => choose(s)}>
              <Icon d={s.icon} size={mobile ? 24 : 26} />
              <span>{mobile && s.short ? s.short : s.label}</span>
              {!mobile && s.key && <kbd>{s.key}</kbd>}
            </button>
          ))}
        </div>, root)}
    </>
  );
}
