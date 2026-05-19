"""일별 마크다운 저널 기록기.

- 파일: `journal/YYYY-MM-DD.md`
- 모든 제안(order)·거부(rejection)를 시간순으로 누적
- LLM thesis/risks/리스크게이트 거부 사유까지 보존 → 사후 분석/회고용
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..agents.moderator import TradeProposal
from ..broker.base import Order

KST = ZoneInfo("Asia/Seoul")


class JournalRecorder:
    def __init__(self, *, journal_dir: Path | str = "journal") -> None:
        self.journal_dir = Path(journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, when: datetime | None = None) -> Path:
        moment = (when or datetime.now(timezone.utc)).astimezone(KST)
        return self.journal_dir / f"{moment:%Y-%m-%d}.md"

    @staticmethod
    def _proposal_block(p: TradeProposal) -> str:
        risks = "\n".join(f"  - {r}" for r in p.risks)
        stop = f"- stop_loss_pct: **{p.stop_loss_pct}%**\n" if p.stop_loss_pct is not None else ""
        return (
            f"- ticker: **{p.ticker}**, side: **{p.side}**, "
            f"conviction: {p.conviction:.2f}, size_pct: {p.size_pct:.2f}\n"
            f"- thesis: {p.thesis}\n"
            f"{stop}"
            f"- risks:\n{risks}\n"
        )

    async def record_order(self, *, proposal: TradeProposal, order: Order) -> None:
        path = self._path_for()
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
        path = self._path_for()
        now_kst = datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds")
        reasons_md = "\n".join(f"  - {r}" for r in reasons) or "  - (no reasons reported)"
        block = (
            f"\n### {now_kst} — REJECTED ({proposal.ticker})\n"
            f"- reasons:\n{reasons_md}\n"
            f"{self._proposal_block(proposal)}"
        )
        await self._append(path, block)

    async def record_note(self, message: str) -> None:
        path = self._path_for()
        now_kst = datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds")
        await self._append(path, f"\n### {now_kst} — NOTE\n{message}\n")

    async def _append(self, path: Path, body: str) -> None:
        if not path.exists():
            header = f"# Journal {path.stem}\n"
            path.write_text(header, encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(body)
