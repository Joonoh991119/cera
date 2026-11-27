import "./asset-path.js";
import "./key.js";
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@excalidraw/excalidraw/index.css";
import "./kfonts.css";
import "./kfonts-extra.css";
import "./app.css";
import { api } from "./api.js";
import Board from "./Board.jsx";
import { left, copyInvite } from "./invite.js";
import { tr, lang } from "./i18n.js";

function useHash() {
  const [h, setH] = useState(location.hash);
  useEffect(() => {
    const f = () => setH(location.hash);
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  return h;
}

function Login({ onDone }) {
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className="wb-page">
      <form className="wb-login" onSubmit={async (e) => {
        e.preventDefault();
        try { await api.login(name.trim()); onDone(await api.me()); } catch (x) { setErr(x.message); }
      }}>
        <h1>{tr("화이트보드", "Whiteboard")}</h1>
        <p>{tr("다른 사람 화면에 커서와 함께 보일 이름을 써 주세요. 같은 이름으로 다시 들어오면 같은 색이 이어져요.", "Enter the name others will see next to your cursor. Come back with the same name to keep the same color.")}</p>
        <input autoFocus maxLength={40} value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("이름", "Name")} aria-label={tr("이름", "Name")} />
        <button className="wb-primary" disabled={!name.trim()}>{tr("들어가기", "Enter")}</button>
        {err && <p className="wb-err">{err}</p>}
      </form>
    </div>
  );
}

function ago(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const s = (Date.now() - t) / 1000;
  if (s < 60) return tr("방금", "just now");
  if (s < 3600) return tr(`${Math.floor(s / 60)}분 전`, `${Math.floor(s / 60)} min ago`);
  if (s < 86400) return tr(`${Math.floor(s / 3600)}시간 전`, `${Math.floor(s / 3600)} h ago`);
  return new Date(t).toLocaleDateString(tr("ko-KR", "en-US"));
}

function List({ me }) {
  const [boards, setBoards] = useState(null);
  const [title, setTitle] = useState("");
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState("");
  const load = () => api.boards().then(setBoards, (e) => setErr(e.message));
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);
  return (
    <div className="wb-page">
      <header className="wb-top">
        <h1>{tr("화이트보드", "Whiteboard")}</h1>
        {me.expires_at && <span className="wb-me" title={new Date(me.expires_at * 1000).toLocaleString()}>{left(me.expires_at)}</span>}
        {me.role === "edit" && me.invite && me.invite.edit && <button type="button" className="wb-btn" onClick={async () => setCopied(await copyInvite(me, "edit"))}>{tr("초대 링크 복사", "Copy invite link")}</button>}
        <span className="wb-me">{me.name}{me.role === "view" ? tr(" · 보기 전용", " · view only") : ""}</span>
      </header>
      {copied && <p className="wb-hint">{tr("복사했어요 — 이 링크를 받은 사람은 같이 그릴 수 있어요.", "Copied — anyone with this link can draw with you.")}</p>}
      {me.role === "edit" && <form className="wb-new" onSubmit={async (e) => {
        e.preventDefault();
        try { const b = await api.create(title.trim() || tr("새 보드", "New board")); location.hash = `#b=${b.id}`; } catch (x) { setErr(x.message); }
      }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={tr("새 보드 이름(예: 회의 메모)", "New board name (e.g. Meeting notes)")} aria-label={tr("새 보드 이름", "New board name")} />
        <button className="wb-primary">{tr("새 보드 만들기", "Create board")}</button>
      </form>}
      {err && <p className="wb-err">{err}</p>}
      <p className="wb-hint">{tr("보드를 열면 같은 보드에 들어온 사람의 커서와 그림이 바로 보여요. 이미지는 복사해서 붙여 넣으면(⌘V · Ctrl+V) 올라가요.", "Open a board to see everyone's cursors and drawings live. Paste an image (⌘V · Ctrl+V) to upload it.")}</p>
      <ul className="wb-list">
        {boards === null && <li className="wb-muted">{tr("불러오는 중…", "Loading…")}</li>}
        {boards && boards.length === 0 && <li className="wb-muted">{tr("아직 보드가 없어요. 위에서 하나 만들어 보세요.", "No boards yet. Create one above.")}</li>}
        {boards && boards.map((b) => (
          <li key={b.id}>
            <a href={`#b=${b.id}`} className="wb-card">
              <strong>{b.title}</strong>
              <span>{tr(`${b.created_by} · ${ago(b.updated_at)} 고침 · 요소 ${b.elements}개`, `${b.created_by} · edited ${ago(b.updated_at)} · ${b.elements} element${b.elements === 1 ? "" : "s"}`)}</span>
              {b.online > 0 && <span className="wb-online">● {tr(`${b.online}명 접속 중`, `${b.online} online`)}</span>}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

function App() {
  const [me, setMe] = useState(undefined);
  const hash = useHash();
  useEffect(() => { api.me().then(setMe, (e) => setMe(e.status === 403 ? false : null)); }, []);
  if (me === undefined) return <div className="wb-page wb-muted">{tr("불러오는 중…", "Loading…")}</div>;
  if (me === false) return (   // key rotation and expiry both land here (the server does not say which one)
    <div className="wb-page"><h1>{tr("초대 링크가 필요해요", "You need an invite link")}</h1>
      <p className="wb-muted">{tr("링크가 바뀌었거나 끝났을 수 있어요. 보드를 연 사람에게 새 링크를 받으세요.", "The link may have changed or expired. Ask the person who opened the board for a new one.")}</p>
      <p className="wb-muted">{tr("보드를 연 사람이라면 터미널에서 ", "If you opened the board, run ")}<code>cera links</code>{tr(" 로 새 링크를 볼 수 있어요.", " in a terminal to see the new links.")}</p></div>
  );
  if (me === null) return <Login onDone={setMe} />;
  const m = hash.match(/^#b=([a-z0-9]+)/);
  return m ? <Board key={m[1]} id={m[1]} me={me} /> : <List me={me} />;
}

document.documentElement.lang = lang();   // screen language (i18n.js): saved choice > browser language > English
createRoot(document.getElementById("root")).render(<App />);
