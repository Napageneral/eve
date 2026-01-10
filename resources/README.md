# Eve Resources

This directory contains Eve's **editable prompt and pack files** that define how the context engine compiles LLM inputs.

## Structure

```
resources/
├── prompts/     # Prompt templates with YAML frontmatter + markdown body
└── packs/       # Context pack YAML files defining retrieval strategies
```

## Override Strategy

Eve supports a **layered resource loading** strategy:

1. **Embedded defaults**: All files in this directory are embedded into the Go binary at build time using `embed.FS`. The binary works standalone without disk access.

2. **Disk overrides**: You can override any resource by:
   - Setting `--resources-dir /path/to/custom/resources` CLI flag
   - Setting `EVE_RESOURCES_DIR=/path/to/custom/resources` environment variable

3. **Skill packaging**: When shipping Eve as a Claude Code skill or agent tool, you can:
   - Export embedded resources: `eve resources export --dir ./my-resources`
   - Edit prompts/packs locally for experimentation
   - Ship the modified `resources/` folder alongside the skill definition

## File Formats

### Prompts (`*.prompt.md`)

Prompts use YAML frontmatter delimited by `---` followed by markdown body:

```markdown
---
id: example-v1
name: Example Prompt
category: analysis
default_pack: example-context
---

# Example Prompt

Your prompt content here with {{variable}} substitutions.
```

### Packs (`*.pack.yaml`)

Packs define retrieval strategies and context composition:

```yaml
id: example-context
name: Example Context Pack
description: Retrieves example data
slices:
  - adapter: static_snippet
    params:
      content: "Static text content"
```

## Usage

- **List prompts**: `eve prompt list`
- **Show prompt**: `eve prompt show <id>`
- **List packs**: `eve pack list`
- **Show pack**: `eve pack show <id>`
- **Export resources**: `eve resources export --dir ./exported`

## Philosophy

Resources are **data, not code**. They should be:
- Human-readable and editable
- Versionable in git
- Overrideable for local experimentation
- Shippable as part of skills/agents

This makes Eve maximally portable: the binary contains sensible defaults, but users and agents can hack prompts without touching Go code.
