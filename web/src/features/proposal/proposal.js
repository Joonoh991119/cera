// Proposal cards (WP-6, B2): pure logic for the card over each proposal anchor, its buttons, the toast and the phone sheet. No DOM, no React.
// An agent's result sits beside the originals inside a locked dashed rectangle (the anchor, id prop-<pid>) whose
// customData.cera.proposal = {pid, rid, sources, results, status: proposed|applied, home, src_box, deleted, note, uncertain, drift, agent}
// (server/agentkit.py make_anchor). People press apply / keep / dismiss / revert; the server does it under its lock (server.py proposal_act).
import { tr } from "../../i18n.js";
import { NOTE_MAX } from "../ask/requests.js";

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

/** Request chips the card already speaks for: a proposed request whose pid has a live card shows the card only (no '제안 도착' chip on top). */
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
  return { title, state, note, uncertain, drift, actions, sheet: ["apply", "keepBoth", "dismiss"], more: ["retry"] };
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
export function cardSize(m, touch) {
  const f = touch ? 14.5 : 13.5, bf = touch ? 14 : 13, bpad = touch ? 24 : 20;   // .wb-ask-act padding
  const btns = m.actions.reduce((s, a) => s + em(label(a)) * bf + bpad + 6, 0);
  const badges = (m.uncertain.length ? 30 : 0) + (m.drift ? em(driftText(m.drift)) * 12 + 22 : 0);
  if (touch) {   // one row: title, badges, buttons
    const w = Math.min(374, Math.ceil(2 * CARD.pad + 8 + em(m.title) * f + badges + btns));
    return { w: Math.max(160, w), h: CARD.rowTouch + 2 * CARD.pad };   // the note is in the sheet
  }
  const head = em(m.title) * f + badges, note = Math.min(em(m.note) * 12, 380);
  const w = Math.min(420, Math.ceil(2 * CARD.pad + 8 + Math.max(head, note, btns, m.actions.length ? 0 : em(stateText(m.state)) * 12)));
  const h = 2 * CARD.pad + 4 + CARD.head + (m.note ? CARD.note : 0) + (m.actions.length ? CARD.row + CARD.gap : CARD.note);
  return { w: Math.max(180, w), h };
}
