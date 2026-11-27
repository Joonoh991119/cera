// Four '+' buttons around the selected shape: pressing one creates a next box in that direction plus an attached elbow arrow.
// On a phone this is the main way to connect flowcharts, so the buttons are enlarged to 44px. The computation is in connect.js.
// blocked = an app-side window (phone text sheet, formula, save, menu, popup) is open: then hide the buttons and ignore presses (a synthetic key runs the engine regardless of focus).
import React, { useEffect, useState } from "react";
import { CaptureUpdateAction } from "@excalidraw/excalidraw";
import { selectedNode, plusSpots, toViewport, canShowPlus, flowKeyEvents, isDarwin, pruneBound } from "./connect.js";
import "./flow.css";
import { tr } from "../../i18n.js";

export default function QuickConnect({ excal, touch, blocked }) {
  const [spots, setSpots] = useState(null);
  const size = touch ? 44 : 30;

  useEffect(() => {
    if (!excal) return undefined;
    let raf = 0;
    const update = () => {
      raf = 0;
      const st = excal.getAppState();
      const node = canShowPlus(st) ? selectedNode(excal.getSceneElements(), st.selectedElementIds) : null;
      const next = node ? { id: node.id, list: plusSpots(toViewport(node, st), size) } : null;
      setSpots((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    };
    const later = () => { if (!raf) raf = requestAnimationFrame(update); };
    const offs = [excal.onChange(later), excal.onScrollChange(later)];
    window.addEventListener("resize", later);
    later();
    return () => { offs.forEach((f) => f()); window.removeEventListener("resize", later); cancelAnimationFrame(raf); };
  }, [excal, size]);

  if (!spots || blocked) return null;
  const grow = (dir) => {
    if (blocked) return;
    const before = excal.getSceneElements().length;
    const darwin = isDarwin(navigator.platform);
    for (const [type, init] of flowKeyEvents(dir, darwin)) document.dispatchEvent(new KeyboardEvent(type, init));
    if (excal.getSceneElements().length === before) {   // the engine changed and insertion failed: remove the preview and drop the dangling reference on the original box
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }));
      const all = excal.getSceneElementsIncludingDeleted();
      const fixed = pruneBound(all.find((e) => e.id === spots.id), new Set(all.map((e) => e.id)));
      if (fixed) excal.updateScene({ elements: all.map((e) => (e.id === fixed.id ? fixed : e)), captureUpdate: CaptureUpdateAction.NEVER });
      excal.setToast({ message: tr("이어 만들기를 못 했어요 — 상자를 다시 골라 주세요", "Couldn't add the next box — select the box again"), duration: 3000 });
      return;
    }
    // If the new box would end up behind our tool bar, move the view so it is visible (Excalidraw only checks against its own hidden bar to decide whether it is on screen)
    const st = excal.getAppState(), node = selectedNode(excal.getSceneElements(), st.selectedElementIds);
    const dock = document.querySelector(".wb-dock"), floor = dock ? dock.getBoundingClientRect().top : window.innerHeight;
    if (node) { const v = toViewport(node, st); if (v.y + v.h + size + 16 > floor || v.y - size < 56 || v.x - size < 0 || v.x + v.w + size > window.innerWidth) excal.scrollToContent(node, { animate: true, duration: 300 }); }
  };
  const names = { up: tr("위", "above"), right: tr("오른쪽", "right"), down: tr("아래", "below"), left: tr("왼쪽", "left") };
  return (
    <div className="wb-plus-layer" aria-label={tr("다음 칸 이어 만들기", "Add a connected box")}>
      {spots.list.map((s) => (
        <button key={s.dir} type="button" className={`wb-plus${touch ? " wb-plus-touch" : ""}`} title={tr(`${names[s.dir]}에 다음 칸`, `Next box ${names[s.dir]}`)} aria-label={tr(`${names[s.dir]}에 다음 칸`, `Next box ${names[s.dir]}`)}
          style={{ left: s.x - size / 2, top: s.y - size / 2, width: size, height: size }}
          onPointerDown={(e) => e.preventDefault()} onMouseDown={(e) => e.preventDefault()}
          onClick={() => grow(s.dir)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v12M6 12h12" /></svg>
        </button>
      ))}
    </div>
  );
}
