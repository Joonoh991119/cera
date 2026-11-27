// English screen text: with the language set to English, labels and messages built in modules come out in English (no Hangul).
// The language must be set before the modules load (module-level tables such as SHAPES are built on import), so they are imported dynamically.
import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.__TB_LANG__ = "en";
const HANGUL = /[ᄀ-ᇿ㄰-㆏가-힣]/;

const { peopleTitle } = await import("../src/features/follow/follow.js");
const { fileName, exportNotices, BACKGROUNDS } = await import("../src/features/export/exporters.js");
const { tooBigMessage } = await import("../src/features/import/importFile.js");
const { SHAPES } = await import("../src/features/flow/connect.js");
const { LEVEL_LABEL } = await import("../src/features/ink/smooth.js");
const { HL_COLORS } = await import("../src/features/ink/hlcolor.js");
const { TEMPLATES } = await import("../src/features/math/palette.js");
const { left } = await import("../src/invite.js");
const { tr } = await import("../src/i18n.js");

test("people title in English, AI counted separately", () => {
  assert.equal(peopleTitle([]), "1 person here");
  assert.equal(peopleTitle([{ client: "a", user: "길동" }]), "2 people here");
  assert.equal(peopleTitle([{ client: "a", user: "x" }, { client: "g", user: "Claude · AI", agent: true }]), "2 people here · AI 1");
});

test("export file names and notices in English", () => {
  assert.equal(fileName("", "selection"), "board-selection.png");
  assert.equal(fileName("수업", "all"), "수업.png", "the board title (user data) is kept as is");
  const notes = exportNotices({ capped: true, scale: 2.96, fontsFailed: ["TB Dokdo"], filesMissing: 2 });
  assert.equal(notes.length, 3);
  assert.match(notes[0], /reduced to 2\.9×/);
  assert.ok(notes.every((s) => !HANGUL.test(s)), notes.join(" | "));
  assert.deepEqual(BACKGROUNDS.map(([, l]) => l), ["White", "Transparent", "As on screen"]);
});

test("too-big import message in English with size and limit", () => {
  const m = tooBigMessage(13.4 * 1024 * 1024);
  assert.match(m, /13\.4 MB · limit 12 MB/);
  assert.match(m, /cera import/);
  assert.ok(!HANGUL.test(m), m);
});

test("module-level labels (shapes, smoothing, marker colors, math palette) in English", () => {
  assert.deepEqual(SHAPES.map((s) => s.label), ["Rectangle", "Diamond", "Ellipse", "Elbow arrow", "Straight arrow", "Line"]);
  assert.deepEqual(LEVEL_LABEL, { off: "Off", low: "Low", high: "High" });
  assert.deepEqual(HL_COLORS.map(([, l]) => l), ["Yellow", "Lime", "Pink"]);
  assert.ok(TEMPLATES.every((t) => !HANGUL.test(t.title)), TEMPLATES.map((t) => t.title).join(" | "));
});

test("invite time left in English; tr follows a later switch", () => {
  assert.equal(left(Date.now() / 1000 + 30 * 60), "30 min left");
  assert.equal(left(Date.now() / 1000 - 5), "closing soon");
  globalThis.__TB_LANG__ = "ko";
  assert.equal(tr("곧 닫혀요", "closing soon"), "곧 닫혀요");
  globalThis.__TB_LANG__ = "en";
});
