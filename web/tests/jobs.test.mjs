import test from "node:test";
import assert from "node:assert/strict";
import "./_lang-ko.mjs";
import { claimPrio, claimDelay, DELAYS, executeJob, applyChanged, withTimeout, renderArgs, monoElements, renderScale, pngBase64, mathPlan, mathCustom, mermaidOrigin } from "../src/features/jobs/jobs.js";
import { pickElements } from "../src/features/jobs/pick.js";

test("visible desktop > hidden desktop > phone", () => {
  assert.equal(claimPrio({ coarse: false, hidden: false }), "high");
  assert.equal(claimPrio({ coarse: false, hidden: true }), "mid");
  assert.equal(claimPrio({ coarse: true, hidden: false }), "low");
  assert.equal(claimPrio({ coarse: true, hidden: true }), "low");
  assert.ok(claimDelay({ coarse: false, hidden: false }) < claimDelay({ hidden: true }) && claimDelay({ hidden: true }) < claimDelay({ coarse: true }));
  assert.deepEqual(DELAYS, { high: 0, mid: 750, low: 1500 });
});

// fake io for executeJob: claim (heartbeat) answers come from `beats`, results are recorded
function fakeIo({ beats = [], results = [200], runner, load, leftMs = 5000 } = {}) {
  const log = { posts: [], board: [], stopped: false };
  let tick = null;
  const io = {
    post: async (op, body) => {
      log.posts.push([op, body]);
      if (op === "claim") { const st = beats.length ? beats.shift() : 200; return { status: st, data: st === 200 ? { lease_ms: leftMs } : { error: "lease lost" } }; }
      const st = results.length ? results.shift() : 200;
      return { status: st, data: st === 400 ? { error: "result: png missing or too large" } : { state: "done" } };
    },
    load: load || (async () => runner),
    every: (fn) => { tick = fn; return () => { log.stopped = true; }; },
    excal: {}, upload: async () => {}, persist: async (els) => { log.board.push(...els); },
    beat: () => tick && tick(),
  };
  return { io, log };
}
const job = { jid: "j1", kind: "mermaid", args: {}, by: "Claude", lease: "tok" };

test("lease lost before the change: the frozen tab adds nothing and posts no result", async () => {
  const { io, log } = fakeIo({
    beats: [409],   // another tab took the job while this one was frozen
    runner: async (_x, _a, ctx) => { await ctx.confirm(); await ctx.persist([{ id: "dup" }]); return { ok: true, data: { ids: ["dup"] } }; },
  });
  assert.equal((await executeJob(job, io)).status, "lost");
  assert.deepEqual(log.board, []);
  assert.equal(log.posts.filter(([op]) => op === "result").length, 0);
  assert.ok(log.stopped, "heartbeat stopped");
});

test("heartbeat refusal mid-run is seen by alive()", async () => {
  let fake;
  const { io, log } = fakeIo({
    beats: [409],
    runner: async (_x, _a, ctx) => { fake.beat(); await new Promise((r) => setTimeout(r, 5)); return ctx.alive() ? { ok: true, data: {} } : { ok: false, error: "x" }; },
  });
  fake = io;
  assert.equal((await executeJob(job, io)).status, "lost");
  assert.equal(log.posts.filter(([op]) => op === "result").length, 0);
});

test("result goes out with the lease token only after the board change is saved", async () => {
  const order = [];
  const { io, log } = fakeIo({ runner: async (_x, _a, ctx) => { await ctx.confirm(); await ctx.persist([{ id: "a" }]); order.push("persisted"); return { ok: true, data: { ids: ["a"] } }; } });
  const post = io.post;
  io.post = async (op, body) => { if (op === "result") order.push("result"); return post(op, body); };
  assert.equal((await executeJob(job, io)).status, "posted");
  assert.deepEqual(order, ["persisted", "result"]);
  const res = log.posts.find(([op]) => op === "result")[1];
  assert.deepEqual([res.lease, res.ok], ["tok", true]);
});

test("a 400 result is re-posted as ok:false with the reason; a save failure becomes ok:false", async () => {
  const { io, log } = fakeIo({ results: [400, 200], runner: async () => ({ ok: true, data: { png: "huge" } }) });
  assert.equal((await executeJob({ ...job, kind: "render" }, io)).status, "posted");
  const rs = log.posts.filter(([op]) => op === "result").map(([, b]) => b);
  assert.equal(rs.length, 2);
  assert.equal(rs[1].ok, false);
  assert.match(rs[1].error, /result rejected: .*too large/);
  const b = fakeIo({ runner: async (_x, _a, ctx) => { await ctx.confirm(); throw new Error("save timed out"); } });
  await executeJob(job, b.io);
  assert.equal(b.log.posts.find(([op]) => op === "result")[1].error, "save timed out");
});

test("a chunk that fails to load is left to another tab (no result)", async () => {
  const { io, log } = fakeIo({ load: async () => { throw new Error("chunk"); } });
  assert.equal((await executeJob(job, io)).status, "abandoned");
  assert.equal(log.posts.length, 0);
});

test("applyChanged keeps edits that arrived meanwhile and adds new elements", () => {
  const out = applyChanged([{ id: "a", version: 5 }, { id: "b", version: 1 }], [{ id: "a", version: 4 }, { id: "b", version: 2 }, { id: "n", version: 1 }]);
  assert.deepEqual(out.map((e) => `${e.id}:${e.version}`), ["a:5", "b:2", "n:1"]);
});

