// Proposal cards (WP-6, B2): pure logic for the card over each proposal anchor, its buttons, the toast and the phone sheet. No DOM, no React.
// An agent's result sits beside the originals inside a locked dashed rectangle (the anchor, id prop-<pid>) whose
// customData.cera.proposal = {pid, rid, sources, results, status: proposed|applied, home, src_box, deleted, note, uncertain, drift, agent}
// (server/agentkit.py make_anchor). People press apply / keep / dismiss / revert; the server does it under its lock (server.py proposal_act).
import { tr } from "../../i18n.js";
import { NOTE_MAX, place, toScreen } from "../ask/requests.js";

export const ANCHOR_PREFIX = "prop-";

/** The proposal record of an anchor element, or null. */
export function proposalOf(e) {
  const p = e && e.customData && e.customData.cera && e.customData.cera.proposal;
  return p && typeof p === "object" && typeof p.pid === "string" ? p : null;
}
export const isAnchor = (e) => !!proposalOf(e);

/** Live anchors in the scene, top to bottom. */
export function anchorsOf(elements) {
  return (elements || []).filter((e) => e && !e.isDeleted && isAnchor(e)).sort((a, b) => a.y - b.y || a.x - b.x);
}

/** Pids of live anchors as one string (cheap to compare between renders). */
export const pidKey = (elements) => anchorsOf(elements).map((e) => proposalOf(e).pid).join(",");

/** Is a card's anchor rect (screen px {x, y, w, h}) at least partly inside the view {w, h}? Off-screen anchors get no card. */
export const onScreen = (rect, view) => rect.x < view.w && rect.y < view.h && rect.x + rect.w > 0 && rect.y + rect.h > 0;

/** Scene boxes {x, y, w, h} of a proposal's live results and the text bound inside them (lines and strokes by their points). The card must
 * never cover any of them. */
export function resultBoxes(p, elements) {
  const ids = new Set((Array.isArray(p && p.results) ? p.results : []).filter((i) => typeof i === "string"));
  const out = [];
  for (const e of elements || []) {
    if (!e || e.isDeleted || !(ids.has(e.id) || (typeof e.containerId === "string" && ids.has(e.containerId)))) continue;
    let x1 = e.x, y1 = e.y, x2 = e.x + (e.width || 0), y2 = e.y + (e.height || 0);
    if (Array.isArray(e.points) && e.points.length) {
      let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;   // a loop, not Math.min(...points): a long stroke would overflow the stack
      for (const q of e.points) { if (q[0] < a) a = q[0]; if (q[0] > c) c = q[0]; if (q[1] < b) b = q[1]; if (q[1] > d) d = q[1]; }
      x1 = e.x + a; x2 = e.x + c; y1 = e.y + b; y2 = e.y + d;
    }
    if ([x1, y1, x2, y2].every(Number.isFinite)) out.push({ x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) });
  }
  return out;
}

const hits = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
/** Where the w x h card goes for an anchor rect (screen px): just above the anchor, else below, right, left (each kept on screen, margin 8),
 * never over a result (`results`, screen rects) nor `obstacles` (chips, other cards) - beside the anchor it slides down past what is in the
 * way; then the chip-style search with the results as
 * obstacles too. null: no room - no card is drawn and the request chip shows instead. */
export function cardSpot(anchor, w, h, results, obstacles, view, gap = 6) {
  if (![anchor.x, anchor.y, anchor.w, anchor.h, w, h].every(Number.isFinite)) return null;
  const m = 8, avoid = [...(results || []), ...(obstacles || [])];
  const fit = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const tries = [[anchor.x, anchor.y - h - gap], [anchor.x, anchor.y + anchor.h + gap], [anchor.x + anchor.w + gap, anchor.y], [anchor.x - w - gap, anchor.y]];
  if (w + 2 * m <= view.w && h + 2 * m <= view.h) {
    for (const [i, [x0, y0]] of tries.entries()) {
      const box = { x: fit(x0, m, view.w - w - m), y: fit(y0, m, view.h - h - m), w, h };
      for (let k = 0; k < 12; k++) {   // beside the anchor (right, left): slide down past what is in the way (the top bar, a node)
        const ob = avoid.find((o) => hits(box, o));
        if (!ob) return { x: box.x, y: box.y };
        if (i < 2) break;
        box.y = ob.y + ob.h + gap;
        if (box.y > view.h - h - m) break;
      }
    }
  }
  if (!(w + 2 * m <= view.w && h + 2 * m <= view.h)) return null;   // wider (or taller) than the screen: never, whatever place() says
  const at = place(anchor, w, h, avoid, view, gap);
  return at && at.x >= 0 && at.x + w <= view.w && !avoid.some((o) => hits({ ...at, w, h }, o)) ? at : null;
}

