// Retrieval adapter interface

export interface RetrievalContext {
  sourceChat?: number;
  vars: Record<string, any>;
  prompt?: any;  // Optional - used for variable substitution
  dbPath?: string; // Database path for direct access
}

export interface RetrievalResult {
  text: string;
  actualTokens?: number;
}

export type RetrievalAdapter = (
  params: Record<string, any>,
  context: RetrievalContext
) => Promise<RetrievalResult>;

