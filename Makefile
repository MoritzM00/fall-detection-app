.PHONY: setup dev dev-gpu check browser-test demo gpu-warm gpu-stop

# Committed non-secret GPU settings, then the Git-ignored token in .env.
GPU_ENV = set -a; . deploy/modal.env; [ ! -f .env ] || . ./.env; set +a;

setup:
	UV_CACHE_DIR=.uv-cache uv sync
	pnpm --dir apps/web install --frozen-lockfile

dev:
	UV_CACHE_DIR=.uv-cache uv run python scripts/dev.py

dev-gpu:
	@[ -f .env ] || { echo "Missing .env: copy .env.example and add your Modal proxy token" >&2; exit 1; }
	$(GPU_ENV) UV_CACHE_DIR=.uv-cache uv run python scripts/dev.py

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

demo:
	.venv/bin/python -m scripts.demo

gpu-warm:
	$(GPU_ENV) UV_CACHE_DIR=.uv-cache uv run python scripts/warm_gpu.py $(if $(MODEL),--model $(MODEL))

gpu-stop:
	$(GPU_ENV) UV_CACHE_DIR=.uv-cache uv run python scripts/stop_gpu.py
