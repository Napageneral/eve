# 💬 Eve — iMessage Analysis & Embeddings

A macOS CLI to sync, analyze, and embed your iMessage conversations using Gemini. Copies data from Messages.app (`chat.db`) into a local SQLite warehouse (`eve.db`), then runs LLM analysis and generates embeddings.

## Features

* **ETL Pipeline**: Sync handles, chats, messages, attachments from `chat.db` → `eve.db`
* **Conversation Grouping**: Automatically groups messages into conversations (3-hour gap threshold)
* **LLM Analysis**: Run conversation analysis using Gemini (entities, topics, emotions, humor)
* **Embeddings**: Generate vector embeddings for semantic search
* **Durable Queue**: Reliable job queue with retry logic and adaptive rate limiting
* **JSON Output**: All commands emit stable JSON (no message text by default for privacy)
* **Single Binary**: Pure Go, no Python/Node runtime required

## Requirements

* macOS 14+ with Messages.app signed in
* Full Disk Access for your terminal to read `~/Library/Messages/chat.db`
* Gemini API key (for analysis and embeddings)

## Install

### Homebrew (recommended)

```bash
brew install Napageneral/tap/eve
```

### Go Install

```bash
go install github.com/Napageneral/eve/cmd/eve@latest
```

### Build from Source

```bash
git clone https://github.com/Napageneral/eve.git
cd eve
make build
# binary at ./bin/eve
```

## Commands

### Initialize

```bash
eve init
```

Creates `eve.db` (warehouse) and `eve-queue.db` (job queue) with schema migrations.

### Sync Messages

```bash
# Full sync
eve sync

# Dry run (count messages without syncing)
eve sync --dry-run
```

Copies all data from `chat.db` → `eve.db`:
- Handles → contacts + contact_identifiers
- Chats → chats
- Messages → messages (incremental via watermark)
- Attachments → attachments
- Auto-builds conversations from messages

### Query Database

```bash
# Count messages
eve db query --sql "SELECT COUNT(*) FROM messages"

# List recent chats
eve db query --sql "SELECT id, chat_name, total_messages FROM chats ORDER BY last_message_date DESC LIMIT 10"

# Write operations (use with caution)
eve db query --sql "UPDATE ..." --write
```

### Run Compute Jobs

```bash
# Check queue status
eve compute status

# Process queued jobs
eve compute run --workers 10 --timeout 300
```

### Test Analysis

```bash
# Run analysis on your biggest chat
eve compute test-analysis --limit 10

# Run on a specific chat
eve compute test-analysis --chat-id 123 --workers 50
```

### Test Embeddings

```bash
# Generate embeddings for your biggest chat
eve compute test-embeddings --limit-conversations 10

# Run on a specific chat
eve compute test-embeddings --chat-id 123
```

### Manage Prompts & Packs

```bash
# List available prompts
eve prompt list

# Show a specific prompt
eve prompt show convo-all-v1

# List context packs
eve pack list

# Export embedded resources
eve resources export --dir ./my-resources
```

### Encode Conversations

```bash
# Encode to file
eve encode conversation --conversation-id 123

# Encode to stdout
eve encode conversation --conversation-id 123 --stdout
```

### Utility

```bash
# Show paths
eve paths

# Show version
eve version
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Gemini API key (required for compute) | — |
| `EVE_APP_DIR` | Override app directory | `~/Library/Application Support/Eve` |
| `EVE_GEMINI_ANALYSIS_RPM` | Max analysis requests/min | `0` (auto) |
| `EVE_GEMINI_EMBED_RPM` | Max embedding requests/min | `0` (auto) |
| `EVE_GEMINI_ANALYSIS_MODEL` | Analysis model | `gemini-2.0-flash` |
| `EVE_GEMINI_EMBED_MODEL` | Embedding model | `gemini-embedding-001` |

## Rate Limiting

Eve has built-in adaptive rate limiting to avoid hitting Gemini quotas:

- **Auto RPM**: When `*_RPM` is `0`, Eve probes the API and auto-adjusts
- **Adaptive Controller**: Backs off on 429s/timeouts, ramps up when stable
- **Smooth Traffic**: Prevents burst spikes that can overwhelm home routers

Example for high-quota tiers:

```bash
export EVE_GEMINI_ANALYSIS_RPM=20000
export EVE_GEMINI_EMBED_RPM=20000
```

## Permissions Troubleshooting

If you see "unable to open database file" or empty output:

1. **Full Disk Access**: System Settings → Privacy & Security → Full Disk Access → add your terminal
2. Ensure Messages.app is signed in and `~/Library/Messages/chat.db` exists

## Database Schema

Eve creates a normalized warehouse with these tables:

- `contacts` — resolved people (includes "Me" where `is_me=1`)
- `contact_identifiers` — phone/email identifiers mapped to contacts
- `chats` — conversation threads
- `chat_participants` — join table for group chats
- `conversations` — message windows (3-hour gap heuristic)
- `messages` — normalized messages
- `attachments` — attachment metadata
- `reactions` — tapback/reactions
- `conversation_analyses` — LLM analysis results
- `embeddings` — vector embeddings

## Development

```bash
# Run tests
make test

# Build binary
make build

# Format code
go fmt ./...
```

## License

MIT

## See Also

- [imsg](https://github.com/steipete/imsg) — CLI for sending/receiving iMessages
- [gogcli](https://github.com/steipete/gogcli) — CLI for Galaxy of Games
