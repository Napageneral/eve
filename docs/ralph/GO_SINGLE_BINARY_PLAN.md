## Go single-binary Eve rewrite (Gemini-only)

### Goal

Build a **single Go binary** (`eve`) that is:
- **Easy**: install → run → works
- **Portable**: no Python runtime, no Redis/Celery required
- **Reliable**: crash-safe, resumable, idempotent
- **Fast**: high-concurrency analysis + embeddings (“fly” through tens of thousands of convos)

Scope decisions:
- **Gemini-only for now** (cloud allowed for both analysis + embeddings).
- **No local embeddings/models and no sidecar binaries**.
- **ETL is ported to Go** (no Python distribution).
- **Vector search** is a stretch goal; prefer SQLite-native if feasible.
- **Raw SQL only** (no ORM query layers).

---

## Why JSON stdout + “no message text by default”

### Why JSON is the default
Agents need output that is:
- **machine parseable**
- **stable across versions**
- **streamable** (JSONL) when needed

Human-friendly text output can be added as `--format table|text`, but JSON must remain the default contract.

### Why message text is opt-in
Message text is sensitive and easy to leak via:
- terminal scrollback/shell history
- logs captured by agent harnesses
- CI/build logs
- screenshots/screen-share

Default commands (status/doctor/sync/compute) should output **counts, IDs, and timings**. Any command that prints message bodies must require explicit flags (e.g., `--include-text`).

This is a UX *and* safety feature: it makes the default “safe” while keeping full access available on demand.

---

## What is `text-embeddings-inference` (TEI), and why not “just write our own”?

**TEI** (Hugging Face text-embeddings-inference) is a production embeddings server written in Rust, built to maximize:
- batching efficiency
- throughput under high concurrency
- consistent latency
- hardware utilization (CPU/GPU), tokenizer performance, memory behavior

Writing a “fast local embedding inference runtime” from scratch is hard because you’d be rebuilding:
- model loading & execution (ONNX/ggml/candle/etc.)
- fast tokenization
- dynamic batching and padding strategies
- SIMD/BLAS/GPU kernels
- memory mapping, quantization, caching
- observability, throttling, backpressure

**But**: we are **not doing local embeddings now**. Since embeddings are Gemini cloud calls, we *will* “write our own” embedding client and high-throughput worker engine in Go, because that is simply:
- HTTP/2 JSON requests
- batching and retry logic
- durable job handling

So TEI is relevant later only if/when we add local embeddings.

---

## High-level architecture (single binary)

### Files on disk
Under Eve app dir (macOS default):
- `~/Library/Application Support/Eve/eve.db` — normalized warehouse DB
- `~/Library/Application Support/Eve/eve-queue.db` — durable job queue DB (separate to avoid lock contention)
- `~/Library/Application Support/Eve/config.json` — non-secret config (models, limits)
- Secrets: prefer environment (`GEMINI_API_KEY`) initially; optional Keychain later.

### Process model
No background daemons are required.
- `eve compute run` runs in foreground until work drains (or `--watch` mode).
- Optional later: `eve compute install-service` for launchd.

### Core components inside the Go binary
- **Config**: loads config + env overrides
- **ETL**: reads `chat.db` + AddressBook, writes to `eve.db` (idempotent)
- **Queue**: leases jobs from `eve-queue.db` (durable)
- **Engine**:
  - scheduler + worker pools (analysis + embeddings)
  - single/batched DB writer to `eve.db`
- **Gemini clients**:
  - analysis (`generateContent`) using Gemini Flash 3.0
  - embeddings (`embedContent` / batch embeddings) using latest embedding model
- **CLI**: stable JSON output, subcommands

---

## Design constraints for “absolutely FLY”

### 1) Concurrency is the only way to beat latency
Cloud analysis/embedding calls are latency-bound. Throughput is approximately:
\( \text{throughput} \approx \frac{\text{in-flight}}{\text{avg latency}} \)

To reach 200 convos/sec, we need:
- high in-flight concurrency (hundreds to thousands)
- HTTP/2 connection reuse
- rate limiting tuned to provider quotas
- low per-task overhead (no process-per-job)

### 2) Use HTTP/2 REST, not gRPC
We avoid gRPC stacks entirely to eliminate:
- `cygrpc` crashes
- runtime/event-loop interactions
HTTP/2 still gives multiplexing and reuse.

### 3) Batch embeddings aggressively
Embeddings should be queued and executed in batches (e.g., 64–256 texts per API request depending on API limits).

### 4) Durable + idempotent
All jobs must be safe to retry. Writes must be “exactly-once” logically via:
- unique idempotency keys
- `INSERT ... ON CONFLICT DO UPDATE`

---

## Databases

### `eve.db` (warehouse)
Keep current schema (as much as possible). Add only what’s necessary for:
- ETL watermarks
- analysis artifacts (already present)
- embeddings artifacts (already present)
- optional vector index metadata (stretch)

Recommended additions (if missing):
- `watermarks` table keyed by `source` + `name` (e.g., `chatdb.message_rowid`)
- indexes to support:
  - selecting unanalyzed conversations
  - selecting unembedded conversations
  - fast chat lookups by contact/name

### `eve-queue.db` (durable job queue)
Create a separate SQLite DB dedicated to job scheduling to avoid writer lock contention on `eve.db`.

#### Tables
- `jobs`
  - `id TEXT PRIMARY KEY` (ULID recommended)
  - `type TEXT NOT NULL`
  - `key TEXT NOT NULL UNIQUE` (idempotency key)
  - `payload_json TEXT NOT NULL`
  - `state TEXT NOT NULL` (pending|leased|succeeded|failed|dead)
  - `attempts INTEGER NOT NULL DEFAULT 0`
  - `max_attempts INTEGER NOT NULL DEFAULT 8`
  - `run_after_ts INTEGER NOT NULL` (unix seconds)
  - `lease_owner TEXT`
  - `lease_expires_ts INTEGER`
  - `last_error TEXT`
  - `created_ts INTEGER NOT NULL`
  - `updated_ts INTEGER NOT NULL`
