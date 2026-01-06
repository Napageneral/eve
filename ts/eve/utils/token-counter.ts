/**
 * Token Counting Utilities
 * 
 * Ported from backend/services/core/token.py
 * Uses tiktoken-js for token counting
 */

import { get_encoding, Tiktoken } from 'tiktoken';

// Initialize tokenizer once (lazy load)
let tokenizer: Tiktoken | null = null;

function getTokenizer(): Tiktoken {
  if (!tokenizer) {
    tokenizer = get_encoding('o200k_base');
  }
  return tokenizer;
}

/**
 * Count tokens in text using tiktoken
 */
export function countTokens(text: string): number {
  if (!text) {
    return 0;
  }

  try {
    const tokens = getTokenizer().encode(text);
    return tokens.length;
  } catch (error) {
    console.error('Token counting failed:', error);
    // Fallback to rough estimate: ~4 chars per token
    return Math.ceil(text.length / 4);
  }
}

/**
 * Estimate tokens for multiple strings
 */
export function countTokensMultiple(texts: string[]): number {
  return texts.reduce((total, text) => total + countTokens(text), 0);
}

/**
 * Check if text exceeds token budget
 */
export function exceedsTokenBudget(text: string, budget: number): boolean {
  return countTokens(text) > budget;
}

/**
 * Truncate text to fit within token budget
 * @param text Text to truncate
 * @param budget Maximum tokens
 * @param suffix Suffix to add if truncated (e.g., "...")
 * @returns Truncated text
 */
export function truncateToTokenBudget(
  text: string,
  budget: number,
  suffix: string = '...'
): string {
  const currentTokens = countTokens(text);
  
  if (currentTokens <= budget) {
    return text;
  }

  const suffixTokens = countTokens(suffix);
  const targetTokens = budget - suffixTokens;
  
  // Binary search for the right length
  let left = 0;
  let right = text.length;
  
  while (left < right) {
    const mid = Math.floor((left + right + 1) / 2);
    const candidate = text.substring(0, mid);
    
    if (countTokens(candidate) <= targetTokens) {
      left = mid;
    } else {
      right = mid - 1;
    }
  }
  
  return text.substring(0, left) + suffix;
}

/**
 * Calculate cost based on token counts and model pricing
 * 
 * Note: Pricing data would need to be ported from Python if needed.
 * For now, this is a stub that returns 0.
 */
export function calculateCost(
  inputTokens: number,
  outputTokens: number,
  model: string
): number {
  // TODO: Port pricing data from backend/services/llm/models.py if needed
  // For now, return 0 as placeholder
  return 0;
}

