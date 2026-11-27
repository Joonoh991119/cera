// Text (mermaid) -> flowchart: opens the mermaid dialog (TTDDialog) already built into Excalidraw 0.18.
// Excalidraw renders TTDDialog itself via __fallback, so there is no AI (text-to-diagram) tab, only mermaid (no external API).
// The converter @excalidraw/mermaid-to-excalidraw (MIT) is downloaded when the dialog first opens (lazy chunk). The inserted result is editable shapes and arrows.
import { CaptureUpdateAction, convertToExcalidrawElements, FONT_FAMILY } from "@excalidraw/excalidraw";
import { restyleMermaid } from "./mermaidStyle.js";
import { tr, lang } from "../../i18n.js";

const LS_KEY = "mermaid-to-excalidraw";   // Excalidraw EDITOR_LS_KEYS.MERMAID_TO_EXCALIDRAW: the value is JSON text
export const MERMAID_EXAMPLE = "flowchart TD\n  A[입력] --> B{판단}\n  B -->|예| C[출력]\n  B -->|아니오| A";

/**
 * Download the converter chunk first, then open the dialog. If the import fails, the Excalidraw dialog just hangs on 'loading',
 * so if the download fails (e.g. an old tab looking for a chunk deleted by a new deploy) we don't open the dialog and notify the user instead. It is the same module, so it is not downloaded twice.
 */
export function openMermaid(excal) {
  if (!excal) return;
  try { if (lang() === "ko" && !localStorage.getItem(LS_KEY)) localStorage.setItem(LS_KEY, JSON.stringify(MERMAID_EXAMPLE)); } catch (_) { /* storage blocked: the English example shows instead (also in English mode: Excalidraw's own example) */ }
  import("@excalidraw/mermaid-to-excalidraw").then(() => {
    watchInsert(excal);
    excal.updateScene({ appState: { openDialog: { name: "ttd", tab: "mermaid" } }, captureUpdate: CaptureUpdateAction.NEVER });
  }, () => excal.setToast({ message: tr("흐름도 변환기를 못 불러왔어요 — 페이지를 새로고침한 뒤 다시 열어 주세요", "Couldn't load the flowchart converter — reload the page and open it again"), duration: 5000 }));
}

// When the dialog closes, tidy the newly created elements (= the inserted flowchart): straight lines and the app body font (sans-serif). Others receive them as ordinary changed elements.
let unwatch = null;
function watchInsert(excal) {
  if (unwatch) { unwatch(); unwatch = null; }
  const before = new Set(excal.getSceneElementsIncludingDeleted().map((e) => e.id));
  let opened = false;
  const off = excal.onChange((elements, st) => {
    if (st.openDialog && st.openDialog.name === "ttd") { opened = true; return; }
    if (!opened) return;
    off(); if (unwatch === off) unwatch = null;
    const fresh = elements.filter((e) => !before.has(e.id) && !e.isDeleted).map((e) => e.id);
    if (!fresh.length) return;
    setTimeout(() => {   // don't change the scene inside onChange
      const next = restyleMermaid(excal.getSceneElementsIncludingDeleted(), fresh, FONT_FAMILY.Nunito, convertToExcalidrawElements);
      if (next) excal.updateScene({ elements: next, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    }, 0);
  });
  unwatch = off;
}
