# Eve — CLI-first personal communications database

**Goal:** make your personal communications (iMessage + contacts + derived analysis) available to agents as **local files + local query tools**, without requiring an Electron UI.

**Non-goal:** modify or break the existing `ChatStats/` app. This repo is a clean extraction/port.

---

## 1) What exists today (source of truth)

### 1.1 `imsg` (thin Messages.app CLI)

`imsg` is a **direct interface** to Apple’s Messages DB (`~/Library/Messages/chat.db`).

- **Primary capabilities**:
  - List chats, get history, `watch` new messages
  - Optionally show attachment metadata
  - Send messages/attachments via **AppleScript** (Automation permission)
- **State model**: essentially stateless; `watch` is streaming while the process is alive.
- **Storage**: **no secondary database**; reads `chat.db` in read-only mode.
- **Normalization**: minimal; some phone normalization to E.164.
- **Search**: none (you search by piping JSON elsewhere).
- **Permissions**:
  - Needs **Full Disk Access** to read `chat.db`
  - Sending also needs **Automation** permission for Messages.app

### 1.2 ChatStats/Eve (pipeline + secondary DB + analysis)

ChatStats is a **data platform** with an optional UI.

- **ETL (Python)**: imports from `chat.db` + AddressBook, resolves contacts, groups messages into conversations, and writes normalized tables into `central.db`.
  - Live sync uses ROWID watermarks + WAL polling and has robust caching.
- **Derived analysis (Python + Celery)**: conversation analysis passes write normalized facets (summary/topics/entities/emotions/humor/etc.).
- **Embeddings + vector search (Python)**:
  - Embedding generation currently uses **Gemini API** (requires key; sends text off-machine)
  - Vectors stored in `embeddings` table as float32 little-endian blob (`vector_blob`)
  - FAISS index built locally (HNSW/FlatIP)
- **Context assembly (TypeScript “Eve”)**:
  - Pack/prompt registry, retrieval adapters, budget fitting
  - Reads `central.db` in readonly mode for fast context assembly

---

## 2) Key differences: “Eve CLI” vs `imsg`

Think of `imsg` as **a sensor** and Eve as **a warehouse + query engine**.

### 2.1 Data model
- **`imsg`**: raw `chat.db` schema, minimal joins, no durable derived tables.
- **Eve**: curated schema in `central.db`:
  - stable IDs, resolved contacts, conversation boundaries, caches, indexes
  - derived analysis tables
  - embeddings table + FAISS index files

### 2.2 Reliability & scale
- **`imsg`**: great for “give me the last N messages” and “stream new messages”; not designed for million-task backfills.
- **Eve**: designed for:
  - 300k+ message imports
  - durable backfills
  - high-throughput analysis/embedding pipelines

### 2.3 Search & retrieval
- **`imsg`**: no built-in search.
- **Eve**: two search modes:
  - **Vector search** (current system): embeddings → FAISS ANN → hydrate rows
  - **(Optional future) BM25/FTS5**: local lexical search, no external keys

### 2.4 Permission footprint
- Both require **Full Disk Access** for initial extraction / direct reads from Apple DB.
- Only `imsg send` requires **Automation** permission.
- Once `central.db` exists, most Eve read/query operations can run without continued access to Apple DB.

### 2.5 Streaming new messages (“watch”) — stateless vs stateful

There are two valid “watch” models:

- **Stateless emitter (imsg-style)**:
  - Process watches `chat.db`/`-wal`/`-shm` changes and emits messages with `ROWID > since_rowid`.
  - State lives outside the process (caller passes `--since-rowid`, or the caller persists it).
  - Best when you only need a live stream and don’t need a secondary DB.

- **Stateful syncer (ChatStats-style)**:
  - Process watches for changes and **syncs** new rows into `central.db`, updating watermarks and triggering downstream work.
  - This is not “stateless” because correctness requires durable watermarks + dedup + id mapping.
  - Best when `central.db` is the primary interface (Eve’s chosen model).

