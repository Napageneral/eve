/**
 * Context Selections API
 * 
 * Handles context selection and preview endpoints.
 * Provides compatibility layer for old backend context_selections table.
 */

import { Request, Response } from 'express';
import { getDbClient } from '../../database/client.js';
import { retrieveConvosContext, ConvosParams } from '../retrieval/convos-impl.js';
import { retrieveAnalysesContext, AnalysesParams } from '../retrieval/analyses-impl.js';
import { retrieveArtifactsContext, ArtifactsParams } from '../retrieval/artifacts-impl.js';
import { retrieveUserName, retrieveChatText, retrieveRawConversation } from '../retrieval/simple-impl.js';
import { countTokens } from '../../utils/token-counter.js';

/**
 * Resolve content for a context definition
 */
function resolveContextContent(definitionId: number, parameterValues: any): string {
  const db = getDbClient();
  
  switch (definitionId) {
    case 4: // Convos
      return retrieveConvosContext(db, parameterValues as ConvosParams);
    case 5: // Analyses
      return retrieveAnalysesContext(db, parameterValues as AnalysesParams);
    case 6: // Artifacts
      return retrieveArtifactsContext(db, parameterValues as ArtifactsParams);
    case 7: // UserName
      return retrieveUserName(db);
    case 8: // ChatText
      return retrieveChatText(db, parameterValues as any);
    case 9: // RawConversation
      return retrieveRawConversation(db, parameterValues as any);
    default:
      throw new Error(`Unknown context_definition_id: ${definitionId}`);
  }
}

/**
 * POST /api/context/selections/preview
 * Get token count and optionally content (NO database persistence)
 */
export async function handlePreviewSelection(req: Request, res: Response) {
  try {
    const { context_definition_id, parameter_values, include_preview, include_content } = req.body;

    // Resolve content
    const content = resolveContextContent(context_definition_id, parameter_values);
    
    // Count tokens
    const tokenCount = countTokens(content);

    const response: any = {
      success: true,
      token_count: tokenCount,
    };

    if (include_preview) {
      response.content_preview = content.slice(0, 500);
    }

    if (include_content) {
      response.content = content;
    }

    res.json(response);
  } catch (error: any) {
    console.error('[Eve] /preview error:', error);
    res.status(500).json({ error: error.message });
  }
}

/**
 * POST /api/context/selections
 * Create context selection and optionally resolve content
 * Returns resolved_content when resolve_now=true
 */
export async function handleCreateSelection(req: Request, res: Response) {
  try {
    const { context_definition_id, parameter_values, resolve_now } = req.body;

    // Generate a fake context_selection_id for compatibility
    const fakeId = Math.floor(Math.random() * 1000000);

    if (!resolve_now) {
      // Just return ID without resolving
      res.json({
        success: true,
        context_selection_id: fakeId,
      });
      return;
    }

    // Resolve content
    const content = resolveContextContent(context_definition_id, parameter_values);
    
    // Count tokens
    const tokenCount = countTokens(content);

    res.json({
      success: true,
      context_selection_id: fakeId,
      resolved_content: content,
      token_count: tokenCount,
    });
  } catch (error: any) {
    console.error('[Eve] /selections error:', error);
    res.status(500).json({ error: error.message });
  }
}








