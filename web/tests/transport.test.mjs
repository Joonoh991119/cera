// node --test tools/board/whiteboard/tests/transport.test.mjs   (Node 22 or later -- global WebSocket and fetch)
// Scenario 1: if the server goes down after an ack but before saving, edits and deletes of the same ID are revived (starts the real server.py, kills it, and starts it again)
// Scenario 2: once connected, if upgrades keep failing it switches to HTTP without a reload, and unacknowledged edits are delivered that way
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { connect } from "../src/transport.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = join(HERE, "..", "..", "server", "server.py");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000, step = 50) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await sleep(step); }
  return false;
}

// the same merge rule as the server (same as Board.jsx localWins)
const localWins = (m, s) => !s || (m.version !== s.version ? m.version > s.version : m.versionNonce < s.versionNonce);
const el = (id, version, extra = {}) => ({ id, type: "freedraw", version, versionNonce: 1000 + version, isDeleted: false, ...extra });

// ---------------------------------------------------------------- with a fake socket: scenario 2
class FakeWS {
  static instances = [];
  static mode = "ok";   // ok · fail
  constructor(url) {
    this.url = url; this.readyState = 0; this.sent = [];
    FakeWS.instances.push(this);
    setTimeout(() => {
      if (FakeWS.mode === "ok") { this.readyState = 1; this.onopen && this.onopen(); }
      else { this.readyState = 3; this.onclose && this.onclose(); }
    }, 5);
  }
  send(d) { this.sent.push(JSON.parse(d)); }
  close() { if (this.readyState !== 3) { this.readyState = 3; this.onclose && this.onclose(); } }
  recv(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }); }
}

test("R2 — 붙은 뒤 업그레이드가 계속 실패하면 HTTP 로 넘어가고, 미확인 편집이 HTTP 로 간다", async () => {
  FakeWS.instances = []; FakeWS.mode = "ok";
  const posted = [];
  let saved = 0, seq = 0;
  const fakeApi = {
    push: async (id, client, els) => { posted.push(...els); seq += 1; return { accepted: els.length, seq }; },
    poll: async (id, client, since, pver, epoch, signal, savedKnown) => { await sleep(30); saved = seq; return { epoch: "E", seq, pver: 0, saved, events: [] }; },
    presence: async () => null,
  };
  const sync = { seq: 0, pver: -1, epoch: "E" };
  const link = connect({ id: "b1", client: "c1", sync, onEvent() {}, onPresence() {}, onReset: async () => {}, onStatus() {},
    deps: { WebSocket: FakeWS, api: fakeApi, location: { protocol: "http:", host: "x" } } });
  await until(() => link.mode() === "ws");
  const ws0 = FakeWS.instances[0];
  ws0.recv({ t: "hello", epoch: "E", seq: 0, saved: 0, events: [] });
  link.sendElements([el("a", 1)]);
  assert.equal(ws0.sent.filter((m) => m.t === "elements").length, 1);
  // the front end drops without an ack, and after that upgrades keep failing
  FakeWS.mode = "fail";
  ws0.close();
  assert.ok(await until(() => link.mode() === "http", 6000), "HTTP 로 넘어감");
  assert.ok(await until(() => posted.some((e) => e.id === "a")), "미확인 편집이 HTTP 로 전달");
  assert.ok(await until(() => link.pending() === 0), "저장 확인 뒤 들고 있던 것 정리");
  link.close();
});

