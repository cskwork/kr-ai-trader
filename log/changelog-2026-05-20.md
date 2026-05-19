# 2026-05-20 — code-review + security-review 후속 하드닝

## 배경

병렬 code-reviewer + security-reviewer 두 에이전트로 전체 Python 백엔드(~2500 LOC) 감사 → CRITICAL/HIGH 결함 다수 발견. KIS_LIVE=1 전환 전 반드시 막아야 할 항목 위주로 수정.

## CRITICAL — 실계좌 전환 차단해야 할 결함

### RiskGate halt/flatten 사다리 elif 버그 (risk/gate.py)
이전: `if flatten:` ... `elif halt and buy:` → 손실이 halt(예: -3%)에 걸려도 flatten(-4%)을 만족하지 않으면 buy 차단 사유가 안 찍히는 일이 가능. 또한 halt < flatten 가정이 강제되지 않음.
변경: 두 임계값을 독립 if 로 분리. `daily_loss_halt_pct > daily_loss_flatten_pct` 조합은 Settings 로드 시 ValueError. 회귀 테스트 2개 추가.

### client_order_id 결정론화 (execution/executor.py, api/server.py)
이전: `uuid4().hex[:8]` 또는 `%Y%m%d%H%M%S` 가 매번 변동 → PaperBroker 의 `_orders[client_order_id]` dedup 이 절대 hit 되지 않음. 재전송 시 이중 체결 가능.
변경: `Executor.make_idempotent_id(strategy, ticker, side, qty, when?)` 가 `(strategy, KST date, ticker, side, qty) → sha1[:10]` 결정론 키 반환. `/ws/cycle` 의 tentative order 도 같은 헬퍼 사용.

### PaperBroker 수수료/세금 외부화 + 매수 수수료 반영 (broker/paper.py, config.py)
이전: 매수 수수료 0, 매도 거래세 0.18% 하드코딩 → 2026 KOSPI/KOSDAQ 세율과 무관, 백테/페이퍼 P&L 과대 평가.
변경: `Settings.commission_pct`, `tax_kospi_sell_pct`, `tax_kosdaq_sell_pct` 신설. PaperBroker 가 `market_overrides` 와 함께 시장별 세율 적용, 매수 수수료를 평단가에 반영. 신규 테스트 4개 (`tests/test_paper_broker_fees.py`).

### KISBroker transport 오류 전파 (broker/kis.py)
이전: `except Exception: order.status="rejected"` → 네트워크/auth 장애가 risk-gate rejection 처럼 보여 운영자 모르게 사이클 진행.
변경: lookup/transport 오류는 `BrokerError` 로 raise. 명시적 `rejected`/`error` 응답만 rejected 처리. 에러 메시지에서 `appkey/appsecret/Authorization/secretkey` 마스킹.

### 1주 미만 노출 floor 제거 (execution/executor.py, api/server.py)
이전: `qty = max(1, int(notional//price))` — size_pct 가 매우 작아도 1주가 강제 매수됨.
변경: 매수 qty < 1 이면 skip (저널에 rejection 기록). 매도 qty 는 보유 수량으로 clamp.

## HIGH

### Settings 강화 (config.py)
- `halt_file` 디폴트 `/tmp/kr-ai-trader.HALT` → `~/.kr-ai-trader/HALT` (world-writable DoS/우회 차단)
- `KIS_LIVE=1` + `KIS_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY` 동시 요구 — model_validator 로 fail-fast
- `reset_settings()` 픽스처 헬퍼 추가

### LLM JSON 처리 견고화 (llm/base.py)
- `extract_json`: 좌→우 균형 스캔으로 문자열 안의 `{}`, 본문 예시 JSON 무시, **마지막 객체** 또는 **마지막 fenced block** 선택
- `validate_against_schema`: jsonschema 가용 시 `Draft202012Validator` 로 enum/min/max/required-nested/additionalProperties 전체 검증, 없으면 기존 fallback. `pyproject.toml` 에 `jsonschema>=4.21` 추가

### Moderator 프롬프트 인젝션 표면 축소 (agents/moderator.py)
- LLM-controllable `market_context` 를 4KB 로 길이 제한
- bull/bear 둘 다 실패 시 short-circuit None
- `debate.data["verdict"]` → `.get("verdict")` 로 KeyError 방어
- TradeProposal 파싱 try/except → 부분 응답 거부

### Codex CLI 안정화 (llm/codex_cli.py)
- `asyncio.wait_for(timeout=120)` — 무한 행 차단
- UTF-8 디코드에 `errors="replace"`
- argv prompt 32KB 길이 제한 (ps 노출 + OS limit)
- NDJSON 파싱 시 `tool_call/tool_result/thought/reasoning` 이벤트 무시 → 최종 답변만 채택

### Journal 동시성 안전 (journal/recorder.py)
- 인스턴스별 `asyncio.Lock`
- `open(path, "x")` + FileExistsError 무시로 헤더 원자적 생성
- LLM 자유 텍스트 안의 백틱 펜스를 zero-width 로 무력화 (`_escape_md`)
- `_path_for` → `path_for` (public), `/api/journal` 도 KST 기준 같은 경로 사용

### FastAPI 서버 (api/server.py)
- CORS `allow_methods/headers=["*"]` → 최소 화이트리스트, `allow_credentials=False` 명시
- `/api/ohlcv/{ticker}`, `/api/features/{ticker}` 6자리 숫자 정규식 검증 — path traversal 차단
- `/ws/cycle` 의 cash 를 `[1e3, 1e10]` 범위로 clamp, 6자리 ticker 만 통과, tentative order id 도 결정론

### Sector 한도 중복 합산 수정 (risk/gate.py)
이전: 동일 ticker 매수 추가 시 기존 보유 가치가 sector 합계에 두 번 들어가 한도 위반 false positive.
변경: `projected_sector = sector_value - same_ticker_existing + new_value`. 회귀 테스트 추가.

## 테스트

- 신규: `tests/test_regression.py` 11개 (halt/flatten 독립성, idempotency, extract_json edge, schema enum/range, journal escape, sector double-count, KIS_LIVE confirm)
- 신규: `tests/test_paper_broker_fees.py` 4개 (매수 수수료, 매도 commission+tax, 코스닥 세율, total-cost rejection)
- 신규: `tests/test_executor_integration.py` 3개 (happy path + 멱등, min-notional skip, universe 외 ticker pre-quote 차단)
- 기존: `test_llm_extract.py`, `test_paper_broker.py`, conftest fixture 조정 (zero-fee 변형)
- 결과: **43/43 passed**, ruff clean, 핵심 모듈 커버리지 risk_gate 92% / paper 89% / config 94% / llm.base 74%

## 미수정 (의도적 후속 작업)

- **money math float → Decimal** — 광범위 리팩토링 필요, 별도 PR.
- **API 인증** — Tauri shell 외 사용 가정이 아직 없어 토큰 없이 진행. 향후 외부 노출 시 X-API-Token 헤더 + subtle-time-compare.
- **LLM provider 0% coverage** — httpx/subprocess respx 모킹 필요. 후속.

## 검증

```bash
make test                        # 43/43 passed
python -m ruff check src/ ...    # All checks passed!
kr-trader info                   # provider/KIS live/universe/risk 정상 출력
python -c "from kr_ai_trader.api import server"     # FastAPI import OK
```
