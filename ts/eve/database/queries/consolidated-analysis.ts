/**
 * Consolidated Analysis Query
 * 
 * Ported from backend/repositories/analysis.py get_chat_consolidated_data()
 * This query groups facets by (conversation_id, contact_id) then aggregates
 */

import { DatabaseClient } from '../client.js';

interface ConsolidatedRow {
  conversation_id: number;
  chat_id: number;
  conversation_summary: string | null;
  conv_start_date: string;
  contact_id: number | null;
  contact_name: string | null;
  emotions: Array<{ title: string; description: string }>;
  humor: Array<{ title: string; description: string }>;
  topics: Array<{ title: string; description: string }>;
  entities: Array<{ title: string; description: string }>;
}

/**
 * Get consolidated analysis data matching backend exactly
 */
export function getChatConsolidatedData(
  db: DatabaseClient,
  chatId: number
): ConsolidatedRow[] {
  // Base conversations query (grouped by conversation_id AND contact_id)
  const conversationsSql = `
    SELECT 
      c.id as conversation_id,
      c.chat_id,
      c.summary as conversation_summary,
      NULL as summary_cum_text,
      NULL as topics_cum_text,
      NULL as entities_cum_text,
      NULL as emotions_cum_text,
      NULL as humor_cum_text,
      MIN(m.id) as first_message_id,
      MIN(m.guid) as first_message_guid,
      strftime('%Y-%m-%dT%H:%M:%SZ', c.start_time) as conv_start_date,
      cont.id as contact_id,
      cont.name as contact_name
    FROM conversations c
    LEFT JOIN messages m ON m.conversation_id = c.id
    LEFT JOIN contacts cont ON cont.id = m.sender_id
    WHERE c.chat_id = :chatId
    GROUP BY c.id, cont.id
    ORDER BY c.start_time ASC
  `;
  const conversations = db.queryAll<any>(conversationsSql, { chatId });

  // Query each dimension with contact_id
  const emotionsData = db.queryAll<{ conversation_id: number; contact_id: number; title: string; description: string }>(
    `SELECT conversation_id, contact_id, emotion_type as title, '' as description 
     FROM emotions WHERE chat_id = :chatId`,
    { chatId }
  );

  const humorData = db.queryAll<{ conversation_id: number; contact_id: number; title: string; description: string }>(
    `SELECT conversation_id, contact_id, snippet as title, snippet as description
     FROM humor_items WHERE chat_id = :chatId`,
    { chatId }
  );

  const topicsData = db.queryAll<{ conversation_id: number; contact_id: number; title: string; description: string }>(
    `SELECT conversation_id, contact_id, title as title, '' as description 
     FROM topics WHERE chat_id = :chatId`,
    { chatId }
  );

  const entitiesData = db.queryAll<{ conversation_id: number; contact_id: number; title: string; description: string }>(
    `SELECT conversation_id, contact_id, title as title, '' as description 
     FROM entities WHERE chat_id = :chatId`,
    { chatId }
  );

  // Group rows by conversation_id + contact_id
  function groupRows(rows: any[]): Record<string, Array<{ title: string; description: string }>> {
    const grouped: Record<string, Array<{ title: string; description: string }>> = {};
    for (const row of rows) {
      const key = `${row.conversation_id}_${row.contact_id}`;
      if (!grouped[key]) {
        grouped[key] = [];
      }
      grouped[key].push({
        title: row.title,
        description: row.description,
      });
    }
    return grouped;
  }

  const emotionsMap = groupRows(emotionsData);
  const humorMap = groupRows(humorData);
  const topicsMap = groupRows(topicsData);
  const entitiesMap = groupRows(entitiesData);

  // Build final result
  const result: ConsolidatedRow[] = [];
  for (const conv of conversations) {
    const key = `${conv.conversation_id}_${conv.contact_id}`;

    const conversationEntry: ConsolidatedRow = {
      conversation_id: conv.conversation_id,
      chat_id: conv.chat_id,
      conversation_summary: conv.conversation_summary,
      conv_start_date: conv.conv_start_date,
      contact_id: conv.contact_id,
      contact_name: conv.contact_name,
      emotions: emotionsMap[key] || [],
      humor: humorMap[key] || [],
      topics: topicsMap[key] || [],
      entities: entitiesMap[key] || [],
    };

    result.push(conversationEntry);
  }

  return result;
}

