// Import sheet: 'Merge into this board / Open as new board / Cancel'. The scene is untouched until a choice is made.
// Merge: upload images first, then the server's /import assigns new ids and places the content at the center of the current view; this view also receives it by broadcast like everyone else's
// (so the server and all views end up with the same drawing). New board: the server creates a board and we navigate to it (the original board is unchanged).
// On open, focus goes to the sheet; Esc closes; keys do not leak to the board (prevents Delete from removing the selected elements and spreading to everyone).
import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { prepareFile, runImport, viewCenter, setPendingToast, freshInBox } from "./importFile.js";
import { tr } from "../../i18n.js";

// After merging: only on the merging user's screen, select the merged elements and fit them into view. Other people's views do not move.
// The server response has no ids (the server file is not changed this time), so among the new elements arriving by broadcast we pick those inside the response bbox.
function canvasOffsets() {
  const top = document.querySelector(".wb-mtop"), dock = document.querySelector(".wb-dock");
  return { top: (top ? top.getBoundingClientRect().bottom : 60) + 8, bottom: dock ? Math.max(0, innerHeight - dock.getBoundingClientRect().top) + 8 : 16, left: 8, right: 8 };
}
function focusMerged(excal, before, res) {
  const want = res.added || 0;
  if (!want) return;
  const t0 = Date.now();
  const tick = () => {
    const fresh = freshInBox(excal.getSceneElements(), before, res.bbox);
    if (fresh.length < want && Date.now() - t0 < 3000) { setTimeout(tick, 50); return; }
    if (!fresh.length) return;
    excal.updateScene({ appState: { selectedElementIds: Object.fromEntries(fresh.filter((e) => !e.containerId).map((e) => [e.id, true])) }, captureUpdate: "NEVER" });
    excal.scrollToContent(fresh, { fitToViewport: true, viewportZoomFactor: 0.9, maxZoom: 1, animate: true, canvasOffsets: canvasOffsets() });
  };
  tick();
}

const KIND = { excalidraw: tr("Excalidraw 그림", "Excalidraw drawing"), library: tr("Excalidraw 라이브러리", "Excalidraw library"), image: tr("그림", "Image"), embedded: tr("장면이 든 그림(Excalidraw)", "Image with an embedded scene (Excalidraw)") };

