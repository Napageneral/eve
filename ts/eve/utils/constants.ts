/**
 * Constants
 * 
 * Ported from backend/services/core/constants.py
 * Only includes constants needed for retrieval and encoding
 */

/**
 * Reaction type to emoji mapping
 */
export const REACTION_EMOJIS: Record<number, string> = {
  2000: '❤️',  // Love
  2001: '👍',  // Like
  2002: '👎',  // Dislike
  2003: '😂',  // Laugh
  2004: '‼️',  // Emphasis
  2005: '❓',  // Question
};

/**
 * Context window configuration for conversation encoding
 */
export const DEFAULT_CONTEXT_WINDOW = 30; // Target total messages across all conversations
export const MIN_PREVIOUS_CONVERSATIONS = 1; // Always include at least this many previous conversations
export const LOOKBACK_DAYS = 7;

/**
 * Common message GUID namespace (if needed for hashing)
 */
export const MESSAGE_GUID_NAMESPACE = 'chatstats-message';

/**
 * Default timeouts and limits
 */
export const DEFAULT_LLM_TIMEOUT = 300000; // 5 minutes in ms
export const DEFAULT_RETRY_COUNT = 3;
export const DEFAULT_RETRY_DELAY = 1000; // 1 second in ms

/**
 * Preview length limits
 */
export const MAX_PROMPT_PREVIEW_LENGTH = 500;
export const MAX_RESPONSE_PREVIEW_LENGTH = 500;
export const MAX_TOKEN_COUNT_DISPLAY = 200000;

