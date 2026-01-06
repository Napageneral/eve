# ChatStats Services Layer Architecture

## Overview

The services layer contains all business logic for ChatStats. Services sit between routers (API layer) and repositories (data layer), orchestrating workflows, calling external systems (LLM providers), and publishing events.

**Core Principle**: Services are the ONLY layer that can call repositories. Routers always call services, never repositories directly.

---

## Service Organization

```
services/
├── core/                   # Shared utilities used by all services
│   ├── token.py           # Token counting and cost calculation
│   ├── event_bus.py       # Redis Streams event publishing
│   ├── utils.py           # BaseService, decorators, helpers
│   ├── workflow_base.py   # Base class for workflow services
│   ├── constants.py       # Shared constants (models, configs)
│   └── chat_message_hub.py # In-process pub-sub for live messages
│
├── llm/                    # LLM integration (consolidated)
│   ├── completions.py      # LiteLLM-based completion service
│   ├── llm.py              # LLMService, LLMConfigResolver (compatibility wrappers)
│   ├── config.py           # LiteLLM configuration
│   ├── models.py           # Model info/validation
│   ├── model_constants.py  # Model constants
│   ├── prompt.py           # Prompt class
│   ├── budget.py           # Budget tracking
│   └── providers/          # Specialized provider features
│       ├── openai/         # Embeddings, images
│       ├── anthropic/      # Batches, prompt caching
│       ├── google/         # Multimodal, token counting
│       └── openrouter/     # OpenRouter features
│
├── context/                # Context retrieval system → [agents.md](./context/agents.md)
│   ├── context.py          # Context definition/selection management
│   ├── retrieval/          # All retrieval functions (consolidated)
│   │   ├── index.py        # RETRIEVAL_FUNCTIONS registry
│   │   ├── convos_context.py    # Modern conversation retrieval
│   │   ├── analyses_context.py  # Modern analysis retrieval
│   │   ├── artifacts_context.py # Chatbot document retrieval
│   │   ├── raw_conversation.py  # Single conversation
│   │   ├── chat_text.py         # Full chat text
│   │   └── utils.py             # Utility functions
│   └── agents.md           # Context system documentation
│
├── conversations/         # Conversation analysis workflows
│   ├── analysis.py        # Core analysis service
│   ├── analysis_workflow.py      # Single conversation workflow
│   ├── bulk_workflow.py          # Bulk/global analysis orchestration
│   ├── wrapped_analysis.py       # Wrapped data aggregation
│   ├── backfill.py               # Historical backfill operations
│   └── db_bulk_flusher.py        # Batch DB writes (worker-only)
│
├── encoding/              # Conversation text encoding for LLMs
│   ├── conversation_encoding.py  # Generic encoding
│   ├── commitment_encoding.py    # With commitment context (paused feature)
│   └── encoding.py               # Compatibility shim
│
├── analysis/              # Progress tracking systems
│   ├── redis_counters.py  # Direct Redis counter manipulation
│   └── counter_buffer.py  # Buffered batch counter updates
│
├── embeddings/            # FAISS vector search
│   └── faiss_index.py     # Build, load, query embedding index
│
├── chatbot/               # AI chatbot document operations
│   └── document_display.py # Generate JSX displays for documents
│
├── infra/                 # Infrastructure services
│   ├── dlq.py             # Dead letter queue management
│   └── queue_monitoring.py # Celery queue stats
│
├── metrics/               # Performance metrics
│   ├── runtime_metrics.py    # Production metrics (always on)
│   └── bottleneck_metrics.py # Performance testing only
│
├── reports/               # Report support services (mostly deprecated)
│   ├── persist.py         # Event publishing (still used by chatbot)
│   └── prompt.py          # Prompt compilation (still used by contexts)
│
├── commitments/           # Commitment tracking (PAUSED FEATURE)
│   ├── commitments.py              # Two-stage LLM pipeline
│   └── commitment_history_workflow.py # Historical analysis
│
├── ask_eve/               # Dynamic prompt generation (NOT USED)
│   └── ask_eve.py         # Example for future agent system
│
└── publish/               # Report publishing
    └── publish.py         # Publish reports with preview
```

