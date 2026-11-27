---
name: board-uiux-test
description: tempboard 를 로컬로 띄워 Playwright 로 데스크톱(Chromium · WebKit) · 폰(iPhone 13 · Pixel 7 흉내) · 두 사람 동기화를 실제로 눌러 보는 시험 절차와 번들 스크립트. board-uiux-tester 에이전트가 쓴다. "UI 시험", "폰에서 되는지 봐 줘", "화면 깨지는지 확인", "두 사람이 같이 쓰는지 시험" 이면 이 스킬.
---

# board-uiux-test

## 보드 띄우기
```bash
S=~/Projects/tempboard/.claude/skills/board-uiux-test/scripts
cd ~/Projects/tempboard/web && npx vite build          # 최신 코드로
URL=$($S/local_board.sh start)                          # 임시 TEMPBOARD_HOME, 터널 없음 → 편집 링크 출력
python3 $S/ui_smoke.py "$URL" _workspace/shots/r1        # 기본 점검(아래)
$S/local_board.sh stop                                  # 끝나면 꼭
```

## 기본 점검(ui_smoke.py)
엔진 · 기기마다: 이름 넣고 들어가기 → 펜 한 획 → 다른 컨텍스트에 보이는지 → 글 하나 넣기 → 페이지 오류 · 가로 스크롤 유무 → 스크린샷. 새 기능 시험은 이 파일을 복사해 `_workspace/tests/` 에 기능별 스크립트로 쓴다(번들 스크립트는 고치지 않는다 — 고쳐야 하면 리더에게 알린다).

## 쓸모 있는 조작
- Excalidraw API: `page.evaluate("window.__wbapi.getSceneElements()")`, `updateScene`, `setActiveTool({type})`.
- 폰 탭: `page.touchscreen.tap(x, y)`, 요소는 `locator.tap()`. 도구 막대(dock)를 피해서 누른다(`--dock-h` 높이).
- 데스크톱 글 입력: 도구 단추 → 캔버스 클릭 → `keyboard.type`. 한글 조합은 `keyboard.insertText`.
- 동기화 확인은 1.5–3초 기다린 뒤 다른 컨텍스트의 요소 수 · 내용으로.
- 시험이 끝나기 전에 브라우저를 닫으면 마지막 변경이 안 나갈 수 있다 — 닫기 전 1–2초.

## 보고 형식(`_workspace/05_uiux_r<k>.md`)
- 받아들일 기준 표: 기준 · 엔진/기기 · 통과/실패/확인 못 함 · 근거(스크린샷 경로 · 값).
- 사용성 문제: 누가 · 무엇을 하려다 · 어디서 막힘 · 심각도.
- 스크린샷을 직접 열어 보고(Read 로 png) 겹침 · 잘림 · 대비를 눈으로 확인한다. 스크린샷을 찍기만 하고 안 보면 시험이 아니다.
- 실제 iOS 자판 동작처럼 흉내로 확인할 수 없는 것은 '확인 못 함' 으로 남긴다.
