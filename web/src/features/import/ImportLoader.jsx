// Fetched only when the import sheet is opened (lazy chunk). If the fetch fails, we stop at this boundary so the board does not go blank, and show a toast.
// The browser remembers a failed chunk URL, so we append ?retry=<timestamp> and fetch a new URL (same approach as ExportSheetLoader).
// The sheet CSS lives in the entry CSS (if lazy CSS fails, Vite remembers it as loaded and never fetches it again).
import React from "react";
import "./import.css";
import { tr } from "../../i18n.js";

const CHUNK = /\/ImportSheet-[\w-]+\.js/;
function failedChunkUrl(err) {
  const m = String((err && err.message) || "").match(/https?:\/\/\S+?\.js/);
  if (m && CHUNK.test(m[0])) return m[0].split("?")[0];
  try {
    const hit = performance.getEntriesByType("resource").map((e) => e.name).reverse().find((n) => CHUNK.test(n));
    return hit ? hit.split("?")[0] : null;
  } catch { return null; }
}
const make = (url) => React.lazy(() => (url ? import(/* @vite-ignore */ `${url}?retry=${Date.now()}`) : import("./ImportSheet.jsx")));
let Sheet = make(null);

class Boundary extends React.Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(err) {
    const url = failedChunkUrl(err);
    Sheet = make(url);
    console.warn("가져오기 창을 못 불러왔어요", err);
    this.props.onFail(!!url);
  }
  render() { return this.state.failed ? null : this.props.children; }
}

export default function ImportLoader(props) {
  const onFail = (canRetry) => {
    props.excal.setToast({ message: canRetry ? tr("가져오기 창을 못 불러왔어요 — 연결을 확인하고 다시 해 주세요", "Couldn't load the import dialog — check your connection and try again") : tr("가져오기 창을 못 불러왔어요 — 새로고침해 주세요", "Couldn't load the import dialog — reload the page"), duration: 4500 });
    props.onClose();
  };
  return <Boundary onFail={onFail}><React.Suspense fallback={null}><Sheet {...props} /></React.Suspense></Boundary>;
}
