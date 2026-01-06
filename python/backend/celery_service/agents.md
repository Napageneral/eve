# ChatStats Celery Service Architecture

## Overview

The Celery service layer orchestrates all asynchronous task processing for ChatStats. It manages conversation analysis, document generation, embeddings, and live message processing.

**Critical Principle:** Tasks are thin wrappers around workflow services. All business logic lives in `backend/services/` (pure Python, no Celery dependencies), making workflows testable without Celery.

---

## ⚠️ CRITICAL: DO NOT MODIFY

### Celery Worker Configuration

The worker configuration in `config.py` has been extensively performance-tuned through weeks of black-box optimization (see `perf_notes.md`).

**DO NOT modify without explicit instructions:**
- Worker counts and concurrency settings
- Pool types (gevent vs prefork)
- Prefetch multiplier
- Broker connection pools
- Queue routing

**Why:** Current config provides optimal throughput while respecting LLM provider rate limits. Changes can cause:
- Performance degradation requiring weeks to re-tune
- Redis connection exhaustion
- SQLite lock contention
- Rate limit violations

**Reference:** See `perf_notes.md` for detailed performance tuning history.

---

## Core Architecture

### Task Pattern: Thin Wrapper + Workflow Service

**Every task follows this pattern:**

```python
from backend.celery_service.tasks.base import BaseTaskWithDLQ
from backend.services.some_module import SomeWorkflowService
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, base=BaseTaskWithDLQ, name="celery.my_task")
def my_task(self, param1, param2, **kwargs):
    logger.info("Task started with param1=%s", param1)
    
    try:
        with self.step("processing", 50):
            # Delegate ALL business logic to workflow service
            result = SomeWorkflowService.run(
                param1=param1,
                param2=param2,
                **kwargs
            )
        
        return result
    except Exception as exc:
        logger.error("Task failed: %s", exc, exc_info=True)
        self.retry_with_backoff(exc)
```

**Why this pattern:**
- Workflows are pure Python (testable without Celery)
- Task only handles: progress, retries, DLQ, event publishing
- Business logic stays in services layer

### BaseTaskWithDLQ

All tasks inherit from `BaseTaskWithDLQ` which provides:

- Automatic retries with exponential backoff
- Dead Letter Queue (DLQ) for permanently failed tasks
- Progress tracking via `self.step()` context manager
- Event publishing for UI updates
- Lifecycle hooks: `on_success`, `on_failure`, `on_retry`

**Usage:**

```python
class MyTask(BaseTaskWithDLQ):
    """Custom task class if needed"""
    max_retries = 10  # Default: 10 retries
    default_retry_delay = 15  # Default: 15 seconds

@shared_task(bind=True, base=MyTask, name="celery.my_task")
def my_task(self, ...):
    # Task implementation
    pass
```

### Retry Configuration

**Default retry settings (as of 2025-11-02):**
- `max_retries = 120` - Up to 24+ hours of retries for extreme network resilience
- Custom backoff schedule optimized for flaky network conditions

**Retry behavior for failed tasks:**

Each task failure triggers a multi-layered retry strategy:

1. **Local LLM retries** (in `services/llm/completions.py`):
   - 2 fast retries per Celery attempt (~50ms, ~100ms delays)
   - Handles transient API errors immediately

2. **Celery-level retries** (in `BaseTaskWithDLQ`):
   - **Custom backoff schedule for network resilience:**
   
   ```
   Phase 1 (Retries 1-6):   Every 20s  → First 2 minutes
   Phase 2 (Retries 7-25):  Every 60s  → Next 19 minutes  
   Phase 3 (Retries 26+):   Every 15min → Next 24+ hours
   ```
   
   - **Timeline:**
     - 0-2 min: 6 fast retries (recover from brief network hiccups)
     - 2-21 min: 19 retries every minute (handle router issues)
     - 21 min - 24+ hours: Retry every 15 minutes (extreme persistence)
   
   - **Total retry window:** Up to 24+ hours before permanent failure
   - **Total retry attempts:** 120 retries

3. **Dead Letter Queue (DLQ)** (after all retries exhausted):
   - Failed tasks stored in DLQ
   - DLQ processing runs every 15 minutes via beat schedule
   - DLQ has its own exponential backoff for re-attempts

