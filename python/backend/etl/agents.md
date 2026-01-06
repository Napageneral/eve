# ChatStats ETL Layer Architecture

## Overview

The ETL (Extract, Transform, Load) layer manages data ingestion from macOS iMessage (`chat.db`) and AddressBook into ChatStats. It operates in two modes: **backup import** (historical one-time load) and **live sync** (continuous incremental updates).

**Critical Principle:** The ETL layer is the ONLY code that writes to the iMessage domain tables (`chats`, `messages`, `conversations`, `contacts`). All other code reads through repositories.

---

## ⚠️ CRITICAL: Two Import Modes

### Backup Import (Historical)

**Used during:** Initial onboarding to load user's entire iMessage history

**When:** First-time user setup, or importing from iPhone backup

**How it works:**
1. Extract all data from backup databases
2. Use "fresh split & compare" for conversations (preserves IDs when intervals match)
3. **Race mode enabled** for maximum speed

**Race Mode:**
- Drops indexes before bulk insert
- Sets `PRAGMA journal_mode=OFF` and `PRAGMA synchronous=OFF`
- **UNSAFE** but necessary for performance
- Imports 300k+ messages in ~7-8 seconds (vs 20+ seconds safe mode)
- **Automatically restores safe settings after import**

**⚠️ WARNING:** Race mode should ONLY be used during initial backup import. Never enable manually unless you know what you're doing.

### Live Import (Incremental)

**Continuous monitoring** of live `chat.db` for new messages while app is running.

**When:** Always running after initial import

**How it works:**
1. Poll `chat.db-wal` file every 50ms for changes
2. Fetch new messages using ROWID watermarks (fast!)
3. Debounce and batch process (50ms + 25ms sleep)
4. Update conversations, trigger analysis, publish events

**Key differences from backup:**
- Uses ROWID-based watermarks instead of timestamps (much faster)
- Incremental conversation updates instead of full re-split
- No race mode (data integrity matters more than speed)
- Triggers real-time UI updates and analysis

---

## The Event Flow (CRITICAL)

**Understanding the event flow is THE key to understanding how the system works:**

```
1. User sends/receives message in Messages.app
   ↓
2. chat.db WAL file changes (detected by 50ms polling)
   ↓
3. wal.py debounces (50ms + 25ms), calls fetch_new_messages(last_rowid)
   ↓
4. sync_messages() processes batch:
   - Creates/updates chats
   - Resolves contacts (AddressBook sync if needed)
   - Creates/extends conversations (90-min window)
   - Inserts messages with conversation_id
   - Commits to database
   ↓
5. Publishes to TWO separate systems:
   A) message_hub (in-process pub/sub) → SSE → Frontend
      Purpose: Real-time UI updates
   B) handle_new_messages_synced_task (Celery) → LIVE analysis
      Purpose: Run analysis passes on new messages
   ↓
6. Updates conversation_tracker in Redis:
   - Stores last_message_time per chat
   - Schedules sealing check (timestamp + 90 min)
   ↓
   [90 minutes pass with no new messages]
   ↓
7. conversation_tracker.check_and_seal_conversations() (periodic, 60s)
   ↓
8. Emits "conversation_sealed" event (Redis Streams via EventBus)
   ↓
9. Celery task handle_sealed_conversation consumes event
   ↓
10. Runs ETL to finalize conversation (etl_conversations)
   ↓
11. Emits "conversation_ready_for_analysis" event
   ↓
12. Triggers BATCH analysis passes
```

### Two Parallel Event Systems

**1. `message_hub` → SSE → Frontend:** Real-time UI updates (new messages appear immediately)

**2. `EventBus` → Redis Streams → Celery:** Analysis pipeline (runs AI on conversations)

**These are separate systems serving different purposes. Don't confuse them.**

---

## Conversation Lifecycle

### States

**Active:** Messages arriving, conversation window still open (< 90 min since last message)

