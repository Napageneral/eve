## Ralph docs: Go single-binary Eve rewrite

This folder is the **execution-grade plan** for migrating Eve from the current Python/Celery/TS-heavy stack to a **single Go binary** that:
- runs ETL (Messages + AddressBook → `eve.db`)
- runs high-concurrency compute (analysis + embeddings) using **Gemini** over **HTTP/2 REST**
- exposes an agent-friendly CLI with **stable JSON** outputs

### How to use these docs

- Start here: `docs/ralph/EXECUTION_CHECKLIST.md` (ordered, step-by-step)
- Full design + rationale: `docs/ralph/GO_SINGLE_BINARY_PLAN.md`

### Decisions already locked (from Tyler)

- **Cloud allowed**: message content can be sent to Gemini for analysis and embeddings.
- **Models**:
  - analysis: **Gemini Flash 3.0**
  - embeddings: **latest Gemini embedding model**
  - both configurable by flags/env, but defaults must match the above.
- **No local models / no sidecars**: everything ships as a single `eve` binary.
- **No Python** as a distribution/runtime dependency: ETL and compute must be ported to Go.
- **Vector search** is a stretch goal; prefer SQLite-native if feasible.
- **Raw SQL only**: do not introduce ORMs for queries.

### Output contract (important for agents + privacy)

We default to **JSON-only stdout** because:
- agents can parse it reliably (no brittle scraping)
- we can guarantee stable schemas and version them
- it avoids accidental sensitive output in logs/transcripts

We **do not print message text by default**. Message content is still stored in `eve.db` and queryable, but printing it to terminals/logs is:
- easy to leak (shell history, CI logs, agent harness logs, screenshots)
- often unnecessary for “status / counts / perf / backlog” tasks

CLI design must support **explicit opt-in** for content output:
- `--include-text` or `--format text` (explicit)
- ideally also `--redact` and `--max-chars` for safety

### Repo pointers

- Current CLI entrypoint (legacy): `bin/eve` (Python)
- TS context/encoding assets we will re-use as *data* initially:
  - `ts/eve/prompts/`
  - `ts/eve/context-packs/`
  - `ts/eve/encoding/` (logic to be ported to Go for single-binary)
- DB docs: `docs/skills/eve-db.md`

