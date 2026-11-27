// Dialog for inserting and editing formulas (moved out of Board.jsx). Symbol palette, recent formulas, and this board's formulas. A lazy chunk (fetched by MathLoader).
// Palette buttons don't steal focus from the input when pressed (mousedown is prevented), so the keyboard doesn't close on phones.
// Phone: the dialog is attached to the bottom of the screen and fitted to the visible area (visualViewport, the height without the keyboard). The symbol row sits right below the input.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { texToSvg, svgDataURL } from "../../math.js";
import { mathKey, canInsert } from "../../mathgate.js";
import { SYMBOLS, TEMPLATES, EXAMPLES, insertSnippet, nextSlot, loadRecent, saveRecent, pushRecent } from "./palette.js";
// The dialog CSS (math.css) is loaded by MathLoader at entry (if lazy CSS fails, Vite does not fetch it again)
export { commitMath, mathInitial } from "./insert.js";   // shipped in this lazy chunk as well
import { isHex } from "./insert.js";
import { tr } from "../../i18n.js";
export { boardFormulas } from "./palette.js";

const storage = (() => { try { return window.localStorage; } catch (_) { return null; } })();
const noSteal = (e) => e.preventDefault();   // pressing keeps focus on the input (keeps the keyboard up)
const PLACEHOLDER = tr("예) x_{t+1} = A\\,x_t + b", "e.g. x_{t+1} = A\\,x_t + b");   // faint example, never used as a value

function Chips({ label, list, onPick }) {
  if (!list.length) return null;
  return (
    <div className="wb-math-chips" role="group" aria-label={label}>
      <span className="wb-math-chips-h">{label}</span>
      <div className="wb-math-scroll">
        {list.map((x) => <button key={x} type="button" tabIndex={-1} title={x} onMouseDown={noSteal} onClick={() => onPick(x)}>{x}</button>)}
      </div>
    </div>
  );
}

