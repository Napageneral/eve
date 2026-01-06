/**
 * Artifacts Context Adapter
 * 
 * Retrieves chatbot document contents
 * Now uses direct DB access instead of HTTP backend calls
 */

import { RetrievalAdapter } from './types.js';
import { getDbClient } from '../../database/client.js';
import { retrieveArtifactsContext, ArtifactsParams } from './artifacts-impl.js';
import { resolveVariables } from '../utils/variables.js';

export const artifactsContextAdapter: RetrievalAdapter = async (params, context) => {
  // Resolve variables in params using consolidated utility
  const resolvedParams = resolveVariables(params, context) as ArtifactsParams;

  // Get database client
  const db = getDbClient(context.dbPath);

  // Retrieve artifacts context using direct DB access
  const text = retrieveArtifactsContext(db, resolvedParams);

  return {
    text,
    actualTokens: text.length / 4, // Rough estimate TODO: use real token counter
  };
};
