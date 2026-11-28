// Screen parts the ask button and the request chips must never cover (viewport rects). Missing ones are skipped.
const SELECTORS = [
  ".wb-dock", ".wb-deskwrap.open", ".wb-pages", ".wb-mtop > :not(.wb-mfill)", ".wb-follow-bar", ".wb-plus",
  ".excalidraw .layer-ui__wrapper__footer-left", ".excalidraw .layer-ui__wrapper__footer-right", ".excalidraw .zoom-actions", ".excalidraw .help-icon",
  ".excalidraw .layer-ui__wrapper__top-right", ".excalidraw .main-menu-trigger", ".excalidraw .Toast",
];

export function screenObstacles(extra = []) {
  const out = [];
  for (const sel of [...SELECTORS, ...extra]) {
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) out.push({ x: r.left, y: r.top, w: r.width, h: r.height });
    }
  }
  return out;
}

// Run fn after the next two frames: the '+' buttons and chips this layout must avoid are placed in their own frame callbacks.
export function afterLayout(fn) {
  let a = 0, b = 0;
  a = requestAnimationFrame(() => { a = 0; b = requestAnimationFrame(() => { b = 0; fn(); }); });
  return () => { cancelAnimationFrame(a); cancelAnimationFrame(b); };
}

export const viewSize = () => ({ w: window.innerWidth, h: window.innerHeight });
