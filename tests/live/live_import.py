"""Live test (Playwright) of import: drop interception, the sheet, merging, new board, image drop, dragging from the library, the phone menu, and view-only.

usage: TMPDIR=<scratchpad>/tmp python3 tests/live/live_import.py --shots <dir> [--dist <web/dist>]
Opens a site with no tunnel in a temporary CERA_HOME and closes it when done (~/.cera is never touched). The name live_* keeps it out of discover.
Covers: the drop bug, new board, image drop, library, phone, Esc/Delete, and the menu at 1024 and 1280."""
from __future__ import annotations

import os as _os_lang

_os_lang.environ["CERA_LANG"] = "ko"   # existing assertions are Korean; set before importing server code or spawning the CLI / MCP

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parents[2]
CLI = HERE / "bin" / "cera"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a5b6d8b10000000049454e44ae426082")

ap = argparse.ArgumentParser()
ap.add_argument("--shots", required=True)
ap.add_argument("--dist", default=str(HERE / "web" / "dist"))
a = ap.parse_args()
shots = Path(a.shots)
shots.mkdir(parents=True, exist_ok=True)


def doc():
    sys.path.insert(0, str(HERE / "server"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("cera_agentkit", HERE / "server" / "agentkit.py")
    ak = importlib.util.module_from_spec(spec)
    sys.modules["cera_agentkit"] = ak
    spec.loader.exec_module(ak)
    a_ = ak.make_box("rectangle", "가져온 A", 0, 0, id="dropA")
    b_ = ak.make_box("ellipse", "가져온 B", 300, 0, id="dropB")
    arr = ak.make_arrow(a_[0], b_[0], "예", id="dropArr")
    img = ak.make_image("oldfile0001", 80, 80, 600, 0, id="dropImg")
    els = a_ + b_ + arr + [img]
    ak.assign_indices([], els)
    return {"type": "excalidraw", "version": 2, "source": "test", "elements": els, "appState": {},
            "files": {"oldfile0001": {"id": "oldfile0001", "mimeType": "image/png", "dataURL": "data:image/png;base64," + base64.b64encode(PNG).decode()}}}


DOC = json.dumps(doc())
LIB = json.dumps({"type": "excalidrawlib", "version": 2, "source": "test", "libraryItems": [
    {"id": "li1", "status": "unpublished", "created": 1, "elements": [
        {"id": "libRect", "type": "rectangle", "x": 0, "y": 0, "width": 120, "height": 60, "angle": 0, "strokeColor": "#1e1e1e",
         "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid", "roughness": 0, "opacity": 100,
         "groupIds": [], "frameId": None, "index": "a0", "roundness": None, "seed": 3, "version": 1, "versionNonce": 9, "isDeleted": False,
         "boundElements": None, "updated": 1, "link": None, "locked": False}]}]})

tmp = Path(tempfile.mkdtemp(prefix="tbimp.", dir=os.environ.get("TMPDIR")))
env = {**os.environ, "CERA_HOME": str(tmp / "home"), "CERA_DIST": a.dist}
out = subprocess.run([sys.executable, str(CLI), "start", "--tunnel", "none", "--json", "--title", "가져오기 시험", "--view-link"],
                     env=env, capture_output=True, text=True, check=True)
site = json.loads(out.stdout)
st = json.loads((tmp / "home" / "sites" / site["id"] / "state.json").read_text())
BASE, BID, EK = st["local_url"], st["board"], st["edit_key"]
EDIT = f"{BASE}/?k={EK}#b={BID}"
VIEW = f"{BASE}/?k={st['view_key']}#b={BID}"
H = {"X-Tb-Key": EK, "X-Whiteboard": "1", "X-Tb-Name": "tester"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path):
    with opener.open(urllib.request.Request(BASE + path, headers=H), timeout=10) as r:
        return json.loads(r.read())


AGENT = json.loads(subprocess.run([sys.executable, str(CLI), "agent", "add", site["id"], "--name", "Claude", "--json"], env=env,
                                  capture_output=True, text=True, check=True).stdout)["token"]


def requests_presence(bid, what):
    """One presence via an agent token (the server adds agent:true and '<name> · AI'). Waits until the supervisor re-reads the token."""
    end = time.time() + 6
    while True:
        r = urllib.request.Request(f"{BASE}/api/whiteboard/boards/{bid}/presence", method="POST",
                                   data=json.dumps({"client": "agent-test", "pointer": {"x": 0, "y": 0, "tool": "pointer"}, "button": "up"}).encode(),
                                   headers={"Authorization": "Bearer " + AGENT, "X-Whiteboard": "1", "Content-Type": "application/json"})
        try:
            with opener.open(r, timeout=10):
                return
        except urllib.error.HTTPError:
            if time.time() > end:
                raise
            time.sleep(0.3)


def server_ids(bid=BID):
    return sorted(e["id"] for e in api(f"/api/whiteboard/boards/{bid}")["elements"] if not e.get("isDeleted"))


def scene_ids(pg):
    return sorted(pg.evaluate("window.__wbapi.getSceneElements().map(e=>e.id)"))


def join(ctx, name, url=EDIT, errs=None):
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errs is not None and errs.append(str(e)[:200]))
    pg.on("console", lambda m: errs is not None and m.type == "error" and errs.append("console: " + m.text[:200]))
    pg.goto(url)
    pg.fill("input[aria-label='이름']", name)
    pg.click("button.wb-primary")
    pg.wait_for_selector(".excalidraw canvas", timeout=30000)
    time.sleep(1.2)
    return pg


DROP_JS = """([txt, name, type]) => { const dt=new DataTransfer();
  if (type === 'png') { const b=Uint8Array.from(atob(txt), c=>c.charCodeAt(0)); dt.items.add(new File([b], name, {type:'image/png'})); }
  else if (type === 'lib') { dt.setData('application/vnd.excalidrawlib+json', txt); }
  else dt.items.add(new File([txt], name, {type: type}));
  const t=document.querySelector('.excalidraw canvas.interactive')||document.querySelector('.excalidraw canvas');
  const r=t.getBoundingClientRect();
  for (const ty of ['dragenter','dragover','drop']) t.dispatchEvent(new DragEvent(ty,{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.x+r.width/2,clientY:r.y+r.height/2})); }"""


MULTI_JS = """(items) => { const dt=new DataTransfer();
  for (const [b64, name, type] of items) { const b=Uint8Array.from(atob(b64), c=>c.charCodeAt(0)); dt.items.add(new File([b], name, {type})); }
  const t=document.querySelector('.excalidraw canvas.interactive')||document.querySelector('.excalidraw canvas');
  const r=t.getBoundingClientRect();
  for (const ty of ['dragenter','dragover','drop']) t.dispatchEvent(new DragEvent(ty,{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.x+r.width/2,clientY:r.y+r.height/2})); }"""


def drop_files(pg, items):
    """items: [(bytes, name, mime)] -- several files at once."""
    pg.evaluate(MULTI_JS, [[base64.b64encode(b).decode(), n, t] for b, n, t in items])


def drop(pg, txt=DOC, name="회의.excalidraw", type_=""):
    pg.evaluate(DROP_JS, [txt, name, type_])


def until(fn, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return time.time() - t0
        time.sleep(0.03)
    return None


report = {"checks": {}, "times": {}, "errors": []}
C = report["checks"]
try:
    with sync_playwright() as p:
        br = p.chromium.launch()
        errs = []
        A = join(br.new_context(viewport={"width": 1280, "height": 800}), "사람A", errs=errs)
        B = join(br.new_context(viewport={"width": 1280, "height": 800}), "사람B", errs=errs)
        A.evaluate("""() => { const a=window.__wbapi; a.updateScene({elements:[...a.getSceneElements(), {id:'human1',type:'ellipse',x:-400,y:-300,width:80,height:80,angle:0,strokeColor:'#1e1e1e',backgroundColor:'transparent',fillStyle:'solid',strokeWidth:2,strokeStyle:'solid',roughness:0,opacity:100,groupIds:[],frameId:null,index:'a0',roundness:null,seed:2,version:2,versionNonce:7,isDeleted:false,boundElements:null,updated:Date.now(),link:null,locked:false}], captureUpdate:'IMMEDIATELY'}); a.updateScene({appState:{selectedElementIds:{human1:true}}}); }""")
        until(lambda: "human1" in scene_ids(B) and "human1" in server_ids())
        base = scene_ids(A)

        # drop -> the sheet, scene unchanged. Delete with the sheet open -> nothing deleted. Esc -> closes
        drop(A)
        A.wait_for_selector(".wb-import-back", timeout=5000)
        time.sleep(0.8)
        C["2_sheet_on_drop"] = True
        C["2_scene_unchanged_before_choice"] = scene_ids(A) == base and server_ids() == base and scene_ids(B) == base
        C["focus_in_sheet"] = A.evaluate("!!document.activeElement.closest('.wb-import')")
        A.screenshot(path=str(shots / "desktop-1280-sheet.png"))
        A.keyboard.press("Delete")   # focus inside the window
        A.evaluate("document.activeElement && document.activeElement.blur()")   # even when focus is outside the window (body)
        A.keyboard.press("Delete")
        A.keyboard.press("Backspace")
        time.sleep(0.4)
        C["9_delete_does_not_delete"] = "human1" in scene_ids(A) and "human1" in server_ids()
        A.keyboard.press("Escape")
        time.sleep(0.3)
        C["9_esc_closes"] = A.query_selector(".wb-import-back") is None
        C["9_unchanged_after_esc"] = scene_ids(A) == base == server_ids() == scene_ids(B)

        # cancel -> all three unchanged
        drop(A)
        A.wait_for_selector(".wb-import-back")
        A.wait_for_selector(".wb-import .wb-primary:not([disabled])")
        A.click(".wb-import >> text=취소")
        time.sleep(0.8)
        C["2_cancel_no_change"] = scene_ids(A) == base == server_ids() == scene_ids(B)

        # merge -> within 1 second A, B, and the server have the same set
        drop(A)
        A.wait_for_selector(".wb-import .wb-primary:not([disabled])")
        t0 = time.time()
        A.click(".wb-import .wb-primary")
        dt = until(lambda: len(server_ids()) == len(base) + 7 and scene_ids(A) == server_ids() == scene_ids(B), 5)
        report["times"]["merge_sync_s"] = dt and round(time.time() - t0, 3)
        C["2_merge_same_within_1s"] = dt is not None and (time.time() - t0) <= 1.5 and dt <= 1.0
        sids = server_ids()
        C["2_merge_new_ids"] = not ({"dropA", "dropB", "dropArr", "dropImg"} & set(sids)) and "human1" in sids
        img_loaded = until(lambda: B.evaluate("(() => { const a=window.__wbapi; const im=a.getSceneElements().find(e=>e.type==='image'); return !!(im && a.getFiles()[im.fileId]); })()"), 4)
        C["merge_image_on_B"] = img_loaded is not None
        time.sleep(0.5)
        A.screenshot(path=str(shots / "desktop-1280-merged.png"))
        B.screenshot(path=str(shots / "desktop-1280-merged-B.png"))
        # diagnosis of my own screen: no drop bug (an element on my screen that the server lacks)
        C["2_no_divergence"] = set(scene_ids(A)) == set(server_ids())

        # image drop -- directly without the sheet, and the image appears on B's screen too
        n0 = len(server_ids())
        drop(A, base64.b64encode(PNG).decode(), "dot.png", "png")
        time.sleep(0.4)
        C["4_image_no_sheet"] = A.query_selector(".wb-import-back") is None
        ok = until(lambda: len(server_ids()) == n0 + 1 and len(scene_ids(B)) == n0 + 1, 5)
        C["4_image_inserted_and_B_sees"] = ok is not None
        okf = until(lambda: B.evaluate("(() => { const a=window.__wbapi; return a.getSceneElements().filter(e=>e.type==='image').every(e=>!!a.getFiles()[e.fileId]); })()"), 5)
        C["4_image_file_on_B"] = okf is not None

        # dragging a library item (a drop with no file) -- Excalidraw inserts it as is
        n1 = len(server_ids())
        drop(A, LIB, "", "lib")
        ok = until(lambda: len(server_ids()) == n1 + 1 and len(scene_ids(B)) == n1 + 1, 5)
        C["4_library_drag_still_works"] = ok is not None and A.query_selector(".wb-import-back") is None

        # -- an SVG / PNG that embeds a scene -> the sheet (scene as is); merge -> A, B, and the server match. An ordinary SVG becomes an image directly
        sys.path.insert(0, str(HERE / "tests"))
        import test_importers as TI
        for tag, data, name, mime in (("svg", TI.svg_with_scene(TI.scene_doc("embS")), "d.excalidraw.svg", "image/svg+xml"),
                                      ("png", TI.png_with_scene(TI.scene_doc("embP")), "d.excalidraw.png", "image/png")):
            b0 = server_ids()
            drop_files(A, [(data, name, mime)])
            shown = until(lambda: A.query_selector(".wb-import .wb-primary:not([disabled])") is not None, 5) is not None
            time.sleep(0.5)
            C[f"B1_{tag}_sheet"] = shown
            C[f"B1_{tag}_unchanged_before_choice"] = scene_ids(A) == b0 == server_ids() == scene_ids(B)
            if tag == "svg":
                A.screenshot(path=str(shots / "desktop-1280-embedded-sheet.png"))
            A.click(".wb-import .wb-primary")
            ok = until(lambda: len(server_ids()) == len(b0) + 2 and scene_ids(A) == server_ids() == scene_ids(B), 5)
            C[f"B1_{tag}_merge_same"] = ok is not None and scene_ids(A) == server_ids() == scene_ids(B)
            labels = B.evaluate("window.__wbapi.getSceneElements().filter(e=>e.type==='text').map(e=>e.text)")
            C[f"B1_{tag}_scene_content"] = "품은 장면" in labels
        n2 = len(server_ids())
        drop_files(A, [(TI.SVG1, "plain.svg", "image/svg+xml")])
        ok = until(lambda: len(server_ids()) == n2 + 1 and len(scene_ids(B)) == n2 + 1, 5)
        C["B1_plain_svg_direct"] = ok is not None and A.query_selector(".wb-import-back") is None

        # -- three at once (2 documents + 1 GIF, not JPEG) -- the image goes directly, documents one at a time. Dropping again while the sheet is open queues behind it
        GIF = TI.GIF1
        n3 = len(server_ids())
        drop_files(A, [(DOC.encode(), "first.excalidraw", ""), (GIF, "dot.gif", "image/gif"), (DOC.encode(), "second.excalidraw", "")])
        A.wait_for_selector(".wb-import .wb-primary:not([disabled])", timeout=5000)
        C["F4_gif_inserted"] = until(lambda: len(server_ids()) == n3 + 1, 5) is not None
        C["F4_first_in_sheet"] = A.inner_text(".wb-import-file") == "first.excalidraw"
        C["F4_next_listed"] = "second.excalidraw" in A.inner_text(".wb-import")
        drop_files(A, [(DOC.encode(), "third.excalidraw", "")])   # dropped again while it is open
        time.sleep(0.5)
        C["S1_current_kept"] = A.inner_text(".wb-import-file") == "first.excalidraw" and "third.excalidraw" in A.inner_text(".wb-import")
        A.click(".wb-import >> text=취소")
        A.wait_for_selector(".wb-import .wb-primary:not([disabled])", timeout=5000)
        C["F4_second_after_cancel"] = A.inner_text(".wb-import-file") == "second.excalidraw"
        A.click(".wb-import .wb-primary")
        ok = until(lambda: len(server_ids()) == n3 + 1 + 7 and scene_ids(A) == server_ids() == scene_ids(B), 5)
        C["F4_second_merged_same"] = ok is not None
        A.wait_for_selector(".wb-import .wb-primary:not([disabled])", timeout=5000)
        C["F4_third_next"] = A.inner_text(".wb-import-file") == "third.excalidraw"
        A.keyboard.press("Escape")
        time.sleep(0.4)
        C["F4_queue_empty"] = A.query_selector(".wb-import-back") is None and scene_ids(A) == server_ids() == scene_ids(B)

        report.setdefault("err_marks", {})["U5"] = len(errs)
        # -- initial focus is on 'merge into this board', the outline is visible, Enter merges. Only the merging user's screen gets the selection, and it is on screen
        b_view = B.evaluate("(() => { const s=window.__wbapi.getAppState(); return [s.scrollX, s.scrollY, s.zoom.value, Object.keys(s.selectedElementIds||{}).filter(k=>s.selectedElementIds[k]).sort()]; })()")
        A.evaluate("(() => { const a=window.__wbapi; a.updateScene({appState:{scrollX: a.getAppState().scrollX - 3000}}); })()")   # move far away to get a place to merge
        time.sleep(0.3)
        n4 = len(server_ids())
        drop(A)
        A.wait_for_selector(".wb-import .wb-primary:not([disabled])")
        time.sleep(0.2)
        C["U5_focus_on_merge"] = A.evaluate("document.activeElement && document.activeElement.classList.contains('wb-primary')")
        C["U5_focus_ring_visible"] = A.evaluate("getComputedStyle(document.activeElement).outlineStyle !== 'none' && parseFloat(getComputedStyle(document.activeElement).outlineWidth) >= 2")
        A.screenshot(path=str(shots / "r2-U5-focus.png"))
        t0 = time.time()
        A.keyboard.press("Enter")
        ok = until(lambda: len(server_ids()) == n4 + 7 and scene_ids(A) == server_ids() == scene_ids(B), 5)
        C["U5_enter_merges_within_1s"] = ok is not None and ok <= 1.0
        time.sleep(1.0)
        sel = A.evaluate("(() => { const s=window.__wbapi.getAppState(); return Object.keys(s.selectedElementIds||{}).filter(k=>s.selectedElementIds[k]); })()")
        tops = A.evaluate("(() => { const a=window.__wbapi; return a.getSceneElements().filter(e=>!e.containerId).map(e=>e.id); })()")
        C["U7_merged_selected"] = len(sel) == 4 and all(i in tops for i in sel)   # 2 boxes, an arrow, and an image (the three labels belong to the boxes and arrow)
        C["U7_merged_in_view"] = A.evaluate("""(ids) => { const a=window.__wbapi, s=a.getAppState(), z=s.zoom.value;
            return a.getSceneElements().filter(e=>ids.includes(e.id)).every(e => { const x=(e.x+s.scrollX)*z, y=(e.y+s.scrollY)*z;
              return x >= 0 && y >= 0 && x + e.width*z <= s.width && y + e.height*z <= s.height; }); }""", sel)
        C["U7_other_screen_not_moved"] = B.evaluate("(() => { const s=window.__wbapi.getAppState(); return [s.scrollX, s.scrollY, s.zoom.value, Object.keys(s.selectedElementIds||{}).filter(k=>s.selectedElementIds[k]).sort()]; })()") == b_view
        A.screenshot(path=str(shots / "r2-U7-merged-selected.png"))

        report.setdefault("err_marks", {})["U6"] = len(errs)
        # -- large file: the guest-facing message, error colours (light and dark), role=alert
        big = json.dumps({"type": "excalidraw", "version": 2, "elements": [
            {"id": "t%05d" % i, "type": "text", "x": 0, "y": i * 30, "width": 100, "height": 25, "text": ("\"따옴표\" 한글 " * 260)[:2600]}
            for i in range(2400)]}, ensure_ascii=False)
        drop(A, big, "big.excalidraw")
        A.wait_for_selector(".wb-import-err[role=alert]", timeout=15000)
        msg = A.inner_text(".wb-import-err")
        C["U6_guest_wording"] = "보드를 연 사람에게" in msg and "보낼 내용" not in msg and "그림 제외" not in msg and "MB" in msg
        C["U6_no_merge_button"] = A.query_selector(".wb-import .wb-primary") is None
        C["U6_light_color"] = A.evaluate("getComputedStyle(document.querySelector('.wb-import-err')).color") == "rgb(180, 35, 24)"
        A.screenshot(path=str(shots / "r2-U6-light.png"))
        A.evaluate("document.querySelector('.wb-board').classList.replace('wb-theme-light','wb-theme-dark')")
        C["U6_dark_color"] = A.evaluate("getComputedStyle(document.querySelector('.wb-import-err')).color") == "rgb(255, 161, 152)"
        A.screenshot(path=str(shots / "r2-U6-dark.png"))
        A.evaluate("document.querySelector('.wb-board').classList.replace('wb-theme-dark','wb-theme-light')")
        A.keyboard.press("Escape")
        time.sleep(0.3)

        report.setdefault("err_marks", {})["lib"] = len(errs)
        # -- dragging an item from the library panel onto the canvas -- the real panel and the real drag (regression check for the re-sent drop path)
        n5 = len(server_ids())
        A.evaluate("window.__wbapi.updateScene({appState:{selectedElementIds:{}}})")   # with a selection, an 'add selection to library' cell appears at the front of the panel
        A.evaluate("""(lib) => window.__wbapi.updateLibrary({ libraryItems: JSON.parse(lib).libraryItems, openLibraryMenu: true })""", LIB)
        try:
            A.wait_for_selector(".default-sidebar [draggable=true]", timeout=8000)
            cv = A.query_selector(".excalidraw canvas.interactive").bounding_box()
            A.drag_and_drop(".default-sidebar [draggable=true]", ".excalidraw canvas.interactive", target_position={"x": cv["width"] * 0.3, "y": cv["height"] * 0.5})
            ok = until(lambda: len(server_ids()) == n5 + 1 and len(scene_ids(B)) == n5 + 1 and scene_ids(A) == server_ids(), 5)
            C["library_panel_drag"] = ok is not None and A.query_selector(".wb-import-back") is None
            report["library_diag"] = {"n5": n5, "server": len(server_ids()), "A": len(scene_ids(A)), "B": len(scene_ids(B)), "errs_since": errs[report["err_marks"]["lib"]:][:3]}
            A.screenshot(path=str(shots / "r2-library-drag.png"))
        except Exception as ex:   # noqa: BLE001
            report["library_drag_error"] = str(ex)[:300]
            C["library_panel_drag"] = False
        A.keyboard.press("Escape")

        report.setdefault("err_marks", {})["newboard"] = len(errs)
        # open as new board -> A moves to the new board, the original board is unchanged
        before = server_ids()
        boards0 = {b["id"] for b in api("/api/whiteboard/boards")}
        drop(A)
        A.wait_for_selector(".wb-import .wb-primary:not([disabled])")
        A.click(".wb-import >> text=새 보드로 열기")
        hash_ = lambda: A.evaluate("location.hash")
        ok = until(lambda: hash_().startswith("#b=") and BID not in hash_(), 5)
        new_ids = {b["id"] for b in api("/api/whiteboard/boards")} - boards0
        C["3_new_board_in_list"] = len(new_ids) == 1
        C["3_A_moved_to_new_board"] = ok is not None and (list(new_ids)[0] in hash_() if new_ids else False)
        C["3_original_unchanged"] = server_ids() == before == scene_ids(B)
        if new_ids:
            nb = list(new_ids)[0]
            A.wait_for_selector(".excalidraw canvas")
            until(lambda: len(A.evaluate("window.__wbapi ? window.__wbapi.getSceneElements().map(e=>e.id) : []")) == 7, 8)
            C["3_new_board_keeps_ids"] = sorted(server_ids(nb)) == sorted(["dropA", "dropA-label", "dropB", "dropB-label", "dropArr", "dropArr-label", "dropImg"])
            C["3_new_board_image"] = until(lambda: A.evaluate("(() => { const a=window.__wbapi; const im=a.getSceneElements().find(e=>e.type==='image'); return !!(im && a.getFiles()[im.fileId]); })()"), 5) is not None
            A.screenshot(path=str(shots / "desktop-1280-newboard.png"))
            toast_ = until(lambda: "같이 보던 사람은 원래 보드에 있어요" in (A.evaluate("(document.querySelector('.Toast')||{}).innerText||''") or ""), 4)
            C["U7_new_board_toast"] = toast_ is not None and "새 보드 '회의'" in A.evaluate("(document.querySelector('.Toast')||{}).innerText||''")

        report.setdefault("err_marks", {})["menu"] = len(errs)
        # desktop menu -- 'Import file...' and the bar at 1280 and 1024
        for w in (1280, 1024):
            pg = join(br.new_context(viewport={"width": w, "height": 800}), f"메뉴{w}", errs=errs)
            pg.click(".excalidraw .dropdown-menu-button, .excalidraw [data-testid='main-menu-trigger']")
            pg.wait_for_selector("text=파일 가져오기…")
            C[f"menu_item_{w}"] = pg.is_visible("text=파일 가져오기…")
            C[f"no_hscroll_{w}"] = not pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            pg.screenshot(path=str(shots / f"desktop-{w}-menu.png"))
            with pg.expect_file_chooser() as fc:
                pg.click("text=파일 가져오기…")
            f = tmp / "pick.excalidraw"
            f.write_text(DOC)
            fc.value.set_files(str(f))
            pg.wait_for_selector(".wb-import-back")
            C[f"menu_pick_opens_sheet_{w}"] = True
            pg.keyboard.press("Escape")
            pg.context.close()
        br.close()

        report.setdefault("err_marks", {})["phone"] = len(errs)
        # phone -- WebKit iPhone 13 and Chromium Pixel 7: menu 'Import file...' -> sheet -> merge, within 390px. The view link has no menu item
        for eng, dev in (("webkit", "iPhone 13"), ("chromium", "Pixel 7")):
            tag = f"{eng}-{dev.replace(' ', '')}"
            b = getattr(p, eng).launch()
            perrs = []
            ctx = b.new_context(**p.devices[dev])
            P = join(ctx, "폰", errs=perrs)
            P.click("button[aria-label='메뉴']")
            P.wait_for_selector(".wb-mmenu")
            C[f"5_{tag}_menu_item"] = P.is_visible(".wb-mmenu >> text=파일 가져오기…")
            f = tmp / "phone.excalidraw"
            f.write_text(DOC)
            try:
                with P.expect_file_chooser(timeout=5000) as fc:
                    P.click(".wb-mmenu >> text=파일 가져오기…")
                fc.value.set_files(str(f))
            except Exception:   # noqa: BLE001 -- on an engine without the file chooser event, go straight to the hidden input
                P.set_input_files("input[type=file]", str(f))
            P.wait_for_selector(".wb-import .wb-primary:not([disabled])", timeout=8000)
            box = P.evaluate("(() => { const r=document.querySelector('.wb-import').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; })()")
            C[f"5_{tag}_sheet_fits"] = box[0] >= -0.5 and box[1] <= box[4] + 0.5 and box[2] >= -0.5 and box[3] <= box[5] + 0.5
            btns = P.evaluate("[...document.querySelectorAll('.wb-import button')].map(b => { const r=b.getBoundingClientRect(); return [r.left, r.right, r.height]; })")
            C[f"5_{tag}_buttons_fit"] = all(l >= 0 and r <= box[4] + 0.5 and h >= 44 for l, r, h in btns)
            C[f"5_{tag}_no_hscroll"] = not P.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            P.screenshot(path=str(shots / f"phone-{tag}-sheet.png"))
            n0 = len(server_ids())
            P.evaluate("(() => { const a=window.__wbapi; a.updateScene({appState:{scrollX: a.getAppState().scrollX + 2500}}); })()")
            P.click(".wb-import .wb-primary")
            ok = until(lambda: len(server_ids()) == n0 + 7 and len(scene_ids(P)) == n0 + 7, 5)
            C[f"5_{tag}_merge"] = ok is not None and set(scene_ids(P)) == set(server_ids())
            time.sleep(1.2)
            sel = P.evaluate("(() => { const s=window.__wbapi.getAppState(); return Object.keys(s.selectedElementIds||{}).filter(k=>s.selectedElementIds[k]); })()")
            C[f"U7_{tag}_merged_whole_in_view"] = len(sel) == 4 and P.evaluate("""(ids) => { const a=window.__wbapi, s=a.getAppState(), z=s.zoom.value;
                const top=document.querySelector('.wb-mtop').getBoundingClientRect().bottom, dock=document.querySelector('.wb-dock').getBoundingClientRect().top;
                return a.getSceneElements().filter(e=>ids.includes(e.id)).every(e => { const x=(e.x+s.scrollX)*z, y=(e.y+s.scrollY)*z;
                  return x >= 0 && x + e.width*z <= s.width && y >= top - 1 && y + e.height*z <= dock + 1; }); }""", sel)
            # when an agent joins, the collapsed chip shows 'AI' (not counted among people), a screen-reader name, and no horizontal scroll
            requests_presence(BID, "pointer")
            okai = until(lambda: "AI" in P.inner_text(".wb-mchip-btn"), 5)
            C[f"U1b_{tag}_chip_ai"] = okai is not None
            C[f"U1b_{tag}_aria"] = "AI 에이전트" in (P.get_attribute(".wb-mchip-btn", "aria-label") or "")
            C[f"U1b_{tag}_no_hscroll"] = not P.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            C[f"U1b_{tag}_bar_fits"] = P.evaluate("(() => { const r=document.querySelector('.wb-mtop').getBoundingClientRect(); return [...document.querySelectorAll('.wb-mtop > *')].every(e => e.getBoundingClientRect().right <= r.right + 0.5); })()")
            P.screenshot(path=str(shots / f"r2-U1b-{tag}-chip.png"))
            if tag.startswith("chromium"):
                gone = until(lambda: "AI" not in P.inner_text(".wb-mchip-btn"), 45)
                C["U1b_ai_gone_after_30s"] = gone is not None and gone > 20
            P.screenshot(path=str(shots / f"phone-{tag}-merged.png"))
            V = join(b.new_context(**p.devices[dev]), "보기", url=VIEW, errs=perrs)
            V.click("button[aria-label='메뉴']")
            V.wait_for_selector(".wb-mmenu")
            C[f"5_{tag}_view_has_no_item"] = V.query_selector(".wb-mmenu >> text=파일 가져오기…") is None and V.query_selector("input[type=file]") is None
            V.click(".wb-msheet-back", position={"x": 5, "y": 5})
            vb = scene_ids(V)
            drop(V)
            time.sleep(0.8)
            C[f"{tag}_view_drop_blocked"] = scene_ids(V) == vb and V.query_selector(".wb-import-back") is None
            report["errors"] += [f"{tag}: {e}" for e in perrs]
            b.close()
        report["errors"] += errs

        # -- changing the key shows 'may have changed or ended' on the open page, plus the cera links guidance
        br2 = p.chromium.launch()
        R = join(br2.new_context(viewport={"width": 1280, "height": 800}), "회전")
        subprocess.run([sys.executable, str(CLI), "keys", site["id"], "--rotate", "all"], env=env, capture_output=True, check=True)
        def body_text():
            try:
                return R.evaluate("document.body.innerText") or ""
            except Exception:   # noqa: BLE001 -- if rejected, the page reloads (the context vanishes meanwhile)
                return ""
        okr = until(lambda: "바뀌었거나 끝났을 수" in body_text(), 10)
        C["U8_rotation_wording"] = okr is not None and "cera links" in body_text()
        R.screenshot(path=str(shots / "r2-U8-denied.png"))
        br2.close()
finally:
    subprocess.run([sys.executable, str(CLI), "stop", "--all"], env=env, capture_output=True)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# the /me 401 on the first screen ('login required' before a name is entered) is normal -- counted separately
report["errors_401_login"] = sum("401" in e for e in report["errors"])
report["errors"] = [e for e in report["errors"] if "401" not in e]
report["ok"] = all(v is True for v in C.values())
print(json.dumps(report, ensure_ascii=False, indent=1))
sys.exit(0 if report["ok"] else 1)
