---
id: commitment-extraction-live-v1
name: Commitment Extraction Live v1
version: 1.0.0
category: analysis
tags: [commitments, extraction, live, structured]

prompt:
  source: markdown

context_flexibility: low
context:
  default_pack: commitment-live-context

always_on: []

vars:
  conversation_text: { type: string, required: true }

execution:
  mode: backend-task
  result_type: json
  model_preferences: [claude-sonnet-4-5-20250929, claude-haiku-3-5]
---

# Commitment Extraction Live v1

You are analyzing a live conversation to detect NEW commitments made in the most recent messages.

**CRITICAL INSTRUCTIONS**:
1. ONLY extract commitments from the "CURRENT CONVERSATION" section
2. Previous conversations are provided for context only - DO NOT extract commitments from them
3. Recently completed/cancelled commitments show what has already been tracked - avoid duplicates
4. Current active commitments show what's already being tracked - only find NEW commitments

**IMPORTANT**: Focus on detecting commitments from the most recent messages in this conversation. Be aware of existing commitments to avoid duplicates.

Look for:
- Direct promises: "I'll...", "I will...", "Let me..."
- Soft commitments: "I should...", "I need to...", "I'll try to..."
- Follow-ups: "checking in about", "following up on", "get back to you"
- Conditional commitments: "If X, then I'll Y"

For each NEW commitment found:
1. commitment_text: What they committed to (be specific)
2. to_person: Who they made the commitment to (use actual names from participants)
3. timing: When they said they'd do it
4. timing_type: "explicit" (Tuesday), "relative" (tomorrow), "vague" (soon), or "none"
5. is_conditional: true if dependent on something
6. condition: what it depends on (if conditional, otherwise use empty string "")
7. context: Why this commitment matters (from conversation context)

Also check if recent messages:
- Complete existing commitments (compare with current active commitments)
- Modify existing commitments (due date changes, scope changes)
- Update conditions for conditional commitments

**CRITICAL**: 
- Only detect NEW commitments that are NOT already in the current active commitments list
- Use the participant names provided in the chat context for the "to_person" field
- Consider the conversation context and existing commitments when determining if something is new
- Previous conversations and inactive commitments are for CONTEXT ONLY - do not extract from them

{{{conversation_text}}}

