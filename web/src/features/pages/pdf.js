// Zero-dependency PDF writer: wraps one JPEG per page as-is with DCTDecode. Tests: web/tests/pdf.test.mjs
// Why raster: embedding a Korean font in a PDF needs font subsetting and CID mapping (even jsPDF embeds a multi-MB font wholesale for Korean).
// Placing a JPEG already drawn with the on-screen font on each page looks identical regardless of the recipient's fonts. Text search is not possible.
// Structure (PDF 1.4): 1 Catalog, 2 Pages, then per page [Page, Image XObject, content stream], and finally Info -> xref -> trailer.

const enc = new TextEncoder();

// Read width, height and component count from the JPEG header (SOF0-SOF15, excluding DHT, JPG and DAC). Returns null otherwise.
export function jpegSize(b) {
  if (!b || b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8) return null;
  let i = 2;
  while (i + 3 < b.length) {
    if (b[i] !== 0xff) { i += 1; continue; }
    const m = b[i + 1];
    if (m === 0xff) { i += 1; continue; }                                  // fill byte
    if (m === 0xd8 || m === 0x01 || (m >= 0xd0 && m <= 0xd7)) { i += 2; continue; }   // marker without a length
    const len = (b[i + 2] << 8) | b[i + 3];
    if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) {
      if (i + 9 >= b.length) return null;
      return { height: (b[i + 5] << 8) | b[i + 6], width: (b[i + 7] << 8) | b[i + 8], components: b[i + 9] };
    }
    i += 2 + len;
  }
  return null;
}

const num = (x) => String(Math.round(x * 100) / 100);
// PDF text string: a Korean title is encoded as UTF-16BE (BOM FEFF) hex.
function pdfText(s) {
  let hex = "FEFF";
  for (const ch of String(s)) {
    const c = ch.codePointAt(0);
    const units = c > 0xffff ? [0xd800 + ((c - 0x10000) >> 10), 0xdc00 + ((c - 0x10000) & 0x3ff)] : [c];
    for (const u of units) hex += u.toString(16).toUpperCase().padStart(4, "0");
  }
  return `<${hex}>`;
}

// pages: [{ jpeg: Uint8Array, width?, height? }]. If width/height (px) are missing they are read from the JPEG header.
// Page size: the long side is longSide pt (default 842 = A4 long side), keeping the image aspect ratio. Returns an array of Uint8Array chunks (pass straight to Blob).
export function pdfParts(pages, { longSide = 842, title = "", producer = "Cera" } = {}) {
  if (!pages || !pages.length) throw new Error("쪽이 없어요");
  const parts = [];
  const offsets = [];
  let pos = 0;
  const push = (x) => { const u = typeof x === "string" ? enc.encode(x) : x; parts.push(u); pos += u.length; };
  const open = (n) => { offsets[n] = pos; push(`${n} 0 obj\n`); };
  const n = pages.length;
  const pageObj = (i) => 3 + 3 * i;
  const infoObj = 3 + 3 * n;

  push(new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a]));   // %PDF-1.4 + binary marker line
  open(1); push("<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
  open(2); push(`<< /Type /Pages /Count ${n} /Kids [${pages.map((_, i) => `${pageObj(i)} 0 R`).join(" ")}] >>\nendobj\n`);
  pages.forEach((p, i) => {
    const head = jpegSize(p.jpeg);
    if (!head) throw new Error(`${i + 1}쪽 그림이 JPEG 이 아니에요`);
    const w = p.width || head.width, h = p.height || head.height;
    const s = longSide / Math.max(w, h);
    const W = num(w * s), H = num(h * s);
    const cs = head.components === 1 ? "/DeviceGray" : head.components === 4 ? "/DeviceCMYK /Decode [1 0 1 0 1 0 1 0]" : "/DeviceRGB";
    const [po, io, co] = [pageObj(i), pageObj(i) + 1, pageObj(i) + 2];
    open(po);
    push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${W} ${H}] /Resources << /XObject << /Im0 ${io} 0 R >> /ProcSet [/PDF /ImageC] >> /Contents ${co} 0 R >>\nendobj\n`);
    open(io);
    push(`<< /Type /XObject /Subtype /Image /Width ${head.width} /Height ${head.height} /ColorSpace ${cs} /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.jpeg.length} >>\nstream\n`);
    push(p.jpeg);
    push("\nendstream\nendobj\n");
    const content = `q ${W} 0 0 ${H} 0 0 cm /Im0 Do Q`;
    open(co); push(`<< /Length ${enc.encode(content).length} >>\nstream\n${content}\nendstream\nendobj\n`);
  });
  open(infoObj);
  push(`<< /Producer ${pdfText(producer)}${title ? ` /Title ${pdfText(title)}` : ""} >>\nendobj\n`);

  const xref = pos;
  const size = infoObj + 1;
  let table = `xref\n0 ${size}\n0000000000 65535 f \n`;
  for (let k = 1; k < size; k++) table += `${String(offsets[k]).padStart(10, "0")} 00000 n \n`;   // each line is exactly 20 bytes
  push(table);
  push(`trailer\n<< /Size ${size} /Root 1 0 R /Info ${infoObj} 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return parts;
}

export function concatParts(parts) {
  const out = new Uint8Array(parts.reduce((a, p) => a + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}
