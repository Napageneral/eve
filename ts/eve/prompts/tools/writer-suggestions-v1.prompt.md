---
id: writer-suggestions-v1
name: Writer Suggestions
version: 1.0.0
category: tools
tags: [tools, writing, suggestions]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/tools.ts
  export: buildWriterSuggestions

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars: {}

execution:
  mode: chatbot-streaming
  result_type: json
  model_preferences: [claude-sonnet-4]
---

# Writer Suggestions

Generate writing improvement suggestions. Built via TypeScript function.

