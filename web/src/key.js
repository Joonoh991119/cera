// Invite key: move ?k= from the URL into tab storage and remove it from the URL. It is sent as a header (X-Tb-Key) on every API request and as ?k= on the WebSocket.
// This lets the link alone get people in even in browsers that block cookies (in-app browsers such as KakaoTalk, Safari tracking prevention).
const SLOT = "tb-key";
function grab() {
  let k = "";
  try {
    const u = new URL(location.href);
    k = u.searchParams.get("k") || "";
    if (k) {
      u.searchParams.delete("k");
      history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
    }
  } catch (_) { /* */ }
  const store = [() => sessionStorage, () => localStorage];
  if (k) { store.forEach((s) => { try { s().setItem(SLOT, k); } catch (_) { /* blocked */ } }); return k; }
  for (const s of store) { try { const v = s().getItem(SLOT); if (v) return v; } catch (_) { /* */ } }
  return "";
}
export const KEY = grab();

// name: kept in tab and local storage so it survives blocked cookies, and sent as a header
export function getName() {
  for (const s of [() => sessionStorage, () => localStorage]) { try { const v = s().getItem("tb-name"); if (v) return v; } catch (_) { /* */ } }
  return memName;
}
let memName = "";
export function setName(n) {
  memName = n;
  for (const s of [() => sessionStorage, () => localStorage]) { try { s().setItem("tb-name", n); } catch (_) { /* */ } }
}
