// Three line-spacing steps and three alignment options, attached at the top of the font popup. Applied immediately to the selected text (including text being edited).
// With no text selected (layout.none), line spacing is dimmed because Excalidraw has no line-spacing value for 'the next text'.
// Alignment shows the value used for the next new text (currentItemTextAlign) as the pressed state.
// Built only from buttons: clicking an input such as a range while typing leaves the text box empty and it gets deleted.
import React from "react";
import { LINE_HEIGHTS, ALIGNS } from "./registry.js";
import "./fonts.css";
import { tr } from "../../i18n.js";

const ALIGN_ICON = {
  left: "M4 6h16M4 10h10M4 14h16M4 18h10",
  center: "M4 6h16M7 10h10M4 14h16M7 18h10",
  right: "M4 6h16M10 10h10M4 14h16M10 18h10",
};

export default function TextLayout({ layout, onPatch }) {
  return (
    <div className="wb-layout">
      <div className="wb-layout-row" role="group" aria-label={tr("줄 간격", "Line spacing")}>
        <span>{tr("줄 간격", "Line spacing")}</span>
        {LINE_HEIGHTS.map(([v, name]) => (
          <button key={v} type="button" className={`wb-seg ${layout.lineHeight === v ? "on" : ""}`} aria-pressed={layout.lineHeight === v}
            disabled={layout.none} title={layout.none ? tr("글을 고르면 바꿀 수 있어요", "Select text to change it") : undefined} onClick={() => onPatch({ lineHeight: v })}>{name}</button>
        ))}
      </div>
      <div className="wb-layout-row" role="group" aria-label={tr("정렬", "Alignment")}>
        <span>{tr("정렬", "Align")}</span>
        {ALIGNS.map(([v, name]) => (
          <button key={v} type="button" className={`wb-seg ${layout.textAlign === v ? "on" : ""}`} aria-pressed={layout.textAlign === v} title={name} aria-label={name}
            onClick={() => onPatch({ textAlign: v })}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d={ALIGN_ICON[v]} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
        ))}
      </div>
    </div>
  );
}