test("R1(가짜) — ack 만 받고 saved 전에 끊기면 다시 붙을 때 다시 보낸다, saved 뒤에는 안 보낸다", async () => {
  FakeWS.instances = []; FakeWS.mode = "ok";
  const sync = { seq: 0, pver: -1, epoch: "E" };
  const link = connect({ id: "b1", client: "c1", sync, onEvent() {}, onPresence() {}, onReset: async () => {}, onStatus() {},
    deps: { WebSocket: FakeWS, api: {}, location: { protocol: "http:", host: "x" } } });
  await until(() => link.mode() === "ws");
  const ws0 = FakeWS.instances[0];
  link.sendElements([el("a", 2)]);
  const mid = ws0.sent.find((m) => m.t === "elements").id;
  ws0.recv({ t: "ack", id: mid, seq: 5 });
  assert.equal(link.pending(), 1, "ack 뒤에도 저장 전에는 들고 있다");
  ws0.close();
  assert.ok(await until(() => FakeWS.instances.length === 2 && FakeWS.instances[1].readyState === 1));
  assert.ok(FakeWS.instances[1].sent.some((m) => m.t === "elements" && m.elements[0].id === "a"), "다시 붙자 다시 보냄");
  FakeWS.instances[1].recv({ t: "saved", seq: 5 });
  assert.equal(link.pending(), 0, "saved 뒤 정리");
  FakeWS.instances[1].close();
  assert.ok(await until(() => FakeWS.instances.length === 3 && FakeWS.instances[2].readyState === 1));
  assert.ok(!FakeWS.instances[2].sent.some((m) => m.t === "elements"), "저장 확인된 것은 다시 안 보냄");
  link.close();
});

// ---------------------------------------------------------------- with the real server: scenario 1
const hasWS = typeof globalThis.WebSocket === "function";
test("R1(실제 서버) — ack 직후 저장 전 강제 종료 · 같은 ID 수정 · 삭제가 되살아난다", { skip: !hasWS && "Node 22+ 전역 WebSocket 필요" }, async () => {
  const data = mkdtempSync(join(tmpdir(), "wb-r1-"));
  const port = 18000 + Math.floor(Math.random() * 2000);
  const base = `http://127.0.0.1:${port}`;
  const start = (interval) => spawn("python3", [SERVER, "--data", data, "--port", String(port), "--test-user", "시험", "--save-interval", String(interval)], { stdio: "ignore" });
  const H = { "Content-Type": "application/json", "X-Whiteboard": "1" };
  const http = {
    push: (id, c, els) => fetch(`${base}/api/whiteboard/boards/${id}/elements`, { method: "POST", headers: H, body: JSON.stringify({ client: c, elements: els }) }).then((r) => r.json()),
    poll: (id, c, since, pver, epoch, signal, saved) => fetch(`${base}/api/whiteboard/boards/${id}/poll?client=${c}&since=${since}&pver=${pver}&saved=${saved}&epoch=${epoch || ""}`, { signal }).then((r) => r.json()),
    presence: () => Promise.resolve(null),
  };
  let srv = start(0.4);
  try {
    assert.ok(await until(async () => { try { return (await fetch(`${base}/api/whiteboard/me`)).ok; } catch { return false; } }, 8000));
    const b = await (await fetch(`${base}/api/whiteboard/boards`, { method: "POST", headers: H, body: JSON.stringify({ title: "R1" }) })).json();
    const local = new Map();   // this page's scene
    const sync = { seq: 0, pver: -1, epoch: "" };
    let link = null;
    const onEvent = (ev, d) => { if (ev === "elements") for (const e of d.elements) { if (localWins(e, local.get(e.id)) ) local.set(e.id, e); } };
    const onReset = async (given) => {
      const els = given || (await (await fetch(`${base}/api/whiteboard/boards/${b.id}`)).json()).elements;
      const server = new Map(els.map((e) => [e.id, e]));
      for (const e of els) if (localWins(e, local.get(e.id))) local.set(e.id, e);
      const mine = [...local.values()].filter((e) => localWins(e, server.get(e.id)));
      if (mine.length) link.sendElements(mine);
    };
    link = connect({ id: b.id, client: "cR1", sync, onEvent, onPresence() {}, onReset, onStatus() {},
      deps: { WebSocket: globalThis.WebSocket, api: http, location: { protocol: "http:", host: `127.0.0.1:${port}` } } });
    assert.ok(await until(() => link.mode() === "ws"));
    // 1) send the first two versions and wait until saving is confirmed
    const a1 = el("keep", 1), d1 = el("gone", 1);
    local.set("keep", a1); local.set("gone", d1);
    link.sendElements([a1, d1]);
    assert.ok(await until(() => link.pending() === 0, 5000), "처음 판 저장 확인");
    // 2) turn the long save interval back on -> edit and delete the same ID -> take only the ack and kill the server at once
    srv.kill("SIGKILL"); await sleep(150);
    srv = start(60);
    // wait for the server itself first (a slow CI machine can take seconds to start Python); the link may come back
    // over WebSocket or, after repeated failed upgrades, over long-polling: both count as reconnected
    assert.ok(await until(async () => { try { return (await fetch(`${base}/api/whiteboard/me`)).ok; } catch { return false; } }, 15000), "서버 재시작 1");
    assert.ok(await until(() => link.mode() !== "connecting", 15000), "다시 붙음(서버 재시작 1)");
    await sleep(300);
    const a2 = el("keep", 2, { x: 42 }), d2 = el("gone", 2, { isDeleted: true });
    local.set("keep", a2); local.set("gone", d2);
    link.sendElements([a2, d2]);
    assert.ok(await until(() => link.pending() > 0 && sync.saved < sync.seq || link.pending() > 0, 2000));
    await sleep(250);   // the ack arrived, but before the 60-second save
    srv.kill("SIGKILL"); await sleep(150);
    // 3) the disk holds the old version (1) -- when it restarts, the page resends the newer one
    const onDisk = JSON.parse((await import("node:fs")).readFileSync(join(data, "boards", b.id, "board.json"), "utf8")).elements;
    assert.equal(onDisk.find((e) => e.id === "keep").version, 1, "저장 전에 죽어 디스크는 옛 판");
    srv = start(0.3);
    assert.ok(await until(async () => {
      try {
        const els = (await (await fetch(`${base}/api/whiteboard/boards/${b.id}`)).json()).elements;
        const k = els.find((e) => e.id === "keep"), g = els.find((e) => e.id === "gone");
        return k && k.version === 2 && k.x === 42 && g && g.isDeleted === true;
      } catch { return false; }
    }, 10000), "재시작 뒤 같은 ID 수정 · 삭제 복구");
    assert.ok(await until(() => link.pending() === 0, 5000), "복구분 저장 확인");
    link.close();
  } finally {
    srv.kill("SIGKILL");
  }
});

