import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync, existsSync} from 'node:fs';
import {EXTRA, RESERVED, registerFontFamilies} from '../src/features/fonts/slots.js';
const BASE = () => ({Virgil:1, Helvetica:2, Cascadia:3, Excalifont:5, Nunito:6, 'Lilita One':7, 'Comic Shanns':8, 'Liberation Sans':9});
test('extra slots are added to FONT_FAMILY in place, ids inside reserved range',()=>{
 const t=BASE(); const out=registerFontFamilies(t);
 assert.equal(out,t); assert.equal(t['TB Dokdo'],21); assert.equal(t.Nunito,6);
 for(const id of Object.values(EXTRA)) assert.ok(id>=RESERVED[0]&&id<=RESERVED[1]);
 assert.equal(new Set(Object.values(t)).size,Object.keys(t).length,'ids unique');
});
test('registering twice is harmless',()=>{
 const t=registerFontFamilies(registerFontFamilies(BASE())); assert.equal(t['TB Do Hyeon'],23);
});
test('collision with an Excalidraw id (upgrade) or out-of-range id throws',()=>{
 assert.throws(()=>registerFontFamilies({...BASE(),'Future Font':21}),/겹침/);
 assert.throws(()=>registerFontFamilies(BASE(),{'TB X':7}),/예약 범위/);
});
// the real Excalidraw table -- fails here if an upgrade starts using the reserved range (20-39)
test('installed Excalidraw FONT_FAMILY / FALLBACKS do not use reserved ids',()=>{
 const dir=new URL('../node_modules/@excalidraw/excalidraw/dist/dev/',import.meta.url);
 const src=readdirSync(dir).filter(f=>/^chunk-.*\.js$/.test(f)).map(f=>readFileSync(new URL(f,dir),'utf8')).find(t=>t.includes('var FONT_FAMILY = {'));
 assert.ok(src,'FONT_FAMILY not found in Excalidraw bundle');
 const block=name=>{const m=src.match(new RegExp(`var ${name} = \\{([^}]*)\\}`));assert.ok(m,name);return [...m[1].matchAll(/:\s*(\d+(?:e\d+)?)/g)].map(x=>Number(x[1]));};
 const ids=[...block('FONT_FAMILY'),...block('FONT_FAMILY_FALLBACKS')];
 assert.ok(ids.includes(1)&&ids.includes(100),`parsed ${ids}`);
 for(const id of ids) assert.ok(id<RESERVED[0]||id>RESERVED[1],`Excalidraw uses reserved id ${id}`);
 const real={};for(const id of block('FONT_FAMILY')) real['x'+id]=id;
 assert.doesNotThrow(()=>registerFontFamilies(real));
});
test('extra families live in the lazy face list (latin + korean), not in the entry CSS',async()=>{
 const {default:faces}=await import('../src/features/fonts/extra-faces.js');
 const css=readFileSync(new URL('../src/kfonts-extra.css',import.meta.url),'utf8');
 for(const name of Object.keys(EXTRA)){
  assert.ok(!css.includes(`'${name}'`),`${name} must not be in entry CSS`);
  const rows=faces.filter(f=>f[0]===name);
  assert.ok(rows.length>50,`${name}: ${rows.length} faces`);
  assert.ok(rows.some(f=>/U\+0000-00FF/.test(f[3])),`${name}: latin`);
  assert.ok(rows.some(f=>/U\+ac/i.test(f[3])),`${name}: hangul`);
  // the canvas asks for normal. The bold slot has only the 700 face, so the variable Korean pieces are drawn bold
  const w=name==='TB Gothic Bold'?700:400;
  assert.ok(rows.every(f=>f[1]===w),`${name}: weight ${w}`);
  for(const f of rows) assert.ok(existsSync(new URL(`../public/kfonts/${f[2]}`,import.meta.url)),f[2]);
 }
});
