// LaTeX -> SVG (MathJax 3, Apache-2.0). Fonts are embedded as paths (fontCache none) so each result is a self-contained image that looks the same wherever it is opened.
// Loaded only on first use (nothing is downloaded if math is never used).
import { tr } from "./i18n.js";
let engine = null;
export const SKIP_PACKAGES = new Set(["bussproofs", "html", "action", "setoptions", "unicode"]);

async function load() {
  if (engine) return engine;
  const [{ mathjax }, { TeX }, { SVG }, { liteAdaptor }, { RegisterHTMLHandler }, { AllPackages }] = await Promise.all([
    import("mathjax-full/js/mathjax.js"),
    import("mathjax-full/js/input/tex.js"),
    import("mathjax-full/js/output/svg.js"),
    import("mathjax-full/js/adaptors/liteAdaptor.js"),
    import("mathjax-full/js/handlers/html.js"),
    import("mathjax-full/js/input/tex/AllPackages.js"),
  ]);
  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const html = mathjax.document("", {
    // html (\href, \style ...), action (\toggle ...), setoptions and unicode are removed: one layer of defense in case the image is ever attached to the DOM
    InputJax: new TeX({ packages: AllPackages.filter((p) => !SKIP_PACKAGES.has(p)) }),
    OutputJax: new SVG({ fontCache: "none" }),
  });
  engine = { adaptor, html };
  return engine;
}

/** tex -> {svg, width, height, error}. size = font size (px). color = formula color. */
export async function texToSvg(tex, { size = 28, color = "#1e1e1e", display = true } = {}) {
  const { adaptor, html } = await load();
  const node = html.convert(String(tex || ""), { display, em: size, ex: size / 2, containerWidth: 2000 });
  let svg = adaptor.innerHTML(node);
  const err = /data-mml-node="merror"/.test(svg) ? (svg.match(/<title>([^<]*)<\/title>/) || [])[1] || tr("수식을 읽지 못했어요", "Could not read the formula") : null;
  const m = /width="([\d.]+)ex" height="([\d.]+)ex"/.exec(svg);
  const width = m ? parseFloat(m[1]) * (size / 2) : 200;
  const height = m ? parseFloat(m[2]) * (size / 2) : 60;
  svg = svg
    .replace(/width="[\d.]+ex"/, `width="${width.toFixed(1)}"`)
    .replace(/height="[\d.]+ex"/, `height="${height.toFixed(1)}"`)
    .replace(/\sstyle="[^"]*"/, "")
    .replace(/currentColor/g, color);
  if (!/xmlns=/.test(svg)) svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
  return { svg, width, height, error: err };
}

export function svgDataURL(svg) {
  return "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(svg)));
}

export async function sha1(text) {
  const bytes = new TextEncoder().encode(text);
  // crypto.subtle exists only in secure contexts (https, localhost); it is missing on http://192.168... with `--lan`
  if (globalThis.crypto && globalThis.crypto.subtle) {
    try { return hex(new Uint8Array(await crypto.subtle.digest("SHA-1", bytes))); } catch (_) { /* fall through below */ }
  }
  return sha1js(bytes);
}

const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");

/** SHA-1 in plain JS (for detecting file-name collisions; not for security). Test: web/tests/mathsha1.test.mjs */
export function sha1js(bytes) {
  const n = bytes.length, words = ((n + 8) >> 6) + 1, w = new Uint32Array(words * 16);
  for (let i = 0; i < n; i++) w[i >> 2] |= bytes[i] << (24 - (i % 4) * 8);
  w[n >> 2] |= 0x80 << (24 - (n % 4) * 8);
  w[words * 16 - 1] = n * 8;
  w[words * 16 - 2] = Math.floor((n * 8) / 2 ** 32);
  let h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476, h4 = 0xc3d2e1f0;
  const x = new Uint32Array(80);
  const rol = (v, s) => (v << s) | (v >>> (32 - s));
  for (let b = 0; b < words * 16; b += 16) {
    for (let t = 0; t < 16; t++) x[t] = w[b + t];
    for (let t = 16; t < 80; t++) x[t] = rol(x[t - 3] ^ x[t - 8] ^ x[t - 14] ^ x[t - 16], 1);
    let a = h0, bb = h1, c = h2, d = h3, e = h4;
    for (let t = 0; t < 80; t++) {
      const f = t < 20 ? (bb & c) | (~bb & d) : t < 40 ? bb ^ c ^ d : t < 60 ? (bb & c) | (bb & d) | (c & d) : bb ^ c ^ d;
      const k = t < 20 ? 0x5a827999 : t < 40 ? 0x6ed9eba1 : t < 60 ? 0x8f1bbcdc : 0xca62c1d6;
      const tmp = (rol(a, 5) + f + e + k + x[t]) >>> 0;
      e = d; d = c; c = rol(bb, 30) >>> 0; bb = a; a = tmp;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + bb) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0; h4 = (h4 + e) >>> 0;
  }
  return [h0, h1, h2, h3, h4].map((v) => v.toString(16).padStart(8, "0")).join("");
}
