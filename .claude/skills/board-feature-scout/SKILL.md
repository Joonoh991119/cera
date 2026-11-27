---
name: board-feature-scout
description: 화이트보드 · 판서 · 도식 도구의 기능을 조사해 Cera 로 옮길 후보 카드를 쓰는 방법(카드 형식 · 채점 · 조사할 곳). board-scout 에이전트가 쓴다. "다른 화이트보드는 어떤 기능이 있나", "drawio 에서 가져올 만한 것", "판서 · 미팅에 필요한 기능 조사", "손글씨 보정 방법 찾아봐" 같은 조사 요청이면 이 스킬.
---

# board-feature-scout

## 조사 순서
1. `_workspace/00_context.md` 로 지금 있는 기능을 안다 — 이미 있는 것은 후보로 내지 않는다(개선이면 '개선' 으로).
2. Excalidraw 0.18.1 이 이미 가진 것부터 본다: `web/node_modules/@excalidraw/excalidraw/dist/types/` (특히 `excalidraw/types.d.ts` 의 `ExcalidrawProps` · `AppState`, `element/types.d.ts`, `index.d.ts` 의 export). frame · laser · 라이브러리 · `exportToSvg/Blob/Clipboard` · `convertToExcalidrawElements` · mermaid(`@excalidraw/mermaid-to-excalidraw`) 같은 것이 있는지.
3. lens 대상 도구의 공식 문서 · 소스를 본다. drawio 는 GitHub `jgraph/drawio`(Apache-2.0): 수식(MathJax), 연결선 · 웨이포인트, 도형 라이브러리, 자동 배치, mermaid/PlantUML 가져오기, 표.
4. 웹에서 사용자 불만 · 요청(포럼, 리뷰)을 찾아 "실제로 누가 아쉬워하는지" 확인한다.

## 후보 카드 형식
```
### <기능 이름>  [lens · 개선/새로]
- 누가 · 언제: (판서/미팅, 어떤 순간)
- 지금 Cera 에서 막히는 것:
- 다른 도구의 방식: (도구 · 근거 링크)
- 구현 경로: (Excalidraw API / 새 모듈 / 라이브러리 이름@버전 · 라이선스 · gzip KB / 꺼내기만)
- 동기화: (기존 요소로 표현되나? customData? 서버 바꿔야 하나?)
- 폰: (가능 / 숨김 / 다른 UI)
- 점수: 가치 1–3 · 빈도 1–3 · 비용 1–3(3=큼) → 가치×빈도÷비용
- 위험 · 모르는 것:
```

## 채점 메모
- 가치 3 = 그것 없으면 판서 · 미팅을 다른 도구로 옮겨 가는 수준. 1 = 있으면 편함.
- 비용 1 = 하루 안, 기존 API 만. 3 = 새 의존성 · 서버 변경 · 폰 UI 따로.
- 손글씨 → 글자(OCR) · 도형 인식처럼 모델이 필요한 것은 브라우저 안에서 도는지, 모델 크기, 한국어 지원을 꼭 적는다. 외부 API 로 그림을 보내는 방식은 '임시 보드 · 키 없음' 원칙과 부딪히므로 표시한다.
