// Tab render queue (agent -> a person's edit tab): pure logic only (tests: web/tests/jobs.test.mjs).
// The server keeps jobs in memory (server.py job_*). An agent token creates one; one edit tab claims it (first one wins, others get 409),
// runs it with what only a browser has (Excalidraw rendering, MathJax, mermaid) and posts the result. This file decides who goes first and
// which elements a render job draws; the browser runners are lazy chunks (renderJob.js, mathJob.js, mermaidJob.js).
// No imports here: useAgentJobs (main chunk) uses the claim helpers, so this file must not drag the export / page modules in. Element picking is in pick.js.

export const KINDS = ["render", "math", "mermaid"];
export const DELAYS = { high: 0, mid: 750, low: 1500 };   // claim delay after a job (re)opens; the server enforces the same table (JOB_DELAY)
export const HEARTBEAT_MS = 1500;   // claiming again with the lease token extends the 5 s lease while a slow job (first MathJax load) runs
export const UPLOAD_MS = 4000;      // an upload that hangs longer is aborted and fails the job instead of holding it
export const SAVE_MS = 1500;        // saving the job's elements on the server (after confirm) is aborted after this
export const CONFIRM_MS = 3000;     // confirm() needs at least this much lease left, so save + result surely land before the lease (or the 15 s cap) ends
export const MAX_EDGE = [64, 1568];

/** Claim priority of this tab: visible desktop "high" (at once) > hidden desktop "mid" (0.75 s) > phone "low" (1.5 s). */
export function claimPrio({ coarse, hidden }) {
  return coarse ? "low" : hidden ? "mid" : "high";
}
export const claimDelay = (env) => DELAYS[claimPrio(env)];

export class LeaseLost extends Error {
  constructor() { super("lease lost"); this.code = "lease"; }
}

/**
 * Runs one claimed job (job = the claim response: {jid, kind, args, by, lease}). Pure orchestration, so it is tested with fakes.
 * io = { post(op, body) -> Promise<{status, data}> for op "claim" | "result"; load(kind) -> Promise<runner> (rejects: chunk failed);
 *        every(fn, ms) -> stop(); excal, upload(file), persist(elements) }
 * - Heartbeat every HEARTBEAT_MS; any non-200 marks the lease lost.
 * - The runner calls ctx.confirm() right before it changes the board: one more heartbeat that must answer 200 with at least CONFIRM_MS of lease
 *   left, otherwise LeaseLost (a tab that froze while another tab took the job, or one about to hit the cap, adds nothing). ctx.alive() is the cheap check.
 * - Result: posted with the lease token. A 400 (result rejected, e.g. PNG too big) is re-posted as {ok:false, error} so the agent sees why.
 * Returns { status: "posted" | "lost" | "abandoned" | "rejected", http? }.
 */
export async function executeJob(job, io) {
  let lost = false;
  const beat = async () => {
    const r = await io.post("claim", { lease: job.lease });
    if (!r || r.status !== 200) lost = true;
    return lost ? 0 : Number((r.data && r.data.lease_ms) || 0);   // lease time left (ms)
  };
  const stop = io.every(() => { if (!lost) beat().catch(() => {}); }, HEARTBEAT_MS);
  const ctx = {
    alive: () => !lost,
    confirm: async () => {
      let left = 0;
      if (!lost) { try { left = await beat(); } catch (_) { left = 0; } }
      // too little lease left (near the server's 15 s cap): change nothing and let the job reopen, rather than save and then have the result refused
      if (left < CONFIRM_MS) { lost = true; throw new LeaseLost(); }
    },
    upload: io.upload, persist: io.persist, by: job.by,
  };
  let out;
  try {
    let runner;
    try { runner = await io.load(job.kind); } catch (_) { return { status: "abandoned" }; }   // chunk did not load: the lease runs out, another tab tries
    try { out = await runner(io.excal, job.args || {}, ctx); } catch (e) {
      if (lost || (e && e.code === "lease")) return { status: "lost" };
      out = { ok: false, error: String((e && e.message) || e).slice(0, 300) };
    }
  } finally { stop(); }
  if (lost) return { status: "lost" };
  let r = await io.post("result", { lease: job.lease, ...out });
  if (r && r.status === 400) r = await io.post("result", { lease: job.lease, ok: false, error: `result rejected: ${(r.data && r.data.error) || 400}`.slice(0, 300) });
  return { status: r && r.status === 200 ? "posted" : "rejected", http: r && r.status };
}