/** Last resort when no spot keeps off the result (it fills the screen): a size x size ✦ on the right (else left) screen edge, off the
 * bars and chips in `obstacles`, so a person can always act. null if even that edge is full. */
export function edgeSpot(size, obstacles, view, gap = 6) {
  const m = 8;
  for (const x of [view.w - size - m, m]) {
    const box = { x, y: m, w: size, h: size };
    for (let k = 0; k < 20 && box.y <= view.h - size - m; k++) {
      const ob = (obstacles || []).find((o) => hits(box, o));
      if (!ob) return { x: box.x, y: box.y };
      box.y = ob.y + ob.h + gap;
    }
  }
  return null;
}

/** Pids of the cards actually drawn ([{p}] from the layer), as one string for hideCarded: an anchor off screen (or with no room for its
 * card) draws nothing, so its '제안 도착' chip must stay. */
export const shownKey = (drawn) => (drawn || []).map((it) => it && it.p && it.p.pid).filter(Boolean).join(",");

/** Request chips a drawn card already speaks for: a proposed request whose pid has a card on screen shows the card only (no chip on top).
 * key: shownKey of the drawn cards. */
export function hideCarded(list, key) {
  const pids = new Set(String(key || "").split(",").filter(Boolean));
  if (!pids.size) return list;
  return (list || []).filter((r) => !(r.status === "proposed" && r.pid && pids.has(r.pid)));
}

const LABEL = {
  apply: () => tr("바꾸기", "Replace"), keepBoth: () => tr("둘 다 두기", "Keep both"), dismiss: () => tr("버리기", "Discard"),
  retry: () => tr("다시", "Retry"), revert: () => tr("원본 ↺", "Original ↺"), keepDone: () => tr("확정", "Done"), open: () => tr("고르기", "Choose"),
  more: () => "…",
};
// button -> what goes to POST /proposals/<pid> (retry and open stay on this side)
export const ACTION = { apply: "apply", keepBoth: "keep", dismiss: "dismiss", revert: "revert", keepDone: "keep" };
export const label = (a) => (LABEL[a] ? LABEL[a]() : a);
export const TITLE = {
  apply: () => tr("원본을 지우고 이것으로 바꾼다(원래대로 되돌릴 수 있다)", "Remove the original and put this in its place (can be undone)"),
  keepBoth: () => tr("원본과 이것을 둘 다 남긴다", "Keep the original and this"), dismiss: () => tr("이 제안을 지운다(원본은 그대로)", "Delete this proposal (the original stays)"),
  retry: () => tr("같은 것을 AI 에게 다시 맡긴다", "Ask the AI again about the same thing"), revert: () => tr("원본을 되살리고 이것은 제안으로 돌린다", "Bring back the original; this becomes a proposal again"),
  keepDone: () => tr("원본 없이 이대로 둔다(카드를 닫는다)", "Keep it this way without the original (close the card)"),
};

/** Card text and buttons. touch: a proposed card is one big 'choose' button that opens the sheet. viewOnly: state only, no buttons.
 * -> {title, state, note, uncertain, drift, actions, sheet, more} */
