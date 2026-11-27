import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import test from 'node:test';
import assert from 'node:assert/strict';
import {listPages, pageKey, currentPage, stepPage, nearestPage, planNewPage, pageElementsFor, pageElements, fitView, fitAspect, viewRect, elementBox, PAGE, pageId} from '../src/features/pages/pages.js';

const frame = (id, x, extra = {}) => ({id, type: 'frame', x, y: 0, width: 1600, height: 900, isDeleted: false, version: 1, ...extra});
const box = (id, x, y, w = 100, h = 100, extra = {}) => ({id, type: 'rectangle', x, y, width: w, height: h, isDeleted: false, ...extra});

test('pages are frames marked customData.tbPage, sorted by that number then x — not by array order; other frames are not pages', () => {
  const els = [frame('c', 0, {customData: {tbPage: 3}}), frame('a', 5000, {customData: {tbPage: 1}}), frame('n', 100, {customData: {tbPage: 2}}),
    frame('x2', 900, {customData: {tbPage: 4}}), frame('x1', 800, {customData: {tbPage: 4}}), frame('plain', 0, {name: '2024 계획'}),
    frame('gone', 0, {isDeleted: true, customData: {tbPage: 0}}), box('r', 0, 0)];
  assert.deepEqual(listPages(els).map((e) => e.id), ['a', 'n', 'c', 'x1', 'x2']);
  assert.deepEqual(listPages([...els].reverse()).map((e) => e.id), ['a', 'n', 'c', 'x1', 'x2']);
  assert.ok(Number.isNaN(pageKey({name: '장 12'})));
});

test('S4: a non-page frame named "2024 계획" does not bump the next number and is avoided like a drawing', () => {
  const els = [frame('tbpage-1', 0, {customData: {tbPage: 1}}), frame('plan', 1760, {name: '2024 계획', width: 100, height: 100})];
  const plan = planNewPage(els, {x: 0, y: 0, w: 10, h: 10});
  assert.equal(plan.goto, 2);
  assert.equal(plan.create[0].w, 1600);
  assert.ok(plan.create[0].x >= 1760 + 100 + PAGE.gap, 'pushed past the plain frame');
});

test('F1: a new blank page never lands on old drawings that are off screen (to the right of the last page)', () => {
  const p1 = frame('tbpage-1', 0, {customData: {tbPage: 1}});
  const els = [p1, box('old', 2400, 100, 500, 300), box('older', 4100, 600, 800, 200)];
  const plan = planNewPage(els, {x: 0, y: 0, w: 1600, h: 900});
  const r = plan.create[0];
  const hits = (b) => r.x < b.x + b.width && b.x < r.x + r.w && r.y < b.y + b.height && b.y < r.y + r.h;
  assert.ok(!els.slice(1).some(hits), JSON.stringify(r));
  assert.equal(r.x, 4100 + 800 + PAGE.gap);
  const [made] = pageElementsFor(plan, els);
  assert.deepEqual(pageElements([...els, made], made).map((e) => e.id), [made.id]);
  // the same scene gives the same position even if the array order differs
  assert.deepEqual(planNewPage([...els].reverse(), {x: 9999, y: 0, w: 5, h: 5}), plan);
});

test('F1: first press — blank page 2 (and a blank page 1 with nothing in view) also skips off-screen drawings', () => {
  const view = {x: -100, y: -100, w: 800, h: 600};
  const els = [box('a', 0, 0, 400, 300), box('b', 2400, 100, 500, 300)];
  const plan = planNewPage(els, view);
  const two = plan.create[1];
  assert.ok(two.x >= 2400 + 500 + PAGE.gap);
  const empty = planNewPage([box('far', 700, 0, 100, 100)], {x: -5000, y: 0, w: 10, h: 10});
  assert.equal(empty.create.length, 1);
});

test('S3: a deleted page whose id still owns a live stroke is not revived as the "blank" page — the number is skipped', () => {
  const els = [frame('tbpage-1', 0, {customData: {tbPage: 1}}), frame('tbpage-2', 1760, {customData: {tbPage: 2}, isDeleted: true}),
    box('orphan', 1800, 100, 50, 50, {frameId: 'tbpage-2'})];
  const plan = planNewPage(els, {x: 0, y: 0, w: 10, h: 10});
  assert.equal(plan.goto, 3);
  assert.ok(plan.create[0].x > 1850);
});

test('current page = the page that overlaps the view most; none → -1; nearest is used for ◀ ▶ then', () => {
  const pages = listPages([frame('p1', 0, {customData: {tbPage: 1}}), frame('p2', 1760, {customData: {tbPage: 2}}), frame('p3', 3520, {customData: {tbPage: 3}})]);
  assert.equal(currentPage(pages, {x: 1700, y: 0, w: 1600, h: 900}), 1);
  assert.equal(currentPage(pages, {x: 0, y: 5000, w: 100, h: 100}), -1);
  assert.equal(nearestPage(pages, {x: 3800, y: 2000, w: 100, h: 100}), 2);
  assert.equal(stepPage(pages, 1, -1), 0);
  assert.equal(stepPage(pages, 0, -1), -1);
  assert.equal(stepPage(pages, 2, 1), -1);
  assert.equal(stepPage(pages, -1, 1, {x: 0, y: 5000, w: 10, h: 10}), 0);
});

