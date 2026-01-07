.DEFAULT_GOAL := help

.PHONY: help tree py-check py-install py-test ts-check ts-install test test-ts test-py

help:
	@echo "Eve (WIP)"
	@echo ""
	@echo "Targets:"
	@echo "  make tree        Show repo tree (high-level)"
	@echo "  make py-check    Sanity-check Python backend import graph"
	@echo "  make ts-check    Sanity-check TypeScript workspace (tsc)"
	@echo "  make ts-install  Install Bun/TS deps (ts/)"
	@echo "  make py-install  Create venv + install minimal Python deps for CLI/ETL"
	@echo "  make test        Run all Eve tests (TS + Python)"

# --- utils ---

tree:
	@echo "./"
	@find . -maxdepth 2 -type d \( -name .git -o -name node_modules -o -name __pycache__ \) -prune -o -type d -print | sed 's|^\./||' | sort

# --- Python ---

py-check:
	@python3 -c "import sys; sys.path.insert(0, 'python'); import backend; print('OK: imported backend as namespace package')"

py-install:
	@python3 -m venv .venv
	@.venv/bin/python -m pip install --upgrade pip
	@.venv/bin/python -m pip install -r python/requirements-cli.txt

py-test: py-install
	@.venv/bin/python -m unittest -v python/tests/test_cli_etl_live_sync.py

# --- TypeScript ---

ts-check:
	@cd ts && (command -v bun >/dev/null 2>&1 && bunx tsc -p tsconfig.json || npx -y tsc -p tsconfig.json)

ts-install:
	@cd ts && bun install

# --- Tests ---

test: test-ts test-py

test-ts:
	@command -v bun >/dev/null 2>&1 || (echo "bun is required to run tests" && exit 1)
	@echo "Running unit tests..."
	@bun run --bun test/unit/broker-communication.test.ts
	@bun run --bun test/unit/encoding-endpoint.test.ts
	@bun run --bun test/unit/preset-ea-spawning.test.ts

test-py: py-test

