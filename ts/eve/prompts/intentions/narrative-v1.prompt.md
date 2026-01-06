---
id: intentions-narrative-v1
name: Intentions Narrative
version: 1.0.0
category: intentions
tags: [intentions, mastery, narrative, specific]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/intentions.ts
  export: buildIntentionsNarrative

context_flexibility: medium
context:
  default_pack: intentions-narrative-context

always_on: [artifact-rules-full, privacy-redlines]

vars:
  userName: { type: string, required: true }
  chatName: { type: string, required: true }
  chatId: { type: number, required: true }
  rangeStartISO: { type: string, required: true }
  rangeEndISO: { type: string, required: true }
  tokenBudget: { type: number, required: true }

execution:
  mode: chatbot-streaming
  result_type: document
  result_title: "{{chatName}} Intentions"
  model_preferences: [claude-sonnet-4]
---

# Intentions Narrative

Create narrative intentions for a specific chat. Built via TypeScript function.

