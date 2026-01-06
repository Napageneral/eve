/**
 * Simple Context Adapters
 * 
 * Adapters for simple/utility context retrievers (UserName, ChatText, RawConversation)
 */

import { RetrievalAdapter } from './types.js';
import { getDbClient } from '../../database/client.js';
import { retrieveUserName, retrieveChatText, retrieveRawConversation } from './simple-impl.js';

/**
 * UserName - User's display name
 */
export const userNameAdapter: RetrievalAdapter = async (params, context) => {
  const db = getDbClient(context.dbPath);
  const text = retrieveUserName(db);
  
  return {
    text,
    actualTokens: text.length / 4, // Rough estimate (UserName is tiny)
  };
};

/**
 * ChatText - Full chat history text
 * 
 * WARNING: Can be HUGE (3.5MB+ for active chats)
 * Consider using Convos with token_max instead
 */
export const chatTextAdapter: RetrievalAdapter = async (params, context) => {
  const db = getDbClient(context.dbPath);
  const text = retrieveChatText(db, params as any);
  
  return {
    text,
    actualTokens: text.length / 4, // Rough estimate
  };
};

/**
 * RawConversation - Single conversation text
 */
export const rawConversationAdapter: RetrievalAdapter = async (params, context) => {
  const db = getDbClient(context.dbPath);
  const text = retrieveRawConversation(db, params as any);
  
  return {
    text,
    actualTokens: text.length / 4, // Rough estimate
  };
};

