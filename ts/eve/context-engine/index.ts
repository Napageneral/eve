import * as path from 'path';
import * as yaml from 'js-yaml';
import * as fs from 'fs';
import { Registry, type LoadedPrompt, type LoadedPack } from './registry.js';
import { RETRIEVAL_ADAPTERS, type RetrievalContext } from './retrieval/index.js';
import type { PackSlice } from './schemas/packSpec.zod.js';

// Control logging verbosity (set DEBUG_CONTEXT_ENGINE=1 to enable detailed logs)
const DEBUG = process.env.DEBUG_CONTEXT_ENGINE === '1';

export type PlanFailureKind = 
  | 'MissingVariable'
  | 'MissingData'
  | 'BudgetExceeded'
  | 'RetrievalError'
  | 'ValidationError'
  | 'PromptNotFound'
  | 'PackNotFound';

export interface PlanFailure {
  kind: PlanFailureKind;
  message: string;
  currentTokens?: number;
  budget?: number;
  tried?: string[];
  suggest?: Array<{ action: string; description?: string }>;
}

export interface LedgerItem {
  slice: string;
  packId: string;
  estTokens: number;
  why?: string;
  encoding?: string;
}

export interface ContextLedger {
  totalTokens: number;
  items: LedgerItem[];
}

export interface HiddenPart {
  name: string;
  text: string;
}

export interface ExecuteResult {
  ledger: ContextLedger;
  hiddenParts: HiddenPart[];
  visiblePrompt: string;
  execution: {
    mode: string;
    resultType: string;
    resultTitle?: string;
    modelPreferences?: string[];
    temperature?: number;
    maxTokens?: number;
    fallbackModels?: string[];      // Models to try on parse failure
    retryOnParseFailure?: boolean;  // Enable automatic retry
  };
  responseSchema?: Record<string, any>;  // JSON schema for structured LLM responses
}

export interface ExecuteRequest {
  promptId: string | null;
  sourceChat?: number;
  vars?: Record<string, any>;
  overridePackId?: string;
  overridePackSpec?: {
    id: string;
    name?: string;
    description?: string;
    slices: any[];
  };
  budgetTokens?: number;
}

interface CompiledPart {
  name: string;
  text: string;
  estTokens: number;
  packId: string;
  why?: string;
  encoding?: string;
}

export class ContextEngine {
  private registry: Registry;
  private config: any;
  private baseDir: string;
  private dbPath?: string;

  constructor(baseDir: string, dbPath?: string) {
    this.baseDir = baseDir;
    this.dbPath = dbPath;
    this.registry = new Registry(baseDir);
    
    const configPath = path.join(baseDir, 'context-engine', 'config.yaml');
    if (fs.existsSync(configPath)) {
      this.config = yaml.load(fs.readFileSync(configPath, 'utf-8'));
    } else {
      this.config = { safety_factors: { default: 0.90 }, category_defaults: {} };
    }
  }

  async initialize(): Promise<{ promptCount: number; packCount: number; errors: string[] }> {
    return await this.registry.load();
  }

  startWatching(onChange: () => void): void {
    this.registry.startWatching(onChange);
  }

  stopWatching(): void {
    this.registry.stopWatching();
  }

