import { z } from 'zod';

export const contextFlexibilitySchema = z.enum(['high', 'medium', 'low']);
export const executionModeSchema = z.enum(['chatbot-streaming', 'backend-task']);
export const resultTypeSchema = z.enum(['document', 'text', 'json']);

export const promptSourceSchema = z.discriminatedUnion('source', [
  z.object({
    source: z.literal('markdown'),
  }),
  z.object({
    source: z.literal('ts_function'),
    path: z.string(),
    export: z.string(),
  }),
]);

export const alwaysOnBehaviorSchema = z.object({
  include_in_budget: z.boolean().optional().default(true),
  position: z.enum(['prepend', 'append']).optional().default('prepend'),
  allow_override: z.boolean().optional().default(false),
}).optional();

export const promptFrontmatterSchema = z.object({
  id: z.string(),
  name: z.string(),
  version: z.string(),
  category: z.string(),
  tags: z.array(z.string()).optional(),

  prompt: promptSourceSchema,

  context_flexibility: contextFlexibilitySchema,
  context: z.object({
    default_pack: z.string(),
    alternatives: z.array(z.string()).optional(),
    compatible_pack_versions: z.record(z.string(), z.string()).optional(),
  }),

  always_on: z.array(z.string()).optional(),
  always_on_behavior: alwaysOnBehaviorSchema,

  vars: z.record(
    z.string(),
    z.object({
      type: z.enum(['string', 'number', 'boolean']),
      required: z.boolean().optional(),
      example: z.any().optional(),
    })
  ).optional(),

  execution: z.object({
    mode: executionModeSchema,
    result_type: resultTypeSchema,
    result_title: z.string().optional(),
    model_preferences: z.array(z.string()).optional(),
    temperature: z.number().optional(),  // Optional temperature override
    max_tokens: z.number().optional(),   // Optional max_tokens override
    
    // Fallback configuration for handling LLM response failures
    fallback_models: z.array(z.string()).optional(),  // Models to try on parse failure
    retry_on_parse_failure: z.boolean().optional(),   // Enable automatic retry on malformed JSON
  }),
  
  response_schema: z.any().optional(),  // JSON schema for structured LLM responses
});

export type PromptFrontmatter = z.infer<typeof promptFrontmatterSchema>;
export type ContextFlexibility = z.infer<typeof contextFlexibilitySchema>;
export type ExecutionMode = z.infer<typeof executionModeSchema>;
export type ResultType = z.infer<typeof resultTypeSchema>;

