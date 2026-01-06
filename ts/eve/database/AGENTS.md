# Database Layer - Hybrid Architecture

Eve's database layer uses a **hybrid architecture** for optimal performance and reliability:

- **Reads:** Direct readonly SQLite access for context retrieval (`central.db`)
- **Writes:** HTTP proxy to Python backend to avoid cross-process contention
- **Agent State:** Direct read/write access to separate `agents.db` (Eve-only database)

## Why Direct Database Access?

**Before (Backend HTTP):**
```
Frontend → Backend HTTP → Python retrieval → SQLite
```

**After (Eve Direct):**
```
Frontend → Eve → TypeScript retrieval → SQLite (readonly)
```

**Benefits:**
- **Zero Contention** - No cross-process SQLite write conflicts
- **Fast Reads** - Direct readonly access for context queries (no HTTP)
- **Reliable Writes** - Single-process writes to `central.db` (Python backend only)
- **Isolated Agent State** - `agents.db` managed exclusively by Eve

## Architecture

### Database Clients

**`client.ts` - DatabaseClient (readonly central.db)**
- **Purpose:** Read-only access to main database for context retrieval
- **Uses:** Bun SQLite in readonly mode
- **Access:** Conversations, messages, chats, analyses (reads only)
- **Pattern:** Direct SQL queries for fast context loading

**`agent-db-client.ts` - AgentDatabaseClient (read/write agents.db)**
- **Purpose:** Agent state management (separate database)
- **Uses:** Bun SQLite with WAL mode
- **Access:** Agent sessions, execution agents, triggers, notifications
- **Pattern:** Direct SQL for both reads and writes
- **Key:** This database is Eve-only, no contention with other processes

**~`document-db-client.ts`~ - REMOVED (Nov 2025)**
- **Replaced with:** HTTP proxy to backend API
- **Reason:** Cross-process SQLite write contention causing "disk I/O error" in tests
- **Now:** All `central.db` writes go through Python backend HTTP API

### Database Files

**`central.db` - Main Application Database**
- **Writers:** Python backend ONLY (single process, zero contention)
- **Readers:** Python backend + Eve DatabaseClient (readonly)
- **Contains:** iMessage data, analyses, chatbot documents, embeddings

**`agents.db` - Agent State Database**  
- **Writers:** Eve AgentDatabaseClient ONLY
- **Readers:** Eve AgentDatabaseClient ONLY
- **Contains:** Agent sessions, execution agents, triggers, notifications

**`queries/`** - SQL query modules for context retrieval
- `analyses.ts` - Analysis facets retrieval
- `conversations.ts` - Conversation loading
- `chats.ts` - Chat metadata
- `contacts.ts` - Contact queries
- `messages.ts` - Message queries
- `consolidated-analysis.ts` - Complex aggregation query

**Database Paths:** Configured via `CHATSTATS_APP_DIR` environment variable
- Dev: `app/.test-user-data/` (during tests)
- Prod: `~/Library/Application Support/ChatStats/`

## Client Usage

```typescript
import { getDb } from '@/eve/database/client';

const db = getDb();

// Simple query
const stmt = db.prepare('SELECT * FROM chat WHERE id = ?');
const chat = stmt.get(chatId);

// Iterate results
const stmt = db.prepare('SELECT * FROM message WHERE chat_id = ?');
for (const msg of stmt.iterate(chatId)) {
  console.log(msg);
}

// Get all results
const messages = stmt.all(chatId);
```

**Key Insight:** `better-sqlite3` is synchronous. No async/await needed.

## Query Patterns

### Simple SELECT

```typescript
export function getChatById(chatId: number): Chat | undefined {
  const db = getDb();
  const stmt = db.prepare('SELECT * FROM chat WHERE id = ?');
  return stmt.get(chatId) as Chat | undefined;
}
```

### Parameterized Query

```typescript
export function getMessagesByChatId(chatId: number, limit: number): Message[] {
  const db = getDb();
  const stmt = db.prepare(`
    SELECT * FROM message 
    WHERE chat_id = ? 
    ORDER BY date DESC 
    LIMIT ?
  `);
  return stmt.all(chatId, limit) as Message[];
}
```

### Complex Aggregation

See `queries/consolidated-analysis.ts` for example of multi-table joins and aggregations.

### Iterate for Large Results

```typescript
export function* iterateMessages(chatId: number) {
  const db = getDb();
  const stmt = db.prepare('SELECT * FROM message WHERE chat_id = ? ORDER BY date');
  for (const msg of stmt.iterate(chatId)) {
    yield msg as Message;
  }
}
```

