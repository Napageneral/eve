import { z } from 'zod';

export const retrievalFunctionSchema = z.enum([
  'convos_context_data',
  'analyses_context_data',
  'artifacts_context_data',
  'suggestion_history',
  'current_messages',
  'static_snippet',
]);

export const packSliceSchema = z.object({
  name: z.string(),
  retrieval: retrievalFunctionSchema,
  params: z.record(z.string(), z.any()),
  estimated_tokens: z.number(),
  encoding_explainer: z.union([z.literal('auto'), z.string()]).optional(),
  why_include: z.string().optional(),
});

export const trimmingStepSchema = z.union([
  z.object({
    shrink_time: z.object({
      slice: z.string().optional(),
      schedule: z.array(z.string()),
    }),
  }),
  z.object({
    compress_encoding: z.object({
      slice: z.string().optional(),
      from: z.string(),
      to: z.string(),
    }),
  }),
  z.object({
    drop_candidates: z.object({
      slices: z.array(z.string()),
    }),
  }),
]);

export const packSpecSchema = z.object({
  id: z.string(),
  name: z.string(),
  version: z.string(),
  category: z.string(),
  tags: z.array(z.string()).optional(),
  description: z.string().optional(),
  flexibility: z.enum(['high', 'medium', 'low']),
  total_estimated_tokens: z.number().optional(),
  slices: z.array(packSliceSchema),
  trimming: z.object({
    steps: z.array(trimmingStepSchema).optional(),
    hard_requirements: z.array(z.string()).optional(),
  }).optional(),
  alternatives: z.array(z.string()).optional(),
});

export type PackSpec = z.infer<typeof packSpecSchema>;
export type PackSlice = z.infer<typeof packSliceSchema>;
export type RetrievalFunction = z.infer<typeof retrievalFunctionSchema>;
export type TrimmingStep = z.infer<typeof trimmingStepSchema>;

