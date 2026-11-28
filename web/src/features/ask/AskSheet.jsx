// The ask window: kind chips + a one-line note + send. Desktop: a small window by the button. Phone: a bottom sheet whose note field is
// folded; '+ 메모' unfolds it and focuses the field inside the same tap (iOS only raises the keyboard for a focus made in the touch handler).
// Focus moves in on open, Esc closes, and keys never reach the board below (Delete would erase the selection for everyone).
import React, { useEffect, useRef, useState } from "react";
import { KINDS, NOTE_MAX, kindLabel } from "./requests.js";
import { tr } from "../../i18n.js";
import "./ask.css";

export default function AskSheet({ mobile, touch, at, count, over, onSend, onClose }) {
  const [kind, setKind] = useState("auto");
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(!mobile);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const first = useRef(null), noteRef = useRef(null), box = useRef(null);

  useEffect(() => { (mobile ? first.current : noteRef.current || first.current)?.focus({ preventScroll: true }); }, [mobile]);
  // keys pressed while focus is outside the window (clicked on its backdrop, or focus lost) never reach the board: Esc closes, Tab comes back in
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    const guard = (e) => {
      if (box.current && box.current.contains(e.target)) return;   // inside: onKey below handles it
      e.stopPropagation(); e.preventDefault();
      if (e.key === "Escape") closeRef.current();
      else if (e.key === "Tab") first.current && first.current.focus();
    };
    window.addEventListener("keydown", guard, true);
    return () => window.removeEventListener("keydown", guard, true);
  }, []);

  const send = async () => {
    if (busy) return;
    if (note.trim().length > NOTE_MAX) { setErr(tr(`메모는 ${NOTE_MAX}자까지예요`, `The note can be up to ${NOTE_MAX} characters`)); return; }
    setBusy(true); setErr("");
    try { await onSend({ kind, note: note.trim() }); }
    catch (e) { setErr(e.message || String(e)); setBusy(false); }
  };
  const onKey = (e) => {
    e.stopPropagation();   // keep every key away from the board
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.target === noteRef.current) { e.preventDefault(); send(); }
    if (e.key === "Tab" && box.current) {   // keep Tab inside the window
      const f = [...box.current.querySelectorAll("button, textarea")].filter((x) => !x.disabled && x.tabIndex >= 0);
      if (!f.length) return;
      const i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
    }
  };
  const unfold = () => { setNoteOpen(true); noteRef.current && noteRef.current.focus(); };   // synchronous focus inside the tap

  const style = mobile || !at ? undefined : {
    left: Math.max(8, Math.min(window.innerWidth - 328, at.x + at.w - 320)),
    top: at.y + at.h + 8 + 250 < window.innerHeight ? at.y + at.h + 8 : Math.max(8, at.y - 258),
  };
  return (
    <div className="wb-ask-back" onPointerDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={box} className={`wb-ask-sheet${mobile ? " wb-ask-bottom" : ""}${touch ? " wb-ask-touch" : ""}`} style={style} role="dialog" aria-modal="true" tabIndex={-1}
        aria-label={tr("AI 에게 맡기기", "Ask the AI")} onKeyDown={onKey}
        onMouseDown={(e) => { if (!e.target.closest("button, textarea")) { e.preventDefault(); box.current.focus({ preventScroll: true }); } }}>
        <p className="wb-ask-head">{tr(`고른 것 ${count}개를 AI 에게`, `Ask the AI about ${count} selected item${count === 1 ? "" : "s"}`)}
          {over && <small>{tr(" (앞 500개만)", " (first 500 only)")}</small>}</p>
        <div className="wb-ask-kinds" role="radiogroup" aria-label={tr("무엇으로", "As what")}>
          {KINDS.map((k, i) => (
            <button key={k} ref={i === 0 ? first : undefined} type="button" role="radio" aria-checked={kind === k} className={`wb-ask-kind${kind === k ? " on" : ""}`}
              onClick={() => setKind(k)}>{kindLabel(k)}</button>
          ))}
        </div>
        {mobile && !noteOpen && <button type="button" className="wb-ask-unfold" onClick={unfold}>{tr("+ 메모", "+ Note")}</button>}
        <textarea ref={noteRef} className={`wb-ask-note${noteOpen ? "" : " folded"}`} rows={1} maxLength={NOTE_MAX} value={note} tabIndex={noteOpen ? 0 : -1}
          aria-hidden={!noteOpen} placeholder={tr("한 줄 메모(선택) — 예: 흐름도로 정리", "One-line note (optional) — e.g. make it a flowchart")} aria-label={tr("메모", "Note")}
          onChange={(e) => setNote(e.target.value)} onFocus={() => setNoteOpen(true)} />
        {err && <p className="wb-ask-err" role="alert">{err}</p>}
        <div className="wb-ask-actions">
          <button type="button" className="wb-btn" onClick={onClose}>{tr("취소", "Cancel")}</button>
          <button type="button" className="wb-primary" disabled={busy} onClick={send}>{busy ? tr("보내는 중…", "Sending…") : tr("보내기", "Send")}</button>
        </div>
      </div>
    </div>
  );
}
