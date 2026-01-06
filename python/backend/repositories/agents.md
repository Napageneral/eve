# Backend Repositories Layer Architecture

## Overview

The repositories layer provides **raw SQL data access** for all database operations. This layer strictly uses SQLAlchemy's raw SQL execution - no ORM query building.

**Core Principle:** Repositories are the ONLY layer that touches the database. Services call repositories, never touch the DB directly.

---

## Critical Patterns

### 1. Raw SQL Only

**NEVER use ORM queries. ALWAYS use raw SQL with `db.sql` helpers:**

```python
from backend.db.sql import fetch_one, fetch_all, execute_write

# Single row
row = fetch_one(session, "SELECT * FROM chats WHERE id = :id", {"id": chat_id})

# Multiple rows  
rows = fetch_all(session, "SELECT * FROM messages WHERE chat_id = :chat_id", {"chat_id": chat_id})

# Write operation
execute_write(session, "UPDATE chats SET title = :title WHERE id = :id", 
              {"title": new_title, "id": chat_id})
```

### 2. Session Management

**Sessions are ALWAYS passed in, never created within repositories:**

```python
@classmethod
def get_by_id(cls, session: Session, record_id: int) -> Optional[Dict[str, Any]]:
    # session parameter is required
    # Never call db.session_scope() inside a repository
    pass
```

### 3. Return Dictionaries, Not ORM Objects

```python
# ✅ CORRECT - Returns dict
def get_user(cls, session, user_id) -> Optional[Dict[str, Any]]:
    return fetch_one(session, "SELECT * FROM users WHERE id = :id", {"id": user_id})

# ❌ WRONG - Returns ORM object
def get_user(cls, session, user_id) -> Optional[User]:
    return session.query(User).filter_by(id=user_id).first()
```

---

## Repository Organization

### Core Infrastructure (`core/`)

- `base.py` - `BaseRepository` with raw SQL helpers
- `generic.py` - `GenericRepository` for CRUD boilerplate elimination  
- `mixins.py` - `JSONFieldMixin`, `TimestampMixin`, `LoggingMixin`
- `status_mixin.py` - `StatusMixin` for status+retry updates
- `exceptions.py` - Custom exceptions

### Analysis Items (`analysis_items/`)

Consolidated pattern eliminates duplication across emotions, entities, humor, topics:
- `analysis_items_base.py` - Base class with shared methods
- `bulk_insert.py` - Bulk insert helper
- `emotions.py`, `entities.py`, `humor.py`, `topics.py` - Minimal subclasses

---

## Repository Catalog (Quick Reference)

### iMessage Domain

**`ChatRepository`** (`chats.py`)
- Get chats with filters (blocked, subscribed, analysis status)
- Chat statistics and metadata
- Subscription management
- Block/unblock operations
- Related: Handles `chat_participants` FK table

**`ContactRepository`** (`contacts.py`)
- CRUD for contacts
- Contact identifiers (phone, email)
- Name mapping for chats
- Get "me" contact
- Contact statistics

**`ConversationRepository`** (`conversations.py`)
- Load conversations with messages
- Filter by chat, date range, analysis status
- Conversation statistics
- Backfill operations
- Temporal queries (before/after conversation)

**`MessageRepository`** (`messages.py`)
- Load messages for chat/conversation
- Message statistics
- Related: Handles `attachments` and `reactions` FK tables

### Analysis Domain

**`ConversationAnalysisRepository`** (`conversation_analysis.py`)
- CRUD for conversation analyses
- Get analysis by conversation/chat
- Chat analysis summaries
- Status updates with retry tracking

**`AnalysisRepository`** (`analysis.py`)
- Consolidated analysis operations
- Cross-table analysis queries
- Analysis aggregations

**`AnalysisResultsRepository`** (`analysis_results.py`)
- Orchestrates multiple analysis types
- Load full analysis (emotions + entities + topics + humor)
- Contact name mapping (delegates to ContactRepository)
- Analysis formatting for LLM context

**`EmotionsRepository`** (`emotions.py`)
- Get emotions by chat/conversation
- Bulk insert emotions from analysis
- Inherits from `AnalysisItemRepository`

