# Backend Routers Architecture Guide

## Overview

This directory contains all FastAPI routers organized by domain. Each router handles HTTP endpoints for a specific area of functionality.

---

## ⚠️ CRITICAL: LOGGING - THE STANDARD APPROACH

**ALWAYS use standard Python logging at the module level:**

```python
import logging

logger = logging.getLogger(__name__)

# In your endpoint functions:
logger.info("Getting chat messages for chat_id=%s", chat_id)
logger.error("Failed to process: %s", error, exc_info=True)
logger.debug("Detailed debug info: %s", data)
```

**How it works:**

1. Python logging writes to stdout/stderr
2. Electron's `pipeLines()` captures these streams
3. Logs appear in Electron console with proper scope

**DON'T:**

- ❌ Use `log_simple()` - it has a hardcoded logger name bug
- ❌ Use `print()` - bypasses logging system
- ❌ Try to import electron-log in Python
- ❌ Add IPC calls to send logs

**DO:**

- ✅ Use `logging.getLogger(__name__)` at module level
- ✅ Use appropriate log levels (DEBUG, INFO, WARNING, ERROR)
- ✅ Include context in log messages (use % formatting, not f-strings for better performance)
- ✅ Use `exc_info=True` for exceptions

---

## Common Utilities

Import from `common.py` for consistency:

```python
from backend.routers.common import (
    create_router, safe_endpoint, 
    HTTPException, Query, Depends, BaseModel,
    text, Session, db, get_db
)
import logging

logger = logging.getLogger(__name__)
router = create_router("/your-path", "Your Domain")

@router.get("/endpoint")
@safe_endpoint
async def your_endpoint():
    logger.info("Endpoint called")
    # Your logic
    pass
```

**Key utilities:**