- `runs`
  - `run_id TEXT PRIMARY KEY`
  - `created_ts INTEGER NOT NULL`
  - `config_json TEXT NOT NULL`
  - counters fields (pending/succeeded/failed/etc.) (optional; can compute by query)

#### Leasing protocol
- scheduler process identifies itself: `lease_owner`
- leases jobs with `lease_expires_ts = now + lease_ttl`
- periodically extends leases for in-flight jobs
- on startup and periodically: requeue expired leases

---

## CLI spec (agent-first)

### Commands
- `eve init`
  - create app dir
  - create/migrate `eve.db` + `eve-queue.db`
  - outputs JSON with paths + versions

- `eve sync`
  - runs ETL (idempotent)
  - default: full sync if no watermark; otherwise incremental
  - outputs JSON counts only (messages imported, chats, contacts, conversations)

- `eve compute enqueue`
  - queues bulk work (analysis + embeddings)
  - examples:
    - queue embeddings for all historic messages/convos
    - queue analysis for top N chats by volume
  - outputs JSON with counts of jobs enqueued (and dedup/skips)

- `eve compute run`
  - runs until queue drained (or `--watch`)
  - outputs JSON progress events (optional) and a final summary JSON

- `eve compute status`
  - reads queue DB only; outputs backlog + throughput metrics JSON

- `eve db query --sql ...`
  - raw SQL against `eve.db` (read-only by default; `--write` to allow mutations)

### Output formats
- default: JSON object on stdout
- streaming modes: `--jsonl` emits one JSON event per line
- text modes: `--format text|table` for humans (optional)
- message text is always behind explicit flag: `--include-text`

---

## Gemini integration (Go)

### Config keys
- `EVE_GEMINI_API_KEY` or `GEMINI_API_KEY` (env)
- `EVE_GEMINI_ANALYSIS_MODEL` default: `gemini-3.0-flash` (string; verify at runtime)
- `EVE_GEMINI_EMBED_MODEL` default: `text-embedding-005` (string; verify at runtime)

### Runtime verification
Implement `eve doctor` / `eve init` sanity checks:
- call Gemini “models list” endpoint (or a minimal request) to verify model IDs
- fail with clear JSON error if model is invalid

### HTTP client requirements
- HTTP/2 enabled
- large connection pools, keepalive
- per-host limits and tuned timeouts
- automatic retries with jitter on retryable errors (429/5xx/network)

### Rate limiting and backpressure
Implement:
- global token bucket (requests/sec)
- per-operation semaphore (max in-flight analysis / embeddings)
- dynamic slowdown on 429 (reduce concurrency temporarily)

### Embeddings batching
Implement an “embedding batcher”:
- collects embedding tasks into batches up to N texts or max bytes
- flushes every X ms or when batch full
- writes results with a single DB transaction

---

## Porting TS encoding/prompts into Go (for single binary)

We cannot depend on Bun/Node runtime if “single binary” is strict.

Plan:
- Treat prompt templates/context packs as **data files**.
  - Store under `assets/prompts/**` and `assets/context-packs/**`
  - Use `go:embed` to embed into the binary
- Port encoding logic from `ts/eve/encoding` to Go:
  - same formatting rules (participants, timestamps, attachments, reactions)
  - add parity tests (character-level where possible)

Short-term bootstrap:
- ship a minimal Go prompt for “basic” analysis (ConvoAll equivalent) to validate pipeline
- then port full prompt registry.

---

## ETL port plan (Python → Go)

### Source DBs (macOS)
- Messages DB: `~/Library/Messages/chat.db` (+ WAL/SHM)
- AddressBook DBs under: `~/Library/Application Support/AddressBook/**`

### ETL shape
Implement idempotent ETL in Go:
- extract chats/messages/handles/attachments/reactions
- resolve contacts and participant mappings
- group into conversations using **3-hour window** consistently
- write into `eve.db` with unique constraints and upserts
- store watermarks (rowid, last timestamp)

### Live sync
Phase 1:
- periodic incremental pull based on rowid watermark (no watcher)

Phase 2:
- optional watcher (file poll / FSEvents) to keep up with WAL (stretch)

---

## Stretch goal: vector search (SQLite-native preferred)

Target: avoid FAISS sidecar and keep single binary.

Options to evaluate:
- SQLite vector extension (if viable to bundle/ship)
- SQLite FTS5 for lexical search as a fallback

Deliverable for stretch:
- `eve search --query "..."`
  - vector (if available) + lexical fallback

---

## Testing strategy (must survive refactor)

### Non-negotiable test properties
- **No message text in test stdout** by default
- tests validate:
  - ETL correctness via counts + invariants
  - idempotency (run twice → no duplicates)
  - compute pipeline writes expected rows (analysis + embeddings)
  - performance sanity (throughput above floor on a batch)

### New Go test targets
- `go test ./...`
- integration tests that point at real `eve.db` but only assert counts/status

---

## Milestones (high-level)

1) Go CLI skeleton + app dir + config + `eve init`
2) `eve-queue.db` durable queue + `eve compute status`
3) Minimal analysis pipeline (Gemini Flash 3.0) + DB writes
4) Embeddings pipeline (Gemini embeddings) + batching + DB writes
5) Port encoding + prompts (Go) for parity with current outputs
6) Port ETL (Go) + remove Python dependency from “happy path”
7) Stretch: SQLite-native vector search

