/**
 * Conversations Context Implementation
 * 
 * Ported from backend/services/context/retrieval/convos_context.py
 * Retrieves raw conversation text with flexible filtering
 */

import { DatabaseClient } from '../../database/client.js';
import * as conversationsQueries from '../../database/queries/conversations.js';
import { encodeConversation, EncodedConversation } from '../../encoding/conversation.js';
import { countTokens } from '../../utils/token-counter.js';

export interface ConvosParams {
  chat_ids?: number[];
  contact_ids?: number[];
  time?: {
    preset?: 'day' | 'week' | 'month' | 'year' | 'all';
    start_date?: string; // ISO string
    end_date?: string;   // ISO string
  };
  token_max?: number;
  order?: 'timeAsc' | 'timeDesc' | 'simAsc' | 'simDesc';
  match?: {
    entities?: string[];
    topics?: string[];
    emotions?: string[];
  };
  encode?: {
    include_sender?: boolean;
    include_attachments?: boolean;
    include_reactions?: boolean;
  };
  similarity?: {
    query?: string;
    min_score?: number;
  };
}

/**
 * Resolve time window from params
 */
export function resolveTime(time: ConvosParams['time']): [string, string] {
  const t = time || {};
  const preset = (t.preset || '').toLowerCase();

  if (preset === 'day' || preset === 'week' || preset === 'month' || preset === 'year') {
    const end = new Date();
    const daysMap = { day: 1, week: 7, month: 30, year: 365 };
    const start = new Date();
    start.setDate(start.getDate() - daysMap[preset as keyof typeof daysMap]);
    return [start.toISOString(), end.toISOString()];
  }

  if (preset === 'all') {
    return ['1970-01-01T00:00:00Z', '3000-01-01T00:00:00Z'];
  }

  const startDate = t.start_date || '1970-01-01T00:00:00Z';
  const endDate = t.end_date || '3000-01-01T00:00:00Z';
  return [startDate, endDate];
}

/**
 * Get chat IDs for a contact
 */
function getChatIdsForContact(db: DatabaseClient, contactId: number): number[] {
  const sql = `
    SELECT DISTINCT chat_id
    FROM chat_participants
    WHERE contact_id = :contactId
  `;
  const rows = db.queryAll<{ chat_id: number }>(sql, { contactId });
  return rows.map((r) => r.chat_id);
}

/**
 * Collect chat IDs (union of chat_ids + expanded contact_ids)
 */
export function collectChatIds(
  db: DatabaseClient,
  chatIds: number[],
  contactIds: number[]
): number[] {
  const ids = new Set(chatIds || []);

  if (contactIds && contactIds.length > 0) {
    for (const cid of contactIds) {
      try {
        const chatIdsForContact = getChatIdsForContact(db, cid);
        chatIdsForContact.forEach((id) => ids.add(id));
      } catch (e) {
        // Best-effort: skip on failure
        console.warn(`Failed to get chat IDs for contact ${cid}:`, e);
      }
    }
  }

  return Array.from(ids);
}

/**
 * Filter conversation IDs by time and facets
 */
function filterConversationIds(
  db: DatabaseClient,
  chatIds: number[],
  startIso: string,
  endIso: string,
  match: ConvosParams['match']
): number[] {
  if (chatIds.length === 0) {
    return [];
  }

  // Base set: time + chat filter
  const sql = `
    SELECT id, chat_id
    FROM conversations
    WHERE chat_id IN (${chatIds.map(() => '?').join(', ')})
      AND datetime(start_time) >= datetime(?)
      AND datetime(start_time) < datetime(?)
  `;

  const params: any[] = [...chatIds, startIso, endIso];
  const baseRows = db.queryAll<{ id: number; chat_id: number }>(sql, params);
  let idSet = new Set(baseRows.map((r) => r.id));

  if (idSet.size === 0) {
    return [];
  }

  const m = match || {};

  // Helper to apply facet filtering
  function applyFacet(table: string, col: string, values: string[] | undefined): void {
    if (!values || values.length === 0 || idSet.size === 0) {
      return;
    }

    const vals = values.map((v) => v.toLowerCase()).filter((v) => v);
    if (vals.length === 0) {
      return;
    }

    const ids = Array.from(idSet);
    const facetSql = `
      SELECT DISTINCT conversation_id
      FROM ${table}
      WHERE conversation_id IN (${ids.map(() => '?').join(', ')})
        AND LOWER(${col}) IN (${vals.map(() => '?').join(', ')})
    `;

    const facetParams: any[] = [...ids, ...vals];
    const facetRows = db.queryAll<{ conversation_id: number }>(facetSql, facetParams);
    idSet = new Set(facetRows.map((r) => r.conversation_id));
  }

  applyFacet('entities', 'title', m.entities);
  applyFacet('topics', 'title', m.topics);
  applyFacet('emotions', 'emotion_type', m.emotions);

  return Array.from(idSet);
}

