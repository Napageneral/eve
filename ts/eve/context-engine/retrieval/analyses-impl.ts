/**
 * Analyses Context Implementation
 * 
 * Ported from backend/services/context/retrieval/analyses_context.py
 * Retrieves structured analysis data (topics, entities, emotions, humor)
 */

import { DatabaseClient } from '../../database/client.js';
import * as analysesQueries from '../../database/queries/analyses.js';
import * as conversationsQueries from '../../database/queries/conversations.js';
import { getChatConsolidatedData } from '../../database/queries/consolidated-analysis.js';
import { countTokens } from '../../utils/token-counter.js';
import { resolveTime, collectChatIds, orderConversationsByTime } from './convos-impl.js';

export interface AnalysesParams {
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
  include?: Array<'summary' | 'topics' | 'entities' | 'emotions' | 'humor'>;
  similarity?: {
    query?: string;
    min_score?: number;
  };
}

interface AnalysisRow {
  conversation_id: number;
  conv_start_date?: string;
  conversation_summary?: string;
  topics: Array<{ title: string }>;
  entities: Array<{ title: string }>;
  emotions: Array<{ title: string }>;  // emotion_type aliased as title
  humor: Array<{ description: string }>;
}

/**
 * Filter conversation IDs by facets (entities, topics, emotions)
 */
