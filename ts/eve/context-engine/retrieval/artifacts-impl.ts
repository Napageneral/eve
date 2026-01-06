/**
 * Artifacts Context Implementation
 * 
 * Ported from backend/services/context/retrieval/artifacts_context.py
 * Retrieves chatbot document contents
 */

import { DatabaseClient } from '../../database/client.js';
import { ChatbotDocument } from '../../database/types.js';

export interface ArtifactsParams {
  document_ids?: string[]; // UUID strings
  document_names?: string[];
  limit?: number;
}

/**
 * Retrieve artifacts (chatbot documents) context
 */
export function retrieveArtifactsContext(db: DatabaseClient, params: ArtifactsParams): string {
  const documentIds = params.document_ids || [];
  const documentNames = params.document_names || [];
  const limit = params.limit || 10;

  let documents: ChatbotDocument[] = [];

  // Fetch by IDs if provided
  if (documentIds.length > 0) {
    const sql = `
      SELECT *
      FROM chatbot_documents
      WHERE id IN (${documentIds.map(() => '?').join(', ')})
      ORDER BY created_at DESC
      LIMIT ?
    `;

    const params = [...documentIds, limit];
    documents = db.queryAll<ChatbotDocument>(sql, params);
  }
  // Otherwise fetch by names
  else if (documentNames.length > 0) {
    console.log('[artifacts] Searching for documents by name:', documentNames);
    const sql = `
      SELECT *
      FROM chatbot_documents
      WHERE title IN (${documentNames.map(() => '?').join(', ')})
      ORDER BY created_at DESC
      LIMIT ?
    `;

    const params = [...documentNames, limit];
    documents = db.queryAll<ChatbotDocument>(sql, params);
    console.log(`[artifacts] Found ${documents.length} documents`);
    if (documents.length > 0) {
      documents.forEach((doc, i) => {
        console.log(`[artifacts]   ${i+1}. ${doc.title} (content: ${(doc.content || '').length} chars)`);
      });
    }
  }
  // Otherwise return empty
  else {
    return '';
  }

  if (documents.length === 0) {
    return '';
  }

  // Format documents
  const parts: string[] = [];
  for (const doc of documents) {
    const title = doc.title || doc.id;
    const content = doc.content || '';
    if (content) {
      parts.push(`### Artifact: ${title}\n${content}`);
    }
  }

  return parts.join('\n\n');
}

