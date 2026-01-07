# Eve (WIP)

Eve is a CLI-first personal communications database: ingest iMessage + contacts into a local SQLite database (`eve.db`), then optionally run high-throughput conversation analysis + embeddings + vector search.

This repo is a **fresh extraction** from `ChatStats/` to avoid disturbing the existing Electron app.

## Repo layout

- `PLAN.md` — architecture + product plan
- `docs/skills/eve-db.md` — agent skill: raw SQL access to `eve.db` (serverless)
- `python/backend/` — ported Python backend (ETL, Celery tasks, FAISS, DB)
- `ts/eve/` — ported TypeScript context engine + encoding + retrieval adapters

## Status

Early port: code copied over; build and packaging are not yet wired for standalone use.

## Philosophy

- CLI-first surface with stable JSON outputs for agents
- Keep `eve.db` as the canonical local dataset
- Keep Celery+Redis for throughput (enhanced mode)
- Drop Electron/Next UI

## Usage (agent-friendly)

### Serverless DB access (recommended)

Run raw SQL against `eve.db` and get stable JSON:

```bash
eve db query --sql "SELECT COUNT(*) AS c FROM messages" --limit 1 --pretty
```

### Compute plane orchestration (optional)

Start/stop Redis + Context Engine + Celery (for analysis/embeddings):

```bash
eve compute up --pretty
eve compute status --pretty
eve compute down --pretty
```

