# ChatStats Event Systems - Complete Reference

## Overview

ChatStats uses **TWO separate event systems** that serve different purposes. Understanding when to use each is critical.

---

## The Two Event Systems

### 1. EventBus (Redis Streams) - For Analysis & Backend Events

**Purpose:** Backend-to-backend communication, analysis progress tracking, Celery task lifecycle

**Implementation:** `services/core/event_bus.py`

**How it works:**
- Publishes events to Redis Streams (default: `task_events:analysis`)
- Frontend SSE endpoints subscribe to Redis Streams
- Filtered by scope (global, chat:X, task:X, historic, commitments:X)
- Persistent (survives backend restarts)

**Usage:**
```python
from backend.services.core.event_bus import EventBus

EventBus.publish(
    scope="global",                    # or "chat:123", "task:abc", "historic"
    event_type="run_complete",
    data={"run_id": run_id, "total": 100, "success": 95}
)
```

### 2. message_hub (In-Process Pub/Sub) - For Real-Time UI Updates

**Purpose:** Real-time message/chat/contact updates to frontend UI

**Implementation:** `services/core/chat_message_hub.py`

**How it works:**
- In-process pub/sub using asyncio.Queue
- Publishers: ETL layer when new messages arrive
- Subscribers: SSE endpoints that stream to frontend
- NOT persistent (lost on backend restart)
- Drops messages on overflow (no backpressure to ETL)

**Usage:**
```python
from backend.services.core.chat_message_hub import message_hub

# Publish new messages
message_hub.publish(chat_id, messages=[{...}])

# Publish chat/contact updates
message_hub.publish_chat_update(chat_id, last_message_time="2024-01-01T12:00:00")
```

---

## When to Use Which System

### Use EventBus (Redis Streams) When:
- ✅ Tracking analysis progress (global runs, per-chat analysis)
- ✅ Publishing Celery task lifecycle events (started, completed, failed, retry)
- ✅ Emitting events that Celery tasks need to consume
- ✅ Need persistence across backend restarts
- ✅ Need scope-based filtering

### Use message_hub (In-Process) When:
- ✅ Streaming new messages to frontend in real-time
- ✅ Updating chat list (recency changes)
- ✅ Updating contact list
- ✅ Need immediate UI updates with minimal latency
- ✅ Data is ephemeral (OK to lose on restart)

---

## Complete Event Type Catalog

### EventBus Events (Redis Streams)

#### Global Scope (`scope="global"`)

| Event Type | Published By | Data Fields | Purpose |
|------------|--------------|-------------|---------|
| `run_starting` | `celery_service/services.py` | run_id, message, percentage, status, running | Global/ranked analysis starting |
| `run_seeded` | `celery_service/services.py` | run_id, total, pending, percentage | Counters seeded, tasks queued |
| `analysis_completed` | `celery_service/tasks/analyze_conversation.py` | run_id, success, failed, processing, timings | Individual task completed (throttled) |
| `run_complete` | `celery_service/tasks/analyze_conversation.py` | run_id, total, success, failed, percentage | All tasks finished |

#### Historic Scope (`scope="historic"`)

| Event Type | Published By | Data Fields | Purpose |
|------------|--------------|-------------|---------|
| `run_started` | `celery_service/services.py` | task_id, run_id | Historic analysis started |
| `analysis_completed` | `celery_service/tasks/analyze_conversation.py` | run_id, success, failed, processing | Task completed in historic run |
| `run_complete` | `celery_service/tasks/analyze_conversation.py` | run_id, is_complete | Historic run finished |

#### Chat Scope (`scope="chat:{chat_id}"`)

| Event Type | Published By | Data Fields | Purpose |
|------------|--------------|-------------|---------|
| `analysis_completed` | `services/conversations/analysis.py` | conversation_id, status | Single conversation analysis done |
| `conversation_sealed` | `etl/live_sync/conversation_tracker.py` | chat_id, conversation_id, last_message_time | 90-min gap detected |

#### Task Scope (`scope="task:{task_id}"`)

