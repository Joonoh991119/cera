// Intercepts canvas drops. When an .excalidraw file is dropped, Excalidraw replaces the local scene wholesale without confirmation,
// leaving old elements on the server so the local view diverges from everyone else's (UIOptions loadScene:false does not prevent it either).
// The same holds for PNG/SVG with an embedded scene (excalidraw.com 'embed scene' export, Obsidian *.excalidraw.png/svg).
// We block it **synchronously** in the window capture phase (Excalidraw's onDrop runs in the bubble phase at the React root, so it would never get there):
//   - non-image files: queued for the sheet (one at a time if there are several)
//   - PNG/SVG: read them; if an embedded scene marker exists, queue for the sheet; otherwise re-dispatch the drop to Excalidraw with the same file (a plain image is handled as before)
//   - only JPEG, GIF, WebP: do not block (Excalidraw inserts them directly)
// Drags without files (library items, text) are left alone. A view-only screen blocks all file drops and shows a notice.
import { useEffect, useRef } from "react";
import { classifyDrop, hasEmbeddedScene } from "./importFile.js";

const ours = new WeakSet();   // drops we re-dispatched ourselves: these go straight to Excalidraw

async function embedsScene(f) {
  try { return hasEmbeddedScene(new Uint8Array(await f.arrayBuffer())); } catch { return false; }
}

function redispatch(target, files, x, y) {
  const dt = new DataTransfer();
  files.forEach((f) => dt.items.add(f));
  const t = target && target.isConnected ? target : (document.querySelector(".excalidraw canvas.interactive") || document.querySelector(".excalidraw canvas"));
  const ev = new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: y });
  ours.add(ev);
  t.dispatchEvent(ev);
}

export default function useImportDrop({ enabled, viewOnly, onFiles, onDenied }) {
  const cb = useRef({ onFiles, onDenied });
  cb.current = { onFiles, onDenied };
  useEffect(() => {
    if (!enabled) return undefined;
    const hasFiles = (e) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
    const over = (e) => { if (hasFiles(e)) e.preventDefault(); };   // so the browser does not open the file when it is dropped outside the canvas (toolbar etc.)
    const drop = (e) => {
      if (ours.has(e)) return;
      const files = e.dataTransfer ? Array.from(e.dataTransfer.files || []) : [];
      if (!files.length) return;                                  // library item or text drag: leave it alone
      const { docs, maybe, plain } = classifyDrop(files);
      if (!viewOnly && !docs.length && !maybe.length) return;     // only JPEG, GIF, WebP: Excalidraw inserts them
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      if (viewOnly) { cb.current.onDenied && cb.current.onDenied(); return; }
      const target = e.target, x = e.clientX, y = e.clientY;
      (async () => {
        const scene = [], pics = [...plain];
        for (const f of maybe) (await embedsScene(f) ? scene : pics).push(f);
        if (pics.length) {
          try { redispatch(target, pics, x, y); }
          catch { scene.push(...pics); }                          // browser cannot create a DataTransfer: send to the sheet (merge)
        }
        const queue = files.filter((f) => docs.includes(f) || scene.includes(f));   // in the order dropped
        if (queue.length) cb.current.onFiles(queue);
      })();
    };
    window.addEventListener("dragover", over, true);
    window.addEventListener("drop", drop, true);
    return () => { window.removeEventListener("dragover", over, true); window.removeEventListener("drop", drop, true); };
  }, [enabled, viewOnly]);
}
