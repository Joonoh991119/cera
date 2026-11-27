// The formula dialog and insert logic are a lazy chunk (to keep the entry small). It is prefetched when idle after the board shows up,
// because on phones the input must get focus at the very moment (synchronously) the user taps 'Formula' for the keyboard to appear, so it must already be loaded by then.
// If loading fails, it is fetched again on the next press (WebKit may remember a failure for the same URL; in that case we tell the user to reload).
import React from "react";
import "./math.css";

let mod = null, loading = null;
export function loadMath() {
  if (mod) return Promise.resolve(mod);
  if (!loading) loading = import("./MathDialog.jsx").then((m) => (mod = m), (e) => { loading = null; throw e; });
  return loading;
}
/** The loaded module (commitMath, mathInitial, boardFormulas), or null if not yet loaded */
export const mathModule = () => mod;
export function preloadMath() {
  const go = () => loadMath().catch(() => { /* retried on the next press */ });
  if ("requestIdleCallback" in window) window.requestIdleCallback(go, { timeout: 3000 }); else setTimeout(go, 300);   // Safari has no requestIdleCallback; fetch early so it is loaded before the first press
}
export default function MathDialog(props) {
  if (!mod) return null;
  const D = mod.default;
  return <D {...props} />;
}