---

## Core Service Patterns

### Workflow Services

Workflow services orchestrate multi-step operations:

```python
from backend.services.core.workflow_base import WorkflowBase
import logging

logger = logging.getLogger(__name__)

class MyWorkflowService(WorkflowBase):
    @staticmethod
    def run(param1, param2, **kwargs):
        logger.info("Starting workflow with param1=%s", param1)
        
        # 1. Load data
        data = MyRepository.get_data(param1)
        
        # 2. Transform
        encoded = encode_data(data)
        
        # 3. Call external system (LLM, etc.)
        result = LLMService.call_llm(encoded)
        
        # 4. Persist results
        MyRepository.save_result(result)
        
        # 5. Publish events
        EventBus.publish(scope=f"task:{param1}", event_type="completed", data=result)
        
        return {"success": True, "result_id": result.id}
```

**Examples:**
- `ConversationAnalysisWorkflow.run()` - Single conversation analysis
- `BulkAnalysisWorkflowService.prepare_global_analysis()` - Prepare bulk tasks
- `DocumentDisplayWorkflowService.generate()` - Generate document display

**Pattern:**
- Workflows are PURE (no Celery dependencies)
- Celery tasks are thin wrappers that call workflows
- This allows testing workflows without Celery

### Service Base Classes

All services should extend `BaseService`:

```python
from backend.services.core.utils import BaseService
import logging

logger = logging.getLogger(__name__)

class MyService(BaseService):
    # Inherits: self.logger, self._log_operation(), self._log_error()
    
    @staticmethod
    def my_operation(arg1, arg2):
        logger.info("Performing operation with arg1=%s", arg1)
        # Service methods are typically @staticmethod
        pass
```

`BaseService` provides:
- Standardized logging via `self.logger`
- `self._log_operation()` helper
- `self._log_error()` helper
- `self.publish_event()` helper (delegates to EventBus)

### Decorators for Common Patterns

```python
from backend.services.core.utils import timed, with_session
import logging

logger = logging.getLogger(__name__)

@timed("my_operation")  # Auto-logs duration
@with_session(commit=True)  # Auto-manages session
def my_operation(arg1, arg2, session=None):
    logger.info("Operation called with arg1=%s", arg1)
    # Session injected, committed on success, rolled back on error
    pass
```

---

## Domain-Specific Services

### Conversation Analysis Services

**Core workflow:**

1. Load/encode conversation
2. Load prompt template
3. Resolve LLM config
4. Call LLM
5. Parse results
6. Persist analysis
7. Publish events

**Key services:**

- `ConversationAnalysisService` - Core CRUD operations
- `ConversationAnalysisWorkflow` - Orchestrates single analysis
- `BulkAnalysisWorkflowService` - Prepares bulk/global analysis tasks

**Progress tracking:**

Two systems exist:
- `redis_counters.py` - Direct Redis manipulation (Lua scripts)
- `counter_buffer.py` - Buffered batch updates

**⚠️ OPEN QUESTION:** When to use direct vs buffered counters? Review during Celery phase.

### Encoding Services

**Purpose:** Convert conversation data into LLM-ready text format.

**Two systems:**

- `ConversationEncodingService.encode_text()` - Generic encoding
- `CommitmentEncodingService.encode_conversation_for_commitments()` - Adds commitment context

**Why two systems?** Commitment analysis needs additional context (active commitments, recent updates, chat metadata). Generic encoding is simpler and faster.

**Note:** Commitments feature is PAUSED but will resume later, so keep both systems.

### Context Services

**Purpose:** Resolve context definitions into actual data for prompt placeholders.

**Pattern:**

