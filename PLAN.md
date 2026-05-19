# PLAN.md — 미구현 작업 로드맵

> 골격 + **실시장 풀사이클 검증** 까지 완료 (2026-05-19, `log/changelog-2026-05-19.md`). pykrx OHLCV → Bull+Bear+RiskOfficer (`claude -p --json-schema`) → RiskGate → PaperBroker → Journal 체인 동작 확인.
>
> 이어서 작업할 때 이 파일을 체크리스트로 사용하세요. 끝난 항목은 `[x]` 로 체크하고 PR 또는 커밋 메시지에 항목 번호를 인용하세요.

---

## 완료된 골격 (참고)

- [x] 프로젝트 메타: `README.md`, `LICENSE`, `DISCLAIMER.md`, `.gitignore`, `.env.example`, `pyproject.toml`, `Makefile`, `docker-compose.yml`, `.pre-commit-config.yaml`, `.gitleaks.toml`
- [x] `config.py` — Pydantic Settings, 5종 LLM/KIS/리스크 파라미터/알람/HALT 파일
- [x] `llm/` — 공통 `Protocol`, JSON 추출·검증, 5종 백엔드(anthropic_api, openai_api, claude_code_cli, codex_cli, ollama), 팩토리
- [x] `broker/` — `Broker` 프로토콜, `PaperBroker`(인메모리, 거래세 0.18% 반영), `KISBroker`(`python-kis` 위 어댑터, 모의/실 플래그), 팩토리
- [x] `data/universe.py` — pykrx KOSPI200/KOSDAQ150 로드, fallback 10종목
- [x] `risk/gate.py` — HALT 파일, 화이트리스트, 포지션·섹터 한도, 일일 손실 서킷브레이커, 레버리지=0 검사
- [x] `agents/` — `PROPOSAL_SCHEMA`, `DEBATE_SCHEMA`, Bull/Bear/RiskOfficer 3에이전트 + Moderator 합의
- [x] `execution/executor.py` — 멱등 client_order_id, 사이즈 계산, 게이트 통과 시 주문
- [x] `journal/recorder.py` — 일별 마크다운 저널
- [x] `backtest/runner.py` — vectorbt 래퍼 (수수료+거래세+슬리피지 디폴트)
- [x] `cli.py` — `kr-trader info`, `kr-trader ping-llm`

---

## 1. 데이터 파이프라인 (우선순위 1)

현재 `data/universe.py` 만 있음. 신호 생성을 위한 입력 데이터가 없으면 에이전트가 무의미.

- [x] **1.1** `data/prices.py` — pykrx OHLCV + `compute_features` (RSI/SMA/모멘텀) + parquet 캐시. KIS WS 실시간 구독은 미구현
  - `get_ohlcv(ticker, start, end)` (pykrx) ✓
  - `latest_quote(ticker)` ✓
  - `compute_features(ticker)` → `PriceSummary` (RSI14, SMA5/20, pct_change_1/5/20d) ✓
  - 캐싱: `cache/prices/{ticker}.parquet` ✓
  - 미구현: `subscribe_realtime(tickers, on_tick)` (KIS WebSocket, 라이브용)
- [ ] **1.2** `data/dart.py` — DART OpenAPI 공시 수집
  - https://opendart.fss.or.kr/ API 키 발급 (`DART_API_KEY`)
  - 최근 N일 공시 → 종목별 dict
  - `.env.example` 에 `DART_API_KEY` 추가
- [ ] **1.3** `data/news.py` — 뉴스 수집
  - 1차: 네이버 뉴스 RSS, 연합뉴스 RSS
  - 2차(선택): 한경 컨센서스, OpenBB
  - 종목별 최근 N건 본문 + 발행시간
- [ ] **1.4** `data/sector_map.py` — 티커→섹터 매핑 (KRX 섹터 분류)
  - `RiskGate.sector_map` 으로 주입 → 섹터 한도 활성화
- [x] **1.5** `data/calendar.py` — KRX 영업일/거래시간/동시호가/이전영업일. pykrx fallback 포함

## 2. 실행 루프 스크립트 (우선순위 1)

`Makefile` 의 `paper`, `live`, `smoke-*`, `backtest` 가 호출하는 실제 스크립트.

- [x] **2.1** `scripts/smoke_llm.py` — `get_llm().healthcheck()` + PROPOSAL_SCHEMA 더미 1회 (검증됨)
- [ ] **2.2** `scripts/smoke_kis.py` — KIS 모의계좌 현재가 조회 + 잔고 조회 + 1주 매수/매도
- [x] **2.3** `scripts/run_paper.py` — `--top-n N --cycles M --sleep S --json-out path` 풀사이클 실행
- [x] **2.7** `scripts/demo_buy_then_sell.py` — 시드 매수 → LLM 매도 → 체결까지 단일 흐름 검증용
- [ ] **2.4** `scripts/run_live.py` — `KIS_LIVE=1` 가드 + paper 와 동일 로직
- [ ] **2.5** `scripts/run_backtest.py` — typer CLI, LLM 신호 사전 생성 후 vectorbt 호출
- [ ] **2.6** `src/kr_ai_trader/execution/reconciliation.py` — 1분마다 KIS 잔고 ↔ 내부 ledger 비교, 불일치 시 알람+정지

## 3. 알람·운영 안전장치 (우선순위 2)

- [ ] **3.1** `src/kr_ai_trader/ops/alerts.py` — Slack/Telegram 클라이언트
  - `send_alert(level, message)` 통합 인터페이스
  - 주문 체결, 거부, 서킷브레이커 발동 시 자동 전송
