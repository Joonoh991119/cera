// Screen language (src/i18n.js): order __TB_LANG__ > saved choice (localStorage "tb-lang") > browser language > English.
// i18n.js remembers its first answer, so every case loads a fresh copy of the module (?case=N).
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";

const realNav = Object.getOwnPropertyDescriptor(globalThis, "navigator");
const realLs = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
const realLoc = Object.getOwnPropertyDescriptor(globalThis, "location");
let n = 0;

function env({ languages, language, storage, reload } = {}) {
  delete globalThis.__TB_LANG__;
  Object.defineProperty(globalThis, "navigator", { value: languages === undefined && language === undefined ? undefined : { languages, language }, configurable: true, writable: true });
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
  Object.defineProperty(globalThis, "location", { value: reload ? { reload } : undefined, configurable: true, writable: true });
}
function memStore(init = {}) {
  const m = new Map(Object.entries(init));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m };
}
const blocked = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("SecurityError"); }, removeItem() { throw new Error("SecurityError"); } };
const fresh = () => import(`../src/i18n.js?case=${++n}`);

afterEach(() => {
  delete globalThis.__TB_LANG__;
  for (const [k, d] of [["navigator", realNav], ["localStorage", realLs], ["location", realLoc]]) {
    if (d) Object.defineProperty(globalThis, k, d); else delete globalThis[k];
  }
});

test("__TB_LANG__ wins over the saved choice and the browser", async () => {
  env({ languages: ["en-US"], storage: memStore({ "tb-lang": "en" }) });
  globalThis.__TB_LANG__ = "ko";
  const m = await fresh();
  assert.equal(m.lang(), "ko");
  assert.equal(m.tr("보드", "Board"), "보드");
  globalThis.__TB_LANG__ = "en";   // read on every call
  assert.equal(m.tr("보드", "Board"), "Board");
  globalThis.__TB_LANG__ = "fr";   // not a supported value: ignored
  assert.equal(m.lang(), "en");    // falls back to the saved choice
});

test("saved choice wins over the browser language", async () => {
  env({ languages: ["ko-KR", "en"], storage: memStore({ "tb-lang": "en" }) });
  assert.equal((await fresh()).lang(), "en");
  env({ languages: ["en-US"], storage: memStore({ "tb-lang": "ko" }) });
  assert.equal((await fresh()).lang(), "ko");
});

test("an unknown saved value is ignored", async () => {
  env({ languages: ["ko"], storage: memStore({ "tb-lang": "de" }) });
  assert.equal((await fresh()).lang(), "ko");
});

test("browser language: the first entry decides, Korean only for ko*", async () => {
  env({ languages: ["ko-KR", "en-US"], storage: memStore() });
  assert.equal((await fresh()).lang(), "ko");
  env({ languages: ["en-GB", "ko-KR"], storage: memStore() });
  assert.equal((await fresh()).lang(), "en");
  env({ languages: ["fr-FR"], storage: memStore() });
  assert.equal((await fresh()).lang(), "en");
  env({ languages: [], language: "ko", storage: memStore() });   // empty languages list: navigator.language
  assert.equal((await fresh()).lang(), "ko");
});

test("no navigator and no storage: English", async () => {
  env({});
  const m = await fresh();
  assert.equal(m.lang(), "en");
  assert.equal(m.excalidrawLang(), "en");
});

test("blocked storage (private window): no throw, browser language decides", async () => {
  env({ languages: ["ko-KR"], storage: blocked });
  const m = await fresh();
  assert.equal(m.lang(), "ko");
  assert.equal(m.excalidrawLang(), "ko-KR");
  let reloads = 0;
  globalThis.location = { reload: () => { reloads += 1; } };
  assert.doesNotThrow(() => m.setLang("en"));
  assert.equal(reloads, 1);
  assert.equal(m.lang(), "en", "the choice lasts for this page even when it can't be saved");
});

test("setLang saves the choice and reloads", async () => {
  const st = memStore();
  let reloads = 0;
  env({ languages: ["en-US"], storage: st, reload: () => { reloads += 1; } });
  const m = await fresh();
  assert.equal(m.lang(), "en");
  m.setLang("ko");
  assert.equal(st.m.get("tb-lang"), "ko");
  assert.equal(reloads, 1);
  assert.equal(m.lang(), "ko");
  assert.equal(m.excalidrawLang(), "ko-KR");
  m.setLang(null);   // forget the choice: back to the browser language on the next load
  assert.equal(st.m.has("tb-lang"), false);
  assert.equal((await fresh()).lang(), "en");
});
