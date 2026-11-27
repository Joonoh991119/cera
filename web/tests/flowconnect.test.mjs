// node --test tests/flowconnect.test.mjs -- shape table, shortcuts, '+' positions, FlowChartCreator key events
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { SHAPES, SHAPE_TYPES, shapeForKey, currentShape, selectedNode, plusSpots, toViewport, canShowPlus, flowKeyEvents, isDarwin } from "../src/features/flow/connect.js";

test("도형 표 — 여섯 개, 화살표 둘은 모양만 다르다", () => {
  assert.equal(SHAPES.length, 6);
  assert.deepEqual([...SHAPE_TYPES].sort(), ["arrow", "diamond", "ellipse", "line", "rectangle"]);
  assert.equal(SHAPES.find((s) => s.id === "elbow").arrowType, "elbow");
  assert.equal(SHAPES.find((s) => s.id === "straight").arrowType, "sharp");
});

test("단축키 r · d · o · a · l — A 는 모양을 바꾸지 않는다, 나머지는 null", () => {
  assert.equal(shapeForKey("r").type, "rectangle");
  assert.equal(shapeForKey("D").type, "diamond");
  assert.equal(shapeForKey("o").type, "ellipse");
  assert.deepEqual(shapeForKey("a"), { id: "arrow", type: "arrow" });
  assert.equal(shapeForKey("l").type, "line");
  for (const k of ["v", "p", "x", "", "ㄱ", null]) assert.equal(shapeForKey(k), null);
});

test("막대 단추 아이콘 — 지금 도구 · 화살표 모양을 따른다, 아니면 마지막 고른 것", () => {
  assert.equal(currentShape("arrow", "elbow", "rectangle").id, "elbow");
  assert.equal(currentShape("arrow", "round", "rectangle").id, "straight");
  assert.equal(currentShape("diamond", "elbow", "rectangle").id, "diamond");
  assert.equal(currentShape("freedraw", "elbow", "ellipse").id, "ellipse");
  assert.equal(currentShape("freedraw", "elbow", "nope").id, "rectangle");
});

test("흐름도 칸 — 하나만 골랐고 사각형 · 마름모 · 타원이며 잠기지 않았을 때만", () => {
  const els = [{ id: "a", type: "rectangle" }, { id: "b", type: "diamond" }, { id: "t", type: "text" }, { id: "l", type: "ellipse", locked: true }, { id: "d", type: "rectangle", isDeleted: true }];
  assert.equal(selectedNode(els, { a: true }).id, "a");
  assert.equal(selectedNode(els, { a: true, b: false }).id, "a");
  assert.equal(selectedNode(els, { a: true, b: true }), null);
  for (const id of ["t", "l", "d", "zz"]) assert.equal(selectedNode(els, { [id]: true }), null);
  assert.equal(selectedNode(els, {}), null);
});

test("'+' 자리 — 늘 상자 바깥, 위는 돌리기 손잡이만큼 더 띄운다", () => {
  const box = { x: 100, y: 200, w: 30, h: 20 };
  const size = 44, r = size / 2;
  const s = Object.fromEntries(plusSpots(box, size).map((p) => [p.dir, p]));
  assert.ok(s.right.x - r >= box.x + box.w && s.right.y === box.y + box.h / 2);
  assert.ok(s.left.x + r <= box.x);
  assert.ok(s.down.y - r >= box.y + box.h);
  assert.ok(s.up.y + r <= box.y - 22, "위쪽은 더 멀리");
});

test("장면 → 화면 좌표(줌 · 스크롤 · 판 위치)", () => {
  assert.deepEqual(toViewport({ x: 10, y: 20, width: 100, height: 50 }, { scrollX: 5, scrollY: -10, zoom: { value: 2 }, offsetLeft: 3, offsetTop: 4 }), { x: 33, y: 24, w: 200, h: 100 });
});

test("'+' 는 선택 도구에서 가만히 있을 때만", () => {
  const ok = { activeTool: { type: "selection" } };
  assert.ok(canShowPlus(ok));
  for (const extra of [{ activeTool: { type: "rectangle" } }, { viewModeEnabled: true }, { editingTextElement: {} }, { selectedElementsAreBeingDragged: true },
    { resizingElement: {} }, { isRotating: true }, { openDialog: { name: "ttd" } }, { newElement: {} }]) assert.ok(!canShowPlus({ ...ok, ...extra }), JSON.stringify(extra));
});

test("FlowChartCreator 키 — 맥은 ⌘, 그 밖은 Ctrl. 방향키 down 뒤 그 키를 뗀다", () => {
  assert.ok(isDarwin("MacIntel") && isDarwin("iPhone") && !isDarwin("Linux armv8l") && !isDarwin("Win32") && !isDarwin(undefined));
  const [down, up] = flowKeyEvents("right", true);
  assert.equal(down[0], "keydown"); assert.equal(down[1].key, "ArrowRight"); assert.equal(down[1].metaKey, true); assert.equal(down[1].ctrlKey, undefined);
  assert.equal(up[0], "keyup"); assert.equal(up[1].key, "Meta"); assert.equal(up[1].metaKey, undefined);
  const [d2, u2] = flowKeyEvents("up", false);
  assert.equal(d2[1].key, "ArrowUp"); assert.equal(d2[1].ctrlKey, true); assert.equal(u2[1].key, "Control");
});
