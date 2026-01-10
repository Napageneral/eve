-- Conversation analysis "blocked" status (e.g., Gemini safety)
-- Stores structured block reason info so "spicy" conversations are understood as intentional.

ALTER TABLE conversation_analyses ADD COLUMN blocked_reason TEXT;
ALTER TABLE conversation_analyses ADD COLUMN blocked_reason_message TEXT;
ALTER TABLE conversation_analyses ADD COLUMN blocked_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_conversation_analyses_blocked_reason ON conversation_analyses(blocked_reason);

