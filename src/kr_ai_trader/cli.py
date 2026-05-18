"""CLI 진입점. `kr-trader --help`"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from .config import get_settings
from .llm import get_llm

app = typer.Typer(add_completion=False, help="kr-ai-trader CLI")
console = Console()


@app.command()
def info() -> None:
    """현재 설정 요약."""
    s = get_settings()
    console.print(f"[bold]LLM provider[/]: {s.llm_provider}")
    console.print(f"[bold]KIS live[/]: {s.kis_live} (False = 모의투자)")
    console.print(f"[bold]Universe[/]: {s.universe}")
    console.print(
        f"[bold]Risk[/]: pos={s.max_position_pct}% sector={s.max_sector_pct}% "
        f"halt={s.daily_loss_halt_pct}% flatten={s.daily_loss_flatten_pct}% "
        f"stop={s.hard_stop_pct}%"
    )


@app.command(name="ping-llm")
def ping_llm() -> None:
    """LLM 프로바이더 healthcheck."""
    s = get_settings()
    llm = get_llm(s)
    ok = asyncio.run(llm.healthcheck())
    status = "[green]OK[/]" if ok else "[red]FAIL[/]"
    console.print(f"{llm.name} ({llm.model}): {status}")


if __name__ == "__main__":
    app()
