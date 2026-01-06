---
id: system-v1
name: Chat System Prompt
version: 1.0.0
category: chat
tags: [system, chatbot, core]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/chat-system.ts
  export: buildChatSystem

context_flexibility: low
context:
  default_pack: static-minimal

always_on: [artifact-rules-full, privacy-redlines, app-meta]
always_on_behavior:
  include_in_budget: true
  position: prepend
  allow_override: true  # Allow modelId to override artifact rules

vars:
  modelId: { type: string, required: true, example: "claude-sonnet-4" }
  hints: { type: string, required: false, example: "JSON string with lat/lon/city/country" }
  plan: { type: string, required: false }

execution:
  mode: chatbot-streaming
  result_type: text
  model_preferences: [claude-sonnet-4, gpt-4o, claude-haiku-3-5]
---

# Chat System Prompt

Dynamic system prompt builder for chatbot conversations. Built via TypeScript function.

