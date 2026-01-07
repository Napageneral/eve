import { _electron as electron, ElectronApplication, Page } from '@playwright/test';
import { exec, spawn, ChildProcess } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import { setTimeout } from 'timers/promises';
import fs from 'fs';

const execAsync = promisify(exec);

// Track dev servers process (managed by concurrently)
let devServersProcess: ChildProcess | null = null;

// Helper to get run-specific paths
function getRunPath(...parts: string[]): string | null {
  const runDir = process.env.CHATSTATS_TEST_RUN_DIR;
  if (!runDir) return null;
  
  const fullPath = path.join(runDir, ...parts);
  const dir = path.dirname(fullPath);
  
  // Ensure directory exists
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  
  return fullPath;
}

// Log writers (initialized if TEST_RUN_DIR is set)
let electronLogStream: fs.WriteStream | null = null;
let sidecarLogStream: fs.WriteStream | null = null;
let frontendLogStream: fs.WriteStream | null = null;

function writeLog(stream: fs.WriteStream | null, message: string): void {
  if (stream && !stream.destroyed) {
    const timestamp = new Date().toISOString();
    stream.write(`[${timestamp}] ${message}\n`);
  }
}

function initializeLogStreams(): void {
  const electronLogPath = getRunPath('logs', 'electron.log');
  const sidecarLogPath = getRunPath('logs', 'sidecar.log');
  const frontendLogPath = getRunPath('logs', 'frontend.log');
  
  if (electronLogPath) {
    electronLogStream = fs.createWriteStream(electronLogPath, { flags: 'a' });
  }
  if (sidecarLogPath) {
    sidecarLogStream = fs.createWriteStream(sidecarLogPath, { flags: 'a' });
  }
  if (frontendLogPath) {
    frontendLogStream = fs.createWriteStream(frontendLogPath, { flags: 'a' });
  }
}

function closeLogStreams(): void {
  if (electronLogStream) {
    electronLogStream.end();
    electronLogStream = null;
  }
  if (sidecarLogStream) {
    sidecarLogStream.end();
    sidecarLogStream = null;
  }
  if (frontendLogStream) {
    frontendLogStream.end();
    frontendLogStream = null;
  }
}

/**
 * Clean ODU test artifacts (database sessions, workspaces, skills)
 * Based on magic-toolbox test-cleanup strategy
 */
export async function cleanODUTestState(): Promise<void> {
  console.log('[Test]   - Cleaning ODU test state...');

  const appRoot = path.join(__dirname, '../..');
  const oduDbPath = path.join(appRoot, 'eve/odu/database/odu.db');
  const workspacesPath = path.join(appRoot, 'eve/odu/workspaces');
  const skillsPath = path.join(appRoot, 'eve/odu/skills');

  // Clear ODU database
  if (fs.existsSync(oduDbPath)) {
    await execAsync(`rm -f "${oduDbPath}"`, { cwd: appRoot }).catch(() => {});
  }

  // Clear workspaces (except .gitkeep)
  if (fs.existsSync(workspacesPath)) {
    const entries = fs.readdirSync(workspacesPath);
    for (const entry of entries) {
      if (entry !== '.gitkeep') {
        const fullPath = path.join(workspacesPath, entry);
        await execAsync(`rm -rf "${fullPath}"`, { cwd: appRoot }).catch(() => {});
      }
    }
  }

  // Clear skills (except .gitkeep)
  if (fs.existsSync(skillsPath)) {
    const entries = fs.readdirSync(skillsPath);
    for (const entry of entries) {
      if (entry !== '.gitkeep') {
        const fullPath = path.join(skillsPath, entry);
        await execAsync(`rm -rf "${fullPath}"`, { cwd: appRoot }).catch(() => {});
      }
    }
  }
}

