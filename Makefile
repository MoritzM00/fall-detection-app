.PHONY: setup dev check browser-test

setup:
	UV_CACHE_DIR=.uv-cache uv sync
	npm install --prefix apps/web

dev:
	UV_CACHE_DIR=.uv-cache uv run python scripts/dev.py

check:
	UV_CACHE_DIR=.uv-cache uv run ruff check .
	UV_CACHE_DIR=.uv-cache uv run ruff format --check .
	UV_CACHE_DIR=.uv-cache uv run ty check
	UV_CACHE_DIR=.uv-cache uv run pytest
	npm run build --prefix apps/web
	npm run typecheck:e2e --prefix apps/web


browser-test:
	.venv/bin/python scripts/run_browser_tests.py
