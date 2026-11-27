import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import test from 'node:test';
import assert from 'node:assert/strict';
import {selectedTexts, selectedStyle, restyleText, selectedLayout} from '../src/textstyle.js';
const fonts=[{id:'meet',family:1},{id:'cute',family:2},{id:'math',family:null}];
const items=[{id:'shape',type:'rectangle',version:2},{id:'bound',type:'text',containerId:'shape',fontFamily:1,fontSize:24,version:3,text:'wrapped',originalText:'original'},{id:'plain',type:'text',fontFamily:2,fontSize:36,version:4,text:'plain'},{id:'deleted',type:'text',isDeleted:true}];
test('shape selection resolves label; deleted and unrelated text excluded',()=>{
 assert.deepEqual(selectedTexts(items,{shape:true}).map(e=>e.id),['bound']);
 assert.deepEqual(selectedTexts(items,{plain:true}).map(e=>e.id),['plain']);
});
test('toolbar reflects selected font and size, mixed selection is explicit',()=>{
 assert.deepEqual(selectedStyle(selectedTexts(items,{shape:true}),fonts),{font:'meet',fontSize:24});
 assert.deepEqual(selectedStyle(selectedTexts(items,{shape:true,plain:true}),fonts),{font:null,fontSize:null});
});
test('restyle includes binding context and remeasures original text, preserves unrelated elements',()=>{
 const out=restyleText(items,{shape:true},{fontFamily:2},(input,local,opts)=>{
  assert.deepEqual(opts,{repairBindings:true,refreshDimensions:true});
  assert.equal(input[1].text,'original');assert.equal(input[1].containerId,'shape');assert.equal(input[1].fontFamily,2);
  return input.map(e=>({...e,width:100,height:30,text:e.type==='text'?'remeasured':e.text}));
 });
 assert.equal(out[1].fontFamily,2);assert.equal(out[1].version,4);assert.equal(out[1].text,'remeasured');
 assert.equal(out[0],items[0]);assert.equal(out[2],items[2]);assert.equal(items[1].fontFamily,1);
 assert.equal(restyleText(items,{}, {},()=>{throw Error('not called')}),null);
});

test('font size changes target text while preserving container binding',()=>{
 const out=restyleText(items,{bound:true},{fontSize:40},input=>input);
 assert.equal(out[1].fontSize,40);assert.equal(out[1].containerId,'shape');assert.equal(out[2],items[2]);
});

test('line height and align: common value or null when mixed',()=>{
 const a={type:'text',lineHeight:1.25,textAlign:'left'}, b={type:'text',lineHeight:1.6,textAlign:'left'};
 assert.deepEqual(selectedLayout([a]),{lineHeight:1.25,textAlign:'left'});
 assert.deepEqual(selectedLayout([a,b]),{lineHeight:null,textAlign:'left'});
 assert.deepEqual(selectedLayout([]),{lineHeight:null,textAlign:null});
});
test('line height change goes through remeasure so height can follow',()=>{
 const out=restyleText(items,{plain:true},{lineHeight:1.6},input=>input.map(e=>e.id==='plain'?{...e,height:e.fontSize*e.lineHeight}:e));
 assert.equal(out[2].lineHeight,1.6);assert.equal(out[2].height,36*1.6);assert.equal(out[2].version,5);
});