**Combined resilience:**
- Each task gets **120 Celery retries × 2 local LLM retries = 240 total LLM call attempts**
- Handles extremely poor network connectivity (low bandwidth, router timeouts, packet loss)
- Tasks complete eventually even with severe prolonged connectivity issues
- Optimized for environments where network may be unavailable for hours

**Why these settings?**
- Previous config (10 retries, exponential) gave up after ~3 hours
- User environment has very low bandwidth where router actively rejects requests
- 99% of tasks complete, but last 1% get stuck when retries exhausted
- New schedule: Aggressive early retries + persistent long-term retries
- Ensures tasks complete even if network is unusable for hours at a time

---

## Queue Organization

### Queue Purposes

- `chatstats-analysis` - LLM calls (gevent, high concurrency)
- `chatstats-db` - DB writes (prefork, concurrency=1)
- `chatstats-embeddings` - Embedding generation (gevent)
- `chatstats-embeddings-index` - FAISS index rebuild (prefork)
- `chatstats-bulk` - Bulk operation orchestration
- `chatstats-display` - Display generation
- `chatstats-events` - Conversation sealing events
- `chatstats-commitments` - Commitment extraction (paused)
- `chatstats-commitments-sequential` - Sequential commitment processing (paused)
- `chatstats-dlq` - Failed task retry queue

### Queue Routing Strategy

**Analysis queue** (gevent workers, ~250 concurrency each):
- `call_llm_task` - Network-heavy LLM calls
- Most conversation analysis operations

**DB queue** (prefork worker, concurrency=1):
- `persist_result_task` - Database writes with bulk flusher
- `historic_status_upsert`, `historic_status_finalize` - Status updates
- Avoids SQLite multi-writer lock contention

**Why separate?** Network I/O (LLM calls) benefits from high gevent concurrency. DB writes need single-writer to avoid locks.

---

## Conversation Analysis System

### Analysis Pass Architecture

**CRITICAL: There is ONLY ONE way to trigger analysis:**

```python
from backend.celery_service.analysis_passes import trigger_analysis_pass

# This is the ONLY correct way
trigger_analysis_pass(conversation_id, chat_id, pass_name)
```

**What it does:**
- Creates `ConversationAnalysis` record
- Resolves prompt template ID
- Handles duplicate/retry logic
- Queues Celery task
- Returns `task_id`

**Never:**
- ❌ Call `analyze_conversation_task.delay()` directly
- ❌ Create CA records manually
- ❌ Call workflow services from routers

### Analysis Pass Types

Analysis passes are defined in `analysis_passes.py`:

**Live Passes** (`pass_type="live"`):
- Trigger on every new message ingestion
- Re-run even if previous analysis succeeded
- **Currently enabled:** `basic_live` (ConvoAll prompt)
- **Currently disabled:** `commitments_live` (half-baked, will resume later)

**Batch Passes** (`pass_type="batch"`):
- Trigger when conversation is sealed (90-min gap)
- Only run if not already completed
- **Currently enabled:** `basic` (ConvoAll prompt)

**How it works:**

```python
ANALYSIS_PASSES = {
    "basic_live": {
        "prompt_name": "ConvoAll",
        "prompt_version": 1,
        "prompt_category": "conversation_analysis",
        "pass_type": "live",
        "priority": 2,
        "enabled": True
    },
    "basic": {
        "prompt_name": "ConvoAll",
        "prompt_version": 1,
        "prompt_category": "conversation_analysis",
        "pass_type": "batch",
        "priority": 1,
        "enabled": True
    }
}
```

### Conversation Analysis Pipeline

**Complete flow from message → analysis:**

```
1. New messages arrive
   ↓
2. ETL creates/updates conversation records
   ↓
3. live_analysis_task triggered
   ↓
4. trigger_analysis_pass (live passes: commitments_live, basic_live)
   ↓
5. [90-minute gap passes]
   ↓
6. check_and_seal_conversations (periodic task)
   ↓
7. handle_sealed_conversation
   ↓
8. ETL runs again (conversation_ready_for_analysis event)
   ↓
9. handle_conversation_ready
   ↓
10. trigger_analysis_pass (batch passes: basic)
```

