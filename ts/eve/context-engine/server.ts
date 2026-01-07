import express, { Request, Response, NextFunction } from 'express';
import cors from 'cors';
import * as path from 'path';
import * as os from 'os';
import { fileURLToPath } from 'url';
import { ContextEngine, type ExecuteRequest } from './index.js';
import { handleGetDefinitions } from './api/definitions.js';
import { handlePreviewSelection, handleCreateSelection } from './api/selections.js';
import { handleEncodeConversation } from './api/encoding.js';

const PORT = Number(process.env.CONTEXT_ENGINE_PORT || 3031);

export function createServer(baseDir: string) {
  const app = express();
  const engine = new ContextEngine(baseDir);

  app.use(cors());
  app.use(express.json());

  let initialized = false;
  let initErrors: string[] = [];

  const initPromise = engine.initialize().then((result) => {
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

  // Ensure registry is initialized before serving requests
  app.use(async (_req: Request, _res: Response, next: NextFunction) => {
    if (!initialized) {
      await initPromise;
    }
    next();
  });

  app.get('/health', (_req: Request, res: Response) => {
    res.json({ status: 'ok', initialized, errors: initErrors });
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
  app.post('/api/engine/execute', executeHandler); // Alias for compatibility

  app.get('/engine/prompts', (_req: Request, res: Response) => {
    const prompts = engine.getAllPrompts().map((p) => ({
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

  app.get('/engine/packs', (_req: Request, res: Response) => {
    const packs = engine.getAllPacks().map((p) => ({
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
  // Context API - compatibility layer
  // ============================================================================

  app.get('/api/context/definitions', handleGetDefinitions);
  app.post('/api/context/selections/preview', handlePreviewSelection);
  app.post('/api/context/selections', handleCreateSelection);

  // ============================================================================
  // Conversation Encoding Endpoint (used by compute plane)
  // ============================================================================

  app.post('/engine/encode', handleEncodeConversation);
  app.post('/api/engine/encode', handleEncodeConversation); // Alias for compatibility

  const server = app.listen(PORT, () => {
    console.log(`[Engine] Server listening on http://127.0.0.1:${PORT}`);

    // Log database paths for debugging (especially in tests)
    const appDir =
      process.env.CHATSTATS_APP_DIR || path.join(os.homedir(), 'Library', 'Application Support', 'ChatStats');
    console.log('[EVE] ========================================');
    console.log('[EVE] Using appDir:', appDir);
    console.log('[EVE] central.db:', path.join(appDir, 'central.db'));
    console.log('[EVE] transpileCache:', process.env.BUN_RUNTIME_TRANSPILER_CACHE_PATH || '(default)');
    console.log('[EVE] ========================================');
  });

  return { app, server, engine };
}

function isMainModule(): boolean {
  try {
    const selfPath = path.resolve(fileURLToPath(import.meta.url));
    const argvPath = path.resolve(process.argv[1] || '');
    return selfPath === argvPath;
  } catch {
    return false;
  }
}

if (isMainModule()) {
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = path.dirname(__filename);
  const baseDir = path.join(__dirname, '..');
  createServer(baseDir);
}


