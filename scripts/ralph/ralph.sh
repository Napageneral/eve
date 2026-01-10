#!/bin/bash
# Ralph Wiggum Loop for Eve (in-place Nexus repo)
# Run with: ./scripts/ralph/ralph.sh [max_iterations]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MAX_ITERATIONS=${1:-50}

# Allow overriding which agent CLI is used.
AGENT_CMD=${AGENT_CMD:-"claude --dangerously-skip-permissions"}

cd "$PROJECT_ROOT"

echo "Starting Ralph for Eve"
echo "Working in: $PROJECT_ROOT"
echo "Max iterations: $MAX_ITERATIONS"
echo "Agent cmd: $AGENT_CMD"
echo ""

for i in $(seq 1 $MAX_ITERATIONS); do
  echo ""
  echo "======================================="
  echo "Iteration $i of $MAX_ITERATIONS"
  echo "======================================="

  OUTPUT=$(cat "$SCRIPT_DIR/prompt.md" \
    | eval "$AGENT_CMD" 2>&1 \
    | tee /dev/stderr) || true

  echo ""
  echo "Verification (scripts/ralph/verify.sh)"
  (./scripts/ralph/verify.sh || true) 2>&1 | tee /dev/stderr >/dev/null || true

  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo ""
    echo "Ralph completed all stories!"
    echo ""
    echo "Final verification:"
    ./scripts/ralph/verify.sh
    exit 0
  fi

  sleep 5
done

echo ""
echo "Max iterations ($MAX_ITERATIONS) reached without completion"
echo ""
echo "Current verification status:"
./scripts/ralph/verify.sh || true
exit 1