**Key details:**
- Live passes run immediately on new messages
- Batch passes run after conversation is sealed (90-min gap)
- ALL chats get analysis (no subscription check)
- Sealing is triggered by `conversation_tracker` (in ETL layer)

### Two-Stage CA Tasks

Conversation analysis is split into two tasks for performance:

**Stage 1: LLM Call** (`call_llm_task`)
- Queue: `chatstats-analysis` (gevent, high concurrency)
- Encodes conversation
- Calls LLM
- Returns raw payload

**Stage 2: Persist** (`persist_result_task`)
- Queue: `chatstats-db` (prefork, concurrency=1)
- Parses LLM response
- Writes to database via bulk flusher
- Updates counters
- Chains embedding tasks

**Why split?**
- Network I/O benefits from high concurrency
- DB writes need single-writer to avoid locks
- Allows different retry strategies

**Chain pattern:**

```python
from celery import chain

analysis_chain = chain(
    call_llm_task.si(convo_id, chat_id, ca_row_id, **kwargs),
    persist_result_task.s()  # .s() receives output from previous task
)
```

---

## Bulk Operations

### Pattern: Seed Counters BEFORE Submit

**CRITICAL: Counter seeding must happen BEFORE task submission:**

```python
# 1. Build workflow (Celery group)
workflow = BulkAnalysisWorkflowService.create_global_analysis_workflow(...)

# 2. Seed Redis counters BEFORE submission
ca_ids = getattr(workflow, "ca_ids", None)
seed_with_items(run_id, list(ca_ids))

# 3. Publish seeded event (UI responsiveness)
EventBus.publish("global", "run_seeded", {...})

# 4. Submit workflow
result = workflow.apply_async(task_id=f"global-{run_id}")
```

**Why?** UI needs immediate feedback. If you submit tasks first, UI shows 0% until first task reports in.

### Progress Tracking: Buffered Counters

**Standard pattern for all bulk operations:**

```python
from backend.services.analysis.counter_buffer import (
    buffered_mark_started,
    buffered_mark_finished,
    force_flush_all
)

# In task:
run_id = kwargs.get("run_id")
ca_row_id = kwargs.get("ca_row_id")

# Mark started
counts = buffered_mark_started(run_id, ca_row_id)
EventBus.publish("global", "analysis_started", {...})

# Mark finished
counts = buffered_mark_finished(run_id, ca_row_id, ok=True)

# Check if complete
if processed >= total:
    force_flush_all()  # Write all buffered counters
    EventBus.publish("global", "run_complete", {...})
    
    # Finalize DB status via DB queue
    historic_status_finalize.delay(run_id, success, failed)
```

**Why buffered?** Batches counter updates to Redis to avoid stampeding the metrics instance during bulk operations.

### Global Analysis Types

**Global Analysis** - All unanalyzed conversations across all chats

```python
start_global_analysis(...)
```

**Ranked Analysis** - Specific set of chats under one run_id

```python
start_ranked_analysis(chat_ids=[...], ...)
```

**Bulk Analysis** - All conversations in one chat

```python
start_bulk_analysis(chat_id=..., ...)
```

---

## Embedding Pipeline

### Embedding Chain

Embeddings are chained after persist completes:

```
persist_result_task
    ↓
embed_conversation_task (conversation text)
    ↓
embed_analyses_for_conversation_task (summary, topics, entities, emotions, humor)
```

**Why chain?** Ensures embeddings only run after successful analysis. No separate "backstop" needed.

### Embedding Sources

**Conversation-level:**
- `embed_conversation_task` - Full conversation text (same encoding as analysis)

**Analysis-derived:**
- `embed_analyses_for_conversation_task` - Extracts from normalized tables:
  - Summary
  - Topics
  - Entities
  - Emotions
  - Humor items

**Batch operations:**
- `embed_analysis_batch_task` - Efficient batch embedding for multiple conversations

**FAISS index:**
- `maybe_rebuild_faiss_index_task` - Periodic coalesced rebuild (30s beat schedule)
- Uses dirty flag to avoid unnecessary rebuilds

### Embedding Implementation Notes

**Provider:** Gemini (`gemini-embedding-001`)
- Output dimension: 768
- Task type: RETRIEVAL_DOCUMENT
- L2 normalized vectors

**Legacy fallback:** If new `google.genai` client fails, falls back to `google.generativeai` with per-item concurrency

