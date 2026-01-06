---
id: title-history-v1
name: Title from History
version: 1.0.0
category: chat
tags: [title, utility, short-text]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/title-generation.ts
  export: buildTitleFromHistory

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  prior: { type: string, required: false, example: "Dinner Plans" }
  historyText: { type: string, required: false }
  userText: { type: string, required: false }
  assistantText: { type: string, required: false }

execution:
  mode: chatbot-streaming
  result_type: text
  model_preferences: [gemini-2.5-flash, claude-haiku-3-5]
---

# Title from History

Generate a short 2-4 word title based on conversation history. Built via TypeScript function.

