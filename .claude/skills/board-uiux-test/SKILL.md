---
name: board-uiux-test
description: Cera 를 로컬로 띄워 Playwright 스크립트로 데스크톱(Chromium · WebKit) · 폰(iPhone 13 · Pixel 7 흉내) · 두 사람 동기화를 자동 확인하는 스모크 절차와 번들 스크립트, 그리고 사람에게 보낼 UX 확인 요청서 형식. board-uiux-tester 에이전트가 쓴다. "스모크", "자동 UI 점검", "UX 확인 요청서" 면 이 스킬. 사용성 판정은 사람에게 요청한다.
---

# board-uiux-test

## 보드 띄우기
```bash
R="$(git rev-parse --show-toplevel)"                    # 이 체크아웃(워크트리여도 된다)
S="$R/.claude/skills/board-uiux-test/scripts"
cd "$R/web" && npx vite build                           # 최신 코드로
URL=$($S/local_board.sh start)                          # 임시 CERA_HOME, 터널 없음 → 편집 링크 출력
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

## 보고 형식(`_workspace/05_smoke.md`, ≤ 20줄)
- 기준 표: 기준 · 엔진/기기 · 통과/실패 · 근거(값 · 재현 명령). 스크린샷은 실패한 것만 경로로.
- 사용성 · 느낌 · 실제 기기 자판 같은 것은 적지 않는다 → `05_ux_request.md` 항목으로 넘긴다.

## 사람 UX 확인 요청서(`_workspace/05_ux_request.md`)
```
# 확인 부탁: <기능>
링크: <편집 링크> (보기 전용: <링크>) — <언제까지 열림>
기기: 데스크톱 브라우저 하나 + 폰 하나(가능하면 두 사람)
1. <해 볼 일> → <보여야 하는 것>   [좋음 / 이상함 / 안 됨] 한 줄:
...(≤ 10개)
```
