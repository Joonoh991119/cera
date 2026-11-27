// Board connection: WebSocket by default. If it cannot connect (at start or mid-session) or drops repeatedly, fall back to HTTP with the same protocol (long polling + POST).
// A batch of drawing changes goes through three stages: sent -> ack (server received it, with event number seq) -> saved (written to disk up to that seq).
// Nothing is discarded before saved arrives: if the server dies before persisting, the batch is resent on reconnect (the server merge gives the same result when it receives the same batch twice).
// When the connection drops, check permission once via /me. On 401 or 403 (invite key rotated, or link or token expired) do not reconnect; call onDenied instead,
// which by default reloads the page so the first screen shows 'invite link needed'. Network errors (no status code) keep reconnecting as before.
import { api } from "./api.js";
import { KEY, getName } from "./key.js";
import { tr } from "./i18n.js";

const CHUNK = 400;          // stay below the server's per-request limit (2000) so no elements are truncated and lost
const WS_FAILS_TO_HTTP = 3; // after this many consecutive failed connections, switch to HTTP (whether or not the first connection ever succeeded)

export const isDenied = (e) => !!e && (e.status === 401 || e.status === 403);

export function connect({ id, client, sync, onEvent, onPresence, onReset, onStatus, onDenied, deps = {} }) {
  const WS = deps.WebSocket || globalThis.WebSocket;
  const net = deps.api || api;
  const loc = deps.location || globalThis.location;
  let ws = null, mode = "ws", alive = true, wsFails = 0, nextId = 1, denied = false;
  const unacked = new Map();   // batch id -> elements (not yet acked)
  const unsaved = new Map();   // batch id -> { els, seq } (acked, awaiting save confirmation)
  let pollCtrl = null, hb = null, lastPresence = null;
  let resetting = null, held = [];
  let people = new Map();   // participants: the server sends either the full list (presence) or only the changes (pdelta, when connected with ?pd=1)
  const fullPresence = (list) => { people = new Map(list.map((p) => [p.client, p])); onPresence(list); };   // events that arrive while the full list is being resynced are buffered and applied afterwards
  sync.saved = sync.saved || 0;

  const status = (s) => onStatus && onStatus(s);
  // whether permission is gone: checked once each time the connection drops. If me is missing (a fake api in tests), no check is made
  const stillAllowed = async (err) => {
    if (denied) return false;
    let gone = false;
    if (isDenied(err)) gone = true;
    if (!gone && typeof net.me === "function") {
      try { await net.me(); } catch (e) { gone = isDenied(e); }
    }
    if (gone && alive) deny();
    return !gone;
  };
  function deny() {
    denied = true; alive = false; clearInterval(hb);
    if (ws) { ws.onclose = null; try { ws.close(); } catch (_) { /* */ } }
    if (pollCtrl) pollCtrl.abort();
    status(tr("초대 링크가 바뀌었거나 끝났어요 — 새 링크를 받으세요", "The invite link changed or expired — get a new link"));
    const go = onDenied || (() => { if (loc && typeof loc.reload === "function") loc.reload(); });
    try { go(); } catch (_) { /* */ }
  }
  const markSaved = (upto) => {
    if (typeof upto !== "number") return;
    sync.saved = Math.max(sync.saved, upto);
    for (const [mid, b] of unsaved) if (b.seq <= sync.saved) unsaved.delete(mid);
  };
  const outstanding = () => [...unacked.entries(), ...[...unsaved.entries()].map(([mid, b]) => [mid, b.els])];
  const applyEvents = (evs) => evs.forEach((ev) => { onEvent(ev.event, ev.data); if (ev.seq) sync.seq = Math.max(sync.seq, ev.seq); });
  const startReset = (msg) => {
    sync.epoch = msg.epoch; sync.seq = msg.seq; held = [];
    // event numbers of a new generation (server restart) are unrelated to the old ones, so batches still awaiting save confirmation are also queued for resending
    for (const [mid, b] of unsaved) unacked.set(mid, b.els);
    unsaved.clear();
    sync.saved = msg.saved || 0;
    resetting = (async () => {
      try { await onReset(msg.elements); }
      finally {
        const q = held; held = []; resetting = null;
        applyEvents(q);
        resendAll();
      }
    })();
  };

  function resendAll() {
    for (const [mid, els] of outstanding()) send(mid, els);
  }
  function send(mid, els) {
    if (mode === "ws") { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ t: "elements", id: mid, elements: els })); }
    else postElements(mid, els);
  }

  // ---------------- WebSocket
  function openWs() {
    if (!alive || mode === "http") return;
    const proto = loc.protocol === "https:" ? "wss" : "ws";
    const q = `client=${encodeURIComponent(client)}&since=${sync.seq}&epoch=${encodeURIComponent(sync.epoch || "")}${KEY ? `&k=${encodeURIComponent(KEY)}` : ""}${getName() ? `&name=${encodeURIComponent(getName())}` : ""}&pd=1`;
    let opened = false;
    try { ws = new WS(`${proto}://${loc.host}/api/whiteboard/boards/${id}/ws?${q}`); } catch (_) { return failed(); }
    const giveUp = setTimeout(() => { if (!opened) { try { ws.close(); } catch (_) { /* */ } } }, 6000);
    ws.onopen = () => {
      opened = true; wsFails = 0; clearTimeout(giveUp); status("");
      resendAll();
      if (lastPresence) ws.send(JSON.stringify({ t: "presence", ...lastPresence }));
      clearInterval(hb); hb = setInterval(() => { if (ws && ws.readyState === 1) ws.send('{"t":"ping"}'); }, 25000);
    };
    ws.onmessage = (m) => {
      let msg; try { msg = JSON.parse(m.data); } catch (_) { return; }
      if (msg.t === "ev") {
        if (resetting) { held.push(msg); return; }
        onEvent(msg.event, msg.data); sync.seq = Math.max(sync.seq, msg.seq);
      } else if (msg.t === "hello") {
        sync.epoch = msg.epoch; applyEvents(msg.events || []); sync.seq = Math.max(sync.seq, msg.seq); markSaved(msg.saved);
      } else if (msg.t === "reset") {
        startReset(msg);
      } else if (msg.t === "presence") {
        sync.pver = msg.pver; fullPresence(msg.data);
      } else if (msg.t === "pdelta") {   // the full list was received first on connect, so apply on top of it (order = server list order: an edited entry stays in place, a new one goes at the end)
        sync.pver = msg.pver;
        (msg.gone || []).forEach((c) => people.delete(c));
        (msg.set || []).forEach((p) => people.set(p.client, p));
        onPresence([...people.values()]);
      } else if (msg.t === "ack") {
        const els = unacked.get(msg.id);
        if (els) { unacked.delete(msg.id); if (msg.seq > sync.saved) unsaved.set(msg.id, { els, seq: msg.seq }); }
      } else if (msg.t === "saved") {
        markSaved(msg.seq);
      }
    };
    ws.onclose = () => {
      clearTimeout(giveUp); clearInterval(hb);
      if (!alive) return;
      failed();
    };
    ws.onerror = () => { /* onclose follows */ };
  }
  function failed() {
    wsFails += 1;
    status(tr("다시 연결 중…", "Reconnecting…"));
    stillAllowed().then((ok) => {
      if (!ok || !alive) return;
      if (wsFails >= WS_FAILS_TO_HTTP) return fallback();   // if a front proxy keeps blocking the upgrade, switch to HTTP without a page reload
      setTimeout(openWs, Math.min(5000, 400 * wsFails));
    });
  }

  // ---------------- HTTP (fallback)
  function fallback() {
    if (mode === "http" || !alive) return;
    mode = "http";
    pollCtrl = new AbortController();
    resendAll();
    (async () => {
      let fails = 0;
      while (alive) {
        try {
          const r = await net.poll(id, client, sync.seq, sync.pver, sync.epoch, pollCtrl.signal, sync.saved);
          if (!alive) break;
          if (r.presence) { sync.pver = r.pver; fullPresence(r.presence); }
          if (r.reset) { startReset({ epoch: r.epoch, seq: r.seq, saved: r.saved }); await resetting; continue; }
          applyEvents(r.events);
          sync.seq = Math.max(sync.seq, r.seq); sync.epoch = r.epoch || sync.epoch;
          markSaved(r.saved);
          status(""); fails = 0;
        } catch (e) {
          if (!alive || e.name === "AbortError") break;
          if (!(await stillAllowed(e))) break;   // 403 or 401: retrying is pointless
          fails += 1; status(tr("다시 연결 중…", "Reconnecting…"));
          await new Promise((res) => setTimeout(res, Math.min(5000, 600 * fails)));
        }
      }
    })();
    hb = setInterval(() => lastPresence && net.presence(id, client, lastPresence).catch(() => {}), 15000);
  }
  function postElements(mid, els) {
    net.push(id, client, els).then((r) => {
      if (!unacked.has(mid)) return;
      unacked.delete(mid);
      const seq = r && typeof r.seq === "number" ? r.seq : Infinity;
      if (seq > sync.saved) unsaved.set(mid, { els, seq });
    }, () => setTimeout(() => alive && unacked.has(mid) && postElements(mid, els), 1500));
  }

  openWs();
  return {
    sendElements(els) {
      for (let i = 0; i < els.length; i += CHUNK) {
        const part = els.slice(i, i + CHUNK), mid = nextId++;
        unacked.set(mid, part);   // if it cannot be sent now, send it on reconnect
        send(mid, part);
      }
    },
    // only the fields that were sent are included (the server keeps the previous value for omitted fields; the viewport range 'view' is sent only when it changes).
    // On reconnect and on the HTTP 15-second heartbeat, send the last accumulated value. summon ('everyone to me') is sent only once and never resent.
    sendPresence(p) {
      const { summon, ...keep } = p;   // eslint-disable-line no-unused-vars
      lastPresence = { ...lastPresence, ...keep };
      // returns whether it was sent: true/false for WebSocket, a Promise<boolean> with the POST result for HTTP (the 'everyone to me' notification)
      if (mode === "ws") { if (ws && ws.readyState === 1) { ws.send(JSON.stringify({ t: "presence", ...p })); return true; } return false; }
      return net.presence(id, client, p).then(() => true, () => false);
    },
    pending: () => unacked.size + unsaved.size,
    denied: () => denied,
    mode: () => (mode === "http" ? "http" : ws && ws.readyState === 1 ? "ws" : "connecting"),
    close() {
      alive = false; clearInterval(hb);
      if (ws) { ws.onclose = null; try { ws.close(); } catch (_) { /* */ } }
      if (pollCtrl) pollCtrl.abort();
    },
  };
}