// mobile = narrow screen (layout, sheet), touch = finger device (Board gives focus at the moment of the press; otherwise autoFocus)
export default function MathDialog({ initial, ink, board, onCancel, onInsert, mobile, touch }) {
  const [tex, setTex] = useState(initial?.latex || "");   // a new dialog opens empty; the example only appears as the placeholder
  const [size, setSize] = useState(initial?.size || 32);
  const [color, setColor] = useState(isHex(initial?.color) ? initial.color : ink);
  const [prev, setPrev] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  const [saving, setSaving] = useState(false);   // applying (waiting for the file upload): so it can't be pressed twice
  const recent = useMemo(() => (storage ? loadRecent(storage) : []), []);
  const ta = useRef(null);
  const back = useRef(null);
  const key = mathKey(tex, size, color);

  useEffect(() => {
    let alive = true;
    if (!tex.trim()) { setPrev(null); setErr(""); setBusy(false); return undefined; }   // empty field: don't typeset (insert is disabled)
    setBusy(true);
    const t = setTimeout(async () => {
      try {
        const r = await texToSvg(tex, { size, color });
        if (!alive) return;
        setPrev({ ...r, key }); setErr(r.error || "");
      } catch (e) { if (alive) { setPrev(null); setErr(String(e.message || e)); } }
      if (alive) setBusy(false);
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [tex, size, color, key]);

  // Phone: when the keyboard comes up, fit the dialog to the visible area (on iOS fixed elements go behind the keyboard)
  useEffect(() => {
    const vv = window.visualViewport, el = back.current;
    if (!mobile || !vv || !el) return undefined;
    const fit = () => { el.style.setProperty("--vvh", `${vv.height}px`); el.style.setProperty("--vvt", `${vv.offsetTop}px`); };
    fit();
    vv.addEventListener("resize", fit); vv.addEventListener("scroll", fit);
    return () => { vv.removeEventListener("resize", fit); vv.removeEventListener("scroll", fit); };
  }, [mobile]);

  const ready = canInsert(prev, tex, size, color, busy, err) && !saving;
  const doInsert = async () => {
    if (!ready) return;
    setSaving(true);
    const ok = await onInsert(tex, size, color, prev);
    if (ok === false) { setSaving(false); return; }   // failed: the dialog stays open (Board shows a message)
    if (storage) saveRecent(storage, pushRecent(loadRecent(storage), tex));   // recent formulas (this browser)
  };

  const setSel = (text, s, e) => {
    setTex(text);
    requestAnimationFrame(() => { const t = ta.current; if (!t) return; if (document.activeElement !== t) t.focus({ preventScroll: true }); t.setSelectionRange(s, e); });
  };
  const put = (item) => {
    const t = ta.current;
    const r = insertSnippet(tex, t ? t.selectionStart : tex.length, t ? t.selectionEnd : tex.length, item);
    setSel(r.text, r.start, r.end);
  };
  const onKey = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doInsert(); return; }
    if (e.key === "Tab" && !e.shiftKey && !e.altKey) {   // to the next □ (default Tab behavior if there is none)
      const t = e.currentTarget, i = nextSlot(t.value, t.selectionEnd);
      if (i >= 0) { e.preventDefault(); t.setSelectionRange(i, i + 1); }
    }
  };
  const showExamples = !board.length;

  return (
    <div ref={back} className={`wb-modal wb-math-modal${mobile ? " wb-math-phone" : ""}`} role="dialog" aria-label={initial ? tr("수식 고치기", "Edit formula") : tr("수식 넣기", "Insert formula")}
      onKeyDown={(e) => { if (e.key === "Escape") onCancel(); e.stopPropagation(); }}>
      <div className="wb-modal-box wb-math">
        <h2>{initial ? tr("수식 고치기", "Edit formula") : tr("수식 넣기", "Insert formula")}</h2>
        <p className="wb-muted wb-math-help">{tr("LaTeX 로 쓰면 바로 조판해 보여 줘요. 아래 기호를 누르면 □ 칸이 생기고, Tab 으로 다음 칸에 가요.", "Type LaTeX and see it typeset live. The symbols below add □ slots; Tab jumps to the next one.")}</p>
        <textarea ref={ta} className="wb-math-input" autoFocus={!touch} rows={3} value={tex} onChange={(e) => setTex(e.target.value)} aria-label="LaTeX" spellCheck={false}
          placeholder={PLACEHOLDER}
          autoCapitalize="off" autoCorrect="off" autoComplete="off" onKeyDown={onKey} />
        <div className="wb-math-palette wb-math-scroll" role="toolbar" aria-label={tr("기호", "Symbols")}>
          {TEMPLATES.map((x) => <button key={x.tex} type="button" tabIndex={-1} className="wb-math-tpl" title={x.title} aria-label={x.title} onMouseDown={noSteal} onClick={() => put(x)}>{x.label}</button>)}
          {SYMBOLS.map((x) => <button key={x.tex} type="button" tabIndex={-1} title={x.title} aria-label={x.title} onMouseDown={noSteal} onClick={() => put(x)}>{x.label}</button>)}
        </div>
        <div className="wb-preview" aria-live="polite">
          {!tex.trim() ? <span className="wb-muted">{tr("식을 쓰거나 위 기호를 누르세요", "Type a formula or press a symbol above")}</span> : busy && !prev ? <span className="wb-muted">{tr("조판 중…", "Typesetting…")}</span> : prev && <img alt={tr("수식 미리보기", "Formula preview")} className={prev.key === key ? "" : "wb-stale"} src={svgDataURL(prev.svg)} />}
        </div>
        {err && <p className="wb-err">{err}</p>}
        <div className="wb-math-row">
          <label className="wb-slider">{tr("크기", "Size")} <input type="range" min="18" max="80" value={size} onChange={(e) => setSize(+e.target.value)} /> <span>{size}</span></label>
          {initial && color.toLowerCase() !== String(ink).toLowerCase() && (
            <button type="button" className="wb-btn wb-math-ink" onMouseDown={noSteal} onClick={() => setColor(ink)} title={tr("지금 고른 펜 색으로 바꿔요", "Change to the current pen color")}>
              <i style={{ background: color }} />→<i style={{ background: ink }} /> {tr("펜 색으로", "Pen color")}
            </button>
          )}
        </div>
        <Chips label={tr("최근", "Recent")} list={recent} onPick={(x) => setSel(x, x.length, x.length)} />
        <Chips label={tr("이 보드", "This board")} list={board} onPick={(x) => setSel(x, x.length, x.length)} />
        {showExamples && <Chips label={tr("예시", "Examples")} list={EXAMPLES} onPick={(x) => setSel(x, x.length, x.length)} />}
        <div className="wb-modal-actions">
          <button type="button" className="wb-btn" onClick={onCancel}>{tr("취소", "Cancel")}</button>
          <button type="button" className="wb-primary" disabled={!ready} onClick={doInsert}>{saving ? tr("넣는 중…", "Inserting…") : initial ? tr("바꾸기", "Update") : tr("넣기", "Insert")}{mobile || saving ? "" : " (⌘/Ctrl+Enter)"}</button>
        </div>
      </div>
    </div>
  );
}
