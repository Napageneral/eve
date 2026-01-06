---
id: title-generation-v1
name: Title Generation
version: 1.0.0
category: analysis
tags: [title, short-text, utility]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  content_text: { type: string, required: true, example: "Summary of conversation analysis..." }
  max_words: { type: number, required: false, example: 4 }

execution:
  mode: backend-task
  result_type: text
  model_preferences: [gemini-2.5-flash]
---

# Title Generation

Generate a concise, descriptive title ({max_words} words maximum) for this content: {content_text}

RESPOND ONLY WITH ONE TITLE, YOUR RESPONSE WILL BE USED DIRECTLY AS THE TITLE OF THE REPORT.

