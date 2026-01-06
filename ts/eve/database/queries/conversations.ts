/**
 * Conversation SQL Queries
 * 
 * Ported from backend/repositories/conversations.py
 */

import { DatabaseClient } from '../client.js';
import { Conversation, Message, Attachment, Reaction, MessageWithSender } from '../types.js';

/**
 * Get conversations for a specific chat
 */
export function getConversationsForChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    limit?: number;
    startDate?: string;
    endDate?: string;
  } = {}
): Conversation[] {
  let sql = `
    SELECT 
      c.*,
      COUNT(m.id) as message_count,
      MIN(m.timestamp) as actual_start_time,
      MAX(m.timestamp) as actual_end_time
    FROM conversations c
    LEFT JOIN messages m ON m.conversation_id = c.id
    WHERE c.chat_id = ?
  `;

  const params: any[] = [chatId];

  if (options.startDate) {
    sql += ` AND c.start_time >= ?`;
    params.push(options.startDate);
  }

  if (options.endDate) {
    sql += ` AND c.end_time <= ?`;
    params.push(options.endDate);
  }

  sql += ` GROUP BY c.id ORDER BY c.start_time DESC`;

  if (options.limit) {
    sql += ` LIMIT ?`;
    params.push(options.limit);
  }

  return db.queryAll<Conversation>(sql, params);
}

/**
 * Get conversation by ID
 */
export function getConversationById(
  db: DatabaseClient,
  conversationId: number
): Conversation | null {
  const sql = `
    SELECT *
    FROM conversations
    WHERE id = ?
    LIMIT 1
  `;
  return db.queryOne<Conversation>(sql, [conversationId]);
}

/**
 * Get messages for a conversation
 */
export function getMessagesForConversation(
  db: DatabaseClient,
  conversationId: number
): MessageWithSender[] {
  const sql = `
    SELECT 
      m.*,
      c.name as sender_name,
      c.nickname as sender_nickname,
      c.is_me as sender_is_me
    FROM messages m
    LEFT JOIN contacts c ON m.sender_id = c.id
    WHERE m.conversation_id = ?
    ORDER BY m.timestamp ASC
  `;
  return db.queryAll<MessageWithSender>(sql, [conversationId]);
}

/**
 * Get attachments for messages
 */
export function getAttachmentsForMessages(
  db: DatabaseClient,
  messageIds: number[]
): Attachment[] {
  if (messageIds.length === 0) return [];

  const sql = `
    SELECT a.*
    FROM attachments a
    WHERE a.message_id IN (${messageIds.map(() => '?').join(', ')})
    ORDER BY a.message_id, a.id
  `;

  return db.queryAll<Attachment>(sql, messageIds);
}

/**
 * Get reactions for messages
 */
export function getReactionsForMessages(
  db: DatabaseClient,
  messageGuids: string[]
): Reaction[] {
  if (messageGuids.length === 0) return [];

  const sql = `
    SELECT 
      r.*,
      c.name as sender_name
    FROM reactions r
    LEFT JOIN contacts c ON r.sender_id = c.id
    WHERE r.original_message_guid IN (${messageGuids.map(() => '?').join(', ')})
    ORDER BY r.original_message_guid, r.id
  `;

  return db.queryAll<Reaction>(sql, messageGuids);
}

/**
 * Load a single conversation with all related data (messages, attachments, reactions)
 */
export interface ConversationWithMessages extends Conversation {
  messages: Array<MessageWithSender & {
    attachments: Attachment[];
    reactions: Reaction[];
  }>;
}

export function loadConversationWithMessages(
  db: DatabaseClient,
  conversationId: number,
  chatId: number
): ConversationWithMessages | null {
  // Get conversation
  const conversation = getConversationById(db, conversationId);
  if (!conversation || conversation.chat_id !== chatId) {
    return null;
  }

  // Get messages
  const messages = getMessagesForConversation(db, conversationId);
  if (messages.length === 0) {
    return {
      ...conversation,
      messages: [],
    };
  }

  // Get attachments
  const messageIds = messages.map(m => m.id);
  const attachments = getAttachmentsForMessages(db, messageIds);

  // Get reactions
  const messageGuids = messages.map(m => m.guid).filter(Boolean);
  const reactions = getReactionsForMessages(db, messageGuids);

  // Build attachment map
  const attachmentMap = new Map<number, Attachment[]>();
  for (const att of attachments) {
    if (!attachmentMap.has(att.message_id)) {
      attachmentMap.set(att.message_id, []);
    }
    attachmentMap.get(att.message_id)!.push(att);
  }

  // Build reaction map
  const reactionMap = new Map<string, Reaction[]>();
  for (const react of reactions) {
    if (!reactionMap.has(react.original_message_guid)) {
      reactionMap.set(react.original_message_guid, []);
    }
    reactionMap.get(react.original_message_guid)!.push(react);
  }

  // Enrich messages with attachments and reactions
  const enrichedMessages = messages.map(msg => ({
    ...msg,
    attachments: attachmentMap.get(msg.id) || [],
    reactions: reactionMap.get(msg.guid) || [],
  }));

  return {
    ...conversation,
    messages: enrichedMessages,
  };
}

/**
 * Get conversations by IDs (for batch loading)
 */
export function getConversationsByIds(
  db: DatabaseClient,
  conversationIds: number[]
): Conversation[] {
  if (conversationIds.length === 0) return [];

  const sql = `
    SELECT *
    FROM conversations
    WHERE id IN (${conversationIds.map(() => '?').join(', ')})
    ORDER BY start_time DESC
  `;

  return db.queryAll<Conversation>(sql, conversationIds);
}

/**
 * Get conversation count for chat
 */
export function getConversationCountForChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    startDate?: string;
    endDate?: string;
  } = {}
): number {
  let sql = `
    SELECT COUNT(*) as count
    FROM conversations
    WHERE chat_id = ?
  `;

  const params: any[] = [chatId];

  if (options.startDate) {
    sql += ` AND start_time >= ?`;
    params.push(options.startDate);
  }

  if (options.endDate) {
    sql += ` AND end_time <= ?`;
    params.push(options.endDate);
  }

  return db.queryValue<number>(sql, params) || 0;
}

