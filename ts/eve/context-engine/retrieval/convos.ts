/**
 * Conversations Context Adapter
 * 
 * Retrieves raw conversation text with flexible filtering
 * Now uses direct DB access instead of HTTP backend calls
 */

import { RetrievalAdapter } from './types.js';
import { getDbClient } from '../../database/client.js';
import { retrieveConvosContext, ConvosParams } from './convos-impl.js';
import { resolveVariables } from '../utils/variables.js';

export const convosContextAdapter: RetrievalAdapter = async (params, context) => {
  // Resolve variables in params using consolidated utility
  const resolvedParams = resolveVariables(params, context) as ConvosParams;

  // Get database client
  const db = getDbClient(context.dbPath);

  // Retrieve convos context using direct DB access
  const text = retrieveConvosContext(db, resolvedParams);

  return {
    text,
    actualTokens: text.length / 4, // Rough estimate TODO: use real token counter
  };
};
