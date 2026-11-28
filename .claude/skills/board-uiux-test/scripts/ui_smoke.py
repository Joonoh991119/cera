"""Cera 기본 UI 점검 — 데스크톱(Chromium · WebKit) + 폰(iPhone 13 · Pixel 7), 두 사람 동기화.
usage: python3 ui_smoke.py <edit-url> <shots-dir>
기능별 시험은 이 파일을 복사해 _workspace/tests/ 에서 고쳐 쓴다."""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

url, shots = sys.argv[1], Path(sys.argv[2]); shots.mkdir(parents=True, exist_ok=True)
CASES = [("chromium", None), ("webkit", None), ("webkit", "iPhone 13"), ("chromium", "Pixel 7")]
n = lambda pg: pg.evaluate("window.__wbapi.getSceneElements().length")
report = []

def join(ctx, name, errs):
    pg = ctx.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)[:200])); pg.goto(url)
    pg.fill("input[aria-label='이름']", name); pg.click("button.wb-primary")
    pg.wait_for_selector(".excalidraw canvas", timeout=30000); time.sleep(1.5)
    return pg

with sync_playwright() as p:
    for eng, dev in CASES:
        tag = f"{eng}-{(dev or 'desktop').replace(' ', '')}"
        try: b = getattr(p, eng).launch()
        except Exception as e: report.append({"case": tag, "skip": str(e)[:80]}); continue
        opt = p.devices[dev] if dev else {"viewport": {"width": 1280, "height": 800}}
        errs = []; A = join(b.new_context(locale="ko-KR", **opt), "A", errs); B = join(b.new_context(locale="ko-KR", **opt), "B", errs)
        mobile = bool(dev)
        A.evaluate("window.__wbapi.setActiveTool({type:'freedraw'})")
        if mobile:
            # 터치 획: 화면 가운데 위쪽(도구 막대를 피해서)
            A.evaluate("""() => { const c=document.querySelector('.excalidraw canvas.interactive')||document.querySelector('.excalidraw canvas');
              const fire=(t,x,y)=>c.dispatchEvent(new PointerEvent(t,{bubbles:true,pointerId:1,pointerType:'touch',clientX:x,clientY:y,isPrimary:true,pressure:.5}));
              fire('pointerdown',100,200); for(let i=1;i<15;i++) fire('pointermove',100+i*10,200+(i%3)*8); fire('pointerup',250,210); }""")
        else:
            A.mouse.move(400, 300); A.mouse.down()
            for i in range(15): A.mouse.move(400 + i * 12, 300 + (i % 3) * 10)
            A.mouse.up()
        time.sleep(2.5)
        hscroll = A.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        r = {"case": tag, "A": n(A), "B_sees": n(B), "hscroll": hscroll, "errors": errs[:3]}
        A.screenshot(path=str(shots / f"{tag}.png")); r["shot"] = str(shots / f"{tag}.png")
        A.evaluate("window.__wbapi.updateScene({elements: window.__wbapi.getSceneElementsIncludingDeleted().map(e=>({...e,isDeleted:true,version:e.version+1}))})")
        time.sleep(1.5); report.append(r); b.close()
print(json.dumps(report, ensure_ascii=False, indent=1))
ok = all(r.get("skip") or (r["A"] >= 1 and r["B_sees"] == r["A"] and not r["hscroll"] and not r["errors"]) for r in report)
sys.exit(0 if ok else 1)
