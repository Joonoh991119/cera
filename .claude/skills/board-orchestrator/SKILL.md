---
name: board-orchestrator
description: Cera 기능 하네스의 지휘 절차 — (필요할 때만) 조사 → 고르기 · 나누기 → 구현 → 적대적 리뷰(neon + Opus medium, 여러 라운드) → 자동 스모크 → 사람에게 UX 확인 요청 → 통합. Cera 에 기능을 더하거나 다른 도구(drawio · tldraw · OneNote · GoodNotes · Miro)의 기능을 가져오거나, 판서 · 미팅 · 도식 · 수식 · 손글씨 · 에이전트 연동 기능을 넣자는 요청이면 이 스킬을 쓴다. 후속 요청 — "다시 실행", "그 기능만 다시", "리뷰만 다시", "UX 확인 요청 다시 만들어", "결과 개선" — 도 이 스킬. 보드를 열고 닫는 운영은 플러그인 스킬 `cera`.
---

# board-orchestrator — Cera 기능 하네스

리더(메인 세션)가 이 절차를 돈다. 역할을 나누는 이유는 앵커링을 피하기 위해서다(구현자가 자기 코드를 리뷰하지 않는다, 코드를 읽은 에이전트는 사용성을 판정하지 않는다).

## 효율 규칙 (모든 단계에 적용)
- **리더의 컨텍스트를 아낀다.** 에이전트의 반환 메시지는 5줄 이하, 경로 + 판정 + 개수만. 리더는 보고 파일을 통째로 읽지 않고 필요한 절만 `grep`/`sed` 로 본다.
- **프롬프트는 파일을 가리킨다.** 계획 · 기준 · 이전 지적을 프롬프트에 다시 쓰지 말고 `_workspace/` 경로와 절 이름만 준다.
- **보고 파일 상한:** 구현 보고 ≤ 30줄, 리뷰 ≤ 40줄(지적 ≤ 12개, 각 ≤ 3줄), 계획 ≤ 150줄. 긴 로그 · 스크린샷 설명은 파일 경로로 대신한다.
- **이어 부르기:** 고침은 같은 구현자에게 SendMessage 로 이어서(새 에이전트를 만들지 않는다). 리뷰 라운드도 같은 리뷰어에게 이어서 보낸다.
- 새로 만든 `.claude/agents/*.md` 가 subagent_type 으로 잡히지 않으면 `general-purpose` 로 부르고 첫 줄에 "너는 `.claude/agents/<역할>.md` 의 역할이다. 그 파일과 스킬을 먼저 읽어라" 를 넣는다.

## 역할과 산출물

| 단계 | 역할 | 산출물 |
|---|---|---|
| 0 맥락 | 리더 | `_workspace/00_context.md`(≤ 40줄) |
| 1 조사(선택) | board-scout × 1–3 | `01_scout_<lens>.md` |
| 2 고르기 · 나누기 | board-orchestrator(또는 범위가 정해졌으면 리더) | `02_plan.md` |
| 3 구현 | board-implementer × WP | 코드 + `03_impl_WP-n.md` |
| 4 적대적 리뷰 | **neon**(`ocx-gemini-4-neon`) + **Opus medium**(`general-purpose`, model `opus`, effort `medium`) | `04_review_WP-n_<neon|opus>_r<k>.md` |
| 5a 자동 스모크 | board-uiux-tester(스크립트만 실행) | `05_smoke.md` |
| 5b 사람 UX 확인 | 리더 → **사용자** | `05_ux_request.md` → `05_ux_human.md` |
| 6 판정 · 통합 | 리더 | 커밋(브랜치), 보고 |

## Phase 0 — 맥락
- 새 주제면 `_workspace` 를 `_workspace_prev` 로 옮긴다. 기본 브랜치면 `feat/<주제>` 를 만든다.
- `00_context.md`: 요청 원문, 관련 파일 목록, 제약(링크 하나로 바로 · 폰 · 임시 보드 · 표준 라이브러리 서버 · 라이선스 · 유료 API 없음).

## Phase 1 — 조사 (필요할 때만)
- 사용자가 외부 조사 · 벤치마킹을 요청했거나, 구현 경로를 모를 때만 한다. 기본 1–2 lens, 최대 3.

