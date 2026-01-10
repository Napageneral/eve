# Eve Skill (Agent-Facing)

Eve is a CLI-first personal communications database. It ingests iMessage + contacts into a local SQLite database (`eve.db`), then optionally runs high-throughput conversation analysis + embeddings.

## Installation

```bash
# Install the eve binary (single Go binary, no dependencies)
curl -sSL https://eve.install.sh | bash

# Or build from source
cd /path/to/eve && make build
```

## Quick Start

```bash
# Initialize: ETL iMessage + contacts into eve.db
eve init

# Check what got synced
eve db query --sql "SELECT COUNT(*) AS messages FROM messages"
eve db query --sql "SELECT COUNT(*) AS chats FROM chats"
eve db query --sql "SELECT COUNT(*) AS contacts FROM contacts"
```

## CLI Commands

### `eve init`
Run full ETL: extract iMessage + AddressBook contacts into `eve.db`.

### `eve db query`
Execute read-only SQL against `eve.db` and return stable JSON:

```bash
eve db query --sql "SELECT id, chat_name FROM chats ORDER BY last_message_date DESC LIMIT 10"
```

### `eve prompt list` / `eve prompt show <id>`
List available prompts or show a specific prompt's content.

### `eve pack list` / `eve pack show <id>`
List available context packs or show a specific pack's definition.

### `eve encode conversation <conversation_id>`
Encode a conversation into LLM-ready text (for analysis/embeddings).

### `eve compute run`
Start the compute engine to process analysis + embedding jobs from the queue.

### `eve compute test-casey-convo-all`
Benchmark: run `convo-all-v1` analysis on all Casey Adams conversations.

### `eve compute test-casey-embeddings`
Benchmark: run embeddings on all Casey conversations + facets.

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
| `GEMINI_API_KEY` | Gemini API key for analysis/embeddings | (required for compute) |
| `EVE_GEMINI_ANALYSIS_MODEL` | Model for conversation analysis | `gemini-3-flash-preview` |
| `EVE_GEMINI_EMBED_MODEL` | Model for embeddings | `gemini-embedding-001` |
| `EVE_GEMINI_ANALYSIS_RPM` | Analysis requests per minute (0=auto) | `0` |
| `EVE_GEMINI_EMBED_RPM` | Embedding requests per minute (0=auto) | `0` |

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