**Sealed:** 90+ minutes have passed since last message, conversation is complete

**Ready for Analysis:** Conversation has been finalized in ETL, ready for batch analysis passes

### The 90-Minute Gap Heuristic

Messages within 90 minutes of each other = same conversation.  
After 90 minute gap = seal previous conversation, start new one.

**Why 90 minutes?** Natural conversation pattern. People often pause for an hour+ then resume the same topic. Beyond 90 minutes, it's usually a new conversation.

### How Sealing Works

**During live sync:**

1. Each new message updates `conversation_tracker` in Redis:
   - Key: `chat:last_msg:{chat_id}`
   - Value: timestamp of last message
   - Scheduled check added to sorted set at `timestamp + 90min`

2. Periodic task `check_and_seal_conversations()` runs every 60 seconds:
   - Checks sorted set for any chats past their check time
   - If 90+ minutes since last message → emit `conversation_sealed` event
   - Celery task consumes event and finalizes conversation

3. Finalization triggers `conversation_ready_for_analysis` event

4. Batch analysis passes run on sealed conversation

**Edge Cases:**

- **App closed for days:** `check_expired_conversations_startup()` runs on startup to seal any expired conversations
- **Force sealing:** `force_seal_chat(chat_id)` can manually seal a conversation (used for testing)
- **Messages arriving 89 minutes apart:** Uses actual message timestamp, not check run time, so handles correctly

**⚠️ NOTE:** There may be unhandled edge cases around conversation sealing failures or mid-batch ETL failures. System has been reliable in practice, but failure modes are not fully documented.

---

## Watermark Management

### What Are Watermarks?

Watermarks track "how far" we've processed in the source data, enabling incremental imports.

### Three Watermarks

**1. Message ROWID Watermark** (Primary for live sync)
- **Stored:** `live_sync_state` table, key = `last_message_rowid`
- **Tracks:** Last processed message ROWID from chat.db
- **Query:** `SELECT * FROM message WHERE ROWID > ?`
- **Why ROWID?** MUCH faster than timestamp filtering (no date conversion, uses primary key index)

**2. Attachment ROWID Watermark** (Primary for live sync)
- **Stored:** `live_sync_state` table, key = `last_attachment_rowid`
- **Tracks:** Last processed attachment ROWID from chat.db
- **Query:** `SELECT * FROM attachment WHERE ROWID > ?`

**3. Apple Epoch Timestamp (Legacy)**
- **Stored:** `live_sync_state` table, key = `apple_epoch_ns`
- **Tracks:** Last processed timestamp in Apple's epoch (nanoseconds since 2001-01-01)
- **Rarely used now** - ROWID watermarks are preferred
- Kept for backward compatibility

### Why ROWID Watermarks Are Faster

**Before (timestamp-based):**
```sql
SELECT * FROM message WHERE date > ? ORDER BY date
-- Requires: Scan all rows, convert timestamps, compare
-- Performance: ~20 seconds for 300k messages
```

**After (ROWID-based):**
```sql
SELECT * FROM message WHERE ROWID > ? ORDER BY ROWID
-- Requires: Use primary key index, no conversion
-- Performance: ~7-8 seconds for 300k messages
```

**Result:** 3x faster due to index usage and no timestamp conversion.

### Watermark Update Flow

1. Fetch new data: `fetch_new_messages(last_rowid)`
2. Process batch: `sync_messages(new_messages)`
3. Commit to database
4. Update watermark: `set_message_rowid_watermark(new_max_rowid)`

**Critical:** Watermark MUST be updated after successful DB commit to ensure we don't lose messages on failure.

---

## Contact Syncing Strategy

### Three-Part System

**1. Immediate Startup Sync**
- Runs as soon as app starts (no 60s wait!)
- Ensures user sees fresh contact names immediately
- Non-blocking (background task)

