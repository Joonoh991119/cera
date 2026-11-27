import "./_lang-ko.mjs";   // globalThis.__TB_LANG__ = "ko" before the modules under test load (Korean labels and messages)
import test from 'node:test';
import assert from 'node:assert/strict';
import {pdfParts, concatParts, jpegSize} from '../src/features/pages/pdf.js';

// a minimal JPEG header: SOI, APP0 (JFIF), SOF0 (height h, width w, 3 components), EOI -- no image data, which is enough for testing the writer
function fakeJpeg(w, h, comps = 3, pad = 0) {
  const app0 = [0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 1, 1, 0, 0, 1, 0, 1, 0, 0];
  const sof = [0xff, 0xc0, 0x00, 8 + 3 * comps, 8, h >> 8, h & 255, w >> 8, w & 255, comps, ...Array(3 * comps).fill(1)];
  return new Uint8Array([0xff, 0xd8, ...app0, ...sof, ...Array(pad).fill(0x55), 0xff, 0xd9]);
}
const latin1 = (u8) => Buffer.from(u8).toString('latin1');

test('jpegSize reads width · height · components; rejects non-JPEG', () => {
  assert.deepEqual(jpegSize(fakeJpeg(2000, 1125)), {width: 2000, height: 1125, components: 3});
  assert.equal(jpegSize(new Uint8Array([0x89, 0x50, 0x4e, 0x47])), null);
  assert.equal(jpegSize(null), null);
});

test('PDF starts with %PDF-, has one /Page per JPEG and a matching /Count', () => {
  const s = latin1(concatParts(pdfParts([fakeJpeg(2000, 1125), fakeJpeg(1600, 900, 3, 500), fakeJpeg(900, 1600)].map((jpeg) => ({jpeg})), {title: '보드-판서'})));
  assert.ok(s.startsWith('%PDF-1.4\n'));
  assert.equal((s.match(/\/Type \/Page\b(?!s)/g) || []).length, 3);
  assert.match(s, /\/Type \/Pages \/Count 3 /);
  assert.equal((s.match(/\/Filter \/DCTDecode/g) || []).length, 3);
  assert.ok(s.trimEnd().endsWith('%%EOF'));
  // long side 842pt, aspect ratio kept -- a portrait image stays portrait
  assert.match(s, /\/MediaBox \[0 0 842 473\.63\]/);
  assert.match(s, /\/MediaBox \[0 0 473\.63 842\]/);
  // a Korean title is UTF-16BE
  assert.match(s, /\/Title <FEFFBCF4B4DC002DD310C11C>/);
});

test('xref offsets point exactly at each "n 0 obj", startxref points at xref, /Length matches stream bytes', () => {
  const bytes = concatParts(pdfParts([fakeJpeg(100, 50, 3, 37), fakeJpeg(50, 100, 1, 3)].map((jpeg) => ({jpeg}))));
  const s = latin1(bytes);
  const sx = Number(/startxref\n(\d+)\n%%EOF/.exec(s)[1]);
  assert.equal(s.slice(sx, sx + 5), 'xref\n');
  const head = /xref\n0 (\d+)\n/.exec(s.slice(sx));
  const size = Number(head[1]);
  const body = s.slice(sx + head[0].length);
  assert.equal(body.slice(0, 20), '0000000000 65535 f \n');
  for (let k = 1; k < size; k++) {
    const line = body.slice(20 * k, 20 * k + 20);
    assert.match(line, /^\d{10} 00000 n \n$/);
    const off = Number(line.slice(0, 10));
    assert.equal(s.slice(off, off + `${k} 0 obj`.length), `${k} 0 obj`, `object ${k}`);
  }
  assert.match(s, new RegExp(`/Size ${size} `));
  for (const m of s.matchAll(/\/Length (\d+) >>\nstream\n/g)) {
    const start = m.index + m[0].length, len = Number(m[1]);
    assert.equal(s.slice(start + len, start + len + 10), '\nendstream');
  }
  assert.match(s, /\/ColorSpace \/DeviceGray/);
});

test('no pages or a non-JPEG page → clear error', () => {
  assert.throws(() => pdfParts([]), /쪽이 없어요/);
  assert.throws(() => pdfParts([{jpeg: new Uint8Array([1, 2, 3, 4])}]), /JPEG/);
});
