/**
 * Analysis SQL Queries
 * 
 * Ported from backend/repositories/conversation_analysis.py and analysis_results.py
 */

import { DatabaseClient } from '../client.js';
import { ConversationAnalysis, Entity, Topic, Emotion, HumorItem, AnalysisFacets } from '../types.js';

/**
 * Get conversation analysis by conversation ID and Eve prompt ID
 */
export function getAnalysis(
  db: DatabaseClient,
  conversationId: number,
  evePromptId: string
): ConversationAnalysis | null {
  const sql = `
    SELECT *
    FROM conversation_analyses
    WHERE conversation_id = :conversationId AND eve_prompt_id = :evePromptId
    LIMIT 1
  `;
  return db.queryOne<ConversationAnalysis>(sql, { conversationId, evePromptId });
}

/**
 * Get all analyses for a conversation
 */
export function getAnalysesByConversation(
  db: DatabaseClient,
  conversationId: number
): ConversationAnalysis[] {
  const sql = `
    SELECT *
    FROM conversation_analyses
    WHERE conversation_id = :conversationId
    ORDER BY created_at DESC
  `;
  return db.queryAll<ConversationAnalysis>(sql, { conversationId });
}

/**
 * Get analyses by chat ID with filters
 */
export function getAnalysesByChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    evePromptIds?: string[];
    startDate?: string; // ISO string
    endDate?: string; // ISO string
    limit?: number;
  } = {}
): ConversationAnalysis[] {
  let sql = `
    SELECT ca.*
    FROM conversation_analyses ca
    JOIN conversations c ON ca.conversation_id = c.id
    WHERE c.chat_id = :chatId
  `;

  const params: any = { chatId };

  if (options.evePromptIds && options.evePromptIds.length > 0) {
    sql += ` AND ca.eve_prompt_id IN (${options.evePromptIds.map((_, i) => `:promptId${i}`).join(', ')})`;
    options.evePromptIds.forEach((id, i) => {
      params[`promptId${i}`] = id;
    });
  }

  if (options.startDate) {
    sql += ` AND c.start_time >= :startDate`;
    params.startDate = options.startDate;
  }

  if (options.endDate) {
    sql += ` AND c.end_time <= :endDate`;
    params.endDate = options.endDate;
  }

  sql += ` ORDER BY c.start_time DESC`;

  if (options.limit) {
    sql += ` LIMIT :limit`;
    params.limit = options.limit;
  }

  return db.queryAll<ConversationAnalysis>(sql, params);
}

/**
 * Get entities by chat ID
 */
export function getEntitiesByChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    startDate?: string;
    endDate?: string;
    limit?: number;
  } = {}
): Entity[] {
  let sql = `
    SELECT e.*
    FROM entities e
    JOIN conversations c ON e.conversation_id = c.id
    WHERE e.chat_id = :chatId
  `;

  const params: any = { chatId };

  if (options.startDate) {
    sql += ` AND c.start_time >= :startDate`;
    params.startDate = options.startDate;
  }

  if (options.endDate) {
    sql += ` AND c.end_time <= :endDate`;
    params.endDate = options.endDate;
  }

  sql += ` ORDER BY c.start_time DESC, e.created_at DESC`;

  if (options.limit) {
    sql += ` LIMIT :limit`;
    params.limit = options.limit;
  }

  return db.queryAll<Entity>(sql, params);
}

/**
 * Get topics by chat ID
 */
export function getTopicsByChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    startDate?: string;
    endDate?: string;
    limit?: number;
  } = {}
): Topic[] {
  let sql = `
    SELECT t.*
    FROM topics t
    JOIN conversations c ON t.conversation_id = c.id
    WHERE t.chat_id = :chatId
  `;

  const params: any = { chatId };

  if (options.startDate) {
    sql += ` AND c.start_time >= :startDate`;
    params.startDate = options.startDate;
  }

  if (options.endDate) {
    sql += ` AND c.end_time <= :endDate`;
    params.endDate = options.endDate;
  }

  sql += ` ORDER BY c.start_time DESC, t.created_at DESC`;

  if (options.limit) {
    sql += ` LIMIT :limit`;
    params.limit = options.limit;
  }

  return db.queryAll<Topic>(sql, params);
}

/**
 * Get emotions by chat ID
 */