  async execute(request: ExecuteRequest): Promise<ExecuteResult | PlanFailure> {
    if (DEBUG) {
      console.log('[ContextEngine] Execute request:', {
        promptId: request.promptId,
        sourceChat: request.sourceChat,
        budgetTokens: request.budgetTokens || 300000,
        varsKeys: Object.keys(request.vars || {}),
        hasOverridePackSpec: !!request.overridePackSpec
      });
    }
    
    // Handle in-memory pack specs (for delegated agents with context agents)
    let prompt: LoadedPrompt | null = null;
    let pack: LoadedPack | null = null;
    let packId: string;
    
    if (request.overridePackSpec) {
      // Use in-memory pack specification
      packId = request.overridePackSpec.id;
      pack = {
        id: packId,
        spec: {
          id: packId,
          name: request.overridePackSpec.name || packId,
          description: request.overridePackSpec.description,
          slices: request.overridePackSpec.slices,
          total_estimated_tokens: request.overridePackSpec.slices.reduce((sum, s) => sum + (s.budget || 0), 0),
        },
        content: '',
      };
      
      if (DEBUG) {
        console.log('[ContextEngine] Using in-memory pack spec:', {
          packId,
          sliceCount: pack.spec.slices.length,
          estimatedTotalTokens: pack.spec.total_estimated_tokens
        });
      }
      
      // For in-memory packs, we don't need a prompt (used by delegated agents)
      // Create a minimal prompt structure
      if (!request.promptId) {
        prompt = {
          id: 'dynamic',
          frontmatter: {
            id: 'dynamic',
            title: 'Dynamic Analysis',
            description: 'Dynamic agent analysis',
            version: '1.0.0',
            prompt: {
              source: 'markdown',
            },
            execution: {
              mode: 'direct',
              result_type: 'document',
            },
            context: {
              default_pack: packId,
              always_on: [],
            },
          },
          visiblePrompt: '',
          body: '', // Empty body for delegated agents (they have their own system prompt)
          content: '',
        };
      } else {
        prompt = this.registry.getPrompt(request.promptId);
        if (!prompt) {
          console.error('[ContextEngine] Prompt not found:', request.promptId);
          return {
            kind: 'PromptNotFound',
            message: `Prompt not found: ${request.promptId}`,
            suggest: [{ action: 'list_prompts', description: 'Run prompts:list to see available prompts' }],
          };
        }
      }
    } else {
      // Standard flow: load prompt and pack from registry
      if (!request.promptId) {
        return {
          kind: 'ValidationError',
          message: 'Either promptId or overridePackSpec must be provided',
        };
      }
      
      prompt = this.registry.getPrompt(request.promptId);
      if (!prompt) {
        console.error('[ContextEngine] Prompt not found:', request.promptId);
        return {
          kind: 'PromptNotFound',
          message: `Prompt not found: ${request.promptId}`,
          suggest: [{ action: 'list_prompts', description: 'Run prompts:list to see available prompts' }],
        };
      }

      packId = request.overridePackId || prompt.frontmatter.context.default_pack;
      pack = this.registry.getPack(packId);
      if (!pack) {
        console.error('[ContextEngine] Pack not found:', packId);
        return {
          kind: 'PackNotFound',
          message: `Pack not found: ${packId}`,
          suggest: [{ action: 'list_packs', description: 'Run packs:list to see available packs' }],
        };
      }
      
      if (DEBUG) {
        console.log('[ContextEngine] Using pack:', {
          packId,
          sliceCount: pack.spec.slices.length,
          estimatedTotalTokens: pack.spec.total_estimated_tokens
        });
      }
    }

    const context: RetrievalContext = {
      sourceChat: request.sourceChat,
      vars: request.vars || {},
      prompt,  // Pass prompt for variable substitution
      dbPath: this.dbPath,  // Pass database path for direct access
    };

    if (request.sourceChat && !context.vars.chat_title) {
      context.vars.chat_title = `Chat ${request.sourceChat}`;
    }

    const compiled = await this.compileSlices(pack.spec.slices, context, packId);
    if ('kind' in compiled) {
      return compiled;
    }

    // Compile always-on packs (skip for in-memory packs)
    let alwaysOnParts: { parts: CompiledPart[] };
    if (request.overridePackSpec) {
      // In-memory packs don't have always-on parts
      alwaysOnParts = { parts: [] };
    } else {
      const alwaysOnResult = await this.compileAlwaysOn(prompt!, context);
      if ('kind' in alwaysOnResult) {
        return alwaysOnResult;
      }
      alwaysOnParts = alwaysOnResult;
    }

    const budget = request.budgetTokens || 300000; // Default to 300k (270k after 0.9 safety factor)
    const safetyFactor = this.getSafetyFactor(prompt!.frontmatter.execution.model_preferences?.[0]);
    const effectiveBudget = Math.floor(budget * safetyFactor);

    const allParts = [...alwaysOnParts.parts, ...compiled.parts];
    const totalTokens = allParts.reduce((sum, p) => sum + p.estTokens, 0);
    
    if (DEBUG) {
      console.log('[ContextEngine] Budget check:', {
        requestedBudget: budget,
        safetyFactor,
        effectiveBudget,
        compiledPartsCount: compiled.parts.length,
        alwaysOnPartsCount: alwaysOnParts.parts.length,
        totalPartsCount: allParts.length,
        totalTokens,
        exceedsBudget: totalTokens > effectiveBudget,
        partsBreakdown: allParts.map(p => ({
          name: p.name,
          estTokens: p.estTokens,
          packId: p.packId
        }))
      });
    }

    /**
     * Budget fitting - STUBBED for v1
     * 
     * Current behavior: Just checks if total tokens exceed budget and returns BudgetExceeded error.
     * 
     * Future implementation (v2):
     * 1. shrink_time: Try shorter time ranges (year → six_months → quarter → month)
     *    - Read pack.trimming.steps for shrink_time configurations
     *    - For each slice with time params, try progressively shorter presets
     *    - Recompile and check if under budget
     * 
     * 2. compress_encoding: Try more compact encodings (compact_json → ultra_compact)
     *    - Read pack.trimming.steps for compress_encoding configurations
     *    - For each slice with encode params, try more compact formats
     *    - Recompile and check if under budget
     * 
     * 3. drop_candidates: Drop optional slices (respect pack.trimming.hard_requirements)
     *    - Read pack.trimming.steps for drop_candidates configurations
     *    - Drop slices one at a time (preserve hard_requirements)
     *    - Recompile and check if under budget
     * 
     * 4. try alternatives: Use alternative packs from prompt.context.alternatives
     *    - If prompt.context_flexibility is 'high' or 'medium'
     *    - Try each alternative pack in order
     *    - Compile with alternative and check if under budget
     * 
     * 5. semantic_compression: Use LLM to summarize/compress context (v3+)
     *    - Call LLM to compress large slices while preserving key information
     *    - Token-aware summarization
     * 
     * Implementation notes:
     * - Apply category defaults from config.yaml if pack doesn't define trimming
     * - Safety factors already applied (effectiveBudget = budget * safetyFactor)
     * - Track tried strategies in PlanFailure.tried array
     * - Always return actionable suggestions in PlanFailure.suggest
     * 
     * See original design doc section "Token Budgeting with Safety Margins" for full spec.
     */
    if (totalTokens > effectiveBudget) {
      console.error('[ContextEngine] ❌ Budget exceeded!', {
        totalTokens,
        effectiveBudget,
        requestedBudget: budget,
        overage: totalTokens - effectiveBudget,
        percentOver: Math.round(((totalTokens - effectiveBudget) / effectiveBudget) * 100)
      });
      
      return {
        kind: 'BudgetExceeded',
        message: `Context exceeds budget: ${totalTokens} tokens > ${effectiveBudget} budget (safety factor: ${safetyFactor})`,
        currentTokens: totalTokens,
        budget: effectiveBudget,
        tried: ['safety_margin_applied'],
        suggest: [
          { action: 'pick_alternative', description: 'Try an alternative pack with less context' },
          { action: 'reduce_time_range', description: 'Use a shorter time range (e.g., month instead of year)' },
        ],
      };
    }

    const ledger: ContextLedger = {
      totalTokens,
      items: allParts.map(p => ({
        slice: p.name,
        packId: p.packId,
        estTokens: p.estTokens,
        why: p.why,
        encoding: p.encoding,
      })),
    };
    
    if (DEBUG) {
      console.log('[ContextEngine] ✅ Budget check passed!', {
        totalTokens,
        effectiveBudget,
        utilizationPercent: Math.round((totalTokens / effectiveBudget) * 100),
        ledgerItemCount: ledger.items.length
      });
    }

    return this.buildResult(prompt, allParts, ledger, context);
  }

