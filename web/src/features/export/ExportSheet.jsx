// Export sheet: scope, background, scale, and [Download PNG, Copy, Share]. Also opens from a view-only link.
// When a chosen value changes, the image is pre-rendered: sharing (navigator.share) and Safari copy must be called at the moment of the click,
// and starting to render after the click may get blocked by the browser.
// The image is built on a single queue (a new value cancels the previous job). If the scene changes while the sheet is open, it is rebuilt.
// On open, focus moves into the sheet and Tab is trapped inside it, so keys do not leak to the board below.
// The scope also offers 'This page' (the current page as one PNG) and 'All pages PDF' (one JPEG per page, dependency-free PDF).
// User-triggered actions (download, copy, share) are pinned: if the scene changes meanwhile, the job is not cancelled and is finished
// with the scene as of the click. The PDF scope is not rebuilt automatically when the scene changes (it takes time proportional to the page count; during a lecture
// it used to restart from scratch at every 1.5 s pause). Changes are included when the user presses the button.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { exportToCanvas, getSceneVersion } from "@excalidraw/excalidraw";
import { ensureSceneFonts } from "../fonts/registry.js";   // make sure all scene fonts are loaded before export (the extra slots 21-24 load only on use)
import { buildExport, BACKGROUNDS, SCALES, defaultBackground, fileName, fmtScale, exportNotices } from "./exporters.js";
import { downloadBlob, canCopyImage, copyImage, canShareFiles, shareBlob } from "./share.js";
import { listPages, currentPage, viewRect } from "../pages/pages.js";
import { buildPagesPdf, PDF_LIMITS } from "../pages/pagesPdf.js";
import { tr } from "../../i18n.js";
// The sheet CSS (export.css) is loaded by ExportSheetLoader at entry

const kb = (n) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
// Scene revision: sum of element versions (including deleted ones) plus the number of loaded files. When it changes, the pre-rendered image is discarded.
const sceneRev = (excal) => `${getSceneVersion(excal.getSceneElementsIncludingDeleted())}.${Object.keys(excal.getFiles() || {}).length}`;
const REBUILD_MS = 400;
const REBUILD_PDF_MS = 1500;   // PDF draws one image per page, so avoid rebuilding too often while others are drawing