test('viewRect turns scroll · zoom into scene coordinates', () => {
  assert.deepEqual(viewRect({scrollX: -100, scrollY: 50, zoom: {value: 2}, width: 800, height: 600}), {x: 100, y: -50, w: 400, h: 300});
});

test('first press on an empty view → one blank 16:9 page in the middle of the view', () => {
  const plan = planNewPage([], {x: 0, y: 0, w: 1000, h: 800});
  assert.equal(plan.goto, 1); assert.equal(plan.create.length, 1);
  const p = plan.create[0];
  assert.deepEqual([p.w, p.h], [PAGE.w, PAGE.h]);
  assert.deepEqual([p.x + p.w / 2, p.y + p.h / 2], [500, 400]);
});

test('first press with drawings in view → page 1 wraps them (16:9, padded) and a blank page 2 to its right', () => {
  const els = [box('a', 0, 0, 3000, 200), box('far', 90000, 0)];
  const plan = planNewPage(els, {x: -100, y: -100, w: 4000, h: 2000});
  assert.equal(plan.goto, 2);
  const [one, two] = plan.create;
  assert.ok(one.x <= -PAGE.pad && one.x + one.w >= 3000 + PAGE.pad, 'wraps the drawing');
  assert.ok(Math.abs(one.w / one.h - 16 / 9) < 0.01);
  assert.equal(two.x, one.x + one.w + PAGE.gap); assert.equal(two.w, one.w);
});

test('next page goes after the right-most page with the next number — same plan on every screen', () => {
  const els = [frame('tbpage-1', 0, {customData: {tbPage: 1}}), frame('tbpage-2', 1760, {customData: {tbPage: 2}, y: 40})];   // both are our pages
  const a = planNewPage(els, {x: 0, y: 0, w: 100, h: 100});
  const b = planNewPage([...els].reverse(), {x: 1760, y: 0, w: 1600, h: 900});   // a different user, a different screen
  assert.deepEqual(a, b);
  assert.deepEqual(a.create[0], {k: 3, x: 1760 + 1600 + PAGE.gap, y: 40, w: 1600, h: 900});
});

test('two people pressing at once make the same id and shape (merge keeps one); a deleted page id is revived with a higher version', () => {
  const els = [frame('tbpage-1', 0, {customData: {tbPage: 1}})];
  const plan = planNewPage(els, {x: 0, y: 0, w: 10, h: 10});
  const [a] = pageElementsFor(plan, els, 1), [b] = pageElementsFor(plan, els, 1);
  assert.equal(a.id, pageId(2)); assert.equal(a.id, b.id);
  const strip = ({versionNonce, ...r}) => r;
  assert.deepEqual(strip(a), strip(b));
  assert.equal(a.type, 'frame'); assert.deepEqual(a.customData, {tbPage: 2}); assert.equal(a.name, '장 2');
  const dead = {...a, isDeleted: true, version: 7, index: 'a5'};
  const [c] = pageElementsFor(plan, [...els, dead]);
  assert.equal(c.isDeleted, false); assert.equal(c.version, 8); assert.equal(c.index, 'a5');
});

test('pageElements: the page, its children and loose things on it — not other pages or their children', () => {
  const p1 = frame('p1', 0), p2 = frame('p2', 1700);
  const els = [p1, p2, box('on', 100, 100), box('child', 5000, 5000, 10, 10, {frameId: 'p1'}), box('other', 100, 100, 10, 10, {frameId: 'p2'}),
    box('off', 1650, 0, 10, 10), {id: 'pen', type: 'freedraw', x: 1590, y: 10, points: [[0, 0], [30, 5]], strokeWidth: 2, isDeleted: false}, box('del', 10, 10, 1, 1, {isDeleted: true})];
  assert.deepEqual(pageElements(els, p1).map((e) => e.id).sort(), ['child', 'on', 'p1', 'pen']);
});

test('elementBox: rotated box grows, points are relative to x/y', () => {
  const r = elementBox({x: 0, y: 0, width: 100, height: 0, angle: Math.PI / 2});
  assert.ok(Math.abs(r.h - 100) < 1e-9 && Math.abs(r.w) < 1e-9);
  const l = elementBox({x: 10, y: 10, points: [[0, 0], [-20, 30]], strokeWidth: 1});
  assert.equal(l.x, -12); assert.equal(l.h, 34);   // generous by 2x the stroke width
});

test('fitAspect / fitView: page fills the safe area, centred', () => {
  assert.deepEqual(fitAspect({x: 0, y: 0, w: 100, h: 100}, 16 / 9, {w: 0, h: 0}), {x: -39, y: 0, w: 178, h: 100});
  const v = fitView({x: 0, y: 0, w: 1600, h: 900}, {x: 0, y: 60, w: 800, h: 450}, {left: 0, top: 0}, 1);
  assert.equal(v.zoom, 0.5);
  // the page centre (800, 450) lands on the safe-area centre (400, 285)
  assert.equal((800 + v.scrollX) * v.zoom, 400); assert.equal((450 + v.scrollY) * v.zoom, 285);
});
