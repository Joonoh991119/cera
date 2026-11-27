// Font picker popup, grouped by genre (body, handwriting, title, special). On phones (narrow screens) it is laid out in two columns.
// Preview text loads that font's pieces, so it is drawn only while the popup is open (a closed popup loads no new fonts).
// children (line spacing, alignment) attach at the top of the popup, grouped in one popup so the toolbar gets no extra buttons.
import React, { useEffect } from "react";
import { FONTS, GENRES, ensureExtraFaces } from "./registry.js";
import "./fonts.css";
import { tr } from "../../i18n.js";

export default function FontPicker({ font, sheet, onPick, keep, children }) {
  useEffect(() => { ensureExtraFaces(); }, []);   // register the added slots' font faces the first time the popup opens
  const list = FONTS.filter((f) => !sheet || f.family);   // in the phone text-entry sheet, exclude math (TeX)
  return (
    <div className={`wb-pop wb-font-pop${sheet ? " wb-pop-top" : ""}`} role="dialog" aria-label={tr("글꼴 · 글 모양", "Font · text style")} onMouseDown={keep}>
      {children}
      <div className="wb-font-list" role="listbox" aria-label={tr("글꼴", "Font")}>
      {GENRES.map(([g, name]) => {
        const items = list.filter((f) => f.genre === g);
        if (!items.length) return null;
        return (
          <div key={g} className="wb-font-genre" role="group" aria-label={name}>
            <p className="wb-font-gname">{name}</p>
            <div className="wb-font-glist">
              {items.map((f) => (
                <button key={f.id} type="button" role="option" aria-selected={font === f.id} className={`wb-font-item ${font === f.id ? "on" : ""}`}
                  title={f.hint} onClick={() => onPick(f.id)}>
                  <span className="wb-font-name" style={{ fontFamily: `"${f.css}", serif`, fontStyle: f.id === "math" ? "italic" : undefined }}>{f.id === "math" ? "x² + y²" : tr(`${f.label} 가나다 Abc`, `${f.label} Abc 가나다`)}</span>
                  <small>{f.hint}</small>
                </button>
              ))}
            </div>
          </div>
        );
      })}
      </div>
    </div>
  );
}