export default function ImportSheet({ file, rest = [], excal, boardId, onClose }) {
  const [prep, setPrep] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const boxRef = useRef(null);
  const primaryRef = useRef(null);
  const cancelRef = useRef(null);
  const alive = useRef(true);
  const busyRef = useRef(false);
  const client = useRef(`imp-${Math.random().toString(36).slice(2, 10)}`);   // must differ from my connection id so my own view also receives the broadcast
  const toast = (message, duration = 3000) => excal.setToast({ message, duration });

  useEffect(() => {
    alive.current = true;
    setPrep(null); setErr("");   // fresh for every file (so a previous file's prepared data is not reused)
    if (boxRef.current) boxRef.current.focus({ preventScroll: true });
    prepareFile(file).then((p) => { if (alive.current) setPrep(p); }, (e) => { if (alive.current) setErr(e.message || String(e)); });
    return () => { alive.current = false; };
  }, [file]);

  // Initial focus: 'Merge into this board' once ready (Enter merges), or 'Cancel' on error. If the user already moved to another button, leave it.
  useEffect(() => {
    const a = document.activeElement;
    if (a && boxRef.current && a !== boxRef.current && a !== document.body && boxRef.current.contains(a) && !a.disabled) return;
    if (prep && primaryRef.current) primaryRef.current.focus({ preventScroll: true });
    else if (err && cancelRef.current) cancelRef.current.focus({ preventScroll: true });
  }, [prep, err]);

  // Even if focus leaves the sheet (clicking an empty area), keep keys from reaching the board: block in the window capture phase
  useEffect(() => {
    const onKey = (e) => {
      if (boxRef.current && boxRef.current.contains(e.target)) return;   // inside the sheet: handled by onKeyDown below
      e.stopPropagation(); e.stopImmediatePropagation();
      if (e.key === "Escape") { e.preventDefault(); if (!busyRef.current) onClose(); return; }
      if (e.key === "Delete" || e.key === "Backspace" || e.key === " " || e.key.length === 1) e.preventDefault();
      if (e.key === "Tab" && boxRef.current) { e.preventDefault(); boxRef.current.focus({ preventScroll: true }); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const go = async (mode) => {
    if (!prep || busyRef.current) return;
    busyRef.current = true;
    const before = new Set(excal.getSceneElementsIncludingDeleted().map((e) => e.id));
    setBusy(prep.files.length ? tr(`그림 올리는 중 0/${prep.files.length}`, `Uploading images 0/${prep.files.length}`) : tr("넣는 중…", "Adding…"));
    try {
      const res = await runImport(api, {
        prepared: prep, mode, boardId, client: client.current, at: viewCenter(excal.getAppState()),
        onProgress: (i, n) => alive.current && setBusy(tr(`그림 올리는 중 ${i}/${n}`, `Uploading images ${i}/${n}`)),
      });
      const sk = Object.values(res.skipped || {}).reduce((a, b) => a + b, 0) + (prep.dropped || 0);
      const extra = sk ? tr(` (못 읽은 것 ${sk}개는 뺐어요)`, ` (${sk} unreadable item(s) left out)`) : "";
      if (mode === "new") {
        // Moving to a new board closes this board's view (and the remaining queue), so list the names of the remaining files
        const left = rest.length ? tr(` · 남은 ${rest.length}개(${rest.slice(0, 3).join(", ")}${rest.length > 3 ? " …" : ""})는 가져오지 않았어요 — 다시 놓아 주세요`, ` · the other ${rest.length} file(s) (${rest.slice(0, 3).join(", ")}${rest.length > 3 ? " …" : ""}) were not imported — drop them again`) : "";
        const title = (res.meta && res.meta.title) || prep.title;
        // Show the toast after the new board view appears (the Board is redrawn and the current toast would disappear)
        setPendingToast({ message: tr(`새 보드 '${title}' 로 옮겼어요 — 같이 보던 사람은 원래 보드에 있어요${extra}${left}`, `Moved to the new board '${title}' — others are still on the original board${extra}${left}`), duration: left ? 7000 : 5000 });
        onClose();
        location.hash = `#b=${res.board}`;
      } else {
        toast(tr(`이 보드에 합쳤어요 — 요소 ${res.added}개${extra}`, `Merged into this board — ${res.added} element(s)${extra}`));
        onClose();
        focusMerged(excal, before, res);
      }
    } catch (e) {
      busyRef.current = false;
      if (!alive.current) return;
      setBusy("");
      toast(tr(`가져오지 못했어요 — ${e.message || e}`, `Import failed — ${e.message || e}`), 5000);   // scene unchanged (the server did not receive it, or only some images were uploaded)
      onClose();
    }
  };

  const onKeyDown = (e) => {
    e.stopPropagation();
    if (e.key === "Escape") { e.preventDefault(); if (!busyRef.current) onClose(); return; }
    if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); return; }
    if (e.key !== "Tab" || !boxRef.current) return;
    const list = [...boxRef.current.querySelectorAll("button:not([disabled])")];
    if (!list.length) return;
    const i = list.indexOf(document.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); list[list.length - 1].focus(); }
    else if (!e.shiftKey && (i === -1 || i === list.length - 1)) { e.preventDefault(); list[0].focus(); }
  };

  const what = prep ? `${KIND[prep.kind] || tr("파일", "File")}${prep.count == null ? "" : prep.kind === "library" ? tr(` · 항목 ${prep.count}개`, ` · ${prep.count} item(s)`) : tr(` · 요소 ${prep.count}개`, ` · ${prep.count} element(s)`)}${prep.files.length ? tr(` · 그림 ${prep.files.length}장`, ` · ${prep.files.length} image(s)`) : ""}` : "";
  return (
    <div className="wb-modal wb-import-back" onMouseDown={(e) => { if (e.target === e.currentTarget && !busyRef.current) onClose(); }} onKeyDown={onKeyDown}>
      <div className="wb-modal-box wb-import" role="dialog" aria-modal="true" aria-labelledby="wb-import-h" tabIndex={-1} ref={boxRef}>
        <h2 id="wb-import-h">{tr("파일 가져오기", "Import file")}</h2>
        <p className="wb-import-file" title={file && file.name}>{file ? file.name : ""}</p>
        <p className="wb-import-info" aria-live="polite">
          {!err && (busy || (prep ? what : tr("읽는 중…", "Reading…")))}
        </p>
        {err && <p className="wb-import-err" role="alert">{err}</p>}
        {!err && <p className="wb-import-hint">{tr("합치면 지금 보이는 가운데에 놓여요. 판서 장이 든 파일은 기존 그림 오른쪽에 놓여요. 같이 보는 사람 화면에도 바로 보여요.", "Merged content lands in the middle of your view; files with pages go to the right of the existing drawing. Everyone sees it right away.")}</p>}
        {rest.length > 0 && <p className="wb-import-hint">{tr(`다음 차례 ${rest.length}개: `, `Next in queue (${rest.length}): `)}{rest.slice(0, 3).join(", ")}{rest.length > 3 ? " …" : ""}</p>}
        <div className="wb-modal-actions wb-import-actions">
          <button type="button" className="wb-btn" ref={cancelRef} onClick={onClose} disabled={!!busy}>{err ? tr("닫기", "Close") : tr("취소", "Cancel")}</button>
          {!err && <button type="button" className="wb-btn" onClick={() => go("new")} disabled={!prep || !!busy}>{tr("새 보드로 열기", "Open as new board")}</button>}
          {!err && <button type="button" className="wb-primary" ref={primaryRef} onClick={() => go("merge")} disabled={!prep || !!busy}>{tr("이 보드에 합치기", "Merge into this board")}</button>}
        </div>
      </div>
    </div>
  );
}
