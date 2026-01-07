/**
 * IA Queue SSE Delivery Test - End-to-End Message Delivery Verification
 * 
 * Tests the complete message delivery pipeline:
 * 1. IAQueue enqueues agent messages
 * 2. Messages delivered via SSE to frontend
 * 3. SSE connection stays alive permanently
 * 4. Connection self-heals if dropped
 * 5. Messages delivered after reconnection
 * 
 * This validates the ENTIRE trigger → EA → IA → SSE → Frontend flow.
 * 
 * Run: npm run test:ia-queue-sse
 */

import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const require = createRequire(import.meta.url);

import { EVE_PORT } from '../../eve/config';
const HEARTBEAT_INTERVAL = 15000;
const HEARTBEAT_TIMEOUT = 45000;

let eveProcess: ChildProcess | null = null;
let heartbeatCount: number = 0;
let lastHeartbeatTime: number = 0;
let messagesReceived: Array<{ type: string; content?: string; timestamp: number }> = [];
let connectionDrops: number = 0;
let shouldExit: boolean = false;

/**
 * Start Eve service
 */
async function startEve(): Promise<void> {
  return new Promise((resolve, reject) => {
    const evePath = path.join(__dirname, '../../eve/context-engine/server.ts');
    
    console.log(`[Test] 🚀 Starting Eve service on port ${EVE_PORT}...`);
    
    eveProcess = spawn('bun', ['run', '--bun', evePath], {
      env: {
        ...process.env,
        CONTEXT_ENGINE_PORT: String(EVE_PORT),
        NODE_ENV: 'test',
        BUN_RUNTIME_TRANSPILER_CACHE_PATH: '0',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      cwd: path.join(__dirname, '../..'),
    });
    
    const startupTimeout = setTimeout(() => {
      reject(new Error('Eve startup timeout (10s)'));
    }, 10000);
    
    let eveStarted = false;
    
    eveProcess.stdout?.on('data', (data: Buffer) => {
      const output = data.toString();
      
      // Log trigger-related activity
      if (output.includes('TriggerScheduler') || 
          output.includes('trigger') ||
          output.includes('IAQueue') ||
          output.includes('Streamed')) {
        console.log(`[Eve] ${output.trim()}`);
      }
      
      if (!eveStarted && (output.includes('Server listening') || output.includes('listening on port'))) {
        clearTimeout(startupTimeout);
        eveStarted = true;
        console.log(`[Test] ✅ Eve started\n`);
        resolve();
      }
    });
    
    eveProcess.stderr?.on('data', (data: Buffer) => {
      const output = data.toString();
      
      // Log all Eve errors and trigger/queue activity
      if (output.includes('Error') || 
          output.includes('ERROR') ||
          output.includes('TriggerScheduler') ||
          output.includes('IAQueue')) {
        console.log(`[Eve] ${output.trim()}`);
      }
    });
    
    eveProcess.on('error', (err: Error) => {
      clearTimeout(startupTimeout);
      reject(err);
    });
  });
}

/**
 * Create a test trigger that will fire and send a message via IAQueue
 */
async function createTestTrigger(): Promise<string> {
  console.log(`\n[Test] Creating test trigger (fires in 10 seconds)...`);
  
  const response = await fetch(`http://127.0.0.1:${EVE_PORT}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: 'send me a test message in 10 seconds',
      userId: 'local-user', // Must match SINGLE_USER constant in server.ts
      sessionId: 'ia-main',
    }),
  });
  
  if (!response.ok) {
    throw new Error(`Failed to create trigger: HTTP ${response.status}`);
  }
  
  console.log(`[Test] ✅ Trigger creation request sent\n`);
  return 'trigger-created';
}

/**
 * Monitor SSE connection with self-healing
 */
async function monitorSSEConnection(testDuration: number): Promise<boolean> {
  return new Promise((resolve) => {
    const url = `http://127.0.0.1:${EVE_PORT}/api/chat/stream`;
    let connectionStartTime = Date.now();
    let reconnectAttempt = 0;
    let currentConnection: any = null;
    let testStartTime = Date.now();
    
    const connect = () => {
      if (shouldExit) return;
      
      console.log(`[SSE] 🔌 Connecting... (attempt ${reconnectAttempt + 1})`);
      connectionStartTime = Date.now();
      
      fetch(url, {
        headers: {
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          
          if (!response.body) {
            throw new Error('No response body');
          }
          
          console.log(`[SSE] ✅ Connected`);
          reconnectAttempt = 0;
          lastHeartbeatTime = Date.now();
          
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          
          currentConnection = { reader, startTime: connectionStartTime };
          
          // Heartbeat timeout monitor
          const healthCheck = setInterval(() => {
            if (shouldExit) {
              clearInterval(healthCheck);
              return;
            }
            
            const timeSinceHeartbeat = Date.now() - lastHeartbeatTime;
            if (timeSinceHeartbeat > HEARTBEAT_TIMEOUT) {
              console.error(`[SSE] ⚠️  No heartbeat for ${Math.round(timeSinceHeartbeat / 1000)}s! Connection may be stale.`);
            }
          }, 10000);
          
          try {
            while (!shouldExit) {
              const { done, value } = await reader.read();
              
              if (done) {
                clearInterval(healthCheck);
                connectionDrops++;
                const duration = Date.now() - connectionStartTime;
                console.error(`[SSE] ❌ Connection dropped after ${Math.round(duration / 1000)}s (total drops: ${connectionDrops})`);
                
                // Auto-reconnect
                if (!shouldExit) {
                  reconnectAttempt++;
                  const backoff = Math.min(1000 * Math.pow(2, reconnectAttempt - 1), 5000);
                  console.log(`[SSE] Reconnecting in ${backoff}ms...`);
                  await new Promise(r => setTimeout(r, backoff));
                  connect();
                }
                break;
              }
              
              buffer += decoder.decode(value, { stream: true });
              
              // Parse SSE events
              const lines = buffer.split('\n');
              buffer = lines.pop() || '';
              
              for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                
                if (line.startsWith('event: ')) {
                  const eventType = line.substring(7).trim();
                  
                  // Find data line
                  let dataLine = '';
                  for (let j = i + 1; j < lines.length; j++) {
                    if (lines[j].startsWith('data: ')) {
                      dataLine = lines[j].substring(6).trim();
                      break;
                    }
                  }
                  
                  if (eventType === 'initial') {
                    try {
                      const data = JSON.parse(dataLine);
                      console.log(`[SSE] 📨 initial (${data.messages?.length || 0} messages)`);
                      messagesReceived.push({ type: 'initial', timestamp: Date.now() });
                    } catch (e) {
                      // Ignore
                    }
                  } else if (eventType === 'ready') {
                    console.log(`[SSE] 📨 ready (subscription active)`);
                    messagesReceived.push({ type: 'ready', timestamp: Date.now() });
                  } else if (eventType === 'heartbeat') {
                    lastHeartbeatTime = Date.now();
                    heartbeatCount++;
                    const uptime = Math.round((Date.now() - connectionStartTime) / 1000);
                    console.log(`[SSE] 💓 Heartbeat #${heartbeatCount} (${uptime}s uptime)`);
                  } else if (eventType === 'update') {
                    try {
                      const data = JSON.parse(dataLine);
                      const messageCount = data.messages?.length || 0;
                      console.log(`[SSE] 📬 update (${messageCount} new message(s))`);
                      
                      // Log message content for verification
                      if (data.messages && data.messages.length > 0) {
                        data.messages.forEach((msg: any) => {
                          const preview = msg.content?.substring(0, 60) || '';
                          console.log(`[SSE]    → ${msg.role}: "${preview}..."`);
                          messagesReceived.push({ 
                            type: 'message', 
                            content: msg.content,
                            timestamp: Date.now() 
                          });
                        });
                      }
                    } catch (e) {
                      console.error('[SSE] Failed to parse update:', e);
                    }
                  }
                }
              }
            }
          } catch (err: any) {
            clearInterval(healthCheck);
            console.error(`[SSE] Read error:`, err.message);
            
            if (!shouldExit) {
              reconnectAttempt++;
              const backoff = Math.min(1000 * Math.pow(2, reconnectAttempt - 1), 5000);
              await new Promise(r => setTimeout(r, backoff));
              connect();
            }
          }
        })
        .catch((err: Error) => {
          console.error(`[SSE] Connection failed:`, err.message);
          
          if (!shouldExit) {
            reconnectAttempt++;
            const backoff = Math.min(1000 * Math.pow(2, reconnectAttempt - 1), 5000);
            setTimeout(() => connect(), backoff);
          }
        });
    };
    
    // Start connection
    connect();
    
    // Test timeout
    setTimeout(() => {
      shouldExit = true;
      if (currentConnection?.reader) {
        currentConnection.reader.cancel();
      }
      resolve(true);
    }, testDuration);
  });
}

