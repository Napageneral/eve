/**
 * SSE Reliability Test - Verify Eve's SSE connection NEVER CLOSES
 * 
 * Tests that the SSE endpoint:
 * 1. Accepts connections reliably
 * 2. Sends heartbeats every 15 seconds
 * 3. STAYS CONNECTED PERMANENTLY (runs until Ctrl+C)
 * 4. Detects ANY connection drops immediately
 * 5. Auto-reconnects if dropped (with exponential backoff)
 * 
 * SUCCESS CRITERIA:
 * - Connection stays alive indefinitely
 * - Heartbeats arrive every 15 seconds
 * - If connection drops, auto-reconnects within 5 seconds
 * - No drops for network stability issues
 * 
 * Run: npm run test:sse-reliability
 * Press Ctrl+C to stop
 */

import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

import { EVE_PORT } from '../../eve/config';
const HEARTBEAT_INTERVAL_MS = 15000; // Should match Eve's heartbeat
const HEARTBEAT_TIMEOUT_MS = HEARTBEAT_INTERVAL_MS * 2.5; // Alert if no heartbeat for 37.5s
const REPORT_INTERVAL_MS = 30000; // Report stats every 30 seconds

let eveProcess: ChildProcess | null = null;
let connectionStartTime: number = 0;
let lastHeartbeatTime: number = 0;
let heartbeatCount: number = 0;
let totalDrops: number = 0;
let currentConnectionAlive: boolean = false;
let longestConnectionDuration: number = 0;
let shouldExit: boolean = false;
let reconnectAttempt: number = 0;

/**
 * Start Eve service
 */
async function startEve(): Promise<void> {
  return new Promise((resolve, reject) => {
    const evePath = path.join(__dirname, '../../eve/context-engine/server.ts');
    
    console.log(`[Test] 🚀 Starting Eve service...`);
    console.log(`[Test]    Port: ${EVE_PORT}\n`);
    
    // Use Bun to run server (Eve uses Bun-specific imports)
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
      reject(new Error('Eve service startup timeout (10s)'));
    }, 10000);
    
    // Listen for successful startup
    eveProcess.stdout?.on('data', (data: Buffer) => {
      const output = data.toString();
      
      if (output.includes('Server listening') || output.includes('listening on port')) {
        clearTimeout(startupTimeout);
        console.log(`[Test] ✅ Eve service started\n`);
        resolve();
      }
    });
    
    eveProcess.stderr?.on('data', (data: Buffer) => {
      const output = data.toString();
      if (output.includes('Error') || output.includes('ERROR')) {
        console.error(`[Eve Error] ${output}`);
      }
    });
    
    eveProcess.on('error', (err: Error) => {
      clearTimeout(startupTimeout);
      reject(err);
    });
  });
}

/**
 * Print status report
 */
function printStatus(): void {
  const now = Date.now();
  const uptime = currentConnectionAlive ? Math.round((now - connectionStartTime) / 1000) : 0;
  
  console.log(`\n━━━━━━━━━━━━━━━━━━━━ SSE STATUS ━━━━━━━━━━━━━━━━━━━━`);
  console.log(`Connection: ${currentConnectionAlive ? '✅ ALIVE' : '🔴 DEAD'}`);
  console.log(`Current uptime: ${uptime}s`);
  console.log(`Total heartbeats: ${heartbeatCount}`);
  console.log(`Connection drops: ${totalDrops} ${totalDrops === 0 ? '✅' : '⚠️'}`);
  console.log(`Longest connection: ${Math.round(longestConnectionDuration / 1000)}s`);
  
  if (currentConnectionAlive && lastHeartbeatTime) {
    const timeSinceLastHeartbeat = now - lastHeartbeatTime;
    const status = timeSinceLastHeartbeat < HEARTBEAT_TIMEOUT_MS ? '✅' : '⚠️ LATE';
    console.log(`Last heartbeat: ${Math.round(timeSinceLastHeartbeat / 1000)}s ago ${status}`);
  }
  
  console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n`);
}

/**
 * Connect to SSE (with auto-reconnect on drop)
 */
async function connectOnce(): Promise<void> {
  return new Promise((resolve) => {
    const url = `http://127.0.0.1:${EVE_PORT}/api/chat/stream`;
    
    console.log(`[Connect] Attempt ${reconnectAttempt + 1} → ${url}`);
    
    const thisConnectionStart = Date.now();
    connectionStartTime = thisConnectionStart;
    
    // Use native fetch with SSE stream (Node.js 18+)
    fetch(url, {
      headers: {
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        if (!response.body) {
          throw new Error('No response body');
        }
        
        console.log(`[Connect] ✅ SSE connection established`);
        currentConnectionAlive = true;
        reconnectAttempt = 0; // Reset on successful connection
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        
        // Monitor for heartbeat timeouts
        const healthCheck = setInterval(() => {
          if (!currentConnectionAlive) {
            clearInterval(healthCheck);
            return;
          }
          
          const now = Date.now();
          const timeSinceLastHeartbeat = now - (lastHeartbeatTime || thisConnectionStart);
          
          if (timeSinceLastHeartbeat > HEARTBEAT_TIMEOUT_MS) {
            console.error(`[Monitor] ❌ No heartbeat for ${Math.round(timeSinceLastHeartbeat / 1000)}s! Connection is stale.`);
          }
        }, 10000);
        
        // Read stream
        try {
          while (true) {
            const { done, value } = await reader.read();
            
            if (done) {
              currentConnectionAlive = false;
              const duration = Date.now() - thisConnectionStart;
              
              if (duration > longestConnectionDuration) {
                longestConnectionDuration = duration;
              }
              
              clearInterval(healthCheck);
              totalDrops++;
              
              console.error(`[Connect] ❌ Connection dropped after ${Math.round(duration / 1000)}s (total drops: ${totalDrops})`);
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
                
                if (eventType === 'initial') {
                  console.log(`[SSE] 📨 initial event (history loaded)`);
                } else if (eventType === 'ready') {
                  console.log(`[SSE] 📨 ready event (subscription active)`);
                } else if (eventType === 'heartbeat') {
                  lastHeartbeatTime = Date.now();
                  heartbeatCount++;
                  const uptime = Math.round((lastHeartbeatTime - thisConnectionStart) / 1000);
                  console.log(`[SSE] 💓 Heartbeat #${heartbeatCount} (${uptime}s uptime)`);
                } else if (eventType === 'update') {
                  console.log(`[SSE] 📨 update event (new messages)`);
                }
              }
            }
          }
        } catch (err: any) {
          currentConnectionAlive = false;
          const duration = Date.now() - thisConnectionStart;
          
          if (duration > longestConnectionDuration) {
            longestConnectionDuration = duration;
          }
          
          clearInterval(healthCheck);
          totalDrops++;
          
          console.error(`[Connect] ❌ Stream read error after ${Math.round(duration / 1000)}s:`, err.message);
        }
        
        resolve();
      })
      .catch((err: Error) => {
        console.error(`[Connect] ❌ Failed to connect:`, err.message);
        resolve();
      });
  });
}

