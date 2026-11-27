"""Tests for the stdio MCP server (`bin/cera mcp`).

Basic flow: initialize -> notifications/initialized -> tools/list (12 tools, including board_import, and the naming rules) -> site_open{tunnel:none} -> board_add (2 boxes + an arrow)
        -> the board_read summary shows "A" -> "B". Every stdout line is JSON-RPC. Unknown tool gives -32602, unknown method -32601. Runs on /usr/bin/python3.
Also covers the server side of conflict handling, board_wait, deletion, remote mode, and token cleanup (revoked at exit). The human-facing page (Chromium, WebKit) is in tests/live/live_mcp_ai.py.

Everything uses a temporary CERA_HOME (~/.cera is never touched) and no tunnel."""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["CERA_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import http.client
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CLI = HERE / "bin" / "cera"
PY39 = Path("/usr/bin/python3")


class Mcp:
    """One MCP server (a subprocess). It collects every stdout line (to check that all of them are JSON-RPC)."""

    def __init__(self, env, py=None):
        self.p = subprocess.Popen([py or sys.executable, str(CLI), "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env)
        self.lines, self.q, self.n = [], queue.Queue(), 0
        self.err = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self.err.extend(self.p.stderr), daemon=True).start()

    def _read(self):
        for raw in self.p.stdout:
            self.lines.append(raw)
            try:
                self.q.put(json.loads(raw))
            except ValueError:
                self.q.put({"_bad": raw})

    def send(self, msg):
        self.p.stdin.write((json.dumps(msg) + "\n").encode())
        self.p.stdin.flush()

    def call(self, method, params=None, timeout=90):
        self.n += 1
        mid = self.n
        self.send({"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params is not None else {})})
        deadline = time.time() + timeout
        while True:
            m = self.q.get(timeout=max(0.1, deadline - time.time()))
            if m.get("id") == mid:
                return m

    def tool(self, name, args=None, timeout=90):
        r = self.call("tools/call", {"name": name, "arguments": args or {}}, timeout)
        if "error" in r:
            raise AssertionError(r)
        return r["result"]

    def ok(self, name, args=None, timeout=90):
        res = self.tool(name, args, timeout)
        if res.get("isError"):
            raise AssertionError(f"{name}: {res['content'][0]['text']}\nstderr: {b''.join(self.err)[-2000:]}")
        return res

    def init(self, client="claude-code"):
        r = self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": client, "version": "1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:   # noqa: BLE001
            self.p.kill()
            self.p.wait(5)
        time.sleep(0.05)   # let the reader thread collect the last line
        for f in (self.p.stdout, self.p.stderr):
            try:
                f.close()
            except Exception:   # noqa: BLE001
                pass


def raw(port, method, path, headers, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
              headers={"Host": f"127.0.0.1:{port}", **headers})
    r = c.getresponse()
    d = r.read()
    c.close()
    return r.status, (json.loads(d) if d else None)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        dist = t / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>tb</title>")
        self.home = t / "home"
        self.env = {**os.environ, "CERA_HOME": str(self.home), "CERA_DIST": str(dist)}
        for k in ("CERA_URL", "CERA_TOKEN", "CERA_AGENT_NAME"):
            self.env.pop(k, None)
        self.mcps = []

    def tearDown(self):
        for m in self.mcps:
            m.close()
        subprocess.run([sys.executable, str(CLI), "stop", "--all"], env=self.env, capture_output=True)
        self.tmp.cleanup()

    def mcp(self, env=None, py=None):
        m = Mcp(env or self.env, py)
        self.mcps.append(m)
        return m

    def state(self, sid):
        return json.loads((self.home / "sites" / sid / "state.json").read_text())

    def human(self, sid):
        st = self.state(sid)
        return st["port"], {"X-Tb-Key": st["edit_key"], "X-Tb-Name": urllib.parse.quote("길동"), "X-Whiteboard": "1",
                            "Content-Type": "application/json"}


class Handshake(Base):
    def test_criterion_1_flow(self):
        m = self.mcp()
        r = m.init()
        res = r["result"]
        self.assertEqual(res["protocolVersion"], "2025-06-18")
        self.assertEqual(res["serverInfo"]["name"], "cera")
        self.assertIn("사용자에게", res["instructions"])
        self.assertEqual(res["capabilities"], {"tools": {"listChanged": False}})
        tools = m.call("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(len(tools), 12, names)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", n) for n in names))
        self.assertTrue(all(t["inputSchema"]["type"] == "object" for t in tools))
        self.assertTrue(next(t for t in tools if t["name"] == "board_delete")["annotations"]["destructiveHint"])
        self.assertIn("사용자가 공개 링크를 원할 때만", next(t for t in tools if t["name"] == "site_open")["description"])

        opened = m.ok("site_open", {"tunnel": "none"}, timeout=120)["structuredContent"]
        sid, bid = opened["site"], opened["board"]
        self.assertTrue(bid)
        self.assertIn("edit", opened["links"])
        add = m.ok("board_add", {"items": [{"kind": "box", "ref": "a", "label": "A"}, {"kind": "box", "ref": "b", "label": "B"},
                                           {"kind": "arrow", "from": "a", "to": "b", "label": "예"}]})
        st = add["structuredContent"]
        self.assertEqual(set(st["created"]), {"a", "b"})
        self.assertRegex(add["content"][0]["text"], r'\[%s\] 상자 "A" @\(-?\d+,-?\d+ \d+×\d+\)  \(v\d+ · 라벨 v1\)' % re.escape(st["created"]["a"]), "board_add 결과 글에도 판")
        summary = m.ok("board_read", {})["content"][0]["text"]
        self.assertIn('"A" → "B" (라벨 "예")', summary)
        # unknown tool / method, and wrong arguments
        self.assertEqual(m.call("tools/call", {"name": "nope", "arguments": {}})["error"]["code"], -32602)
        self.assertEqual(m.call("resources/list")["error"]["code"], -32601)
        self.assertEqual(m.call("ping")["result"], {})
        bad = m.tool("board_add", {"items": [{"kind": "math", "latex": "x"}]})
        self.assertTrue(bad["isError"])
        self.assertIn("수식", bad["content"][0]["text"])
        self.assertTrue(m.tool("board_read", {"board": "../../x"})["isError"])
        # the name and AI marker shown on the human's screen -- server presence
        port, h = self.human(sid)
        st_, pres = raw(port, "GET", f"/api/whiteboard/boards/{bid}/poll?since=0&client=h&pver=-1&epoch=&wait=0.2", h)
        ai = [p for p in pres["presence"] if p.get("agent")]
        self.assertEqual([p["user"] for p in ai], ["Claude · AI"])
        # one token with by:mcp, revoked at the end
        self.assertEqual([a["by"] for a in self.state(sid)["agents"]], ["mcp"])
        m.close()
        out = b"".join(m.lines).decode()
        for line in out.splitlines():
            msg = json.loads(line)
            self.assertEqual(msg.get("jsonrpc"), "2.0", line)
            self.assertTrue("result" in msg or "error" in msg, line)
        self.assertEqual(self.state(sid)["agents"], [], "MCP 가 끝나면 자기 토큰을 취소한다")

    def test_protocol_versions_and_parse_error(self):
        m = self.mcp()
        for v, want in (("2025-11-25", "2025-11-25"), ("2025-03-26", "2025-03-26"), ("1999-01-01", "2025-11-25")):
            r = m.call("initialize", {"protocolVersion": v, "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}})
            self.assertEqual(r["result"]["protocolVersion"], want)
        m.p.stdin.write(b"{not json\n")
        m.p.stdin.flush()
        bad = m.q.get(timeout=10)
        self.assertEqual(bad["error"]["code"], -32700)
        # batching (2025-03-26)
        m.send([{"jsonrpc": "2.0", "id": 900, "method": "ping"}, {"jsonrpc": "2.0", "id": 901, "method": "nope"}])
        got = {m.q.get(timeout=10)["id"] for _ in range(2)}
        self.assertEqual(got, {900, 901})

    @unittest.skipUnless(PY39.exists(), "/usr/bin/python3 없음")
    def test_system_python_39(self):
        m = self.mcp(py=str(PY39))
        r = m.init("codex-mcp-client")
        self.assertEqual(r["result"]["serverInfo"]["name"], "cera")
        self.assertEqual(len(m.call("tools/list")["result"]["tools"]), 12)
        self.assertTrue(m.tool("site_list")["isError"] is False)

    def test_agent_names(self):
        sys.path.insert(0, str(HERE / "server"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("cera_tb_mcp", HERE / "server" / "tb_mcp.py")
        mod = sys.modules.get("cera_tb_mcp")
        if mod is None:
            mod = importlib.util.module_from_spec(spec)
            sys.modules["cera_tb_mcp"] = mod
            spec.loader.exec_module(mod)
        old = os.environ.pop("CERA_AGENT_NAME", None)
        try:
            self.assertEqual(mod.agent_name_for("claude-code"), "Claude")
            self.assertEqual(mod.agent_name_for("codex-mcp-client"), "Codex")
            self.assertEqual(mod.agent_name_for("antigravity"), "Antigravity")
            self.assertEqual(mod.agent_name_for("agy"), "Antigravity")
            self.assertEqual(mod.agent_name_for("my-tool"), "my-tool")
            self.assertEqual(mod.agent_name_for(None), "Agent")
            os.environ["CERA_AGENT_NAME"] = "Robo"
            self.assertEqual(mod.agent_name_for("claude-code"), "Robo")
        finally:
            os.environ.pop("CERA_AGENT_NAME", None)
            if old is not None:
                os.environ["CERA_AGENT_NAME"] = old


class Flows(Base):
    def setUp(self):
        super().setUp()
        self.m = self.mcp()
        self.m.init()
        o = self.m.ok("site_open", {"tunnel": "none", "title": "흐름"}, timeout=120)["structuredContent"]
        self.sid, self.bid = o["site"], o["board"]
        add = self.m.ok("board_add", {"items": [{"kind": "box", "ref": "a", "label": "A"}, {"kind": "box", "ref": "b", "label": "B"},
                                                {"kind": "arrow", "ref": "ab", "from": "a", "to": "b"}]})["structuredContent"]
        self.a, self.b = add["created"]["a"], add["created"]["b"]
        self.arrow = add["created"]["ab"]
        self.port, self.h = self.human(self.sid)

    def board(self):
        return raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", self.h)[1]

    def els(self):
        return {e["id"]: e for e in self.board()["elements"]}

    def test_update_moves_label_and_arrow_and_conflict(self):
        """A stale base_version gives conflict and the human's change is kept. A matching one carries the label and arrow along."""
        read = self.m.ok("board_read", {})["structuredContent"]
        item = next(i for i in read["items"] if i["id"] == self.a)
        v = item["version"]
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": v, "label_base_version": item["label_version"],
                                                    "text": "입력 단계", "dx": 0, "dy": 200}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "ok")
        e = self.els()
        box, lab, arr = e[self.a], e[self.a + "-label"], e[self.arrow]
        self.assertEqual(lab["originalText"], "입력 단계")
        self.assertTrue(box["x"] <= lab["x"] and lab["x"] + lab["width"] <= box["x"] + box["width"], "라벨이 상자 안")
        self.assertTrue(box["y"] <= lab["y"] <= box["y"] + box["height"])
        sx, sy = arr["x"], arr["y"]
        self.assertTrue(box["y"] - 10 <= sy <= box["y"] + box["height"] + 10, "화살표 시작이 옮긴 상자로 따라왔다")
        # a human edits first (version goes up) -> the agent's old base_version gets conflict
        human = dict(e[self.a], x=e[self.a]["x"] + 5, version=e[self.a]["version"] + 1, versionNonce=1)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h, {"client": "h", "elements": [human]})[0], 200)
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": box["version"], "label_base_version": lab["version"],
                                                    "text": "AI 가 덮으려 함"}]})
        res = r["structuredContent"]["results"][0]
        self.assertEqual(res["status"], "conflict")
        self.assertIn("충돌", r["content"][0]["text"])
        e2 = self.els()
        self.assertEqual(e2[self.a]["x"], human["x"], "사람의 변경이 남는다")
        self.assertEqual(e2[self.a + "-label"]["originalText"], "입력 단계")
        # putting text into a text box with no base_version and no label
        self.m.ok("board_add", {"items": [{"kind": "box", "ref": "n"}], "at": {"x": 0, "y": 900}})
        nitem = [i for i in self.m.ok("board_read", {})["structuredContent"]["items"] if i.get("kind") == "box" and not i.get("text")][0]
        nid = nitem["id"]
        r = self.m.ok("board_update", {"changes": [{"id": nid, "text": "새 라벨", "color": "#0072B2"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "refused", "글 · 색은 base_version 필수(r2)")
        self.m.ok("board_update", {"changes": [{"id": nid, "base_version": nitem["version"], "text": "새 라벨", "color": "#0072B2"}]})
        e3 = self.els()
        self.assertEqual(e3[nid + "-label"]["containerId"], nid)
        self.assertIn({"type": "text", "id": nid + "-label"}, e3[nid]["boundElements"])
        self.assertEqual(e3[nid]["strokeColor"], "#0072B2")
        self.assertIn('[%s] 상자 "새 라벨"' % nid, self.m.ok("board_read", {})["content"][0]["text"])

    def test_wait_returns_on_human_edit_and_times_out(self):
        """When a human edits a text, board_wait returns and summarizes it in one line. If nothing happens, it returns an empty result at the timeout."""
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]
        t0 = time.time()
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 2})
        self.assertTrue(r["structuredContent"]["timeout"])
        self.assertEqual(r["structuredContent"]["changes"], [])
        self.assertLess(time.time() - t0, 6)

        def edit():
            time.sleep(1.0)
            lab = self.els()[self.b + "-label"]
            new = dict(lab, text="길동이 고침", originalText="길동이 고침", version=lab["version"] + 1, versionNonce=3)
            raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/presence", self.h, {"client": "hum1", "pointer": {"x": 0, "y": 0}})
            raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h, {"client": "hum1", "elements": [new]})
        threading.Thread(target=edit, daemon=True).start()
        t0 = time.time()
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 60}, timeout=90)
        self.assertLess(time.time() - t0, 30)
        text = r["content"][0]["text"]
        self.assertIn('"B" → "길동이 고침"', text)
        self.assertIn("길동", text)
        ch = r["structuredContent"]["changes"]
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0]["action"], "글 고침")
        # the agent's own changes do not make it return
        seq2 = r["structuredContent"]["seq"]
        self.m.ok("board_update", {"changes": [{"id": self.a, "dx": 10}]})
        r = self.m.ok("board_wait", {"since_seq": seq2, "timeout_s": 2})
        self.assertTrue(r["structuredContent"].get("timeout"))

    def vers(self, eid):
        """Reads only the board_read content text and turns the (vN, label vM) at the end of an [eid] line into (N, M|None)."""
        text = self.m.ok("board_read", {})["content"][0]["text"]
        m = re.search(r"\[%s\] .*  \(v(\d+)(?: · 라벨 v(\d+))?\)$" % re.escape(eid), text, re.M)
        self.assertIsNotNone(m, text)
        return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)

    def item(self, eid):
        n, m = self.vers(eid)
        return {"id": eid, "base_version": n, **({"label_base_version": m} if m is not None else {})}

    def test_delete_cleans_label_and_bindings(self):
        """Deleting a box also cleans up its label and the end bindings of arrows attached to it (0 orphan texts even after a fresh read)."""
        r = self.m.ok("board_delete", {"ids": [self.item(self.a)]})
        self.assertIn(self.a + "-label", r["structuredContent"]["deleted"])
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "ok")
        e = self.els()
        self.assertTrue(e[self.a]["isDeleted"] and e[self.a + "-label"]["isDeleted"])
        arr = e[self.arrow]
        self.assertFalse(arr["isDeleted"])
        self.assertIsNone(arr["startBinding"], "지운 상자 쪽 끝은 풀렸다")
        self.assertEqual(arr["endBinding"]["elementId"], self.b)
        live = {i: x for i, x in e.items() if not x.get("isDeleted")}
        orphans = [x for x in live.values() if x["type"] == "text" and x.get("containerId") and x["containerId"] not in live]
        self.assertEqual(orphans, [])
        # deleting an arrow also removes it from the end box's boundElements
        self.m.ok("board_delete", {"ids": [self.item(self.arrow)]})
        e = self.els()
        self.assertNotIn(self.arrow, [b["id"] for b in e[self.b].get("boundElements") or []])
        self.assertTrue(self.m.ok("board_delete", {"ids": [{"id": "nope", "base_version": 1}]})["structuredContent"]["missing"] == ["nope"])

    def test_r3_delete_needs_versions_and_respects_human_edits(self):
        """Deleting also needs a version. No version, no label version, a label id, or 'v2' -> refused; if a human edited the label after the read,
        conflict (says what changed) and nothing is deleted."""
        before = self.els()
        for ids, err in (([self.b], "need_base"), ([{"id": self.b, "base_version": self.vers(self.b)[0]}], "need_label_base"),
                         ([{"id": self.b + "-label", "base_version": 1}], "label_id"),
                         ([{"id": self.b, "base_version": "v2", "label_base_version": 1}], "bad_version")):
            r = self.m.ok("board_delete", {"ids": ids})["structuredContent"]["results"][0]
            self.assertEqual((r["status"], r["error"]), ("refused", err), ids)
        self.assertEqual({i: e["version"] for i, e in before.items()}, {i: e["version"] for i, e in self.els().items()}, "아무것도 안 바뀜")
        it = self.item(self.b)
        lab = self.els()[self.b + "-label"]
        human = dict(lab, text="사람이 방금 쓴 중요한 글", originalText="사람이 방금 쓴 중요한 글", version=lab["version"] + 1, versionNonce=5)
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h, {"client": "h", "elements": [human]})
        r = self.m.ok("board_delete", {"ids": [it]})
        res = r["structuredContent"]["results"][0]
        self.assertEqual(res["status"], "conflict")
        self.assertIn("라벨이 바뀌었다", r["content"][0]["text"])
        self.assertIn("사람이 방금 쓴 중요한 글", r["content"][0]["text"])
        e = self.els()
        self.assertFalse(e[self.b]["isDeleted"] or e[self.b + "-label"]["isDeleted"])
        self.assertFalse((e[self.arrow].get("endBinding") or {}).get("elementId") != self.b, "정리(바인딩 풀기)도 하지 않았다")

    def test_add_to_existing_box_page_text_image_and_export(self):
        """Connecting an arrow to an existing box (patch add_bound), new pages, free text, images, and export."""
        png = Path(self.tmp.name) / "dot.png"
        png.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000002000000030806000000") + b"\x00" * 40)
        r = self.m.ok("board_add", {"items": [{"kind": "frame_page", "title": "둘째"}, {"kind": "box", "ref": "c", "label": "C"},
                                              {"kind": "arrow", "from": self.b, "to": "c"}, {"kind": "text", "text": "메모"},
                                              {"kind": "image", "path": str(png), "w": 100}]})
        e = self.els()
        frame = next(x for x in e.values() if x["type"] == "frame" and not x.get("isDeleted"))
        self.assertEqual(frame["customData"]["tbPage"], 1)
        c = r["structuredContent"]["created"]["c"]
        self.assertEqual(e[c]["frameId"], frame["id"])
        arr = next(x for x in e.values() if x["type"] == "arrow" and (x.get("endBinding") or {}).get("elementId") == c)
        self.assertIn(arr["id"], [b["id"] for b in e[self.b]["boundElements"]], "기존 상자 B 에 화살표가 이어졌다")
        img = next(x for x in e.values() if x["type"] == "image")
        self.assertEqual((round(img["width"]), round(img["height"])), (100, 150), "PNG 크기 비율")
        st, f = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}/files/{img['fileId']}", self.h)
        self.assertEqual(st, 200)
        idx = [x["index"] for x in e.values()]
        self.assertEqual(len(set(idx)), len(idx), "index 가 겹치지 않는다")
        text = self.m.ok("board_read", {"page": 1})["content"][0]["text"]
        self.assertIn('[장 1] "둘째"', text)
        self.assertIn('"B" → "C"', text)
        self.assertTrue(self.m.tool("board_add", {"items": [{"kind": "arrow", "from": "zz", "to": self.b}]})["isError"])
        self.assertTrue(self.m.tool("board_add", {"items": [{"kind": "image", "path": "/etc/passwd"}]})["isError"])
        out = Path(self.tmp.name) / "ex"
        p = self.m.ok("board_export", {"out_dir": str(out)})["structuredContent"]
        doc = json.loads(Path(p["path"]).read_text())
        self.assertEqual(doc["type"], "excalidraw")
        self.assertIn(img["fileId"], doc["files"])
        # board list, creating, site list (links), closing
        nb = self.m.ok("board_create", {"title": "둘"})["structuredContent"]["board"]
        self.assertIn(nb, self.m.ok("board_list", {})["content"][0]["text"])
        self.assertIn(f"보드 {self.bid}", self.m.ok("board_read", {})["content"][0]["text"], "board 를 생략하면 사이트의 첫 보드")
        sites = self.m.ok("site_list", {})["structuredContent"]["sites"]
        self.assertEqual(sites[0]["status"], "running")
        self.m.ok("site_close", {"site": self.sid})
        self.assertEqual(self.state(self.sid)["status"], "stopped")
        self.assertTrue(self.m.tool("board_read", {"site": self.sid})["isError"])

    def test_r2_versions_in_text_protect_human_edits(self):
        """Edits using a version obtained by reading only the content text (without structuredContent). After a human edits only the label, conflict and
        the human's text is kept. Changing text without a base gives refused and the board is unchanged. Moving works without a base."""
        text = self.m.ok("board_read", {})["content"][0]["text"]
        m = re.search(r"\[%s\] 상자 \"A\" @\(-?\d+,-?\d+ \d+×\d+\)  \(v(\d+) · 라벨 v(\d+)\)" % re.escape(self.a), text)
        self.assertIsNotNone(m, text)
        bv, lv = int(m.group(1)), int(m.group(2))
        self.assertRegex(text, r"\[%s\] 연결: \"A\" → \"B\" @\(-?\d+,-?\d+ \d+×\d+\)  \(v\d+\)" % re.escape(self.arrow))
        lab = self.els()[self.a + "-label"]
        human = dict(lab, text="사람 글", originalText="사람 글", version=lab["version"] + 1, versionNonce=11)
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h, {"client": "h", "elements": [human]})
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": bv, "label_base_version": lv, "text": "에이전트 글"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "conflict")
        before = self.els()
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "text": "에이전트 글"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "refused")
        self.assertIn("board_read 를 먼저", r["content"][0]["text"])
        for k in ("fill", "color"):
            r = self.m.ok("board_update", {"changes": [{"id": self.b, k: "#0072B2"}]})
            self.assertEqual(r["structuredContent"]["results"][0]["status"], "refused", k)
        after = self.els()
        self.assertEqual({i: e["version"] for i, e in before.items()}, {i: e["version"] for i, e in after.items()}, "refused 는 아무것도 안 바꾼다")
        self.assertEqual(after[self.a + "-label"]["originalText"], "사람 글")
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "dx": 30}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "ok", "옮기기는 base 없이")
        self.assertRegex(r["content"][0]["text"], r"고침  \(v\d+ · 라벨 v\d+\)")
        # works with the re-read version
        text = self.m.ok("board_read", {})["content"][0]["text"]
        m = re.search(r"\[%s\] 상자 \"사람 글\" @\(-?\d+,-?\d+ \d+×\d+\)  \(v(\d+) · 라벨 v(\d+)\)" % re.escape(self.a), text)
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": int(m.group(1)), "label_base_version": int(m.group(2)),
                                                    "text": "에이전트 글"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "ok")

    def test_r3_label_id_path_is_closed(self):
        """Regression: after a human edits only the label, passing the summary text's N (the box version) for the label id that board_wait showed must not overwrite it.
        Neither text nor colour is changed through a label id (refused, pointing to the box id)."""
        n, m = self.vers(self.a)
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]
        lab = self.els()[self.a + "-label"]
        human = dict(lab, text="사람 글", originalText="사람 글", version=lab["version"] + 1, versionNonce=77)
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h, {"client": "h", "elements": [human]})
        w = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 10})["content"][0]["text"]
        self.assertIn(f"[{self.a}-label]", w, "board_wait 는 라벨 id 를 보여 준다")
        before = self.els()
        for extra in ({"text": "AI 가 덮음"}, {"color": "#ff0000"}, {"fill": "#00ff00"}, {"text": "x", "label_base_version": m}):
            r = self.m.ok("board_update", {"changes": [{"id": self.a + "-label", "base_version": n, **extra}]})
            res = r["structuredContent"]["results"][0]
            self.assertEqual((res["status"], res["error"]), ("refused", "label_id"), extra)
            self.assertIn(f"상자 id [{self.a}]", r["content"][0]["text"])
        after = self.els()
        self.assertEqual(after[self.a + "-label"]["originalText"], "사람 글", "사람의 글이 남는다")
        self.assertEqual((after[self.a]["strokeColor"], after[self.a]["backgroundColor"]),
                         (before[self.a]["strokeColor"], before[self.a]["backgroundColor"]), "상자 색도 그대로")
        self.assertEqual({i: x["version"] for i, x in before.items()}, {i: x["version"] for i, x in after.items()})
        # doing the same with the box id + the old version gives conflict and says what changed
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": n, "label_base_version": m, "text": "AI 가 덮음"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "conflict")
        self.assertIn('라벨이 바뀌었다(지금 라벨 v%d, 글 "사람 글"' % (m + 1), r["content"][0]["text"])
        # colour only (a free text with no label) -- without a base it is refused
        self.m.ok("board_add", {"items": [{"kind": "text", "ref": "t", "text": "메모"}]})
        tid = [i["id"] for i in self.m.ok("board_read", {})["structuredContent"]["items"] if i.get("kind") == "text"][0]
        self.assertEqual(self.m.ok("board_update", {"changes": [{"id": tid, "color": "#ff0000"}]})["structuredContent"]["results"][0]["status"],
                         "refused")

    def test_r3_bad_version_and_noop(self):
        """'v2', '2', 2.5, and true give refused (bad_version), not conflict -- so it never loops on conflict forever. A change that alters nothing does not raise the version."""
        n, m = self.vers(self.a)
        for bad in ("v%d" % n, str(n), n + 0.5, True):
            r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": bad, "label_base_version": m, "text": "x"}]})
            res = r["structuredContent"]["results"][0]
            self.assertEqual((res["status"], res["error"]), ("refused", "bad_version"), bad)
            self.assertIn("정수", r["content"][0]["text"])
        before = self.els()
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": n, "label_base_version": m, "text": "A"}]})   # the same text
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "ok")
        self.assertEqual({i: x["version"] for i, x in before.items()}, {i: x["version"] for i, x in self.els().items()}, "판이 그대로")
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": n, "dx": 0}]})
        self.assertEqual({i: x["version"] for i, x in before.items()}, {i: x["version"] for i, x in self.els().items()})

    def test_human_label_edit_is_never_overwritten(self):
        """A human leaves the box alone and edits only the label (only the label version goes up). If the agent edits using board_read's version,
        it is a conflict (when label_base_version is given); if only base_version is given without label_base_version, nothing is changed and it explains."""
        read = self.m.ok("board_read", {})["structuredContent"]
        item = next(i for i in read["items"] if i["id"] == self.a)
        self.assertEqual((item["version"], item["label_version"]) != (None, None), True)
        lab = self.els()[self.a + "-label"]
        human = dict(lab, text="사람이 고친 라벨", originalText="사람이 고친 라벨", version=lab["version"] + 1, versionNonce=9)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h, {"client": "h", "elements": [human]})[0], 200)
        self.assertEqual(self.els()[self.a]["version"], item["version"], "상자 version 은 그대로(라벨만 오름)")
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": item["version"], "label_base_version": item["label_version"],
                                                    "text": "AI 라벨"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "conflict")
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": item["version"], "text": "AI 라벨"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "refused")
        self.assertIn("label_base_version", r["content"][0]["text"])
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": item["version"], "color": "#D55E00"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "refused", "색도 라벨을 고친다")
        self.assertEqual(self.els()[self.a + "-label"]["originalText"], "사람이 고친 라벨", "사람의 라벨이 남는다")
        # a label id is not accepted -- it points to the box id
        r = self.m.ok("board_update", {"changes": [{"id": self.a + "-label", "base_version": lab["version"], "text": "AI 라벨"}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "refused")
        # moving alone works with just the box base_version (the label text is left as it is now and just follows the position)
        r = self.m.ok("board_update", {"changes": [{"id": self.a, "base_version": item["version"], "dy": 50}]})
        self.assertEqual(r["structuredContent"]["results"][0]["status"], "ok")
        self.assertEqual(self.els()[self.a + "-label"]["originalText"], "사람이 고친 라벨")

    def test_box_text_is_accepted_as_label(self):
        """A box item given `text` instead of `label` keeps its words (agents often send `text`)."""
        r = self.m.ok("board_add", {"items": [{"kind": "box", "ref": "t", "text": "alias label"}]})
        bid = r["structuredContent"]["refs"]["t"] if "refs" in r.get("structuredContent", {}) else None
        self.assertIn('"alias label"', r["content"][0]["text"])
        if bid:
            self.assertEqual(self.els()[bid + "-label"]["originalText"], "alias label")

    def test_view_board_read_elements(self):
        r = self.m.ok("board_read", {"format": "elements", "ids": [self.a]})
        self.assertNotIn("structuredContent", r, "같은 JSON 을 두 번 싣지 않는다(r1 F1)")
        text = r["content"][0]["text"]
        doc = json.loads(text.split("\n", 1)[1])
        self.assertEqual({e["id"] for e in doc["elements"]}, {self.a, self.a + "-label"})
        self.assertTrue(self.m.tool("board_read", {"bogus": 1})["isError"], "모르는 칸은 거절(r1 사소함 3)")


HANGUL = re.compile("[\uac00-\ud7a3]")


def _strings(o):
    """Every string value (and key) inside a JSON value."""
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def _ink(i, x, y, version=1, w=60, updated=1):
    return {"id": "hs%03d" % i, "type": "freedraw", "x": x, "y": y, "width": w, "height": 20, "angle": 0, "isDeleted": False,
            "points": [[0, 0], [w / 2, 10], [w, 20]], "pressures": [], "simulatePressure": True, "version": version,
            "versionNonce": 1000 + i * 7 + version, "strokeColor": "#1e1e1e", "index": None, "updated": updated}


class Wp1(Base):
    """09 plan WP-1: positions in the summary, arrow heads / lines, refusing unknown fields, the board_wait settle window,
    per-client caps + progress, and the agent cursor (placed outside the group, hidden when the MCP process ends)."""

    def setUp(self):
        super().setUp()
        self.m = self.mcp()
        self.m.init()
        o = self.m.ok("site_open", {"tunnel": "none", "title": "러프"}, timeout=120)["structuredContent"]
        self.sid, self.bid = o["site"], o["board"]
        self.port, self.h = self.human(self.sid)
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/presence", self.h, {"client": "hum1", "pointer": {"x": 0, "y": 0}})

    def board(self):
        return raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}", self.h)[1]

    def put(self, els):
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h,
                             {"client": "hum1", "elements": els})[0], 200)

    def agent_pointer(self):
        st, d = raw(self.port, "GET", f"/api/whiteboard/boards/{self.bid}/poll?since=0&client=watch&pver=-1&wait=0.1", self.h)
        ag = [p for p in d.get("presence") or [] if p.get("agent")]
        return next((p["pointer"] for p in ag if p.get("pointer")), None)   # an agent that left stays listed (pointer null) for up to 30 s

    def test_heads_line_and_unknown_fields(self):
        """Criterion 2: head none → no arrowheads, both → two; an unknown field (heads) is refused with the allowed list and nothing is written."""
        r = self.m.ok("board_add", {"items": [{"kind": "box", "ref": "a", "label": "A"}, {"kind": "box", "ref": "b", "label": "B", "x": 300},
                                              {"kind": "arrow", "ref": "n", "from": "a", "to": "b", "head": "none"},
                                              {"kind": "arrow", "ref": "w", "from": "b", "to": "a", "head": "both"},
                                              {"kind": "line", "ref": "l", "points": [[0, 200], [100, 260], [300, 200]]}]})
        c = r["structuredContent"]["created"]
        els = {e["id"]: e for e in self.board()["elements"]}
        self.assertIsNone(els[c["n"]]["endArrowhead"])
        self.assertIsNone(els[c["n"]]["startArrowhead"])
        self.assertEqual((els[c["w"]]["startArrowhead"], els[c["w"]]["endArrowhead"]), ("arrow", "arrow"))
        ln = els[c["l"]]
        self.assertEqual((ln["type"], len(ln["points"]), ln["startBinding"], ln["endArrowhead"]), ("line", 3, None, None))
        text = self.m.ok("board_read", {})["content"][0]["text"]
        self.assertRegex(text, r'\[%s\] 연결: "A" — "B" @\(' % re.escape(c["n"]))
        self.assertRegex(text, r'\[%s\] 연결: "B" ↔ "A" @\(' % re.escape(c["w"]))
        seq = self.board()["seq"]
        bad = self.m.tool("board_add", {"items": [{"kind": "box", "ref": "x", "label": "X"},
                                                  {"kind": "arrow", "from": "x", "to": "x", "heads": "none"}]})
        self.assertTrue(bad["isError"])
        msg = bad["content"][0]["text"]
        self.assertIn("heads", msg)
        self.assertIn("head", msg.split("쓸 수 있는 칸:")[1])
        self.assertEqual(self.board()["seq"], seq, "nothing was written")
        self.assertTrue(self.m.tool("board_add", {"items": [{"kind": "arrow", "from": {"x": 0, "y": 0}, "to": {"x": 9, "y": 9}, "head": "up"}]})["isError"])
        self.assertTrue(self.m.tool("board_add", {"items": [{"kind": "line", "points": [[0, 0]]}]})["isError"])
        # r1: head:null is not "none" — refused instead of silently drawing the default arrowhead
        self.assertTrue(self.m.tool("board_add", {"items": [{"kind": "arrow", "from": {"x": -30, "y": 100}, "to": {"x": 0, "y": 0}, "head": None}]})["isError"])
        # r1: arrows do not bind to a line (new ref or existing id)
        r = self.m.tool("board_add", {"items": [{"kind": "line", "ref": "l2", "points": [[0, 0], [50, 0]]}, {"kind": "arrow", "from": "l2", "to": {"x": 90, "y": 90}}]})
        self.assertTrue(r["isError"])
        self.assertIn("line", r["content"][0]["text"])
        self.assertTrue(self.m.tool("board_add", {"items": [{"kind": "arrow", "from": c["l"], "to": {"x": 90, "y": 90}}]})["isError"])
        self.assertEqual(self.board()["seq"], seq)

    def test_wait_settles_and_folds_smoothing_echo(self):
        """Criterion 3: 18 strokes at < 1 s intervals, each re-written once by smoothing → idle_ms 3000 returns once,
        3 s after the last stroke, with one ink line and no resized / edited lines."""
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]
        done = []

        def draw():
            time.sleep(0.5)
            for i in range(18):
                self.put([_ink(i, 100 + (i % 6) * 70, 100 + (i // 6) * 60)])
                time.sleep(0.15)
                self.put([_ink(i, 100 + (i % 6) * 70, 100 + (i // 6) * 60, version=2, w=62)])   # smoothing re-write
                last = time.time()
                time.sleep(0.5)
            done.append(last)
        threading.Thread(target=draw, daemon=True).start()
        r = self.m.ok("board_wait", {"since_seq": seq, "idle_ms": 3000, "timeout_s": 60}, timeout=90)
        back = time.time()
        self.assertTrue(done, "returned before the drawing ended")
        self.assertGreaterEqual(back - done[0], 2.8)
        text = r["content"][0]["text"]
        self.assertIn("손글씨 획 18개가 늘었다 @(", text)
        self.assertRegex(text.split("\n")[0], r"^길동 가 획 18 을 더함 @\(100,100 \d+×\d+\)$")
        self.assertNotIn("크기", text)
        self.assertNotIn("고침", text)
        self.assertEqual(r["structuredContent"]["changes"][0]["count"], 18)
        # idle 0: the first stroke wakes it; the smoothing echo of that stroke within 2 s does not wake the next wait
        seq = r["structuredContent"]["seq"]

        def one():
            time.sleep(0.5)
            self.put([_ink(50, 0, 600)])
        threading.Thread(target=one, daemon=True).start()
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 10})
        self.assertIn("손글씨 획 1개가 늘었다", r["content"][0]["text"])
        self.put([_ink(50, 0, 600, version=2, w=64)])
        r = self.m.ok("board_wait", {"since_seq": r["structuredContent"]["seq"], "timeout_s": 2})
        self.assertTrue(r["structuredContent"].get("timeout"), r["content"][0]["text"])

    def test_wait_kinds(self):
        """kinds:["text"] ignores ink and returns on text."""
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]

        def act():
            time.sleep(0.4)
            self.put([_ink(1, 0, 0)])
            time.sleep(0.6)
            self.put([{"id": "ht1", "type": "text", "x": 0, "y": 100, "width": 40, "height": 25, "angle": 0, "text": "메모",
                       "originalText": "메모", "fontSize": 20, "fontFamily": 6, "isDeleted": False, "version": 1, "versionNonce": 5,
                       "index": None, "containerId": None}])
        threading.Thread(target=act, daemon=True).start()
        r = self.m.ok("board_wait", {"since_seq": seq, "kinds": ["text"], "timeout_s": 10})
        text = r["content"][0]["text"]
        self.assertIn('"메모"', text)
        self.assertNotIn("손글씨", text)
        self.assertTrue(self.m.tool("board_wait", {"kinds": ["request"], "timeout_s": 1})["isError"])

    def test_wait_ignores_job_events(self):
        """WP-2 tab-render jobs publish 'job' events on /poll — they must not wake board_wait nor show up as human changes."""
        out = subprocess.run([sys.executable, str(CLI), "agent", "add", self.sid, "--name", "Other", "--json"], env=self.env,
                             capture_output=True, text=True).stdout
        tok = json.loads(out)["token"]
        time.sleep(1.2)   # the supervisor re-reads state.json
        ah = {"Authorization": f"Bearer {tok}", "X-Whiteboard": "1", "Content-Type": "application/json"}
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]

        def jobs():
            for _ in range(3):
                time.sleep(0.5)
                st, _ = raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/jobs", ah, {"client": "ag-other", "kind": "render", "args": {}})
                self.assertEqual(st, 201)
        th = threading.Thread(target=jobs, daemon=True)
        th.start()
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 4})
        th.join()
        self.assertTrue(r["structuredContent"].get("timeout"), r["content"][0]["text"])
        self.assertEqual(r["structuredContent"]["changes"], [])
        # a human edit after the jobs still wakes it, and only that edit is reported
        threading.Thread(target=lambda: (time.sleep(0.5), self.put([_ink(7, 0, 0)])), daemon=True).start()
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 10})
        self.assertEqual([c["action"] for c in r["structuredContent"]["changes"]], ["손글씨"])

    def test_wait_cap_env_and_progress(self):
        """Criterion 4 (fast form): CERA_WAIT_MAX caps timeout_s; progress notifications every 20 s carry the client's token."""
        m = self.mcp({**self.env, "CERA_WAIT_MAX": "2"})
        m.init("codex-mcp-client")
        t0 = time.time()
        r = m.ok("board_wait", {"timeout_s": 600})
        self.assertTrue(r["structuredContent"]["timeout"])
        self.assertLess(time.time() - t0, 6)
        m = self.m   # claude-code, no override
        t0 = time.time()
        m.n += 1
        mid = m.n
        m.send({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                "params": {"name": "board_wait", "arguments": {"timeout_s": 23}, "_meta": {"progressToken": "pt-1"}}})
        got, deadline = [], time.time() + 60
        while True:
            msg = m.q.get(timeout=max(0.1, deadline - time.time()))
            if msg.get("method") == "notifications/progress":
                got.append((time.time() - t0, msg["params"]))
            if msg.get("id") == mid:
                break
        self.assertTrue(msg["result"]["structuredContent"]["timeout"])
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0][1]["progressToken"], "pt-1")
        self.assertEqual(got[0][1]["total"], 23)

    # ---- review r1 regressions (04_review_WP-1_neon_r1 · opus_r1)
    def presence(self, client, name):
        h = dict(self.h, **{"X-Tb-Name": urllib.parse.quote(name)})
        raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/presence", h, {"client": client, "pointer": {"x": 0, "y": 0}})

    def test_r1_replay_old_seq_uses_board_as_seen(self):
        """neon: since_seq older than now compared with the current board → an added stroke showed as 'edited', a delete vanished."""
        seq0 = self.m.ok("board_read", {})["structuredContent"]["seq"]
        self.put([_ink(1, 0, 0)])
        r = self.m.ok("board_wait", {"since_seq": seq0, "timeout_s": 5})
        self.assertIn("손글씨 획 1개가 늘었다", r["content"][0]["text"])
        self.assertNotIn("고침", r["content"][0]["text"])
        seq1 = self.m.ok("board_read", {})["structuredContent"]["seq"]
        self.put([dict(_ink(1, 0, 0), version=5, versionNonce=9, isDeleted=True)])
        r = self.m.ok("board_wait", {"since_seq": seq1, "timeout_s": 5})
        self.assertIn("를 지움", r["content"][0]["text"])
        self.assertFalse(r["structuredContent"].get("timeout"))

    def test_r2_human_moves_or_deletes_agent_box_after_replay_seq(self):
        """opus r2: board_read → board_add → board_wait(since_seq=read's seq): a person moving the agent's box is 'moved'
        (not 'added'), and deleting it is reported (not dropped). Same for board_update / board_delete writes."""
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]
        add = self.m.ok("board_add", {"items": [{"kind": "box", "ref": "a", "label": "A"}, {"kind": "box", "ref": "b", "label": "B", "x": 300}]})
        a, b = add["structuredContent"]["created"]["a"], add["structuredContent"]["created"]["b"]
        els = {e["id"]: e for e in self.board()["elements"]}
        self.put([dict(els[a], x=els[a]["x"] + 200, version=els[a]["version"] + 1, versionNonce=77)])
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 5})
        text = r["content"][0]["text"]
        self.assertIn(f"[{a}] 상자 를 옮김", text)
        self.assertNotIn("넣음", text)
        self.assertNotIn("더함", text)
        seq2 = r["structuredContent"]["seq"]
        self.m.ok("board_update", {"changes": [{"id": b, "dx": 40}]})   # the agent's own patch
        els = {e["id"]: e for e in self.board()["elements"]}
        self.put([dict(els[b], isDeleted=True, version=els[b]["version"] + 1, versionNonce=78)])
        r = self.m.ok("board_wait", {"since_seq": seq2, "timeout_s": 5})
        self.assertFalse(r["structuredContent"].get("timeout"), r["content"][0]["text"])
        self.assertIn(f"[{b}] 상자 를 지움", r["content"][0]["text"])
        self.assertNotIn("옮김", r["content"][0]["text"], "the agent's own dx is not reported as a person's move")

    def test_r1_cap_while_drawing_is_flagged(self):
        """neon: reaching the cap while the person is still drawing must not look like a settled sketch."""
        m = self.mcp({**self.env, "CERA_WAIT_MAX": "3"})
        m.init("codex-mcp-client")
        seq = m.ok("board_read", {})["structuredContent"]["seq"]

        def draw():
            for i in range(10):
                self.put([_ink(300 + i, i * 40, 0)])
                time.sleep(0.5)
        th = threading.Thread(target=draw, daemon=True)
        th.start()
        t0 = time.time()
        r = m.ok("board_wait", {"since_seq": seq, "idle_ms": 3000, "timeout_s": 600})
        self.assertLess(time.time() - t0, 4.5)
        th.join()
        self.assertIs(r["structuredContent"].get("settled"), False)
        self.assertIn("아직 그리는 중", r["content"][0]["text"])
        self.assertIn(f"since_seq={r['structuredContent']['seq']}", r["content"][0]["text"])   # r2: says which seq to use

    def test_r1_echo_by_element_clock_not_read_time(self):
        """opus: stroke → 100 ms smoothing echo → reported → the agent thinks 3 s → next wait must not wake on the echo."""
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]
        self.put([_ink(60, 0, 600, updated=50_000)])
        time.sleep(0.1)
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 5})
        self.assertIn("손글씨 획 1개가 늘었다", r["content"][0]["text"])
        self.put([_ink(60, 0, 600, version=2, w=64, updated=50_100)])
        time.sleep(3)
        r = self.m.ok("board_wait", {"since_seq": r["structuredContent"]["seq"], "timeout_s": 2})
        self.assertTrue(r["structuredContent"].get("timeout"), r["content"][0]["text"])

    def test_r1_echo_needs_same_connection_and_place(self):
        """neon: another connection with the same display name moving the stroke is a real change, not an echo."""
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]
        self.put([_ink(61, 0, 0, updated=70_000)])
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 5})
        self.presence("hum2", "길동")
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h,
                             {"client": "hum2", "elements": [_ink(61, 500, 0, version=2, updated=70_500)]})[0], 200)
        r = self.m.ok("board_wait", {"since_seq": r["structuredContent"]["seq"], "timeout_s": 5})
        self.assertIn("옮김", r["content"][0]["text"])

    def test_r1_window_keeps_first_author_and_names_later_editor(self):
        """neon: in the settle window the last editor replaced the author ('수진 added …'). Now 길동 added, 수진 edited after."""
        self.presence("hum3", "수진")
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]

        def act():
            time.sleep(0.3)
            self.put([_ink(62, 0, 0)])
            time.sleep(0.5)
            raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", self.h,
                {"client": "hum3", "elements": [_ink(62, 300, 0, version=2, updated=99_000)]})
        threading.Thread(target=act, daemon=True).start()
        r = self.m.ok("board_wait", {"since_seq": seq, "idle_ms": 1500, "timeout_s": 10})
        text = r["content"][0]["text"]
        self.assertTrue(text.startswith("길동 가 획 1 을 더함"), text)
        self.assertIn("뒤에 수진 가 고침", text)

    def test_r1_other_agents_do_not_wake(self):
        """neon: another agent's write (presence agent:true) is not a person's edit."""
        out = subprocess.run([sys.executable, str(CLI), "agent", "add", self.sid, "--name", "Other", "--json"], env=self.env,
                             capture_output=True, text=True).stdout
        ah = {"Authorization": f"Bearer {json.loads(out)['token']}", "X-Whiteboard": "1", "Content-Type": "application/json"}
        time.sleep(1.2)
        seq = self.m.ok("board_read", {})["structuredContent"]["seq"]

        def act():
            time.sleep(0.4)
            raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/presence", ah, {"client": "otherbot1", "pointer": {"x": 1, "y": 1}})
            time.sleep(0.3)
            raw(self.port, "POST", f"/api/whiteboard/boards/{self.bid}/elements", ah, {"client": "otherbot1", "elements": [_ink(63, 0, 0)]})
        threading.Thread(target=act, daemon=True).start()
        r = self.m.ok("board_wait", {"since_seq": seq, "timeout_s": 3})
        self.assertTrue(r["structuredContent"].get("timeout"), r["content"][0]["text"])

    def test_r1_cursor_cleared_on_close_during_wait_and_on_kill(self):
        """opus: stdin closed while two board_waits run → cursor gone within 2 s (not after the joins). neon: SIGKILL → watchdog clears it."""
        self.m.ok("board_add", {"items": [{"kind": "text", "text": "a"}], "at": {"x": 0, "y": 0}})
        for k in (1, 2):
            self.m.send({"jsonrpc": "2.0", "id": 900 + k, "method": "tools/call", "params": {"name": "board_wait", "arguments": {"timeout_s": 30}}})
        time.sleep(1.0)
        self.assertIsNotNone(self.agent_pointer())
        self.m.p.stdin.close()
        t0 = time.time()
        while self.agent_pointer() is not None and time.time() - t0 < 6:
            time.sleep(0.1)
        self.assertLess(time.time() - t0, 2.0)
        m = self.mcp()
        m.init()
        m.ok("board_add", {"items": [{"kind": "text", "text": "b"}], "at": {"x": 0, "y": 900}})
        self.assertIsNotNone(self.agent_pointer())
        m.p.kill()
        t0 = time.time()
        while self.agent_pointer() is not None and time.time() - t0 < 6:
            time.sleep(0.1)
        self.assertLess(time.time() - t0, 2.0)

    def test_cursor_outside_group_and_cleared_on_exit(self):
        """Criterion 5: after board_add the agent cursor sits 24 px outside the top-left of the group; it is gone within 2 s of the MCP process ending."""
        r = self.m.ok("board_add", {"items": [{"kind": "box", "label": "A", "x": 0, "y": 0}], "at": {"x": 500, "y": 400}})
        self.assertEqual(self.agent_pointer()["x"], 476)
        self.assertEqual(self.agent_pointer()["y"], 376)
        self.m.p.stdin.close()
        t0 = time.time()
        while self.agent_pointer() is not None and time.time() - t0 < 5:
            time.sleep(0.1)
        self.assertLess(time.time() - t0, 2.0)
        # SIGTERM path
        m = self.mcp()
        m.init()
        m.ok("board_add", {"items": [{"kind": "text", "text": "x"}], "at": {"x": 0, "y": 900}})
        self.assertIsNotNone(self.agent_pointer())
        m.p.terminate()
        t0 = time.time()
        while self.agent_pointer() is not None and time.time() - t0 < 5:
            time.sleep(0.1)
        self.assertLess(time.time() - t0, 2.0)


