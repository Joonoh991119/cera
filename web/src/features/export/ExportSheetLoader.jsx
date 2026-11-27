// Fetched only when the export sheet is opened (lazy chunk). If the fetch fails, we stop at this boundary so the board does not go blank.
//
// Retry: the browser remembers a failed module URL (module map), so re-running the same import fails immediately; just recreating the lazy object
// is not enough (verified 2026-11-20 on Chromium and WebKit). So we append ?retry=<timestamp> to the failed chunk URL
// and fetch it as a new URL. The URL is taken from the error message (Chromium, Firefox) or resource timing. If it cannot be found (WebKit's error
// message has no URL), we tell the user to reload.
// The sheet CSS lives in the entry CSS (0.5 kB): if lazy CSS fails, Vite remembers that URL as loaded and never fetches it again.
import React from "react";
import "./export.css";
import { tr } from "../../i18n.js";

const CHUNK = /\/ExportSheet-[\w-]+\.js/;
function failedChunkUrl(err) {
  const m = String((err && err.message) || "").match(/https?:\/\/\S+?\.js/);
  if (m && CHUNK.test(m[0])) return m[0].split("?")[0];
  try {
    const hit = performance.getEntriesByType("resource").map((e) => e.name).reverse().find((n) => CHUNK.test(n));
    return hit ? hit.split("?")[0] : null;
  } catch { return null; }
}
const make = (url) => React.lazy(() => (url ? import(/* @vite-ignore */ `${url}?retry=${Date.now()}`) : import("./ExportSheet.jsx")));
let Sheet = make(null);

class Boundary extends React.Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(err) {
    const url = failedChunkUrl(err);
    Sheet = make(url);   // the next press fetches the new URL (the original URL if none was found, which leads to the reload message)
    console.warn("그림 저장 창을 못 불러왔어요", err);
    this.props.onFail(!!url);
  }
  render() { return this.state.failed ? null : this.props.children; }
}

export default function ExportSheetLoader(props) {
  const onFail = (canRetry) => {
    props.excal.setToast({ message: canRetry ? tr("저장 창을 못 불러왔어요 — 연결을 확인하고 다시 눌러 주세요", "Couldn't load the save dialog — check your connection and try again") : tr("저장 창을 못 불러왔어요 — 연결을 확인하고 새로고침해 주세요", "Couldn't load the save dialog — check your connection and reload the page"), duration: 4500 });
    props.onClose();
  };
  return <Boundary onFail={onFail}><React.Suspense fallback={null}><Sheet {...props} /></React.Suspense></Boundary>;
}
