/**
 * Simple Retrieval Implementations
 * 
 * Ported from backend/services/context/retrieval/
 * Includes: raw_conversation, chat_text, user_name
 */

import { DatabaseClient } from '../../database/client.js';
import * as conversationsQueries from '../../database/queries/conversations.js';
import * as contactsQueries from '../../database/queries/contacts.js';
import { encodeConversation, EncodedConversation } from '../../encoding/conversation.js';

/**
 * Raw Conversation - Single conversation text
 */
export function retrieveRawConversation(
  db: DatabaseClient,
  params: { conversation_id: number; chat_id: number }
): string {
  const { conversation_id, chat_id } = params;

  const conv = conversationsQueries.loadConversationWithMessages(db, conversation_id, chat_id);
  if (!conv || !conv.messages || conv.messages.length === 0) {
    return '';
  }

  return encodeConversation(conv as unknown as EncodedConversation, {
    includeSender: true,
    includeAttachments: true,
    includeReactions: true,
  });
}

/**
 * Chat Text - All conversations for a chat
 */
export function retrieveChatText(
  db: DatabaseClient,
  params: { chat_id: number; limit?: number }
): string {
  const { chat_id, limit } = params;

  const conversations = conversationsQueries.getConversationsForChat(db, chat_id, { limit });

  if (conversations.length === 0) {
    return '';
  }

  const encodedConvos: string[] = [];

  for (const conv of conversations) {
    const fullConv = conversationsQueries.loadConversationWithMessages(db, conv.id, chat_id);
    if (!fullConv || !fullConv.messages || fullConv.messages.length === 0) {
      continue;
    }

    const encoded = encodeConversation(fullConv as unknown as EncodedConversation, {
      includeSender: true,
      includeAttachments: true,
      includeReactions: true,
    });

    encodedConvos.push(encoded);
  }

  return encodedConvos.join('\n\n');
}

/**
 * User Name - Get current user's name
 */
export function retrieveUserName(db: DatabaseClient): string {
  const meContact = contactsQueries.getMeContact(db);
  if (!meContact) {
    return 'User';
  }

  return meContact.name || meContact.nickname || 'User';
}

