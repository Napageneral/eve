---
id: update-v1
name: Update Document
version: 1.0.0
category: documents
tags: [update, edit, document, artifact]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/update-document.ts
  export: buildUpdateDocument

context_flexibility: high
context:
  default_pack: document-update-context

always_on: []

vars:
  currentContent: { type: string, required: false }
  type: { type: string, required: true, example: "text" }

execution:
  mode: chatbot-streaming
  result_type: document
  model_preferences: [claude-sonnet-4, gpt-4o]
---

# Update Document

Improve an existing document based on user instructions. Built via TypeScript function.