export default function ExportSheet({ excal, theme, title, viewOnly, onClose }) {
  const hasSel = useMemo(() => !viewOnly && Object.values(excal.getAppState().selectedElementIds || {}).some(Boolean), [excal, viewOnly]);
  // Page list and current page at open time (page switching happens outside the sheet, so 'This page' stays fixed while it is open)
  const pg = useMemo(() => { const list = listPages(excal.getSceneElements()); return { list, cur: currentPage(list, viewRect(excal.getAppState())) }; }, [excal]);
  const page = pg.cur >= 0 ? pg.list[pg.cur] : null;
  const phone = useMemo(() => window.matchMedia("(pointer: coarse)").matches || window.innerWidth <= 760, []);
  const [scope, setScope] = useState(hasSel ? "selection" : page ? "frame" : "all");
  const [progress, setProgress] = useState("");
  const [background, setBackground] = useState(() => defaultBackground(theme));
  const [scale, setScale] = useState(2);
  const [rev, setRev] = useState(() => sceneRev(excal));
  const [ready, setReady] = useState(null);   // { key, blob, width, height, scale, capped, … }
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [tick, setTick] = useState(0);   // incremented when a pressed job finishes, to re-check the deferred pre-render
  const pinned = useRef(0);               // number of pressed jobs in flight; when nonzero, automatic rebuilds do not cancel them
  const job = useRef({ key: "", promise: null, ctrl: null });
  const queue = useRef(Promise.resolve());
  const alive = useRef(true);
  const boxRef = useRef(null);
  const copyOk = useMemo(canCopyImage, []);
  const shareOk = useMemo(() => canShareFiles(), []);
  const sharePdfOk = useMemo(() => canShareFiles("application/pdf"), []);
  const isPdf = scope === "pdf";
  const bg = isPdf && background === "transparent" ? "white" : background;   // PDF (JPEG) has no transparency
  const opts = `${scope}|${bg}|${isPdf ? "" : scale}|${theme}`;
  const liveKey = () => `${opts}|${sceneRev(excal)}`;   // based on the scene at the moment of the press

  // Image for the current values and scene. If one is already being built, reuse it; otherwise cancel the previous one and queue a new one.
  const start = () => {
    const k = liveKey();
    if (pinned.current && job.current.promise) return job.current.promise;   // pinned jobs run to completion
    if (job.current.key !== k) {
      if (job.current.ctrl) job.current.ctrl.abort();
      const ctrl = new AbortController();
      const promise = queue.current.then(() => {
        if (ctrl.signal.aborted) throw Object.assign(new Error("취소됨"), { code: "cancelled" });
        const deps = { exportToCanvas, ensureSceneFonts };
        if (isPdf) return buildPagesPdf(excal, deps, { background: bg, theme, phone, title: title || "", signal: ctrl.signal, onProgress: (i, n) => { if (alive.current && job.current.key === k) setProgress(tr(`${i + 1}/${n}쪽`, `page ${i + 1}/${n}`)); } });
        return buildExport(excal, deps, { scope, frameId: page && page.id, background: bg, scale, theme, signal: ctrl.signal });
      });
      queue.current = promise.catch(() => {});
      job.current = { key: k, promise, ctrl };
      promise.then((r) => { if (alive.current && job.current.key === k) setReady({ key: k, ...r }); }, () => {});
    }
    return job.current.promise;
  };
  useEffect(() => {
    alive.current = true;
    if (boxRef.current) boxRef.current.focus({ preventScroll: true });   // move focus to the sheet
    return () => { alive.current = false; if (job.current.ctrl) job.current.ctrl.abort(); };
  }, []);
  // If the scene changes while the sheet is open (someone else draws, an image file arrives), rebuild after a short delay
  useEffect(() => {
    let t = null;
    const off = excal.onChange(() => {
      clearTimeout(t);
      t = setTimeout(() => { const r = sceneRev(excal); setRev((old) => (old === r ? old : r)); }, isPdf ? REBUILD_PDF_MS : REBUILD_MS);
    });
    return () => { clearTimeout(t); if (typeof off === "function") off(); };
  }, [excal, isPdf]);
  // Pre-render. PDF only when the chosen values change (scene changes are included when the user presses the button).
  useEffect(() => {
    if (isPdf && job.current.key.startsWith(`${opts}|`)) return undefined;
    setNote(""); setProgress("");
    const t = setTimeout(start, 120);
    return () => clearTimeout(t);
  }, [opts, isPdf ? "" : rev, tick]);   // eslint-disable-line react-hooks/exhaustive-deps
  // Wraps one pressed job: start, pin, then on completion unpin and wake the deferred pre-render
  const pressed = async (fn) => {
    const p = start();
    pinned.current += 1;
    setBusy(true);
    try { await fn(p); }
    catch (err) { fail(err); }
    finally { pinned.current -= 1; if (alive.current) { setBusy(false); setTick((t) => t + 1); } }
  };

  const toast = (message, duration = 2500) => excal.setToast({ message, duration });
  const tell = (first, r, base = 2500) => {
    const extra = exportNotices(r);
    const msg = [first, ...extra].filter(Boolean).join(" · ");
    if (msg) toast(msg, extra.length ? 4000 : base);
  };
  const fail = (err) => {
    if (err && err.code === "cancelled") {   // a pinned job is cancelled only when the sheet closes; if it happens anyway, do not end silently
      if (alive.current) toast(tr("그리는 중이라 그림을 다시 만들어야 해요 — 한 번 더 눌러 주세요", "The board changed, so the image must be rebuilt — press once more"), 4000);
      return;
    }
    toast(err && err.code === "empty" ? tr("저장할 그림이 없어요", "Nothing to save") : tr("그림을 만들지 못했어요", "Couldn't create the image"));
    if (err && err.code !== "empty") console.warn(err);
  };

  // File names: single page '<title>-page3.png', all pages '<title>-board.pdf'
  const nameOf = (r) => (r.scope === "pdf" ? fileName(title, "pdf", "pdf", tr("판서", "board")) : fileName(title, r.scope, "png", r.scope === "frame" ? tr(`장${pg.cur + 1}`, `page${pg.cur + 1}`) : undefined));
  const onDownload = () => pressed(async (p) => { const r = await p; downloadBlob(r.blob, nameOf(r)); tell("", r); onClose(); });
  const onCopy = () => pressed(async (p) => {   // start the clipboard write at the moment of the press (Safari); pressed calls start() synchronously
    const res = await copyImage(p.then((r) => r.blob));
    {
      const r = await p;
      if (res === "copied") { tell(tr("복사했어요 — 붙여 넣으세요", "Copied — paste it anywhere"), r); onClose(); return; }
      if (shareOk) { setNote(tr("이 브라우저는 그림 복사가 안 돼요 — '공유' 나 'PNG 받기' 를 쓰세요", "This browser can't copy images — use 'Share' or 'Download PNG'")); return; }
      downloadBlob(r.blob, nameOf(r)); tell(tr("이 브라우저는 그림 복사가 안 돼서 PNG 로 받았어요", "This browser can't copy images, so it was downloaded as PNG"), r, 3500); onClose();
    }
  });
  const onShare = () => {
    // If an image for the current scene is already built, open the share sheet right away without waiting (while the press is still fresh).
    // For PDF, use the pre-built one even if slightly older than the scene (rebuilding would let the press go stale and sharing would be blocked).
    const now = ready && (ready.key === liveKey() || (isPdf && ready.key.startsWith(`${opts}|`))) ? ready : null;
    return pressed(async (p) => {
      const r = now || await p;
      const res = await shareBlob(r.blob, nameOf(r), title || tr("보드", "Board"));
      if (res === "shared") { tell("", r); onClose(); return; }
      if (res === "blocked") { setNote(tr("그림이 준비됐어요 — '공유' 를 한 번 더 누르세요", "The image is ready — press 'Share' again")); return; }
      if (res === "failed") { downloadBlob(r.blob, nameOf(r)); tell(tr(`공유가 안 돼서 ${isPdf ? "PDF" : "PNG"} 로 받았어요`, `Sharing failed, so it was downloaded as ${isPdf ? "PDF" : "PNG"}`), r, 3500); onClose(); }
    });
  };

  // Esc closes and Tab cycles within the sheet. Keys stop here (React stops at the root, so they never reach Excalidraw's document listener).
  const onKeyDown = (e) => {
    e.stopPropagation();
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
    if (e.key === "Backspace") { e.preventDefault(); return; }   // some browsers (WebKit) navigate back on Backspace
    if (e.key !== "Tab" || !boxRef.current) return;
    const list = [...boxRef.current.querySelectorAll("button:not([disabled])")];
    if (!list.length) return;
    const i = list.indexOf(document.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); list[list.length - 1].focus(); }
    else if (!e.shiftKey && (i === -1 || i === list.length - 1)) { e.preventDefault(); list[0].focus(); }
  };

  // For PDF, keep showing the pre-built file even after the scene changes (it is not rebuilt automatically); if it changed, say so in one line
  const cur = ready && (ready.key === `${opts}|${rev}` || (isPdf && ready.key.startsWith(`${opts}|`))) ? ready : null;
  const stale = isPdf && cur && cur.key !== `${opts}|${rev}`;
  const seg = (on, label, onClick, extra = {}) => (
    <button type="button" className={`wb-seg ${on ? "on" : ""}`} aria-pressed={on} onClick={onClick} {...extra}>{label}</button>
  );
  return (
    <div className="wb-modal wb-export-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }} onKeyDown={onKeyDown}>
      <div className="wb-modal-box wb-export" role="dialog" aria-modal="true" aria-label={tr("그림 저장", "Save image")} tabIndex={-1} ref={boxRef}>
        <h2>{tr("그림 저장", "Save image")}</h2>
        <div className="wb-export-row wb-export-scope" role="group" aria-label={tr("범위", "Scope")}>
          <span>{tr("범위", "Scope")}</span>
          {seg(scope === "selection", tr("선택한 것만", "Selection"), () => setScope("selection"), { disabled: !hasSel, title: hasSel ? undefined : viewOnly ? tr("보기 전용은 보드 전체만 저장해요", "View-only users can save the whole board only") : tr("먼저 저장할 것을 선택하세요", "Select what to save first") })}
          {seg(scope === "frame", tr("이 장", "This page"), () => setScope("frame"), { disabled: !page, title: page ? tr(`장 ${pg.cur + 1}`, `Page ${pg.cur + 1}`) : pg.list.length ? tr("화면에 장이 없어요 — 장으로 넘겨 오세요", "No page in view — flip to a page first") : tr("아직 장이 없어요", "No pages yet") })}
          {seg(scope === "all", tr("보드 전체", "Whole board"), () => setScope("all"))}
          {seg(isPdf, tr("모든 장 PDF", "All pages PDF"), () => setScope("pdf"), { disabled: !pg.list.length, title: pg.list.length ? tr(`${pg.list.length}장을 한 PDF 로`, `${pg.list.length} page(s) in one PDF`) : tr("아직 장이 없어요", "No pages yet") })}
        </div>
        <div className="wb-export-row" role="group" aria-label={tr("배경", "Background")}>
          <span>{tr("배경", "Background")}</span>
          {BACKGROUNDS.map(([v, label]) => <React.Fragment key={v}>{seg(bg === v, label, () => setBackground(v), isPdf && v === "transparent" ? { disabled: true, title: tr("PDF 는 투명 배경이 없어요", "PDF has no transparent background") } : {})}</React.Fragment>)}
        </div>
        {isPdf ? <p className="wb-export-hint">{tr(`장마다 한 쪽 · 긴 변 ${phone ? PDF_LIMITS.longSidePhone : PDF_LIMITS.longSide}px 그림으로 넣어요(한글 글꼴 그대로, 글자 검색은 안 돼요).`, `One PDF page per board page, as an image with a ${phone ? PDF_LIMITS.longSidePhone : PDF_LIMITS.longSide}px long side (fonts look the same; text is not searchable).`)}</p> : (
        <div className="wb-export-row" role="group" aria-label={tr("배율", "Scale")}>
          <span>{tr("배율", "Scale")}</span>
          {SCALES.map((s) => <React.Fragment key={s}>{seg(scale === s, `${s}×`, () => setScale(s))}</React.Fragment>)}
        </div>)}
        {theme === "dark" && background !== "screen" && <p className="wb-export-hint">{tr("어두운 화면에서 밝은 색으로 쓴 글은 이 배경에서 안 보일 수 있어요 — 그러면 '화면 그대로' 를 고르세요.", "Light-colored writing from dark mode may be invisible on this background — pick 'As on screen' instead.")}</p>}
        <p className="wb-export-info" aria-live="polite">
          {cur ? (cur.scope === "pdf" ? tr(`${cur.pages}쪽 · ${kb(cur.blob.size)}${cur.pagesSkipped ? ` · 앞 ${cur.pages}장만` : ""}`, `${cur.pages} page(s) · ${kb(cur.blob.size)}${cur.pagesSkipped ? ` · first ${cur.pages} only` : ""}`) : `${cur.width} × ${cur.height} px · ${kb(cur.blob.size)}${cur.capped ? tr(` · 커서 ${fmtScale(cur.scale)}배로 줄임`, ` · too big, reduced to ${fmtScale(cur.scale)}×`) : ""}`)
            : tr(`${isPdf ? "PDF" : "그림"} 만드는 중…${isPdf && progress ? ` ${progress}` : ""}`, `Creating ${isPdf ? "PDF" : "image"}…${isPdf && progress ? ` ${progress}` : ""}`)}
        </p>
        {!copyOk && !isPdf && <p className="wb-export-hint">{tr("이 브라우저는 그림 복사를 못 해요", "This browser can't copy images")}{shareOk ? tr(" — '공유' 나 'PNG 받기' 를 쓰세요", " — use 'Share' or 'Download PNG'") : tr(" — 'PNG 받기' 를 쓰세요", " — use 'Download PNG'")}.</p>}
        {stale && !busy && <p className="wb-export-hint">{tr("그 뒤에 바뀐 판서는 'PDF 받기' 를 누를 때 넣어요.", "Later changes are included when you press 'Download PDF'.")}</p>}
        {busy && isPdf && <p className="wb-export-hint" aria-live="polite">{tr(`PDF 만드는 중${progress ? ` ${progress}` : "…"} — 누른 순간의 판서로 끝까지 만들어요.`, `Creating PDF${progress ? ` ${progress}` : "…"} — it uses the board as it was when you pressed.`)}</p>}
        {note && <p className="wb-export-note" role="status">{note}</p>}
        <div className="wb-modal-actions wb-export-actions">
          <button type="button" className="wb-btn" onClick={onClose}>{tr("닫기", "Close")}</button>
          {copyOk && !isPdf && <button type="button" className="wb-btn" disabled={busy} onClick={onCopy}>{tr("복사", "Copy")}</button>}
          {(isPdf ? sharePdfOk : shareOk) && <button type="button" className="wb-btn" disabled={busy} onClick={onShare}>{tr("공유", "Share")}</button>}
          <button type="button" className="wb-primary" disabled={busy} onClick={onDownload}>{isPdf ? tr("PDF 받기", "Download PDF") : tr("PNG 받기", "Download PNG")}</button>
        </div>
      </div>
    </div>
  );
}
