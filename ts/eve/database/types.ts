/**
 * Database Row Types
 * 
 * TypeScript interfaces matching SQLite schema from backend/db/
 * All fields are nullable to match database reality (better-sqlite3 behavior)
 */

// ============================================================================
// iMessage Domain (from etl_models.py)
// ============================================================================

export interface Contact {
  id: number;
  name: string | null;
  nickname: string | null;
  avatar: Buffer | null;
  last_updated: string | null; // ISO string
  data_source: string | null;
  is_me: number; // SQLite boolean (0 or 1)
}

export interface ContactIdentifier {
  id: number;
  contact_id: number;
  identifier: string;
  type: string; // 'email' or 'phone'
  is_primary: number; // SQLite boolean
  last_used: string | null; // ISO string
}

export interface Chat {
  id: number;
  chat_identifier: string;
  chat_name: string | null;
  created_date: string | null; // ISO string
  last_message_date: string | null; // ISO string
  is_group: number; // SQLite boolean
  service_name: string | null;
  is_blocked: number; // SQLite boolean
  total_messages: number;
  last_embedding_update: string | null; // ISO string
  wrapped_in_progress: number; // SQLite boolean
  wrapped_done: number; // SQLite boolean
}

export interface ChatParticipant {
  chat_id: number;
  contact_id: number;
}

export interface Conversation {
  id: number;
  chat_id: number;
  initiator_id: number | null;
  start_time: string; // ISO string
  end_time: string; // ISO string
  message_count: number;
  summary: string | null;
  gap_threshold: number | null;
}

export interface Message {
  id: number;
  chat_id: number;
  sender_id: number | null;
  content: string | null;
  timestamp: string; // ISO string
  is_from_me: number; // SQLite boolean
  message_type: number;
  service_name: string | null;
  guid: string;
  associated_message_guid: string | null;
  reply_to_guid: string | null;
  conversation_id: number | null;
}

export interface Reaction {
  id: number;
  original_message_guid: string;
  timestamp: string; // ISO string
  sender_id: number;
  chat_id: number;
  reaction_type: number;
  guid: string;
}

export interface Attachment {
  id: number;
  message_id: number;
  file_name: string | null;
  mime_type: string | null;
  size: number | null;
  created_date: string | null; // ISO string
  is_sticker: number; // SQLite boolean
  guid: string;
  uti: string | null;
}

// ============================================================================
// Analysis Domain (from models.py)
// ============================================================================

export interface ConversationAnalysis {
  id: number;
  conversation_id: number;
  prompt_template_id: number | null;
  eve_prompt_id: string | null;
  status: string; // 'pending' | 'processing' | 'completed' | 'failed'
  temporal_workflow_id: string | null;
  completion_id: number | null;
  error_message: string | null;
  retry_count: number;
  created_at: string; // ISO string
  updated_at: string; // ISO string
}

export interface Completion {
  id: number;
  conversation_id: number | null;
  chat_id: number | null;
  contact_id: number | null;
  prompt_template_id: number;
  compiled_prompt_text: string | null;
  model: string;
  result: any; // JSON
  created_at: string; // ISO string
}

export interface Entity {
  id: number;
  conversation_id: number | null;
  chat_id: number | null;
  contact_id: number | null;
  title: string;
  created_at: string; // ISO string
}

export interface Topic {
  id: number;
  conversation_id: number | null;
  chat_id: number | null;
  contact_id: number | null;
  title: string;
  created_at: string; // ISO string
}

export interface Emotion {
  id: number;
  conversation_id: number | null;
  chat_id: number | null;
  contact_id: number | null;
  emotion_type: string;
  created_at: string; // ISO string
}

export interface HumorItem {
  id: number;
  conversation_id: number | null;
  chat_id: number | null;
  contact_id: number | null;
  snippet: string;
  created_at: string; // ISO string
}

// ============================================================================
// Commitment Domain (from models.py)
// ============================================================================

export interface Commitment {
  id: number;
  commitment_id: string;
  conversation_id: number;
  chat_id: number;
  contact_id: number;
  to_person_id: number;
  commitment_text: string;
  context: string | null;
  created_date: string; // ISO string
  due_date: string | null; // ISO date
  due_specificity: string; // 'explicit' | 'inferred' | 'vague'
  status: string; // 'pending' | 'monitoring_condition' | 'completed' | 'cancelled' | 'failed'
  priority: string | null; // 'high' | 'medium' | 'low'
  condition: string | null;
  modifications: any; // JSON array
  reminder_data: any | null; // JSON
  completed_date: string | null; // ISO string
  resolution_method: string | null;
  final_due_date: string | null; // ISO date
  analysis_timestamp: string | null; // ISO string
  message_timestamp: string | null; // ISO string
  analysis_generation_id: string | null;
  source_conversation_id: number | null;
  last_modified_conversation_id: number | null;
  created_at: string; // ISO string
  updated_at: string; // ISO string
}

// ============================================================================
// Chatbot Domain (from models.py)
// ============================================================================

export interface ChatbotUser {
  id: string; // UUID
  email: string;
  password: string | null;
  created_at: string; // ISO string
  updated_at: string; // ISO string
}

export interface ChatbotThread {
  id: string; // UUID
  created_at: string; // ISO string
  title: string;
  user_id: string; // UUID
  visibility: string; // 'public' | 'private'
}

export interface ChatbotMessage {
  id: string; // UUID
  chat_id: string; // UUID
  role: string; // 'user' | 'assistant' | 'system'
  parts: any; // JSON
  attachments: any; // JSON
  created_at: string; // ISO string
}

export interface ChatbotDocument {
  id: string; // UUID
  created_at: string; // ISO string (also part of composite PK)
  title: string;
  content: string | null;
  kind: string; // 'text' | 'code' | 'image' | 'sheet'
  user_id: string; // UUID
  origin_chat_id: string | null; // UUID
}

// ============================================================================
// Helper Types
// ============================================================================

/**
 * Extended message with joined data
 */
export interface MessageWithSender extends Message {
  sender_name?: string;
  sender_nickname?: string;
  sender_is_me?: number;
}

/**
 * Extended conversation with analysis data
 */
export interface ConversationWithAnalysis extends Conversation {
  analyses?: ConversationAnalysis[];
  chat_name?: string;
  participant_names?: string[];
}

/**
 * Analysis facet results (entities, topics, emotions, humor)
 */
export interface AnalysisFacets {
  entities?: Entity[];
  topics?: Topic[];
  emotions?: Emotion[];
  humor_items?: HumorItem[];
}

/**
 * Chat with participant info
 */
export interface ChatWithParticipants extends Chat {
  participant_names?: string[];
  participant_ids?: number[];
}

