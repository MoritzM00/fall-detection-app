.PHONY: setup dev check

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