**2. Periodic Sync**
- Every 60 seconds after startup
- Checks AddressBook database modification times
- Only processes databases that changed (efficient)

**3. On-Demand Sync**
- When unknown sender appears in message
- Attempts to find contact in AddressBook
- Falls back to creating basic contact (identifier as name)

### Smart Contact Merging

**Problem:** User has nameless contact "+1234567890", then adds proper name "John Smith" in Contacts app.

**Solution:**
1. Detect nameless contact (phone number or email as name)
2. Find AddressBook entry with proper name
3. Merge contacts:
   - Update contact name
   - Update all message references
   - Update all reaction references
   - Update chat_participants links
   - Update chat names for individual chats
4. Publish update events (SSE → Frontend)

**Cooldown:** 60-second cooldown per identifier to prevent redundant syncs.

---

## Caching Architecture

### Why Caching?

Live sync processes messages in batches every 50-75ms. Each message requires lookups for:
- Chat ID by identifier
- Contact ID by phone/email
- Existing message GUIDs (to detect duplicates)

**Without caching:** Thousands of DB queries per second.  
**With caching:** ~10-20 DB queries per batch.

### What's Cached

**Contact Map** (`CONTACT_MAP_CACHE`)
- Maps: normalized identifier → contact_id
- Normalized: Emails lowercase, phones without +1/-/spaces
- Thread-safe with lock

**Chat Map** (`CHAT_MAP_CACHE`)
- Maps: chat_identifier → chat_id
- Updated when new chats created

**Message GUID Map** (`MESSAGE_GUID_TO_ID_CACHE`)
- Maps: message guid → message database id
- Used to detect duplicate messages

**Reaction GUID Cache** (`REACTION_GUID_CACHE`)
- Set of all reaction GUIDs
- Used to detect duplicate reactions

**Attachment GUID Cache** (`ATTACHMENT_GUID_CACHE`)
- Set of all attachment GUIDs
- Used to detect duplicate attachments

**Chat Participants Map** (`CHAT_DB_ROWID_TO_PARTICIPANTS_CACHE`)
- Maps: chat.db ROWID → comma-separated participants
- Avoids GROUP_CONCAT on every message

### Cache Invalidation

**Automatically invalidated:**
- Contact added/updated: `update_contact_cache(identifier, contact_id)`
- Contact removed: `remove_contact_from_cache(identifier)`
- New chat created: Updates `CHAT_MAP_CACHE`
- New message inserted: Updates `MESSAGE_GUID_TO_ID_CACHE`

**Manual invalidation:**
- `reset_contact_cache()` - Force refresh contacts
- `reset_all_caches()` - Nuclear option, reloads everything

**Thread Safety:** All cache operations use `_cache_lock` to prevent race conditions during concurrent access.

---

## Performance Optimizations

### Key Optimizations (3x Speedup)

**1. ROWID Watermarks**
- Use `WHERE ROWID > ?` instead of `WHERE date > ?`
- Leverages primary key index
- No timestamp conversion overhead

**2. Pre-Built Maps**
- Build contact/chat/message maps once per batch
- Avoid per-message JOINs and subqueries
- Example: `_build_chat_identifier_map()` runs once, not per message

**3. Bulk Inserts**
- Collect all inserts in memory
- Single `executemany()` call
- SQLite handles batch efficiently

**4. Minimal JOINs**
- Extract data with separate queries
- Join in memory using maps
- Faster than complex SQL JOINs for chat.db

**5. Race Mode (Initial Import Only)**
- Drop indexes before bulk insert
- Unsafe PRAGMAs (`journal_mode=OFF`, `synchronous=OFF`)
- Rebuild indexes after
- 3x faster but only safe for one-time import

### Performance Results

- **Before optimizations:** 20+ seconds for 300k messages
- **After optimizations:** 7-8 seconds for 300k messages
- **3x speedup** with ROWID watermarks + pre-built maps + bulk inserts + race mode

