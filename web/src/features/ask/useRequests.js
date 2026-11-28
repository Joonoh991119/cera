// 'Ask the AI' requests on the person's side: the list (GET on open and after a reset, then `request` events), and the actions
// a person may take - create, cancel, close (done), retry (a new request on the same targets, then close the old one), copy the sentence.
// Board.jsx forwards `request` events to notify() and calls refresh() when the transport resets.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KEY, getName } from "../../key.js";
import { tr } from "../../i18n.js";
import { mergeRequest, replaceAll, copyText, ACTION_STATUS } from "./requests.js";

export async function call(method, url, body) {   // also used by the proposal cards (WP-6)
  const h = { "X-Whiteboard": "1" };
  if (KEY) h["X-Tb-Key"] = KEY;
  if (getName()) h["X-Tb-Name"] = encodeURIComponent(getName());
  if (body) h["Content-Type"] = "application/json";
  const r = await fetch(url, { method, headers: h, credentials: "same-origin", body: body ? JSON.stringify(body) : undefined });
  let d = null;
  try { d = await r.json(); } catch (_) { /* empty */ }
  return { status: r.status, data: d };
}

function why(r) {
  if (r.status === 429) return (r.data && r.data.error) || tr("요청이 너무 잦아요 — 잠시 뒤에", "Too many requests — try again shortly");
  if (r.status === 403) return tr("이 링크로는 요청할 수 없어요", "This link can't make requests");
  if (r.status === 409) return tr("그사이 상태가 바뀌었어요", "It changed in the meantime");
  return (r.data && r.data.error) || `HTTP ${r.status}`;
}

export default function useRequests({ id, client, viewOnly, toast }) {
  const [list, setList] = useState([]);
  const env = useRef({});
  env.current = { id, client, viewOnly, toast };

  const refresh = useCallback(async () => {
    try {
      const r = await call("GET", `/api/whiteboard/boards/${env.current.id}/requests`);
      if (r.status === 200 && r.data) setList((l) => replaceAll(l, r.data.requests));
    } catch (_) { /* offline: events and the next refresh fill it in */ }
  }, []);
  useEffect(() => { setList([]); refresh(); }, [id, refresh]);

  const ctl = useMemo(() => {
    const base = () => `/api/whiteboard/boards/${env.current.id}/requests`;
    const say = (m) => env.current.toast && env.current.toast(m);
    async function create({ kind, note, ids }) {
      const r = await call("POST", base(), { client: env.current.client, kind, note, ids });
      if (r.status !== 201) throw new Error(why(r));
      setList((l) => mergeRequest(l, r.data));
      return r.data;
    }
    async function setStatus(rid, status) {
      const r = await call("POST", `${base()}/${rid}`, { client: env.current.client, status });
      if (r.status === 200) setList((l) => mergeRequest(l, r.data));
      else { say(why(r)); refresh(); }
      return r.status === 200;
    }
    async function act(req, action) {
      if (env.current.viewOnly) return;
      if (action === "copy") {
        const text = copyText(env.current.id, req.rid);
        try { await navigator.clipboard.writeText(text); say(tr("복사했어요 — 에이전트 세션에 붙여 넣으세요", "Copied — paste it into your agent session")); }
        catch (_) { if (window.prompt) window.prompt(tr("이 문장을 에이전트 세션에 붙여 넣으세요", "Paste this into your agent session"), text); }
        return;
      }
      if (action === "retry") {
        try { await create({ kind: req.kind, note: req.note || "", ids: (req.targets || []).map((t) => t.id) }); }
        catch (e) { say(tr(`다시 보내지 못했어요: ${e.message}`, `Couldn't send it again: ${e.message}`)); return; }
      }
      if (ACTION_STATUS[action]) await setStatus(req.rid, ACTION_STATUS[action]);
    }
    return { create, act };
  }, [refresh]);

  const notify = useCallback((r) => setList((l) => mergeRequest(l, r)), []);
  return { list, notify, refresh, create: ctl.create, act: ctl.act };
}
