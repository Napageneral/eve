import express, { Request, Response, NextFunction } from 'express';
import cors from 'cors';
import { ContextEngine, type ExecuteRequest } from './index.js';
import * as path from 'path';
import * as os from 'os';
import { handleGetDefinitions } from './api/definitions.js';
import { handlePreviewSelection, handleCreateSelection } from './api/selections.js';
import { handleEncodeConversation } from './api/encoding.js';
import { InteractionAgent } from '../agents/interaction/agent.js';
import { getAgentDb } from '../database/agent-db-client.js';
import { getTriggerScheduler } from '../agents/shared/trigger-scheduler.js';
import { TRIGGER_POLL_INTERVAL_MS } from '../agents/shared/config.js';
import { getMeContact } from '../database/queries/contacts.js';

const PORT = process.env.CONTEXT_ENGINE_PORT || 3031;

// Single-tenant macOS app: ONE user, ONE IA session, always
const SINGLE_USER = 'local-user';
const SINGLE_IA_SESSION = 'ia-main';

// SSE Connection: Used by Electron bridge to forward notifications to renderer
// Electron main process connects to this SSE endpoint and forwards to renderer via IPC
let sseConnection: Response | null = null;
let connectionEstablishedAt: Date | null = null;

function registerSSEConnection(res: Response) {
  sseConnection = res;
  connectionEstablishedAt = new Date();
  console.log(`[SSE] ✅ Registered connection (Electron bridge) at ${connectionEstablishedAt.toISOString()}`);
}

function unregisterSSEConnection() {
  if (sseConnection) {
    const duration = connectionEstablishedAt 
      ? `${Math.round((Date.now() - connectionEstablishedAt.getTime()) / 1000)}s`
      : 'unknown';
    console.log(`[SSE] ❌ Unregistered connection (duration: ${duration})`);
  }
  sseConnection = null;
  connectionEstablishedAt = null;
}

/**
 * Get SSE connection with automatic stale connection cleanup
 * Tests if connection is still writable before returning
 */
export function getSSEConnection(): Response | null {
  if (!sseConnection) {
    console.warn('[SSE] getSSEConnection() called but sseConnection is null');
    return null;
  }
  
  // Test if connection is still alive by checking writableEnded
  if (sseConnection.writableEnded || sseConnection.destroyed) {
    console.warn(`[SSE] ⚠️  Connection exists but is not writable (ended: ${sseConnection.writableEnded}, destroyed: ${sseConnection.destroyed})`);
    unregisterSSEConnection();
    return null;
  }
  
  console.log('[SSE] getSSEConnection() → connection is healthy and writable');
  return sseConnection;
}

