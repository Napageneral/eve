/**
 * Agent Database Client - Writable SQLite access for agent system
 * 
 * Separate from readonly Eve database client to allow agent persistence.
 * Uses a separate database file (agents.db) to avoid conflicts.
 * 
 * Uses Bun's native SQLite for better performance and compatibility.
 */

import { Database } from 'bun:sqlite';
import * as path from 'path';
import * as os from 'os';
import * as fs from 'fs';

export class AgentDatabaseClient {
  private db: Database;
  private isConnected: boolean = false;

  constructor(dbPath?: string) {
    const finalPath = dbPath || this.getDefaultAgentDbPath();
    
    // Ensure directory exists
    const dir = path.dirname(finalPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    
    // Open in read-write mode (Bun SQLite creates if doesn't exist)
    this.db = new Database(finalPath, { create: true });
    this.isConnected = true;
    
    // Enable WAL mode for better concurrent access
    this.db.exec('PRAGMA journal_mode = WAL');
    
    // Set AGGRESSIVE busy timeout for test environments where concurrent writes happen rapidly
    // Read from env var for consistency with backend
    const busyTimeoutMs = parseInt(process.env.CHATSTATS_SQLITE_BUSY_TIMEOUT_MS || '60000', 10);
    this.db.exec(`PRAGMA busy_timeout = ${busyTimeoutMs}`);
    
    // Use NORMAL synchronous mode for better performance (safer with WAL mode)
    this.db.exec('PRAGMA synchronous = NORMAL');
    
    // Log DB path for debugging (especially useful in tests)
    console.log(`[AgentDB] Opened: ${finalPath}`);
    console.log(`[AgentDB] WAL mode: ${this.db.query('PRAGMA journal_mode').get()}`);
    console.log(`[AgentDB] Busy timeout: ${busyTimeoutMs}ms (from env: ${process.env.CHATSTATS_SQLITE_BUSY_TIMEOUT_MS || 'default'})`);
    
    // Initialize schema
    this.initializeSchema();
  }

  private getDefaultAgentDbPath(): string {
    const appDataDir = process.env.CHATSTATS_APP_DIR || 
      path.join(os.homedir(), 'Library', 'Application Support', 'ChatStats');
    return path.join(appDataDir, 'agents.db');
  }

  private initializeSchema(): void {
    // Use embedded schema to avoid file path issues with ES modules
    // 
    // session_data stores JSON with structure:
    // {
    //   messages?: SDKMessage[];           // Conversation history (Claude Agent SDK format)
    //   lastInvokedAt?: string;            // ISO timestamp of last invocation
    //   recommendation?: string;           // For context agents
    //   taskCompletionSummary?: string;    // For completed tasks
    //   completed_at?: string;             // ISO timestamp of completion
    // }
    const schema = `
CREATE TABLE IF NOT EXISTS agent_sessions (
  id TEXT PRIMARY KEY,
  agent_type TEXT NOT NULL CHECK (agent_type IN ('interaction', 'execution')),
  user_id TEXT NOT NULL,
  session_data TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_user_type ON agent_sessions(user_id, agent_type);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_last_active ON agent_sessions(last_active);

CREATE TABLE IF NOT EXISTS execution_agents (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,
  chat_id INTEGER,
  status TEXT DEFAULT 'running' CHECK (status IN ('running', 'complete', 'failed', 'paused')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP,
  FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_execution_agents_status ON execution_agents(status);
CREATE INDEX IF NOT EXISTS idx_execution_agents_type ON execution_agents(type);
CREATE INDEX IF NOT EXISTS idx_execution_agents_chat ON execution_agents(chat_id);

CREATE TABLE IF NOT EXISTS agent_outputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  output_type TEXT NOT NULL CHECK (output_type IN ('document', 'commitment', 'trigger')),
  output_id TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (agent_id) REFERENCES execution_agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_outputs_agent ON agent_outputs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_type ON agent_outputs(output_type, output_id);

CREATE TABLE IF NOT EXISTS triggers (
  id TEXT PRIMARY KEY,
  agent_id TEXT,
  user_id TEXT NOT NULL,
  schedule TEXT NOT NULL,
  cron TEXT NOT NULL,
  action TEXT NOT NULL,
  next_run TIMESTAMP NOT NULL,
  enabled BOOLEAN DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (agent_id) REFERENCES execution_agents(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_triggers_next_run ON triggers(next_run, enabled);
CREATE INDEX IF NOT EXISTS idx_triggers_user ON triggers(user_id);

CREATE TABLE IF NOT EXISTS ia_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  metadata TEXT,
  FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ia_messages_session ON ia_messages(session_id, timestamp);

CREATE TABLE IF NOT EXISTS ia_notifications (
  id TEXT PRIMARY KEY,
  from_agent_id TEXT NOT NULL,
  from_agent_name TEXT,
  notification_type TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'normal',
  message TEXT NOT NULL,
  metadata TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  user_id TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  processed_at TIMESTAMP,
  delivered_at TIMESTAMP,
  deferred_until TIMESTAMP,
  FOREIGN KEY (from_agent_id) REFERENCES execution_agents(id)
);

CREATE INDEX IF NOT EXISTS idx_ia_notifications_status ON ia_notifications(status);
CREATE INDEX IF NOT EXISTS idx_ia_notifications_user ON ia_notifications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_ia_notifications_priority ON ia_notifications(priority, status);
CREATE INDEX IF NOT EXISTS idx_ia_notifications_deferred ON ia_notifications(deferred_until, status);
    `;
    this.db.exec(schema.trim());
  }

  /**
   * Execute a SELECT query and return all rows
   */
  queryAll<T = any>(sql: string, params: any[] = []): T[] {
    if (!this.isConnected) {
      throw new Error('Agent database client not connected');
    }

    const stmt = this.db.prepare(sql);
    return stmt.all(...params) as T[];
  }

  /**
   * Execute a SELECT query and return a single row
   */
  queryOne<T = any>(sql: string, params: any[] = []): T | null {
    if (!this.isConnected) {
      throw new Error('Agent database client not connected');
    }

    const stmt = this.db.prepare(sql);
    const result = stmt.get(...params);
    return result ? (result as T) : null;
  }

  /**
   * Execute an INSERT/UPDATE/DELETE query
   */
  run(sql: string, params: any[] = []): { changes: number; lastInsertRowid: number } {
    if (!this.isConnected) {
      throw new Error('Agent database client not connected');
    }

    const stmt = this.db.prepare(sql);
    stmt.run(...params);
    return {
      changes: this.db.query('SELECT changes() as changes').get() as any,
      lastInsertRowid: this.db.query('SELECT last_insert_rowid() as id').get() as any
    };
  }

  /**
   * Execute multiple statements in a transaction
   */
  transaction<T>(fn: () => T): T {
    const txn = this.db.transaction(fn);
    return txn();
  }

  /**
   * Get the underlying database instance (for advanced use)
   */
  get raw(): Database {
    return this.db;
  }

  /**
   * Check if connected
   */
  get connected(): boolean {
    return this.isConnected;
  }

  /**
   * Close the database connection
   */
  close(): void {
    if (this.isConnected) {
      this.db.close();
      this.isConnected = false;
    }
  }
}

// Singleton instance
let agentDbInstance: AgentDatabaseClient | null = null;

/**
 * Get or create agent database client instance
 */
export function getAgentDb(dbPath?: string): AgentDatabaseClient {
  if (!agentDbInstance) {
    agentDbInstance = new AgentDatabaseClient(dbPath);
  }
  return agentDbInstance;
}

/**
 * Reset agent database client (useful for testing)
 */
export function resetAgentDb(): void {
  if (agentDbInstance) {
    agentDbInstance.close();
    agentDbInstance = null;
  }
}