**`EntitiesRepository`** (`entities.py`)
- Get entities (people, places, things) by chat/conversation
- Bulk insert entities from analysis
- Inherits from `AnalysisItemRepository`

**`HumorRepository`** (`humor.py`)
- Get humor items by chat/conversation
- Bulk insert humor from analysis
- Inherits from `AnalysisItemRepository`

**`TopicsRepository`** (`topics.py`)
- Get topics by chat/conversation
- Bulk insert topics from analysis
- Inherits from `AnalysisItemRepository`

**`RawCompletionRepository`** (`raw_completions.py`)
- Store raw LLM completions for auditing
- Get completions by source (conversation, report, etc.)

### Chatbot Domain

**`DocumentDisplayRepository`** (`document_displays.py`)
- CRUD for chatbot document displays (JSX UI)
- Get display by document ID
- Display versioning

**`DocumentReadsRepository`** (`document_reads.py`)
- Track document read status
- Uses `chatbot_document_reads_simple` table
- Mark documents as read

**`ThreadContextRepository`** (`thread_contexts.py`) ⭐ NEW
- Get contexts for chatbot threads
- Add/manage thread-specific context
- **TODO:** Migrate direct DB access from routers/services

**`EmbeddingsRepository`** (`embeddings.py`) ⭐ NEW
- Semantic search embeddings
- Get embeddings by source (conversation, document)
- Upsert embeddings
- **TODO:** Migrate direct DB access from services

**`SuggestionsHistoryRepository`** (`suggestions_history.py`) ⭐ NEW
- Smart Cues suggestion tracking
- Get suggestion history for chat
- Record and mark suggestions as used
- **TODO:** Migrate direct DB access from routers

### Context & Prompts

**`ContextRepository`** (`contexts.py`)
- CRUD for context definitions
- Link definitions to retrieval functions
- Parameter schema validation

**`ContextSelectionRepository`** (`context_selections.py`)
- CRUD for context selections (instances with params)
- Resolve selections (call retrieval functions)
- Cache resolved content

### Commitments Domain

**`CommitmentRepository`** (`commitments.py`)
- CRUD for commitment tracking
- Get active/completed/failed commitments
- Status updates and history
- Commitment statistics

### System & Infrastructure

**`UserRepository`** (`users.py`)
- CRUD for user profiles
- Primary identifier management
- User metadata

**`AppSettingsRepository`** (`app_settings.py`)
- Key-value settings storage
- Get/set app configuration

**`ChatSubscriptionRepository`** (`chat_subscriptions.py`)
- Subscription status for chats
- Activate/deactivate subscriptions
- Check subscription validity

**`DLQRepository`** (`dlq.py`)
- Dead letter queue for failed tasks
- Store, retry, and query failed operations
- Error tracking and diagnostics

**`HistoricAnalysisRepository`** (`historic_analysis.py`)
- Track one-time onboarding analysis status
- Get status by user or run_id
- Upsert and finalize analysis runs
- Used by onboarding flow and live sync gating

---

## Recently Deleted (No Longer Available)

**DO NOT recreate these - they were intentionally removed:**

- ~~`wrapped_analysis.py`~~ - Deprecated wrapped analysis feature
- ~~`stats.py`~~ - Old stats functionality (unused)
- ~~`reports.py`~~ - Reports feature removed
- ~~`published_reports.py`~~ - Report publishing removed
- ~~`displays.py`~~ - Report displays removed
- ~~`prompts.py`~~ - Prompt templates removed (2025-10-27) - all prompts now managed by Eve

---

## Core Patterns

### GenericRepository Pattern

Eliminates CRUD boilerplate for simple tables:

```python
from .core.generic import GenericRepository

class ContactRepository(GenericRepository):
    TABLE = "contacts"
    # Inherits: get_by_id, get_all, create, update, delete, exists
    
    # Add custom methods as needed
    @classmethod
    def get_by_phone(cls, session, phone_number):
        return cls.fetch_one(session,
            "SELECT * FROM contacts WHERE phone_number = :phone",
            {"phone": phone_number})
```

### Analysis Items Pattern