**⚠️ Note:** Further optimizations possible but not worth the effort. 7-8 seconds is acceptable for initial import.

---

## File Structure

```
etl/
├── data_importer.py              # Orchestrates backup/live imports
├── etl_chats.py                  # Extract/transform/load chats
├── etl_contacts.py               # Extract/transform/load contacts
├── etl_messages.py               # Extract/transform/load messages
├── etl_attachments.py            # Extract/transform/load attachments
├── etl_conversations.py          # Two algorithms for conversation grouping
├── iphone_backup.py              # Locate iPhone backup databases
├── utils.py                      # Shared utilities (normalize_phone_number, etc.)
└── live_sync/                    # Real-time sync system
    ├── wal.py                    # Main watcher loop (polling + orchestration)
    ├── state.py                  # Watermark management
    ├── extractors.py             # Fast queries against chat.db
    ├── sync_messages.py          # Process new messages incrementally
    ├── sync_attachments.py       # Process new attachments incrementally
    ├── sync_contacts.py          # AddressBook syncing + merging
    ├── conversation_tracker.py   # Redis-based conversation sealing logic
    ├── cache.py                  # In-memory caches for fast lookups
    ├── timing.py                 # Performance timing helpers
    └── README.md                 # Live sync documentation
```

---

## Conversation Algorithms

### Two Approaches

**1. Forward/Incremental** (`etl_conversations()`)

**Used for:** Live sync, incremental backup imports

**Algorithm:**
- Fetch messages since last sync
- Split into conversations (90-min gaps)
- Append to last conversation if within gap, else create new

**Benefits:** Fast, simple, handles most cases

**2. Fresh Split & Compare** (`etl_conversations_fresh_split_compare()`)

**Used for:** Full backup imports (historical data)

**Algorithm:**
- Re-split ALL messages into conversations
- Compare each new interval to existing intervals
- If exact match (same message set) → reuse existing `conversation_id`
- Otherwise → delete overlapping intervals, insert new conversation

**Benefits:** Handles interleaved messages from backups, preserves conversation IDs when possible

**Why two algorithms?**  
Backup data has messages interleaved differently than live data. The "fresh split & compare" ensures conversations are correct even when historical messages arrive out of order.

**⚠️ DO NOT MODIFY:** Both algorithms are complex but necessary. Everything works correctly. Don't simplify unless you have weeks to debug edge cases.

---

## Live Sync Polling Strategy

### Why Polling Instead of File Watching?

**Current approach:** Poll `chat.db-wal`, `chat.db-shm`, and `chat.db` every 50ms

**Why polling instead of file watching?**
- macOS FSEvents can be unreliable for SQLite WAL files
- SQLite checkpoints can reset WAL without triggering FS events
- Polling is more reliable, only 50ms latency

**Debouncing:**
- 50ms wait after file change detected
- Additional 25ms sleep before processing
- Prevents stampede when multiple messages arrive quickly

---

## Global Analysis Gating

**Current behavior:** Live sync checks if global analysis is running before processing batches:

```python
async def _is_global_analysis_running() -> bool:
    # Check env flag: CHATSTATS_BULK_ANALYSIS_RUNNING
    # Check database: historic_analysis_status table
```

**Why?** Global analysis causes heavy DB reads/writes. Checking prevents SQLite write lock contention.

**⚠️ NOTE:** This was added by an LLM to fix lock issues. It's not the ideal solution (better would be a DB access queue), but it works. Leave it alone for now.

---

## Open Questions & Future Work

### ✅ RESOLVED (See EVENT_SYSTEMS.md)

**Event System Investigation** - COMPLETED
- Two event systems documented (EventBus vs message_hub)
- Complete event type catalog created
- Event flow mapped across all layers
- Clear guidelines on when to use each system