1. Create `ContextDefinition` (links name to retrieval function)
2. Create `ContextSelection` (instance with parameters)
3. Resolve selection (calls retrieval function, caches result)
4. Use in prompt (replace placeholder with cached content)

**Example:**

```python
# Create selection
cs_id = ContextRepository.create_context_selection(
    session, 
    context_definition_id,
    {"chat_id": 123, "start_date": "2024-01-01"}
)

# Resolve (calls retrieval function, caches result)
ReportPromptService.resolve_context_selections({"context": cs_id})

# Use in prompt
prompt = "Analyze this: {{{context}}}"
final_prompt = prompt.replace("{{{context}}}", resolved_content)
```

**⚠️ OPEN QUESTION:** Context retrieval functions are scattered. Full audit needed to document all retrieval patterns.

### LLM Services

**`LLMService` (`core/llm.py`) - Centralized LLM client**

**Features:**
- Automatic fallback to backup models on errors
- Retry logic for transient failures
- Token counting and cost tracking
- Response schema validation
- Structured response parsing

**Config resolution hierarchy:**

1. Base config (hardcoded service defaults)
2. Prompt template config (stored with template)
3. User override (from API request)

`LLMConfigResolver` handles merging these layers.

**Usage:**

```python
from backend.services.core.llm import LLMService, LLMConfigResolver
from backend.services.core.constants import TaskDefaults

# Resolve config
llm_config = LLMConfigResolver.resolve_config(
    base_config={
        "model_name": TaskDefaults.CA_MODEL,
        "temperature": TaskDefaults.CA_TEMPERATURE,
        "max_tokens": TaskDefaults.CA_MAX_TOKENS,
    },
    prompt_config=prompt_template.get("default_llm_config"),
    user_override=user_provided_overrides,
)

# Make the call
response = LLMService.call_llm(
    prompt_str=final_prompt,
    llm_config_dict=llm_config,
    response_schema_dict=prompt_template.get("response_schema"),
)

# Extract content
content = response.get("content")
cost = response.get("usage", {}).get("total_cost", 0.0)
```

**Important: Pydantic Config Handling**

When loading prompt templates, the `default_llm_config` may be a Pydantic object with None defaults:

```python
# In workflow services, convert Pydantic to dict excluding None values
prompt_llm_config = prompt_dict.get("default_llm_config")
if prompt_llm_config and hasattr(prompt_llm_config, 'dict'):
    prompt_llm_config = prompt_llm_config.dict(exclude_none=True)

llm_config = LLMConfigResolver.resolve_config(
    base_config={...},
    prompt_config=prompt_llm_config,  # Now clean dict without None values
    user_override={...}
)
```

This prevents Pydantic's `None` defaults from overriding your base configuration.

**LLM Response Parsing:**

After calling `LLMService.call_llm()`, extract content directly:

```python
response = LLMService.call_llm(...)
content = response.get("content", {})

# Content can be dict (structured), str (needs parsing), or list
if isinstance(content, str):
    content = json.loads(content)
elif isinstance(content, list):
    # LLM returned array directly
    pass
```

### Display Generation Services

**`DocumentDisplayService` (`chatbot/document_display.py`) - Generate JSX displays for chatbot documents**

**Pattern:**

1. Load document snapshot (versioned)
2. Generate prompt (mobile-first, edge-to-edge)
3. Call LLM (Claude Sonnet)
4. Clean code (remove markdown fences)
5. Persist display
6. Publish event (SSE for UI updates)

**Note:** This is the ONLY display generation service now. Old `reports/display.py` has been removed.

### Event Publishing

**EventBus** (`core/event_bus.py`) - Publish events to Redis Streams for SSE

**Usage:**

```python
from backend.services.core.event_bus import EventBus

# Publish to specific scope (chat_id, run_id, task_id, etc.)
EventBus.publish(
    scope=f"chat:{chat_id}",
    event_type="analysis_completed",
    data={"conversation_id": conv_id, "status": "success"}
)

# Publish to global scope
EventBus.publish(
    scope="global",
    event_type="analysis_started",
    data={"run_id": run_id, "total": total_count}
)
```

