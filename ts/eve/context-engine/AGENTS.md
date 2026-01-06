# Context Engine - Eve's Core Service

The Context Engine is Eve's central assembly service that loads prompts, resolves context, and compiles final prompts.

## Architecture

### Main Components

**`index.ts`** - Core engine API
- `executePrompt()` - Main entry point for prompt execution
- Variable resolution and substitution
- Budget fitting logic (v1: basic checking, v2: intelligent trimming)
- Returns compiled prompt parts + token ledger

**`registry.ts`** - Prompt & Pack Management
- Loads and validates .prompt.md files
- Loads and validates .pack.yaml files
- Caches prompts and packs in memory
- Zod schema validation at load time

**`server.ts`** - HTTP API Server
- Express server on port 3031
- `/engine/execute` - Execute prompts
- `/engine/prompts`, `/engine/packs` - List and inspect prompts/packs
- Imports API handlers from `api/` directory

**`api/` Directory** - API Route Handlers
- `definitions.ts` - Context definitions mapping + GET endpoint
- `selections.ts` - Context selection + preview endpoints
- `encoding.ts` - Conversation encoding endpoint

**`cli.ts`** - Command-Line Tools
- `prompts:list` - List all prompts
- `prompts:inspect <id>` - Show prompt details
- `prompts:dry-run <id>` - Test prompt with real/fake data
- `packs:list` - List all context packs
- `packs:inspect <id>` - Show pack details

### Retrieval Adapters (`retrieval/`)

**Pattern:** Adapter interface + implementation files

**Adapters:**
- `analyses.ts` + `analyses-impl.ts` - Analysis facets retrieval (direct DB)
- `convos.ts` + `convos-impl.ts` - Conversation text retrieval (direct DB)
- `artifacts.ts` + `artifacts-impl.ts` - Chatbot documents retrieval (direct DB)
- `simple.ts` + `simple-impl.ts` - UserName, ChatText, RawConversation (direct DB)
- `history.ts` - Suggestion history
- `current_messages.ts` - Recent messages
- `static_snippet.ts` - Inline text snippets

**Registry:** `retrieval/index.ts` exports all adapters by retrieval function name.

**Adapter Interface:**
```typescript
export interface RetrievalAdapter {
  (ctx: RetrievalContext): Promise<string>;
}

export interface RetrievalContext {
  sourceChat?: number;
  vars: Record<string, any>;
  params: Record<string, any>;  // From pack slice
  budgetTokens?: number;
}
```

**Key Insight:** Adapters resolve context **synchronously** during prompt assembly. The compiled text is returned immediately, not fetched on-demand.

## Execution Flow

```
1. Client calls executePrompt({ promptId, sourceChat, vars, budgetTokens })
   ↓
2. Registry loads prompt .prompt.md file
   - Extracts context pack requirements
   - Extracts always-on packs
   - Extracts variable definitions
   ↓
3. Registry loads required context packs
   - Main pack (from prompt.default_pack)
   - Always-on packs (from prompt.always_on)
   ↓
4. Engine resolves variables
   - {{source_chat}} → sourceChat value
   - {{user_var}} → vars['user_var']
   ↓
5. Engine resolves pack parameters
   - chat_ids: ["{{source_chat}}"] → [123]
   - time: {preset: "year"} → {preset: "year"}
   ↓
6. Engine calls retrieval adapters
   - For each pack slice, call retrieval_function
   - Pass resolved params + context
   - Returns compiled text
   ↓
7. Engine assembles parts
   - visiblePrompt (user-facing instruction)
   - hiddenParts (context slices)
   ↓
8. Engine checks budget
   - Counts tokens in all parts
   - Applies safety factor (0.90 default)
   - Fails if over budget
   ↓
9. Returns result
   - ledger: { totalTokens, items }
   - hiddenParts: [{ name, text }]
   - visiblePrompt: string
```

## Budget Fitting

**Current (v1):** Basic checking
- Counts total tokens
- Applies safety factor
- Fails if over budget

**Planned (v2):** Intelligent trimming
- See lines 228-276 in `index.ts` for spec
- Category-specific strategies (shrink_time, reduce_facets, etc.)
- Pack-level alternatives
- Graceful degradation

