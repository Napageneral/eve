---
id: compile-report-v1
name: Compile Report Guidance
version: 1.0.0
category: tools
tags: [tools, report, guidance]

prompt:
  source: ts_function
  path: app/eve/prompts/dynamic/tools.ts
  export: buildReportGuidance

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  titleHint: { type: string, required: false }
  schemaJson: { type: string, required: false, example: "JSON string with schema" }

execution:
  mode: chatbot-streaming
  result_type: text
  model_preferences: [claude-sonnet-4]
---

# Compile Report Guidance

Guidance prefix for compiling reports. Built via TypeScript function.

