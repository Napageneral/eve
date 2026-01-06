/**
 * Chat SQL Queries
 * 
 * Ported from backend/repositories/chats.py
 */

import { DatabaseClient } from '../client.js';
import { Chat, ChatWithParticipants } from '../types.js';

/**
 * Get chat by ID
 */
export function getChatById(
  db: DatabaseClient,
  chatId: number
): Chat | null {
  const sql = `SELECT * FROM chats WHERE id = :chatId LIMIT 1`;
  return db.queryOne<Chat>(sql, { chatId });
}

/**
 * Get chat by chat_identifier
 */
export function getChatByIdentifier(
  db: DatabaseClient,
  chatIdentifier: string
): Chat | null {
  const sql = `SELECT * FROM chats WHERE chat_identifier = :chatIdentifier LIMIT 1`;
  return db.queryOne<Chat>(sql, { chatIdentifier });
}

/**
 * Get all chats
 */
export function getAllChats(
  db: DatabaseClient,
  options: {
    excludeBlocked?: boolean;
    limit?: number;
  } = {}
): Chat[] {
  let sql = `SELECT * FROM chats WHERE 1=1`;
  const params: any = {};

  if (options.excludeBlocked) {
    sql += ` AND is_blocked = 0`;
  }

  sql += ` ORDER BY last_message_date DESC`;

  if (options.limit) {
    sql += ` LIMIT :limit`;
    params.limit = options.limit;
  }

  return db.queryAll<Chat>(sql, params);
}

/**
 * Get chats by IDs
 */
export function getChatsByIds(
  db: DatabaseClient,
  chatIds: number[]
): Chat[] {
  if (chatIds.length === 0) return [];

  const sql = `
    SELECT * 
    FROM chats
    WHERE id IN (${chatIds.map((_, i) => `:chatId${i}`).join(', ')})
  `;

  const params: any = {};
  chatIds.forEach((id, i) => {
    params[`chatId${i}`] = id;
  });

  return db.queryAll<Chat>(sql, params);
}

/**
 * Get chat with participant info
 */
export function getChatWithParticipants(
  db: DatabaseClient,
  chatId: number
): ChatWithParticipants | null {
  const chat = getChatById(db, chatId);
  if (!chat) return null;

  const sql = `
    SELECT c.id as contact_id, c.name
    FROM contacts c
    JOIN chat_participants cp ON cp.contact_id = c.id
    WHERE cp.chat_id = :chatId
  `;

  const participants = db.queryAll<{ contact_id: number; name: string }>(sql, { chatId });

  return {
    ...chat,
    participant_names: participants.map(p => p.name).filter(Boolean),
    participant_ids: participants.map(p => p.contact_id),
  };
}

