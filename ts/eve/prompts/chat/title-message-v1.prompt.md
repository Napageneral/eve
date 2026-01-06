---
id: title-message-v1
name: Title from Message
version: 1.0.0
category: chat
tags: [title, utility, short-text]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/title-generation.ts
  export: buildTitleFromMessage

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  messageText: { type: string, required: true, example: "Let's grab dinner tomorrow at that new place" }

execution:
  mode: chatbot-streaming
  result_type: text
  model_preferences: [gemini-2.5-flash, claude-haiku-3-5]
---

# Title from Message

Generate a short 2-4 word title based on a single user message. Built via TypeScript function.

