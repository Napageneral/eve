# Ralph Agent Instructions for Eve

## Context

You are working in the **Eve** repository at `/Users/tyler/Desktop/projects/eve/`.

We are executing a major pivot:
- Move Eve away from Python/Celery/gevent into a **single Go binary**.
- **Cloud is allowed**: it is OK to send message content to Gemini for **analysis + embeddings**.
- **No local models / no sidecars** for now.
- **Port ETL away from Python** (distribution simplicity).

**Primary design docs (read first):**
- `docs/ralph/GO_SINGLE_BINARY_PLAN.md`
- `docs/ralph/EXECUTION_CHECKLIST.md`

**Ralph task list + memory:**
- `scripts/ralph/prd.json`
- `scripts/ralph/progress.txt`

## Absolute rules

- **ONE story per iteration** (pick the highest priority story where `passes: false`)
- **Raw SQL only** (no ORM query layers)
- **Default CLI stdout is JSON** and should not print message text unless explicitly requested
- **Do not call real Gemini APIs in unit tests**:
  - use `httptest`/fake servers for Go tests
  - tests must be deterministic and cheap

## Your Task (repeat every iteration)

1. Read `scripts/ralph/prd.json`
2. Read `scripts/ralph/progress.txt` (check **Codebase Patterns** first)
3. Check you’re on the correct branch (see `branchName` in prd.json)
4. Pick the highest priority story where `passes: false`
5. Implement that ONE story only
6. Run verification harness:

```bash
./scripts/ralph/verify.sh
```

7. If verification passes:
   - Commit: `feat(eve-go): [ID] - [Title]`
   - Update `scripts/ralph/prd.json`: set `passes: true` for that story
   - Append learnings to `scripts/ralph/progress.txt`
8. If verification fails:
   - Append the failure details to `scripts/ralph/progress.txt` under the story ID
   - Do NOT mark the story as passing

## Stop condition

If **ALL** stories in `scripts/ralph/prd.json` have `passes: true`, reply:

<promise>COMPLETE</promise>

Otherwise end normally (the loop will restart).

