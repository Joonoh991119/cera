// node --test tests/importfile.test.mjs -- pure logic on the page side of import: hiding the drop, the image header, detaching images (id = SHA-1), and the screen centre
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { isImageFile, shouldIntercept, sniffMime, checkDataURL, stripDoc, viewCenter, hasEmbeddedScene, classifyDrop, mayEmbedScene, bodyBytes, MAX_BODY, SCENE_MIME, tooBigMessage, freshInBox, setPendingToast, takePendingToast } from "../src/features/import/importFile.js";
import { deflateSync, crc32 } from "node:zlib";

const PNG = Buffer.from("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a5b6d8b10000000049454e44ae426082", "hex");
const GIF = Buffer.from("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b", "hex");
const durl = (b, m = "image/png") => `data:${m};base64,${b.toString("base64")}`;
const sha = (b) => createHash("sha1").update(b).digest("hex");
const F = (name, type = "") => ({ name, type });

test("드롭 가리기 — 이미지만이면 Excalidraw 에, .excalidraw · 라이브러리 · JSON · 섞인 것은 가로챈다, 파일 없는 끌기는 그대로", () => {
  assert.equal(isImageFile(F("a.png", "image/png")), true);
  assert.equal(isImageFile(F("a.SVG", "")), true);
  assert.equal(isImageFile(F("a.png", "application/json")), false);   // only the extension says image -- the content may be JSON
  assert.equal(shouldIntercept([F("a.png", "image/png"), F("b.jpg", "image/jpeg")]), false);
  assert.equal(shouldIntercept([F("x.excalidraw", "")]), true);
  assert.equal(shouldIntercept([F("x.excalidraw", "application/vnd.excalidraw+json")]), true);
  assert.equal(shouldIntercept([F("lib.excalidrawlib", "")]), true);
  assert.equal(shouldIntercept([F("noext", "")]), true);                 // even a JSON without a name is read by Excalidraw as a scene
  assert.equal(shouldIntercept([F("a.png", "image/png"), F("x.excalidraw", "")]), true);
  assert.equal(shouldIntercept([]), false);
  assert.equal(shouldIntercept(undefined), false);
});

test("그림 머리(magic) — 서버 sniff_mime 과 같다", () => {
  assert.equal(sniffMime(new Uint8Array(PNG)), "image/png");
  assert.equal(sniffMime(new Uint8Array(GIF)), "image/gif");
  assert.equal(sniffMime(new TextEncoder().encode('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>')), "image/svg+xml");
  assert.equal(sniffMime(new TextEncoder().encode("<html><script>")), null);
  assert.equal(checkDataURL(durl(GIF, "image/png")), null, "png 라고 했지만 gif");
  assert.equal(checkDataURL("data:text/html;base64,PHNjcmlwdD4="), null);
  assert.equal(checkDataURL(durl(PNG)).id, sha(PNG));
});

test("그림 떼기 — id 는 내용 SHA-1, 요소 fileId 도 바뀜, 깨진 그림과 그 요소는 빠짐, 문서에는 data URL 이 없다", () => {
  const doc = {
    type: "excalidraw", version: 2,
    elements: [
      { id: "i1", type: "image", fileId: "old1", x: 0, y: 0, width: 10, height: 10 },
      { id: "i2", type: "image", fileId: "bad1", x: 0, y: 0, width: 10, height: 10 },
      { id: "i3", type: "image", fileId: "gone", x: 0, y: 0, width: 10, height: 10 },   // an image that is not in the document -- left as is (the server decides)
      { id: "r1", type: "rectangle", x: 0, y: 0, width: 10, height: 10 },
    ],
    files: { old1: { id: "old1", mimeType: "image/png", dataURL: durl(PNG) }, bad1: { id: "bad1", mimeType: "image/png", dataURL: durl(GIF, "image/png") } },
  };
  const s = stripDoc(doc);
  assert.equal(s.dropped, 1);
  assert.deepEqual(s.doc.elements.map((e) => e.id), ["i1", "i3", "r1"]);
  assert.equal(s.doc.elements[0].fileId, sha(PNG));
  assert.equal(s.doc.elements[1].fileId, "gone");
  assert.deepEqual(s.doc.files, { [sha(PNG)]: { id: sha(PNG), mimeType: "image/png" } });
  assert.equal(s.files.length, 1);
  assert.equal(s.files[0].dataURL, durl(PNG));
  assert.ok(!JSON.stringify(s.doc).includes("base64"));
  assert.equal(doc.elements[0].fileId, "old1", "원본은 그대로");
  // detaching again (an already detached document) gives the same result
  assert.deepEqual(stripDoc(s.doc).doc.files, s.doc.files);
});