- `create_router(prefix, tags)` - Standardized router setup
- `@safe_endpoint` - Automatic error handling (doesn't swallow HTTPException)
- `to_dict(obj, fields, transforms)` - Response formatting helper
- Common imports - Reduces boilerplate

---

## Session Management

**ALWAYS use context manager:**

```python
from backend.db.session_manager import db

with db.session_scope() as session:
    # Your database operations
    # Auto-commits on success, auto-rollbacks on exception
    pass
```

**Alternative for dependency injection:**

```python
from backend.routers.common import Depends, Session, get_db

@router.get("/endpoint")
@safe_endpoint
async def endpoint(session: Session = Depends(get_db)):
    # Use session
    pass
```

---

## Raw SQL Only

**NEVER use ORM query methods. ALWAYS use raw SQL:**

```python
from backend.db.sql import fetch_one, fetch_all, execute_write

with db.session_scope() as session:
    # Single row
    row = fetch_one(session, 
        "SELECT * FROM chats WHERE id = :id", 
        {"id": chat_id}
    )
    
    # Multiple rows
    rows = fetch_all(session,
        "SELECT * FROM messages WHERE chat_id = :chat_id",
        {"chat_id": chat_id}
    )
    
    # Write (with automatic SQLite lock retry)
    execute_write(session,
        "UPDATE chats SET title = :title WHERE id = :id",
        {"title": new_title, "id": chat_id}
    )
```

**Why:** ORM queries cause functional and performance issues. Raw SQL is explicit and predictable.

---

## SSE Streaming Pattern

**Use SSE for real-time updates, NOT polling loops:**

```python
from backend.routers.sse_utils import encode_sse_event
from starlette.responses import StreamingResponse
import asyncio
import json

@router.get("/stream")
async def stream_updates(request: Request):
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                    
                # Send data event
                yield encode_sse_event(
                    event="progress",
                    data=json.dumps({"status": "processing", "progress": 0.5})
                )
                
                # Send heartbeat every ~30 seconds
                await asyncio.sleep(30)
                yield encode_sse_event(event="heartbeat", data="ping")
        finally:
            # Cleanup code here
            pass
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
```

**Key points:**

- Check `request.is_disconnected()` to stop streaming
- Send periodic heartbeats (every 1-30 seconds depending on use case)
- Use `encode_sse_event()` for proper SSE format
- Filter by scope when using Redis streams
- Always include proper cleanup in `finally` block

**❌ DON'T create polling loops - use SSE streaming instead!**

---

## Celery Task Queuing

**Standard pattern for async work:**

```python
from backend.celery_service.tasks.your_task import your_task

@router.post("/trigger", status_code=202)
@safe_endpoint
async def trigger_work(request: YourRequest):
    result = your_task.apply_async(
        args=[arg1, arg2],
        kwargs={"kwarg1": value1},
        queue='chatstats-queue-name'
    )
    
    return {
        "task_id": result.id,
        "status": "queued",
        "message": "Task queued successfully"
    }
```

**Standard queues:**

- `chatstats-report` - Report generation
- `chatstats-analysis` - Conversation analysis
- `chatstats-bulk` - Bulk operations
- `chatstats-display` - Display generation

---

## Router Organization

### Domain Structure

```
routers/
├── admin/              # Queue monitoring, admin utilities
├── analysis/           # Conversation analysis operations
│   ├── analysis_router.py        # Historic/global analysis
│   ├── bulk_operations.py        # Bulk chat analysis
│   ├── single_operations.py      # Single conversation analysis
│   ├── progress_streaming.py     # SSE progress updates
│   ├── progress.py               # Progress snapshots
│   └── runtime_metrics.py        # Performance metrics
├── chatbot/            # AI chatbot domain (threads, messages, documents)
│   ├── chats.py                  # Thread CRUD
│   ├── messages.py               # Message CRUD
│   ├── documents.py              # AI-generated artifacts
│   ├── document_displays.py      # Visual displays for documents
│   ├── votes.py                  # Message voting
│   ├── streams.py                # Stream management
│   ├── users.py                  # User management
│   ├── suggestions_history.py   # Suggestion tracking
│   ├── llm.py                    # LLM generation
│   ├── tags.py                   # Tag parsing/resolution
│   └── utils.py                  # Chatbot-specific utilities
├── chats/              # iMessage domain (historical messages)
│   └── messages.py               # iMessage CRUD and analysis
├── commitments/        # Commitment tracking (currently inactive)
│   ├── history_analysis.py       # Historical commitment detection
│   ├── operations.py             # Commitment CRUD
│   └── two_stage_processing.md   # System documentation
├── core/               # Core utilities
│   ├── health.py                 # Health checks
│   └── utils.py                  # Token counting
├── reports/            # Report generation (NOTE: may be obsolete - see Open Questions)
├── system/             # System operations
│   ├── database.py               # DB stats, ETL triggers
│   ├── imports.py                # Data import operations
│   └── live_sync.py              # SSE streaming for live updates
├── templates/          # Prompt and context templates
│   ├── contexts.py               # Context definitions/selections
│   └── prompts.py                # Prompt templates
├── users/              # User profile management
├── billing.py          # Subscription management
├── catalog.py          # Catalog operations
├── common.py           # Shared utilities ⭐
├── embeddings.py       # Embedding search
├── notify_router.py    # Notifications (SMS)
├── shared_models.py    # Shared Pydantic models ⭐
└── sse_utils.py        # SSE streaming utilities ⭐
```

### The Two Domains

**⚠️ CRITICAL: There are TWO completely separate "chat" systems:**

**1. iMessage Domain (`/chats/*`) - Historical message analysis**

- **Tables:** `chats`, `messages`, `conversations`, `contacts`
- **Routes:** `/chats/{chat_id}/messages`, `/chats/{chat_id}/analysis`
- **Purpose:** Analyze historical iMessage data
- **Naming:** `chat_id` refers to iMessage conversations

**2. AI Chatbot Domain (`/chatbot/*`) - AI assistant conversations**

- **Tables:** `chatbot_chats`, `chatbot_messages_v2`, `chatbot_documents`
- **Routes:** `/chatbot/chats`, `/chatbot/messages`, `/chatbot/documents`
- **Purpose:** AI agent conversations with the user
- **Naming:** In chatbot routes, `chat_id` refers to AI threads
- **Note:** UI calls these "threads" - see Open Questions about naming consistency

---

## Special Router Patterns

### Analysis Routers

**Three types of analysis operations:**

1. **Global/Historic Analysis** (`analysis_router.py`)
   - Analyzes all conversations across all chats
   - Returns `run_id` for tracking progress
   - Uses Redis counters for live progress

2. **Bulk Analysis** (`bulk_operations.py`)
   - Analyzes all conversations in a single chat
   - Queues Celery tasks per conversation

3. **Single Analysis** (`single_operations.py`)
   - Analyzes one conversation
   - Direct task queue

**Progress Tracking:**

Three approaches exist (see Open Questions):
- `/status` - One-shot JSON snapshot
- `/queue/stream` - SSE streaming with Redis
- Redis `snapshot(run_id)` - Direct Redis query

### Chatbot Routers

**Special Patterns:**

**1. Message Part Normalization** (`utils.py`):

```python
from backend.routers.chatbot.utils import normalize_parts_for_storage

# Hides compiled prompt context from UI
normalized = normalize_parts_for_storage(parts, role)
```

This prevents the UI from flashing long compiled prompts. 

**Future:** May be replaced by an agent managing communication layer.

**2. Document Auto-Generation:**

Documents automatically queue display generation based on tags:

```python
tags = body.get("tags") or []
should_generate = (
    body.get("kind") in ("text", "sheet") or 
    ("display:auto" in tags)
)
if should_generate:
    generate_document_display_task.apply_async(...)
```

**3. Draft Management:**

Threads (chatbot chats) support draft messages:

```python
# Ensure draft columns exist (migration helper)
ensure_draft_columns(session)

# Update draft
session.execute(text("""
    UPDATE chatbot_chats 
    SET has_draft = TRUE, 
        draft_text = :text,
        draft_updated_at = CURRENT_TIMESTAMP
    WHERE id = :id
"""))
```

**4. Thread Context Tracking System:**

**Overview:** Tracks which iMessage chats/contacts are being analyzed in Eve threads

Threads (AI conversations) track their source context via the `thread_contexts` table:

```sql
CREATE TABLE thread_contexts (
    id TEXT PRIMARY KEY,              -- "{thread_id}:{type}:{id}"
    chat_id TEXT NOT NULL,            -- FK to chatbot_chats
    context_type TEXT NOT NULL,       -- 'chat' or 'contact'
    context_id TEXT,                  -- Source chat/contact ID
    context_name TEXT,                -- Resolved name
    added_at TIMESTAMP,
    added_by_message_id TEXT
);
```

**When saved:**
- Thread creation (`/create-eve` endpoint) - Saves contexts immediately
- Message creation (`/messages` endpoint) - Saves contexts from message metadata

**Name resolution:**
Backend automatically resolves chat/contact names from source tables:

```python
if ctx_type == "chat" and ctx_ref:
    # Look up actual chat name from chats table
    chat_row = session.execute(text(
        "SELECT chat_name FROM chats WHERE id = :chat_id"
    ), {"chat_id": int(ctx_ref)}).fetchone()
    if chat_row:
        ctx_name = chat_row[0]
elif ctx_type == "contact" and ctx_ref:
    # Look up contact name from contacts table
    contact_row = session.execute(text(
        "SELECT name FROM contacts WHERE id = :contact_id"
    ), {"contact_id": int(ctx_ref)}).fetchone()
    if contact_row:
        ctx_name = contact_row[0]
```

**When queried:**
`/chats-with-latest` JOINs `thread_contexts` and returns aggregated `contexts` array

**Result:** Thread chips display "Casey Adams" instead of "Chat 3" in inbox UI

**5. Document Context Tracking System:**

**Overview:** Documents snapshot their origin thread's contexts at creation time

Documents (AI-generated artifacts) track context via the `document_contexts` table:

```sql
CREATE TABLE document_contexts (
    id TEXT PRIMARY KEY,              -- "{doc_id}:{type}:{id}"
    document_id TEXT NOT NULL,        -- FK to chatbot_documents
    context_type TEXT NOT NULL,       -- 'chat' or 'contact'
    context_id TEXT,                  -- Source chat/contact ID
    context_name TEXT,                -- Resolved name
    added_at TIMESTAMP
);
```

**When saved:**
- Document creation (`POST /documents`) - Snapshots thread contexts automatically
- Document update (`POST /documents/{id}/update`) - Optionally updates contexts
- Context-only update (`PATCH /documents/{id}/contexts`) - Updates without new version

**Snapshotting logic** (in `save_document()` endpoint):

```python
# After saving document, snapshot thread contexts
if body.get("origin_chat_id"):
    # Get all contexts from the origin thread
    thread_contexts = session.execute(text(
        "SELECT context_type, context_id, context_name FROM thread_contexts WHERE chat_id = :chat_id"
    ), {"chat_id": body["origin_chat_id"]}).mappings().all()
    
    # Copy each context to document_contexts
    for ctx in thread_contexts:
        session.execute(text(
            "INSERT INTO document_contexts (...) VALUES (...) ON CONFLICT DO NOTHING"
        ), {...})
```

**When queried:**
`/documents` endpoint JOINs `document_contexts` and returns aggregated `contexts` array (same pattern as threads)

**Updateable contexts:**
- `POST /documents/{id}/update` accepts `contexts` parameter
- `PATCH /documents/{id}/contexts` dedicated endpoint for context-only updates
- Supports "replace" mode (delete all, add new) or "add" mode (merge)

**Result:** Documents show correct source chat name in inbox (e.g., "Casey Adams" not "System")

**Why separate from threads?**
- Documents are immutable snapshots - contexts preserved at creation time
- Documents can outlive their origin threads
- Allows updating contexts independently of document content
- Matches frontend expectation that documents have stable, explicit contexts

**5. Eve Thread Creation (Hybrid Architecture):**

**Endpoint:** `POST /api/chatbot/threads/create-eve`

**Purpose:** Atomically create Eve-generated thread before streaming starts

Eve prompts are executed via Next.js `/api/chat` (Vercel AI SDK streaming), but thread creation is a data operation that belongs in Python.

This endpoint creates the thread record BEFORE streaming starts, ensuring:
1. Thread exists when inbox queries database
2. No race conditions between creation and display  
3. Clean separation: Python owns data, Next.js owns streaming
4. Frontend can orchestrate both backends

**Request:**
```json
{
  "title": "Casey Adams : Hogwarts House Sorting",
  "source_chat_id": 3,
  "prompt_id": "hogwarts-v1",
  "user_id": "local-user",
  "visibility": "private"
}
```

**Response:**
```json
{
  "thread_id": "abc-123-...",
  "created_at": "2025-10-25T12:34:56Z"
}
```

**Database operations:**
- INSERT into `chatbot_chats` table
- Optional INSERT into `chatbot_thread_metadata` (stores prompt_id, source_chat_id)

**Flow:**
```
Frontend calls /create-eve
  ↓
Python: Creates thread, returns ID
  ↓
Frontend: dispatch('analysis:started')
  ↓
Frontend: POST /api/chat (Next.js streaming)
  ↓
Frontend: dispatch('chat:created')
  ↓
Inbox: Queries DB, finds thread
```

**Related endpoints:**
- `POST /api/chat` (Next.js) - Streams LLM response for thread ID
- `GET /api/chatbot/chats-with-latest` - Returns threads (including those without messages)

**Critical:** The `chats-with-latest` query was updated to use `LEFT JOIN` and remove the message-existence filter, allowing threads without messages to be returned. This is essential for Eve threads to appear before streaming completes.

### System Routers

**Live Sync SSE Endpoints:**

Three streaming endpoints:
- `/stream/chat/{chat_id}/messages` - Message updates for specific chat
- `/stream/chats` - Chat list updates (recency, metadata)
- `/stream/contacts` - Contact list updates

**Pattern:**
1. Send `initial` event with full data
2. Send `ready` event when subscription active
3. Stream `update` events for changes
4. Periodic `heartbeat` events

---

## Common Pitfalls

### 1. Don't Create Polling Loops

❌ **WRONG:**

```python
# Don't do this!
while True:
    status = await get_status()
    if status.is_complete:
        break
    await asyncio.sleep(1)
```

✅ **CORRECT:**

```python
# Use SSE streaming instead
@router.get("/stream")
async def stream_progress():
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
```

### 2. Don't Mix Domains

❌ **WRONG:**

```python
# Don't reference chatbot tables in iMessage routes
@router.get("/chats/{chat_id}/analysis")
def get_analysis(chat_id: int):
    # This is iMessage domain, don't touch chatbot_chats!
    pass
```

### 3. Don't Use ORM Queries

❌ **WRONG:**

```python
chats = session.query(Chat).filter(Chat.id == chat_id).all()
```

✅ **CORRECT:**

```python
from backend.db.sql import fetch_all
chats = fetch_all(session, 
    "SELECT * FROM chats WHERE id = :id", 
    {"id": chat_id}
)
```

### 4. Don't Forget Error Handling

Always use `@safe_endpoint`:

```python
@router.get("/endpoint")
@safe_endpoint  # ← Don't forget this!
async def endpoint():
    pass
```

---

## Adding New Routers

### Checklist for new router files:

1. **Import from common.py:**

```python
from backend.routers.common import (
    create_router, safe_endpoint,
    HTTPException, Query, Depends, BaseModel,
    text, Session, db, get_db
)
```

2. **Use standard logging:**

```python
import logging
logger = logging.getLogger(__name__)
```

3. **Create router:**

```python
router = create_router("/your-path", "Your Domain")
```

4. **Apply `@safe_endpoint` to all endpoints**

5. **Use raw SQL with `db.sql` helpers**

6. **Use `db.session_scope()` for transactions**

7. **Register in `main.py`:**

```python
from backend.routers.yourmodule import router as your_router

router_specs = [
    # ...
    (your_router, "/api/your-path", {"tags": ["your-tag"]}),
]
```

---

## Testing Endpoints

**No automated tests exist in backend.**

**Manual testing via:**
- Electron app (calls via IPC → HTTP)
- Direct HTTP calls (curl, Postman, etc.)

**TODO:** Testing strategy TBD for future.

---

## Resolved Questions

### ✅ Progress Tracking Pattern (Verified 2025-10-06)

**Finding:** Multiple patterns serve different purposes - all actively used

**Patterns:**
1. **SSE streaming** (`/queue/stream`) - Real-time UI updates
2. **Polling** (`/status`) - One-shot snapshots for diagnostics
3. **Direct Redis** (`snapshot(run_id)`) - Used by both above, low-level access

**Usage:**
- Frontend components use SSE streaming (`useProgressSSE` hook)
- Routers use direct Redis access via `redis_counters.snapshot()`
- Celery tasks use `counter_buffer` for batched writes

**Decision:** Keep all patterns - each serves distinct purpose

## Open Questions & TODOs

### Backend-Wide Open Questions (Tracked in Main agents.md)

For backend-wide open questions that affect routers, services, and repositories, see the main backend agents.md:
- Thread Naming Consistency (accepted per ADR-003)
- Documentation Accuracy
- Analysis Router Endpoints - Which Are Active?

---

## Related Documentation

- **[Main Backend Guide](../agents.md)** - Overall backend architecture
- **[Electron IPC](../../electron/agents.md)** - How frontend calls these routers
- **[two_stage_processing.md](commitments/two_stage_processing.md)** - Commitment system documentation
