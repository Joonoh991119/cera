// Screen language: Korean ("ko") or English ("en").
// Order: the viewer's saved choice (localStorage "tb-lang") > the browser language (navigator.languages) > English.
// Tests and other non-browser code can force it with globalThis.__TB_LANG__ = "ko" | "en".
// Usage, with both texts side by side at the call site:  tr("보드 목록", "Boards")

export const LANGS = ["ko", "en"];
const KEY = "tb-lang";

function stored() {
  try {
    const v = globalThis.localStorage && globalThis.localStorage.getItem(KEY);
    return LANGS.includes(v) ? v : null;
  } catch {
    return null;   // storage blocked (private window, in-app browser)
  }
}

function fromBrowser() {
  const nav = globalThis.navigator;
  const list = (nav && (nav.languages && nav.languages.length ? nav.languages : [nav.language])) || [];
  for (const l of list) {
    if (!l) continue;
    return String(l).toLowerCase().startsWith("ko") ? "ko" : "en";
  }
  return "en";
}

let current = null;

export function lang() {
  if (LANGS.includes(globalThis.__TB_LANG__)) return globalThis.__TB_LANG__;
  if (!current) current = stored() || fromBrowser();
  return current;
}

export function tr(ko, en) {
  return lang() === "ko" ? ko : en;
}

// Excalidraw's own UI language code for the current language.
export function excalidrawLang() {
  return lang() === "ko" ? "ko-KR" : "en";
}

// Save the viewer's choice and reload so every label (including Excalidraw's) switches at once.
export function setLang(l) {
  try {
    if (LANGS.includes(l)) globalThis.localStorage.setItem(KEY, l);
    else globalThis.localStorage.removeItem(KEY);
  } catch { /* storage blocked: the choice lasts for this page only */ }
  current = LANGS.includes(l) ? l : null;
  if (globalThis.location) globalThis.location.reload();
}
