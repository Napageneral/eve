## Execution checklist (Ralph)

This is the ordered “do this next” list. Each step has a deliverable and an acceptance check.

> Rule: keep stdout machine-readable JSON by default. Logs to stderr/files.

---

### 0) Pre-flight: align on contracts

- [ ] **Write down contracts**
  - Output: `docs/ralph/contracts.md` (can be small)
  - Must include: JSON stdout default, opt-in message text, idempotency keys, raw SQL only

- [ ] **Inventory existing schema + invariants**
  - Output: `docs/ralph/db-invariants.md`
  - Include unique constraints we rely on (e.g., conversations uniqueness, analyses uniqueness)

Acceptance:
- docs exist and are referenced from `docs/ralph/README.md`

---

### 1) Go skeleton + packaging

- [ ] Add Go module
  - `go.mod`, `cmd/eve/main.go`, `internal/...`
- [ ] Add minimal CLI framework
  - commands: `eve version`, `eve paths`
- [ ] Add release scaffolding
  - `goreleaser` config (optional now) + Makefile target: `make go-build`

Acceptance:
- `go build ./cmd/eve` produces a binary
- `./eve version` prints JSON

---

### 2) App dir + config

- [ ] Implement app dir resolution (macOS default)
- [ ] Implement config load:
  - env overrides: `GEMINI_API_KEY`, `EVE_GEMINI_ANALYSIS_MODEL`, `EVE_GEMINI_EMBED_MODEL`
  - file: `~/Library/Application Support/Eve/config.json`

Acceptance:
- `eve paths` returns JSON with `app_dir`, `eve_db_path`, `queue_db_path`, `config_path`

---

### 3) Migrations (SQL, embedded)

- [ ] Create `internal/migrate` that runs `.sql` migrations (embedded with `go:embed`)
- [ ] Create queue DB migrations (schema in plan)
- [ ] Add `eve init` that creates/migrates both DBs

Acceptance:
- `eve init` returns JSON `{ok:true,...}`
- rerunning `eve init` is idempotent

---

### 4) Durable queue (SQLite)

- [ ] Implement queue primitives:
  - enqueue (idempotent by `key`)
  - lease batch
  - heartbeat/extend leases
  - mark succeeded/failed + retry scheduling
- [ ] `eve compute status` reads queue DB and prints JSON backlog summary

Acceptance:
- enqueue same job twice → second is a no-op (dedup)
- kill process mid-run → leases expire → jobs requeue

---

### 5) Gemini client (HTTP/2 REST)

- [ ] Implement a single shared HTTP client with:
  - HTTP/2 enabled
  - tuned pools and keepalives
  - retry/backoff on 429/5xx/network
- [ ] Implement `eve doctor`:
  - checks `GEMINI_API_KEY` present
  - validates model strings (best-effort)

Acceptance:
- `eve doctor` prints JSON with clear error fields if key missing

---

### 6) Minimal compute engine (analysis)

- [ ] Implement `eve compute run`:
  - scheduler leases jobs
  - worker pool executes analysis jobs concurrently
  - writer persists results to `eve.db` idempotently
- [ ] Implement analysis job:
  - read conversation from `eve.db`
  - encode (temporary simple encoder)
  - call Gemini Flash 3.0
  - write into `conversation_analyses` + downstream facet tables (as per current schema)

Acceptance:
- queue 100 analysis jobs → `compute run` completes
- rerun → no duplicates
- stdout contains counts/timings only by default (no message text)

---

### 7) Embeddings pipeline (batching)

- [ ] Implement embedding jobs with batching:
  - queue contains “embed conversation X” items
  - batcher groups into requests to Gemini embedding model
  - writer persists blobs to `embeddings` table idempotently

Acceptance:
- queue 100 embedding jobs → `compute run` completes
- steady-state throughput is acceptable and stable (no crashes)

---

### 8) Port encoding/prompts (remove TS runtime dependency)

- [ ] Embed prompt files (`assets/prompts/**`) and implement prompt loader
- [ ] Port TS encoding rules to Go (parity tests)
- [ ] Replace temporary encoder with ported encoder

Acceptance:
- encoding parity tests pass (or documented deltas)

---

### 9) Port ETL (Python → Go)

- [ ] Implement `eve sync` in Go:
  - reads `chat.db` + AddressBook
  - loads into `eve.db` idempotently
  - conversation grouping uses 3-hour window
  - maintains watermarks

Acceptance:
- `eve sync` run twice → same counts, no duplicates
- `eve sync` incremental works (watermark advances)

---

### 10) Performance + “fly” tuning

- [ ] Add `eve bench` commands for:
  - analysis throughput
  - embeddings throughput
- [ ] Add adaptive throttling:
  - reduce concurrency on 429 bursts
  - ramp back up gradually

Acceptance:
- On a large batch (e.g., 5k convos), throughput is stable and significantly higher than the current Python stack.