| Event Type | Published By | Data Fields | Purpose |
|------------|--------------|-------------|---------|
| `progress` | `celery_service/tasks/base.py` (BaseTaskWithDLQ) | task_id, progress, status, message | Task progress update |
| `completed` | `celery_service/tasks/base.py` | task_id, message, result | Task completed successfully |
| `failed` | `celery_service/tasks/base.py` | task_id, error, permanent | Task failed |
| `retry` | `celery_service/tasks/base.py` | task_id, error, retry_count, max_retries | Task retrying |
| `status` | `celery_service/tasks/base.py` | task_id, status, error | Task status change |

---

## Complete SSE Endpoint Catalog

### message_hub Endpoints (Real-Time Data)

**1. `/api/live-sync/stream/chat/{chat_id}/messages`**
- **Purpose:** Real-time message updates for a specific chat
- **Events:**
  - `initial` - Full message history on connect
  - `ready` - Subscription active
  - `update` - New messages batch
  - `heartbeat` - Keep-alive ping (every 30s)
  - `error` - Error occurred
- **Data Source:** message_hub.subscribe(chat_id)
- **Use Case:** Chat view in frontend

**2. `/api/live-sync/stream/chats`**
- **Purpose:** Chat list recency updates
- **Events:**
  - `initial` - All chats with stats
  - `ready` - Subscription active
  - `update` - Chat metadata changed (last_message_time, title)
  - `heartbeat` - Keep-alive ping
  - `error` - Error occurred
- **Data Source:** message_hub.subscribe_all_chats()
- **Use Case:** Chat list in frontend

**3. `/api/live-sync/stream/contacts`**
- **Purpose:** Contact list recency updates
- **Events:**
  - `initial` - All contacts with stats
  - `ready` - Subscription active
  - `update` - Contact metadata changed
  - `heartbeat` - Keep-alive ping
  - `error` - Error occurred
- **Data Source:** message_hub.subscribe_all_contacts()
- **Use Case:** Contact list in frontend

### EventBus Endpoints (Redis Streams)

**4. `/api/analysis/streaming/queue/stream`**
- **Purpose:** Analysis progress and task lifecycle events
- **Query Params:**
  - `scope` - Filter events (global, chat:X, task:X, historic)
  - `run_id` - Further filter global events by run_id
  - `start_id` - Redis Stream starting point (default: `$` for new only)
- **Events:** All EventBus event types (see catalog above)
- **Data Source:** Redis Stream `task_events:analysis`
- **Use Case:** Analysis progress bars, task status tracking

**5. `/api/analysis/streaming/redis_stream/{stream_key}`**
- **Purpose:** Direct Redis Stream access (generic)
- **Query Params:**
  - `start_id` - Stream starting point (default: `0`)
- **Events:** Depends on stream_key
- **Data Source:** Any Redis Stream
- **Use Case:** Custom stream consumers

**6. `/api/metrics/stream`**
- **Purpose:** Runtime performance metrics
- **Events:** Continuous metrics snapshots
- **Data Source:** RuntimeMetrics.snapshot()
- **Use Case:** Performance monitoring dashboard

---

## Event Flow Diagrams

### Analysis Progress Events (EventBus)

```
Frontend SSE: /api/analysis/streaming/queue/stream?scope=global&run_id=123
    ↓ subscribes to
Redis Stream: task_events:analysis
    ↑ receives events from
Celery Tasks / Services:
    - run_starting (when analysis starts)
    - run_seeded (when tasks queued)
    - analysis_completed (per task, throttled)
    - run_complete (when all tasks done)
```

### Real-Time Message Updates (message_hub)

```
Frontend SSE: /api/live-sync/stream/chat/456/messages
    ↓ subscribes to
message_hub.subscribe(456)
    ↑ receives from
ETL Layer: sync_messages.py
    - Publishes after committing new messages to DB
    - Includes serialized message data
```

### Conversation Sealing Flow (EventBus)

```
conversation_tracker (Redis-based)
    ↓ after 90-min gap
EventBus.publish(scope="{chat_id}", event_type="conversation_sealed")
    ↓ written to
Redis Stream: task_events:analysis
    ↓ consumed by
Celery Task: handle_sealed_conversation
    ↓ runs ETL
etl_conversations() finalizes conversation
    ↓ triggers
handle_conversation_ready → batch analysis passes
```

