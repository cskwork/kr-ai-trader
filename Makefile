.PHONY: help install dev fmt lint type test smoke-llm smoke-kis paper backtest live clean

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
	@echo "  paper       run paper-trading loop"
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
	python scripts/smoke_llm.py

smoke-kis:
	python scripts/smoke_kis.py

paper:
	python scripts/run_paper.py

backtest:
	python scripts/run_backtest.py --universe=$${UNIVERSE:-kospi200} --from=$${FROM:-2024-01-01} --to=$${TO:-2025-12-31}

live:
	@[ "$$KIS_LIVE" = "1" ] || (echo "set KIS_LIVE=1 explicitly" && exit 1)
	python scripts/run_live.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache **/__pycache__ build dist *.egg-info
