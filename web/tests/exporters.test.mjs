import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import test from 'node:test';
import assert from 'node:assert/strict';
import {expandSelection, resolveScope, capScale, exportAppState, defaultBackground, missingFileIds, fileName, LIMITS} from '../src/features/export/exporters.js';

const el = (id, extra = {}) => ({id, type: 'rectangle', isDeleted: false, ...extra});
const scene = () => [
  el('box'), el('boxText', {type: 'text', containerId: 'box'}),
  el('other'),
  el('arrow', {type: 'arrow'}), el('arrowText', {type: 'text', containerId: 'arrow'}),
  el('frame', {type: 'frame'}), el('inFrame', {frameId: 'frame'}), el('inFrameText', {type: 'text', containerId: 'inFrame', frameId: 'frame'}),
  el('gone', {isDeleted: true, containerId: 'box'}),
];
const ids = (els) => els.map((e) => e.id).sort();

test('selecting a box brings its bound text, not other elements or deleted ones', () => {
  assert.deepEqual(ids(expandSelection(scene(), {box: true})), ['box', 'boxText']);
});
test('selecting only the bound text brings its container', () => {
  assert.deepEqual(ids(expandSelection(scene(), {arrowText: true})), ['arrow', 'arrowText']);
});
test('selecting a frame brings its children and their text', () => {
  assert.deepEqual(ids(expandSelection(scene(), {frame: true})), ['frame', 'inFrame', 'inFrameText']);
});
test('false entries in selectedElementIds are ignored; empty → []', () => {
  assert.deepEqual(expandSelection(scene(), {box: false}), []);
  assert.deepEqual(expandSelection(scene(), undefined), []);
});
test('resolveScope falls back to the whole board when nothing is selected', () => {
  const r = resolveScope('selection', scene(), {});
  assert.equal(r.scope, 'all'); assert.equal(r.fellBack, true); assert.equal(r.elements.length, 8);
  const s = resolveScope('selection', scene(), {other: true});
  assert.equal(s.scope, 'selection'); assert.deepEqual(ids(s.elements), ['other']);
  assert.equal(resolveScope('all', scene(), {other: true}).elements.length, 8);
});

test('capScale keeps the asked scale when the canvas fits', () => {
  const r = capScale(800, 600, 2);
  assert.deepEqual(r, {scale: 2, capped: false, width: 1600, height: 1200});
  assert.equal(capScale(800, 600, 3).width, 2400);
});
test('capScale lowers the scale for a big board × 3 and stays within area and side limits', () => {
  const r = capScale(3000, 2000, 3);
  assert.equal(r.capped, true);
  assert.ok(r.scale < 3 && r.scale > 1, String(r.scale));
  assert.ok(r.width * r.height <= LIMITS.maxArea, `${r.width}x${r.height}`);
  const long = capScale(20000, 100, 1);   // a very long board -- the per-side limit
  assert.equal(long.capped, true); assert.ok(long.width <= LIMITS.maxSide);
});
test('capScale never returns a zero-sized canvas', () => {
  const r = capScale(1e6, 1e6, 1);
  assert.ok(r.width >= 1 && r.height >= 1 && r.scale > 0);
});

test('background → appState: transparent drops the background, screen follows the theme', () => {
  assert.equal(exportAppState('transparent', 'dark').exportBackground, false);
  assert.equal(exportAppState('transparent', 'dark').exportWithDarkMode, false);
  assert.equal(exportAppState('white', 'dark').exportWithDarkMode, false);
  assert.equal(exportAppState('screen', 'dark').exportWithDarkMode, true);
  assert.equal(exportAppState('screen', 'light').exportWithDarkMode, false);
  assert.equal(defaultBackground('dark'), 'screen'); assert.equal(defaultBackground('light'), 'white');
});
test('missingFileIds lists image files not yet loaded (once each)', () => {
  const els = [el('i1', {type: 'image', fileId: 'f1'}), el('i2', {type: 'image', fileId: 'f1'}), el('i3', {type: 'image', fileId: 'f2'}), el('i4', {type: 'image', fileId: 'f3', isDeleted: true})];
  assert.deepEqual(missingFileIds(els, {f2: {}}), ['f1']);
});
test('fileName strips characters that are not allowed in file names', () => {
  assert.equal(fileName('수업/1:판서?', 'all'), '수업 1 판서.png');
  assert.equal(fileName('', 'selection'), 'board-선택.png');
});

test('darkenPixels matches invert(93%) hue-rotate(180deg): white → near-black, black → near-white, alpha kept', async () => {
  const {darkenPixels} = await import('../src/features/export/exporters.js');
  const d = new Uint8ClampedArray([255, 255, 255, 255, 30, 30, 30, 128, 227, 26, 28, 255]);
  darkenPixels(d);
  assert.deepEqual([...d.slice(0, 4)], [18, 18, 18, 255]);   // white background -> Excalidraw's dark background (#121212)
  assert.ok(d[4] > 200 && d[5] > 200 && d[6] > 200 && d[7] === 128, `${[...d.slice(4, 8)]}`);
  assert.ok(d[8] > 150 && d[8] > d[9] && d[8] > d[10], `red stays reddish: ${[...d.slice(8, 11)]}`);   // red stays on the red side
});

test('fmtScale rounds down so a capped scale never reads as the asked one', async () => {
  const {fmtScale} = await import('../src/features/export/exporters.js');
  assert.equal(fmtScale(2.96), '2.9'); assert.equal(fmtScale(0.83), '0.8'); assert.equal(fmtScale(0.047), '0.04'); assert.equal(fmtScale(0.001), '0.01');
});
test('exportNotices joins capped · font · file warnings (none when clean)', async () => {
  const {exportNotices} = await import('../src/features/export/exporters.js');
  assert.deepEqual(exportNotices({capped: false, fontsFailed: [], filesMissing: 0}), []);
  const n = exportNotices({capped: true, scale: 2.96, fontsFailed: ['TB Dokdo'], filesMissing: 2});
  assert.equal(n.length, 3); assert.match(n[0], /2\.9배/); assert.match(n[2], /2개/);
});
test('bandRows keeps one ImageData band to a few MB', async () => {
  const {bandRows} = await import('../src/features/export/exporters.js');
  for (const w of [100, 1000, 5005, 16384]) { const r = bandRows(w); assert.ok(r >= 16 && r <= 512); assert.ok(r * w * 4 <= 4 * 1024 * 1024 || r === 16, `${w}:${r}`); }
});
