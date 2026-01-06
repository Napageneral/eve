/**
 * Conversation Encoding
 * 
 * Ported from backend/services/encoding/conversation_encoding.py
 * Handles generic conversation encoding for analysis, embeddings, and retrieval
 */

import { REACTION_EMOJIS } from '../utils/constants.js';

export interface EncodedMessage {
  id: number;
  guid: string;
  timestamp: string; // ISO string
  sender_id: number | null;
  sender_name?: string;
  text?: string;
  content?: string;
  is_from_me?: number | boolean;
  sender_is_me?: number | boolean;
  attachments?: Array<{
    id: number;
    mime_type?: string;
    file_name?: string;
    filename?: string; // Support both spellings
    is_sticker?: number | boolean;
  }>;
  reactions?: Array<{
    reaction_type?: number;
    associated_message_type?: number; // Legacy field name
    sender_id: number;
    sender_name?: string;
    is_from_me?: number | boolean;
  }>;
  conversation_id: number;
  chat_id: number;
}

export interface EncodedConversation {
  id: number;
  chat_id: number;
  start_time: string; // ISO string
  end_time: string; // ISO string
  messages: EncodedMessage[];
}

export interface EncodeOptions {
  includeSender?: boolean;
  includeAttachments?: boolean;
  includeReactions?: boolean;
  includeStartDate?: boolean;
  includeSendTime?: boolean;
}

/**
 * Parse timestamp to Date object with fallbacks
 */
function parseTimestamp(value: any): Date {
  if (value instanceof Date) {
    return value;
  }

  if (typeof value === 'string') {
    let s = value;
    // Handle ISO8601 with 'Z' suffix
    if (s.endsWith('Z')) {
      s = s.slice(0, -1) + '+00:00';
    }
    try {
      return new Date(s);
    } catch {
      // Fallback: try parsing as seconds since epoch
      try {
        return new Date(parseFloat(s) * 1000);
      } catch {
        console.warn(`Unable to parse timestamp '${value}'; using epoch`);
        return new Date(0);
      }
    }
  }

  if (typeof value === 'number') {
    // Assume seconds since epoch
    return new Date(value * 1000);
  }

  console.warn(`Unexpected timestamp type ${typeof value}; using epoch`);
  return new Date(0);
}

/**
 * Format timestamp as readable string
 */
function formatTimestamp(date: Date, includeDate: boolean = false): string {
  if (includeDate) {
    // Format: "Monday Oct 27, 2025 - 3:45pm"
    const options: Intl.DateTimeFormatOptions = {
      weekday: 'long',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    };
    return new Intl.DateTimeFormat('en-US', options).format(date).replace(/AM|PM/, (m) => m.toLowerCase());
  } else {
    // Format: "3:45pm"
    const options: Intl.DateTimeFormatOptions = {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    };
    return new Intl.DateTimeFormat('en-US', options).format(date).replace(/AM|PM/, (m) => m.toLowerCase());
  }
}

/**
 * Format reactions for a message
 */
function formatReactions(reactions: EncodedMessage['reactions']): string {
  if (!reactions || reactions.length === 0) {
    return '';
  }

  const reactionCounts: Record<string, number> = {};
  
  for (const react of reactions) {
    const rType = react.associated_message_type || react.reaction_type;
    if (rType !== undefined && rType in REACTION_EMOJIS) {
      const emoji = REACTION_EMOJIS[rType];
      reactionCounts[emoji] = (reactionCounts[emoji] || 0) + 1;
    }
  }

  if (Object.keys(reactionCounts).length === 0) {
    return '';
  }

  const parts = Object.entries(reactionCounts).map(([emoji, count]) =>
    count > 1 ? `${emoji}(${count})` : emoji
  );

  return `[${parts.join(', ')}]`;
}

/**
 * Encode a single message into text format
 */
export function encodeMessage(
  message: EncodedMessage,
  options: EncodeOptions = {}
): string {
  const {
    includeSender = true,
    includeAttachments = true,
    includeReactions = true,
    includeSendTime = false,
  } = options;

  const parts: string[] = [];

  // Add timestamp if requested
  if (includeSendTime) {
    const ts = parseTimestamp(message.timestamp);
    const timeStr = formatTimestamp(ts, false);
    parts.push(`[${timeStr}]`);
  }

  // Add sender name
  if (includeSender) {
    const sender = message.sender_name || 'Unknown';
    parts.push(`${sender}:`);
  }

  // Add message text (support both 'text' and 'content' fields)
  const textValue = message.text || message.content;
  if (textValue) {
    parts.push(textValue);
  }

  // Add attachments
  if (includeAttachments && message.attachments && message.attachments.length > 0) {
    const attTexts: string[] = [];
    for (const att of message.attachments) {
      if (att.mime_type?.startsWith('image/')) {
        attTexts.push('[Image]');
      } else {
        const filename = att.filename || att.file_name || 'Unknown file';
        attTexts.push(`[Attachment: ${filename}]`);
      }
    }
    if (attTexts.length > 0) {
      parts.push(attTexts.join(' '));
    }
  }

  // Add reactions
  if (includeReactions && message.reactions) {
    const reactionText = formatReactions(message.reactions);
    if (reactionText) {
      parts.push(reactionText);
    }
  }

  return parts.join(' ');
}

/**
 * Encode a conversation into text format
 */
export function encodeConversation(
  conversation: EncodedConversation,
  options: EncodeOptions = {}
): string {
  const {
    includeSender = true,
    includeAttachments = true,
    includeReactions = true,
    includeStartDate = false,
    includeSendTime = false,
  } = options;

  // Sort messages by timestamp
  const messages = [...conversation.messages].sort((a, b) => {
    const dateA = parseTimestamp(a.timestamp);
    const dateB = parseTimestamp(b.timestamp);
    return dateA.getTime() - dateB.getTime();
  });

  const encodedLines: string[] = [];

  // Optional date header
  if (includeStartDate && messages.length > 0) {
    const startTime = parseTimestamp(messages[0].timestamp);
    const dateHeader = formatTimestamp(startTime, true);
    encodedLines.push(`=== ${dateHeader} ===`);
  }

  // Encode each message
  for (const msg of messages) {
    const encodedMsg = encodeMessage(msg, {
      includeSender,
      includeAttachments,
      includeReactions,
      includeSendTime,
    });
    if (encodedMsg) {
      encodedLines.push(encodedMsg);
    }
  }

  return encodedLines.join('\n');
}

/**
 * Encode multiple conversations into a map of id -> text
 */
export function encodeConversations(
  conversations: EncodedConversation[],
  options: EncodeOptions = {}
): Record<string, string> {
  const result: Record<string, string> = {};

  for (const conv of conversations) {
    result[conv.id.toString()] = encodeConversation(conv, options);
  }

  return result;
}

