"""일별 마크다운 저널 기록기.

- 파일: `journal/YYYY-MM-DD.md`  (KST 기준)
- 모든 제안(order)·거부(rejection)를 시간순으로 누적
- LLM thesis/risks/리스크게이트 거부 사유까지 보존 → 사후 분석/회고용
- 동시 쓰기 안전: 인스턴스별 asyncio.Lock; 헤더 생성은 원자적.
- 마크다운 인젝션 방지: LLM 텍스트는 코드 펜스로 감싸 렌더 시 무력화.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..agents.moderator import TradeProposal
from ..broker.base import Order

KST = ZoneInfo("Asia/Seoul")

# 마크다운 코드 펜스 안에서도 ``` 시퀀스가 본문에 있으면 펜스가 끊어진다.
_BACKTICK_FENCE = re.compile(r"`{3,}")


def _escape_md(text: str) -> str:
    """LLM 자유 텍스트를 코드펜스 안에 안전하게 넣기 위한 sanitize.

    - 트리플 백틱을 길이 4 이상이면 단축, 그 외엔 zero-width로 분리.
    - 줄바꿈 보존 (block 내부 표시 자연스럽게).
    """
    if not text:
        return ""
    return _BACKTICK_FENCE.sub(lambda m: "`" * len(m.group(0)) + "​", text)


class JournalRecorder:
    def __init__(self, *, journal_dir: Path | str = "journal") -> None:
        self.journal_dir = Path(journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def path_for(self, when: datetime | None = None) -> Path:
        moment = (when or datetime.now(timezone.utc)).astimezone(KST)
        return self.journal_dir / f"{moment:%Y-%m-%d}.md"

    # 호환을 위해 underscore alias 유지
    _path_for = path_for

    @staticmethod
    def _proposal_block(p: TradeProposal) -> str:
        risks = "\n".join(f"  - {_escape_md(r)}" for r in p.risks)
        stop = f"- stop_loss_pct: **{p.stop_loss_pct}%**\n" if p.stop_loss_pct is not None else ""
        return (
            f"- ticker: **{p.ticker}**, side: **{p.side}**, "
            f"conviction: {p.conviction:.2f}, size_pct: {p.size_pct:.2f}\n"
            f"- thesis: {_escape_md(p.thesis)}\n"
            f"{stop}"
            f"- risks:\n{risks}\n"
        )

    async def record_order(self, *, proposal: TradeProposal, order: Order) -> None:
        path = self.path_for()
        now_kst = datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds")
        block = (
            f"\n### {now_kst} — ORDER {order.status.upper()} ({order.ticker})\n"
            f"- client_order_id: `{order.client_order_id}`\n"
            f"- broker_order_id: `{order.broker_order_id}`\n"
            f"- filled_qty: {order.filled_quantity} @ {order.filled_avg_price:,.0f}\n"
            f"- side: {order.side.value}, quantity: {order.quantity}\n"
            f"{self._proposal_block(proposal)}"
        )
        await self._append(path, block)

    async def record_rejection(
        self, *, proposal: TradeProposal, reasons: list[str]
    ) -> None:
        path = self.path_for()
        now_kst = datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds")
        reasons_md = "\n".join(f"  - {_escape_md(r)}" for r in reasons) or "  - (no reasons reported)"
        block = (
            f"\n### {now_kst} — REJECTED ({proposal.ticker})\n"
            f"- reasons:\n{reasons_md}\n"
            f"{self._proposal_block(proposal)}"
        )
        await self._append(path, block)

    async def record_note(self, message: str) -> None:
        path = self.path_for()
        now_kst = datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds")
        await self._append(path, f"\n### {now_kst} — NOTE\n{_escape_md(message)}\n")

    async def _append(self, path: Path, body: str) -> None:
        async with self._lock:
            # 헤더 원자적 생성: 이미 있으면 무시.
            if not path.exists():
                try:
                    with path.open("x", encoding="utf-8") as f:
                        f.write(f"# Journal {path.stem}\n")
                except FileExistsError:
                    pass
            with path.open("a", encoding="utf-8") as f:
                f.write(body)