**Implementation notes in code are comprehensive - refer to them when implementing v2.**

## HTTP API Endpoints

### Prompt Execution

**POST `/engine/execute`**

Execute a prompt with context assembly.

```json
Request:
{
  "promptId": "hogwarts-v1",
  "sourceChat": 123,
  "vars": {"chat_title": "Alice"},
  "budgetTokens": 180000
}

Response (Success):
{
  "ledger": { "totalTokens": 481, "items": [...] },
  "hiddenParts": [{"name": "ANALYSES_YEAR", "text": "..."}],
  "visiblePrompt": "# Hogwarts House Sorting\n..."
}

Response (Failure):
{
  "kind": "BudgetExceeded",
  "message": "...",
  "currentTokens": 240000,
  "budget": 162000,
  "suggest": [...]
}
```

### Conversation Encoding (Backend Integration)

**POST `/engine/encode`**

Encode conversation for LLM analysis (used by Celery workers).

```json
Request:
{
  "conversation_id": 123,
  "chat_id": 456
}

Response:
{
  "encoded_text": "...",
  "token_count": 15234,
  "message_count": 42
}
```

### Context Selection (Frontend Integration)

**GET `/api/context/definitions`**

List available context definitions (compatibility layer for old frontend).

**POST `/api/context/selections`**

Resolve context selections (used by frontend for @-tag context).

**POST `/api/context/selections/preview`**

Token counting without content resolution.

### Registry Inspection

**GET `/engine/prompts`** - List all prompts

**GET `/engine/prompts/:id`** - Get prompt details

**GET `/engine/packs`** - List all context packs  

**GET `/engine/packs/:id`** - Get pack details

## Configuration

**`config.yaml`** - Engine configuration

```yaml
safety_factors:
  claude-sonnet-4: 0.90      # Plan to 90% of budget
  claude-haiku-3-5: 0.92
  gpt-4o: 0.88
  default: 0.90

category_defaults:
  personality-insights:
    - shrink_time: [year, six_months, quarter, month]
  fun:
    - shrink_time: [year, six_months, quarter]
  intelligence:
    - shrink_time: [month, two_weeks]
```

## Schemas (`schemas/`)

**`promptFrontmatter.zod.ts`** - Validates prompt .prompt.md front-matter

**`packSpec.zod.ts`** - Validates context pack .pack.yaml structure

Schemas are applied at **load time** (not runtime), catching errors early.

## Testing

**CLI dry-run:**
```bash
npx ts-node eve/context-engine/cli.ts prompts:dry-run test-v1 --chat 123 --budget 10000
```

**With real data:**
```bash
npx ts-node eve/context-engine/cli.ts prompts:dry-run hogwarts-v1 --chat <ID> --budget 180000
```

**Inspect results:**
- Token count per slice
- Context ledger (human-readable summary)
- Compiled visible prompt
- Budget fit check

## Common Patterns

### Adding a New Retrieval Adapter

1. Create adapter in `retrieval/your-adapter.ts`
2. Implement `RetrievalAdapter` interface
3. Export from `retrieval/index.ts` registry
4. Reference in pack slice `retrieval_function` field

### Adding a New Endpoint

1. Add route in `server.ts`
2. Call engine functions from `index.ts`
3. Follow existing error handling patterns
4. Add to this doc's API reference

### Variable Resolution

Variables resolve in this order:
1. Built-in vars (`{{source_chat}}`)
2. User-provided vars (from `executePrompt({ vars })`)
3. Pack-level param defaults

### Error Handling

- **BudgetExceeded** - Return 400 with suggestions
- **PromptNotFound** - Return 404 with available prompts
- **ValidationError** - Return 400 with Zod error details
- **RetrievalError** - Return 500 with adapter name and error

## Related Documentation

- **[prompts/AGENTS.md](../prompts/AGENTS.md)** - How to create prompts
- **[context-packs/AGENTS.md](../context-packs/AGENTS.md)** - How to create packs
- **[database/AGENTS.md](../database/AGENTS.md)** - Database access patterns
- **[encoding/AGENTS.md](../encoding/AGENTS.md)** - Conversation encoding

