-- EA → IA Notification Queue
-- Stores messages from Execution Agents that need IA attention

CREATE TABLE IF NOT EXISTS ia_notifications (
  id TEXT PRIMARY KEY,
  from_agent_id TEXT NOT NULL,
  from_agent_name TEXT,
  notification_type TEXT NOT NULL, -- 'reminder', 'completion', 'question', 'error'
  priority TEXT NOT NULL DEFAULT 'normal', -- 'low', 'normal', 'high', 'urgent'
  message TEXT NOT NULL,
  metadata TEXT, -- JSON with additional context
  status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'processing', 'delivered', 'deferred', 'ignored'
  user_id TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  processed_at TIMESTAMP,
  delivered_at TIMESTAMP,
  deferred_until TIMESTAMP, -- For delayed notifications
  
  FOREIGN KEY (from_agent_id) REFERENCES execution_agents(id)
);

CREATE INDEX IF NOT EXISTS idx_ia_notifications_status ON ia_notifications(status);
CREATE INDEX IF NOT EXISTS idx_ia_notifications_user ON ia_notifications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_ia_notifications_priority ON ia_notifications(priority, status);
CREATE INDEX IF NOT EXISTS idx_ia_notifications_deferred ON ia_notifications(deferred_until, status);

