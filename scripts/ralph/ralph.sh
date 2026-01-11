#!/bin/bash
set -e

# Ralph Wiggum loop for Eve CLI implementation
# Usage: ./ralph.sh [max_iterations]

MAX_ITERATIONS=${1:-20}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "🚀 Starting Ralph for Eve CLI Consolidation"
echo "   Project: $PROJECT_DIR"
echo "   Max iterations: $MAX_ITERATIONS"
echo ""

cd "$PROJECT_DIR"

# Ensure we're on the right branch
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
echo "📌 Current branch: $BRANCH"

for i in $(seq 1 $MAX_ITERATIONS); do
  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "═══ Iteration $i of $MAX_ITERATIONS ═══"
  echo "════════════════════════════════════════════════════════════"
  echo ""
  
  # Run the agent with the prompt
  # Replace 'amp' with your preferred agent (claude, cursor, etc.)
  OUTPUT=$(cat "$SCRIPT_DIR/prompt.md" \
    | npx --yes @anthropic-ai/claude-code --dangerously-skip-permissions 2>&1 \
    | tee /dev/stderr) || true
  
  # Check for completion signal
  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo ""
    echo "✅ All stories complete!"
    echo ""
    exit 0
  fi
  
  # Brief pause between iterations
  sleep 2
done

echo ""
echo "⚠️  Max iterations ($MAX_ITERATIONS) reached"
echo "    Run again to continue: ./ralph.sh"
echo ""
exit 1
