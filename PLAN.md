# Eve CLI Consolidation Plan

## Vision
Eve becomes the single, unified tool for all iMessage operations - replacing the need for `imsg` as a separate dependency. One binary to install, one skill to learn.

---

## Current State Analysis

### What Eve Has (✅)

**ETL & Sync:**
- `eve init` - Initialize databases
- `eve sync` - Full ETL from chat.db → eve.db (handles, chats, messages, attachments, conversations)
- `eve sync --dry-run` - Preview sync
- Watermark-based incremental sync
- Contact name resolution from AddressBook

**Database:**
- `eve db query --sql "..."` - Raw SQL queries
- `eve db query --db queue` - Query queue database
- Schema: contacts, chats, messages, attachments, reactions, conversations, embeddings, entities, topics, emotions, humor_items

**Compute Engine:**
- `eve compute status` - Queue stats
- `eve compute run` - Process analysis/embedding jobs
- `eve compute test-analysis` - Test convo-all-v1 analysis
- `eve compute test-embeddings` - Test embedding generation
- Adaptive rate limiting, auto RPM probing
- Micro-batched DB writes for high concurrency

**Context Engine:**
- `eve prompt list/show` - Manage 29 prompts
- `eve pack list/show` - Manage 22 context packs
- `eve encode conversation --conversation-id N` - Encode for LLM
- `eve resources export` - Export embedded resources

**Utilities:**
- `eve whoami` - User info (name, phones, emails)
- `eve paths` - Show app paths
- `eve version` - Version info

### What Eve is Missing (❌)

**From imsg:**
1. **`chats` command** - List chats with names, not just raw SQL
2. **`history` command** - Get messages by chat with date filters
3. **`send` command** - Send messages via AppleScript
4. **`watch` command** - Stream incoming messages (FSEvents on chat.db)

**High-Level Query Commands:**
5. **`contacts` command** - Search/list contacts by name
6. **`messages` command** - Search messages by contact, date, keyword
7. **`search` command** - Semantic search using embeddings

**Live Sync / Events:**
8. **Watch daemon** - Background process monitoring chat.db
9. **Event publishing** - Webhooks/callbacks on new messages

**Attachment Access:**
10. **Image/file retrieval** - Access attachment files, not just metadata

---

## Implementation Plan

### Phase 1: Core Query Commands (Priority: HIGH)
Make Eve immediately useful for common lookups without raw SQL.

#### 1.1 `eve chats` - List Chats
```bash
eve chats                          # List all chats (most recent first)
eve chats --limit 10               # Limit results
eve chats --search "Casey"         # Filter by name
eve chats --json                   # JSON output
```

**Implementation:**
- Query `chats` table joined with `contacts` for names
- Sort by `last_message_date DESC`
- Include: id, name, last_message_date, total_messages, is_group

#### 1.2 `eve contacts` - Search Contacts
```bash
eve contacts                       # List all contacts
eve contacts --search "Casey"      # Fuzzy search by name
eve contacts --top 10              # Top contacts by message count
eve contacts --json
```

**Implementation:**
- Query `contacts` table
- Join with message counts per contact
- Fuzzy matching on name field

#### 1.3 `eve messages` - Query Messages
```bash
eve messages --chat-id 2           # Messages from chat
eve messages --contact "Casey"     # Messages with contact (fuzzy)
eve messages --since "2026-01-03"  # Date filter
eve messages --until "2026-01-04"
eve messages --search "curry"      # Keyword search
eve messages --limit 50
eve messages --json
```

**Implementation:**
- Query `messages` table with joins
- Support contact name → contact_id resolution
- Date parsing (ISO8601, relative: "yesterday", "last week")
- Full-text search on content

#### 1.4 `eve history` - Conversation History (imsg-compatible)
```bash
eve history --chat-id 2 --limit 20
eve history --chat-id 2 --start 2026-01-01 --end 2026-01-05
eve history --chat-id 2 --attachments
```

**Implementation:**
- Wrapper around messages query
- Include attachment metadata
- Format similar to imsg for compatibility

### Phase 2: Send & Watch (Priority: HIGH)
Enable bidirectional messaging.

#### 2.1 `eve send` - Send Messages
```bash
eve send --to "+16319056994" --text "Hello!"
eve send --chat-id 2 --text "Hello!"
eve send --contact "Casey" --text "Hello!"  # Resolve by name
eve send --to "..." --text "..." --file ~/photo.jpg
```

**Implementation:**
- Use AppleScript (like imsg does)
- Support phone, email, chat-id, or contact name
- Attachment support via AppleScript

