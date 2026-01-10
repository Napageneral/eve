# Context Packs - Context Specifications

Context packs define **what context to retrieve** and **how to retrieve it** for Eve prompts.

## File Format

**Naming:** `<id>.pack.yaml` (e.g., `analyses-year-personality.pack.yaml`)

**Structure:**
```yaml
id: analyses-year-personality
name: 1 Year Personality Analysis
category: personality-insights
description: |
  Long-range personality analysis covering 12 months of chat history.
  Reduces recency bias by showing stable patterns.

slices:
  - name: ANALYSES_YEAR
    retrieval_function: analyses_context_data
    params:
      chat_ids: ["{{source_chat}}"]
      time:
        preset: year
      include: [entities, topics, emotions, humor]
      encode:
        format: compact_json
    estimate_tokens: 150000
    doc: |
      Year-long analysis reveals stable personality traits rather than
      recent mood fluctuations.
    encoding: auto

trimming:
  strategies:
    - shrink_time: [year, six_months, quarter, month]
  alternatives:
    - analyses-month-recent
```

## Spec Structure

### Required Fields

**`id`** - Unique identifier
- Example: `analyses-year-personality`
- Used in prompt front-matter

**`name`** - Human-readable name
- Example: "1 Year Personality Analysis"

**`category`** - Organization category
- Options: `personality-insights`, `fun`, `intelligence`, `intentions`, `analysis`, `documents`, `ui`, `system`, `utility`

**`slices`** - Array of context slices
- Each slice defines one piece of context
- See Slice Spec below

### Optional Fields

**`description`** - Pack purpose and usage
- Multiline YAML string
- Explains when to use this pack

**`trimming`** - Budget fitting strategies
- `strategies` - How to reduce tokens
- `alternatives` - Other packs to try if over budget

## Slice Spec

Each slice in the `slices` array defines **one piece of context**.

### Required Slice Fields

**`name`** - Slice identifier (SCREAMING_SNAKE_CASE)
- Example: `ANALYSES_YEAR`, `CONVOS_WEEK`, `USER_NAME`
- Used in context ledger

**`retrieval_function`** - Retrieval adapter name
- Example: `analyses_context_data`, `convos_context_data`
- Must exist in `context-engine/retrieval/index.ts` registry

**`params`** - Parameters passed to retrieval function
- Structure depends on retrieval function
- Supports `{{variable}}` substitution

**`estimate_tokens`** - Token estimate for this slice
- Integer (rough estimate)
- Used for budget checking

### Optional Slice Fields

**`doc`** - Slice documentation
- Explains what this slice provides
- Why it's included

**`encoding`** - Encoding format
- Options: `auto`, `xml_structured`, `json_compact`, `plaintext`
- Currently not enforced (future feature)

## Current Packs (21 Total)

### Main Context Packs (13)

**Personality Insights:**
- `analyses-year-personality` (150k tokens, 1 slice)
- `analyses-month-recent` (50k tokens, 1 slice)

**Intentions:**
- `intentions-bootstrap-context` (450k tokens, 1 slice)
- `intentions-cards-context` (200k tokens, 1 slice)
- `intentions-narrative-context` (500k tokens, 1 slice)
- `intentions-recalibrate-context` (200k tokens, 1 slice)
- `intentions-revamp-context` (1800k tokens, 2 slices)

**Intelligence:**
- `eve-suggestion-context` (22k tokens, 5 slices)

**Utility:**
- `static-minimal` (0 tokens, 0 slices) - No context needed

**Test:**
- `test-static` (500 tokens, 1 slice)

### Always-On Packs (4)

These are included automatically by most prompts:

**System Category:**
- `artifact-rules-min` (2k tokens) - Prevent dumping reports in chat
- `artifact-rules-full` (500 tokens) - Extended artifact rules
- `privacy-redlines` (500 tokens) - Privacy and safety rules
- `app-meta` (500 tokens) - App capabilities context

### Specialized Packs (4)

**Analysis Category:**
- `commitment-live-context` - Commitment extraction context
- `commitment-reconciliation-context` - Commitment reconciliation

**Other Categories:**
- `document-update-context` - Document update context
- `ui-generation-context` - UI generation context

## Creating a New Pack

### Step 1: Create File

```bash
touch eve/context-packs/<id>.pack.yaml
```

### Step 2: Define Pack

```yaml
id: my-new-pack-v1
name: My New Context Pack
category: fun
description: |
  Provides context for my new feature.

slices:
  - name: MY_CONTEXT
    retrieval_function: analyses_context_data
    params:
      chat_ids: ["{{source_chat}}"]
      time: {preset: month}
      include: [topics, emotions]
      encode: {format: compact_json}
    estimate_tokens: 50000
    doc: Recent month analysis for quick insights

trimming:
  strategies:
    - shrink_time: [month, two_weeks, week]
  alternatives:
    - analyses-year-personality
```

### Step 3: Test

```bash
npm run packs:inspect my-new-pack-v1
```

### Step 4: Use in Prompt

```yaml
# In prompt front-matter
context:
  default_pack: my-new-pack-v1
```