test("withTimeout rejects a hung upload", async () => {
  let seen = null;
  await assert.rejects(withTimeout((signal) => { seen = signal; return new Promise(() => {}); }, 20, "upload"), /upload timed out/);
  assert.equal(seen.aborted, true, "the fetch is aborted, not just abandoned");
  assert.equal(await withTimeout(() => Promise.resolve(3), 50), 3);
});

test("near the 15 s cap (little lease left) confirm refuses: nothing saved, no result, so no duplicate in the next tab", async () => {
  const { io, log } = fakeIo({
    leftMs: 900,   // reviewer r2 repro: the confirm heartbeat passed just before the cap, then the result got 409 and the job reopened
    runner: async (_x, _a, ctx) => { await ctx.confirm(); await ctx.persist([{ id: "dup" }]); return { ok: true, data: { ids: ["dup"] } }; },
  });
  assert.equal((await executeJob(job, io)).status, "lost");
  assert.deepEqual(log.board, []);
  assert.equal(log.posts.filter(([op]) => op === "result").length, 0);
});

test("render args are clamped and defaulted", () => {
  const a = renderArgs({ max_edge: 99999, pad: -5, ids: ["a", 3, ""], scale: 100 });
  assert.deepEqual([a.maxEdge, a.pad, a.ids, a.mono, a.scale], [1568, 0, ["a"], true, 8]);
  const b = renderArgs(null);
  assert.deepEqual([b.maxEdge, b.pad, b.ids, b.region, b.page, b.scale], [1024, 16, null, null, null, null]);
  assert.deepEqual(renderArgs({ region: { x: 0, y: 0, w: 0, h: 5 } }).region, null);
  assert.equal(renderArgs({ mono: false }).mono, false);
  assert.equal(renderArgs({ page: 2.7 }).page, 2);
});

const R = (id, x, y, w = 10, h = 10, more = {}) => ({ id, type: "rectangle", x, y, width: w, height: h, angle: 0, ...more });

test("pick by ids brings bound text, reports missing ids; region picks overlaps; deleted never", () => {
  const els = [R("a", 0, 0, 100, 50, { boundElements: [{ id: "t", type: "text" }] }), { id: "t", type: "text", x: 10, y: 10, width: 20, height: 10, containerId: "a" },
    R("b", 500, 500), R("gone", 0, 0, 10, 10, { isDeleted: true })];
  const p = pickElements(els, renderArgs({ ids: ["a", "nope"] }));
  assert.deepEqual(p.elements.map((e) => e.id).sort(), ["a", "t"]);
  assert.deepEqual(p.missing, ["nope"]);
  assert.deepEqual(pickElements(els, renderArgs({ region: { x: 490, y: 490, w: 20, h: 20 } })).elements.map((e) => e.id), ["b"]);
  assert.deepEqual(pickElements(els, renderArgs({})).elements.map((e) => e.id), ["a", "t", "b"]);
  const page = pickElements(els, renderArgs({ page: 3 }));
  assert.deepEqual([page.elements.length, page.missing], [0, ["3"]]);
});

test("mono: dark ink, no fill, full opacity; images untouched; originals unchanged", () => {
  const hl = { id: "h", type: "freedraw", strokeColor: "#cc8f00", opacity: 50, backgroundColor: "#ff0" };
  const img = { id: "i", type: "image", strokeColor: "transparent", opacity: 40 };
  const [m, i] = monoElements([hl, img]);
  assert.deepEqual([m.strokeColor, m.opacity, m.backgroundColor], ["#1e1e1e", 100, "transparent"]);
  assert.equal(i, img);
  assert.equal(hl.strokeColor, "#cc8f00");
});

test("render scale never makes a side longer than max_edge", () => {
  for (const [w, h, edge] of [[3000, 1000, 1024], [10, 10, 1024], [1023.6, 1, 1024], [5000, 5000, 64]]) {
    const r = renderScale(w, h, edge);
    assert.ok(r.width <= edge && r.height <= edge, `${w}x${h}`);
  }
  assert.equal(renderScale(10, 10, 1024).scale, 4, "tiny selection: at most 4x unless asked");
  assert.ok(renderScale(100, 50, 1024, 2).scale === 2);
});

test("png data URL to base64", () => {
  assert.equal(pngBase64("data:image/png;base64,iVBOR"), "iVBOR");
  assert.equal(pngBase64("data:image/jpeg;base64,xx"), null);
});

test("math job plan and state", () => {
  const el = { id: "m", type: "image", customData: { latex: "\\frac{a}{b}", cera: { render: "pending", pid: "p1" } } };
  assert.equal(mathPlan(el, { latex: "\\frac{a}{b}" }), "run");
  assert.equal(mathPlan(el, { latex: "x" }), "changed");
  assert.equal(mathPlan({ ...el, isDeleted: true }, { latex: "x" }), "missing");
  assert.equal(mathPlan(undefined, { latex: "x" }), "missing");
  const err = mathCustom(el.customData, "error", "Undefined control sequence \\fracc");
  assert.deepEqual([err.cera.render, err.cera.pid, err.latex], ["error", "p1", "\\frac{a}{b}"]);
  assert.match(err.cera.message, /fracc/);
  const done = mathCustom(err, "done");
  assert.deepEqual([done.cera.render, "message" in done.cera], ["done", false]);
});

test("mermaid origin: given point, else right of the board", () => {
  assert.deepEqual(mermaidOrigin({ x: 5, y: 6 }, [0, 0, 100, 100]), { x: 5, y: 6 });
  assert.deepEqual(mermaidOrigin(null, [0, -20, 100, 100]), { x: 180, y: -20 });
  assert.deepEqual(mermaidOrigin({ x: "1" }, null), { x: 0, y: 0 });
});
