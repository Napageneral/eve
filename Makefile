.DEFAULT_GOAL := help

.PHONY: help tree py-check py-install py-test ts-check ts-install test test-ts test-py
.PHONY: ralph-verify

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
	@.venv/bin/python -m unittest -v python/tests/test_real_full_pipeline.py

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

# --- Ralph (agent loop verification harness) ---
#
# This target is intentionally:
# - fast
# - non-interactive
# - safe (no real-data ETL, no cloud calls)
#
# It provides the "Feedback" mechanism for Ralph loops.
ralph-verify:
	@echo "Ralph verify: starting"
	@echo ""
	@echo "1) Python import graph (fast)"
	@$(MAKE) py-check
	@echo ""
	@echo "2) TypeScript typecheck (fast)"
	@$(MAKE) ts-check
	@echo ""
	@echo "3) Go tests (only if go.mod exists)"
	@if [ -f go.mod ]; then \
		echo "Running gofmt + go test ./..."; \
		command -v go >/dev/null 2>&1 || (echo "go not found" && exit 1); \
		gofmt -w $$(find . -name '*.go' -not -path './ts/node_modules/*' 2>/dev/null || true) >/dev/null 2>&1 || true; \
		go test ./...; \
	else \
		echo "go.mod not present yet; skipping Go tests"; \
	fi
	@echo ""
	@echo "Ralph verify: OK"
