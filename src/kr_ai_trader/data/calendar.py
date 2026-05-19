"""KRX 거래 캘린더.

- 거래시간(KST): 정규 09:00-15:30, 동시호가 08:30-09:00 & 15:20-15:30
- 휴장일: 토/일 + 한국 공휴일 + KRX 별도 휴장
- pykrx 의 `get_previous_business_day` / `get_nearest_business_day_in_a_week` 활용
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

REGULAR_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)
PRE_AUCTION_OPEN = time(8, 30)
CLOSING_AUCTION_OPEN = time(15, 20)


@dataclass(frozen=True)
class MarketSession:
    is_business_day: bool
    is_regular_session: bool
    is_pre_auction: bool
    is_closing_auction: bool
    now_kst: datetime

    @property
    def can_place_market_order(self) -> bool:
        return self.is_business_day and self.is_regular_session


def now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(KST)


def is_business_day(d: date) -> bool:
    """KRX 영업일 판정. pykrx 의 `get_nearest_business_day_in_a_week` 가 같은 날 반환하면 영업일."""
    if d.weekday() >= 5:
        return False
    try:
        from pykrx import stock as krx_stock

        ymd = d.strftime("%Y%m%d")
        nearest = krx_stock.get_nearest_business_day_in_a_week(date=ymd)
        return str(nearest) == ymd
    except Exception:
        return d.weekday() < 5


def previous_business_day(d: date | None = None) -> date:
    target = d or now_kst().date()
    try:
        from pykrx import stock as krx_stock

        prev = krx_stock.get_previous_business_day(date=target.strftime("%Y%m%d"))
        return datetime.strptime(str(prev), "%Y%m%d").date()
    except Exception:
        cur = target - timedelta(days=1)
        while cur.weekday() >= 5:
            cur -= timedelta(days=1)
        return cur


def market_session(at: datetime | None = None) -> MarketSession:
    moment = (at or now_kst()).astimezone(KST)
    today = moment.date()
    t = moment.time()
    biz = is_business_day(today)
    return MarketSession(
        is_business_day=biz,
        is_regular_session=biz and REGULAR_OPEN <= t < REGULAR_CLOSE,
        is_pre_auction=biz and PRE_AUCTION_OPEN <= t < REGULAR_OPEN,
        is_closing_auction=biz and CLOSING_AUCTION_OPEN <= t < REGULAR_CLOSE,
        now_kst=moment,
    )
