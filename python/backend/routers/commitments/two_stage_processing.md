# Phase 2: Two-Stage LLM Processing

## Recent Fixes (Phase 2 Cleanup)

**Critical Fixes Applied:**
1. **Fixed Conversation Text Extraction**: Stage 1 now receives just the encoded conversation text instead of the full compiled prompt, preventing nested prompt instructions.
2. **Enhanced Reconciliation Prompt**: Added brief examples to improve LLM understanding of CREATE/UPDATE/DELETE/LEAVE actions.
3. **Removed Code Duplication**: Consolidated inactive commitments retrieval into a single public method in `CommitmentService`.

## Overview

The two-stage LLM processing system transforms commitment analysis from a single-stage approach into a more robust, accurate, and maintainable two-stage process:

- **Stage 1: Pure Extraction** - Extract all potential commitments without deduplication
- **Stage 2: Intelligent Reconciliation** - Match extracted commitments with existing ones and determine appropriate actions

## Architecture

### Stage 1: Pure Extraction
- **Purpose**: Extract ALL commitment-related statements from the current conversation
- **Prompt**: `CommitmentExtractionLive v2`
- **Focus**: Capture everything that might be commitment-related
- **No filtering**: Includes duplicates, modifications, completions
- **Low temperature**: 0.3 for consistent extraction

### Stage 2: Intelligent Reconciliation
- **Purpose**: Match extracted commitments with existing ones and decide actions
- **Prompt**: `CommitmentReconciliation v1`
- **Focus**: CREATE/UPDATE/DELETE/LEAVE decisions
- **Higher temperature**: 0.5 for reasoning and decision-making
- **Context-aware**: Considers semantic similarity and conversation context

## Benefits

### Separation of Concerns
- **Extraction logic** is separate from **reconciliation logic**
- Each stage can be optimized independently
- Easier to debug and improve over time

### Better Accuracy
- **Stage 1** captures more commitments without false negatives
- **Stage 2** prevents duplicates and handles modifications correctly
- More transparent decision-making process

### Enhanced Maintainability
- Clear logging for each stage
- Easy to test individual stages
- Simpler prompt engineering for each specific task

## Usage

### Automatic Detection
The system automatically detects when to use two-stage processing based on the prompt template:

```python
# These patterns trigger two-stage processing:
use_two_stage = (
    "v2" in template_name or 
    "two-stage" in template_name or
    template_name.endswith("live") and "extractionlive" in template_name
)
```

### Manual Usage
```python
from backend.services.commitment_service import CommitmentService

commitment_service = CommitmentService()

# Run two-stage processing
result = commitment_service.process_two_stage_commitment_analysis(
    session=session,
    conversation_id=conversation_id,
    chat_id=chat_id,
    encoded_conversation=encoded_conversation,
    is_realtime=True
)

# Result contains:
# - stage1: Extraction results
# - stage2: Reconciliation results  
# - applied_actions: Actions that were successfully applied
```

## Setup

### 1. Add Prompt Templates to Database
```bash
cd app/backend/scripts
python setup_two_stage_prompts.py
```

This will add:
- `CommitmentExtractionLive v2` (Stage 1 extraction)
- `CommitmentReconciliation v1` (Stage 2 reconciliation)

### 2. Verify Installation
The setup script will list existing prompt templates and confirm the new ones were added.

### 3. Use Two-Stage Processing
Simply use the v2 extraction prompt template and the system will automatically use two-stage processing.

## Stage 1: Extraction

### Input
- Enhanced conversation context (from Phase 1)
- Current conversation only (for extraction)

### Output Schema
```json
{
  "extracted_commitments": [
    {
      "commitment_text": "I'll send the report by Friday",
      "commitment_type": "new",
      "to_person": "Logan",
      "timing": "Friday",
      "timing_type": "explicit",
      "is_conditional": false,
      "condition": "",
      "context": "Discussion about project deliverables",
      "confidence": "high",
      "original_message_text": "I'll send the report by Friday"
    }
  ]
}
```

### Commitment Types
- **new**: Brand new commitment
- **modification**: Change to existing commitment
- **completion**: Statement that a commitment was completed
- **cancellation**: Statement that a commitment was cancelled

## Stage 2: Reconciliation

### Input
- Extracted commitments from Stage 1
- Current active commitments
- Recently inactive commitments (last 7 days)

### Output Schema
```json
{
  "actions": [
    {
      "action": "CREATE",
      "extracted_commitment": { /* from stage 1 */ },
      "matched_commitment_id": null,
      "reasoning": "This is a new commitment with no existing match",
      "updates": {}
    }
  ]
}
```