class SlowPoll(Base):
    """neon r1: a board server that answers /poll late must not push board_wait past the client's cap (HTTP timeout bounded by the deadline)."""

    def test_stuck_poll_respects_cap(self):
        import http.server

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if "/poll" in self.path:
                    time.sleep(8)
                body = json.dumps({"elements": [], "seq": 0, "epoch": "e1", "meta": {"title": "x"}, "events": []}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            m = self.mcp({**self.env, "CERA_URL": f"http://127.0.0.1:{srv.server_address[1]}", "CERA_TOKEN": "tb_ag_fake", "CERA_WAIT_MAX": "3"})
            m.init("codex-mcp-client")
            t0 = time.time()
            r = m.ok("board_wait", {"board": "abcdefgh12", "timeout_s": 600}, timeout=30)
            self.assertLess(time.time() - t0, 5.0)
            self.assertTrue(r["structuredContent"]["timeout"])
        finally:
            srv.shutdown()
            srv.server_close()


class WaitCap(unittest.TestCase):
    """B7: per-client cap on one board_wait (criterion 4 runs live in the WP-1 report; 50 / 55 s is too slow for the suite)."""

    def test_caps(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cera_tb_mcp_cap", HERE / "server" / "tb_mcp.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = os.environ.pop("CERA_WAIT_MAX", None)
        try:
            self.assertEqual(mod.wait_cap("claude-code"), 600)
            self.assertEqual(mod.wait_cap("codex-mcp-client"), 50)
            self.assertEqual(mod.wait_cap("antigravity"), 55)
            self.assertEqual(mod.wait_cap(None), 55)
            self.assertEqual(mod.wait_cap("claude-code-preview"), 55)   # r1: exact names only
            self.assertEqual(mod.wait_cap("codex-other"), 55)
            os.environ["CERA_WAIT_MAX"] = "90"
            self.assertEqual(mod.wait_cap("codex-mcp-client"), 90)
            os.environ["CERA_WAIT_MAX"] = "abc"
            self.assertEqual(mod.wait_cap("codex-mcp-client"), 50)
        finally:
            os.environ.pop("CERA_WAIT_MAX", None)
            if old is not None:
                os.environ["CERA_WAIT_MAX"] = old


class English(Base):
    """CERA_LANG=en: everything the server tells the model is English (board data the test wrote is excepted)."""

    def setUp(self):
        super().setUp()
        self.env["CERA_LANG"] = "en"

    def test_handshake_tools_and_board_read_have_no_korean(self):
        m = self.mcp()
        res = m.init()["result"]
        self.assertIn("Ask the user first", res["instructions"])
        self.assertIsNone(HANGUL.search(res["instructions"]), res["instructions"])
        self.assertIsNone(HANGUL.search(res["serverInfo"]["title"]))
        tools = m.call("tools/list")["result"]["tools"]
        self.assertEqual(len(tools), 12)
        for t in tools:
            for s in _strings(t):
                self.assertIsNone(HANGUL.search(s), (t["name"], s))
        self.assertIn("only when the user wants a public link", next(t for t in tools if t["name"] == "site_open")["description"])
        opened = m.ok("site_open", {"tunnel": "none", "title": "Flow"}, timeout=120)
        self.assertIsNone(HANGUL.search(opened["content"][0]["text"]), opened["content"][0]["text"])
        self.assertIn("Opened site", opened["content"][0]["text"])
        sid = opened["structuredContent"]["site"]
        add = m.ok("board_add", {"items": [{"kind": "frame_page", "title": "Intro"}, {"kind": "box", "ref": "a", "label": "A"},
                                           {"kind": "box", "ref": "b", "label": "B"},
                                           {"kind": "arrow", "from": "a", "to": "b", "label": "yes"}]})
        created = add["structuredContent"]["created"]
        add_text = add["content"][0]["text"]
        self.assertIn("Added 7 item(s)", add_text)
        self.assertRegex(add_text, r'\[%s\] box "A" @\(-?\d+,-?\d+ \d+×\d+\)  \(v\d+ · label v1\)' % re.escape(created["a"]))
        summary = m.ok("board_read", {})["content"][0]["text"]
        self.assertIn("Board summary", summary)
        self.assertIn('"A" → "B" (label "yes")', summary)
        # the only Korean allowed is the page label '장 1' (board data the tool wrote itself)
        self.assertIsNone(HANGUL.search(summary.replace("장 1", "")), summary)
        # refusals and conflict-style messages
        upd = m.ok("board_update", {"changes": [{"id": created["a"], "text": "A2"}]})["content"][0]["text"]
        self.assertIn("base_version is required", upd)
        self.assertIsNone(HANGUL.search(upd), upd)
        err = m.tool("board_read", {"board": "../../x"})
        self.assertTrue(err["isError"])
        self.assertIn("board id format is wrong", err["content"][0]["text"])
        self.assertIsNone(HANGUL.search(err["content"][0]["text"]))
        bad = m.tool("board_add", {"items": [{"kind": "math", "latex": "x"}]})
        self.assertIsNone(HANGUL.search(bad["content"][0]["text"]), bad["content"][0]["text"])
        unk = m.tool("board_read", {"nope": 1})
        self.assertIsNone(HANGUL.search(unk["content"][0]["text"]), unk["content"][0]["text"])
        wait = m.ok("board_wait", {"timeout_s": 1})["content"][0]["text"]
        self.assertIn("Nothing changed", wait)
        m.close()
        self.assertEqual(self.state(sid)["agents"], [])


class AgentAddUrl(Base):
    def test_agent_add_prints_url_and_token_that_work(self):
        """The two lines CERA_URL and CERA_TOKEN from the `agent add` output, put in as-is, start a remote MCP on which board_add works."""
        p = subprocess.run([sys.executable, str(CLI), "start", "--tunnel", "none", "--title", "u9", "--json"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        sid = json.loads(p.stdout)["id"]
        out = subprocess.run([sys.executable, str(CLI), "agent", "add", sid, "--name", "Remote"], env=self.env, capture_output=True,
                             text=True).stdout
        kv = dict(re.findall(r"^\s*(CERA_URL|CERA_TOKEN)=(\S+)$", out, re.M))
        self.assertEqual(kv["CERA_URL"], self.state(sid)["local_url"])
        self.assertTrue(kv["CERA_TOKEN"].startswith("tb_ag_"))
        self.assertIn("이 컴퓨터에서만", out)
        j = json.loads(subprocess.run([sys.executable, str(CLI), "agent", "add", sid, "--name", "J", "--json"], env=self.env,
                                      capture_output=True, text=True).stdout)
        self.assertEqual(j["url"], self.state(sid)["local_url"])
        time.sleep(1.2)
        m = self.mcp({**self.env, "CERA_HOME": str(Path(self.tmp.name) / "elsewhere"), **kv})
        m.init()
        self.assertEqual(len(m.ok("board_add", {"items": [{"kind": "box", "label": "U9"}]})["structuredContent"]["ids"]), 2)

    def test_url_for_modes(self):
        ag = sys.modules.get("cera_agents") or _tb_mcp().agents
        self.assertEqual(ag.url_for({"local_url": "http://127.0.0.1:1", "public_url": "https://x.trycloudflare.com"})[0],
                         "https://x.trycloudflare.com")
        u, note = ag.url_for({"local_url": "http://127.0.0.1:1", "public_url": "https://board.example.com", "mode": "personal"})
        self.assertEqual(u, "http://127.0.0.1:1")
        self.assertIn("CF_ACCESS_CLIENT_ID", note)


class Remote(Base):
    def test_remote_mode_hides_site_tools(self):
        """An MCP started in a different CERA_HOME with CERA_URL + CERA_TOKEN: no site_* tools, and board_add works."""
        p = subprocess.run([sys.executable, str(CLI), "start", "--tunnel", "none", "--title", "원격", "--json"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        sid = json.loads(p.stdout)["id"]
        tok = json.loads(subprocess.run([sys.executable, str(CLI), "agent", "add", sid, "--name", "Remote", "--json"], env=self.env,
                                        capture_output=True, text=True).stdout)["token"]
        st = self.state(sid)
        other = Path(self.tmp.name) / "other-home"
        env = {**self.env, "CERA_HOME": str(other), "CERA_URL": st["local_url"], "CERA_TOKEN": tok}
        time.sleep(1.2)   # time for the supervisor to carry the token
        m = self.mcp(env)
        m.init("some-remote-agent")
        names = [t["name"] for t in m.call("tools/list")["result"]["tools"]]
        self.assertFalse([n for n in names if n.startswith("site_")])
        self.assertEqual(len(names), 9)
        self.assertNotIn("site", json.dumps([t["inputSchema"] for t in m.call("tools/list")["result"]["tools"]]))
        self.assertEqual(m.call("tools/call", {"name": "site_open", "arguments": {}})["error"]["code"], -32602)
        r = m.ok("board_add", {"items": [{"kind": "box", "label": "원격 상자"}]})
        self.assertEqual(len(r["structuredContent"]["ids"]), 2)
        self.assertIn('"원격 상자"', m.ok("board_read", {})["content"][0]["text"])
        port, h = self.human(sid)
        _, pres = raw(port, "GET", f"/api/whiteboard/boards/{st['board']}/poll?since=0&client=h&pver=-1&epoch=&wait=0.2", h)
        self.assertEqual([p["user"] for p in pres["presence"] if p.get("agent")], ["Remote · AI"])
        self.assertFalse(other.exists() and any((other / "sites").glob("*")), "원격 모드는 자기 HOME 에 사이트를 만들지 않는다")


def _tb_mcp():
    import importlib.util
    mod = sys.modules.get("cera_tb_mcp")
    if mod is None:
        spec = importlib.util.spec_from_file_location("cera_tb_mcp", HERE / "server" / "tb_mcp.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["cera_tb_mcp"] = mod
        spec.loader.exec_module(mod)
    return mod


class Size(Base):
    """Even on a large board (400 boxes + 3000 freehand strokes with 200 points each) the response stays within the limit, and if truncated it says how to keep reading."""

    def test_big_board_responses_are_capped(self):
        m = self.mcp()
        m.init()
        o = m.ok("site_open", {"tunnel": "none"}, timeout=120)["structuredContent"]
        sid, bid = o["site"], o["board"]
        port, h = self.human(sid)
        ak = sys.modules.get("cera_agentkit") or _tb_mcp().ak
        els = []
        for i in range(400):
            els += ak.make_box("rectangle", f"상자 {i}", (i % 20) * 200, (i // 20) * 120)
        for i in range(3000):
            st = ak.make_text("x", 0, 0)   # only borrows the frame
            st.update(type="freedraw", points=[[j, (j * 7) % 13] for j in range(200)], pressures=[], simulatePressure=True,
                      x=float(i % 60) * 30, y=3000.0 + (i // 60) * 30)
            for k in ("text", "originalText", "fontSize", "fontFamily", "textAlign", "verticalAlign", "containerId", "autoResize", "lineHeight"):
                st.pop(k, None)
            els.append(st)
        ak.assign_indices([], els)
        for i in range(0, len(els), 700):
            self.assertEqual(raw(port, "POST", f"/api/whiteboard/boards/{bid}/elements", h, {"client": "h", "elements": els[i:i + 700]})[0], 200)
        mod = _tb_mcp()

        def size(res):
            return len(json.dumps(res, ensure_ascii=False).encode())
        r = m.ok("board_read", {})
        self.assertLess(size(r), mod.MAX_RESPONSE + 5000)
        stc = r["structuredContent"]
        self.assertEqual(len(stc["items"]), mod.READ_LIMIT)
        self.assertEqual(stc["truncated"]["items"], 400 - mod.READ_LIMIT)
        self.assertTrue(all("ids" not in g and g["ids_omitted"] for g in stc["strokes"]))
        self.assertIn("줄 더", r["content"][0]["text"])
        r = m.ok("board_read", {"format": "elements", "limit": 2000})
        self.assertLess(size(r), mod.MAX_RESPONSE + 5000)
        head, body = r["content"][0]["text"].split("\n", 1)
        doc = json.loads(body)
        self.assertGreater(doc["truncated"], 0)
        self.assertIn(f"offset={doc['next_offset']}", head)
        self.assertTrue(all("points" not in e for e in doc["elements"] if e["type"] == "freedraw"))
        r2 = m.ok("board_read", {"format": "elements", "offset": doc["next_offset"], "limit": 50})
        doc2 = json.loads(r2["content"][0]["text"].split("\n", 1)[1])
        self.assertEqual(doc2["offset"], doc["next_offset"])
        self.assertFalse({e["id"] for e in doc2["elements"]} & {e["id"] for e in doc["elements"]}, "이어 읽기는 겹치지 않는다")

    def test_cap_result_unit(self):
        mod = _tb_mcp()
        r = mod.cap_result("가" * 200000, {"big": "x" * 10})
        self.assertLessEqual(len(r["content"][0]["text"].encode()), mod.MAX_RESPONSE)
        self.assertIn("잘렸다", r["content"][0]["text"])
        self.assertTrue(r["structuredContent"]["truncated"])
        r = mod.cap_result("작다", None)
        self.assertNotIn("structuredContent", r)


class Cancel(Base):
    def test_cancelled_request_gets_no_response(self):
        """After notifications/cancelled, no reply is sent."""
        m = self.mcp()
        m.init()
        m.ok("site_open", {"tunnel": "none"}, timeout=120)
        m.n += 1
        wid = m.n
        m.send({"jsonrpc": "2.0", "id": wid, "method": "tools/call", "params": {"name": "board_wait", "arguments": {"timeout_s": 60}}})
        time.sleep(0.8)
        m.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": wid, "reason": "test"}})
        self.assertEqual(m.call("ping")["result"], {})
        time.sleep(1.5)   # board_wait notices the cancellation after its next poll (at most the remaining time) and stops -- so this finishes quickly, its wait is not in 1-second steps
        got = []
        while not m.q.empty():
            got.append(m.q.get())
        self.assertFalse([x for x in got if x.get("id") == wid])


class _Capture(threading.Thread):
    """A fake board server -- records the headers it receives and returns an empty board list [] (imitates remote Access)."""

    def __init__(self):
        super().__init__(daemon=True)
        import http.server
        seen = self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append(dict(self.headers))
                body = b"[]"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]

    def run(self):
        self.httpd.serve_forever()


class RemoteAccess(Base):
    def test_service_token_only(self):
        """With only CERA_URL + CF_ACCESS_CLIENT_ID/SECRET (no CERA_TOKEN) it is remote mode: no site_* tools,
        it sends CF-Access-Client-Id/Secret headers and does not send Authorization."""
        cap = _Capture()
        cap.start()
        try:
            env = {**self.env, "CERA_URL": f"http://127.0.0.1:{cap.port}", "CF_ACCESS_CLIENT_ID": "cid.access",
                   "CF_ACCESS_CLIENT_SECRET": "sekret"}
            m = self.mcp(env)
            m.init("codex-mcp-client")
            names = [t["name"] for t in m.call("tools/list")["result"]["tools"]]
            self.assertEqual(len(names), 9)
            self.assertFalse([n for n in names if n.startswith("site_")])
            self.assertIn("보드가 없다", m.ok("board_list", {})["content"][0]["text"])
            h = {k.lower(): v for k, v in cap.seen[-1].items()}
            self.assertEqual((h.get("cf-access-client-id"), h.get("cf-access-client-secret")), ("cid.access", "sekret"))
            self.assertNotIn("authorization", h)
            # with only a URL and no credentials: it starts in remote mode (without touching the local site) but tools return a guidance error
            m2 = self.mcp({**self.env, "CERA_URL": f"http://127.0.0.1:{cap.port}"})
            m2.init()
            self.assertEqual(len(m2.call("tools/list")["result"]["tools"]), 9)
            r = m2.tool("board_list", {})
            self.assertTrue(r["isError"])
            self.assertIn("CF_ACCESS_CLIENT_ID", r["content"][0]["text"])
            m2.close()
            self.assertIn("CERA_URL", b"".join(m2.err).decode())
        finally:
            cap.httpd.shutdown()
            cap.httpd.server_close()


if __name__ == "__main__":
    unittest.main()
