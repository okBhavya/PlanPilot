.PHONY: install dev test lint typecheck eval up down clean

install:
	uv pip install -e .

dev:
	uv pip install -e ".[dev]"

test:
	python -m pytest tests/ -v --tb=short -m "not live"

test-all:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff check --fix src/ tests/
	ruff format src/ tests/

typecheck:
	mypy --strict src/planpilot/

check: lint typecheck test

eval:
	python -m planpilot.cli eval --output results/

up:
	docker compose up -d --build

down:
	docker compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info
