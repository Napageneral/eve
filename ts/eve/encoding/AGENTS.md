# Encoding Layer - Conversation Formatting

The encoding layer formats conversations and commitments for LLM context.

## Purpose

Transform raw database records into structured text that LLMs can understand:
- Conversations → Formatted message sequences
- Commitments → Context windows with related messages

**Used by:**
- Backend Celery workers (conversation analysis)
- Frontend (commitment extraction - disabled)

## Conversation Encoding

**`conversation.ts`** - Main conversation encoder

### Input

Raw conversation from database:
```typescript
{
  id: number,
  chat_id: number,
  messages: Message[]
}
```

### Output

Formatted text for LLM:
```
[Conversation #123 - Chat: Alice & Bob - 42 messages]

2024-10-15 14:32 | Alice: Hey, how's it going?
2024-10-15 14:35 | Bob: Pretty good! Working on that project.
2024-10-15 14:36 | Alice: Nice! Need any help?
...
```

### Encoding Options

**Format Options:**
- `include_metadata` - Include conversation header
- `include_timestamps` - Include message timestamps
- `include_reactions` - Include reaction emojis
- `max_messages` - Limit number of messages

**Reaction Handling:**

Reactions are encoded as emoji annotations:
```
2024-10-15 14:32 | Alice: That's hilarious! [👍 Loved by Bob]
```

Reaction emojis mapped from iMessage codes (see `utils/constants.ts`).

### Character-Level Accuracy

Eve's encoding matches the old Python backend at **99.98% character level**.

**Verified differences** (<0.02%):
- Whitespace normalization (trailing spaces)
- Timestamp formatting (microseconds)
- Reaction emoji ordering

All **semantically identical**.

## Commitment Encoding (Disabled)

**`commitment.ts`** - Commitment context encoder

**Status:** Currently disabled. Logic exists but not actively used.

**Purpose:** Would provide context window around commitment mentions in conversations.

**Implementation notes:**
- Extracts N messages before/after commitment mention
- Includes commitment metadata
- Formats for LLM extraction task

**Why disabled:** Commitment extraction moved to different approach. May be re-enabled in future.

## Token Counting

**See `utils/token-counter.ts`** for token counting logic.

Token estimates are **rough approximations**:
- 1 token ≈ 4 characters
- Actual count depends on LLM tokenizer

**For accurate counts:** Use tiktoken with model-specific encoding.

## Encoding Strategies

### Time-Based Trimming

Limit conversation to specific time window:
```typescript
const messages = getMessagesInTimeRange(chatId, startDate, endDate);
const encoded = encodeConversation({ messages, include_timestamps: true });
```

### Message-Based Trimming

Limit to N most recent messages:
```typescript
const messages = getRecentMessages(chatId, limit);
const encoded = encodeConversation({ messages });
```

### Reaction Filtering

Exclude reactions to reduce token usage:
```typescript
const encoded = encodeConversation({ 
  messages, 
  include_reactions: false 
});
```

## API Usage

**Backend Celery:**
```python
# Call Eve encoding endpoint
response = requests.post('http://localhost:3031/engine/encode', json={
  'conversation_id': 123,
  'chat_id': 456
})

result = response.json()
# result['encoded_text'] - Formatted conversation
# result['token_count'] - Token estimate
# result['message_count'] - Number of messages
```

**Direct TypeScript:**
```typescript
import { encodeConversation } from '@/eve/encoding/conversation';

const messages = getMessagesByChatId(chatId);
const encoded = encodeConversation({
  messages,
  include_metadata: true,
  include_timestamps: true,
  include_reactions: true,
  max_messages: 1000
});

console.log(encoded);
```

## Format Examples

### With Metadata

```
[Conversation #1234 - Chat: Alice & Bob - 156 messages - Oct 2024]

2024-10-15 09:23 | Alice: Morning!
2024-10-15 09:25 | Bob: Hey! Ready for the meeting?
2024-10-15 09:26 | Alice: Yep, see you at 10
```

### Without Timestamps

```
Alice: Morning!
Bob: Hey! Ready for the meeting?
Alice: Yep, see you at 10
```

### With Reactions

```
2024-10-15 09:23 | Alice: That joke was amazing! [👍 Liked by Bob, Charlie]
2024-10-15 09:25 | Bob: I know right? [❤️ Loved by Alice]
```

## Performance

**Encoding speed:**
- ~100 messages → ~10ms
- ~1000 messages → ~50ms
- ~10000 messages → ~300ms

**Bottleneck:** String concatenation and timestamp formatting.

**Optimization:** Use array.join() instead of repeated +=

## Testing

**Parity tests** verified encoding matches old Python backend:

```bash
# Backend encoding (Python)
curl -X POST http://localhost:8000/api/encode \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": 123}'

# Eve encoding (TypeScript)
curl -X POST http://localhost:3031/engine/encode \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": 123, "chat_id": 456}'

# Compare character-by-character
diff backend_output.txt eve_output.txt
```

**Results:** 99.98%+ match across 1,800+ conversations.

## Related Documentation

- **[database/AGENTS.md](../database/AGENTS.md)** - How messages are retrieved
- **[utils/token-counter.ts](../utils/token-counter.ts)** - Token counting logic
- **[utils/constants.ts](../utils/constants.ts)** - Reaction emoji mapping








