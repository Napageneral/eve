-- Agent Sessions (SDK state persistence)
-- Stores serialized Claude Agent SDK session data for both Interaction and Execution agents

CREATE TABLE IF NOT EXISTS agent_sessions (
  id TEXT PRIMARY KEY,
  agent_type TEXT NOT NULL CHECK (agent_type IN ('interaction', 'execution')),
  user_id TEXT NOT NULL,
  session_data TEXT NOT NULL, -- JSON serialized SDK session state
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_user_type ON agent_sessions(user_id, agent_type);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_last_active ON agent_sessions(last_active);

-- Execution Agent Metadata
-- Tracks lifecycle and status of background execution agents

CREATE TABLE IF NOT EXISTS execution_agents (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL, -- 'hogwarts-analysis', 'overall-analysis', 'commitment-tracker', etc.
  chat_id INTEGER, -- Source iMessage chat (if applicable)
  status TEXT DEFAULT 'running' CHECK (status IN ('running', 'complete', 'failed', 'paused')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP,
  FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_execution_agents_status ON execution_agents(status);
CREATE INDEX IF NOT EXISTS idx_execution_agents_type ON execution_agents(type);
CREATE INDEX IF NOT EXISTS idx_execution_agents_chat ON execution_agents(chat_id);

-- Agent Outputs
-- Links agents to artifacts they create (documents, commitments, triggers)

CREATE TABLE IF NOT EXISTS agent_outputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  output_type TEXT NOT NULL CHECK (output_type IN ('document', 'commitment', 'trigger')),
  output_id TEXT NOT NULL, -- Foreign key to relevant table (chatbot_documents.id, etc.)
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (agent_id) REFERENCES execution_agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_outputs_agent ON agent_outputs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_type ON agent_outputs(output_type, output_id);

-- Triggers (P0 - Critical for proactive features)
-- Scheduled actions that the Interaction Agent executes

CREATE TABLE IF NOT EXISTS triggers (
  id TEXT PRIMARY KEY,
  agent_id TEXT, -- Agent that created it (optional)
  user_id TEXT NOT NULL,
  schedule TEXT NOT NULL, -- Natural language ("every morning", "daily at 9pm")
  cron TEXT NOT NULL, -- Parsed cron expression
  action TEXT NOT NULL, -- What to do when triggered
  next_run TIMESTAMP NOT NULL,
  enabled BOOLEAN DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (agent_id) REFERENCES execution_agents(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_triggers_next_run ON triggers(next_run, enabled);
CREATE INDEX IF NOT EXISTS idx_triggers_user ON triggers(user_id);

-- IA Messages (Nov 2025)
-- Individual message storage for InteractionAgent chat history
-- Enables SSE initial/update pattern (matches backend live_sync)

CREATE TABLE IF NOT EXISTS ia_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  metadata TEXT,  -- JSON: { type, agentId, agentName, etc. }
  FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ia_messages_session ON ia_messages(session_id, timestamp);