function filterConvIdsByFacets(
  db: DatabaseClient,
  chatIds: number[],
  startIso: string,
  endIso: string,
  match: AnalysesParams['match']
): number[] {
  if (chatIds.length === 0) {
    return [];
  }

  // Get base conversations in time range
  const sql = `
    SELECT id
    FROM conversations
    WHERE chat_id IN (${chatIds.map((_, i) => `:chatId${i}`).join(', ')})
      AND datetime(start_time) >= datetime(:start)
      AND datetime(start_time) < datetime(:end)
  `;

  const params: any = { start: startIso, end: endIso };
  chatIds.forEach((id, i) => {
    params[`chatId${i}`] = id;
  });

  const baseRows = db.queryAll<{ id: number }>(sql, params);
  let idSet = new Set(baseRows.map((r) => r.id));
  
  console.log('[CTX][analyses] filterConvIdsByFacets SQL result:', {
    chatIds,
    startIso,
    endIso,
    rowsFound: baseRows.length
  });

  if (idSet.size === 0) {
    console.log('[CTX][analyses] No conversations found in time range - check if analyses exist for this chat');
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
      WHERE conversation_id IN (${ids.map((_, i) => `:id${i}`).join(', ')})
        AND LOWER(${col}) IN (${vals.map((_, i) => `:v${i}`).join(', ')})
    `;

    const facetParams: any = {};
    ids.forEach((id, i) => {
      facetParams[`id${i}`] = id;
    });
    vals.forEach((val, i) => {
      facetParams[`v${i}`] = val;
    });

    const facetRows = db.queryAll<{ conversation_id: number }>(facetSql, facetParams);
    idSet = new Set(facetRows.map((r) => r.conversation_id));
  }

  // Apply each facet filter
  applyFacet('entities', 'title', m.entities);
  applyFacet('topics', 'title', m.topics);
  applyFacet('emotions', 'emotion_type', m.emotions);

  return Array.from(idSet);
}

/**
 * Format a single analysis block
 * Matches backend format_analysis_block exactly
 */
function formatAnalysisBlock(row: AnalysisRow, include: string[]): string {
  const lines: string[] = [];
  const dt = (row.conv_start_date || '').slice(0, 10);
  const header = `### ${dt} — Conversation ${row.conversation_id}`;
  lines.push(header);

  // Summary
  if (include.includes('summary')) {
    const summary = (row.conversation_summary || '').trim();
    if (summary) {
      lines.push(`Summary: ${summary}`);
    }
  }

  // Helper function to pack facet lists (matches backend pack_list)
  function packList(name: string, key: keyof AnalysisRow) {
    if (!include.includes(name)) {
      return;
    }
    const items = (row[key] as any[]) || [];
    const values: string[] = [];
    for (const it of items) {
      const title = (it.title || '').trim();
      if (title) {
        values.push(title);
      }
    }
    if (values.length > 0) {
      const uniqueSorted = Array.from(new Set(values)).sort();
      lines.push(`${name.charAt(0).toUpperCase() + name.slice(1)}: ${uniqueSorted.join(', ')}`);
    }
  }

  packList('topics', 'topics');
  packList('entities', 'entities');
  packList('emotions', 'emotions');

  // Humor (special formatting)
  if (include.includes('humor')) {
    const humorItems = row.humor || [];
    const snippets: string[] = [];
    for (const it of humorItems) {
      const desc = (it.description || '').trim();
      if (desc) {
        snippets.push(desc);
      }
    }
    if (snippets.length > 0) {
      lines.push('Humor:');
      snippets.slice(0, 10).forEach((s) => {
        lines.push(`  - ${s}`);
      });
    }
  }

  return lines.join('\n') + '\n';
}

/**
 * Retrieve analyses context
 */
export function retrieveAnalysesContext(db: DatabaseClient, params: AnalysesParams): string {
  const chatIds = params.chat_ids || [];
  const contactIds = params.contact_ids || [];
  const [startIso, endIso] = resolveTime(params.time);  // ← FIX: Use resolveTime like convos does
  const tokenMax = params.token_max || 10000;
  const order = params.order || 'timeAsc';
  const match = params.match || {};
  const include = params.include || ['summary', 'topics', 'entities', 'emotions', 'humor'];
  const similarity = params.similarity || {};

  console.log('[CTX][analyses] params=', JSON.stringify(params));
  console.log('[CTX][analyses] resolved time range:', { startIso, endIso });

  // Collect chat IDs (expand contact IDs to chat IDs)
  const ids = collectChatIds(db, chatIds, contactIds);
  console.log('[CTX][analyses] collected chat ids:', ids);
  if (ids.length === 0) {
    console.log('[CTX][analyses] no chat ids resolved; returning empty');
    return '';
  }

  // Filter conversations by time and facets
  let convIds = filterConvIdsByFacets(db, ids, startIso, endIso, match);
  console.log('[CTX][analyses] filtered conversations:', { count: convIds.length });
  if (convIds.length === 0) {
    console.log('[CTX][analyses] no conversations after filtering; returning empty');
    return '';
  }

  // Order conversations
  if (order === 'timeAsc' || order === 'timeDesc') {
    convIds = orderConversationsByTime(db, convIds, order === 'timeAsc');
  } else {
    // TODO: Implement similarity ordering
    console.warn('[CTX][analyses] similarity ordering not yet implemented, using timeAsc');
    convIds = orderConversationsByTime(db, convIds, true);
  }

  // Map conv -> chat
  const convToChatSql = `
    SELECT id, chat_id
    FROM conversations
    WHERE id IN (${convIds.map((_, i) => `:cid${i}`).join(', ')})
  `;
  const convToChatParams: any = {};
  convIds.forEach((id, i) => {
    convToChatParams[`cid${i}`] = id;
  });
  const chatRows = db.queryAll<{ id: number; chat_id: number }>(convToChatSql, convToChatParams);
  const convToChat = new Map<number, number>();
  chatRows.forEach((r) => {
    convToChat.set(r.id, r.chat_id);
  });

  // Group conv_ids by chat
  const byChat = new Map<number, number[]>();
  for (const cid of convIds) {
    const chatId = convToChat.get(cid);
    if (chatId === undefined) {
      continue;
    }
    if (!byChat.has(chatId)) {
      byChat.set(chatId, []);
    }
    byChat.get(chatId)!.push(cid);
  }

  const outLines: string[] = [];
  let totalTokens = 0;

  // For each chat, get consolidated analysis data (matches backend exactly)
  for (const [chatId, cids] of byChat.entries()) {
    console.log('[CTX][analyses] getting consolidated data for chat:', { chatId, convIds: cids.length });
    const consolidated = getChatConsolidatedData(db, chatId);
    console.log('[CTX][analyses] consolidated rows:', { chatId, rowCount: consolidated.length });
    
    // Aggregate rows by conversation_id (matches backend bucketing logic)
    const bucket = new Map<number, AnalysisRow>();
    
    for (const row of consolidated) {
      const cid = row.conversation_id;
      const entry = bucket.get(cid) || {
        conversation_id: cid,
        conv_start_date: row.conv_start_date,
        conversation_summary: row.conversation_summary || undefined,
        topics: [],
        entities: [],
        emotions: [],
        humor: [],
      };
      
      // Extend lists if present (matches backend aggregation)
      for (const key of ['topics', 'entities', 'emotions', 'humor'] as const) {
        const vals = row[key] || [];
        if (vals.length > 0) {
          entry[key].push(...vals);
        }
      }
      
      bucket.set(cid, entry);
    }

    // Format each conversation's analysis
    for (const cid of cids) {
      const row = bucket.get(cid);
      if (!row) {
        continue;
      }

      const textBlock = formatAnalysisBlock(row, include);
      const approx = countTokens(textBlock);

      if (totalTokens + approx > tokenMax) {
        console.log(
          `[CTX][analyses] conversations_included=${outLines.length} total_tokens=${totalTokens}`
        );
        return outLines.join('\n');
      }

      outLines.push(textBlock);
      totalTokens += approx;
    }
  }

  console.log(
    `[CTX][analyses] conversations_included=${outLines.length} total_tokens=${totalTokens}`
  );
  return outLines.join('\n');
}

