// remaining time and invite-link copy: shared by the list and the board
import { tr } from "./i18n.js";
export function left(expires) {
  if (!expires) return "";
  const s = expires - Date.now() / 1000;
  if (s <= 0) return tr("곧 닫혀요", "closing soon");
  if (s < 3600) return tr(`${Math.ceil(s / 60)}분 뒤 닫혀요`, `${Math.ceil(s / 60)} min left`);
  if (s < 86400 * 2) return tr(`${Math.round(s / 3600)}시간 뒤 닫혀요`, `${Math.round(s / 3600)}h left`);
  return tr(`${Math.round(s / 86400)}일 뒤 닫혀요`, `${Math.round(s / 86400)} days left`);
}

export async function copyInvite(me, kind, boardId) {
  const key = me.invite && me.invite[kind];
  const url = key ? `${location.origin}/?k=${encodeURIComponent(key)}${boardId ? `#b=${boardId}` : ""}` : `${location.origin}/${boardId ? `#b=${boardId}` : ""}`;
  try { await navigator.clipboard.writeText(url); return url; } catch (_) { window.prompt && window.prompt(tr("이 링크를 복사해 보내세요", "Copy this link and send it"), url); return url; }
}