- [ ] **3.2** `src/kr_ai_trader/ops/halt.py` — HALT 파일 핸들러 + CLI (`kr-trader halt on/off`)
- [ ] **3.3** 구조화 로그(structlog) → JSON 파일 + 콘솔 dual sink
- [ ] **3.4** 일일 손실 추적 모듈 (start_of_day_equity 저장, 현재 PnL% 계산)

## 4. 테스트 (우선순위 1, 커밋 전 필수)

- [x] **4.1** `tests/test_risk_gate.py` (9 케이스, 0.08초)
  - 화이트리스트 외 티커 거부
  - max_position_pct 초과 거부
  - daily_loss_halt 시 매수만 거부, 매도는 허용
  - daily_loss_flatten 시 매수 거부
  - 레버리지=0 + cash 부족 시 거부
  - HALT 파일 존재 시 거부
- [x] **4.2** `tests/test_paper_broker.py` (5 케이스)
  - 매수 → 잔고 차감 + 포지션 생성
  - 매도 → 거래세 0.18% 반영 + 포지션 감소
  - 멱등: 동일 client_order_id 2회 호출 결과 동일
  - 잔고 부족 매수 거부
- [x] **4.3** `tests/test_llm_extract.py` (8 케이스: plain/fence/숫자 타입 등)
- [ ] **4.4** `tests/test_llm_providers.py` (mock httpx/subprocess)
  - Ollama 응답 파싱
  - Claude Code CLI envelope 파싱
  - Codex CLI NDJSON 파싱
- [ ] **4.5** `tests/test_executor.py`
  - 게이트 거부 시 주문 안 나가고 저널에 rejection 기록
  - 게이트 통과 시 주문 + 저널 order 기록
- [ ] **4.6** `tests/conftest.py` — 공용 fixtures (Settings 오버라이드, PaperBroker, tmp journal)
- [ ] **4.7** 커버리지 80% 이상 (`make test`)

## 5. CI / 배포 (우선순위 2)

- [x] **5.1** `.github/workflows/ci.yml` — 3.10/3.11/3.12 matrix + ruff + pytest + gitleaks 잡
- [ ] **5.3** `.github/workflows/docker.yml` (선택)
  - 컨테이너 빌드 & ghcr.io 푸시

## 6. 문서화 (우선순위 2)

- [ ] **6.1** `docs/architecture.md` — 시퀀스 다이어그램 (Mermaid)
- [ ] **6.2** `docs/llm-providers.md` — 5종 백엔드 셋업 가이드 (Claude Code CLI 로그인, Codex CLI 로그인, Ollama 모델 다운로드)
- [ ] **6.3** `docs/kr-regulation.md` — 자동매매 합법성, 일임투자 라이선스, 세금
- [ ] **6.4** `docs/risk-tuning.md` — Half-Kelly, walk-forward, 백테 함정 (lookahead/survivorship)
- [ ] **6.5** README 에 데모 GIF 또는 스크린샷

## 7. 잔여 개선 (우선순위 3)

- [x] **7.1** `datetime.utcnow()` deprecation 제거 → `datetime.now(timezone.utc)` 일괄 교체
- [ ] **7.2** `jsonschema` 패키지 도입 → `validate_against_schema` 강화 (enum, minimum/maximum, additionalProperties 등)
- [ ] **7.3** Prompt caching: Anthropic `cache_control` + OpenAI prompt caching 활성화 (시스템 프롬프트·종목 메타)
- [ ] **7.4** LiteLLM 백엔드 옵션 추가 (선택)
- [ ] **7.5** Strategies 디렉토리 분리: `src/kr_ai_trader/strategies/{momentum,meanrev,llm_news}.py`
- [ ] **7.6** Walk-forward 자동화 (`scripts/walk_forward.py`)
- [ ] **7.7** 백테 → 페이퍼 → 라이브 단계 게이팅 자동화 (성과 기준 미달 시 다음 단계 차단)
- [ ] **7.8** 해외주식(미국) 어댑터 — KIS Open API 해외주식 엔드포인트
- [ ] **7.9** 암호화폐 어댑터 (업비트) — `pyupbit` 위에 같은 `Broker` 프로토콜 구현
- [ ] **7.10** 멀티 LLM 앙상블: Bull=Claude, Bear=GPT-5, RiskOfficer=Ollama 로 분산

## 8. 사용 흐름 검증 체크리스트 (PLAN 완료 정의)

이어서 작업을 마치면 아래가 동작해야 합니다:

```bash
make dev
cp .env.example .env  # 키 채우기
make smoke-llm        # 선택한 LLM 백엔드 OK
make smoke-kis        # KIS 모의계좌 OK
make test             # 커버리지 80%+
make paper            # 페이퍼 트레이딩 루프 1사이클 무사고
make backtest UNIVERSE=kospi200 FROM=2024-01-01 TO=2025-12-31
# 4주 후 KIS_LIVE=1 make live (실계좌 소액)
```

---

## 작업 순서 권장

1. **4(테스트) + 1.1(prices) + 2.1·2.2(스모크 스크립트)** 부터. 골격이 실제 동작하는지 검증 가능해짐.
2. 그 다음 **2.3(paper loop)** + **3.4(daily PnL)** — 모의투자에서 한 사이클 돌리기.
3. **1.2·1.3(DART·뉴스)** — LLM 컨텍스트 풍성하게.
4. **5(CI)** — public repo 이므로 PR 받기 전 필수.
5. 나머지(6, 7) 점진 개선.

## 막혔을 때

- 한국 증권사 API 차이 → `docs/llm-providers.md` 대신 `docs/brokers.md` 신설 후 KIS/키움 REST/LS 비교 정리
- LLM 응답 품질 → 시스템 프롬프트 튜닝 + `agents/schemas.py` 의 `risks.minItems` 상향
- 백테 결과 너무 좋음 → lookahead bias 의심, 신호 생성 시점에 t-1 종가까지만 사용했는지 확인
