# Eve Skill (Agent-Facing)

Eve is a CLI-first personal communications database. It ingests iMessage + contacts into a local SQLite database (`eve.db`), then optionally runs high-throughput conversation analysis + embeddings.

## Installation

```bash
# Eve runs from source (requires Python 3.10+ and the repo)
cd /path/to/eve
make py-install  # Creates .venv with dependencies

# The CLI is at bin/eve (Python script that auto-activates venv)
./bin/eve --help
```

## Two CLIs

Eve has two CLI implementations:

| CLI | Path | Description |
|-----|------|-------------|
| **Python CLI** | `bin/eve` | Full-featured: ETL, sync, compute plane, analysis |
| **Go CLI** | `bin/eve-go` | Lightweight: prompts, packs, encoding, context compile |

**Build the Go CLI:**
```bash
make go-build  # Creates bin/eve-go
```

**Go CLI commands:**
```bash
eve-go version                     # Version info
eve-go prompt list                 # List available prompts
eve-go prompt show <id>            # Show prompt details
eve-go pack list                   # List context packs
eve-go pack show <id>              # Show pack definition
eve-go resources export --dir DIR  # Export embedded resources
eve-go db query --sql "..."        # Query eve.db
eve-go encode conversation --conversation-id N --stdout  # Encode conversation
eve-go context compile --prompt <id>  # Compile context pack
```

## Quick Start

```bash
# 1. Initialize: create eve.db and run migrations
eve init

# 2. Sync: ETL iMessage + contacts into eve.db
eve sync

# 3. Check what got synced
eve status
```

## CLI Commands

### Core Data Commands

#### `eve init`
Initialize Eve: create app directory and run database migrations.

```bash
eve init
# Output: {"ok":true,"app_dir":"...","db_path":"...","migrated":true}
```

#### `eve sync`
Run one-shot ETL import from `chat.db` into `eve.db`.

```bash
# Full import (first run)
eve sync --race-mode

# Import only last 30 days
eve sync --since-days 30

# Import since specific date
eve sync --since "2025-01-01T00:00:00Z"

# Skip AddressBook contacts
eve sync --no-contacts
```

#### `eve status`
Print database counts and live sync state.

```bash
eve status
# Output: {"ok":true,"counts":{"messages":12345,"chats":100,...},"live_sync_state":{...}}
```

#### `eve watch`
Run live sync watcher (incremental updates).

```bash
# Run for 60 seconds
eve watch --seconds 60

# Run until 10 batches processed
eve watch --max-batches 10

# Custom poll interval
eve watch --poll-interval-ms 100
```

### Database Access

#### `eve db query`
Execute raw SQL against `eve.db` and return stable JSON.

```bash
# Count messages
eve db query --sql "SELECT COUNT(*) AS total FROM messages"

# List recent chats
eve db query --sql "SELECT id, chat_name, total_messages FROM chats ORDER BY last_message_date DESC LIMIT 10"

# Custom row limit
eve db query --sql "SELECT * FROM messages" --limit 500

# Enable writes (dangerous)
eve db query --sql "UPDATE contacts SET name = 'Test' WHERE id = 1" --write
```

### Compute Plane (Analysis + Embeddings)

#### `eve compute doctor`
Diagnose compute-plane readiness (Redis, Celery, Context Engine).

```bash
eve compute doctor
# Output: {"ok":true,"checks":{"redis_running":{"ok":true},...},"advice":[...]}
```

#### `eve compute up`
Start compute plane processes (Redis + Context Engine + Celery workers).

```bash
# Start everything
eve compute up

# Custom ports
eve compute up --redis-port 6380 --context-port 3032

# Custom worker concurrency
eve compute up --celery-concurrency 100

# Skip specific components
eve compute up --no-redis --no-context-engine
```

#### `eve compute down`
Stop compute plane processes.

```bash
eve compute down
```

#### `eve compute status`
Report compute plane process status.

```bash
eve compute status
# Output: {"ok":true,"status":{"redis":{"running":true},"celery_ping":{"ok":true,"workers":[...]},...}}
```

#### `eve compute analyze`
Trigger analysis pass for a conversation (optionally wait).

```bash
# Analyze latest conversation
eve compute analyze --latest

# Analyze specific conversation
eve compute analyze --conversation-id 12345

# Wait for completion
eve compute analyze --conversation-id 12345 --wait --timeout-seconds 300

# Require embeddings to be generated too
eve compute analyze --conversation-id 12345 --wait --require-embeddings
```

### Utility Commands

#### `eve paths`
Print computed paths (app dir, db path).

```bash
eve paths
# Output: {"ok":true,"app_dir":"...","db_path":"...","source_chat_db":"..."}
```

#### `eve migrate`
Migrate an existing ChatStats `central.db` into Eve's `eve.db` location.

