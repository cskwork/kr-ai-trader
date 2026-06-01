# 2026-06-02 — 리서치 레이어 + 운영 안전장치

LLM 이 **기술적 지표만** 보고 매매 제안을 내던 구조를 보강. 펀더멘털·공시·뉴스·섹터를 함께 공급하고, 그동안 스텁이던 일일 PnL 서킷브레이커를 실제로 구동하도록 배선. 모의(paper) 운영에 필요한 알람·잔고 대조를 추가.

## 왜 (rationale)

- **안전마진 원칙 위배**: 자매 리포지토리 `investment-agent-rules` 의 Margin of Safety 계명은 가격이 가치 대비 싼지를 요구하는데, LLM 이 받는 컨텍스트는 RSI/SMA/모멘텀 같은 **기술적 신호뿐**이었음. 밸류에이션(PER/PBR/EPS), 공시 이벤트, 뉴스 흐름, 섹터 정보가 없으면 LLM 은 가치 판단을 할 근거가 없음. → 리서치 레이어로 입력을 풍성하게.
- **서킷브레이커 미구동**: `RiskGate` 는 `day_pnl_pct` 임계치(halt/flatten) 검사를 갖고 있었지만 실제 PnL 을 계산해 주입하는 모듈이 없어 사실상 동작하지 않았음. → `DailyPnLTracker` 로 KST 장 시작 자본을 영속화하고 매 사이클 PnL% 를 계산해 게이트에 주입.
- **paper-safe 운영**: 모의에서도 잔고 드리프트와 거부/정지 이벤트를 사람이 알아야 안전. → 알람 + reconciliation.

## 변경 요약

### 리서치 데이터 (PLAN 1.2 / 1.3 / 1.4 / 1.6 / 1.7)

- **`data/fundamentals.py`** — 네이버 금융 모바일 API(로그인 불필요) 우선 → `FundamentalSummary`(PER/PBR/EPS/BPS/배당수익률/DPS), pykrx `get_market_fundamental_by_date`(KRX_ID/KRX_PW 필요) fallback + parquet 캐시. NaN/예외/빈 응답은 None 으로 강등, 절대 raise 안 함.
  - **사유**: pykrx 1.2.x 부터 펀더멘털 조회에 KRX 회원 로그인을 요구 → 라이브에서 전 필드 None 강등 확인됨. 리테일 사용자가 키 없이 바로 쓰도록 네이버를 1순위로 전환. 라이브 E2E 검증 완료(005930 PER 28.21/PBR 4.85, 000660 PER 22.83, 035420 PER 23.57).
- **`data/dart.py`** — OpenDART REST. corpCode.xml(ZIP) 1회 다운로드 후 캐시, 최근 N일 공시를 `Disclosure` 리스트로. `DART_API_KEY` 미설정 시 빈 리스트.
- **`data/news.py`** — Google News RSS(종목명 쿼리, ko-KR/en-US). 종목당 N건 헤드라인+출처+발행시간. HTTP/파싱 오류 시 빈 리스트.
- **`data/sector_map.py`** — pykrx 업종지수 구성종목 → 티커별 섹터. 캐시 + 정적 fallback. `RiskGate.sector_map` 으로 주입돼 섹터 한도 활성화.
- **`data/research.py`** — 위를 한데 모으는 집계기. `build_research_context(ticker, settings)` → `ResearchContext`, `research_to_context_string` 이 모더레이터 4000자 클램프 아래의 JSON 컨텍스트(technical + fundamentals + disclosures + news + sector)를 생성. 회사명 미해결 시 뉴스 쿼리를 건너뛰고, DART 키 없으면 공시 호출 자체를 생략.

### 운영 안전장치 (PLAN 2.6 / 3.1 / 3.4)

