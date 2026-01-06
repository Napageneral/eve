/**
 * Message SQL Queries
 * 
 * Ported from backend/repositories/messages.py
 */

import { DatabaseClient } from '../client.js';
import { Message, MessageWithSender } from '../types.js';

/**
 * Get messages for chat
 */
export function getMessagesForChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    limit?: number;
    startDate?: string;
    endDate?: string;
    includeDeleted?: boolean;
  } = {}
): MessageWithSender[] {
  let sql = `
    SELECT 
      m.*,
      c.name as sender_name,
      c.nickname as sender_nickname,
      c.is_me as sender_is_me
    FROM messages m
    LEFT JOIN contacts c ON m.sender_id = c.id
    WHERE m.chat_id = :chatId
  `;

  const params: any = { chatId };

  if (options.startDate) {
    sql += ` AND m.timestamp >= :startDate`;
    params.startDate = options.startDate;
  }

  if (options.endDate) {
    sql += ` AND m.timestamp <= :endDate`;
    params.endDate = options.endDate;
  }

  sql += ` ORDER BY m.timestamp ASC`;

  if (options.limit) {
    sql += ` LIMIT :limit`;
    params.limit = options.limit;
  }

  return db.queryAll<MessageWithSender>(sql, params);
}

/**
 * Get message count for chat
 */
export function getMessageCountForChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    startDate?: string;
    endDate?: string;
  } = {}
): number {
  let sql = `
    SELECT COUNT(*) as count
    FROM messages
    WHERE chat_id = :chatId
  `;

  const params: any = { chatId };

  if (options.startDate) {
    sql += ` AND timestamp >= :startDate`;
    params.startDate = options.startDate;
  }

  if (options.endDate) {
    sql += ` AND timestamp <= :endDate`;
    params.endDate = options.endDate;
  }

  return db.queryValue<number>(sql, params) || 0;
}

/**
 * Get message by ID
 */
export function getMessageById(
  db: DatabaseClient,
  messageId: number
): Message | null {
  const sql = `SELECT * FROM messages WHERE id = :messageId LIMIT 1`;
  return db.queryOne<Message>(sql, { messageId });
}

/**
 * Get message by GUID
 */
export function getMessageByGuid(
  db: DatabaseClient,
  guid: string
): Message | null {
  const sql = `SELECT * FROM messages WHERE guid = :guid LIMIT 1`;
  return db.queryOne<Message>(sql, { guid });
}

