---
name: board-orchestrator
description: tempboard 기능 하네스의 지휘 절차 — 조사팀(board-scout) → 오케스트레이터(board-orchestrator) 고르기 → 구현팀(board-implementer) → 리뷰어(board-reviewer) → UI/UX 시험자(board-uiux-tester) → 판정 → 통합. tempboard 에 기능을 더하거나 다른 도구(drawio · tldraw · OneNote · GoodNotes · Miro)의 기능을 체리피킹하거나, 판서 · 미팅용 기능 · 글꼴 · 수식 · 도형 · 손글씨 보정 · 내보내기를 조사하고 넣자는 요청이면 반드시 이 스킬을 쓴다. 후속 요청 — "다시 실행", "그 기능만 다시", "리뷰만 다시", "UI 시험만 다시", "이전 결과 기반으로 보완", "빠진 기능 더해", "결과 개선" — 도 이 스킬. 보드를 열고 닫는 운영(링크 만들기 · 닫기 · 꺼내기)은 이 스킬이 아니라 플러그인 스킬 `tempboard`.
---

# board-orchestrator — tempboard 기능 하네스

메인 세션(리더)이 이 절차를 돈다. 리더는 판단을 직접 하지 않고 역할 에이전트에게 맡긴 뒤 결과 파일만 읽어 다음 단계로 넘긴다. 역할을 나누는 이유: 조사한 사람이 고르면 자기 후보를 과대평가하고, 구현한 사람이 리뷰하면 자기 설명에 앵커링되고, 코드를 읽은 사람이 UI 를 시험하면 "이렇게 누르면 되는데" 를 안다. 각 단계의 눈을 깨끗하게 두려고 나눈다.

**실행 모드: 서브 에이전트(파일 기반 전달).** 이 환경에는 팀 도구(TeamCreate)가 없으므로 Agent 도구로 역할 에이전트를 부르고, 산출물은 `_workspace/` 파일로 주고받는다. 새로 만든 `.claude/agents/*.md` 는 세션을 다시 열기 전에는 subagent_type 으로 잡히지 않을 수 있다 — 그때는 `subagent_type: general-purpose` 로 부르고 프롬프트 첫 줄에 "너는 `.claude/agents/<역할>.md` 의 역할이다. 그 파일과 거기 적힌 스킬을 먼저 읽어라" 를 넣는다. 모델은 `opus`.

## 역할과 산출물

| 단계 | 역할(에이전트) | 스킬 | 산출물 |
|---|---|---|---|
| 0 맥락 | 리더 | — | `_workspace/00_context.md` |
| 1 조사 | board-scout × lens 수(병렬) | board-feature-scout | `01_scout_<lens>.md` |
| 2 고르기 · 나누기 | board-orchestrator | (이 문서 + 에이전트 정의) | `02_plan.md` |
| 3 구현 | board-implementer × WP | board-implement | 코드 + `03_impl_WP-n.md` |
| 4 리뷰 | board-reviewer × WP | board-review | `04_review_WP-n_r<k>.md` |
| 5 UI/UX 시험 | board-uiux-tester | board-uiux-test | `05_uiux_r<k>.md` + `shots/` |
| 6 판정 | board-orchestrator | — | `06_verdict_r<k>.md` |
| 7 통합 | 리더 | board-implement(빌드 · 버전) | 커밋(브랜치), 보고 |

## Phase 0 — 맥락 확인
1. `_workspace/` 가 없으면 초기 실행. 있으면: 사용자가 일부만 다시 하라 했으면 그 단계 에이전트만 다시 부른다(부분 재실행), 새 주제면 `_workspace` 를 `_workspace_prev` 로 옮기고 새로 시작.
2. 기본 브랜치면 `feat/<주제>` 브랜치를 만든다.
3. `00_context.md` 에 사용자 요청 원문, 지금 있는 기능 목록(Board.jsx 의 TOOLS · FONTS · 메뉴 항목), 제약(링크 하나로 바로, 폰 지원, 임시 보드, 라이선스)을 적는다.

