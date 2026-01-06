import { RetrievalAdapter } from './types.js';

export const currentMessagesAdapter: RetrievalAdapter = async (params, context) => {
  const chatId = params.chat_id || context.sourceChat;
  const limit = params.limit || 5;

  if (!chatId) {
    return { text: '', actualTokens: 0 };
  }

  const response = await fetch(`http://127.0.0.1:8000/api/chats/${chatId}/messages?limit=${limit}`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!response.ok) {
    return { text: '', actualTokens: 0 };
  }

  const data = await response.json();
  const messages = data.messages || [];
  
  const text = messages
    .map((m: any) => `${m.role}: ${m.content}`)
    .join('\n');
  
  return {
    text,
    actualTokens: Math.ceil(text.length / 4),
  };
};