export function getEmotionsByChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    startDate?: string;
    endDate?: string;
    limit?: number;
  } = {}
): Emotion[] {
  let sql = `
    SELECT e.*
    FROM emotions e
    JOIN conversations c ON e.conversation_id = c.id
    WHERE e.chat_id = :chatId
  `;

  const params: any = { chatId };

  if (options.startDate) {
    sql += ` AND c.start_time >= :startDate`;
    params.startDate = options.startDate;
  }

  if (options.endDate) {
    sql += ` AND c.end_time <= :endDate`;
    params.endDate = options.endDate;
  }

  sql += ` ORDER BY c.start_time DESC, e.created_at DESC`;

  if (options.limit) {
    sql += ` LIMIT :limit`;
    params.limit = options.limit;
  }

  return db.queryAll<Emotion>(sql, params);
}

/**
 * Get humor items by chat ID
 */
export function getHumorByChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    startDate?: string;
    endDate?: string;
    limit?: number;
  } = {}
): HumorItem[] {
  let sql = `
    SELECT h.*
    FROM humor_items h
    JOIN conversations c ON h.conversation_id = c.id
    WHERE h.chat_id = :chatId
  `;

  const params: any = { chatId };

  if (options.startDate) {
    sql += ` AND c.start_time >= :startDate`;
    params.startDate = options.startDate;
  }

  if (options.endDate) {
    sql += ` AND c.end_time <= :endDate`;
    params.endDate = options.endDate;
  }

  sql += ` ORDER BY c.start_time DESC, h.created_at DESC`;

  if (options.limit) {
    sql += ` LIMIT :limit`;
    params.limit = options.limit;
  }

  return db.queryAll<HumorItem>(sql, params);
}

/**
 * Get all analysis facets for chat (entities, topics, emotions, humor)
 */
export function getAnalysisFacetsByChat(
  db: DatabaseClient,
  chatId: number,
  options: {
    startDate?: string;
    endDate?: string;
    limit?: number;
  } = {}
): AnalysisFacets {
  return {
    entities: getEntitiesByChat(db, chatId, options),
    topics: getTopicsByChat(db, chatId, options),
    emotions: getEmotionsByChat(db, chatId, options),
    humor_items: getHumorByChat(db, chatId, options),
  };
}

/**
 * Get entities by conversation IDs
 */
export function getEntitiesByConversations(
  db: DatabaseClient,
  conversationIds: number[]
): Entity[] {
  if (conversationIds.length === 0) return [];

  const sql = `
    SELECT *
    FROM entities
    WHERE conversation_id IN (${conversationIds.map((_, i) => `:convId${i}`).join(', ')})
    ORDER BY created_at DESC
  `;

  const params: any = {};
  conversationIds.forEach((id, i) => {
    params[`convId${i}`] = id;
  });

  return db.queryAll<Entity>(sql, params);
}

/**
 * Get topics by conversation IDs
 */
export function getTopicsByConversations(
  db: DatabaseClient,
  conversationIds: number[]
): Topic[] {
  if (conversationIds.length === 0) return [];

  const sql = `
    SELECT *
    FROM topics
    WHERE conversation_id IN (${conversationIds.map((_, i) => `:convId${i}`).join(', ')})
    ORDER BY created_at DESC
  `;

  const params: any = {};
  conversationIds.forEach((id, i) => {
    params[`convId${i}`] = id;
  });

  return db.queryAll<Topic>(sql, params);
}

/**
 * Get emotions by conversation IDs
 */
export function getEmotionsByConversations(
  db: DatabaseClient,
  conversationIds: number[]
): Emotion[] {
  if (conversationIds.length === 0) return [];

  const sql = `
    SELECT *
    FROM emotions
    WHERE conversation_id IN (${conversationIds.map((_, i) => `:convId${i}`).join(', ')})
    ORDER BY created_at DESC
  `;

  const params: any = {};
  conversationIds.forEach((id, i) => {
    params[`convId${i}`] = id;
  });

  return db.queryAll<Emotion>(sql, params);
}

/**
 * Get humor items by conversation IDs
 */
export function getHumorByConversations(
  db: DatabaseClient,
  conversationIds: number[]
): HumorItem[] {
  if (conversationIds.length === 0) return [];

  const sql = `
    SELECT *
    FROM humor_items
    WHERE conversation_id IN (${conversationIds.map((_, i) => `:convId${i}`).join(', ')})
    ORDER BY created_at DESC
  `;

  const params: any = {};
  conversationIds.forEach((id, i) => {
    params[`convId${i}`] = id;
  });

  return db.queryAll<HumorItem>(sql, params);
}

