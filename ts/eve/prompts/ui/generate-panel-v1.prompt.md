---
id: generate-panel-v1
name: Generate UI Panel
version: 1.0.0
category: ui
tags: [ui, react, tsx, panel, generation]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/ui-generation.ts
  export: buildUIRenderSystem

context_flexibility: high
context:
  default_pack: ui-generation-context

always_on: []

vars:
  widthPx: { type: number, required: false, example: 360 }
  suggestions: { type: string, required: true, example: "JSON array of suggestion objects" }
  analysisSummary: { type: string, required: false }

execution:
  mode: chatbot-streaming
  result_type: text
  model_preferences: [claude-sonnet-4]
---

# Generate UI Panel

Generate React TSX code for interactive UI panels. Built via TypeScript function.

