/**
 * Conversation Encoding API
 * 
 * Encodes conversations for LLM analysis.
 * Used by backend Celery tasks.
 */

import { Request, Response } from 'express';
import { getDbClient } from '../../database/client.js';
import { countTokens } from '../../utils/token-counter.js';

/**
 * POST /engine/encode
 * Encode a conversation for LLM analysis
 */
export async function handleEncodeConversation(req: Request, res: Response) {
  try {
    const { conversation_id, chat_id } = req.body;
    
    // Encoding request (logging disabled to reduce noise in tests)

    if (!conversation_id || !chat_id) {
      res.status(400).json({ error: 'Missing required fields: conversation_id, chat_id' });
      return;
    }

    // Get database client
    const db = getDbClient();

    // Load conversation with messages
    const conversationsQueries = await import('../../database/queries/conversations');
    const conv = conversationsQueries.loadConversationWithMessages(db, conversation_id, chat_id);

    if (!conv || !conv.messages || conv.messages.length === 0) {
      // Gracefully handle empty database (e.g., test environment, fresh install)
      console.log(`[ENCODE] Conversation ${conversation_id} not found or has no messages - returning empty result`);
      res.json({
        success: true,
        encoded_text: '',
        token_count: 0,
        message_count: 0,
      });
      return;
    }

    // Encode conversation
    const { encodeConversation } = await import('../../encoding/conversation');
    const encodedText = encodeConversation(conv as any);

    // Count tokens
    const tokens = countTokens(encodedText);

    res.json({
      success: true,
      encoded_text: encodedText,
      token_count: tokens,
      message_count: conv.messages.length,
    });
  } catch (error: any) {
    console.error('[Eve] /engine/encode error:', error);
    res.status(500).json({ error: error.message });
  }
}



