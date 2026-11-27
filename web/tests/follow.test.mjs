// Pure logic for following -- measuring the range, same range, fitting to a page, direct movement, default presenter, collection order
import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import test from 'node:test';
import assert from 'node:assert/strict';
import {sceneView, sameView, validView, followTarget, followViewport, movedFrom, userMoved, defaultLeader, findLeader, latestSummon, maxSummon, resolveView, isCreator, peopleTitle} from '../src/features/follow/follow.js';

const frame = (id, x, y = 0, k = 1) => ({id, type: 'frame', x, y, width: 1600, height: 900, isDeleted: false, customData: {tbPage: k}});

test('sceneView: the unobstructed screen area in scene coords (zoom · scroll · canvas offset)', () => {
  const st = {zoom: {value: 2}, scrollX: -100, scrollY: 50, offsetLeft: 0, offsetTop: 0};
  assert.deepEqual(sceneView(st, {x: 0, y: 0, w: 800, h: 600}), [100, -50, 500, 250]);
  assert.deepEqual(sceneView(st, {x: 20, y: 60, w: 800, h: 400}), [110, -20, 510, 180]);
});

test('sameView tolerates jitter under 0.2% of the width, validView rejects bad shapes', () => {
  assert.ok(sameView([0, 0, 1000, 500], [1, 1.5, 1001, 501]));
  assert.ok(!sameView([0, 0, 1000, 500], [3, 0, 1003, 500]));
  assert.ok(!sameView(null, [0, 0, 1, 1]));
  assert.ok(validView([0, 0, 1, 1]));
  for (const v of [null, [0, 0, 1], [0, 0, NaN, 1], [5, 0, 1, 1], '0,0,1,1']) assert.ok(!validView(v), String(v));
});

test('followTarget: a page seen whole becomes the target (the follower sees the same page), otherwise the raw range', () => {
  const pages = [frame('p1', 0, 0, 1), frame('p2', 1760, 0, 2)];
  // a screen where a desktop presenter fitted page 2 at 95%
  assert.deepEqual(followTarget([1700, -60, 3420, 960], pages), {rect: {x: 1760, y: 0, w: 1600, h: 900}, page: 1});
  // a phone (portrait) presenter -- a page counts when it covers more than 20% of the screen
  assert.equal(followTarget([1740, -900, 3380, 1800], pages).page, 1);
  // zoomed in on only part of one page -> the range is kept as is
  assert.equal(followTarget([1800, 100, 2400, 500], pages).page, -1);
  // zoomed out so both pages are visible -> the range is kept as is (not narrowed to one page)
  assert.equal(followTarget([-100, -100, 3500, 1000], pages).page, -1);
  assert.equal(followTarget([0, 0, 10, 10], []).page, -1);
});

test('followViewport fits the leader range inside the follower safe area (portrait phone shows the whole range)', () => {
  const g = followViewport([0, 0, 1600, 800], [], {x: 8, y: 60, w: 374, h: 600}, {left: 0, top: 0});
  assert.ok(Math.abs(g.zoom - 374 / 1600) < 1e-9);
  // screen centre = range centre
  const cx = (8 + 374 / 2) / g.zoom - g.scrollX, cy = (60 + 300) / g.zoom - g.scrollY;
  assert.ok(Math.abs(cx - 800) < 1e-6 && Math.abs(cy - 400) < 1e-6);
});

test('movedFrom: only a real zoom/scroll change counts (sub-pixel float noise does not)', () => {
  const a = {scrollX: 10, scrollY: 20, zoom: 1.5};
  assert.ok(!movedFrom({scrollX: 10.0001, scrollY: 20, zoom: {value: 1.5}}, a));
  assert.ok(movedFrom({scrollX: 12, scrollY: 20, zoom: {value: 1.5}}, a));
  assert.ok(movedFrom({scrollX: 10, scrollY: 20, zoom: {value: 1.6}}, a));
  assert.ok(!movedFrom({scrollX: 99, scrollY: 0, zoom: {value: 1}}, null));
});

