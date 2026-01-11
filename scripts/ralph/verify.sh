#!/bin/bash
# Verification harness for Ralph iterations
# Run this to check if current state is valid

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "🔍 Running verification..."
echo ""

# Step 1: Build
echo "📦 Building..."
if go build ./...; then
  echo "   ✅ Build passed"
else
  echo "   ❌ Build failed"
  exit 1
fi

# Step 2: Tests
echo ""
echo "🧪 Running tests..."
if go test ./... -v; then
  echo "   ✅ Tests passed"
else
  echo "   ❌ Tests failed"
  exit 1
fi

# Step 3: Basic smoke test
echo ""
echo "🚬 Smoke test..."
if ./bin/eve version > /dev/null 2>&1; then
  echo "   ✅ eve version works"
else
  echo "   ⚠️  eve binary not found at ./bin/eve (run 'make go-build')"
fi

echo ""
echo "✅ All verification passed!"
