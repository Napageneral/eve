#!/bin/bash
# Ralph verification harness for Eve
#
# This script is intentionally:
# - fast
# - deterministic
# - non-interactive
# - safe (no real Gemini calls; no real-data ETL)
#
# It is the "Feedback" mechanism for Ralph loops.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "Ralph verify: starting"

echo ""
echo "1) Python import graph (fast)"
python3 -c "import sys; sys.path.insert(0, 'python'); import backend; print('OK: imported backend as namespace package')"

echo ""
echo "2) Go tests (only if go.mod exists)"
if [ -f go.mod ]; then
  command -v go >/dev/null 2>&1 || (echo "go not found" && exit 1)
  # Best-effort formatting; ok if no .go files yet
  find . -name '*.go' -not -path './ts/node_modules/*' -print0 2>/dev/null | xargs -0 gofmt -w >/dev/null 2>&1 || true
  go test ./...
else
  echo "go.mod not present yet; skipping Go tests"
fi

echo ""
echo "3) TypeScript typecheck (optional; enable with RALPH_INCLUDE_TS=1)"
if [ "${RALPH_INCLUDE_TS:-0}" = "1" ]; then
  # NOTE: This currently requires Bun/TS toolchain to be healthy.
  # Keep it opt-in so the Go rewrite can proceed even if TS is mid-refactor.
  make ts-check
else
  echo "Skipping (set RALPH_INCLUDE_TS=1 to enable)"
fi

echo ""
echo "Ralph verify: OK"

