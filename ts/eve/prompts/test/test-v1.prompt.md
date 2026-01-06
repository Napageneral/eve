---
id: test-v1
name: Test Prompt (Static Context)
version: 1.0.0
category: test
tags: [test]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: test-static

always_on: [artifact-rules-min]

execution:
  mode: chatbot-streaming
  result_type: text
---

# Test Prompt

This is a simple test prompt to verify the Context Engine works end-to-end.

Context should be provided in TEST_CONTEXT.