test('defaultLeader: the board creator among editors, else the first editor; never a viewer or me', () => {
  const users = [{client: 'v', user: '학생', role: 'view'}, {client: 'e1', user: '조교', role: 'edit'}, {client: 'e2', user: '교수', role: 'edit'}, {client: 'me', user: '나', role: 'edit'}];
  assert.equal(defaultLeader(users, 'me', '교수').client, 'e2');
  assert.equal(defaultLeader(users, 'me', '없는 사람').client, 'e1');
  assert.equal(defaultLeader(users, 'e1', null).client, 'e2');
  assert.equal(defaultLeader([{client: 'v', user: '학생', role: 'view'}], 'me', '학생'), null);
});

test('findLeader follows the same person after a reload (new client id, same name)', () => {
  const users = [{client: 'new', user: '교수', role: 'edit'}];
  assert.equal(findLeader(users, {client: 'old', user: '교수'}, 'me').client, 'new');
  assert.equal(findLeader(users, {client: 'old', user: '다른'}, 'me'), null);
});

test('latestSummon: the latest one after what I have seen wins; my own is marked mine; old ones are ignored', () => {
  const users = [{client: 'a', summon: {at: 100}}, {client: 'b', summon: {at: 250}}, {client: 'me', summon: {at: 200}}, {client: 'c'}];
  assert.equal(maxSummon(users), 250);
  assert.equal(latestSummon(users, 'me', 0).user.client, 'b');
  assert.equal(latestSummon(users, 'me', 250), null);
  const s = latestSummon([...users, {client: 'me2', summon: {at: 300}}], 'me2', 250);
  assert.ok(s.mine && s.at === 300);
});

test('userMoved: the stale pre-apply state in the same frame is not a user move; anything else is', () => {
  const applied = {scrollX: 100, scrollY: 0, zoom: 1, from: {scrollX: -500, scrollY: 40, zoom: 2}};
  assert.ok(!userMoved({scrollX: -500, scrollY: 40, zoom: {value: 2}}, applied), 'Excalidraw 가 아직 옛 값');
  assert.ok(!userMoved({scrollX: 100, scrollY: 0, zoom: {value: 1}}, applied), '맞춘 값');
  assert.ok(userMoved({scrollX: 100, scrollY: 80, zoom: {value: 1}}, applied), '사용자가 움직임');
  assert.ok(userMoved({scrollX: 1, scrollY: 0, zoom: {value: 1}}, {scrollX: 100, scrollY: 0, zoom: 1}));
  assert.ok(!userMoved({scrollX: 1, scrollY: 0, zoom: {value: 1}}, null));
});

test('r1 B1/S7 resolveView: followers do not relay; the follow chain leads to the original presenter; loops and dead ends give null', () => {
  const users = [
    {client: 'ipad', user: '교수', role: 'edit', view: [0, 0, 100, 50]},
    {client: 'laptop', user: '교수', role: 'edit', follow: 'ipad'},
    {client: 's1', user: '학생', role: 'view', follow: 'laptop'},
    {client: 'a', user: 'A', role: 'edit', follow: 'b'}, {client: 'b', user: 'B', role: 'edit', follow: 'a'},
    {client: 'x', user: 'X', role: 'edit', follow: 'gone'},
  ];
  const by = (c) => users.find((u) => u.client === c);
  assert.deepEqual(resolveView(users, by('laptop'), 'me'), [0, 0, 100, 50]);
  assert.deepEqual(resolveView(users, by('s1'), 'me'), [0, 0, 100, 50], '따라가는 사람을 따라가도 원래 발표자');
  assert.equal(resolveView(users, by('a'), 'me'), null, '서로 따라가기 — 아무도 안 움직인다');
  assert.equal(resolveView(users, by('laptop'), 'ipad'), null, '사슬이 나에게 돌아오면 멈춤');
  assert.equal(resolveView(users, by('x'), 'me'), null);
});

