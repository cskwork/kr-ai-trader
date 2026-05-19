# 10대 투자원칙 → kr-ai-trader 코드 매핑

자매 리포 [cskwork/investment-agent-rules](https://github.com/cskwork/investment-agent-rules)
의 10대 계명이 실제 코드 어디에서 강제되는지 투명하게 정리.

| # | 계명 | 코드 위치 / 메커니즘 | 상태 |
|---|---|---|---|
| 1 | Capital Preservation First | `risk/gate.py` HALT 파일, `daily_loss_halt_pct`/`flatten_pct` 서킷브레이커, `leverage=0`, 공매도 차단 | enforced |
| 2 | Margin of Safety | `agents/moderator.py` Risk Officer 시스템 프롬프트 (conviction<0.6 reject). 정량 fair-value 산출은 미구현 | partial |
| 3 | Circle of Competence | `data/universe.py` KOSPI200 화이트리스트 + `risk/gate.py` 화이트리스트 외 티커 거부 (LLM 환각 차단) | enforced |
| 4 | Second-Level Thinking | `agents/moderator.py` Bull + Bear + Risk Officer 3-에이전트 토론, 합의 시에만 proceed | enforced |
| 5 | Cycles & Reflexivity | `agents/moderator.py` 시스템 프롬프트에 "RSI>70 또는 20일 +30% 시 진입 보수화" 휴리스틱. `data/prices.py compute_features` 가 RSI14/모멘텀 제공 | partial |
| 6 | Position Sizing & Asymmetric R:R | `risk/gate.py` `proposed_stop_loss_pct > hard_stop_pct` 시 거부, `max_position_pct=3%` 한도. Kelly·VaR 정량 미구현 | partial |
| 7 | Concentration vs Diversification | `risk/gate.py` `max_position_pct=3%`, `max_sector_pct=30%` (`sector_map` 주입 시 활성). 무상관성 자산 분산은 미구현 | partial |
| 8 | Long-term Compounding | 현재 단기 시그널 위주. 보유기간 가중·턴오버 제한은 미구현 | missing |
| 9 | Process Over Outcome | `journal/recorder.py` 모든 의사결정 thesis/risks/거부 사유 영구 기록. 멱등 `client_order_id`. OOS 검증·시그널 decay 모니터링은 미구현 | partial |
| 10 | Behavioral Discipline | HALT 파일 kill switch, 일일 손실 CB. in-session rule edits 차단은 미구현 (Settings 가 런타임에 read-only 가 아님) | partial |

## 런타임에 어떻게 보이나

`RiskGate.evaluate()` 가 `RiskDecision` 에 `principles_applied` + `principles_violated` 를 반환합니다. WebSocket 의 `risk_gate_decision` 이벤트에 두 필드가 실리고, 데스크톱 앱 **분석 실행** 탭의 카드에 컬러 칩으로 표시됩니다:

- 초록 칩 (✓): 적용되었고 통과
- 빨강 칩 (✗): 적용되었고 위배 → 주문 차단

`agents/moderator.py` 의 Bull/Bear/Risk Officer 시스템 프롬프트에 10대 계명 전문이 인용되어 LLM 도 어떤 계명을 적용했는지 thesis 에 명시하도록 유도됩니다.

## 알려진 갭

1. **Margin of Safety 정량화** — 현재는 LLM 의 정성 평가. DCF/PER/PBR 기반 fair value 산출 모듈이 필요.
2. **Cycles** — 시장 전체 사이클 (KOSPI200 valuation percentile, 신용잔고/매수차익 등) 미사용. 종목 단위 RSI 만.
3. **Compounding** — 단기 매매에 편향. 최소 보유기간, 회전율 한도가 필요.
4. **Process** — 시그널 백테 OOS 검증·decay 모니터링이 `backtest/runner.py` 의 vectorbt 래퍼 외에 자동화 없음.
5. **Behavioral** — Settings (`config.py`) 가 런타임에 immutable 하지 않음. 실거래 중 파라미터 편집 차단 미구현.

이 갭은 [PLAN.md](../PLAN.md) 의 우선순위 3 항목으로 추적됩니다.
