/**
 * Context Definitions API
 * 
 * Maps context definition names to retrieval functions.
 * Provides compatibility layer for old backend context_definitions table.
 */

import { Request, Response } from 'express';

/**
 * Context definition mapping
 * Matches backend context_definitions table structure
 */
export const CONTEXT_DEFINITIONS: Record<string, { id: number; name: string; retrieval_fn: string }> = {
  'Convos': { id: 4, name: 'Convos', retrieval_fn: 'convos_context_data' },
  'Analyses': { id: 5, name: 'Analyses', retrieval_fn: 'analyses_context_data' },
  'Artifacts': { id: 6, name: 'Artifacts', retrieval_fn: 'artifacts_context_data' },
  'UserName': { id: 7, name: 'UserName', retrieval_fn: 'user_name_data' },
  'ChatText': { id: 8, name: 'ChatText', retrieval_fn: 'chat_text_data' },
  'RawConversation': { id: 9, name: 'RawConversation', retrieval_fn: 'raw_conversation_text_data' },
};

/**
 * Helper: Get definition by name
 */
export function getDefinitionByName(name: string) {
  return CONTEXT_DEFINITIONS[name];
}

/**
 * Helper: Get definition by ID
 */
export function getDefinitionById(id: number) {
  return Object.values(CONTEXT_DEFINITIONS).find(def => def.id === id);
}

/**
 * GET /api/context/definitions
 * List context definitions (compatible with backend)
 */
export function handleGetDefinitions(req: Request, res: Response) {
  const { name } = req.query;
  
  if (name) {
    const def = CONTEXT_DEFINITIONS[name as string];
    if (!def) {
      res.status(404).json({ error: `Context definition '${name}' not found` });
      return;
    }
    res.json([{
      id: def.id,
      name: def.name,
      retrieval_function_ref: def.retrieval_fn,
      description: `${def.name} context retrieval`,
      parameter_schema: [],
    }]);
  } else {
    // Return all definitions
    const all = Object.values(CONTEXT_DEFINITIONS).map(def => ({
      id: def.id,
      name: def.name,
      retrieval_function_ref: def.retrieval_fn,
      description: `${def.name} context retrieval`,
      parameter_schema: [],
    }));
    res.json(all);
  }
}