```bash
# Default: migrate from ~/Library/Application Support/ChatStats/central.db
eve migrate

# Custom source
eve migrate --from-db /path/to/central.db

# Overwrite existing
eve migrate --force
```

## Where the DB lives

- **Default path (macOS):** `~/Library/Application Support/Eve/eve.db`
- **Override:** set `EVE_APP_DIR` (Eve will use `$EVE_APP_DIR/eve.db`)

## Core Tables (iMessage domain)

| Table | Description |
|-------|-------------|
| `contacts` | Resolved people (includes "Me" where `is_me=1`) |
| `contact_identifiers` | Phone/email identifiers mapped to contacts |
| `chats` | Conversation threads (one-on-one or group) |
| `chat_participants` | Join table linking chats ↔ contacts |
| `conversations` | "Conversation windows" inside a chat (3-hour gap heuristic) |
| `messages` | Normalized messages |
| `attachments` | Attachment metadata linked to messages |
| `reactions` | Tapback/reaction rows |

## Analysis Tables (computed)

| Table | Description |
|-------|-------------|
| `conversation_analyses` | Analysis results per conversation + prompt |
| `entities` | Named entities extracted from conversations |
| `topics` | Topics discussed in conversations |
| `emotions` | Emotions detected per participant |
| `humor_items` | Humorous snippets extracted |
| `embeddings` | Vector embeddings for conversations + facets |

## Common Query Patterns

### List chats (most recent first)

```sql
SELECT id, chat_name, is_group, last_message_date, total_messages
FROM chats
ORDER BY last_message_date DESC
LIMIT 50;
```

### Find chats by name

```sql
SELECT id, chat_name, is_group
FROM chats
WHERE chat_name LIKE '%Casey%'
ORDER BY last_message_date DESC;
```

### Find contacts by name

```sql
SELECT id, name
FROM contacts
WHERE name LIKE '%Adams%'
ORDER BY name;
```

### Get chats involving a contact

```sql
SELECT c.id, c.chat_name, c.last_message_date
FROM chats c
JOIN chat_participants cp ON cp.chat_id = c.id
WHERE cp.contact_id = 42
ORDER BY c.last_message_date DESC;
```

### Fetch messages for a conversation

```sql
SELECT
  m.timestamp,
  COALESCE(ct.name, CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE 'Unknown' END) AS sender_name,
  m.is_from_me,
  m.content
FROM messages m
LEFT JOIN contacts ct ON ct.id = m.sender_id
WHERE m.conversation_id = 12345
ORDER BY m.timestamp ASC;
```

### Get analysis facets for a chat

```sql
SELECT 'entities' AS type, COUNT(*) AS count FROM entities WHERE chat_id = 1
UNION ALL
SELECT 'topics', COUNT(*) FROM topics WHERE chat_id = 1
UNION ALL
SELECT 'emotions', COUNT(*) FROM emotions WHERE chat_id = 1
UNION ALL
SELECT 'humor_items', COUNT(*) FROM humor_items WHERE chat_id = 1;
```

### Search messages (lexical substring)

```sql
SELECT m.id, m.chat_id, m.timestamp, m.content
FROM messages m
WHERE m.content LIKE '%pizza%'
ORDER BY m.timestamp DESC
LIMIT 50;
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `EVE_APP_DIR` | Base directory for Eve data | `~/Library/Application Support/Eve` |
| `EVE_SOURCE_CHAT_DB` | Override source chat.db path | `~/Library/Messages/chat.db` |
| `GEMINI_API_KEY` | Gemini API key for analysis/embeddings | (required for compute) |
| `EVE_REDIS_URL` | Redis broker URL | `redis://127.0.0.1:6379/0` |
| `EVE_SQLITE_BUSY_TIMEOUT_MS` | SQLite busy timeout | `5000` |

## Global Flags

All commands accept these flags:

| Flag | Description |
|------|-------------|
| `--app-dir PATH` | Override Eve app directory |
| `--source-chat-db PATH` | Override source chat.db path |
| `--sqlite-busy-timeout-ms MS` | Override SQLite busy timeout |
| `--pretty` | Pretty-print JSON output |

## Prompts & Packs

Eve ships with editable prompt resources in `resources/prompts/` and context pack definitions in `resources/packs/`. These are plain markdown/YAML files you can customize.

Key prompts:
- `convo-all-v1` — Structured conversation analysis (summary, entities, topics, emotions, humor)
- `overall-v1` — Year/period overview across multiple conversations

## Performance Notes

With a Tier-3 Gemini API key:
- **Conversation analysis:** ~150-180 convos/sec
- **Conversation embeddings:** ~400+ embeddings/sec
- **Facet embeddings:** ~300+ embeddings/sec

The compute engine includes an adaptive controller that automatically adjusts concurrency based on network conditions and API rate limits.

## Testing

```bash
# Run all tests
make test

# Run only Go tests
make go-test

# Run only Python tests
make py-test

# Run only TypeScript tests
make test-ts
```