#### 2.2 `eve watch` - Stream Messages
```bash
eve watch                          # Watch all chats
eve watch --chat-id 2              # Watch specific chat
eve watch --since-rowid 12345      # Start from rowid
eve watch --json                   # JSON output per message
```

**Implementation:**
- FSEvents watcher on chat.db (like imsg)
- Poll for new messages when WAL changes
- Output JSON events: `{"event":"message","chat_id":2,"text":"...","from":"Casey"}`

#### 2.3 `eve daemon` - Background Sync Service
```bash
eve daemon start                   # Start background watcher
eve daemon stop                    # Stop daemon
eve daemon status                  # Check if running
```

**Implementation:**
- Runs `eve watch` in background
- Auto-syncs new messages to eve.db
- Optionally triggers webhooks/events

### Phase 3: Semantic Search (Priority: MEDIUM)
Leverage embeddings for intelligent queries.

#### 3.1 `eve search` - Semantic Search
```bash
eve search "when did we talk about moving"
eve search "curry restaurant recommendations" --chat-id 2
eve search "funny moments" --limit 10
```

**Implementation:**
- Embed query text using Gemini
- Cosine similarity against conversation embeddings
- Return ranked results with snippets

#### 3.2 `eve similar` - Find Similar Conversations
```bash
eve similar --conversation-id 123  # Find similar convos
eve similar --message-id 456       # Find similar messages
```

### Phase 4: Attachment Access (Priority: MEDIUM)

#### 4.1 `eve attachments` - List/Access Attachments
```bash
eve attachments --chat-id 2        # List attachments in chat
eve attachments --message-id 123   # Attachments for message
eve attachments --type image       # Filter by type
eve attachments export --chat-id 2 --output ./exports/
```

**Implementation:**
- Query `attachments` table
- Resolve file paths (~/Library/Messages/Attachments/...)
- Copy/export files

#### 4.2 Image Analysis Integration
```bash
eve describe --attachment-id 456   # Describe image with vision model
eve ocr --attachment-id 456        # Extract text from image
```

### Phase 5: Analysis Improvements (Priority: LOW)

#### 5.1 `eve analyze` - On-Demand Analysis
```bash
eve analyze --chat-id 2            # Run full analysis
eve analyze --conversation-id 123  # Single conversation
eve analyze --contact "Casey"      # All convos with contact
```

#### 5.2 `eve insights` - Query Analysis Results
```bash
eve insights --chat-id 2           # Summary of analysis
eve insights topics --chat-id 2    # List topics
eve insights entities --chat-id 2  # List entities
eve insights emotions --chat-id 2  # Emotion breakdown
```

---

## Technical Considerations

### 1. AppleScript for Sending
imsg uses AppleScript to send messages. We need to port this:
```applescript
tell application "Messages"
    set targetService to 1st account whose service type = iMessage
    set targetBuddy to participant "+16319056994" of targetService
    send "Hello" to targetBuddy
end tell
```

Go can execute AppleScript via `osascript`.

### 2. FSEvents for Watching
imsg uses macOS FSEvents to detect chat.db changes. Options:
- Use `fsnotify` Go package
- Shell out to `fswatch`
- Poll with configurable interval

### 3. Embedding Search Performance
- Store embeddings as BLOB in SQLite
- Use sqlite-vec extension for vector search (or brute-force cosine for small datasets)
- Consider caching hot embeddings in memory

### 4. Contact Resolution
Need robust fuzzy matching:
- Exact match on name
- Case-insensitive match
- Partial match (first name, last name)
- Phone/email lookup

### 5. Date Parsing
Support multiple formats:
- ISO8601: `2026-01-03T00:00:00Z`
- Date only: `2026-01-03`
- Relative: `yesterday`, `last week`, `7 days ago`

---

## Priority Order

1. **Phase 1.1-1.4**: Query commands (chats, contacts, messages, history) - 2-3 days
2. **Phase 2.1**: Send command - 1 day
3. **Phase 2.2**: Watch command - 1-2 days
4. **Phase 3.1**: Semantic search - 2 days
5. **Phase 4.1**: Attachment access - 1 day
6. **Phase 2.3**: Daemon - 1 day
7. **Phase 5**: Analysis improvements - 2 days

**Total estimate: ~10-12 days of focused work**

---

## Questions to Resolve

1. Should `eve watch` output to stdout or use a callback/webhook system?
2. For semantic search, use sqlite-vec or brute-force cosine similarity?
3. Should the daemon auto-run analysis on new conversations?
4. How to handle group chats in contact resolution?
5. Should we support RCS messages (newer iOS feature)?