## Common Retrieval Functions

### `analyses_context_data`

Fetches analysis facets for chats.

**Params:**
```yaml
params:
  chat_ids: ["{{source_chat}}"]
  time: {preset: year}
  include: [entities, topics, emotions, humor, quotes]
  encode: {format: compact_json}
```

### `convos_context_data`

Fetches conversation text.

**Params:**
```yaml
params:
  selectors: ["?chat={{source_chat}}&preset=week"]
```

### `artifacts_context_data`

Fetches chatbot documents.

**Params:**
```yaml
params:
  titles: ["Eve Intentions", "Overall Analysis"]
```

### `static_snippet`

Inline text snippet.

**Params:**
```yaml
params:
  text: |
    This is inline context text.
    It will be included as-is.
```

## Variable Resolution

Pack params support `{{variable}}` substitution:

**Built-in variables:**
- `{{source_chat}}` - Chat ID being analyzed

**User variables:**
- From `executePrompt({ vars: { ... } })`

**Example:**
```yaml
params:
  chat_ids: ["{{source_chat}}"]  # Resolves to [123]
  user_name: "{{user_name}}"     # Resolves to provided var
```

## Trimming Strategies

When context exceeds budget, Eve can intelligently trim using strategies defined in the pack.

### Strategy Types

**`shrink_time`** - Reduce time window
```yaml
strategies:
  - shrink_time: [year, six_months, quarter, month, two_weeks, week, day]
```

**`reduce_facets`** - Remove less important analysis facets
```yaml
strategies:
  - reduce_facets: [quotes, humor, entities, topics, emotions]
```

**`limit_artifacts`** - Reduce number of documents
```yaml
strategies:
  - limit_artifacts: [5, 3, 1]
```

### Alternative Packs

If trimming fails, try alternative packs:
```yaml
alternatives:
  - analyses-month-recent  # Smaller time window
  - static-minimal         # No context
```

**Trimming is not yet fully implemented.** See `context-engine/index.ts` lines 228-276 for spec.

## Token Estimation

**Token estimates are rough:**
- 1 token ≈ 4 characters
- Actual count depends on LLM tokenizer

**Best practice:** Test with dry-run to verify actual token usage.

```bash
npx ts-node eve/context-engine/cli.ts prompts:dry-run <prompt-id> --chat <chat-id> --budget 180000
```

## Global Packs

**Location:** `eve/context-packs/global/`

Global packs are **always-on** system-level context:
- `artifact-rules-min.pack.yaml` - Artifact usage rules
- `privacy-redlines.pack.yaml` - Privacy constraints
- `app-meta.pack.yaml` - App capabilities

**Included by most prompts** to ensure consistent behavior.

## Best Practices

### Pack Design

1. **Single responsibility** - One pack, one purpose
2. **Meaningful names** - Describe what context provides
3. **Document why** - Explain pack's purpose and trade-offs
4. **Reasonable estimates** - Test token usage with real data

### Slice Design

1. **Descriptive slice names** - `ANALYSES_YEAR` not `DATA`
2. **Include doc strings** - Explain what slice provides
3. **Parameterize wisely** - Use variables for flexibility

### Token Budgets

1. **Conservative estimates** - Better to overestimate than underestimate
2. **Test with real data** - Dry-run prompts to verify
3. **Define trimming** - Provide fallback strategies

### Versioning

1. **Version pack IDs** - `my-pack-v1`, `my-pack-v2`
2. **Deprecate old packs** - Don't delete immediately
3. **Update references** - Find prompts using old pack

## Example Packs

### Simple Static Pack

```yaml
id: welcome-message-v1
name: Welcome Message
category: system

slices:
  - name: WELCOME_TEXT
    retrieval_function: static_snippet
    params:
      text: |
        Welcome to ChatStats! I'm Eve, your personal mastery assistant.
    estimate_tokens: 50
    doc: Static welcome message
```

### Multi-Slice Pack

```yaml
id: comprehensive-analysis-v1
name: Comprehensive Analysis
category: analysis

slices:
  - name: ANALYSES_YEAR
    retrieval_function: analyses_context_data
    params:
      chat_ids: ["{{source_chat}}"]
      time: {preset: year}
    estimate_tokens: 150000
    doc: Year-long personality analysis

  - name: RECENT_CONVOS
    retrieval_function: convos_context_data
    params:
      selectors: ["?chat={{source_chat}}&preset=month"]
    estimate_tokens: 50000
    doc: Recent conversations for context

  - name: EXISTING_INSIGHTS
    retrieval_function: artifacts_context_data
    params:
      titles: ["Overall Analysis"]
    estimate_tokens: 10000
    doc: Previous analysis results

trimming:
  strategies:
    - shrink_time: [year, six_months, quarter]
  alternatives:
    - analyses-month-recent
```

## Related Documentation

- **[prompts/AGENTS.md](../prompts/AGENTS.md)** - How prompts use packs
- **[context-engine/AGENTS.md](../context-engine/AGENTS.md)** - How packs are loaded
- **[database/AGENTS.md](../database/AGENTS.md)** - How context is retrieved