Brilliant consolidation for analysis items (emotions, entities, humor, topics):

```python
from .analysis_items.analysis_items_base import AnalysisItemRepository

class EmotionsRepository(AnalysisItemRepository):
    TABLE = "emotions"
    ITEM_NAME_FIELD = "emotion_type"
    # Inherits get_by_chat, get_by_conversation, delete_by_conversation, etc.
```

This eliminates ~80% of duplicate code across the 4 analysis item types.

### StatusMixin Pattern

Common status update pattern:

```python
from .core.status_mixin import StatusMixin

# Update status with optional error message and retry bump
StatusMixin.set_status(
    session,
    table="conversation_analyses",
    id_column="id",
    id_value=analysis_id,
    new_status="failed",
    extra={"error_message": "Timeout"},
    bump_retry=True
)
```

---

## Best Practices

### 1. Use GenericRepository When Possible

**✅ GOOD:**
```python
class AppSettingsRepository(GenericRepository):
    TABLE = "app_settings"
    # Free CRUD methods!
```

**❌ AVOID:**
```python
class AppSettingsRepository(BaseRepository):
    @classmethod
    def get_by_id(cls, session, id):
        # Manually reimplementing CRUD
```

### 2. Always Use Parameter Binding

**✅ GOOD:**
```python
sql = "SELECT * FROM users WHERE id = :user_id"
fetch_one(session, sql, {"user_id": user_id})
```

**❌ BAD:**
```python
sql = f"SELECT * FROM users WHERE id = {user_id}"  # SQL injection!
```

### 3. No Business Logic in Repos

**✅ GOOD:**
```python
# Repository: Just data access
def get_active_commitments(cls, session, chat_id):
    return fetch_all(session, 
        "SELECT * FROM commitments WHERE chat_id = :chat_id AND status = 'active'",
        {"chat_id": chat_id})
```

**❌ BAD:**
```python
# Repository: Has business logic
def get_commitments_needing_reminders(cls, session, chat_id):
    commitments = fetch_all(session, ...)
    # ❌ Filtering logic belongs in service layer
    return [c for c in commitments if c['due_date'] < tomorrow]
```

### 4. Return Meaningful Data Structures

When fetching related data, build complete structures:

```python
@classmethod
def load_conversation_with_messages(cls, session, conversation_id):
    # Get conversation
    convo = fetch_one(session, "SELECT * FROM conversations WHERE id = :id", 
                      {"id": conversation_id})
    
    # Get messages
    messages = fetch_all(session, 
        "SELECT m.*, c.name as sender_name FROM messages m "
        "LEFT JOIN contacts c ON m.sender_id = c.id "
        "WHERE m.conversation_id = :cid ORDER BY m.timestamp",
        {"cid": conversation_id})
    
    # Return complete structure
    return {
        **convo,
        "messages": messages
    }
```

---

## Resolved Questions

### ✅ Frontend Direct DB Access (Verified 2025-10-06)

**Finding:** Frontend has NO direct database access

**Clarification:**
- Frontend `lib/chatbot/db/queries.ts` is an **HTTP API client**, NOT direct DB
- Makes HTTP calls to FastAPI backend `/api/chatbot/*` endpoints
- Backend API routes use raw SQL (following project preference)
- No `sqlite3` or `redis` imports in frontend source code

**Architecture Decision:**
- Chatbot tables accessed via Next.js API routes → FastAPI backend
- API routes use raw SQL (no repositories needed per user preference)
- **No repositories needed** for chatbot tables - raw SQL in API routes is acceptable

**Status:** Resolved - hybrid architecture works well, no action needed

---

### ✅ Chatbot Naming Inconsistency (Accepted 2025-10-06)

**Current state:**
- Database table: `chatbot_chats` (kept for backward compatibility)
- SQLAlchemy model: `ChatbotThread` (renamed in code)
- UI terminology: "threads"

**Decision:** See ADR-003 (Thread vs Chat Naming Convention)
- Model name reflects UI terminology
- Table name kept for migration compatibility
- Intentional mismatch, no action needed

**Status:** Accepted as-is

---

