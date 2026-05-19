# 2026-05-19 — 풀사이클 검증

골격(skeleton)에서 **실시장 데이터 → 의사결정 → 페이퍼 체결** 풀사이클까지 동작 검증.

## 변경 요약

- **데이터 파이프라인**: `data/prices.py` (pykrx OHLCV + RSI/SMA/모멘텀 + parquet 캐시), `data/calendar.py` (KRX 영업일/거래시간/동시호가)
- **LLM**: `claude_code_cli.py` 를 `claude -p --json-schema` 로 단순화 → envelope `structured_output` 필드에서 검증 JSON 추출. 텍스트 파싱 폴백 유지
- **저널**: `journal/recorder.py` 신규 — 일별 마크다운에 `ORDER FILLED`, `REJECTED`, `NOTE` 기록
- **스크립트**: `scripts/smoke_llm.py`, `scripts/run_paper.py`, `scripts/demo_buy_then_sell.py`
- **테스트**: pytest 22개 추가 (risk_gate 9 + paper_broker 5 + llm_extract 8)
- **CI**: `.github/workflows/ci.yml` (3.10/3.11/3.12 matrix + ruff + gitleaks)
- **호환성**: `requires-python>=3.10` 으로 완화 (StrEnum → `str, Enum`), `datetime.utcnow()` → `datetime.now(timezone.utc)`

## 풀사이클 결과 (2026-05-18 종가 기준)

| 종목 | RSI14 | 20d% | LLM 결정 | RiskGate | 최종 |
|---|---|---|---|---|---|
| 005930 (삼성전자) | 70.85 | +29.20% | hold (Risk Officer reject) | — | no order |
| 000660 (SK하이닉스) | 79.43 | +59.31% | **sell, conv 0.72, size 2.5%** | **rejected** (short-selling blocked) | journal REJECTED |
| 035420 (NAVER) | 39.06 | -8.47% | hold | — | no order |

**핵심 검증 포인트**: LLM이 SK하이닉스 과매수를 정확히 잡아 매도 신호를 제시했지만, 보유 포지션 없는 상태에서 단순 매도는 공매도 → RiskGate가 결정론으로 차단. LLM이 분석가, 코드가 리스크매니저라는 핵심 원칙이 실제로 작동.

## 의사결정 기록

1. `--bare` 옵션은 OAuth 세션 미사용 → CLI 가 ANTHROPIC_API_KEY 만 읽음. 기본 OFF.
2. pykrx 가 KRX 인덱스 조회용으로 KRX_ID/KRX_PW 환경변수를 요구하지만 개별 종목 OHLCV 만 쓰는 본 프로젝트에는 무영향. 로그에 경고만 출력.
3. Moderator 가 종목당 3 LLM 콜 (Bull/Bear/Risk) 사용 → 3종목 풀사이클 ~3분. `--top-n 1` 디폴트.
4. 매수 강제 데모(`scripts/demo_buy_then_sell.py`)는 LLM 응답 변동성으로 항상 매도를 반환하지는 않음 (Risk Officer 의 보수 성향). RSI 79 이상 종목에서 매도 신호 확률 ~50%.

## 미구현 (PLAN.md 참조)

- 데이터: DART 공시, 뉴스 RSS, 섹터 매핑
- 실행: smoke_kis (실 KIS 호출), run_live 가드, reconciliation
- 운영: Slack/Telegram, HALT CLI, 일일 PnL 추적
- 백테: vectorbt 래퍼 실호출 (의존성 미설치)
- 문서: architecture.md, llm-providers.md, kr-regulation.md, risk-tuning.md