  private async compileSlices(
    slices: PackSlice[],
    context: RetrievalContext,
    packId: string
  ): Promise<{ parts: CompiledPart[] } | PlanFailure> {
    const parts: CompiledPart[] = [];

    for (const slice of slices) {
      const adapter = RETRIEVAL_ADAPTERS[slice.retrieval];
      if (!adapter) {
        return {
          kind: 'ValidationError',
          message: `Unknown retrieval function: ${slice.retrieval}`,
        };
      }

      try {
        const result = await adapter(slice.params, context);
        parts.push({
          name: slice.name,
          text: result.text,
          estTokens: result.actualTokens || slice.estimated_tokens,
          packId,
          why: slice.why_include,
          encoding: slice.encoding_explainer === 'auto' ? 'auto' : slice.encoding_explainer,
        });
      } catch (err: any) {
        return {
          kind: 'RetrievalError',
          message: `Failed to retrieve slice ${slice.name}: ${err.message}`,
        };
      }
    }

    return { parts };
  }

  private async compileAlwaysOn(
    prompt: LoadedPrompt,
    context: RetrievalContext
  ): Promise<{ parts: CompiledPart[] } | PlanFailure> {
    const parts: CompiledPart[] = [];
    const alwaysOn = prompt.frontmatter.always_on || [];

    for (const packId of alwaysOn) {
      const pack = this.registry.getPack(packId);
      if (!pack) {
        console.warn(`[Engine] Always-on pack not found: ${packId}`);
        continue;
      }

      const compiled = await this.compileSlices(pack.spec.slices, context, packId);
      if ('kind' in compiled) {
        return compiled;
      }

      parts.push(...compiled.parts);
    }

    return { parts };
  }

