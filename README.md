# kr-ai-trader

한국 리테일 투자자를 위한 LLM 기반 자동매매 봇 템플릿. **모의투자 디폴트**, **결정론 리스크 게이트**, **멀티 LLM 백엔드**(Claude API / OpenAI API / Claude Code CLI / Codex CLI / Ollama), **Tauri 데스크톱 관측 UI**.

[![CI](https://github.com/cskwork/kr-ai-trader/actions/workflows/ci.yml/badge.svg)](https://github.com/cskwork/kr-ai-trader/actions/workflows/ci.yml)
[![Landing page](https://img.shields.io/badge/landing%20page-cskwork.github.io-2dd4a7)](https://cskwork.github.io/kr-ai-trader/)

**랜딩 페이지: https://cskwork.github.io/kr-ai-trader/** — 핵심 원칙·아키텍처·LLM 백엔드·시작 가이드를 한눈에.

투자 의사결정 원칙은 자매 리포지토리 **[cskwork/investment-agent-rules](https://github.com/cskwork/investment-agent-rules)** — Buffett, Munger, Graham, Lynch, Dalio, Marks, Soros, Druckenmiller, Simons, Greenblatt 에서 추출한 10대 계명 + 한국시장 오버레이 + AI 봇 시행 매핑. **kr-ai-trader 코드에 어느 계명이 어디에서 강제되는지** 는 [docs/principles-mapping.md](./docs/principles-mapping.md) 참고 (4 enforced / 5 partial / 1 missing).

> **DISCLAIMER**: 이 코드는 교육/연구 목적입니다. 실제 자금 손실 책임은 전적으로 사용자에게 있습니다. 자세한 내용은 [DISCLAIMER.md](./DISCLAIMER.md) 참고.

## 데스크톱 앱 (Tauri)

`desktop/app/` 에 Vite + React + TypeScript + Tauri 2.x 로 4-뷰 관측 UI 가 들어 있습니다.

| 탭 | 보여주는 것 |
|---|---|
| **Dashboard** | LLM provider/모델, 유니버스 크기, 리스크 파라미터, HALT 상태, 포지션 테이블, 현재 equity/cash |
| **Run cycle** | 티커 입력 → WebSocket 스트림 → `settings_loaded → features_computed → moderator_started → proposal_built (thesis + risks) → risk_gate_decision (accepted + reasons) → order_placed/rejected → cycle_done` 까지 단계별 카드로 표시 |
| **Journal** | 오늘 자 `journal/YYYY-MM-DD.md` 를 그대로 렌더링. 모든 매매 의사결정의 thesis/risks/거부 사유 보존 |
| **Observability** | 3초 주기 equity 샘플링 sparkline + 백엔드 상태 메트릭 |

### 실행

```bash
# 1. Python 백엔드 (8765 포트)
pip install -e ".[api]"
make api

# 2. 데스크톱 앱 (다른 터미널)
make desktop   # 또는 cd desktop/app && pnpm install && pnpm tauri dev
```

데스크톱 앱은 백엔드(127.0.0.1:8765) 에 HTTP + WebSocket 으로 연결합니다. `VITE_API_BASE` 환경변수로 백엔드 주소 오버라이드 가능.

## 실시장 풀사이클 검증

`make smoke-llm && make paper` 한 줄로 실데이터 풀사이클이 돈다.

```text
# 2026-05-18 KRX 종가로 검증한 실제 사이클
삼성전자 (005930)   RSI 70.85, 20d +29% → Risk Officer no_action
SK하이닉스 (000660) RSI 79.43, 20d +59% → LLM sell proposal (conv 0.72) → RiskGate 차단 (short-selling blocked)
NAVER (035420)      RSI 39.06, 20d -8%  → 모더레이터 no_action
```

체인: `pykrx OHLCV → compute_features → Bull+Bear+RiskOfficer (claude code -p --json-schema) → RiskGate → PaperBroker → Journal 마크다운`.

## 핵심 원칙

- **LLM은 분석가, 코드가 리스크매니저** — LLM은 매매 *제안*만, 실제 주문은 결정론 게이트가 결정
- **모의투자가 디폴트** — 실계좌 전환은 환경변수 `KIS_LIVE=1` 명시 필요
- **티커 화이트리스트** — LLM 환각으로 존재하지 않는 종목 주문 방지
- **멱등 주문 ID + reconciliation** — 중복/유실 방지

## 아키텍처

```
[데이터: KIS WS / OpenBB / DART / 뉴스]
              │
              ▼
[멀티에이전트: Analyst(Bull) / Critic(Bear) / RiskOfficer / Moderator]
              │  structured JSON {ticker, side, conviction, thesis, risks}
              ▼
[결정론 리스크 게이트: 화이트리스트·포지션·드로다운·서킷브레이커]
              │
              ▼
[Broker: KIS 모의/실 또는 PaperBroker]
              │
              ▼
[Journal: 일별 마크다운 + Slack/Telegram 알람]
```

## LLM 백엔드

| Provider | 인증 | 비고 |
|---|---|---|
| `anthropic_api` | `ANTHROPIC_API_KEY` | Claude Sonnet/Opus, tool_use로 structured output |
| `openai_api` | `OPENAI_API_KEY` | GPT-5 계열, `response_format=json_schema` |
| `claude_code_cli` | **API 키 불필요** | `claude -p --json-schema` 가 envelope `structured_output` 으로 검증 JSON 반환 |
| `codex_cli` | **API 키 불필요** | `codex` CLI 로그인된 세션 활용 |
| `ollama` | 로컬 | `http://localhost:11434`, `llama3.1`/`qwen2.5` 등 |

`.env`의 `LLM_PROVIDER` 한 줄로 전환됩니다.

## 시작 순서

```bash
# 1. 클론 후 설치
git clone <your-fork>
cd kr-ai-trader
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# 2. KIS Open API 키 발급 (한국투자증권 apiportal.koreainvestment.com)
cp .env.example .env
# .env에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NUMBER 입력
# 모의투자 계정으로 시작

# 3. LLM 백엔드 선택
# 옵션 A: Claude Code CLI (API 키 0원) — 권장
#   claude --version  # 로그인된 세션 사용
#   LLM_PROVIDER=claude_code_cli
# 옵션 B: Ollama 로컬
#   docker compose up -d ollama
#   ollama pull qwen2.5:14b-instruct
#   LLM_PROVIDER=ollama
# 옵션 C: API 키
#   LLM_PROVIDER=anthropic_api  (or openai_api)

# 4. 스모크 테스트
make smoke-llm          # LLM 프로바이더 핑
make smoke-kis          # KIS 모의계좌 현재가 조회 + 잔고

# 5. 페이퍼 트레이딩 루프 (모의투자)
make paper

# 6. 백테스트
make backtest UNIVERSE=kospi200 FROM=2024-01-01 TO=2025-12-31

# 7. (충분한 검증 후) 실계좌
KIS_LIVE=1 make live
```

## 디렉토리

```
src/kr_ai_trader/
  config.py          # Pydantic settings
  llm/               # 5종 LLM provider 추상화
  broker/            # KIS + PaperBroker
  agents/            # Bull/Bear/RiskOfficer/Moderator
  risk/              # 결정론 리스크 게이트
  execution/         # 멱등 주문 + reconciliation
  data/              # 시세·유니버스
  backtest/          # vectorbt 래퍼
  journal/           # 일별 마크다운 저널
  cli.py             # 진입점
```

## 안전 기본값

- `MAX_POSITION_PCT=3` (종목당 3%)
- `MAX_SECTOR_PCT=30`
- `DAILY_LOSS_HALT_PCT=2` (일일 -2% 도달 시 신규진입 중단)
- `DAILY_LOSS_FLATTEN_PCT=4` (-4% 시 전량청산)
- `LEVERAGE=0` (신용·미수 금지)
- `HARD_STOP_PCT=7`

`.env`로 조정 가능. 변경 시 백테스트 재실행 권장.

## 라이선스

MIT. 상세는 [LICENSE](./LICENSE).
