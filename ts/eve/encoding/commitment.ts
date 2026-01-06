/**
 * Commitment Encoding
 * 
 * Ported from backend/services/encoding/commitment_encoding.py
 * Handles commitment-specific encoding with context window rules
 * 
 * NOTE: Commitments feature is PAUSED but preserved for future use
 */

import { DatabaseClient } from '../database/client.js';
import { Conversation, Commitment, Contact, Chat } from '../database/types.js';
import * as conversationsQueries from '../database/queries/conversations.js';
import * as chatsQueries from '../database/queries/chats.js';
import * as contactsQueries from '../database/queries/contacts.js';
import { encodeConversation, EncodedConversation } from './conversation.js';
import {
  DEFAULT_CONTEXT_WINDOW,
  MIN_PREVIOUS_CONVERSATIONS,
  LOOKBACK_DAYS,
} from '../utils/constants.js';

/**
 * Get previous conversations for context window
 */
function getPreviousConversations(
  db: DatabaseClient,
  chatId: number,
  currentConversationId: number,
  currentMessageCount: number,
  targetTotalMessages: number = DEFAULT_CONTEXT_WINDOW,
  lookbackDays: number = LOOKBACK_DAYS,
  minConversations: number = MIN_PREVIOUS_CONVERSATIONS
): EncodedConversation[] {
  // Get cutoff date
  const cutoffDate = new Date();
  cutoffDate.setDate(cutoffDate.getDate() - lookbackDays);
  const cutoffDateStr = cutoffDate.toISOString();

  // Get conversations before current one
  const sql = `
    SELECT *
    FROM conversations
    WHERE chat_id = :chatId
      AND id < :currentConversationId
      AND start_time >= :cutoffDate
    ORDER BY start_time DESC
    LIMIT 20
  `;

  const conversations = db.queryAll<Conversation>(sql, {
    chatId,
    currentConversationId,
    cutoffDate: cutoffDateStr,
  });

  if (conversations.length === 0) {
    return [];
  }

  // Select conversations to include based on message count budget
  const selected: EncodedConversation[] = [];
  let totalMessages = currentMessageCount;

  for (let i = 0; i < conversations.length; i++) {
    const conv = conversations[i];
    
    // Load messages for this conversation
    const messages = conversationsQueries.getMessagesForConversation(db, conv.id);
    const convMessageCount = messages.length;

    // Include if within min conversations OR within message budget
    if (i < minConversations || totalMessages + convMessageCount <= targetTotalMessages) {
      selected.push({
        ...conv,
        messages: messages as any[], // Cast to match EncodedMessage type
      });
      totalMessages += convMessageCount;
    } else {
      break;
    }
  }

  // Reverse to chronological order
  selected.reverse();
  return selected;
}

/**
 * Get commitment context (active and recent commitments)
 */
function getCommitmentContext(db: DatabaseClient, chatId: number): string {
  // Get active commitments
  const activeSql = `
    SELECT *
    FROM commitments
    WHERE chat_id = :chatId
      AND status IN ('pending', 'monitoring_condition')
    ORDER BY due_date ASC
  `;
  const activeCommitments = db.queryAll<Commitment>(activeSql, { chatId });

  // Get recently updated commitments (last 7 days)
  const recentCutoff = new Date();
  recentCutoff.setDate(recentCutoff.getDate() - 7);
  const recentSql = `
    SELECT *
    FROM commitments
    WHERE chat_id = :chatId
      AND status NOT IN ('pending', 'monitoring_condition')
      AND updated_at >= :cutoffDate
    ORDER BY updated_at DESC
    LIMIT 10
  `;
  const recentCommitments = db.queryAll<Commitment>(recentSql, {
    chatId,
    cutoffDate: recentCutoff.toISOString(),
  });

  const parts: string[] = [];

  if (activeCommitments.length > 0) {
    const activeText = activeCommitments
      .map((c) => `- ${c.commitment_text} (Due: ${c.due_date}, Status: ${c.status})`)
      .join('\n');
    parts.push(`### Active Commitments ###\n${activeText}`);
  }

  if (recentCommitments.length > 0) {
    const recentText = recentCommitments
      .map((c) => `- ${c.commitment_text} (Status: ${c.status}, Updated: ${c.updated_at})`)
      .join('\n');
    parts.push(`### Recently Updated Commitments ###\n${recentText}`);
  }

  return parts.length > 0 ? parts.join('\n\n') + '\n\n' : '';
}

/**
 * Get chat metadata context
 */
function getChatMetadata(db: DatabaseClient, chatId: number): string {
  const chat = chatsQueries.getChatById(db, chatId);
  if (!chat) {
    return '';
  }

  const contacts = contactsQueries.getContactsForChat(db, chatId);
  const names = contacts.map((c) => c.name || c.nickname || `Contact ${c.id}`).join(', ');

  const chatName = chat.chat_name || 'Unknown';
  return `### Chat Metadata ###\nChat: ${chatName}\nParticipants: ${names}\n\n`;
}

/**
 * Encode conversation with commitment-specific context
 */
export function encodeConversationForCommitments(
  db: DatabaseClient,
  conversation: EncodedConversation,
  chatId: number,
  isRealtime: boolean = true
): string {
  const conversationId = conversation.id;
  const currentMessages = conversation.messages || [];
  const currentMessageCount = currentMessages.length;
  const analysisType = isRealtime ? 'LIVE' : 'HISTORICAL';

  // 1. Get previous conversations for context
  const prevConvs = getPreviousConversations(
    db,
    chatId,
    conversationId,
    currentMessageCount,
    DEFAULT_CONTEXT_WINDOW,
    LOOKBACK_DAYS,
    MIN_PREVIOUS_CONVERSATIONS
  );

  // 2. Encode previous conversations
  let prevContext = '';
  if (prevConvs.length > 0) {
    const encodedPrevConvs = prevConvs.map((conv) =>
      encodeConversation(conv, {
        includeSender: true,
        includeAttachments: true,
        includeReactions: true,
        includeStartDate: true,
      })
    );
    prevContext = `### Previous Conversations ###\n${encodedPrevConvs.join('\n\n')}\n\n`;
  }

  // 3. Encode current conversation
  const currentContext =
    `### Current Conversation ###\n` +
    encodeConversation(conversation, {
      includeSender: true,
      includeAttachments: true,
      includeReactions: true,
      includeStartDate: true,
    }) +
    '\n\n';

  // 4. Get commitment and metadata context
  const commitmentCtx = getCommitmentContext(db, chatId);
  const chatMetadata = getChatMetadata(db, chatId);

  // 5. Assemble full context
  const fullContext = `${chatMetadata}${commitmentCtx}${prevContext}${currentContext}`;

  console.log(
    `[PHASE3] ${analysisType} encoded conversation ${conversationId} with ${fullContext.length} chars`
  );

  return fullContext;
}

