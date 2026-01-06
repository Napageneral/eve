# Eve (WIP)

Eve is a CLI-first personal communications database: ingest iMessage + contacts into a local SQLite database (`central.db`), then optionally run high-throughput conversation analysis + embeddings + vector search.

This repo is a **fresh extraction** from `ChatStats/` to avoid disturbing the existing Electron app.

## Repo layout

- `PLAN.md` — architecture + product plan
- `python/backend/` — ported Python backend (ETL, Celery tasks, FAISS, DB)
- `ts/eve/` — ported TypeScript context engine + encoding + retrieval adapters

## Status

Early port: code copied over; build and packaging are not yet wired for standalone use.

## Philosophy

- CLI-first surface with stable `--json` outputs for agents
- Keep `central.db` as the canonical local dataset
- Keep Celery+Redis for throughput (enhanced mode)
- Drop Electron/Next UI

