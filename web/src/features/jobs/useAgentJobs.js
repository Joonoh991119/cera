// Tab render queue on the person's side: listens for `job` events, claims (visible desktop first, hidden desktop 0.75 s, phone 1.5 s later),
// runs one job at a time in a lazy chunk and posts the result with the lease token from the claim (executeJob in jobs.js).
// Only kinds render / math / mermaid; anything else is ignored. View-only tabs never take part (the server also answers 403).
// Board.jsx only calls this hook and forwards `job` events to notify().
import { useEffect, useMemo, useRef } from "react";
import { KEY, getName } from "../../key.js";
import { KINDS, UPLOAD_MS, SAVE_MS, claimPrio, claimDelay, executeJob, withTimeout } from "./jobs.js";

const RUNNERS = { render: () => import("./renderJob.js"), math: () => import("./mathJob.js"), mermaid: () => import("./mermaidJob.js") };

async function call(method, url, body, signal) {
  const h = { "X-Whiteboard": "1" };
  if (KEY) h["X-Tb-Key"] = KEY;
  if (getName()) h["X-Tb-Name"] = encodeURIComponent(getName());
  if (body) h["Content-Type"] = "application/json";
  const r = await fetch(url, { method, headers: h, credentials: "same-origin", body: body ? JSON.stringify(body) : undefined, signal });
  let d = null;
  try { d = await r.json(); } catch (_) { /* empty */ }
  return { status: r.status, data: d };
}

const need = (p, what) => p.then((r) => { if (r.status >= 300) throw new Error(`${what} failed: HTTP ${r.status}`); return r.data; });

export default function useAgentJobs({ excal, id, client, viewOnly, coarse }) {
  const env = useRef({});
  env.current = { excal, id, client, viewOnly, coarse };
  const st = useRef({ busy: null, timers: new Map() });   // busy: the jid being claimed or run (claims are never in flight in parallel)

  const ctl = useMemo(() => {
    const base = () => `/api/whiteboard/boards/${env.current.id}/jobs`;
    const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
    const later = (jid, ms) => {
      clearTimeout(st.current.timers.get(jid));
      st.current.timers.set(jid, setTimeout(() => { st.current.timers.delete(jid); attempt(jid); }, ms));
    };
    function consider(job) {
      const e = env.current, s = st.current;
      if (!job || !job.jid || !KINDS.includes(job.kind) || e.viewOnly || !e.excal) return;
      if (job.state !== "open") { clearTimeout(s.timers.get(job.jid)); s.timers.delete(job.jid); return; }
      if (s.busy) return;   // one job at a time; open jobs are picked up again afterwards (refresh)
      later(job.jid, claimDelay({ coarse: e.coarse, hidden: hidden() }));
    }
    async function attempt(jid) {
      const e = env.current, s = st.current;
      if (s.busy || !e.excal || e.viewOnly) return;
      s.busy = jid;
      let retry = 0;
      try {
        let r;
        try { r = await call("POST", `${base()}/${jid}/claim`, { client: e.client, prio: claimPrio({ coarse: e.coarse, hidden: hidden() }) }); } catch (_) { return; }
        if (r.status === 409 && r.data && r.data.error === "wait") { retry = Math.max(100, r.data.retry_ms || 300); return; }
        if (r.status !== 200 || !r.data || !r.data.lease) return;
        const job = r.data;
        await executeJob(job, {
          post: (op, body) => call("POST", `${base()}/${job.jid}/${op}`, { client: e.client, ...body }),
          load: (kind) => RUNNERS[kind]().then((m) => m.default),
          every: (fn, ms) => { const t = setInterval(fn, ms); return () => clearInterval(t); },
          excal: e.excal,
          upload: (f) => withTimeout((signal) => need(call("POST", `/api/whiteboard/boards/${e.id}/files`, { client: e.client, id: f.id, mimeType: f.mimeType, dataURL: f.dataURL }, signal), "upload"), UPLOAD_MS),
          persist: async (els) => { if (els.length) await withTimeout((signal) => need(call("POST", `/api/whiteboard/boards/${e.id}/elements`, { client: e.client, elements: els }, signal), "save"), SAVE_MS, "save"); },
        }).catch(() => {});
      } finally {
        s.busy = null;
        if (retry) later(jid, retry); else refresh();
      }
    }
    async function refresh() {
      const e = env.current;
      if (!e.excal || e.viewOnly) return;
      try {
        const r = await call("GET", base());
        if (r.status === 200 && r.data && Array.isArray(r.data.jobs)) r.data.jobs.forEach(consider);
      } catch (_) { /* offline: the next job event or reconnect brings it back */ }
    }
    return { notify: (data) => consider(data), refresh };
  }, []);

  // a tab that just opened (or came back to the foreground) takes jobs that are still open, e.g. a formula an agent added while nobody had the board open
  useEffect(() => {
    if (!excal || viewOnly) return undefined;
    ctl.refresh();
    const vis = () => { if (document.visibilityState === "visible") ctl.refresh(); };
    document.addEventListener("visibilitychange", vis);
    return () => document.removeEventListener("visibilitychange", vis);
  }, [excal, id, viewOnly, ctl]);
  useEffect(() => () => { st.current.timers.forEach(clearTimeout); st.current.timers.clear(); }, []);
  return ctl;
}