### Action Types

#### CREATE
- **When**: No similar commitment exists
- **Result**: New commitment added to database
- **Use case**: Genuinely new commitments

#### UPDATE
- **When**: Similar commitment exists but with changes
- **Result**: Existing commitment updated
- **Use case**: Date changes, scope modifications, status updates

#### DELETE
- **When**: Commitment explicitly cancelled or completed
- **Result**: Commitment marked as completed/cancelled
- **Use case**: "I finished X", "I can't do Y anymore"

#### LEAVE
- **When**: No action needed
- **Result**: No database changes
- **Use case**: Duplicates, low confidence, already processed

## Error Handling

### Stage 1 Failures
If Stage 1 fails:
- Returns empty `extracted_commitments` array
- Logs error details
- Stage 2 is skipped
- No database changes occur

### Stage 2 Failures
If Stage 2 fails:
- Returns empty `actions` array
- Logs error details
- No database changes occur
- Stage 1 results are preserved for debugging

### Action Application Failures
If individual actions fail:
- Other actions continue processing
- Failed actions are logged with details
- `applied_actions` array shows success/failure for each action

## Monitoring and Logging

### Log Patterns
```
[2-STAGE] Starting two-stage analysis for conversation 1234
[2-STAGE] Starting Stage 1: Pure extraction
[2-STAGE] Stage 1 complete: extracted 3 commitments
[2-STAGE] Starting Stage 2: Reconciliation
[2-STAGE] Stage 2 complete: 3 actions determined
[2-STAGE] Action 1: CREATE - This is a new commitment with no existing match
[2-STAGE] Applied 3/3 actions successfully
```

### Metrics to Monitor
- **Extraction accuracy**: Are Stage 1 results capturing all commitments?
- **Reconciliation accuracy**: Are Stage 2 decisions correct?
- **Processing time**: How long does each stage take?
- **Error rates**: What percentage of runs fail?
- **Action distribution**: How many CREATE/UPDATE/DELETE/LEAVE actions?

## Testing

### Test Individual Stages
```python
from backend.celery_service.activities.test_two_stage import (
    test_stage1_extraction_only,
    test_stage2_reconciliation_only,
    test_two_stage_processing
)

# Test Stage 1 only
result = test_stage1_extraction_only(chat_id, conversation_id)

# Test Stage 2 only (with sample data)
result = test_stage2_reconciliation_only(extracted_commitments, chat_id, conversation_id)

# Test complete pipeline
result = test_two_stage_processing(chat_id, conversation_id, verbose=True)
```

### Unit Test Coverage
- Stage 1 extraction with various commitment types
- Stage 2 reconciliation with different matching scenarios
- Action application with success and failure cases
- Error handling for each stage

## Migration from Single-Stage

### Backward Compatibility
- Single-stage processing still works with v1 prompts
- Two-stage automatically enabled for v2 prompts
- No database schema changes required

### Gradual Rollout
1. Add new prompt templates to database
2. Test with sample conversations
3. Monitor results and tune prompts
4. Gradually switch conversations to v2 prompts
5. Monitor metrics and error rates

### Rollback Plan
If issues arise:
1. Switch back to v1 prompt templates
2. System automatically uses single-stage processing
3. No data loss or corruption
4. Can address issues and re-enable v2 later

## Future Enhancements

### Phase 3: Snapshot Management
- Better commitment state tracking
- Conflict resolution for concurrent updates
- Audit trail for all changes

### Phase 4: Frontend Synchronization
- Real-time WebSocket updates
- Optimistic UI updates
- Conflict resolution in frontend

### Phase 5: Advanced Reconciliation
- Machine learning for better matching
- User feedback integration
- Custom reconciliation rules per chat

## Troubleshooting

### Common Issues

#### "Prompt not found in database"
- Run `setup_two_stage_prompts.py` script
- Check database connection
- Verify prompt templates table exists

#### Stage 1 returns no commitments
- Check conversation encoding quality
- Verify LLM model availability
- Review extraction prompt effectiveness

#### Stage 2 produces wrong actions
- Review reconciliation prompt logic
- Check active/inactive commitment data quality
- Verify matching algorithm effectiveness

#### Actions fail to apply
- Check database permissions
- Verify commitment repository methods
- Review validation logic

### Debug Mode
Enable detailed logging:
```python
import logging
logging.getLogger('backend.services.commitment_service').setLevel(logging.DEBUG)
```

This provides detailed information about each stage and action. 