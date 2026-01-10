#!/bin/bash
# Ralph verification harness for Eve (Go-first port)
#
# This script is intentionally:
# - fast
# - deterministic
# - non-interactive
# - safe (no real Gemini calls; no real user data)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "Ralph verify: starting"

echo ""
echo "1) Go tests"
command -v go >/dev/null 2>&1 || (echo "go not found" && exit 1)

if [ -f "go.mod" ]; then
  # Best-effort formatting
  find . -name '*.go' -not -path './ts/node_modules/*' -print0 2>/dev/null | xargs -0 gofmt -w >/dev/null 2>&1 || true
  go test ./...
else
  echo "go.mod not found yet (expected until EVGO-000 lands)"
  exit 1
fi

echo ""
echo "2) TypeScript typecheck (optional; enable with RALPH_INCLUDE_TS=1)"
if [ "${RALPH_INCLUDE_TS:-0}" = "1" ]; then
  make ts-check
else
  echo "Skipping (set RALPH_INCLUDE_TS=1 to enable)"
fi

echo ""
echo "Ralph verify: OK"