- **`ops/daily_pnl.py`** — `DailyPnLTracker`. KST 날짜 기준 장 시작 자본을 JSON(`DAILY_PNL_FILE`)에 영속화, `start_of_day` 멱등, 날짜 롤오버 시 자동 리셋, 손상 파일은 미기록으로 강등. `compute(equity)` → `pnl_pct`. 이 값이 `Executor.execute(..., day_pnl_pct=...)` 를 통해 `RiskGate` 의 halt/flatten 서킷브레이커를 실제 구동.
- **`ops/alerts.py`** — `send_alert(level, message)` async 통합. Slack webhook + Telegram bot 동시 전송, 채널별 결과 dict 반환. 미설정/HTTP 오류/네트워크 예외는 False 로 graceful degrade(한 채널 실패가 다른 채널을 막지 않음).
- **`execution/reconciliation.py`** — `reconcile(broker, ledger)` 로 missing/extra/qty_mismatch 탐지(tolerance 지원), `reconcile_and_guard` 가 불일치 시 HALT 파일 생성 + 알람. 브로커 예외는 안전한 빈 결과로 강등하되, 알람이 실패해도 HALT 는 반드시 떨어지도록 보장.

### 배선 (scripts/run_paper.py)

매 사이클이 `build_research_context` → `research_to_context_string` 을 모더레이터에 전달하고, `DailyPnLTracker.compute` 결과를 `executor.execute(..., day_pnl_pct=...)` 로 넘겨 서킷브레이커를 구동. 거부 시 `send_alert` 호출. config 에 리서치/알람/운영 설정 추가, `.env.example` 에 `DART_API_KEY`·`DART_LOOKBACK_DAYS`·`ENABLE_DART`·`NEWS_LOOKBACK_ITEMS`·`ENABLE_NEWS`·`SLACK_WEBHOOK_URL`·`TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`·`RECONCILIATION_INTERVAL_SEC`·`DAILY_PNL_FILE` 반영.

## 테스트

신규 모듈 전부 단위 테스트 추가. 외부 의존성(pykrx/네트워크)은 monkeypatch·respx 로 완전 격리 — 실제 네트워크나 pykrx 호출 0.

- `test_fundamentals.py`, `test_dart.py`, `test_news.py`, `test_sector_map.py`, `test_research.py`
- `test_daily_pnl.py`, `test_alerts.py`, `test_reconciliation.py`
- `test_executor_integration.py` (PaperBroker + RiskGate + Journal 한 흐름)

## 검증

```
PYTHONPATH=src ruff check src tests scripts   → All checks passed!
PYTHONPATH=src python -m pytest -q             → 108 passed in 0.68s
```

### 라이브 데이터 검증 (실제 네트워크)

모킹 테스트와 별개로 실 API 를 직접 호출해 확인:

- 기술적 지표(pykrx OHLCV): OK — 삼성전자 종가/RSI 정상.
- 펀더멘털(네이버): OK — 005930/000660/035420 PER·PBR·EPS 실값 수신.
- 뉴스(Google News RSS): OK — 종목별 실제 헤드라인 수신.
- 섹터: pykrx 업종지수 열거 실패 시 정적 fallback 으로 강등(전기전자 등) — 라이브 fetch 는 미작동, fallback 동작 확인.
- DART: `DART_API_KEY` 미보유로 라이브 미검증(설계상 빈 리스트로 off).

## 테스트 확충 + 아키텍처 문서

리서치/운영 모듈 추가 직후 미커버였던 경계(api/server, llm providers, broker/kis, backtest/runner, cli, moderator, prices/universe/calendar)를 단위·통합 테스트로 메웠다.

- **커버리지 점프**: 위 모듈 대부분이 0~50% → 93~100% 로 상승. **총 커버리지 94%** (1770 stmts / 98 miss), **269 passed**, `ruff check src tests scripts` All checks passed. 80% 목표를 전체·개별 모듈 모두에서 초과.
  - api/server 0→93%, llm/* providers 0→96~100%, broker/kis 0→100%, backtest/runner 0→100%, cli 0→96%, agents/moderator 50→100%, data/prices 34→100%, data/universe 22→100%, data/calendar 46→100%.
  - 남은 저커버: `llm/base.py` 77% (추상 베이스의 폴백/에러 경로), `data/research.py` 85%, `broker/paper.py` 89% — 모두 80% 미만이나 동작 경로는 다른 테스트가 간접 커버.
- **문서 2종 추가**: `docs/architecture.md`(전체 사이클 시스템 설계 + Mermaid 시퀀스), `docs/codebase.md`(패키지별 목적 → 공개 API → 협력 모듈 → 커버 테스트 개발자 지도). README `## 문서` 섹션에서 `docs/principles-mapping.md` 와 함께 링크. PLAN 6.1 완료 체크 + 6.6(codebase.md) 추가.