export function cardModel(p, { touch = false, viewOnly = false } = {}) {
  const agent = String(p.agent || "AI").slice(0, 40);
  const applied = p.status === "applied";
  const title = applied ? tr(`바꿈 · ${agent}`, `Replaced · ${agent}`) : tr(`제안 · ${agent}`, `Proposal · ${agent}`);
  const state = applied ? "applied" : "proposed";
  const note = typeof p.note === "string" ? p.note.slice(0, NOTE_MAX) : "";
  const uncertain = Array.isArray(p.uncertain) ? p.uncertain.filter((u) => u && typeof u.text === "string").slice(0, 20) : [];
  const drift = Number.isInteger(p.drift) && p.drift > 0 ? p.drift : 0;
  let actions;
  if (viewOnly) actions = [];
  else if (applied) actions = ["revert", "keepDone"];
  else actions = touch ? ["open"] : ["apply", "keepBoth", "dismiss", "retry"];
  // the phone sheet: what the card would offer (an applied card's sheet opens from the small ✦ button when there is no room for the card)
  return { title, state, note, uncertain, drift, actions, sheet: applied ? ["revert", "keepDone"] : ["apply", "keepBoth", "dismiss"], more: applied ? [] : ["retry"] };
}

export const driftText = (n) => tr(`읽은 뒤 바뀜 ${n}`, `Changed after reading ${n}`);
export const stateText = (state) => (state === "applied" ? tr("바뀜 — 원래대로 되돌릴 수 있어요", "Replaced — can be undone") : tr("사람이 고르기를 기다려요", "Waiting for someone to choose"));

// the card state each button belongs to: a button left on screen after the card moved on (a stale phone sheet) gets 409, never a different action
const FROM = { apply: "proposed", keepBoth: "proposed", dismiss: "proposed", revert: "applied", keepDone: "applied" };
/** The body for POST /proposals/<pid>: the action and the card status that button was drawn for (the server refuses with 409 if it moved on). */
export function actionBody(p, button, client) {
  const action = ACTION[button];
  if (!action) return null;
  return { client, action, from_status: FROM[button] };
}

// Korean subject particle: 이 after a final consonant, else 가 (non-Hangul names get 가)
function subj(name) {
  const s = String(name || "?"), c = s.charCodeAt(s.length - 1);
  return c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 ? `${s}이` : `${s}가`;
}

/** Toast for a `proposal` event {pid, action, by, status}: 3 s on every screen. undo: the apply toast offers '원래대로' (revert). */
export function toastModel(ev, viewOnly = false) {
  const by = String((ev && ev.by) || "?").slice(0, 40);
  const verb = {
    apply: ["바꿈", "replaced it"], revert: ["원래대로 되돌림", "brought back the original"], dismiss: ["제안을 버림", "discarded the proposal"],
    keep: ev && ev.status === "gone" && ev.from === "applied" ? ["확정함", "kept it"] : ["둘 다 둠", "kept both"],
  }[ev && ev.action];
  if (!verb) return null;
  return { text: tr(`${subj(by)} ${verb[0]}`, `${by} ${verb[1]}`), undo: ev.action === "apply" && !viewOnly ? "revert" : null, pid: ev.pid };
}
export const TOAST_MS = 3000;

/** Note for 'retry': links the earlier proposal so the agent knows what was tried. At most NOTE_MAX characters. */
export function retryNote(pid, note) {
  const head = tr(`다시(앞 제안 ${pid})`, `Again (earlier proposal ${pid})`);
  const s = note ? `${head} · ${note}` : head;
  return s.length > NOTE_MAX ? s.slice(0, NOTE_MAX - 1) + "…" : s;
}

/** Ids for 'retry': the originals still on the board. */
export function retryIds(p, elements) {
  const live = new Set((elements || []).filter((e) => e && !e.isDeleted).map((e) => e.id));
  return (Array.isArray(p.sources) ? p.sources : []).filter((i) => live.has(i)).slice(0, 500);
}

/** Scene hash used to compare screens: sorted `id:version` of the elements that are not deleted. */
export function sceneHash(elements) {
  return (elements || []).filter((e) => e && !e.isDeleted).map((e) => `${e.id}:${e.version}`).sort().join("|");
}

// Card size (px) the CSS draws. Wide letters (Hangul, CJK, symbols) count 1 em, others 0.6 em.
const em = (t) => [...String(t || "")].reduce((n, c) => n + (c.charCodeAt(0) > 0x2000 ? 1 : 0.6), 0);
export const CARD = { pad: 8, head: 24, note: 18, row: 30, rowTouch: 44, gap: 4 };
const TITLE_MIN = 64;   // the title may end in '…' down to this width; less than that and the card does not fit
/** Card size (px) the CSS draws, at most maxW wide (the screen less its margins). null when even with the title cut to TITLE_MIN the badges and
 * buttons do not fit - then the layer tries two rows (narrow), then the ✦ button. */