export async function cleanAppState(): Promise<void> {
  console.log('[Test] Cleaning app state (same as npm run dev-clean)...');

  const appRoot = path.join(__dirname, '../..');

  // Exactly what npm run dev-clean does:
  // 1. Kill ports
  console.log('[Test]   - Killing ports...');
  await execAsync('node scripts/kill-ports.js', { cwd: appRoot }).catch(() => {});

  // 2. Clear storage (npm run clear-storage)
  console.log('[Test]   - Clearing localStorage and sessionStorage...');
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/Local\\ Storage/*', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/Session\\ Storage/*', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/Preferences', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/Cookies*', { cwd: appRoot }).catch(() => {});

  // 3. Clear DB (npm run clear-db)
  console.log('[Test]   - Clearing databases...');
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/eve.db*', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf ~/.eve', { cwd: appRoot }).catch(() => {});

  // 3.5. Clean ODU test state
  await cleanODUTestState();

  // 4. Nuclear clear (npm run nuclear-clear)
  console.log('[Test]   - Nuclear clear (stopping services)...');
  await execAsync('pkill -f "celery.*worker" || true', { cwd: appRoot }).catch(() => {});
  await execAsync('pkill -f "celery.*beat" || true', { cwd: appRoot }).catch(() => {});
  await execAsync('pkill -f "context-engine/server" || true', { cwd: appRoot }).catch(() => {}); // Old server (legacy)
  await execAsync('pkill -f "bun.*server.ts" || true', { cwd: appRoot }).catch(() => {}); // NEW ODU server
  await execAsync('redis-cli FLUSHALL || true', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf backend/celerybeat-schedule* backend/*.db', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf eve/.claudesdk', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/agents.db*', { cwd: appRoot }).catch(() => {});
  
  // 5. Clean DB files (npm run clean-db-files)
  console.log('[Test]   - Cleaning WAL files...');
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/eve.db-wal', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/eve.db-shm', { cwd: appRoot }).catch(() => {});
  await execAsync('rm -rf ~/Library/Application\\ Support/Eve/eve.db-journal', { cwd: appRoot }).catch(() => {});
  
  console.log('[Test] ✅ Clean wipe completed');
}

async function prepareApp(): Promise<void> {
  const appRoot = path.join(__dirname, '../..');
  
  console.log('[Test] Preparing app (clean + build)...');
  
  // Step 1: npm run clean (remove dist/)
  console.log('[Test]   - Cleaning dist/...');
  await execAsync('npm run clean', { cwd: appRoot });
  
  // Also clean Next.js cache to ensure fresh compilation
  console.log('[Test]   - Cleaning Next.js cache...');
  await execAsync('rm -rf frontend/.next', { cwd: appRoot });
  
  // Kill any lingering Eve processes to force fresh start
  console.log('[Test]   - Force-killing Eve processes...');
  await execAsync('pkill -9 -f "context-engine/server" || true', { cwd: appRoot }).catch(() => {}); // Old server (legacy)
  await execAsync('pkill -9 -f "bun.*server.ts" || true', { cwd: appRoot }).catch(() => {}); // NEW ODU server
  
  // Clear Bun cache to ensure Eve TypeScript changes take effect
  console.log('[Test]   - Clearing Bun module cache...');
  await execAsync('rm -rf eve/node_modules/.cache eve/.bun 2>/dev/null || true', { cwd: appRoot });
  
  // Step 2: npm run build:tsc (compile TypeScript)
  console.log('[Test]   - Building TypeScript...');
  await execAsync('npm run build:tsc', { cwd: appRoot });
  
  console.log('[Test] ✅ App prepared');
}

async function startDevServers(): Promise<void> {
  const appRoot = path.join(__dirname, '../..');
  
  console.log('[Test] Starting dev servers with concurrently...');
  
  // Use concurrently exactly like npm run dev (but skip electron + tsc watch)
  const testUserData = path.join(appRoot, '.test-user-data');
  
  // Ensure the directory exists BEFORE Eve starts so we never write into a path that gets deleted later
  await execAsync(`mkdir -p ${testUserData}`).catch(() => {});
  
  // Force clear Bun cache one more time right before starting Eve
  await execAsync('rm -rf eve/node_modules/.cache eve/.bun 2>/dev/null || true', { cwd: appRoot });
  
  devServersProcess = spawn('npx', [
    'concurrently',
    '--names', 'FRONT,ARTIFACT,EVE',
    '--prefix-colors', 'blue,green,magenta',
    '--kill-others-on-fail',
    'npm run dev:frontend',
    'npm run dev:artifact-runner',
    'npm run dev:eve'
  ], {
    cwd: appRoot,
    stdio: 'pipe', // We'll forward it below with a prefix
    detached: false,
    env: {
      ...process.env,
      // Pass test database path to Eve service
      EVE_APP_DIR: testUserData,
      CHATSTATS_APP_DIR: testUserData, // compat
      // Force Bun to not cache modules
      BUN_CONFIG_NO_CACHE: '1',
      // Set aggressive SQLite timeout for test concurrency
      EVE_SQLITE_BUSY_TIMEOUT_MS: '60000',
      CHATSTATS_SQLITE_BUSY_TIMEOUT_MS: '60000', // compat
    },
  });
  
  // Forward dev-server output so AgentDB/Eve logs are visible in test output
  if (devServersProcess.stdout) {
    devServersProcess.stdout.on('data', (d) => process.stdout.write(`[DEV] ${d}`));
  }
  if (devServersProcess.stderr) {
    devServersProcess.stderr.on('data', (d) => process.stderr.write(`[DEV-ERR] ${d}`));
  }
  
  // Wait for all 3 servers (same as npm run dev)
  console.log('[Test] Waiting for dev servers...');
  console.log('[Test]   - Frontend: http://127.0.0.1:3030');
  console.log('[Test]   - Artifact Runner: http://127.0.0.1:5173');
  console.log('[Test]   - Eve: http://127.0.0.1:3032/health');
  
  await execAsync('npx wait-on http://127.0.0.1:3030 http://127.0.0.1:5173 http://127.0.0.1:3032/health --timeout 60000', {
    cwd: appRoot,
  });
  
  console.log('[Test] ✅ All dev servers ready');
}

export function stopDevServers(): void {
  console.log('[Test] Stopping dev servers...');
  if (devServersProcess) {
    devServersProcess.kill('SIGTERM');
    // Also kill children (concurrently spawns npm processes)
    if (devServersProcess.pid) {
      execAsync(`pkill -P ${devServersProcess.pid}`).catch(() => {});
    }
  }
}

export async function launchApp(): Promise<{ 
  app: ElectronApplication; 
  mainWindow: Page; 
  getSidecarWindow: () => Promise<Page>;
}> {
  // Prepare app (clean + build)
  await prepareApp();
  
  // Ensure clean test userData directory BEFORE any servers start,
  // so we never delete the directory after Eve has opened databases.
  const testUserData = path.resolve(__dirname, '../../.test-user-data'); // MUST be absolute
  console.log(`[Test] Preparing test userData directory: ${testUserData}`);
  await execAsync(`rm -rf ${testUserData}`).catch(() => {});
  await execAsync(`mkdir -p ${testUserData}`).catch(() => {});
  
  // Now start dev servers (Eve will use this directory)
  await startDevServers();
  
  // Initialize log streams if TEST_RUN_DIR is set
  initializeLogStreams();
  
  console.log('[Test] Launching Electron app...');
  console.log('[Test] Electron will manage backend, redis, celery');
  
  console.log(`[Test] Test userData directory (absolute): ${testUserData}`);
  
  const app = await electron.launch({
    args: [
      path.join(__dirname, '../../dist/electron/main.js'),
      `--user-data-dir=${testUserData}`
    ],
    env: {
      ...process.env,
      NODE_ENV: 'development',
      PLAYWRIGHT_TEST: 'true', // Tell Electron we're in E2E test (prevents auto-shutdown)
      // Point Eve's database to test userData directory (MUST be absolute path for child processes)
      EVE_APP_DIR: testUserData,
      CHATSTATS_APP_DIR: testUserData, // compat
      // Disable Bun's runtime transpiler cache to ensure fresh TypeScript compilation in tests
      BUN_RUNTIME_TRANSPILER_CACHE_PATH: '0',
      // Set aggressive SQLite timeout for test concurrency (applies to both Python and Eve)
      EVE_SQLITE_BUSY_TIMEOUT_MS: '60000',
      CHATSTATS_SQLITE_BUSY_TIMEOUT_MS: '60000', // compat
    },
  });
  
  // Track critical errors for fail-fast
  let criticalErrorCount = 0;
  const criticalErrors: string[] = [];
  
  // Capture ALL console output from Electron (backend, eve, celery logs pipe through here)
  app.on('console', msg => {
    const type = msg.type();
    const text = msg.text();
    
    // Write to log file if enabled
    writeLog(electronLogStream, `[${type}] ${text}`);
    
    // Log everything to see Eve agent behavior
    if (type === 'error') {
      console.error('[Electron Console Error]', text);
      
      // Fail fast on critical backend errors
      if (text.includes('Conversation analysis failed: 500') || 
          text.includes('Internal Server Error for url: http://127.0.0.1:3032/engine/encode')) {
        criticalErrorCount++;
        criticalErrors.push(`Eve encoding error: ${text.substring(0, 100)}`);
        if (criticalErrorCount >= 3) {
          console.error('\n❌❌❌ CRITICAL: 3 Eve encode failures detected - test should fail fast');
          console.error('Errors:', criticalErrors);
        }
      }
    } else if (type === 'warning') {
      // Suppress color warnings, they're noise
      if (!text.includes('NO_COLOR')) {
        console.warn('[Electron Warning]', text);
      }
    } else if (type === 'log' || type === 'info') {
      // Capture Eve and backend activity
      if (text.includes('[ExecutionAgent]') || text.includes('[InteractionAgent]') || 
          text.includes('[TriggerScheduler]') || text.includes('[NotificationQueue]') ||
          text.includes('[Callbacks]') || text.includes('POST /api/chat')) {
        console.log('[Eve Activity]', text);
      }
      // Also log database errors
      if (text.includes('database') || text.includes('sqlite') || text.includes('unable to open')) {
        console.error('[Database Issue]', text);
      }
    }
    
    // Critical errors that indicate startup failure
    // BUT: Exclude LLM network errors (expected with bad internet, tasks will retry)
    const isLLMNetworkError = text.includes('GeminiException') || 
                              text.includes('Server disconnected without sending') ||
                              text.includes('APIConnectionError') ||
                              text.includes('litellm.APIConnectionError');
    
    // Worker shutdown detection - this is critical!
    if (text.includes('Warm shutdown') || text.includes('Cold shutdown')) {
      console.error('[WORKER SHUTDOWN DETECTED]', text);
    }
    
    if (!isLLMNetworkError && (
        text.includes('ECONNREFUSED') || text.includes('Cannot find module') || 
        text.includes('failed to load') || text.includes('SyntaxError')
    )) {
      console.error('[CRITICAL ERROR DETECTED]', text);
    }
  });
  
  // Expose critical error status for tests to check
  (globalThis as any).getCriticalErrors = () => ({ count: criticalErrorCount, errors: criticalErrors });
  
  // Get main window with better error handling
  let mainWindow: Page;
  try {
    mainWindow = await app.firstWindow({ timeout: 30000 });
  } catch (err: any) {
    console.error('\n❌ ================================');
    console.error('❌ ELECTRON APP FAILED TO START');
    console.error('❌ ================================\n');
    console.error('Common causes:');
    console.error('  1. TypeScript not compiled (run: npm run build:tsc)');
    console.error('  2. Port conflicts - services already running (run: npm run kill-ports)');
    console.error('  3. Redis/Celery/Backend crashes on startup (Electron starts these)');
    console.error('\nDebugging steps:');
    console.error('  1. Run: npm run build:tsc  (compile TypeScript)');
    console.error('  2. Run: npm run kill-ports  (kill conflicting processes)');
    console.error('  3. Check Electron logs above for specific service failures');
    console.error('  4. Try: npm run dev  (to verify services start manually)');
    console.error('\nOriginal error:', err.message);
    throw new Error('Electron app failed to start. See debugging steps above.');
  }
  
  // Set up frontend console listeners
  mainWindow.on('console', msg => {
    const type = msg.type();
    const text = msg.text();
    
    // Write to log file if enabled
    writeLog(frontendLogStream, `[${type}] ${text}`);
    
    if (type === 'error') {
      console.error('[Frontend Error]', text);
    } else if (type === 'log' || type === 'info') {
      // Capture DiskAccessStep activity (ETL + analysis triggering)
      if (text.includes('[DiskAccessStep]')) {
        console.log('[Frontend]', text);
      }
      // Capture Eve chat activity
      if (text.includes('[EveChatPanel]') || text.includes('[Eve') || text.includes('Eve:')) {
        console.log('[Frontend Eve Activity]', text);
      }
    }
  });
  
  // CRITICAL: Capture page errors that cause test failures
  mainWindow.on('pageerror', error => {
    writeLog(frontendLogStream, `[pageerror] ${error.message}\n${error.stack || ''}`);
    console.error('╔════════════════════════════════════════════════╗');
    console.error('║  🚨 MAIN WINDOW PAGE ERROR (CRITICAL!)  🚨    ║');
    console.error('╚════════════════════════════════════════════════╝');
    console.error(error.message);
    console.error(error.stack);
  });
  
  mainWindow.on('pageerror', error => {
    console.error('[Page Error]', error.message);
    if (error.stack) {
      console.error('[Stack]', error.stack);
    }
  });
  
  // Wait for page to load
  try {
    await mainWindow.waitForLoadState('domcontentloaded', { timeout: 10000 });
  } catch (err: any) {
    console.error('\n❌ Page failed to load within 10 seconds');
    console.error('This usually means a React error prevented initial render.');
    console.error('Check the console errors above for React/JavaScript errors.');
    throw err;
  }
  
  console.log('[Test] ✅ App launched successfully');
  
  // Helper to get sidecar window when it opens
  const getSidecarWindow = async (): Promise<Page> => {
    const windows = app.windows();
    const sidecar = windows.find(w => w.url().includes('/sidecar'));
    
    const setupSidecarListeners = (window: Page) => {
      // Capture ALL console output from sidecar (this is where Eve chat runs)
      window.on('console', msg => {
        const type = msg.type();
        const text = msg.text();
        
        // Write to log file if enabled
        writeLog(sidecarLogStream, `[${type}] ${text}`);
        
        if (type === 'error') {
          console.error('[Sidecar Error]', text);
        } else if (type === 'log' || type === 'info') {
          // Log Eve chat activity
          if (text.includes('[EveChatPanel]') || text.includes('sendEveMessage') || 
              text.includes('Eve responded') || text.includes('POST /api/chat')) {
            console.log('[Sidecar Eve Activity]', text);
          }
          // Log background analysis auto-trigger activity
          if (text.includes('[MasteryAutoTrigger]') || text.includes('[Mastery]')) {
            console.log('[Sidecar Mastery Activity]', text);
          }
        }
      });
      
      window.on('pageerror', error => {
        writeLog(sidecarLogStream, `[pageerror] ${error.message}\n${error.stack || ''}`);
        console.error('╔════════════════════════════════════════════════╗');
        console.error('║  🚨 SIDECAR PAGE ERROR (CRITICAL!)  🚨        ║');
        console.error('╚════════════════════════════════════════════════╝');
        console.error(error.message);
        console.error(error.stack);
      });
      
      // Track network requests to identify 404s and 500s
      window.on('response', response => {
        const status = response.status();
        const url = response.url();
        if (status === 404) {
          console.error(`[Sidecar 404] ${url}`);
        } else if (status >= 500) {
          console.error(`[Sidecar ${status}] ${url}`);
        }
      });
    };
    
    if (sidecar) {
      await sidecar.waitForLoadState('domcontentloaded');
      setupSidecarListeners(sidecar);
      return sidecar;
    }
    
    // Wait for new sidecar window to open
    return new Promise((resolve) => {
      const handler = async (window: Page) => {
        const url = window.url();
        if (url.includes('/sidecar')) {
          await window.waitForLoadState('domcontentloaded');
          setupSidecarListeners(window);
          console.log('[Test] ✅ Sidecar window detected');
          app.removeListener('window', handler);
          resolve(window);
        }
      };
      app.on('window', handler);
    });
  };
  
  // Monitor Electron app lifecycle to detect unexpected exits
  console.log(`[Test] Electron PID: ${app.process().pid}`);
  
  // Simple event listeners (no aggressive polling that might confuse Playwright)
  app.process().on('exit', (code, signal) => {
    console.error(`[Test] ⚠️  Electron process EXITED! Code: ${code}, Signal: ${signal}`);
  });
  
  app.on('close', () => {
    console.error('[Test] ⚠️  Electron app is closing!');
  });
  
  return { app, mainWindow, getSidecarWindow };
}

