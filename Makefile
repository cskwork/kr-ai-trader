.PHONY: help install dev fmt lint type test smoke-llm smoke-kis paper demo backtest live clean

help:
	@echo "Targets:"
	@echo "  install     editable install (runtime only)"
	@echo "  dev         editable install + dev extras + pre-commit"
	@echo "  fmt         ruff format"
	@echo "  lint        ruff check"
	@echo "  type        mypy"
	@echo "  test        pytest"
	@echo "  smoke-llm   LLM provider connectivity check"
	@echo "  smoke-kis   KIS account & quote smoke test (paper)"
	@echo "  paper       paper-trading 1 cycle (TOP_N, CYCLES env)"
	@echo "  demo        seed buy → LLM sell → fill 풀사이클 데모"
	@echo "  backtest    UNIVERSE=kospi200 FROM=YYYY-MM-DD TO=YYYY-MM-DD"
	@echo "  live        KIS_LIVE=1 로 실계좌 실행 (사전 검증 필수)"
	@echo "  clean       remove caches"

install:
	pip install -e .

dev:
	pip install -e ".[dev]"
	pre-commit install

fmt:
	ruff format src tests scripts

lint:
	ruff check src tests scripts

type:
	mypy src

test:
	pytest --cov=kr_ai_trader --cov-report=term-missing

smoke-llm:
	PYTHONPATH=src python -m scripts.smoke_llm

smoke-kis:
	PYTHONPATH=src python -m scripts.smoke_kis

paper:
	PYTHONPATH=src python -m scripts.run_paper --top-n $${TOP_N:-1} --cycles $${CYCLES:-1}

demo:
	PYTHONPATH=src python -m scripts.demo_buy_then_sell

backtest:
	PYTHONPATH=src python -m scripts.run_backtest --universe=$${UNIVERSE:-kospi200} --from=$${FROM:-2024-01-01} --to=$${TO:-2025-12-31}

live:
	@[ "$$KIS_LIVE" = "1" ] || (echo "set KIS_LIVE=1 explicitly" && exit 1)
	PYTHONPATH=src python -m scripts.run_live

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache **/__pycache__ build dist *.egg-info
