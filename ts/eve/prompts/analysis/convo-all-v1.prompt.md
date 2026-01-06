---
id: convo-all-v1
name: Conversation-Wide Analysis v1
version: 1.0.0
category: analysis
tags: [conversation, analysis, entities, topics, emotions, humor]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  conversation_id: { type: number, required: true, example: 12345 }
  chat_id: { type: number, required: true, example: 1 }
  conversation_text: { type: string, required: true, example: "Alice: Hey there\nBob: Hi!" }

execution:
  mode: backend-task
  result_type: json
  model_preferences: [gemini-2.0-flash, claude-sonnet-4-5-20250929, gpt-4o]
  fallback_models: [xai/grok-4, claude-sonnet-4-5-20250929]
  retry_on_parse_failure: true
  temperature: 0.7
  
response_schema:
  type: object
  properties:
    summary:
      type: string
      description: Short summary of the conversation (10-50 words)
    entities:
      type: array
      items:
        type: object
        properties:
          participant_name:
            type: string
          entities:
            type: array
            items:
              type: object
              properties:
                name:
                  type: string
              required: [name]
        required: [participant_name, entities]
    topics:
      type: array
      items:
        type: object
        properties:
          participant_name:
            type: string
          topics:
            type: array
            items:
              type: object
              properties:
                name:
                  type: string
              required: [name]
        required: [participant_name, topics]
    emotions:
      type: array
      items:
        type: object
        properties:
          participant_name:
            type: string
          emotions:
            type: array
            items:
              type: object
              properties:
                name:
                  type: string
              required: [name]
        required: [participant_name, emotions]
    humor:
      type: array
      items:
        type: object
        properties:
          participant_name:
            type: string
          humor:
            type: array
            items:
              type: object
              properties:
                message:
                  type: string
              required: [message]
        required: [participant_name, humor]
  required: [summary, entities, topics, emotions, humor]
  additionalProperties: false
---

# Conversation-Wide Analysis

You are an expert conversation analyzer. Analyze the following conversation
chunk and extract the information below.

1) A short summary (10–50 words).  
2) A list of **entities** – each item is `{"participant_name": …, "entities": [{"name": …}, …]}`  
3) A list of **topics**   – `{"participant_name": …, "topics":  [{"name": …}, …]}`  
4) A list of **emotions** – `{"participant_name": …, "emotions":[{"name": …}, …]}`  
5) A list of **humor**    – `{"participant_name": …, "humor":   [{"message": …}, …]}`  

Guidelines
* The input lines look like `Name: message text`.
* Attachments appear as `[[…]]`, reactions as `<<…>>`.
* Omit participants from a category if they have no items.
* Return **valid JSON** with the top-level keys exactly as in the schema.

Conversation chunk:
{{{conversation_text}}}
