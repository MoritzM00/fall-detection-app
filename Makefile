.PHONY: setup dev check browser-test

setup:
	UV_CACHE_DIR=.uv-cache uv sync
	pnpm --dir apps/web install --frozen-lockfile

dev:
	UV_CACHE_DIR=.uv-cache uv run python scripts/dev.py

check:
	UV_CACHE_DIR=.uv-cache uv run ruff check .
	UV_CACHE_DIR=.uv-cache uv run ruff format --check .
	UV_CACHE_DIR=.uv-cache uv run ty check
	UV_CACHE_DIR=.uv-cache uv run pytest
	pnpm --dir apps/web build
	pnpm --dir apps/web typecheck:e2e
	pnpm --dir apps/web test


browser-test:
	.venv/bin/python scripts/run_browser_tests.py