### ✅ Document Reads Table (Verified 2025-10-06)

**Finding:** Both tables actively used

**Tables:**
- `chatbot_document_reads` - Used by `DocumentReadsRepository`
- `chatbot_document_reads_simple` - Used by `routers/chatbot/documents.py`

**Current Usage:**
- `DocumentReadsRepository` uses `chatbot_document_reads` (old table name in code)
- `routers/chatbot/documents.py` uses `chatbot_document_reads_simple` directly
- Migration exists: `20250927_userless_reads.py` migrates between them

**Status:** Both in use during transition period, migration path established

**Action:** No immediate action needed - migration will consolidate over time

---

### 🟢 Repository Method Usage Audit

**Issue:** Many repositories have 10-20+ methods. Not all may be actively used.

**Examples of potentially unused methods:**
- `ConversationRepository.get_conversations_by_date_range()`
- `ConversationRepository.list_recent_for_chat()`
- Various "get X for Y" permutations

**Action:** Audit during services layer review to identify unused methods for removal.

---

### 🟢 Direct SQL in Services/Routers - Extract to Repositories

**Issue:** Some services and routers contain direct SQL queries instead of using repositories.

**Why it matters:**
- Violates three-layer architecture (Router → Service → Repository)
- Makes data access patterns harder to track
- Duplicates SQL logic across layers

**Action:**
- [ ] Audit all service files for direct `session.execute()` or `fetch_*()` calls
- [ ] Audit all router files for direct SQL queries
- [ ] Extract to appropriate repositories
- [ ] Update services/routers to call repository methods

**Priority:** Medium - doesn't break functionality, but improves architecture consistency.

---

### ✅ Database Seeding & Prompt Migration - COMPLETED (2025-10-27)

**Prompts fully migrated to Eve:**
- All prompts now in Eve context packs (`app/eve/context-packs/`)
- Legacy `seed_data/prompts/` directory removed
- `PromptRepository` deleted - no longer needed
- Analysis passes use `eve_prompt_id` instead of `prompt_template_id`

**Context definitions still seeded:**
- Context definitions: `seed_data/context_definitions.yaml` (6 active)
- Maps retrieval function names to implementations
- Orchestrator: `seed_data/seed_data.py`

---

**Issue:** No repositories exist for core chatbot tables because frontend accesses DB directly.

**Tables needing repositories:**
- `chatbot_users`
- `chatbot_threads` (or `chatbot_chats`)
- `chatbot_documents`
- `chatbot_messages_v2`
- `chatbot_suggestions`
- `chatbot_streams` (if still used)
- `chatbot_votes_v2` (if still used)

**Action:** Create repositories during frontend review once access patterns are clear.

---

## Adding New Repositories

### For Simple Tables (CRUD only)

```python
from .core.generic import GenericRepository

class MyRepository(GenericRepository):
    TABLE = "my_table"
    # That's it! Free CRUD methods.
```

### For Complex Tables

```python
from .core.base import BaseRepository
from backend.db.sql import fetch_one, fetch_all, execute_write
import logging

logger = logging.getLogger(__name__)

class MyRepository(BaseRepository):
    TABLE = "my_table"
    
    @classmethod
    def custom_query(cls, session, param):
        logger.info("Custom query for param=%s", param)
        return fetch_all(session,
            "SELECT * FROM my_table WHERE field = :param",
            {"param": param})
```

### For Analysis Items

```python
from .analysis_items.analysis_items_base import AnalysisItemRepository

class MyAnalysisRepository(AnalysisItemRepository):
    TABLE = "my_analysis_items"
    ITEM_NAME_FIELD = "item_type"
    EXTRA_FIELDS = ["snippet", "confidence"]
    # Inherits get_by_chat, get_by_conversation, bulk insert, etc.
```

---

## Related Documentation

- **[Main Backend Guide](../agents.md)** - Overall backend architecture
- **[Services Layer](../services/agents.md)** - How services call repositories
- **[Context System](../services/context/agents.md)** - Context retrieval patterns
- **[Seeding Parity Check](../seed_data/SEEDING_PARITY_CHECK.md)** - Seeding consolidation plan

