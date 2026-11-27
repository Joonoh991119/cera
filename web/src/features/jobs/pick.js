// Which elements a render job draws: pure logic (tests: web/tests/jobs.test.mjs). Used only by the lazy renderJob chunk.
import { expandSelection } from "../export/exporters.js";
import { elementBox, listPages, pageElements } from "../pages/pages.js";

export const overlaps = (b, r) => b.x < r.x + r.w && b.x + b.w > r.x && b.y < r.y + r.h && b.y + b.h > r.y;

/**
 * Which elements a render job draws. -> { elements, frame, missing }
 * ids: those elements plus their bound text / container / frame children; region: everything whose box overlaps it; page: that page (1-based number or frame id);
 * none: the whole board. missing = requested ids that are not on the board.
 */
export function pickElements(all, a) {
  const live = (all || []).filter((e) => !e.isDeleted);
  if (a.ids) {
    const have = new Set(live.map((e) => e.id));
    return { elements: expandSelection(live, Object.fromEntries(a.ids.map((i) => [i, true]))), frame: null, missing: a.ids.filter((i) => !have.has(i)) };
  }
  if (a.region) return { elements: live.filter((e) => overlaps(elementBox(e), a.region)), frame: null, missing: [] };
  if (a.page) {
    const pages = listPages(live);
    const frame = typeof a.page === "string" ? pages.find((p) => p.id === a.page) : pages[a.page - 1];
    return frame ? { elements: pageElements(live, frame), frame, missing: [] } : { elements: [], frame: null, missing: [String(a.page)] };
  }
  return { elements: live, frame: null, missing: [] };
}

