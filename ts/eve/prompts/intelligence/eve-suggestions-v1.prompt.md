---
id: eve-suggestions-v1
name: Eve Suggestions System
version: 1.0.0
category: intelligence
tags: [suggestions, mastery, eve]

prompt:
  source: ts_function
  path: prompts/dynamic/eve-suggestions.ts
  export: buildSuggestionsSystem

context_flexibility: low
context:
  default_pack: eve-suggestion-context

always_on: [app-meta, privacy-redlines]

vars:
  titleMax:
    type: number
    required: false
  subtitleMax:
    type: number
    required: false

execution:
  mode: chatbot-streaming
  result_type: json
  model_preferences: [claude-sonnet-4, claude-haiku-3-5]
---

(Prompt body defined in dynamic TS function)