/**
 * Main connection loop with auto-reconnect
 */
async function monitorConnection(): Promise<void> {
  console.log(`\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
  console.log(`  SSE PERMANENT CONNECTION MONITOR`);
  console.log(`  Press Ctrl+C to stop`);
  console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n`);
  
  // Status reporting interval
  const statusInterval = setInterval(() => {
    if (!shouldExit) {
      printStatus();
    }
  }, REPORT_INTERVAL_MS);
  
  // Connection loop
  while (!shouldExit) {
    await connectOnce();
    
    if (shouldExit) {
      break;
    }
    
    // Connection dropped - wait before reconnecting (exponential backoff)
    reconnectAttempt++;
    const backoff = Math.min(1000 * Math.pow(2, reconnectAttempt - 1), 10000);
    console.log(`[Reconnect] Waiting ${Math.round(backoff / 1000)}s before reconnecting...`);
    await new Promise(resolve => setTimeout(resolve, backoff));
  }
  
  clearInterval(statusInterval);
}

/**
 * Cleanup
 */
function cleanup(): void {
  shouldExit = true;
  
  if (eveProcess && !eveProcess.killed) {
    console.log('\n[Test] 🛑 Stopping Eve service...');
    eveProcess.kill();
  }
  
  // Final status report
  printStatus();
  
  // Summary
  console.log(`\n━━━━━━━━━━━━━━━━━━ FINAL SUMMARY ━━━━━━━━━━━━━━━━━━`);
  console.log(`Total heartbeats: ${heartbeatCount}`);
  console.log(`Total drops: ${totalDrops}`);
  console.log(`Longest connection: ${Math.round(longestConnectionDuration / 1000)}s`);
  
  if (totalDrops === 0 && heartbeatCount > 0) {
    console.log(`\n✅ SUCCESS - SSE connection was PERMANENT (no drops!)`);
  } else if (totalDrops > 0) {
    console.log(`\n⚠️  WARNING - SSE connection dropped ${totalDrops} time(s)`);
    console.log(`   This should NEVER happen. Investigate SSE reliability.`);
  }
  
  console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n`);
}

/**
 * Main test
 */
async function runTest(): Promise<void> {
  // Handle Ctrl+C gracefully
  process.on('SIGINT', () => {
    console.log('\n[Test] 🛑 Received SIGINT (Ctrl+C)');
    cleanup();
    process.exit(0);
  });
  
  try {
    await startEve();
    await new Promise(resolve => setTimeout(resolve, 2000)); // Wait for Eve to stabilize
    
    await monitorConnection();
    
    cleanup();
    process.exit(totalDrops === 0 ? 0 : 1);
  } catch (err: any) {
    console.error('[Test] ❌ Test failed:', err.message);
    cleanup();
    process.exit(1);
  }
}

// Run test
runTest();