test('r1 S2/S3: only role "edit" is an editor; among the creator connections prefer the one not following anyone', () => {
  const users = [{client: 'old', user: '교수'}, {client: 'none', user: '교수', role: null},
    {client: 'laptop', user: '교수', role: 'edit', follow: 'ipad'}, {client: 'ipad', user: '교수', role: 'edit'}, {client: 'ta', user: '조교', role: 'edit'}];
  assert.equal(defaultLeader(users, 'me', '교수').client, 'ipad');
  assert.equal(defaultLeader(users.slice(0, 2), 'me', '교수'), null, 'role 이 없거나 null 이면 편집자가 아니다');
  assert.ok(isCreator(users[3], '교수') && !isCreator(users[4], '교수') && !isCreator(users[1], '교수'));
});

import { fitChips, chipOrder } from '../src/features/follow/follow.js';
test('fitChips: as many chips as fit, the rest fold into one "+k" chip', () => {
  const w = [40, 40, 40, 40];
  assert.equal(fitChips(1000, w, 30, 6), 4, 'all fit — no +k');
  assert.equal(fitChips(4 * 46, w, 30, 6), 4, 'exactly fits without +k');
  assert.equal(fitChips(4 * 46 - 1, w, 30, 6), 3, 'one px short — 3 chips + "+1" (3·46 + 36 = 174 ≤ 183)');
  assert.equal(fitChips(46 + 36, w, 30, 6), 1, 'one chip + "+3"');
  assert.equal(fitChips(20, w, 30, 6), 0, 'nothing fits — only "+4"');
  assert.equal(fitChips(0, [], 30, 6), 0);
});
test('fitChips: budget checks include the "+k" chip', () => {
  // 3 chips of 50 + gaps = 168, plus "+1" chip 30+6 = 204
  assert.equal(fitChips(204, [50, 50, 50, 50], 30, 6), 3);
  assert.equal(fitChips(203, [50, 50, 50, 50], 30, 6), 2);
});
test('chipOrder: followed person first so the folded row still shows them', () => {
  const o = [{ client: 'a' }, { client: 'b' }, { client: 'c' }];
  assert.deepEqual(chipOrder(o, 'c').map((u) => u.client), ['c', 'a', 'b']);
  assert.deepEqual(chipOrder(o, 'a').map((u) => u.client), ['a', 'b', 'c']);
  assert.equal(chipOrder(o, null), o);
});

import { over, contrast } from '../src/features/ink/hlcolor.js';
test('dark theme: followed chip text (--dk-fg #eceef1) on person colour 25% over the dark canvas ≥ 4.5:1', () => {
  const PERSON = ['#D55E00', '#0072B2', '#009E73', '#CC79A7', '#F0E442', '#7B61FF', '#66A61E', '#E31A1C'];   // server.py COLORS
  const fg = [0xec, 0xee, 0xf1], canvas = [18, 18, 18];
  for (const c of PERSON) {
    const bg = over(c, 0x40 / 255, canvas);
    assert.ok(contrast(fg, bg) >= 4.5, `${c} ${contrast(fg, bg).toFixed(2)}`);
  }
});

test('사람 창 머리 — AI 는 사람 수에서 빼고 따로 센다', () => {
  assert.equal(peopleTitle([]), '접속한 사람 1명');
  assert.equal(peopleTitle([{ client: 'a', user: '길동' }]), '접속한 사람 2명');
  assert.equal(peopleTitle([{ client: 'a', user: '길동' }, { client: 'g', user: 'Claude · AI', agent: true }]), '접속한 사람 2명 · AI 1');
  assert.equal(peopleTitle([{ client: 'g', user: 'Claude · AI', agent: true }, { client: 'h', user: 'Codex · AI', agent: true }]), '접속한 사람 1명 · AI 2');
  assert.equal(peopleTitle([{ client: 'f', user: 'Claude · AI' }]), '접속한 사람 2명', '이름만 AI 인 사람은 사람');
});
