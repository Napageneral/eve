/**
 * Analyses Context Adapter
 * 
 * Retrieves structured analysis data (topics, entities, emotions, humor)
 * Now uses direct DB access instead of HTTP backend calls
 */

import { RetrievalAdapter } from './types.js';
import { getDbClient } from '../../database/client.js';
import { retrieveAnalysesContext, AnalysesParams } from './analyses-impl.js';
import { resolveVariables } from '../utils/variables.js';

export const analysesContextAdapter: RetrievalAdapter = async (params, context) => {
  // Resolve variables in params using consolidated utility
  const resolvedParams = resolveVariables(params, context) as AnalysesParams;

  // Get database client
  const db = getDbClient(context.dbPath);

  // Retrieve analyses context using direct DB access
  const text = retrieveAnalysesContext(db, resolvedParams);

  return {
    text,
    actualTokens: text.length / 4, // Rough estimate TODO: use real token counter
  };
};
