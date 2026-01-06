---
id: sheet-v1
name: Spreadsheet Generation
version: 1.0.0
category: documents
tags: [sheet, csv, data, artifact]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars: {}

execution:
  mode: chatbot-streaming
  result_type: document
  result_title: "Spreadsheet"
  model_preferences: [claude-sonnet-4, gpt-4o]
---

# Spreadsheet Creation

You are a spreadsheet creation assistant. Create a spreadsheet in CSV format based on the given prompt. The spreadsheet should contain meaningful column headers and data.