/**
 * Order conversations by time
 */
export function orderConversationsByTime(
  db: DatabaseClient,
  convIds: number[],
  asc: boolean
): number[] {
  if (convIds.length === 0) {
    return [];
  }

  const order = asc ? 'ASC' : 'DESC';
  const sql = `
    SELECT id
    FROM conversations
    WHERE id IN (${convIds.map(() => '?').join(', ')})
    ORDER BY start_time ${order}
  `;

  const rows = db.queryAll<{ id: number }>(sql, convIds);
  return rows.map((r) => r.id);
}

/**
 * Retrieve conversations context
 */
export function retrieveConvosContext(db: DatabaseClient, params: ConvosParams): string {
  const chatIds = params.chat_ids || [];
  const contactIds = params.contact_ids || [];
  const [startIso, endIso] = resolveTime(params.time);
  const tokenMax = params.token_max || 10000;
  const order = params.order || 'timeAsc';
  const match = params.match || {};
  const encode = params.encode || {};
  const includeSender = encode.include_sender !== false;
  const includeAttachments = encode.include_attachments !== false;
  const includeReactions = encode.include_reactions !== false;
  const similarity = params.similarity || {};

  console.log('[CTX][convos] params=', JSON.stringify({ ...params, encode: undefined }));

  // Resolve scope to chat IDs
  const ids = collectChatIds(db, chatIds, contactIds);
  if (ids.length === 0) {
    console.log('[CTX][convos] no chat ids resolved; returning empty');
    return '';
  }

  // Filter conversations
  let convIds = filterConversationIds(db, ids, startIso, endIso, match);
  console.log(`[CTX][convos] filtered ${convIds.length} conversations`);
  if (convIds.length === 0) {
    console.log('[CTX][convos] no conversations found in date range, returning empty');
    return '';
  }

  // Order conversations
  if (order === 'timeAsc' || order === 'timeDesc') {
    convIds = orderConversationsByTime(db, convIds, order === 'timeAsc');
  } else {
    // TODO: Implement similarity ordering
    console.warn('[CTX][convos] similarity ordering not yet implemented, using timeAsc');
    convIds = orderConversationsByTime(db, convIds, true);
  }

  // Map conv -> chat for hydration
  const convToChatSql = `
    SELECT id, chat_id
    FROM conversations
    WHERE id IN (${convIds.map(() => '?').join(', ')})
  `;
  const chatRows = db.queryAll<{ id: number; chat_id: number }>(convToChatSql, convIds);
  const convToChat = new Map<number, number>();
  chatRows.forEach((r) => {
    convToChat.set(r.id, r.chat_id);
  });

  // Load and encode conversations with token budget
  const outLines: string[] = [];
  let totalTokens = 0;

  console.log(`[CTX][convos] loading ${convIds.length} conversations...`);
  
  for (const convId of convIds) {
    const chatId = convToChat.get(convId);
    if (chatId === undefined) {
      console.log(`[CTX][convos] convId ${convId} has no chat_id mapping, skipping`);
      continue;
    }

    // Load conversation with messages
    const conv = conversationsQueries.loadConversationWithMessages(db, convId, chatId);
    if (!conv) {
      console.log(`[CTX][convos] convId ${convId} failed to load, skipping`);
      continue;
    }
    if (!conv.messages || conv.messages.length === 0) {
      console.log(`[CTX][convos] convId ${convId} has no messages, skipping`);
      continue;
    }

    // Encode conversation
    const encoded = encodeConversation(conv as unknown as EncodedConversation, {
      includeSender,
      includeAttachments,
      includeReactions,
    });

    const approx = countTokens(encoded);

    if (totalTokens + approx > tokenMax) {
      console.log(`[CTX][convos] conversations_included=${outLines.length} total_tokens=${totalTokens}`);
      return outLines.join('\n\n');
    }

    outLines.push(encoded);
    totalTokens += approx;
  }

  console.log(`[CTX][convos] conversations_included=${outLines.length} total_tokens=${totalTokens}`);
  return outLines.join('\n\n');
}

