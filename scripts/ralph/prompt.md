# Ralph Agent Instructions for Eve

## Context

You are completing the **Go ETL migration** for Eve at `/Users/tyler/Desktop/projects/eve/`.

**Goal**: Make `eve sync` actually copy data from chat.db to eve.db so the Go binary is fully self-sufficient. After this phase, all Python code will be deleted.

**Key existing code:**
- `internal/etl/chatdb.go` — chat.db reader with performance pragmas (already works)
- `internal/etl/watermark.go` — watermark tracking (already works)
- `internal/migrate/sql/warehouse/002_core_schema.sql` — target schema (already exists)

## Absolute Rules

- **ONE story per iteration**
- **Raw SQL only** (no ORMs)
- **JSON stdout** — never print message text unless explicitly requested
- **Idempotent writes** — use ON CONFLICT clauses
- **Synthetic test fixtures** — create temp chat.db with known data; do NOT use real user data in tests

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
   - Commit: `feat(eve-go): [ID] - [Title]`
   - Update `scripts/ralph/prd.json`: set `passes: true`
   - Append learnings to `scripts/ralph/progress.txt`

7. If verification fails:
   - Append failure details to `scripts/ralph/progress.txt`
   - Do NOT mark the story as passing

## Stop Condition

If **ALL** stories have `passes: true`, reply:

<promise>COMPLETE</promise>

Otherwise end normally (the loop will restart).