## Writing to central.db - HTTP Proxy Pattern

**⚠️ CRITICAL: Never write directly to central.db from Eve**

All writes to `central.db` MUST go through the Python backend HTTP API:

```typescript
// ❌ WRONG - causes cross-process SQLite contention
const docDb = getDocumentDb();
docDb.createDocument({...});  // Don't do this!

// ✅ CORRECT - use backend HTTP API
const response = await fetch('http://127.0.0.1:8000/api/chatbot/documents', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    id: documentId,
    title,
    content,
    kind: 'text',
    user_id: userId,
    tags: ['display:auto'],
  }),
});

if (!response.ok) {
  throw new Error(`Failed to create document: ${response.status}`);
}
```

**Why HTTP Proxy?**
1. **Zero SQLite contention** - Python backend is the ONLY writer to `central.db`
2. **Reliable in tests** - No "disk I/O error" from concurrent writes
3. **Proper separation** - Data layer (Python) vs intelligence layer (Eve)
4. **Automatic benefits** - Backend triggers document displays, embeddings automatically

**Backend Endpoints:**
- `POST /api/chatbot/documents` - Create document
- `POST /api/chatbot/documents/{id}/update` - Update document

See `backend/routers/chatbot/documents.py` for API contract.

## Schema Parity

Eve's TypeScript types (`types.ts`) match the SQLite schema exactly.

**When schema changes:**
1. Backend migrations add/modify tables
2. Update `types.ts` to reflect changes
3. Update query modules if needed

**No need to update Eve migrations** - Eve is readonly, so schema changes are backend-only.

## Error Handling

```typescript
try {
  const db = getDb();
  const stmt = db.prepare('SELECT * FROM chat WHERE id = ?');
  return stmt.get(chatId);
} catch (error) {
  console.error('[DB] Query failed:', error);
  throw new Error(`Failed to fetch chat ${chatId}: ${error.message}`);
}
```

**Common errors:**
- Database locked (shouldn't happen with readonly, but log it)
- Invalid SQL (syntax error)
- Missing table (schema mismatch)

## Performance Considerations

**better-sqlite3 is FAST:**
- Synchronous I/O (no event loop overhead)
- Prepared statements cached automatically
- WAL mode enabled (better concurrency)

**Query Optimization:**
- Use LIMIT when possible
- Create indexes on frequently queried columns (backend-side)
- Use `.iterate()` for large result sets (avoids loading all into memory)

## Testing with Parity Checks

**Migration testing** verified Eve queries produce identical results to old Python backend:

- Convos: 99.98% character match
- Analyses: 99.9998% character match
- Artifacts: Eve BETTER (backend HTTP was broken)

**How parity was tested:**
1. Capture old backend output (Python)
2. Capture Eve output (TypeScript)
3. Character-by-character comparison
4. Log differences

**Remaining differences** (<0.02%) were:
- Whitespace normalization
- JSON key ordering
- Timestamp formatting

All **semantically identical**.

## Common Queries

### Get Chat with Contacts

```typescript
export function getChatWithContacts(chatId: number) {
  const db = getDb();
  
  const chat = db.prepare('SELECT * FROM chat WHERE id = ?').get(chatId);
  
  const contacts = db.prepare(`
    SELECT c.* FROM contact c
    JOIN chat_contact_association cca ON c.id = cca.contact_id
    WHERE cca.chat_id = ?
  `).all(chatId);
  
  return { chat, contacts };
}
```

### Get Messages with Sender Info

```typescript
export function getMessagesWithSenders(chatId: number, limit: number = 100) {
  const db = getDb();
  return db.prepare(`
    SELECT 
      m.*,
      c.full_name as sender_name,
      c.phone_number as sender_phone
    FROM message m
    LEFT JOIN contact c ON m.sender_contact_id = c.id
    WHERE m.chat_id = ?
    ORDER BY m.date DESC
    LIMIT ?
  `).all(chatId, limit);
}
```

### Get Analysis Facets

```typescript
export function getAnalysisFacets(conversationId: number) {
  const db = getDb();
  return db.prepare(`
    SELECT af.* 
    FROM analysis_facet af
    WHERE af.conversation_id = ?
  `).all(conversationId);
}
```

## Related Documentation

- **[encoding/AGENTS.md](../encoding/AGENTS.md)** - How conversations are formatted
- **[context-engine/AGENTS.md](../context-engine/AGENTS.md)** - How queries are called
- **[Backend schema](../../backend/db/models.py)** - Authoritative schema source



