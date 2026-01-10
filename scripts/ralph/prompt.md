# Ralph Agent Instructions for Eve (in-place in Nexus)

## Context

You are evolving Eve **in-place** at:

- `/Users/tyler/nexus/home/projects/eve`

**Goal**: Make Eve maximally portable as:
- a **single Go CLI** exposing core primitives (raw SQL, encoding, context compilation) with stable JSON stdout
- plus a **skill-shippable resources folder** containing editable prompt + pack files (so users/agents can hack prompts locally)

TypeScript is **reference material only** during the port. The end state is Go-first runtime.

## Absolute Rules

- **ONE story per iteration**
- **Raw SQL only** (no ORMs)
- **JSON stdout** — never print message text unless explicitly requested by a flag
- **Synthetic test fixtures** — use temp SQLite DBs with known rows; do NOT use real user data in tests
- **No real Gemini calls in unit tests** — use fakes / httptest

## Your Task (repeat every iteration)

1. Read `scripts/ralph/prd.json`
2. Read `scripts/ralph/progress.txt` (check **Codebase Patterns** first)
3. Pick the highest priority story where `passes: false`
4. Implement that ONE story only
5. Run verification:

```bash
./scripts/ralph/verify.sh
```

6. If verification passes:
   - Commit: `feat(eve): [ID] - [Title]`
   - Update `scripts/ralph/prd.json`: set `passes: true`
   - Append learnings to `scripts/ralph/progress.txt`

7. If verification fails:
   - Append failure details to `scripts/ralph/progress.txt`
   - Do NOT mark the story as passing

## Stop Condition

If **ALL** stories have `passes: true`, reply:

<promise>COMPLETE</promise>

