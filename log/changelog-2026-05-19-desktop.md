# 2026-05-19 (오후) — Tauri 데스크톱 관측 UI

매매 의사결정 체인 전체를 실시간 시각화하는 데스크톱 앱 추가. FastAPI 백엔드 + Tauri 2 + React + TypeScript.

## 추가/변경

- **`src/kr_ai_trader/api/server.py`** — FastAPI: `/health`, `/api/settings`, `/api/universe`, `/api/positions`, `/api/features/{ticker}`, `/api/journal`, `/ws/cycle` (WebSocket). CORS 화이트리스트로 Tauri dev/번들 모두 허용.
- **`pyproject.toml`** — `api` extras: fastapi / uvicorn / websockets.
- **`Makefile`** — `make api` (백엔드), `make desktop` (Tauri dev).
- **`desktop/app/`** — Vite + React + TS 스캐폴드 + Tauri 2.x.
  - `src/api.ts` — REST/WebSocket 클라이언트, `CycleEvent` 디스크리미네이티드 유니언
  - `src/App.tsx` — 4 탭 (Dashboard / Run cycle / Journal / Observability)
  - `src/views/Dashboard.tsx` — settings/universe/positions 폴링 5s
  - `src/views/CycleRunner.tsx` — 티커 입력 → WS 스트림 → kind 별 단계 카드 (proposal 의 thesis/risks, risk_gate 의 reasons, 주문 ID 까지)
  - `src/views/JournalView.tsx` — 오늘 자 `journal/YYYY-MM-DD.md` 렌더링
  - `src/views/Observability.tsx` — 3s 주기 equity sparkline + 샘플 테이블
  - `vite.config.ts` — 1420 strictPort
  - `src-tauri/tauri.conf.json` — identifier 변경, 윈도우 1280×800

## 검증

| 항목 | 결과 |
|---|---|
| FastAPI `uvicorn` 기동 | OK, 127.0.0.1:8765 |
| `/health`, `/api/settings`, `/api/features/005930` | 200, 실데이터 (281,000원 / RSI 70.85) |
| WebSocket `/ws/cycle` (삼성 005930) | settings_loaded → features → moderator (58s) → no_action → cycle_done 정상 |
| Vite dev (1420) | 200, `index.html` 정상 |
| `pnpm build` (TS + Vite) | 5 TS1484 (verbatimModuleSyntax) 수정 후 OK. 207KB JS / 4KB CSS |
| `pnpm tauri build --debug --bundles app` | 54.82s 컴파일 → `kr-ai-trader.app` 번들 생성 |
| `open kr-ai-trader.app` | 정상 실행 |

## 의사결정 이유 (Observability 핵심)

Run cycle 탭의 단계 카드가 다음 5층을 모두 노출:

1. **Features** — RSI/SMA/모멘텀 등 LLM 입력
2. **Proposal** — LLM thesis (왜 매수/매도) + risks (틀릴 시나리오) + conviction + size_pct
3. **RiskGate decision** — accepted/rejected + reasons (whitelist/포지션 한도/short-selling/leverage/HALT 어떤 게 걸렸는지)
4. **Order** — client_order_id (멱등) + broker_order_id + 체결가
5. **Cycle done** — 최종 cash + 포지션 변경

같은 정보가 Journal 탭의 마크다운에 영구 기록되어 사후 분석 가능.

## 알려진 제약

- Tauri 데스크톱 앱은 백엔드(`make api`) 가 먼저 떠있어야 데이터 표시. backend down 배지로 알림.
- 디폴트 LLM `claude_code_cli` + `haiku` 는 사이클당 종목 1개에 ~60초 (Moderator LLM 3회). 다수 종목은 비례 증가.
- `.app` 번들은 디버그 빌드 — 릴리스 빌드/코드사이닝/공증은 미구현.
