import { RetrievalAdapter } from './types.js';

export const suggestionHistoryAdapter: RetrievalAdapter = async (params, context) => {
  const chatId = params.chat_id || context.sourceChat;
  const limit = params.limit || 20;

  const response = await fetch(`http://127.0.0.1:8000/api/suggestions/history?chat_id=${chatId}&limit=${limit}`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!response.ok) {
    return { text: '[]', actualTokens: 10 };
  }

  const data = await response.json();
  const text = JSON.stringify(data.suggestions || [], null, 2);
  
  return {
    text,
    actualTokens: Math.ceil(text.length / 4),
  };
};