**How it works:**
- EventBus writes to Redis Streams
- Frontend SSE endpoints subscribe to streams (filtered by scope)
- UI updates in real-time without polling

**Service Event Wrappers:**

Some services wrap EventBus for domain-specific events:
- `ReportEventsService.publish_report_event()` - Report lifecycle events
- (Add more as patterns emerge)

**⚠️ OPEN QUESTION:** Should services call `EventBus.publish()` directly or use service wrappers? Review during Celery phase.

### Metrics Services

**Two systems (both kept):**

**1. Runtime Metrics** (`metrics/runtime_metrics.py`) - Production metrics (always on)
- Conversation analysis task metrics
- LLM call metrics (latency, tokens, cost)
- Stage timing (encode, LLM, persist)
- Queue depths

**2. Bottleneck Metrics** (`metrics/bottleneck_metrics.py`) - Performance testing only
- Detailed QPS bottleneck analysis
- Per-stage timing distribution (p50, p95, p99)
- Used during performance tuning sessions

**Why two systems?** Runtime metrics are lightweight and always on. Bottleneck metrics add overhead and are only enabled during perf testing.

---

## Legacy / Deprecated Services

### Catalog Service (DEPRECATED)

**Status:** No frontend calls, replaced by newer context system

**Purpose:** Legacy context retrieval for wrapped analysis

**What it does:**
- Retrieves various chat analysis data types
- Formats data for LLM consumption
- Returns structured catalog items

**⚠️ ACTION:** Document what context it retrieves and format for consolidation with other context retrieval systems.

### Ask Eve Service (NOT USED)

**Status:** Not currently used, kept as example

**Purpose:** Dynamic prompt generation from natural language questions

**How it works:**
1. User asks a question
2. Load example prompt templates
3. LLM generates prompt from question + examples
4. Create new prompt template
5. Generate report using dynamic prompt

**Why keeping it:** Example for future agent system prompt structuring.

### Commitments Service (PAUSED FEATURE)

**Status:** Feature paused, will resume later

**Purpose:** Two-stage LLM pipeline for commitment tracking
1. Extract commitments from conversation
2. Reconcile against existing commitments

**Keep because:** Will resume development later, proven architecture worth preserving.

**Related documentation:** `routers/commitments/two_stage_processing.md`

### Reports Services (MOSTLY DEPRECATED)

**Status:** Most report functionality moved to chatbot tools

**Remaining files:**
- `reports/persist.py` (`ReportEventsService`) - Still used by chatbot for event publishing
- `reports/prompt.py` (`ReportPromptService`) - Still used by contexts router

**Deleted (no longer used):**
- ~~`reports/display.py`~~ - Use `chatbot/document_display.py` instead
- ~~`reports/workflow.py`~~ - Use analysis workflows instead
- ~~`reports/llm.py`~~ - Use `core/llm.py` instead
- ~~`reports/cost.py`~~ - Not imported anywhere

**⚠️ WARNING:** Some Celery tasks may have broken imports after cleanup:
- `tasks/generate_report.py`
- `tasks/generate_display.py`
- `tasks/generate_report_with_display.py`

**⚠️ OPEN QUESTION:** Are report generation tasks still used? Review during frontend/Celery phases. May be able to delete entire `reports/` folder + related tasks.

---

## Common Service Patterns

### BaseService Pattern

All services should extend `BaseService`:

```python
from backend.services.core.utils import BaseService
import logging

logger = logging.getLogger(__name__)

class MyService(BaseService):
    # Inherits helper methods
    
    @staticmethod
    def my_operation(arg1, arg2):
        logger.info("Performing operation with arg1=%s", arg1)
        # Service methods are typically @staticmethod
        pass
```