test("presence 는 보낸 칸만 가고, 다시 붙으면 모아 둔 칸(view 포함)을 보낸다. summon 은 다시 보내지 않는다", async () => {
  FakeWS.instances = []; FakeWS.mode = "ok";
  const sync = { seq: 0, pver: -1, epoch: "E" };
  const link = connect({ id: "b1", client: "c1", sync, onEvent() {}, onPresence() {}, onReset: async () => {}, onStatus() {},
    deps: { WebSocket: FakeWS, api: {}, location: { protocol: "http:", host: "x" } } });
  await until(() => link.mode() === "ws");
  const ws0 = FakeWS.instances[0];
  link.sendPresence({ pointer: { x: 1, y: 2 }, button: "up", selected: {} });
  link.sendPresence({ view: [0, 0, 10, 10], summon: true });
  const sent = ws0.sent.filter((m) => m.t === "presence");
  assert.deepEqual(sent[1], { t: "presence", view: [0, 0, 10, 10], summon: true }, "바뀐 칸만");
  ws0.close();
  assert.ok(await until(() => FakeWS.instances.length > 1 && link.mode() === "ws"));
  const again = FakeWS.instances.at(-1).sent.find((m) => m.t === "presence");
  assert.deepEqual(again, { t: "presence", pointer: { x: 1, y: 2 }, button: "up", selected: {}, view: [0, 0, 10, 10] });
  link.close();
});