**Decision for Eve CLI:** keep the ChatStats-style sync pipeline as the canonical path, and optionally provide an imsg-like `eve watch --json` output that *reads from `central.db`* (plus a “raw watch” mode later if needed).

---

## 3) Product stance

**Name:** Eve

**Primary distribution:** Homebrew formula + “run from source” option.

**User experience:** CLI-first with progressive disclosure.

- `eve onboard` — wizard setup
- `eve status` / `eve doctor` — health checks and recovery
- `eve query ...` / `eve search ...` — stable JSON output for agents
- Optional:
  - `eve watch` — incremental sync while running
  - `eve mcp` — thin wrapper if a harness benefits

---

## 4) Architecture (keep Celery/Redis, drop UI)

### 4.1 Two-plane model

**Data plane (always available):**
- `central.db` (SQLite)
- Eve TS encoding + context assembly (library + CLI)
- Read-only queries/search against `central.db`

**Compute plane (optional but recommended for “enhanced” mode):**
- Redis broker
- Celery workers (analysis, embeddings, FAISS rebuild)
- Optional beat schedule (coalesced rebuilds, sealing checks)

**Principle:** Eve CLI must still work if the compute plane is down.

### 4.2 “Enhanced mode” gating

If user config includes `GEMINI_API_KEY` (or equivalent):
- enable conversation analysis passes
- enable embeddings generation
- enable vector search endpoint/command

If no key:
- still allow raw data + encoding + context packs
- optionally offer FTS/BM25 later

---

## 5) Install & setup flow (Homebrew + wizard)

### 5.1 `eve onboard`

Wizard responsibilities:
1. Verify macOS permissions (Full Disk Access guidance)
2. Create app data dir (e.g. `~/Library/Application Support/Eve/`)
3. Run initial ETL import into `central.db`
4. Ask for Gemini key (optional) and persist config securely
5. Ensure Redis is available (brew service or bundled)
6. Start Celery workers + beat (launchd preferred)
7. Queue backfills (analysis + embeddings + FAISS build)

### 5.2 Ongoing behavior
- `eve status` shows:
  - DB present + last sync watermark
  - Redis reachable
  - Celery workers alive
  - backlog metrics (queued/running)
  - FAISS dirty flag / last built timestamp
- Any `eve ...` command may optionally “auto-heal” by starting services if configured.

---

## 6) Config + secrets model

Design goal: similar ergonomics to Clawdbot’s centralized config.

- Single config location (suggested): `~/.eve/config.json`
- Secrets:
  - either macOS Keychain
  - or file-based with clear warning + permissions

---

## 7) Repo plan (this project)

### 7.1 Minimal directory layout (proposed)

```
eve/
  PLAN.md
  README.md
  brew/
    eve.rb
  src/
    cli/
    engine/        # TS context engine + encoding
    adapters/
  python/
    etl/
    workers/
    services/
  scripts/
```

### 7.2 Porting strategy (keep ChatStats intact)

- **Phase A (copy/preserve)**
  - Port ETL (`app/backend/etl/`) into `python/etl/`
  - Port embeddings pipeline + FAISS builder into `python/workers/`
  - Port Eve context engine + encoding into `src/engine/`

- **Phase B (shrink/rename/decouple from UI)**
  - Remove Electron assumptions (logging, IPC)
  - Drop Next.js frontend and chatbot UI domain
  - Keep only schemas/tables needed for personal communications + analysis

- **Phase C (CLI UX polish)**
  - `onboard`, `status`, `doctor`, `sync`, `query`, `search`
  - stable `--json` outputs + docs for agents

---

## 8) Open questions

1. Do we keep the FastAPI HTTP server at all, or run everything via CLI + direct DB access?
   - Hypothesis: keep HTTP optional (debug only). CLI is primary.

2. Redis distribution:
   - brew-managed `redis` (simplest) vs bundled (harder but “single install”).

3. How to represent/ship launchd services:
   - `eve services install` generates plists per-user.

4. Sending (Messages.app):
   - Option A: shell out to `imsg send` (dependency, simplest)
   - Option B: port the AppleScript/ScriptingBridge approach into Eve (more control)
