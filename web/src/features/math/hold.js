// Receiving side: briefly holds back just that one element until the new image file for a formula edited by someone else arrives.
// While held, the old formula stays on screen; after the file is received and the image decoded, it is swapped in at once (no gray placeholder flicker).
// If the file is not on the server yet (still uploading), wait for the 'file' event. After MAX_HOLD_MS it is released anyway (so it shows up, however late).
// On release, release(elements) feeds back into Board's onElements and gets reconciled (the version rules keep the order).

const MAX_HOLD_MS = 4000;

/**
  * Receiving side: drawing a formula edited by someone else (same id, new fileId) before its file arrives makes a gray placeholder flicker.
  * Only 'swaps' whose file is missing are held briefly. Newly created images, deleted ones, and ones whose file is already present are passed through as is.
  * local = id -> current scene element, have = fileId -> present
 */
export function splitImageSwaps(incoming, local, have) {
  const now = [], held = [];
  for (const e of incoming) {
    const l = e && e.type === "image" && e.fileId && !e.isDeleted && !have(e.fileId) ? local.get(e.id) : null;
    if (l && !l.isDeleted && l.type === "image" && l.fileId && l.fileId !== e.fileId && have(l.fileId) && e.version > l.version) held.push(e);
    else now.push(e);
  }
  return { now, held };
}

const decode = (dataURL) => new Promise((res) => {
  try {
    const img = new Image();
    img.onload = img.onerror = () => res();
    img.src = dataURL;
    if (img.decode) img.decode().then(res, res);
  } catch (_) { res(); }
});

/**
  * If this side (B) moved or resized a held formula in the meantime: B's version (old fileId) would beat A's edit by version and A's edit would vanish.
  * So we don't send B's version. Instead we build a new version by putting A's fileId and customData on top of B's current appearance (position, size, groups, ...) and setting version
  * above both (rebaseHeld). That version wins on the server, so both A's edit and B's move survive.
 */
export function rebaseHeld(cur, held, base = null, rand = Math.random) {
  // Size: apply only the scale factor this side enlarged by to the edit's new size (if this side only moved it, the edit's size is used as is)
  const sx = base && base.width > 0 ? cur.width / base.width : 1, sy = base && base.height > 0 ? cur.height / base.height : 1;
  return { ...cur, fileId: held.fileId, customData: held.customData, status: "saved", width: held.width * sx, height: held.height * sy,
    version: Math.max(cur.version, held.version) + 1, versionNonce: Math.floor(rand() * 2 ** 31) };
}

export function createSwapHold({ excal, getFile, release, rebased }) {
  const waiting = new Map();   // fileId -> { els: Map(id -> element), timer, busy }
  const bases = new Map();     // the version in this side's scene at hold time (id -> element), to measure how much this side scaled it in the meantime
  const have = (fid) => !!(excal.getFiles() || {})[fid];

  const flush = (fid) => {
    const w = waiting.get(fid); if (!w) return;
    waiting.delete(fid); clearTimeout(w.timer);
    for (const id of w.els.keys()) bases.delete(id);
    release([...w.els.values()]);
    const f = (excal.getFiles() || {})[fid];
    if (f) excal.addFiles([f]);   // fill the image cache right after the element changes (otherwise Excalidraw fills it up to 0.5s later)
  };
  const fetch = async (fid) => {
    const w = waiting.get(fid); if (!w || w.busy) return;
    w.busy = true;
    let f = null;
    try { f = have(fid) ? excal.getFiles()[fid] : await getFile(fid); } catch (_) { f = null; }
    if (!waiting.has(fid)) return;
    w.busy = false;
    if (!f) return;   // not on the server yet: wait for the 'file' event or the timeout
    if (!have(fid)) excal.addFiles([f]);
    await decode(f.dataURL);
    flush(fid);
  };

  return {
    /** Of the incoming elements, return only those to draw now */
    take(incoming) {
      const local = new Map(excal.getSceneElementsIncludingDeleted().map((e) => [e.id, e]));
      const { now, held } = splitImageSwaps(incoming, local, have);
      for (const e of held) {
        let w = waiting.get(e.fileId);
        if (!w) { w = { els: new Map(), timer: setTimeout(() => flush(e.fileId), MAX_HOLD_MS), busy: false }; waiting.set(e.fileId, w); }
        if (!bases.has(e.id)) bases.set(e.id, local.get(e.id));
        const p = w.els.get(e.id);
        if (!p || p.version < e.version) w.els.set(e.id, e);
        fetch(e.fileId);
      }
      return now;
    },
    /**
          * Element e changed in this side's onChange: true if this side changed a formula that has a held edit (do not send that version).
          * On the next microtask, replace the scene with a version rebuilt on top of the edit, so that onChange sends the new version.
     */
    rebase(e) {
      if (!e || e.type !== "image" || e.isDeleted) return false;
      for (const [fid, w] of waiting) {
        const h = w.els.get(e.id);
        if (!h || e.fileId === h.fileId) continue;
        w.els.delete(e.id);
        const base = bases.get(e.id); bases.delete(e.id);
        if (!w.els.size) { waiting.delete(fid); clearTimeout(w.timer); }
        queueMicrotask(() => {
          const scene = excal.getSceneElementsIncludingDeleted();
          const cur = scene.find((x) => x.id === e.id);
          if (!cur || cur.isDeleted) return;
          const next = rebaseHeld(cur, h, base);
          excal.updateScene({ elements: scene.map((x) => (x.id === e.id ? next : x)), captureUpdate: "NEVER" });
          if (rebased) rebased([next]);
        });
        return true;
      }
      return false;
    },
    /** Event that a new file has been uploaded to the server */
    onFile(fid) { if (waiting.has(fid)) fetch(fid); },
    get size() { return waiting.size; },
    close() { for (const w of waiting.values()) clearTimeout(w.timer); waiting.clear(); bases.clear(); },
  };
}
