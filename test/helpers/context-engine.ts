import { spawn, type ChildProcessWithoutNullStreams } from 'child_process';
import * as net from 'net';
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs/promises';

export type ContextEngineProcess = {
  port: number;
  proc: ChildProcessWithoutNullStreams;
  stop: () => Promise<void>;
};

export async function getFreePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') {
        reject(new Error('Failed to get ephemeral port'));
        return;
      }
      const port = address.port;
      server.close(() => resolve(port));
    });
  });
}

async function sleep(ms: number) {
  await new Promise((r) => setTimeout(r, ms));
}

export async function waitForOk(url: string, maxAttempts = 40, delayMs = 250): Promise<void> {
  let lastErr: unknown = null;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
      lastErr = new Error(`HTTP ${res.status}`);
    } catch (err) {
      lastErr = err;
    }
    await sleep(delayMs);
  }
  throw new Error(`Server not ready: ${url}. Last error: ${String((lastErr as any)?.message || lastErr)}`);
}

export async function startContextEngineServer(opts: {
  repoRoot: string;
  port?: number;
  appDir?: string;
}): Promise<ContextEngineProcess> {
  const port = opts.port ?? (await getFreePort());

  const serverPath = path.join(opts.repoRoot, 'ts', 'eve', 'context-engine', 'server.ts');

  const createdTempAppDir = !opts.appDir;
  const appDir = opts.appDir ?? (await fs.mkdtemp(path.join(os.tmpdir(), 'eve-test-appdir-')));

  const env: Record<string, string> = {
    ...(process.env as any),
    NODE_ENV: 'test',
    CONTEXT_ENGINE_PORT: String(port),
    BUN_RUNTIME_TRANSPILER_CACHE_PATH: '0',
    CHATSTATS_APP_DIR: appDir,
  };

  const proc = spawn('bun', ['run', '--bun', serverPath], {
    cwd: opts.repoRoot,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  proc.stdout.setEncoding('utf8');
  proc.stderr.setEncoding('utf8');

  proc.stdout.on('data', (d) => {
    const text = String(d).trim();
    if (text) console.log(`[EVE] ${text}`);
  });
  proc.stderr.on('data', (d) => {
    const text = String(d).trim();
    if (text) console.error(`[EVE ERR] ${text}`);
  });

  await waitForOk(`http://127.0.0.1:${port}/health`);

  async function stop(): Promise<void> {
    if (proc.killed) return;
    proc.kill('SIGTERM');
    await sleep(500);
    if (!proc.killed) {
      proc.kill('SIGKILL');
    }

    if (createdTempAppDir) {
      await fs.rm(appDir, { recursive: true, force: true }).catch(() => {});
    }
  }

  return { port, proc: proc as ChildProcessWithoutNullStreams, stop };
}