## Phase 2 — 고르기 · 나누기
- 사용자가 범위를 정했으면 리더가 직접 `02_plan.md` 를 쓴다. 아니면 board-orchestrator 를 한 번 부른다.
- WP 마다 목적 · 파일 · 받아들일 기준(시험 가능한 문장) · 의존 · 병렬 묶음. 같은 파일(`server.py` · `tb_mcp.py` · `Board.jsx`)을 만지는 WP 는 차례로.

## Phase 3–4 — 구현과 적대적 리뷰 (WP 마다)
1. 구현자가 끝내면 **리뷰어 둘을 동시에** 부른다: `subagent_type: ocx-gemini-4-neon` 과 `subagent_type: general-purpose, model: opus, effort: medium`. 둘 다 `board-reviewer.md` 역할, 입력은 diff 범위 + `02_plan.md` 의 그 WP 절 + (r2 부터) 이전 지적 파일. 구현 보고는 주지 않는다.
2. 두 리뷰의 `막음/고침` 을 합쳐(중복은 하나로) 같은 구현자에게 넘긴다.
3. 다음 라운드는 **고친 부분의 diff 와 이전 지적만** 같은 리뷰어 둘에게 다시 보낸다. 둘 다 `막음 0 · 고침 0` 이면 그 WP 는 통과.
4. 최대 3라운드. 3라운드 뒤에도 막음이면 리더가 빼기 · 미루기를 정하고 보고에 적는다. 두 리뷰어가 서로 반대로 판정하면 재현 시험(코드 · 명령)으로 가린다 — 말로 다수결하지 않는다.

## Phase 5 — 스모크와 사람 UX 확인
- **5a 자동 스모크(에이전트):** `board-uiux-test` 스킬의 스크립트(`ui_smoke.py` 4기기 + 기능별 live 스크립트)를 돌려 통과/실패만 `05_smoke.md` 에. 탐색적 UI 평가는 하지 않는다.
- **5b 사람 UX 확인(사용자):** 리더가 `05_ux_request.md` 를 만든다 — 열어 볼 링크(로컬 보드, 필요하면 임시 터널), 기기별로 해 볼 일 ≤ 10개("○○ 를 하면 △△ 가 보여야 한다" 형식), 답 칸(좋음/이상함/안 됨 + 한 줄). 사용자에게 짧게 요청하고, 답을 `05_ux_human.md` 에 옮긴 뒤 '안 됨 · 이상함' 만 Phase 3 으로 돌린다. 사용자 답 없이 UX 를 '통과' 로 적지 않는다.

## Phase 6 — 통합
1. `cd web && npm test && npx vite build`, `python3 -m unittest discover -s tests -q`, `/usr/bin/python3 -m unittest discover -s tests -q`, `python3 packaging/gen.py --check`, `claude plugin validate .` 통과.
2. 버전(plugin.json → `packaging/gen.py`, web/package.json · lock 루트 두 곳). README 는 짧게 유지, 세부는 docs/guide.md.
3. 브랜치에 커밋. **push · release 는 사용자가 요청할 때만.** 보고: 넣은 것 / 미룬 것 / 사람 확인 결과 / 확인 못 한 것.

## 비용 상한
조사 ≤ 3 + 계획 ≤ 1 + 구현 WP 수 + 리뷰 (2 × WP 수 × 라운드 ≤ 3) + 스모크 1. 리뷰 라운드는 같은 에이전트를 이어 쓰므로 새 컨텍스트를 만들지 않는다.

## 에러 핸들링
- 에이전트 실패 → 1회 재시도, 다시 실패하면 그 결과 없이 진행하고 보고에 적는다. neon 이 응답하지 않으면 그 라운드는 Opus medium 단독으로 하고 표시한다.
- 시험 스크립트 탓인지 앱 탓인지 모르면 재현 명령으로 가린다.

## 테스트 시나리오
- **정상:** "수식 손글씨 변환 넣어 줘" → 리더가 계획(WP 3) → WP 마다 구현 → neon + Opus medium r1 지적 4 → 고침 → r2 둘 다 0 → 스모크 통과 → `05_ux_request.md` 로 사용자에게 폰 · 데스크톱 확인 요청 → '폰에서 버튼이 가려짐' → 고침 → 커밋.
- **에러:** WP-2 가 3라운드 뒤에도 막음 → 리더가 미룸으로 정하고 나머지만 통합.
- **부분 재실행:** "UX 확인 요청 다시 만들어" → `05_ux_request.md` 만 다시 쓴다.
