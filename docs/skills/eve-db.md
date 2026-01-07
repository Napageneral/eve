# Eve DB Skill (Agent-Facing)

This doc is a **skill** for agents/harnesses: how to read and query Eve’s local database (`eve.db`) directly using **raw SQL**.

## Where the DB lives

- **Default path (macOS):** `~/Library/Application Support/Eve/eve.db`
- **Override:** set `EVE_APP_DIR` (Eve will use `EVE_APP_DIR/eve.db`)

## Serverless access (recommended)

Use the CLI to execute **read-only** SQL and return stable JSON:

```bash
eve db query --sql "SELECT COUNT(*) AS c FROM messages" --limit 1 --pretty
```

Notes:
- By default, `eve db query` blocks non-`SELECT`/`WITH` statements.
- Pass `--write` only if you intentionally want to run a write (unsafe).

## Core tables (iMessage domain)

- **`contacts`**: resolved people (includes “Me” where `is_me=1`)
- **`contact_identifiers`**: phone/email identifiers mapped to contacts
- **`chats`**: conversation threads (one-on-one or group)
- **`chat_participants`**: join table linking chats ↔ contacts
- **`conversations`**: “conversation windows” inside a chat (90-minute gap heuristic)
- **`messages`**: normalized messages
- **`attachments`**: attachment metadata linked to `messages`
- **`reactions`**: tapback/reaction rows

## Common query patterns

### List chats (most recent first)

```sql
SELECT
  c.id,
  c.chat_name,
  c.is_group,
  c.last_message_date,
  c.total_messages
FROM chats c
ORDER BY c.last_message_date DESC
LIMIT 50;
```

### Find chats by name (substring match)

```sql
SELECT id, chat_name, is_group
FROM chats
WHERE chat_name LIKE '%' || :q || '%'
ORDER BY last_message_date DESC
LIMIT 50;
```

(If you’re using `eve db query`, inline the value instead of `:q`.)

### Find contacts by name

```sql
SELECT id, name
FROM contacts
WHERE name LIKE '%' || :q || '%'
ORDER BY name
LIMIT 50;
```

### Get chats involving a contact (via participants join)

```sql
SELECT c.id, c.chat_name, c.last_message_date
FROM chats c
JOIN chat_participants cp ON cp.chat_id = c.id
WHERE cp.contact_id = :contact_id
ORDER BY c.last_message_date DESC
LIMIT 50;
```

### Get most recent conversation for a chat

```sql
SELECT id, start_time, end_time, message_count
FROM conversations
WHERE chat_id = :chat_id
ORDER BY end_time DESC
LIMIT 1;
```

### Fetch messages for a conversation (for transcript building)

```sql
SELECT
  m.timestamp,
  COALESCE(ct.name, CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE 'Unknown' END) AS sender_name,
  m.is_from_me,
  m.content
FROM messages m
LEFT JOIN contacts ct ON ct.id = m.sender_id
WHERE m.conversation_id = :conversation_id
ORDER BY m.timestamp ASC;
```

### Quick “search messages” (lexical substring; slow on huge DB)

```sql
SELECT m.id, m.chat_id, m.timestamp, m.content
FROM messages m
WHERE m.content LIKE '%' || :q || '%'
ORDER BY m.timestamp DESC
LIMIT 50;
```

## Live sync state (incremental ingestion)

Watermarks live in `live_sync_state`:

```sql
SELECT key, value FROM live_sync_state;
```

Keys you’ll see:
- `last_message_rowid`
- `last_attachment_rowid`
- `apple_epoch_ns`

## Optional: Context Engine server

Eve also ships a TypeScript “Context Engine” server (HTTP) that provides:
- `/engine/encode` – canonical encoding output for analysis/embeddings
- `/engine/execute` – prompt + context-pack assembly with budget fitting

This is **optional** for agents. It’s mainly valuable for the **compute plane** (Celery) because it avoids spawning a new Bun process for every encode/execute call and caches the prompt/pack registry.


