// Request chips over the board: a dashed box around each live request's targets and a chip with its state
// (waiting · working · proposal ready · failed · interrupted). Drawn as an HTML layer, never as elements, so PNG / PDF exports
// and undo never see them. It never moves the view (a follower's screen stays where the presenter put it).
import React, { useEffect, useMemo, useState } from "react";
import { layoutChips, visibleRequests, ACTION_LABEL, COPY_AFTER_MS } from "./requests.js";
import { screenObstacles, viewSize, afterLayout } from "./dom.js";
import { tr } from "../../i18n.js";
import "./ask.css";

export default function RequestLayer({ excal, list, touch, viewOnly, onAction }) {
  const [frame, setFrame] = useState(0);   // bumped on scroll / zoom / resize so the layout is redone
  const [now, setNow] = useState(() => Date.now());
  const live = useMemo(() => visibleRequests(list), [list]);

  const any = live.length > 0;
  useEffect(() => {
    if (!excal || !any) return undefined;
    let raf = 0, cancel = null;
    const bump = () => setFrame((f) => f + 1);
    // now (follow scrolling closely) and once more after the '+' buttons have moved (they are obstacles)
    const later = () => {
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; bump(); });
      if (cancel) cancel();
      cancel = afterLayout(() => { cancel = null; bump(); });
    };
    const offs = [excal.onScrollChange(later), excal.onChange(later)];
    window.addEventListener("resize", later);
    later();
    return () => { offs.forEach((f) => f()); window.removeEventListener("resize", later); cancelAnimationFrame(raf); if (cancel) cancel(); };
  }, [excal, any]);

  // tick only while an open request may still cross the 20 s mark (the copy button appears then)
  useEffect(() => {
    const t0 = Date.now();
    const pending = live.some((r) => r.status === "open" && t0 - (r.created || t0) < COPY_AFTER_MS + 1000);
    if (!pending) return undefined;
    const t = setInterval(() => {
      const n = Date.now();
      setNow(n);
      if (!live.some((r) => r.status === "open" && n - (r.created || n) < COPY_AFTER_MS + 1000)) clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [live]);

  useEffect(() => { window.dispatchEvent(new Event("wb-ask-layout")); });   // after every layout: the ask button keeps off the chips' new places

  if (!excal || !live.length) return null;
  const st = excal.getAppState();
  const items = layoutChips(live, st, screenObstacles(), viewSize(), Math.max(now, Date.now()), { touch, viewOnly });
  void frame;
  return (
    <div className="wb-ask-layer" aria-live="polite" aria-label={tr("AI 요청", "AI requests")}>
      {items.map(({ rid, rect, chip, order, model, r }) => (
        <React.Fragment key={rid}>
          <div className={`wb-ask-area wb-ask-${model.tone}`} style={{ left: rect.x - 6, top: rect.y - 6, width: rect.w + 12, height: rect.h + 12 }} aria-hidden="true" />
          {chip && (
            <div className={`wb-ask-chip wb-ask-${model.tone}${touch ? " wb-ask-touch" : ""}`} data-rid={rid} style={{ left: chip.x, top: chip.y, width: chip.w, height: chip.h }}
              role="status" onPointerDown={(e) => e.stopPropagation()}>
              <span className="wb-ask-line">
                {order && <b className="wb-ask-order" title={tr(`겹치는 요청 중 ${order}번째`, `Request ${order} in this area`)}>{order}</b>}
                <span className="wb-ask-text" title={model.text}>{model.text}</span>
                {model.actions.map((a) => (
                  <button key={a} type="button" className="wb-ask-act" onClick={() => onAction(r, a)}>{ACTION_LABEL[a]()}</button>
                ))}
              </span>
              {model.sub && <small className="wb-ask-sub" title={model.sub}>{model.sub}</small>}
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}
