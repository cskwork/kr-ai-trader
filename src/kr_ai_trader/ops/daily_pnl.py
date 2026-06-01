"""일일 손익(PnL) 추적 — 서킷브레이커 입력값 제공.

`risk/gate.py` 의 `day_pnl_pct` 는 executor 에서 현재 0.0 으로 하드코딩되어 있어
일일 손실 halt/flatten 게이트가 사실상 무력화된 상태다. 이 모듈은 KST 기준
"장 시작 자본(start_equity)" 을 작은 JSON 파일에 기록하고, 현재 자본과 비교해
당일 손익률(`pnl_pct`)을 계산해 그 값을 채워준다.

- KST 날짜 기준으로 하루를 구분한다 (executor.py / journal/recorder.py 와 동일한 Asia/Seoul).
- 파일이 없거나 손상되어도 절대 예외를 올리지 않는다 — "start 미기록" 으로 취급한다.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

log = structlog.get_logger(__name__)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_PNL_FILE = Path.home() / ".kr-ai-trader" / "daily_pnl.json"


@dataclass(frozen=True)
class DayPnL:
    """당일 손익 스냅샷. `pnl_pct` 가 서킷브레이커 입력값."""

    start_equity: float
    current_equity: float
    pnl_pct: float
    trading_date: str  # KST 기준 YYYY-MM-DD


def day_pnl_to_dict(p: DayPnL) -> dict[str, object]:
    return {
        "start_equity": p.start_equity,
        "current_equity": p.current_equity,
        "pnl_pct": p.pnl_pct,
        "trading_date": p.trading_date,
    }


def _kst_date(when: datetime | None) -> str:
    """기준 시각을 KST 날짜 문자열(YYYY-MM-DD)로. naive 입력은 UTC 로 간주."""
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KST).date().isoformat()


class DailyPnLTracker:
    """장 시작 자본을 영속화하고 당일 손익률을 계산한다.

    저장 포맷: ``{"trading_date": "YYYY-MM-DD", "start_equity": float}``.
    날짜가 바뀌면 자동으로 새 날로 롤오버한다(이전 start 는 무시).
    """

    def __init__(self, path: Path = DEFAULT_PNL_FILE) -> None:
        self._path = path

    def _read(self) -> dict[str, object]:
        """저장 파일 읽기. 없거나 손상 시 빈 dict — 절대 예외 안 올림."""
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except FileNotFoundError:
            return {}
        except Exception as exc:  # 손상 파일은 미기록으로 강등
            log.warning("daily_pnl.read_failed", path=str(self._path), error=str(exc))
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, trading_date: str, start_equity: float) -> None:
        payload = {"trading_date": trading_date, "start_equity": start_equity}
        with contextlib.suppress(Exception):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload), encoding="utf-8")

    def _start_for(self, trading_date: str) -> float | None:
        """해당 KST 날짜에 기록된 start_equity. 없거나 다른 날이면 None."""
        data = self._read()
        if data.get("trading_date") != trading_date:
            return None
        raw_start = data.get("start_equity")
        if not isinstance(raw_start, (int, float)):
            return None
        return float(raw_start)

    def start_of_day(self, equity: float, *, when: datetime | None = None) -> None:
        """오늘(KST) start_equity 기록. 멱등 — 이미 기록돼 있으면 덮어쓰지 않음."""
        trading_date = _kst_date(when)
        if self._start_for(trading_date) is not None:
            return  # 멱등: 같은 날 재호출은 무시
        self._write(trading_date, float(equity))
        log.info("daily_pnl.start_of_day", trading_date=trading_date, start_equity=float(equity))

    def compute(self, current_equity: float, *, when: datetime | None = None) -> DayPnL:
        """당일 손익률 계산. start 미기록이면 pnl_pct=0.0 (게이트 무사통과)."""
        trading_date = _kst_date(when)
        start = self._start_for(trading_date)
        current = float(current_equity)
        if start is None or start == 0.0:
            return DayPnL(
                start_equity=start or 0.0,
                current_equity=current,
                pnl_pct=0.0,
                trading_date=trading_date,
            )
        pnl_pct = (current - start) / start * 100.0
        return DayPnL(
            start_equity=start,
            current_equity=current,
            pnl_pct=pnl_pct,
            trading_date=trading_date,
        )