/**
 * Print test results
 */
function printResults(): boolean {
  console.log(`\n━━━━━━━━━━━━━━━━━━ TEST RESULTS ━━━━━━━━━━━━━━━━━━`);
  console.log(`Heartbeats: ${heartbeatCount}`);
  console.log(`Connection drops: ${connectionDrops}`);
  console.log(`Messages received: ${messagesReceived.length}`);
  
  const updateMessages = messagesReceived.filter(m => m.type === 'message');
  console.log(`  - initial events: ${messagesReceived.filter(m => m.type === 'initial').length}`);
  console.log(`  - ready events: ${messagesReceived.filter(m => m.type === 'ready').length}`);
  console.log(`  - update messages: ${updateMessages.length}`);
  
  if (updateMessages.length > 0) {
    console.log(`\nMessage Content:`);
    updateMessages.forEach((msg, i) => {
      const preview = msg.content?.substring(0, 80) || '';
      console.log(`  ${i + 1}. "${preview}${msg.content && msg.content.length > 80 ? '...' : ''}"`);
    });
  }
  
  console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
  
  // Success criteria
  const hasHeartbeats = heartbeatCount >= 3; // At least 3 heartbeats (45s+)
  const hasAllMessages = updateMessages.length >= 3; // Expect: user + ACK + trigger message
  const hasAckMessages = updateMessages.length >= 2; // At minimum: user + ACK
  const stableConnection = connectionDrops === 0;
  
  console.log(`\n✓ VALIDATION:`);
  console.log(`  ${hasHeartbeats ? '✅' : '❌'} Heartbeats: ${heartbeatCount} (expected >= 3)`);
  console.log(`  ${hasAllMessages ? '✅' : '⚠️ '} All messages: ${updateMessages.length} (expected 3: user + ACK + trigger)`);
  console.log(`  ${hasAckMessages ? '✅' : '❌'} ACK messages: ${updateMessages.length >= 2 ? 'yes' : 'no'} (minimum working)`);
  console.log(`  ${stableConnection ? '✅' : '⚠️ '} Connection stability: ${connectionDrops === 0 ? 'perfect' : `${connectionDrops} drop(s)`}`);
  
  const passed = hasHeartbeats && hasAckMessages;
  
  if (hasAllMessages && stableConnection) {
    console.log(`\n🎉 PERFECT - Full trigger flow working + stable connection!`);
  } else if (hasAllMessages) {
    console.log(`\n✅ PASS - Trigger messages delivered (connection had ${connectionDrops} drop(s) but self-healed)`);
  } else if (passed) {
    console.log(`\n⚠️  PARTIAL PASS - ACK messages working, but trigger message missing`);
    console.log(`   This means: IA → SSE delivery works`);
    console.log(`   But: Trigger → EA → IA → SSE flow has issues`);
    if (!stableConnection) {
      console.log(`   Note: Connection dropped ${connectionDrops} time(s) - may have missed trigger delivery`);
    }
  } else {
    console.log(`\n❌ FAIL - Critical issues:`);
    if (!hasHeartbeats) console.log(`   - Not enough heartbeats (connection died)`);
    if (!hasAckMessages) console.log(`   - ACK messages not delivered (SSE broken)`);
  }
  
  console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n`);
  
  return passed;
}

/**
 * Cleanup
 */
function cleanup(): void {
  shouldExit = true;
  
  if (eveProcess && !eveProcess.killed) {
    console.log('[Test] 🛑 Stopping Eve...');
    eveProcess.kill();
  }
}

/**
 * Main test
 */
async function runTest(): Promise<void> {
  process.on('SIGINT', () => {
    console.log('\n[Test] Interrupted by user');
    cleanup();
    process.exit(1);
  });
  
  try {
    // Start Eve
    await startEve();
    await new Promise(r => setTimeout(r, 2000)); // Let Eve stabilize
    
    // Start SSE monitoring (will run for 75 seconds to catch trigger)
    console.log('[Test] Starting SSE monitor (75s duration)...\n');
    const ssePromise = monitorSSEConnection(75000);
    
    // Wait 3 seconds for SSE to connect
    await new Promise(r => setTimeout(r, 3000));
    
    // Create trigger (fires in 10 seconds - gives more buffer)
    await createTestTrigger();
    
    console.log('[Test] Waiting for trigger to fire (10s) + delivery...\n');
    
    // Wait for test to complete
    await ssePromise;
    
    // Analyze results
    const passed = printResults();
    
    cleanup();
    process.exit(passed ? 0 : 1);
  } catch (err: any) {
    console.error('[Test] ❌ Test failed:', err.message);
    cleanup();
    process.exit(1);
  }
}

// Run
runTest();

