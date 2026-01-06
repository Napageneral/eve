---
id: summary-report-v1
name: Summary Report Generation
version: 1.0.0
category: analysis
tags: [summary, report, analysis]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  input_data: { type: string, required: true, example: "Analysis results, conversation data..." }

execution:
  mode: backend-task
  result_type: text
  result_title: "Summary Report"
  model_preferences: [claude-sonnet-4-5-20250929, gpt-4o]
---

# Summary Report Generation

Please generate a summary report based on the following data:

{{{input_data}}}