test("?pd=1 로 붙고, 전체 목록 위에 바뀐 것만(pdelta) 더한다(순서 유지 · 나간 사람 빼기)", async () => {
  FakeWS.instances = []; FakeWS.mode = "ok";
  const seen = [];
  const sync = { seq: 0, pver: -1, epoch: "E" };
  const link = connect({ id: "b1", client: "c1", sync, onEvent() {}, onPresence(l) { seen.push(l.map((p) => `${p.client}${p.view ? "*" : ""}`).join(",")); }, onReset: async () => {}, onStatus() {},
    deps: { WebSocket: FakeWS, api: {}, location: { protocol: "http:", host: "x" } } });
  await until(() => link.mode() === "ws");
  const ws0 = FakeWS.instances[0];
  assert.match(ws0.url, /[?&]pd=1(&|$)/);
  ws0.recv({ t: "presence", pver: 1, data: [{ client: "a" }, { client: "b" }, { client: "c" }] });
  ws0.recv({ t: "pdelta", pver: 2, set: [{ client: "b", view: [0, 0, 1, 1] }, { client: "d" }], gone: ["a"] });
  assert.deepEqual(seen, ["a,b,c", "b*,c,d"]);
  assert.equal(sync.pver, 2);
  link.close();
});

test("키를 바꿔 끊기면 /me 403 을 보고 다시 붙지 않고 onDenied(기본은 새로 고침 → '초대 링크가 필요해요')", async () => {
  FakeWS.instances = []; FakeWS.mode = "ok";
  let meErr = null, deniedCalls = 0;
  const statuses = [];
  const fakeApi = { me: async () => { if (meErr) throw meErr; return { role: "edit" }; }, poll: async () => ({}), presence: async () => null };
  const sync = { seq: 0, pver: -1, epoch: "E" };
  const link = connect({ id: "b1", client: "c1", sync, onEvent() {}, onPresence() {}, onReset: async () => {}, onStatus(s) { statuses.push(s); },
    onDenied() { deniedCalls += 1; }, deps: { WebSocket: FakeWS, api: fakeApi, location: { protocol: "http:", host: "x" } } });
  await until(() => link.mode() === "ws");
  // 1) only the network drops (me works, or an error with no status code) -- reconnects as usual
  meErr = Object.assign(new TypeError("Failed to fetch"));
  FakeWS.instances[0].close();
  assert.ok(await until(() => FakeWS.instances.length === 2 && link.mode() === "ws"), "네트워크 오류는 다시 붙음");
  assert.equal(deniedCalls, 0);
  // 2) key rotation -- the server disconnects and me gives 403
  meErr = Object.assign(new Error("invite link required"), { status: 403 });
  FakeWS.mode = "fail";
  FakeWS.instances[1].close();
  assert.ok(await until(() => deniedCalls === 1), "onDenied 한 번");
  const n = FakeWS.instances.length;
  await sleep(1500);
  assert.equal(FakeWS.instances.length, n, "더는 다시 붙지 않는다");
  assert.equal(link.denied(), true);
  assert.match(statuses.at(-1), /새 링크/);
  link.close();
});

test("HTTP(롱폴링) 쪽에서 403 이 오면 멈추고 onDenied, 기본 onDenied 는 location.reload", async () => {
  FakeWS.instances = []; FakeWS.mode = "fail";
  let polls = 0, reloads = 0;
  const fakeApi = {
    me: async () => ({ role: "edit" }),   // a front end that blocks only the WS upgrade -- switches to HTTP
    poll: async () => { polls += 1; throw Object.assign(new Error("invite link required"), { status: 403 }); },
    presence: async () => null, push: async () => ({ seq: 1 }),
  };
  const sync = { seq: 0, pver: -1, epoch: "E" };
  const link = connect({ id: "b1", client: "c1", sync, onEvent() {}, onPresence() {}, onReset: async () => {}, onStatus() {},
    deps: { WebSocket: FakeWS, api: fakeApi, location: { protocol: "http:", host: "x", reload() { reloads += 1; } } } });
  assert.ok(await until(() => link.mode() === "http", 6000), "업그레이드가 막혀 HTTP 로");
  assert.ok(await until(() => reloads === 1, 4000), "poll 403 → 새로 고침");
  await sleep(1500);
  assert.equal(reloads, 1);
  assert.equal(polls, 1, "403 뒤 다시 시도하지 않는다");
  link.close();
});