/** Scene after a job: put `changed` (new elements or newer versions) into the CURRENT scene, leaving everything else as it is now. */
export function applyChanged(scene, changed) {
  const byId = new Map(changed.map((e) => [e.id, e]));
  const out = scene.map((e) => { const c = byId.get(e.id); if (!c) return e; byId.delete(e.id); return c.version > e.version ? c : e; });
  return [...out, ...byId.values()];
}

/** start(signal) with a deadline: after ms the signal is aborted (the fetch really stops) and it rejects with `what` + " timed out". */
export function withTimeout(start, ms, what = "upload") {
  const ctl = new AbortController();
  let t;
  const late = new Promise((_, rej) => { t = setTimeout(() => { ctl.abort(); rej(new Error(`${what} timed out`)); }, ms); });
  return Promise.race([Promise.resolve().then(() => start(ctl.signal)), late]).finally(() => clearTimeout(t));
}

const num = (v) => typeof v === "number" && Number.isFinite(v);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Render job args -> clean values (bad or missing ones get defaults). */
export function renderArgs(a) {
  const args = a && typeof a === "object" ? a : {};
  const ids = Array.isArray(args.ids) ? args.ids.filter((x) => typeof x === "string" && x).slice(0, 2000) : null;
  const r = args.region;
  const region = r && num(r.x) && num(r.y) && num(r.w) && num(r.h) && r.w > 0 && r.h > 0 ? { x: r.x, y: r.y, w: r.w, h: r.h } : null;
  const page = (typeof args.page === "string" && args.page) || (num(args.page) && args.page >= 1 ? Math.floor(args.page) : null);
  return {
    ids: ids && ids.length ? ids : null, region, page: page || null,
    maxEdge: clamp(num(args.max_edge) ? Math.round(args.max_edge) : 1024, MAX_EDGE[0], MAX_EDGE[1]),
    mono: args.mono !== false,
    pad: clamp(num(args.pad) ? args.pad : 16, 0, 200),
    scale: num(args.scale) && args.scale > 0 ? Math.min(args.scale, 8) : null,
  };
}

/** For recognition: one dark ink on white (highlighter and colored pens become black, fills are dropped). Images are left as they are. Copies only. */
export function monoElements(els, ink = "#1e1e1e") {
  return els.map((e) => (e.type === "image" || e.type === "frame" ? e
    : { ...e, strokeColor: ink, backgroundColor: "transparent", opacity: 100 }));
}

/**
 * Export scale: fit the longest side into maxEdge (never above `want`, default 4x so a tiny selection is not blown up endlessly).
 * w, h are the scene size including padding. Rounding never pushes a side over maxEdge.
 */
export function renderScale(w, h, maxEdge, want = null) {
  const longest = Math.max(1, w, h);
  const s = Math.min(want || 4, (maxEdge / longest) * 0.999);
  return { scale: s, width: Math.max(1, Math.min(maxEdge, Math.round(w * s))), height: Math.max(1, Math.min(maxEdge, Math.round(h * s))) };
}

/** data:image/png;base64,... -> the base64 part (null if it is not a PNG data URL). */
export function pngBase64(dataUrl) {
  const m = /^data:image\/png;base64,(.*)$/.exec(String(dataUrl || ""));
  return m ? m[1] : null;
}

/** Math job: what to do with element el for args {id, latex}. -> "run" | "missing" | "changed" (someone edited the formula since; skip). */
export function mathPlan(el, args) {
  if (!el || el.isDeleted || el.type !== "image") return "missing";
  const cd = el.customData || {};
  if (typeof args.latex !== "string" || !args.latex.trim()) return "missing";
  if (cd.latex !== args.latex) return "changed";
  return "run";
}

/** customData after a math job: the cera.render state ("done" | "error" + message); everything else is kept. */
export function mathCustom(cd, state, message) {
  const cera = { ...((cd && cd.cera) || {}), render: state };
  if (state === "error") cera.message = String(message || "").slice(0, 300);
  else delete cera.message;
  return { ...(cd || {}), cera };
}

/** Where a mermaid result goes: args.at if given, otherwise to the right of everything on the board (80 px gap). */
export function mermaidOrigin(at, sceneBounds) {
  if (at && num(at.x) && num(at.y)) return { x: at.x, y: at.y };
  if (!sceneBounds) return { x: 0, y: 0 };
  const [, minY, maxX] = sceneBounds;
  return { x: Math.round(maxX + 80), y: Math.round(minY) };
}