export function createServer(baseDir: string) {
  const app = express();
  const engine = new ContextEngine(baseDir);

  app.use(cors());
  app.use(express.json());
  

  let initialized = false;
  let initErrors: string[] = [];

  const initPromise = engine.initialize().then(result => {
    initialized = true;
    initErrors = result.errors;
    console.log(`[Engine] Loaded ${result.promptCount} prompts, ${result.packCount} packs`);
    if (result.errors.length > 0) {
      console.error('[Engine] Errors during load:', result.errors);
    }

    if (process.env.NODE_ENV !== 'production') {
      engine.startWatching(() => {
        console.log('[Engine] Registry reloaded due to file change');
      });
    }

    return result;
  });

  app.use(async (req: Request, res: Response, next: NextFunction) => {
    if (!initialized) {
      await initPromise;
    }
    next();
  });

  app.get('/health', (req: Request, res: Response) => {
    res.json({ status: 'ok', initialized });
  });

  // Execute endpoint - available at both /engine/execute and /api/engine/execute
  const executeHandler = async (req: Request, res: Response) => {
    try {
      const request: ExecuteRequest = req.body;
      const result = await engine.execute(request);

      if ('kind' in result) {
        res.status(400).json(result);
      } else {
        res.json(result);
      }
    } catch (err: any) {
      res.status(500).json({
        kind: 'ValidationError',
        message: err.message,
      });
    }
  };
  
  app.post('/engine/execute', executeHandler);
  app.post('/api/engine/execute', executeHandler);  // Alias for IPC compatibility

  app.get('/engine/prompts', (req: Request, res: Response) => {
    const prompts = engine.getAllPrompts().map(p => ({
      id: p.frontmatter.id,
      name: p.frontmatter.name,
      version: p.frontmatter.version,
      category: p.frontmatter.category,
      tags: p.frontmatter.tags,
      default_pack: p.frontmatter.context.default_pack,
      execution_mode: p.frontmatter.execution.mode,
    }));
    res.json({ prompts });
  });

  app.get('/engine/prompts/:id', (req: Request, res: Response) => {
    const prompt = engine.getPrompt(req.params.id);
    if (!prompt) {
      res.status(404).json({ error: 'Prompt not found' });
      return;
    }
    res.json({
      id: prompt.frontmatter.id,
      frontmatter: prompt.frontmatter,
      body: prompt.body,
      filePath: prompt.filePath,
    });
  });

  app.get('/engine/packs', (req: Request, res: Response) => {
    const packs = engine.getAllPacks().map(p => ({
      id: p.spec.id,
      name: p.spec.name,
      version: p.spec.version,
      category: p.spec.category,
      tags: p.spec.tags,
      flexibility: p.spec.flexibility,
      total_estimated_tokens: p.spec.total_estimated_tokens,
      slices_count: p.spec.slices.length,
    }));
    res.json({ packs });
  });

  app.get('/engine/packs/:id', (req: Request, res: Response) => {
    const pack = engine.getPack(req.params.id);
    if (!pack) {
      res.status(404).json({ error: 'Pack not found' });
      return;
    }
    res.json({
      id: pack.spec.id,
      spec: pack.spec,
      filePath: pack.filePath,
    });
  });

  // ============================================================================
  // Context API - Backend Compatibility Layer
  // ============================================================================

  app.get('/api/context/definitions', handleGetDefinitions);

  // ============================================================================
  // Conversation Encoding Endpoint
  // ============================================================================

  app.post('/engine/encode', handleEncodeConversation);
  app.post('/api/engine/encode', handleEncodeConversation); // Alias for backend compatibility
  app.post('/api/context/selections/preview', handlePreviewSelection);
  app.post('/api/context/selections', handleCreateSelection);

  // ============================================================================
  // Agent System API
  // ============================================================================

  console.log('[Server] Registering /api/chat endpoint...');
  
  // Simple test route
  app.get('/api/test', (req: Request, res: Response) => {
    res.json({ status: 'Eve agent system online' });
  });
  
  // POST /api/chat - Send message to Interaction Agent (SSE streaming)
  app.post('/api/chat', async (req: Request, res: Response) => {
    const { message } = req.body;  // Only message matters - single tenant app
    
    console.log('[ChatAPI] Received request:', {
      user: SINGLE_USER,
      session: SINGLE_IA_SESSION,
      messagePreview: message?.substring(0, 50) + '...',
      timestamp: new Date().toISOString(),
    });
    
    if (!message || typeof message !== 'string') {
      return res.status(400).json({ error: 'Message is required' });
    }
    
    try {
      // Single-tenant app: Always use the same IA session
      console.log('[ChatAPI] Creating InteractionAgent...');
      const agent = new InteractionAgent(SINGLE_USER, SINGLE_IA_SESSION);
      
      console.log('[ChatAPI] Initializing agent...');
      await agent.initialize();
      console.log('[ChatAPI] Agent initialized successfully');
      
      // Track messages count in ia_messages for SSE delta emission
      const db = getAgentDb();
      const beforeCount = db.queryOne<{ count:number }>(
        'SELECT COUNT(*) as count FROM ia_messages WHERE session_id = ?',
        [SINGLE_IA_SESSION]
      )?.count || 0;
      
      // Set up SSE (Server-Sent Events) headers
      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('Connection', 'keep-alive');
      res.flushHeaders();
      
      // Stream messages from the agent (request-scope stream, used only for loading state)
      console.log('[ChatAPI] Starting agent.chat() stream...');
      const stream = agent.chat(message);
      
      let messageCount = 0;
      for await (const sdkMessage of stream) {
        messageCount++;
        console.log(`[ChatAPI] Streaming message ${messageCount}, type: ${sdkMessage.type}`);
        
        // Send each SDK message to the client
        res.write(`data: ${JSON.stringify({ message: sdkMessage })}\n\n`);
        
        // If this is the final result, also send a done event
        if (sdkMessage.type === 'result') {
          res.write(`data: ${JSON.stringify({ done: true })}\n\n`);
        }
      }
      
      console.log(`[ChatAPI] Stream complete, sent ${messageCount} messages`);
      
      // Emit an SSE 'update' on the persistent connection with any new ia_messages
      try {
        const db = getAgentDb();
        const afterCount = db.queryOne<{ count:number }>(
          'SELECT COUNT(*) as count FROM ia_messages WHERE session_id = ?',
          [SINGLE_IA_SESSION]
        )?.count || 0;
        const delta = afterCount - beforeCount;
        if (delta > 0) {
          const rows = db.queryAll<{ id:number; role:string; content:string; timestamp:string; metadata:string|null }>(
            `SELECT id, role, content, timestamp, metadata
             FROM ia_messages
             WHERE session_id = ?
             ORDER BY timestamp DESC
             LIMIT ?`,
            [SINGLE_IA_SESSION, delta]
          ).reverse();
          const formatted = rows.map(r => ({
            id: r.id,
            role: r.role as 'user'|'assistant',
            content: r.content,
            timestamp: r.timestamp,
            metadata: r.metadata ? JSON.parse(r.metadata) : undefined,
          }));
          const sse = getSSEConnection();
          if (sse) {
            sse.write(`event: update\ndata: ${JSON.stringify({ messages: formatted })}\n\n`);
            console.log(`[ChatAPI] Pushed ${formatted.length} ACK message(s) to persistent SSE`);
          } else {
            console.warn('[ChatAPI] No persistent SSE connection to push ACK');
          }
        }
      } catch (pushErr:any) {
        console.error('[ChatAPI] Failed to push ACK update to SSE:', pushErr.message);
      }
      
      // Always send done event when the request-scope stream completes
      res.write(`data: ${JSON.stringify({ done: true })}\n\n`);
      res.end();
      
    } catch (error: any) {
      console.error('[ChatAPI] ========== ERROR ==========');
      console.error('[ChatAPI] Error message:', error.message);
      console.error('[ChatAPI] Error stack:', error.stack);
      console.error('[ChatAPI] Error type:', error.constructor.name);
      console.error('[ChatAPI] ============================');
      res.write(`data: ${JSON.stringify({ error: error.message })}\n\n`);
      res.end();
    }
  });
  
  // TEST endpoint to verify code is loading
  app.get('/agents/test', (req: Request, res: Response) => {
    res.json({ status: 'Agents endpoint code loaded successfully!', timestamp: new Date().toISOString() });
  });
  
  // ENV/runtime diagnostic endpoint (verify test env is working correctly)
  app.get('/env/runtime', (req: Request, res: Response) => {
    res.json({
      bunVersion: process.versions?.bun || 'not running on Bun',
      transpileCache: process.env.BUN_RUNTIME_TRANSPILER_CACHE_PATH ?? '(default)',
      chatstatsDir: process.env.CHATSTATS_APP_DIR || '(default)',
      playwrightTest: process.env.PLAYWRIGHT_TEST === 'true',
      pid: process.pid,
    });
  });
  
  // POST /agents/spawn-post-analysis - Auto-spawn 2 ExecutionAgents after historic analysis completes
  app.post('/agents/spawn-post-analysis', async (req: Request, res: Response) => {
    console.log('[AgentSpawn] ===== START =====');
    console.log('[AgentSpawn] CHATSTATS_APP_DIR:', process.env.CHATSTATS_APP_DIR);
    
    try {
      console.log('[AgentSpawn] Step 1: Getting agent DB');
      const agentDb = getAgentDb(); // For agent sessions/triggers
      console.log('[AgentSpawn] ✓ Got agent DB');
      
      console.log('[AgentSpawn] Step 2: Importing DatabaseClient');
      const { DatabaseClient } = await import('../database/client.js');
      console.log('[AgentSpawn] ✓ Imported DatabaseClient');
      
      // Use explicit path that respects CHATSTATS_APP_DIR
      const appDir = process.env.CHATSTATS_APP_DIR || path.join(os.homedir(), 'Library', 'Application Support', 'ChatStats');
      const mainDbPath = path.join(appDir, 'central.db');
      console.log(`[AgentSpawn] Step 3: Creating DB client with path: ${mainDbPath}`);
      const mainDb = new DatabaseClient(mainDbPath); // Direct connection for test compatibility
      console.log('[AgentSpawn] ✓ Created DB client');
      
      const { generateId } = await import('../agents/shared/utils.js');
      
      // Get top chat from main database (central.db)
      console.log('[AgentSpawn] Querying for top chat...');
      const topChatQuery = `
        SELECT c.id, c.chat_name
        FROM chats c
        JOIN (
          SELECT chat_id, COUNT(*) as msg_count
          FROM messages
          WHERE DATE(timestamp) >= DATE('now', '-1 year')
          GROUP BY chat_id
          ORDER BY msg_count DESC
          LIMIT 1
        ) ranked ON c.id = ranked.chat_id
      `;
      
      const topChat = mainDb.queryOne(topChatQuery);
      console.log('[AgentSpawn] Top chat result:', topChat);
      
      if (!topChat) {
        console.warn('[AgentSpawn] No top chat found');
        return res.status(404).json({ 
          success: false,
          error: 'No chats found for analysis' 
        });
      }
      
      const chatId = topChat.id;
      const chatName = topChat.chat_name || `Chat ${chatId}`;
      console.log(`[AgentSpawn] Top chat: ${chatName} (ID: ${chatId})`);
      
      // Get user name from contacts table (where is_me = 1)
      let userName = 'You';
      const meContact = getMeContact(mainDb);
      if (meContact?.name) {
        userName = meContact.name;
      } else if (meContact?.nickname) {
        userName = meContact.nickname;
      }
      
      const SINGLE_USER = 1; // Single-user app mode
      const spawnedAgents = [];
      
      // Generate IDs upfront
      const intentionsAgentId = generateId();
      const intentionsSessionId = `ea-intentions-${Date.now()}`;
      const overallAgentId = generateId();
      const overallSessionId = `ea-overall-${Date.now() + 1}`; // +1 to avoid collision
      
      // Wrap all DB inserts in a single transaction to avoid "disk I/O error"
      // AgentDatabaseClient.transaction() executes immediately - no need to call result
      console.log('[AgentSpawn] Creating agent records in transaction...');
      agentDb.transaction(() => {
        // EA #1: Intentions Analysis
        agentDb.run(
          `INSERT INTO agent_sessions (id, agent_type, user_id, session_data, created_at, last_active)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)`,
          [intentionsSessionId, 'execution', SINGLE_USER, JSON.stringify({
            auto_spawned: true,
            spawn_reason: 'post_analysis_intentions',
            chat_id: chatId,
            chat_name: chatName,
          })]
        );
        
        agentDb.run(
          `INSERT INTO execution_agents (id, session_id, type, chat_id, status, created_at)
           VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP)`,
          [intentionsAgentId, intentionsSessionId, 'intentions-analysis', chatId]
        );
        
        // EA #2: Overall Analysis
        agentDb.run(
          `INSERT INTO agent_sessions (id, agent_type, user_id, session_data, created_at, last_active)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)`,
          [overallSessionId, 'execution', SINGLE_USER, JSON.stringify({
            auto_spawned: true,
            spawn_reason: 'post_analysis_overall',
            chat_id: chatId,
            chat_name: chatName,
          })]
        );
        
        agentDb.run(
          `INSERT INTO execution_agents (id, session_id, type, chat_id, status, created_at)
           VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP)`,
          [overallAgentId, overallSessionId, 'overall-analysis', chatId]
        );
      });
      console.log(`[AgentSpawn] ✅ Transaction executed successfully - created 2 ExecutionAgent records`);
      
      // Import ExecutionAgent class
      const { ExecutionAgent } = await import('../agents/execution/agent.js');
      
      // Spawn EA #1: Intentions Analysis (in background)
      console.log('[AgentSpawn] Spawning Intentions ExecutionAgent...');
      const intentionsAgent = new ExecutionAgent({
        agentId: intentionsAgentId,
        userId: SINGLE_USER,
        type: 'delegated',
        agentName: `${chatName} Intentions`,
        chatId: chatId,
        description: `Using the intentions-narrative-v2 prompt, analyze the intentions and relationship patterns for ${chatName}. Call load_context with the intentions-narrative-v2 pack and create a comprehensive intentions document.`,
      }, intentionsSessionId);
      
      intentionsAgent.execute().catch(err => {
        console.error(`[AgentSpawn] Intentions EA ${intentionsAgentId} failed:`, err);
      });
      
      spawnedAgents.push({
        id: intentionsAgentId,
        type: 'intentions',
        chat_id: chatId,
        chat_name: chatName,
      });
      
      // Spawn EA #2: Overall Analysis (in background)
      console.log('[AgentSpawn] Spawning Overall Analysis ExecutionAgent...');
      const overallAgent = new ExecutionAgent({
        agentId: overallAgentId,
        userId: SINGLE_USER,
        type: 'delegated',
        agentName: `${chatName} : Overall Analysis`,
        chatId: chatId,
        description: `Using the overall-v1 prompt, create a comprehensive analysis of ${chatName}. Call load_context with the analyses-year-personality pack and create a detailed overall analysis document titled "${chatName} : Overall Analysis".`,
      }, overallSessionId);
      
      overallAgent.execute().catch(err => {
        console.error(`[AgentSpawn] Overall EA ${overallAgentId} failed:`, err);
      });
      
      spawnedAgents.push({
        id: overallAgentId,
        type: 'overall',
        chat_id: chatId,
        chat_name: chatName,
      });
      
      console.log(`[AgentSpawn] ✅ Spawned ${spawnedAgents.length} ExecutionAgents`);
      
      res.json({
        success: true,
        agents: spawnedAgents,
      });
      
    } catch (error: any) {
      console.error('[AgentSpawn] ===== ERROR =====');
      console.error('[AgentSpawn] Error:', error.message);
      console.error('[AgentSpawn] Stack:', error.stack);
      console.error('[AgentSpawn] =================');
      
      // Include diagnostic info in response
      const appDir = process.env.CHATSTATS_APP_DIR || path.join(os.homedir(), 'Library', 'Application Support', 'ChatStats');
      const mainDbPath = path.join(appDir, 'central.db');
      
      res.status(500).json({ 
        success: false,
        error: error.message,
        stack: error.stack,
        diagnostics: {
          CHATSTATS_APP_DIR: process.env.CHATSTATS_APP_DIR,
          computed_db_path: mainDbPath,
          error_line: error.stack?.split('\n')[1]
        }
      });
    }
  });

  // GET /api/chat/stream - Persistent SSE connection (matches backend live_sync pattern)
  // Frontend connects directly via EventSource (same as backend SSE)
  // Sends initial history + streams updates (initial/update/ready/heartbeat events)
  app.get('/api/chat/stream', (req: Request, res: Response) => {
    console.log(`[SSE] 🔌 New connection request from ${req.ip || 'unknown'}`);
    
    // Disable any timeout on this connection (keep alive indefinitely)
    req.socket.setTimeout(0);
    req.socket.setNoDelay(true);
    req.socket.setKeepAlive(true);
    
    // Set up SSE headers (matches backend live_sync pattern)
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no', // Disable nginx buffering
    });
    
    // Flush headers immediately
    res.flushHeaders();
    
    // 1. Send initial message history (matches backend's initial event)
    try {
      const db = getAgentDb();
      const history = db.queryAll<{ id: number; role: string; content: string; timestamp: string; metadata: string | null }>(
        `SELECT id, role, content, timestamp, metadata 
         FROM ia_messages 
         WHERE session_id = ? 
         ORDER BY timestamp ASC`,
        [SINGLE_IA_SESSION]  // Single-tenant: always use ia-main session
      );
      
      const messages = history.map(row => ({
        id: row.id,
        role: row.role,
        content: row.content,
        timestamp: row.timestamp,
        metadata: row.metadata ? JSON.parse(row.metadata) : undefined,
      }));
      
      res.write(`event: initial\ndata: ${JSON.stringify({ messages })}\n\n`);
      console.log(`[SSE] Sent initial history: ${messages.length} messages`);
    } catch (err: any) {
      console.error('[SSE] Failed to send initial history:', err.message);
      // Send empty initial if DB query fails (don't crash on first connection)
      res.write(`event: initial\ndata: ${JSON.stringify({ messages: [] })}\n\n`);
    }
    
    // 2. Send ready event (subscription active)
    try {
      res.write(`event: ready\ndata: ${JSON.stringify({ status: 'subscribed' })}\n\n`);
      res.flush?.();
    } catch (err: any) {
      console.error('[SSE] Failed to send ready event:', err.message);
      return;
    }
    
    // 3. Register the single connection (for future update events)
    registerSSEConnection(res);
    console.log('[SSE] ✅ Connection registered and ready');
    
    // 4. Heartbeat every 15 seconds to keep connection alive (matches backend pattern)
    const heartbeat = setInterval(() => {
      if (res.writableEnded || res.destroyed) {
        console.warn('[SSE] ⚠️  Response ended/destroyed, cleaning up heartbeat');
        clearInterval(heartbeat);
        unregisterSSEConnection();
        return;
      }
      
      try {
        // Send heartbeat event (matches backend: event: heartbeat, data: ping)
        const success = res.write(`event: heartbeat\ndata: ping\n\n`);
        if (!success) {
          console.warn('[SSE] ⚠️  Heartbeat write buffered (backpressure)');
        }
      } catch (err: any) {
        console.error('[SSE] ❌ Heartbeat failed:', err.message);
        clearInterval(heartbeat);
        unregisterSSEConnection();
      }
    }, 15000);
    
    // Handle connection errors
    res.on('error', (err: any) => {
      console.error('[SSE] ❌ Connection error:', err.message);
      clearInterval(heartbeat);
      unregisterSSEConnection();
    });
    
    // Cleanup on disconnect
    req.on('close', () => {
      clearInterval(heartbeat);
      unregisterSSEConnection();
      console.log(`[SSE] 🔌 Connection closed by client`);
    });
    
    // Double-check cleanup on response finish
    res.on('finish', () => {
      clearInterval(heartbeat);
      unregisterSSEConnection();
      console.log('[SSE] 🔌 Connection finished');
    });
    
    // Keep the request handler alive - don't return or call res.end()
    // The intervals and event handlers will keep this connection open
  });

  // GET /api/chat/history - Get conversation history
  app.get('/api/chat/history', (req: Request, res: Response) => {
    const { userId = 'local-user', sessionId } = req.query;
    
    try {
      const db = getAgentDb();
      
      // Get agent session
      const session = sessionId 
        ? db.queryOne(
            'SELECT * FROM agent_sessions WHERE id = ? AND agent_type = ?',
            [sessionId, 'interaction']
          )
        : db.queryOne(
            'SELECT * FROM agent_sessions WHERE user_id = ? AND agent_type = ? ORDER BY last_active DESC LIMIT 1',
            [userId, 'interaction']
          );
      
      if (!session) {
        return res.json({ messages: [], sessionId: null });
      }
      
      // For now, return basic session info
      // The full chat history is managed by the Claude SDK session
      res.json({ 
        sessionId: session.id,
        created: session.created_at,
        lastActive: session.last_active,
        messages: [] // TODO: Extract from SDK session if needed
      });
      
    } catch (error: any) {
      console.error('[ChatAPI] Error fetching history:', error);
      res.status(500).json({ error: error.message });
    }
  });

  // GET /api/debug/agents - List execution agents
  app.get('/api/debug/agents', (req: Request, res: Response) => {
    try {
      const db = getAgentDb();
      const agents = db.queryAll(`
        SELECT ea.*, ass.session_data 
        FROM execution_agents ea
        LEFT JOIN agent_sessions ass ON ea.session_id = ass.id
        ORDER BY ea.created_at DESC
        LIMIT 100
      `);
      res.json({ agents });
    } catch (error: any) {
      console.error('[DebugAPI] Error:', error.message);
      res.status(500).json({ error: error.message });
    }
  });

  // GET /api/debug/agents/:id - Get specific agent
  app.get('/api/debug/agents/:id', async (req: Request, res: Response) => {
    try {
      const db = getAgentDb();
      const agent = db.queryOne(
        'SELECT * FROM execution_agents WHERE id = ?',
        [req.params.id]
      );
      
      if (!agent) {
        return res.status(404).json({ error: 'Agent not found' });
      }
      
      const session = db.queryOne(
        'SELECT * FROM agent_sessions WHERE id = ?',
        [(agent as any).session_id]
      );
      
      const outputs = db.queryAll(
        'SELECT * FROM agent_outputs WHERE agent_id = ?',
        [req.params.id]
      );
      
      res.json({ agent, session, outputs });
    } catch (error: any) {
      console.error('[DebugAPI] Error:', error);
      console.error('[DebugAPI] Stack:', error.stack);
      res.status(500).json({ error: error.message, stack: error.stack });
    }
  });

  // GET /api/debug/triggers - List all triggers
  app.get('/api/debug/triggers', (req: Request, res: Response) => {
    try {
      const db = getAgentDb();
      const triggers = db.queryAll(`
        SELECT * FROM triggers 
        ORDER BY next_run ASC
      `);
      res.json({ triggers });
    } catch (error: any) {
      console.error('[DebugAPI] Error:', error.message);
      res.status(500).json({ error: error.message });
    }
  });
  

  // GET /api/debug/scheduler - Get scheduler status
  app.get('/api/debug/scheduler', (req: Request, res: Response) => {
    try {
      const scheduler = getTriggerScheduler();
      const status = scheduler.getStatus();
      res.json(status);
    } catch (error: any) {
      console.error('[DebugAPI] Error:', error);
      res.status(500).json({ error: error.message });
    }
  });

  const server = app.listen(PORT, () => {
    console.log(`[Engine] Server listening on http://127.0.0.1:${PORT}`);
    
    // Log database paths for debugging (especially in tests)
    const appDir = process.env.CHATSTATS_APP_DIR || path.join(os.homedir(), 'Library', 'Application Support', 'ChatStats');
    console.log('[EVE] ========================================');
    console.log('[EVE] Using appDir:', appDir);
    console.log('[EVE] central.db:', path.join(appDir, 'central.db'));
    console.log('[EVE] agents.db :', path.join(appDir, 'agents.db'));
    console.log('[EVE] transpileCache:', process.env.BUN_RUNTIME_TRANSPILER_CACHE_PATH || '(default)');
    console.log('[EVE] ========================================');
    
    // Start the trigger scheduler
    const scheduler = getTriggerScheduler(TRIGGER_POLL_INTERVAL_MS);
    scheduler.start();
  });

  return { app, server, engine };
}

// ES module equivalent of require.main === module
if (import.meta.url === `file://${process.argv[1]}`) {
  const baseDir = path.join(import.meta.dirname, '..');
  createServer(baseDir);
}