test("화면 가운데(장면 좌표) — scroll · zoom", () => {
  assert.deepEqual(viewCenter({ scrollX: 0, scrollY: 0, width: 1000, height: 600, zoom: { value: 1 } }), { x: 500, y: 300 });
  assert.deepEqual(viewCenter({ scrollX: -200, scrollY: 100, width: 1000, height: 600, zoom: { value: 2 } }), { x: 450, y: 50 });
});

// ── r1 B1 · F3 · F4
function pngWithText(keyword, text) {
  const data = Buffer.from(`${keyword}\0${text}`, "latin1");
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from("tEXt"), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32 ? crc32(body) >>> 0 : 0);
  return Buffer.concat([PNG.subarray(0, PNG.length - 12), len, body, crc, PNG.subarray(PNG.length - 12)]);
}
const payload = JSON.stringify({ version: "1", encoding: "bstring", compressed: true, encoded: deflateSync(Buffer.from('{"type":"excalidraw","elements":[]}')).toString("latin1") });

test("장면을 품은 그림 — PNG tEXt 키워드 · SVG payload 주석이면 true, 보통 그림 · 다른 키워드면 false", () => {
  assert.equal(hasEmbeddedScene(new Uint8Array(pngWithText(SCENE_MIME, payload))), true);
  assert.equal(hasEmbeddedScene(new Uint8Array(pngWithText("Comment", "hello"))), false);
  assert.equal(hasEmbeddedScene(new Uint8Array(PNG)), false);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg"><!-- payload-type:${SCENE_MIME} --><!-- payload-start -->eyJ9<!-- payload-end --></svg>`;
  assert.equal(hasEmbeddedScene(new TextEncoder().encode(svg)), true);
  assert.equal(hasEmbeddedScene(new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')), false);
  assert.equal(hasEmbeddedScene(new Uint8Array(GIF)), false);
});

test("떨군 파일 나누기 — 문서는 시트, PNG · SVG 는 확인, 나머지 그림은 바로", () => {
  const c = classifyDrop([F("a.excalidraw"), F("b.png", "image/png"), F("c.jpg", "image/jpeg"), F("d.svg", ""), F("e.excalidrawlib", "")]);
  assert.deepEqual(c.docs.map((f) => f.name), ["a.excalidraw", "e.excalidrawlib"]);
  assert.deepEqual(c.maybe.map((f) => f.name), ["b.png", "d.svg"]);
  assert.deepEqual(c.plain.map((f) => f.name), ["c.jpg"]);
  assert.equal(mayEmbedScene(F("x.webp", "image/webp")), false);
});

test("본문 크기는 실제 UTF-8 바이트(JSON 다시 인코딩 뒤)로 잰다", () => {
  const content = JSON.stringify({ t: '"따옴표" 한글' });
  const body = { name: "a.excalidraw", content };
  assert.equal(bodyBytes(body), Buffer.byteLength(JSON.stringify(body), "utf8"));
  assert.ok(bodyBytes(body) > content.length * 1.3);
  assert.equal(MAX_BODY, 12 * 1024 * 1024);
});

// ── r2 U6 · U7
test("큰 파일 문구 — 손님 말(보드를 연 사람에게), 내부 말(보낼 내용 · 그림 제외) 없음, 크기 · 한도", () => {
  const m = tooBigMessage(13.4 * 1024 * 1024);
  assert.match(m, /보드를 연 사람에게 파일을 보내 주세요/);
  assert.match(m, /13\.4 MB · 한도 12 MB/);
  assert.doesNotMatch(m, /보낼 내용|그림 제외/);
});

test("합친 묶음 고르기 — 전에 없던 · 지우지 않은 · 응답 bbox 안의 요소만", () => {
  const before = new Set(["old"]);
  const els = [{ id: "old", x: 10, y: 10 }, { id: "n1", x: 100, y: 100 }, { id: "n2", x: 300, y: 120 }, { id: "far", x: 5000, y: 0 }, { id: "del", x: 100, y: 100, isDeleted: true }];
  assert.deepEqual(freshInBox(els, before, [100, 100, 300, 200]).map((e) => e.id), ["n1", "n2"]);
  assert.deepEqual(freshInBox(els, before, null).map((e) => e.id), ["n1", "n2", "far"]);
});

test("새 보드 토스트는 한 번만 넘겨진다", () => {
  setPendingToast({ message: "새 보드", duration: 1 });
  assert.equal(takePendingToast().message, "새 보드");
  assert.equal(takePendingToast(), null);
});