## Phase 1 — 조사(병렬)
- lens 를 2–4개로 나눠 board-scout 를 동시에 부른다(같은 메시지에서 여러 Agent 호출, `run_in_background`). lens 는 요청에 맞춰 정한다. 예: `drawio`(도식 · 수식 · 연결선 · 자동 배치), `lecture`(강의 판서 도구), `meeting`(미팅 도구), `ink`(손글씨 · 도형 보정 · 글꼴).
- 조사팀 수는 4 를 넘기지 않는다 — 넘으면 고르는 쪽이 읽는 비용이 가치보다 커진다.

## Phase 2 — 고르기 · 나누기
- board-orchestrator 를 한 번 부른다. 입력: 모든 `01_scout_*`, `00_context.md`, 코드베이스.
- 리더는 `02_plan.md` 의 고른 것 표를 사용자에게 짧게 알린다. 사용자가 범위를 미리 정해 두었으면 묻지 않고 진행한다.

## Phase 3–5 — 구현 · 리뷰 · 시험 루프
- WP 순서는 `02_plan.md` 를 따른다. `Board.jsx` 를 같이 만지는 WP 는 차례로, 파일이 겹치지 않는 WP 만 병렬.
- WP 하나가 끝날 때마다 board-reviewer 를 바로 부른다(점진 리뷰). 리뷰어 프롬프트에는 diff 범위와 받아들일 기준만 주고 구현자 설명은 주지 않는다.
- 리뷰 `막음/고침` 지적 → 같은 구현자에게 해당 파일만 넘겨 고침(최대 2라운드). 2라운드 뒤에도 막음이면 판정 단계로 넘겨 빼기를 검토한다.
- 모든 WP 가 리뷰를 통과하면 board-uiux-tester 를 부른다(묶음이 크면 중간에 한 번 더).
- 리뷰어는 모델 다양성이 필요하면 `ocx-gpt-6-1-sol` 로 한 번 더 돌릴 수 있다(선택).

## Phase 6 — 판정
- board-orchestrator 가 리뷰 · 시험 결과로 WP 마다 통과/고침/빼기. 고침이면 Phase 3 으로 돌아간다(전체 루프 최대 3회).

## Phase 7 — 통합
1. `cd web && npm test && npx vite build`, `python3 -m unittest discover -s tests -q`, `claude plugin validate .` 가 모두 통과.
2. 버전을 올린다(plugin.json · marketplace.json · web/package.json · package-lock 루트 두 곳 — sed 로 lock 전체를 바꾸지 않는다).
3. README 기능 줄을 고치고 브랜치에 커밋한다. **push · release 는 사용자가 요청할 때만.**
4. 사용자 보고: 넣은 것 / 미룬 것(이유) / 확인 못 한 것, 그리고 하네스 개선점 물어보기.

## 에러 핸들링
- 에이전트 실패 → 1회 재시도, 다시 실패하면 그 결과 없이 진행하고 보고서에 빠졌다고 적는다.
- 조사 결과가 서로 다르면 지우지 않고 출처를 둘 다 적어 오케스트레이터에게 넘긴다.
- 시험 스크립트 탓인지 앱 탓인지 모르면 시험자가 판정하게 하고, 리더가 임의로 넘기지 않는다.

## 비용 상한
한 번 실행에 에이전트 호출은 대략 조사 4 + 고르기 2 + 구현 WP 수 + 리뷰 WP 수 + 시험 1–2 안에서 끝낸다. 리뷰어를 WP 마다 여러 명 붙이지 않는다.

## 테스트 시나리오
- **정상:** "판서용 기능 조사해서 넣어 줘" → 조사 3 lens → 계획 WP 4개 → WP 마다 구현 · 리뷰 1라운드 통과 → UI 시험 1회 → 폰에서 버튼 겹침 1건 → 구현자 고침 → 판정 통과 → 브랜치 커밋 · 보고.
- **에러:** 구현 WP-2 가 빌드 실패를 두 번 반복 → 구현자가 막힌 이유를 돌려줌 → 오케스트레이터가 WP-2 를 미룸으로 판정 → 나머지만 통합하고 보고에 WP-2 와 이유를 적는다.
- **부분 재실행:** "수식 기능만 UI 시험 다시" → `_workspace` 유지, board-uiux-tester 만 그 WP 기준으로 부른다.