export function cardSize(m, touch, narrow = false, maxW = Infinity) {
  const f = touch ? 14.5 : 13.5, bf = touch ? 14 : 13, bpad = touch ? 24 : 20;   // .wb-ask-act padding
  const btns = m.actions.reduce((s, a) => s + em(label(a)) * bf + bpad + 6, 0);
  const badges = (m.uncertain.length ? (touch ? 38 : 28) : 0) + (m.drift ? em(driftText(m.drift)) * 12 + 22 : 0);
  const title = em(m.title) * f, edge = 2 * CARD.pad + 8 + 4;   // padding, border and a little slack
  const size = (want, need, lo, cap, h) => {
    const w = Math.max(lo, Math.min(cap, maxW, Math.ceil(want)));
    return w <= maxW && w >= need ? { w, h } : null;
  };
  if (touch && narrow) {   // phone, no room for one row beside the result: title on top, buttons below
    return size(edge + Math.max(title + badges, btns), edge + Math.max(Math.min(title, TITLE_MIN) + badges, btns), 120, 374,
      2 * CARD.pad + 4 + 32 + CARD.rowTouch + CARD.gap);
  }
  if (touch) {   // one row: title, badges, buttons (the note is in the sheet)
    return size(edge + title + badges + btns, edge + Math.min(title, TITLE_MIN) + badges + btns, 160, 374, CARD.rowTouch + 2 * CARD.pad);
  }
  const note = Math.min(em(m.note) * 12, 380);
  const h = 2 * CARD.pad + 4 + CARD.head + (m.note ? CARD.note : 0) + (m.actions.length ? CARD.row + CARD.gap : CARD.note);
  return size(edge + Math.max(title + badges, note, btns, m.actions.length ? 0 : em(stateText(m.state)) * 12),
    edge + Math.max(Math.min(title, TITLE_MIN) + badges, btns), 180, 420, h);
}

export const MINI = 48;   // no room even for the two-row card: one ✦ button (opens the sheet), its tap area 44 x 44 inside the border

/** Where each on-screen anchor's card goes (screen px). A card keeps off the bars, every result of every proposal, the cards placed before it
 * and the request chips - except its own request's chip (that one goes away once the card is drawn; counting it would keep the card out and
 * the chip there for good). Wide card, then (phone) two rows, then the ✦ button beside the result, then the ✦ on the screen edge when the
 * result fills the screen. chips: [{rid, x, y, w, h}]. -> [{p, m, card:{x, y, w, h}, narrow, mini}] */
export function layoutCards(anchors, elements, st, view, { touch = false, viewOnly = false, bars = [], chips = [] } = {}) {
  const out = [], placed = [], maxW = view.w - 16;
  const allRes = anchors.flatMap((a) => resultBoxes(proposalOf(a), elements).map((b) => toScreen(b, st)));
  for (const a of anchors) {
    const p = proposalOf(a), m = cardModel(p, { touch, viewOnly });
    const rect = toScreen({ x: a.x, y: a.y, w: a.width, h: a.height }, st);
    if (!onScreen(rect, view)) continue;
    const obs = [...bars, ...placed, ...chips.filter((c) => !p.rid || c.rid !== p.rid)];
    const spot = (sz) => (sz ? cardSpot(rect, sz.w, sz.h, allRes, obs, view) : null);
    let kind = "card", sz = cardSize(m, touch, false, maxW), at = spot(sz);
    if (!at && touch) { kind = "narrow"; sz = cardSize(m, touch, true, maxW); at = spot(sz); }
    if (!at) { kind = "mini"; sz = { w: MINI, h: MINI }; at = spot(sz) || edgeSpot(MINI, obs, view); }
    if (!at) continue;
    const card = { x: at.x, y: at.y, w: sz.w, h: sz.h };
    placed.push(card);
    out.push({ p, m, card, narrow: kind === "narrow", mini: kind === "mini" });
  }
  return out;
}