**Storage:** Vectors stored as float32 little-endian blobs in `embeddings` table

---

## Event Publishing for UI Updates

### EventBus Pattern

Tasks publish events for real-time UI updates:

```python
from backend.services.core.event_bus import EventBus

# Publish to specific scope
EventBus.publish(
    scope=f"chat:{chat_id}",
    event_type="analysis_completed",
    data={"conversation_id": conv_id, "status": "success"}
)

# Publish to global scope (for bulk operations)
EventBus.publish(
    scope="global",
    event_type="run_complete",
    data={"run_id": run_id, "total": total, ...}
)
```

**How it works:**
- Events written to Redis Streams
- Frontend SSE endpoints subscribe to streams (filtered by scope)
- UI updates without polling

**Common scopes:**
- `chat:{chat_id}` - Chat-specific updates
- `task:{task_id}` - Task-specific progress
- `global` - Global analysis runs
- `historic` - Historic analysis status

---

## Commitment System (PAUSED)

The commitment tracking system is currently paused but will be reactivated later. Here's how it's organized:

### Three-Part System

**1. Individual Conversation Analysis** (`tasks/commitment_analysis.py`)
- Task: `process_commitment_analysis_task`
- Runs on a single conversation
- Two-stage LLM pipeline:
  - Stage 1: Extract commitments
  - Stage 2: Reconcile against existing commitments

**2. Historical Batch Processing** (`tasks/commitment_history.py`)
- Task: `analyze_historical_commitments_task`
- Queues all past conversations in chronological order
- Uses sequential queue (`chatstats-commitments-sequential`, concurrency=1)
- Builds analysis chain for durability

**3. Live Processing** (`analysis_passes.py`)
- Pass: `commitments_live`
- Triggers on every new message
- Picks up where historical left off

**Status:** All three components need work before reactivation. Code is preserved as-is for future use.

**Related:** See `routers/commitments/two_stage_processing.md` for detailed commitment system architecture.

---

## Periodic Tasks (Beat Schedule)

```python
beat_schedule = {
    'check-sealed-conversations': {
        'task': 'celery.check_and_seal_conversations',
        'schedule': 60.0,  # Every minute
    },
    'coalesced-faiss-rebuild': {
        'task': 'celery.embeddings.maybe_rebuild_faiss_index',
        'schedule': 30.0,  # Every 30 seconds (coalesced via dirty flag)
    },
    'process-dlq-items': {
        'task': 'celery.process_dlq_items',
        'schedule': 900.0,  # Every 15 minutes
    },
}
```

**Conversation sealing:** Checks for conversations with 90+ minute gaps, seals them, triggers ETL + batch analysis  
**FAISS rebuild:** Checks dirty flag, rebuilds if needed (avoids redundant rebuilds)  
**DLQ processing:** Retries failed tasks with exponential backoff

---

## Special Task Patterns

### Conversation Sealing Pipeline

**Automatic sealing:**

```
check_and_seal_conversations (beat task, 60s)
    ↓
handle_sealed_conversation
    ↓ (runs ETL)
conversation_ready_for_analysis event
    ↓
handle_conversation_ready
    ↓
trigger_analysis_pass (batch passes)
```

**Manual sealing:**

```python
force_seal_chat.delay(chat_id)
```

### Live Message Processing

**Flow:**

```
New messages arrive via live sync
    ↓
handle_new_messages_synced_task
    ↓
For each recent conversation:
    trigger_analysis_pass (live passes)
```

**Reliability:** Replaces unreliable Redis pub/sub with durable Celery tasks.

### Document Display Generation

**Task:** `generate_document_display_task`

**Flow:**
1. Load document snapshot (versioned)
2. Generate mobile-first JSX prompt
3. Call Claude Sonnet
4. Clean code (remove markdown fences)
5. Persist display
6. Publish event for UI update

**Auto-generation:** Documents with `display:auto` tag or kind in `["text", "sheet"]` automatically queue display generation.

---

## Ask Eve Task (Reference Implementation)

**Status:** NOT CURRENTLY USED - Kept as reference for future agent system

**Purpose:** Dynamic prompt generation from natural language questions

**Pattern:**
1. User asks question
2. Load example prompts
3. LLM generates new prompt from question + examples
4. Create prompt template
5. Generate report using dynamic prompt

