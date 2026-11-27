---
name: board-implement
description: Cera 코드(web/src · server · bin)에 기능을 구현하는 방법 — 코드 지도, Excalidraw 0.18 함정, 동기화 규칙, 글꼴 칸, 빌드 · 시험 · 버전 올리기. board-implementer 에이전트가 쓰고, 리더의 통합 단계도 쓴다. Cera 코드를 고치거나 버그를 잡거나 기능을 더하는 일이면 이 스킬.
---

# board-implement

## 코드 지도
| 파일 | 무엇 |
|---|---|
| `web/src/main.jsx` | 진입 · 보드 목록 · 이름 입력 · CSS 불러오기 |
| `web/src/Board.jsx` | 보드 화면 전부(도구 막대 TOOLS · 글꼴 FONTS · SIZES · 색 · 폰 글 입력 시트 · 수식 창 MathDialog · 메뉴 · PNG 저장 `savePng`). 큰 파일 — 새 기능은 `web/src/features/` 로 |
| `web/src/transport.js` | WebSocket ↔ HTTP 롱폴링, 보냄 → ack → saved |
| `web/src/math.js` | MathJax(TeX → SVG), 쓸 때만 dynamic import |
| `web/src/textstyle.js` · `mathgate.js` | 순수 로직(시험 있음) |
| `web/src/kfonts*.css` | 한글 글꼴을 Excalidraw 글꼴 칸에 unicode-range 로 붙임. `scripts/fetch-kfonts.py` 가 생성 |
| `server/server.py` | 표준 라이브러리 서버. 요소 병합 `wins()`, 파일(이미지 data URL ≤10 MB, png/jpeg/gif/webp/svg), 한 번 2000 요소 |
| `bin/cera` | CLI(start/list/links/stop/export/delete), dist 내려받기 |

## 동기화 규칙
- 보드 상태 = Excalidraw 요소 배열 + files. 새 기능은 **요소로 표현**한다(도형 · 텍스트 · 이미지 · 화살표 · frame, 부가 정보는 `customData`). 그래야 서버 병합(version/versionNonce)과 다른 사람 화면이 그대로 동작한다.
- 이미지성 결과물(수식 SVG 등)은 `excal.addFiles` + image 요소. 파일은 업로드돼야 다른 사람이 본다(onChange 의 files 루프가 올린다).
- 보기 전용(view role)에서는 편집 UI 를 숨긴다.

## Excalidraw 0.18 함정(겪은 것)
- 글 상자 편집기는 setTimeout 안에서 focus 한다 → iOS 는 자판이 안 뜬다. 폰 글 입력은 `.wb-sheet` textarea 를 터치 핸들러 안에서 **동기로** focus 한다. 새 입력 UI 도 같은 원칙.
- 글 편집 중 다른 입력(range 슬라이더 등)을 누르면 글 상자가 빈 채로 끝나 지워진다 → 편집 중 쓰는 조절은 button 으로.
- 글꼴은 정수 id 칸(FONT_FAMILY: Virgil 1, Helvetica 2, Cascadia 3, Excalifont 5, Nunito 6, Lilita One 7, Comic Shanns 8, Liberation Sans 9)만 다른 사람 화면에서도 같은 글꼴로 그려진다. 지금 Helvetica(2) 말고는 다 한글 글꼴이 붙어 있다. 글꼴을 더 늘리려면 (a) 남은 칸에 붙이거나 (b) 이미 쓴 칸의 한글 글꼴을 바꾸거나 (c) 글을 SVG/이미지로 굳히는 방식 중 하나를 골라 이유를 적는다. 임의의 새 id 는 다른 사람 화면에서 기본 글꼴로 떨어진다.
- 한글 IME 켜진 상태의 단축키는 `e.key` 가 아니라 `e.code` 로 본다(ALLOWED_KEYS 막기 참고).
- 테스트 훅: `window.__wbapi` = Excalidraw API.
- 폰(coarse)에서 Excalidraw 의 글 편집기로 가는 길은 모두 막고 폰 글 시트로 보낸다 — 자유 글뿐 아니라 상자 안 글(bound text)도. 새 기능이 글을 만드는 길을 열면 이 규칙부터 본다(WP-2 에서 다시 열렸다).
- 두 사람이 '같은 것' 을 동시에 처음 만들 수 있으면(상자 글 등) id 를 무작위로 하지 말고 대상에서 정해지게(`<상자id>-label`) — 아니면 둘 다 살아남는다. 지운 같은 id 가 있으면 version 을 올려 되살린다.
- 받는 쪽에서 그림(files)이 요소보다 늦게 올 수 있다. 같은 요소의 fileId 를 바꿀 때는 넣는 쪽이 파일을 먼저 올리고, 받는 쪽은 파일을 받은 뒤 바꿔 끼운다(`features/math/hold.js`).
- WebKit 은 `document.fonts.check` 가 거짓 true 를 줄 수 있다 — 글꼴은 `features/fonts/registry.js` 의 함수로 직접 불러온다. 저장 전에는 `ensureSceneFonts`.
- 지연 청크(React.lazy · dynamic import)는 실패할 수 있다(터널 끊김 · dist 교체) — 오류 경계와 토스트로, 보드 전체가 흰 화면이 되지 않게.
- 새 창 · 시트를 열면 초점을 옮기고 Esc 로 닫히게, 키가 아래 보드로 새지 않게(Delete 가 선택 요소를 지워 모두에게 퍼진다). 앱 창이 열려 있으면 보드 위 떠 있는 단추('+' 등)는 숨긴다.
- 데스크톱 막대 폭: 1024 · 1280px 에서 줌 단추 · '?' 를 덮지 않는지 스크린샷으로. 761–1279px 는 '더 보기' 로 접힌다.

## 의존성
- 무거운 것은 dynamic import(쓸 때만). 새 의존성은 MIT/Apache-2.0/OFL 만. 출처를 `LICENSE`(제3자 고지 절) 또는 파일 머리 주석에 남긴다.
- drawio(Apache-2.0)에서 코드를 옮기면 원 파일 경로와 라이선스를 주석에 적는다.

## 빌드 · 시험
```bash
R="$(git rev-parse --show-toplevel)"   # 이 체크아웃(워크트리여도 된다)
cd "$R/web" && npm test && npx vite build
cd "$R" && python3 -m unittest discover -s tests -q
claude plugin validate "$R"
```
순수 로직은 `web/tests/<이름>.test.mjs`(node:test). UI 확인은 board-uiux-test 스킬의 `local_board.sh`.

## 버전 올리기(통합 단계만)
`.claude-plugin/plugin.json` · `.claude-plugin/marketplace.json` · `web/package.json` · `web/package-lock.json` 의 루트 두 곳(`version`, `packages[""].version`)만 — lock 파일에 sed 를 쓰지 않는다(의존성 버전까지 바뀐 적 있다). 릴리스 자산 `cera-dist.tar.gz` 는 `scripts/release-dist.sh`, push · release 는 사용자가 요청할 때만.
