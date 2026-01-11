# Ralph Agent Instructions for Eve CLI

## Context

You are implementing the Eve CLI consolidation - making Eve a single unified tool for all iMessage operations.

## Your Task

1. Read `scripts/ralph/prd.json` to see all user stories
2. Read `scripts/ralph/progress.txt` for learnings from previous iterations (check **Codebase Patterns** first)
3. Read `PLAN.md` for detailed implementation guidance
4. Pick the highest priority story where `passes: false`
5. Implement that **ONE** story completely
6. Run verification:
   - `go build ./...`
   - `go test ./...`
7. If tests pass:
   - `git add -A`
   - `git commit -m "feat: [ID] - [Title]"`
8. Update `scripts/ralph/prd.json`: set `passes: true` for completed story
9. Append learnings to `scripts/ralph/progress.txt`

## Codebase Structure

```
eve/
├── cmd/eve/main.go          # CLI entry point (Cobra commands)
├── internal/
│   ├── config/              # Configuration loading
│   ├── db/                  # Database readers/writers
│   ├── encoding/            # Message encoding for LLM
│   ├── engine/              # Compute engine (analysis, embeddings)
│   ├── etl/                 # ETL from chat.db
│   ├── gemini/              # Gemini API client
│   ├── queue/               # Job queue
│   └── resources/           # Embedded prompts/packs
├── PLAN.md                  # Detailed implementation plan
└── scripts/ralph/           # Ralph automation
```

## Key Patterns

- All commands output JSON via `printJSON()`
- Errors use `printErrorJSON()`
- Use raw SQL queries (no ORM)
- Follow existing patterns in `cmd/eve/main.go`
- Add tests in `*_test.go` files

## Progress Format

APPEND to `scripts/ralph/progress.txt`:

```
---
## [Date] - [Story ID]: [Title]
- What was implemented
- Files changed
- **Learnings:**
  - Patterns discovered
  - Gotchas encountered
```

## Codebase Patterns (Add to TOP of progress.txt)

When you discover reusable patterns, add them to the **Codebase Patterns** section at the top of `progress.txt`.

## Stop Condition

If ALL stories in prd.json have `passes: true`, reply:
```
<promise>COMPLETE</promise>
```

Otherwise, end your turn normally after completing one story.

## Critical Rules

1. **ONE story per iteration** - Don't try to do multiple
2. **Search before implementing** - Code may already exist
3. **Tests must pass** - Don't commit if `go test` fails
4. **Update progress.txt** - Document learnings for future iterations
5. **No placeholders** - Implement fully, not stubs

## Existing Commands Reference

Look at how these existing commands are implemented in `cmd/eve/main.go`:
- `initCmd` - Database initialization
- `syncCmd` - ETL sync with flags
- `dbQueryCmd` - SQL query execution
- `whoamiCmd` - User info retrieval
- `computeCmd` - Compute engine operations