### Decorator Usage

```python
from backend.services.core.utils import timed, with_session
import logging

logger = logging.getLogger(__name__)

@timed("my_operation")  # Auto-logs duration
@with_session(commit=True)  # Auto-manages session
def my_operation(arg1, arg2, session=None):
    logger.info("Operation called")
    # Session injected, committed on success, rolled back on error
    pass
```

### Error Handling

Services should raise meaningful exceptions:

```python
# In service methods
if not result:
    raise ValueError(f"Entity {entity_id} not found")

# Routers catch and convert to HTTP errors
try:
    result = MyService.do_thing(id)
except ValueError as e:
    raise HTTPException(status_code=404, detail=str(e))
```

### Workflow Base Pattern

For multi-step orchestrations, extend `WorkflowBase`:

```python
from backend.services.core.workflow_base import WorkflowBase
import logging

logger = logging.getLogger(__name__)

class MyWorkflowService(WorkflowBase):
    @staticmethod
    def run(param1, param2, **kwargs):
        logger.info("Starting workflow")
        
        # 1. Load data
        # 2. Transform
        # 3. Call external system
        # 4. Persist results
        # 5. Publish events
        
        return {"success": True}
```

**Benefits:**
- Pure Python (no Celery dependencies in workflow)
- Easy to test
- Celery tasks are thin wrappers that call `.run()`

---

## Resolved Questions

### ✅ Progress Tracking Pattern (Verified 2025-10-06)
**Finding:** Both systems actively used in different contexts

**Pattern:**
- `analysis/redis_counters.py` (direct) → Use in **API routers** for real-time reads (10 imports)
- `analysis/counter_buffer.py` (buffered) → Use in **Celery tasks** for batched writes (6 imports)

**Rationale:**
- Direct: Low latency for API endpoints that need immediate counts
- Buffered: Better performance for high-volume Celery tasks (reduces Redis round-trips)

**Decision:** Keep both, use appropriately based on context

### ✅ Event Publishing Pattern (Verified 2025-10-06)
**Finding:** EventBus.publish() called directly (12 times), wrappers rarely used (1 time)

**Pattern:** Always use `EventBus.publish()` directly
```python
from backend.services.core.events import EventBus

EventBus.publish('analysis.started', {'run_id': run_id, 'chat_id': chat_id})
```

**Rationale:**
- Wrappers add no value (just thin pass-throughs)
- Direct calls are clearer and more maintainable
- Documented in `EVENT_SYSTEMS.md`

**Decision:** Use EventBus.publish() directly, wrappers are legacy

## Open Questions & TODOs

### Backend-Wide Open Questions (Tracked in Main agents.md)

For backend-wide open questions that affect services, routers, and repositories, see the main backend agents.md:
- Thread Naming Consistency (accepted as-is per ADR-003)
- Documentation Accuracy
- Analysis Router Endpoints - Which Are Active?

---

## Adding New Services

### Checklist

1. **Import core utilities:**

```python
from backend.services.core.utils import BaseService, timed, with_session
import logging

logger = logging.getLogger(__name__)
```

2. **Extend BaseService:**

```python
class MyService(BaseService):
    pass
```

3. **Use standard patterns:**
   - Raw SQL with `db.sql` helpers
   - `db.session_scope()` for transactions
   - `@timed()` for important operations
   - `EventBus.publish()` for UI updates
   - `logging.getLogger(__name__)` for logging

4. **Document in this file:**
   - Add to service organization tree
   - Document key patterns if novel
   - Update open questions if relevant

5. **Follow three-layer pattern:**
   - Services call repositories (never routers)
   - Routers call services (never repositories directly)

---

## Related Documentation

- **[Main Backend Guide](../agents.md)** - Overall backend architecture  
- **[Backend Routers Guide](../routers/agents.md)** - How routers call services
- **[Electron Layer Guide](../../electron/agents.md)** - How logging flows to Electron
