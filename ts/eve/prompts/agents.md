# Prompts - LLM Instruction Definitions

Eve prompts are **Markdown files with YAML front-matter** that define LLM instructions and context requirements.

## File Format

**Naming:** `<id>.prompt.md` (e.g., `hogwarts-v1.prompt.md`)

**Structure:**
```markdown
---
id: hogwarts-v1
name: Hogwarts House Sorting
version: 1.0.0
category: fun
tags: [personality, harry-potter]

prompt:
  source: markdown

model_preferences: [claude-sonnet-4]

context_flexibility: medium
context:
  default_pack: analyses-year-personality

always_on: [artifact-rules-min, privacy-redlines]

variables:
  required: [chat_title]
  optional: [time_range]
---

# Hogwarts House Sorting

You are the Sorting Hat from Harry Potter...

Based on the personality analysis, sort {{chat_title}} into their Hogwarts house.
```

## Front-Matter Spec

### Required Fields

**`id`** - Unique identifier (kebab-case with version)
- Example: `hogwarts-v1`, `intentions-narrative-v2`
- Used to reference prompt in code

**`name`** - Human-readable name
- Example: "Hogwarts House Sorting"
- Displayed in UI

**`version`** - Semantic version
- Example: "1.0.0"
- Helps track breaking changes

**`category`** - Organization category
- Options: `analysis`, `fun`, `intentions`, `chat`, `documents`, `intelligence`, `tools`, `ui`, `test`
- Used for grouping in lists

**`prompt.source`** - Source type
- Value: `markdown` (standalone prompt in file body)
- Alternative: `ts_function` (deprecated, being migrated)

### Optional Fields

**`tags`** - Searchable tags
- Example: `[personality, harry-potter, humor]`
- Used for filtering/discovery

**`model_preferences`** - Preferred LLM models
- Example: `[claude-sonnet-4, gpt-4o]`
- Advisory only (client decides)

**`context_flexibility`** - How flexible is context
- Options: `low`, `medium`, `high`
- Used for budget fitting (not yet implemented)

**`context.default_pack`** - Default context pack
- Example: `analyses-year-personality`
- Loaded automatically unless overridden

**`always_on`** - Always-included packs
- Example: `[artifact-rules-min, privacy-redlines]`
- Included regardless of budget

**`variables.required`** - Required variables
- Example: `[chat_title, chat_id]`
- Must be provided in `executePrompt({ vars })`

**`variables.optional`** - Optional variables
- Example: `[time_range, token_budget]`
- Have defaults if not provided

## Prompt Body

The Markdown content after front-matter is the **actual prompt text** sent to the LLM.

### Variable Substitution

Use `{{variable_name}}` syntax for runtime substitution:

```markdown
Analyze {{chat_title}}'s communication patterns over the last {{time_range}}.

User Name: {{user_name}}
Chat ID: {{chat_id}}
```

**Built-in variables:**
- `{{source_chat}}` - Chat ID being analyzed
- `{{chat_title}}` - Chat display name

**User variables:**
- Provided in `executePrompt({ vars: { chat_title: 'Alice' } })`

### Context Slices

Context is **not inline** in the prompt body. It's assembled by Eve and passed as `hiddenParts`.

**Don't do this:**
```markdown
# Bad - inline context
Here's the conversation data: {{conversation_text}}
```

**Do this instead:**
```markdown
# Good - reference context pack
You will receive conversation analysis in the hidden context.
```

Then specify context pack in front-matter:
```yaml
context:
  default_pack: analyses-year-personality
```

## Current Prompts (31 Total)

### Analysis (11 prompts)

- `convo-all-v1` - Conversation-wide analysis
- `commitment-extraction-live-v2` - Extract commitments
- `commitment-reconciliation-v1` - Reconcile commitments
- `title-generation-v1` - Generate titles
- `display-generation-v1` - React component generation
- `summary-report-v1` - Summary generation
- `overall-v1` - Overall chat analysis

### Fun (4 prompts)

- `hogwarts-v1` - Hogwarts house sorting
- `enneagram-v1` - Enneagram personality typing
- `gift-ideas-v1` - Gift recommendations
- `astrology-v1` - Astrological sign analysis

### Intentions (5 prompts)

- `bootstrap-v1` - Initial intentions setup
- `cards-v1` - Intention cards generation
- `narrative-v1` - Intentions narrative
- `recalibrate-v1` - Update intentions
- `revamp-v1` - Redesign intentions system

### Chat (3 prompts)

- `system-v1` - System prompt for chat
- `title-message-v1` - Generate title from message
- `title-history-v1` - Generate title from history

### Other Categories

- Documents (3 prompts)
- Intelligence (1 prompt)
- Tools (2 prompts)
- UI (1 prompt)
- Test (1 prompt)

## Creating a New Prompt

### Step 1: Create File

```bash
touch eve/prompts/<category>/<id>.prompt.md
```

### Step 2: Add Front-Matter

```yaml
---
id: my-new-prompt-v1
name: My New Prompt
version: 1.0.0
category: fun
tags: [example]

prompt:
  source: markdown

model_preferences: [claude-sonnet-4]

context:
  default_pack: analyses-year-personality

always_on: [artifact-rules-min]

variables:
  required: [chat_title]
---
```

### Step 3: Write Prompt Body

```markdown
# My New Prompt

You are an expert at...

Analyze {{chat_title}}'s patterns and provide insights.
```

### Step 4: Test

```bash
npm run prompts:inspect my-new-prompt-v1
npm run prompts:dry-run my-new-prompt-v1 --chat 123 --budget 180000
```

### Step 5: Use in Code

```typescript
const result = await executeEvePrompt({
  promptId: 'my-new-prompt-v1',
  sourceChat: 123,
  vars: { chat_title: 'Alice' },
  budgetTokens: 180000
});
```

## Best Practices

### Prompt Writing

1. **Be specific** - Vague prompts produce vague results
2. **Use examples** - Show the LLM what you want
3. **Reference context** - Tell the LLM what data it will receive
4. **Set constraints** - Word limits, format requirements, etc.

### Variable Usage

1. **Minimize required vars** - Use defaults when possible
2. **Name clearly** - `chat_title` not `ct`
3. **Document in prompt** - Explain what each variable means

### Context Selection

1. **Match context to task** - Don't use 1-year analysis for quick summary
2. **Always-on wisely** - Only include truly essential packs
3. **Test token usage** - Verify prompt fits in budget

### Versioning

1. **Increment version** - When changing behavior significantly
2. **Deprecate old versions** - Don't delete immediately
3. **Update references** - Find all code using old version

## Common Patterns

### Analysis Prompts

```yaml
category: analysis
context:
  default_pack: analyses-year-personality
always_on: [privacy-redlines]
```

### Fun Prompts

```yaml
category: fun
context:
  default_pack: analyses-year-personality
always_on: [artifact-rules-min]
```

### Chat Prompts

```yaml
category: chat
context:
  default_pack: static-minimal  # No extra context
always_on: [privacy-redlines]
```

### Intentions Prompts

```yaml
category: intentions
context:
  default_pack: intentions-bootstrap-context
always_on: [artifact-rules-full, privacy-redlines]
```

## Related Documentation

- **[context-packs/AGENTS.md](../context-packs/AGENTS.md)** - How to create context packs
- **[context-engine/AGENTS.md](../context-engine/AGENTS.md)** - How prompts are executed
- **[Example prompts](./fun/)** - See real examples
