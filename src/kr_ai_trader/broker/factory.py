from __future__ import annotations

from ..config import Settings, get_settings
from .base import Broker
from .paper import PaperBroker


def get_broker(settings: Settings | None = None, *, force_paper: bool = False) -> Broker:
    s = settings or get_settings()
    if force_paper or not (s.kis_app_key and s.kis_app_secret and s.kis_account_number):
        return PaperBroker(initial_cash=10_000_000.0)

    from .kis import KISBroker

    return KISBroker(
        app_key=s.kis_app_key.get_secret_value(),
        app_secret=s.kis_app_secret.get_secret_value(),
        account_number=s.kis_account_number,
        is_live=s.kis_live,
    )