---

## Redis Stream Configuration

**Default Stream:** `task_events:analysis`

**Max Length:** 200,000 events (approx, uses MAXLEN ~)

**Event Format:**
```json
{
  "type": "analysis_completed",
  "scope": "global",
  "ts": "2024-01-01T12:00:00.000000",
  "run_id": "123",
  "success": "95",
  "failed": "5",
  ...
}
```

**Field Serialization:**
- Numbers → strings
- Dicts/lists → JSON strings
- Everything else → str()

**Consumer Pattern:**
- Use `XREAD BLOCK` with `start_id`
- Filter by `scope` field
- Further filter by `run_id` if needed

---

## Common Patterns

### Publishing Analysis Events

```python
from backend.services.core.event_bus import EventBus

# Global run event
EventBus.publish(
    scope="global",
    event_type="run_complete",
    data={"run_id": run_id, "total": 100, "success": 95}
)

# Chat-specific event
EventBus.publish(
    scope=f"chat:{chat_id}",
    event_type="analysis_completed",
    data={"conversation_id": conv_id}
)
```

### Publishing UI Updates (Messages)

```python
from backend.services.core.chat_message_hub import message_hub

# Publish new messages
message_hub.publish(chat_id, messages=[
    {"id": 1, "content": "Hello", "timestamp": "..."},
    {"id": 2, "content": "World", "timestamp": "..."}
])

# Publish chat metadata update
message_hub.publish_chat_update(
    chat_id=123,
    last_message_time="2024-01-01T12:00:00",
    title="Updated Title"
)
```

### SSE Consumer Pattern (Frontend)

```typescript
const eventSource = new EventSource('/api/analysis/streaming/queue/stream?scope=global&run_id=123');

eventSource.addEventListener('message', (e) => {
  const data = JSON.parse(e.data);
  if (data.type === 'run_complete') {
    // Update UI
  }
});

eventSource.addEventListener('heartbeat', () => {
  // Connection still alive
});
```

---

## Debugging Events

### Check Redis Streams

```bash
# See recent events
redis-cli XREVRANGE task_events:analysis + - COUNT 10

# See stream length
redis-cli XLEN task_events:analysis

# Monitor live events
redis-cli XREAD BLOCK 0 STREAMS task_events:analysis $
```

### Check message_hub State

```python
# In Python console
from backend.services.core.chat_message_hub import message_hub

# See active subscribers
print(message_hub._subscribers)  # Per-chat
print(len(message_hub._all_chats_subscribers))  # Global chat list
print(len(message_hub._all_contacts_subscribers))  # Global contact list
```

---

## Common Pitfalls

**1. Using Wrong Event System**

❌ **WRONG:** Using EventBus for real-time messages
```python
# Don't do this - message_hub is for messages
EventBus.publish(f"chat:{chat_id}", "new_message", {"content": "..."})
```

✅ **CORRECT:** Use message_hub for messages
```python
message_hub.publish(chat_id, messages=[{...}])
```

**2. Not Filtering SSE Events**

❌ **WRONG:** Subscribing to all events and filtering in frontend
```javascript
// Don't subscribe to entire stream
const es = new EventSource('/api/analysis/streaming/queue/stream?scope=global');
```

✅ **CORRECT:** Filter by scope in query params
```javascript
// Backend filters before sending
const es = new EventSource('/api/analysis/streaming/queue/stream?scope=global&run_id=123');
```

**3. Blocking message_hub Publishers**

❌ **WRONG:** Slow consumers blocking ETL
```python
# message_hub has maxsize=1000 and drops on overflow
# If your consumer is slow, messages get dropped
```

✅ **CORRECT:** Process messages quickly in SSE consumer
```python
# SSE endpoints use asyncio.wait_for with timeout
# Frontend processes updates in batches
```

---

## Related Documentation

- **[Main Backend Guide](./agents.md)** - Overall backend architecture
- **[Celery Service](./celery_service/agents.md)** - How tasks publish events
- **[ETL Layer](./etl/agents.md)** - How message_hub is used
- **[Routers Guide](./routers/agents.md)** - SSE endpoint patterns