**File:** `tasks/ask_eve.py`

**Why keeping:** Demonstrates how to structure prompts for an agent system. Will be used as reference when building agent layer.

---

## Common Pitfalls

### 1. Don't Call Tasks Directly

❌ **WRONG:**

```python
# In a router
from backend.celery_service.tasks.analyze_conversation import analyze_conversation_task
analyze_conversation_task.delay(conv_id, chat_id, ca_id)
```

✅ **CORRECT:**

```python
# In a router
from backend.celery_service.analysis_passes import trigger_analysis_pass
trigger_analysis_pass(conv_id, chat_id, "basic")
```

### 2. Don't Create CA Records Manually

The analysis pass system handles this. If you create records manually, you'll miss:
- Duplicate detection
- Prompt resolution
- Status initialization
- Task routing

### 3. Don't Forget to Seed Counters

For bulk operations, always seed counters BEFORE submitting tasks:

```python
# ❌ WRONG - UI shows 0% until first task reports
workflow.apply_async()
seed_counters(run_id, total)

# ✅ CORRECT - UI shows progress immediately
seed_counters(run_id, total)
workflow.apply_async()
```

### 4. Don't Use Direct Redis Counter Access

Use buffered counters instead:

```python
# ❌ LEGACY
from backend.services.analysis.redis_counters import increment
increment(run_id, "success")

# ✅ CORRECT
from backend.services.analysis.counter_buffer import buffered_mark_finished
buffered_mark_finished(run_id, ca_row_id, ok=True)
```

### 5. Don't Skip Error Handling in Tasks

Always use try/except with `retry_with_backoff`:

```python
@shared_task(bind=True, base=BaseTaskWithDLQ)
def my_task(self, ...):
    try:
        result = do_work()
        return result
    except Exception as exc:
        logger.error("Task failed: %s", exc, exc_info=True)
        self.retry_with_backoff(exc)  # DON'T FORGET THIS
```

---

## Performance Notes

See `perf_notes.md` for detailed tuning history. Key takeaways:

**Throughput formula:**
```
TPS ≈ inflight / p95_latency
```

**Inflight calculation:**
```
inflight = num_worker_procs × concurrency_per_proc
```

**Current optimizations:**
- Dynamic `max_tokens` based on conversation size
- Bulk DB commit batching
- Provider-scoped hold caps (0.5-1.0s)
- Lane semaphores to prevent stream resets

**Never modify without:**
- Reading `perf_notes.md`
- Having a performance testing strategy
- Baseline metrics to compare against

---

## Adding New Tasks

### Checklist

1. **Create task file in `tasks/`:**

```python
from celery import shared_task
from backend.celery_service.tasks.base import BaseTaskWithDLQ
from backend.services.my_module import MyWorkflowService
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, base=BaseTaskWithDLQ, name="celery.my_task")
def my_task(self, param1, param2, **kwargs):
    logger.info("Task started with param1=%s", param1)
    
    try:
        with self.step("processing", 50):
            result = MyWorkflowService.run(
                param1=param1,
                param2=param2,
                **kwargs
            )
        return result
    except Exception as exc:
        logger.error("Task failed: %s", exc, exc_info=True)
        self.retry_with_backoff(exc)
```

2. **Add route to `config.py`:**

```python
task_routes = {
    'celery.my_task': {'queue': 'chatstats-analysis'},
}
```

3. **Import in `tasks/__init__.py`:**

```python
from . import my_task_module
```

4. **Create workflow service in `backend/services/`:**

```python
class MyWorkflowService:
    @staticmethod
    def run(param1, param2, **kwargs):
        # ALL business logic here
        # NO Celery dependencies
        pass
```

5. **Test workflow without Celery:**

```python
# This should work without Celery running
result = MyWorkflowService.run(param1, param2)
```

6. **Update this documentation**

---

## Related Documentation

- **[Main Backend Guide](../agents.md)** - Overall backend architecture
- **[Backend Services](../services/agents.md)** - Workflow service patterns
- **[Backend Repositories](../repositories/agents.md)** - Data access layer
- **[Electron Layer](../../electron/agents.md)** - How Celery is spawned and managed
- **Performance Notes** (`perf_notes.md`) - Detailed tuning history

