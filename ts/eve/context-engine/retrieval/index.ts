import { RetrievalAdapter } from './types.js';
import { analysesContextAdapter } from './analyses.js';
import { convosContextAdapter } from './convos.js';
import { artifactsContextAdapter } from './artifacts.js';
import { suggestionHistoryAdapter } from './history.js';
import { currentMessagesAdapter } from './current_messages.js';
import { staticSnippetAdapter } from './static_snippet.js';
import { userNameAdapter, chatTextAdapter, rawConversationAdapter } from './simple.js';

export const RETRIEVAL_ADAPTERS: Record<string, RetrievalAdapter> = {
  // Full names (used by YAML packs)
  analyses_context_data: analysesContextAdapter,
  convos_context_data: convosContextAdapter,
  artifacts_context_data: artifactsContextAdapter,
  suggestion_history: suggestionHistoryAdapter,
  current_messages: currentMessagesAdapter,
  static_snippet: staticSnippetAdapter,
  user_name_data: userNameAdapter,
  chat_text_data: chatTextAdapter,
  raw_conversation_text_data: rawConversationAdapter,
  
  // Short aliases (used by dynamic packs from Context Agent)
  analyses: analysesContextAdapter,
  convos: convosContextAdapter,
  artifacts: artifactsContextAdapter,
};

export * from './types.js';
export {
  analysesContextAdapter,
  convosContextAdapter,
  artifactsContextAdapter,
  suggestionHistoryAdapter,
  currentMessagesAdapter,
  staticSnippetAdapter,
  userNameAdapter,
  chatTextAdapter,
  rawConversationAdapter,
};

