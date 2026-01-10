---
id: commitment-extraction-live-v2
name: "Live Commitment Extraction v2 (Two-Stage: Extraction)"
version: 2.0.0
category: analysis
tags: [commitments, extraction, live, two-stage]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  conversation_id: { type: number, required: true, example: 12345 }
  chat_id: { type: number, required: true, example: 1 }
  conversation_text: { type: string, required: true, example: "Alice: I'll send that tomorrow\nBob: Thanks!" }

execution:
  mode: backend-task
  result_type: json
  model_preferences: [claude-sonnet-4-5-20250929, claude-haiku-3-5]
  fallback_models: [xai/grok-4, claude-sonnet-4-5-20250929]
  retry_on_parse_failure: true
  temperature: 0.3
  max_tokens: 4000
---

# Live Commitment Extraction (Stage 1)

You are analyzing a live conversation to extract ALL potential commitments from the CURRENT CONVERSATION section only.

**STAGE 1: PURE EXTRACTION**
Your ONLY job is to extract commitments. Do NOT worry about:
- Whether similar commitments already exist
- Deduplication
- Modifications or completions

Extract ANY statement that could be a commitment, including:
- Direct promises: "I'll...", "I will...", "Let me..."
- Soft commitments: "I should...", "I need to...", "I'll try to..."
- Follow-ups: "checking in about", "following up on", "get back to you"
- Conditional commitments: "If X, then I'll Y"
- Rescheduled commitments: "Actually, I'll do it tomorrow instead"
- Status updates: "I finished X", "I couldn't do Y"

For EACH commitment-related statement found, extract:
1. commitment_text: Exact what was said (preserve original wording)
2. commitment_type: "new" | "modification" | "completion" | "cancellation"
3. to_person: Who it was made to
4. timing: When mentioned
5. timing_type: "explicit" | "relative" | "vague" | "none"
6. is_conditional: true/false
7. condition: Condition text if applicable
8. context: Surrounding conversation context
9. confidence: "high" | "medium" | "low"
10. original_message_text: The exact message containing this commitment

**IMPORTANT**: 
- Extract EVERYTHING that might be commitment-related
- Include apparent duplicates, modifications, completions
- Preserve exact wording - do not paraphrase
- The reconciliation stage will handle deduplication

{{{conversation_text}}}
