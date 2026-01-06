---
id: commitment-reconciliation-v1
name: "Commitment Reconciliation v1 (Two-Stage: Reconciliation)"
version: 1.0.0
category: analysis
tags: [commitments, reconciliation, two-stage]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  chat_id: { type: number, required: true, example: 1 }
  active_commitments: { type: string, required: true, example: "JSON array of active commitments" }
  inactive_commitments: { type: string, required: true, example: "JSON array of recently inactive commitments" }
  extracted_commitments: { type: string, required: true, example: "JSON array from extraction stage" }

execution:
  mode: backend-task
  result_type: json
  model_preferences: [claude-sonnet-4-5-20250929]
  fallback_models: [xai/grok-4, gpt-4o]
  retry_on_parse_failure: true
  temperature: 0.5
  max_tokens: 6000
---

# Commitment Reconciliation (Stage 2)

You are reconciling newly extracted commitments with existing active and recent commitments.

**STAGE 2: INTELLIGENT RECONCILIATION**

You have:
1. EXISTING ACTIVE COMMITMENTS - Currently tracked commitments
2. RECENTLY INACTIVE COMMITMENTS - Completed/cancelled in last 7 days
3. NEWLY EXTRACTED COMMITMENTS - From the current conversation

For EACH newly extracted commitment, determine the appropriate action:

**CREATE** - This is a genuinely new commitment if:
- No similar commitment exists in active or recent lists
- Different scope/target even if similar topic
- New condition or timing makes it distinct

**UPDATE** - This updates an existing commitment if:
- Same core commitment but with new timing
- Added details or clarification
- Change in scope or conditions
- Status change (but still active)

**DELETE** - This cancels an existing commitment if:
- Explicitly cancelled ("I won't be able to...")
- Completed (mark as completed, not deleted)
- Superseded by a new commitment

**LEAVE** - No action needed if:
- Exact duplicate with no new information
- Already completed/cancelled in recent list
- Confidence too low to act on

**Brief Examples:**
- CREATE: "I'll review the document tomorrow" → No existing commitment about documents
- UPDATE: "Actually, make that Friday" → Updates timing of existing commitment  
- DELETE: "I finished sending the report" → Completes existing commitment
- LEAVE: Mentioned same commitment twice → No new information

For matching, consider:
- Semantic similarity, not just exact text match
- Same person, same recipient, similar action = likely same commitment
- Time proximity - recent mentions likely refer to same commitment
- Context clues from conversation

Output format:
{
  "actions": [
    {
      "action": "CREATE" | "UPDATE" | "DELETE" | "LEAVE",
      "extracted_commitment": { /* from extraction */ },
      "matched_commitment_id": "commit_xxx" | null,
      "reasoning": "Brief explanation",
      "updates": { /* fields to update if UPDATE action */ }
    }
  ]
}

EXISTING ACTIVE COMMITMENTS:
{{{active_commitments}}}

RECENTLY INACTIVE COMMITMENTS:
{{{inactive_commitments}}}

NEWLY EXTRACTED COMMITMENTS:
{{{extracted_commitments}}}