**SSE System Deep Dive** - COMPLETED
- 6 SSE endpoints identified and documented
- 3 message_hub endpoints (messages, chats, contacts)
- 3 EventBus endpoints (queue/stream, redis_stream, metrics)
- Event schemas and patterns documented

### 🟡 MEDIUM PRIORITY

**3. Redis Memory Tracking**

The conversation tracker has debug methods for memory monitoring:
- [ ] Are `cleanup_stale_checks()` and memory tracking methods needed?
- [ ] Monitor Redis memory usage in production
- [ ] Document if memory leaks occur and how to handle
- [ ] Consider automatic cleanup vs manual intervention

**4. Global Analysis Gating**

Current solution works but is hacky:
- [ ] Replace env flag + DB check with proper DB access queue
- [ ] Or use SQLite connection pooling with better timeouts
- [ ] Document the lock contention issue more thoroughly

### 🟢 LOW PRIORITY

**5. Edge Case Handling**

Document potential failure scenarios:
- [ ] What happens if `sync_messages` fails mid-batch?
- [ ] Add retry logic or dead letter queue for failed messages?
- [ ] Handle corrupted messages from chat.db gracefully?

**6. Contact Sync Frequency**

Current: 60 second intervals
- [ ] Is this the right interval?
- [ ] Make configurable based on user preference?
- [ ] Track how often AddressBook actually changes?

---

## Common Pitfalls

### 1. Forgetting to Commit Before Updating Watermark

❌ **WRONG:**
```python
process_messages(batch)
set_watermark(new_value)  # Watermark updated even if commit fails!
session.commit()
```

✅ **CORRECT:**
```python
process_messages(batch)
session.commit()  # Commit first
set_watermark(new_value)  # Only update watermark after successful commit
```

### 2. Using Wrong Watermark

❌ **WRONG - Slow timestamp filtering:**
```python
fetch_new_messages_by_date(last_apple_epoch)
```

✅ **CORRECT - Fast ROWID filtering:**
```python
fetch_new_messages(last_rowid)
```

### 3. Creating Contacts Without Checking Cache First

❌ **WRONG - Creates duplicate contacts:**
```python
contact_id = create_basic_contact(cursor, identifier)
```

✅ **CORRECT - Check cache first:**
```python
contact_id = CONTACT_MAP_CACHE.get(normalized_identifier)
if not contact_id:
    contact_id = sync_contact_from_addressbook(identifier)
    if not contact_id:
        contact_id = create_basic_contact(cursor, identifier)
```

### 4. Modifying Conversation Logic Without Extensive Testing

❌ **DANGER ZONE:**
```python
# Changing gap threshold, sealing logic, or split algorithms
# requires testing with 100+ conversations to avoid edge cases
```

✅ **SAFE:**
```python
# Leave conversation logic alone unless critical bug found
```

---

## Critical Don'ts

❌ **NEVER modify conversation windowing logic** - Complex, tested, works perfectly  
❌ **NEVER enable race mode manually** - Auto-enabled only during initial import  
❌ **NEVER use ORM queries** - Use raw SQL with `db.sql` helpers  
❌ **NEVER create sessions inside ETL functions** - Always use `db.session_scope()` or accept session parameter  
❌ **NEVER use timestamp-based watermarks for new code** - Use ROWID watermarks  
❌ **NEVER bypass caching during batch processing** - 10x slower without caches

---

## Related Documentation

- **[Main Backend Guide](../agents.md)** - Overall backend architecture
- **[Backend Routers](../routers/agents.md)** - API endpoints that trigger ETL
- **[Backend Services](../services/agents.md)** - Business logic that uses ETL data
- **[Backend Repositories](../repositories/agents.md)** - Data access patterns for ETL tables
- **[Celery Service](../celery_service/agents.md)** - Analysis triggered by ETL events
- **[Live Sync README](live_sync/README.md)** - Additional live sync documentation
- **[Conversation Event System](../docs/conversation_event_system.md)** - Event flow documentation

