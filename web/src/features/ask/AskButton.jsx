// '✦ AI 에게' next to the current selection (above its right end; below when there is no room). Pressing it opens the ask sheet with
// the selected ids. Hidden for view-only links, while dragging / editing text, and while an app window is open (blocked).
import React, { useEffect, useState } from "react";
import { canShowPlus } from "../flow/connect.js";
import { selectionBox, toScreen, askSpot, targetIds } from "./requests.js";
import { screenObstacles, viewSize, afterLayout } from "./dom.js";
import { tr } from "../../i18n.js";
import "./ask.css";

export default function AskButton({ excal, touch, blocked, onOpen }) {
  const [spot, setSpot] = useState(null);
  const h = touch ? 44 : 34, w = touch ? 112 : 96;

  useEffect(() => {
    if (!excal) return undefined;
    let cancel = null;
    const update = () => {
      cancel = null;
      const st = excal.getAppState();
      const box = canShowPlus(st) ? selectionBox(excal.getSceneElements(), st.selectedElementIds) : null;
      const next = box ? askSpot(toScreen(box, st), w, h, screenObstacles([".wb-ask-chip"]), viewSize()) : null;   // the chips are placed first, the button keeps off them
      setSpot((prev) => (prev && next && prev.x === next.x && prev.y === next.y ? prev : next));
    };
    const later = () => { if (!cancel) cancel = afterLayout(update); };
    const offs = [excal.onChange(later), excal.onScrollChange(later)];
    window.addEventListener("resize", later);
    window.addEventListener("wb-ask-layout", later);
    later();
    return () => { offs.forEach((f) => f()); window.removeEventListener("resize", later); window.removeEventListener("wb-ask-layout", later); if (cancel) cancel(); };
  }, [excal, w, h]);

  if (!spot || blocked) return null;
  const open = () => {
    const st = excal.getAppState();
    const { ids, over } = targetIds(excal.getSceneElements(), st.selectedElementIds);
    if (ids.length) onOpen({ ids, over, at: { x: spot.x, y: spot.y, w, h } });
  };
  return (
    <button type="button" className={`wb-ask-btn${touch ? " wb-ask-touch" : ""}`} style={{ left: spot.x, top: spot.y, width: w, height: h }}
      title={tr("고른 것을 AI 에게 맡기기", "Ask the AI about the selection")} aria-haspopup="dialog"
      onPointerDown={(e) => e.preventDefault()} onMouseDown={(e) => e.preventDefault()} onClick={open}>
      {tr("✦ AI 에게", "✦ Ask AI")}
    </button>
  );
}
