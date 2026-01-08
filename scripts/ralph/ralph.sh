#!/bin/bash
# Ralph Wiggum Loop for Eve
# Run with: ./scripts/ralph/ralph.sh [max_iterations]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MAX_ITERATIONS=${1:-50}

# Allow overriding which agent CLI is used.
# Default matches the Nexus example (Claude Code).
AGENT_CMD=${AGENT_CMD:-"claude --dangerously-skip-permissions"}

cd "$PROJECT_ROOT"

echo "🚀 Starting Ralph for Eve"
echo "📁 Working in: $PROJECT_ROOT"
echo "🔄 Max iterations: $MAX_ITERATIONS"
echo "🤖 Agent cmd: $AGENT_CMD"
echo ""

echo "📋 Pre-flight checks..."
command -v python3 >/dev/null 2>&1 || (echo "❌ python3 not found" && exit 1)
command -v npx >/dev/null 2>&1 || (echo "❌ npx not found" && exit 1)
command -v claude >/dev/null 2>&1 || echo "⚠️ claude CLI not found (AGENT_CMD may point elsewhere)"

echo "✅ Pre-flight OK"
echo ""

echo "═══════════════════════════════════════"
echo "═══ Starting Ralph Loop              ═══"
echo "═══════════════════════════════════════"

for i in $(seq 1 $MAX_ITERATIONS); do
  echo ""
  echo "═══════════════════════════════════════"
  echo "═══ Iteration $i of $MAX_ITERATIONS ═══"
  echo "═══════════════════════════════════════"

  # Run the agent with the prompt.
  # NOTE: We use eval so AGENT_CMD can include flags.
  OUTPUT=$(cat "$SCRIPT_DIR/prompt.md" \
    | eval "$AGENT_CMD" 2>&1 \
    | tee /dev/stderr) || true

  echo ""
  echo "📊 Verification (scripts/ralph/verify.sh)"
  (./scripts/ralph/verify.sh || true) 2>&1 | tee /dev/stderr >/dev/null || true

  # Completion signal (agent prints this when all stories in prd.json pass)
  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo ""
    echo "✅ Ralph completed all stories!"
    echo ""
    echo "📊 Final verification:"
    ./scripts/ralph/verify.sh
    exit 0
  fi

  # Brief pause between iterations (avoid hammering the agent CLI)
  sleep 5
done

echo ""
echo "⚠️ Max iterations ($MAX_ITERATIONS) reached without completion"
echo ""
echo "📊 Current verification status:"
./scripts/ralph/verify.sh || true
exit 1

