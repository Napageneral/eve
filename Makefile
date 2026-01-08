.DEFAULT_GOAL := help

.PHONY: help tree ts-check ts-install test test-ts go-test go-build
.PHONY: ralph-verify

help:
	@echo "Eve - Single Go binary for iMessage analysis"
	@echo ""
	@echo "Targets:"
	@echo "  make tree        Show repo tree (high-level)"
	@echo "  make go-build    Build eve binary"
	@echo "  make go-test     Run Go tests"
	@echo "  make ts-check    Sanity-check TypeScript workspace (tsc)"
	@echo "  make ts-install  Install Bun/TS deps (ts/)"
	@echo "  make test        Run all Eve tests (TS + Go)"

# --- utils ---

tree:
	@echo "./"
	@find . -maxdepth 2 -type d \( -name .git -o -name node_modules -o -name __pycache__ \) -prune -o -type d -print | sed 's|^\./||' | sort

# --- Go ---

go-build:
	@command -v go >/dev/null 2>&1 || (echo "go is required to build" && exit 1)
	@go build -o bin/eve ./cmd/eve

go-test:
	@command -v go >/dev/null 2>&1 || (echo "go is required to run tests" && exit 1)
	@go test ./...

# --- TypeScript ---

ts-check:
	@cd ts && (command -v bun >/dev/null 2>&1 && bunx tsc -p tsconfig.json || npx -y tsc -p tsconfig.json)

ts-install:
	@cd ts && bun install

# --- Tests ---

test: test-ts go-test

test-ts:
	@command -v bun >/dev/null 2>&1 || (echo "bun is required to run tests" && exit 1)
	@echo "Running unit tests..."
	@bun run --bun test/unit/broker-communication.test.ts
	@bun run --bun test/unit/encoding-endpoint.test.ts
	@bun run --bun test/unit/preset-ea-spawning.test.ts

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
	@echo "1) Go tests"
	@command -v go >/dev/null 2>&1 || (echo "go not found" && exit 1)
	@echo "Running gofmt + go test ./..."
	@gofmt -w $$(find . -name '*.go' -not -path './ts/node_modules/*' 2>/dev/null || true) >/dev/null 2>&1 || true
	@go test ./...
	@echo ""
	@echo "Ralph verify: OK"
