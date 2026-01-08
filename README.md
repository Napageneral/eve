# Eve

**Eve** is a single Go binary for iMessage analysis and embeddings. It copies data from macOS Messages (`chat.db`) into a local SQLite warehouse (`eve.db`), then runs conversation analysis and embeddings using Gemini.

## Architecture

- **Single binary**: `eve` — pure Go, no Python/Node runtime required
- **ETL**: Copies handles, chats, messages, attachments from `chat.db` → `eve.db`
- **Conversations**: Automatically groups messages into conversations (3-hour gap threshold)
- **Compute engine**: Durable job queue + worker pool for Gemini analysis/embeddings
- **JSON output**: All commands output stable JSON (no message text by default for privacy)

## Installation

```bash
go build -o bin/eve ./cmd/eve
```

Or use `make go-build`

## Usage

### Initialize databases

```bash
eve init
```

Creates `eve.db` (warehouse) and `eve-queue.db` (durable queue) with schema migrations.

### Sync data from Messages

```bash
eve sync
```

Copies all data from `chat.db` → `eve.db`:
- Handles → contacts + contact_identifiers
- Chats → chats
- Messages → messages (incremental via watermark)
- Attachments → attachments
- Builds conversations from messages

Supports incremental sync: only syncs new messages since last run.

Use `--dry-run` to count messages without copying.

### Run compute jobs

```bash
# Check queue status
eve compute status

# Process queued jobs
eve compute run --workers 10
```

### View paths

```bash
eve paths
```

Shows where `eve.db`, `eve-queue.db`, and config are stored.

## Development

```bash
# Run all tests
make test

# Run Go tests only
make go-test

# Build binary
make go-build
```
