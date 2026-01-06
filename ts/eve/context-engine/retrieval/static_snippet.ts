import { RetrievalAdapter } from './types.js';

export const staticSnippetAdapter: RetrievalAdapter = async (params, _context) => {
  const text = params.text || '';
  
  return {
    text,
    actualTokens: Math.ceil(text.length / 4),
  };
};