  private buildResult(prompt: LoadedPrompt, parts: CompiledPart[], ledger: ContextLedger, context: RetrievalContext): ExecuteResult {
    let visiblePrompt: string;
    if (prompt.frontmatter.prompt.source === 'markdown') {
      visiblePrompt = prompt.body;
    } else {
      visiblePrompt = prompt.body || '(Dynamic prompt - see TS function)';
    }
    
    // Substitute variables in the prompt body
    // Supports both {{var}} and {{{var}}} syntax
    visiblePrompt = this.substituteVariables(visiblePrompt, context.vars);

    const hiddenParts: HiddenPart[] = parts.map(p => ({
      name: p.name,
      text: p.text,
    }));

    return {
      ledger,
      hiddenParts,
      visiblePrompt,
      execution: {
        mode: prompt.frontmatter.execution.mode,
        resultType: prompt.frontmatter.execution.result_type,
        resultTitle: prompt.frontmatter.execution.result_title,
        modelPreferences: prompt.frontmatter.execution.model_preferences,
        temperature: prompt.frontmatter.execution.temperature,
        maxTokens: prompt.frontmatter.execution.max_tokens,
        fallbackModels: prompt.frontmatter.execution.fallback_models,
        retryOnParseFailure: prompt.frontmatter.execution.retry_on_parse_failure,
      },
      responseSchema: prompt.frontmatter.response_schema,  // Pass through JSON schema for LLM
    };
  }
  
  private substituteVariables(text: string, vars: Record<string, any>): string {
    let result = text;
    
    // Replace {{{variable_name}}} and {{variable_name}}
    for (const [key, value] of Object.entries(vars)) {
      const triplePattern = new RegExp(`\\{\\{\\{${key}\\}\\}\\}`, 'g');
      const doublePattern = new RegExp(`\\{\\{${key}\\}\\}`, 'g');
      
      const stringValue = String(value ?? '');
      result = result.replace(triplePattern, stringValue);
      result = result.replace(doublePattern, stringValue);
    }
    
    return result;
  }

  private getSafetyFactor(modelId?: string): number {
    if (!modelId) {
      return this.config.safety_factors?.default || 0.90;
    }
    return this.config.safety_factors?.[modelId] || this.config.safety_factors?.default || 0.90;
  }

  getAllPrompts() {
    return this.registry.getAllPrompts();
  }

  getAllPacks() {
    return this.registry.getAllPacks();
  }

  getPrompt(id: string) {
    return this.registry.getPrompt(id);
  }

  getPack(id: string) {
    return this.registry.getPack(id);
  }
}

export type { LoadedPrompt, LoadedPack };

