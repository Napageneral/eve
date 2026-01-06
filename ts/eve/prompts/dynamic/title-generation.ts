// Title generation prompt builders
// Dynamic TypeScript functions for Eve

export function buildTitleFromMessage(vars: { messageText: string }): string {
  const systemPrompt = 'Return ONLY a concise title of 2-4 words. No punctuation beyond spaces. No quotes. No emojis. Avoid generic words. Output the title only.';
  const userPrompt = `Title the chat based on the user message. 2-4 words only. Message: ${String(vars.messageText || '').slice(0, 500)}`;
  
  return `${systemPrompt}\n\n${userPrompt}`;
}

export function buildTitleFromHistory(vars: {
  prior?: string;
  historyText?: string;
  userText?: string;
  assistantText?: string;
}): string {
  const systemPrompt = 'Return ONLY a concise title of 2-4 words. No punctuation beyond spaces. No quotes. No emojis. Avoid generic or vague words. Output the title only.';
  
  const lines = [
    vars.prior ? `Current title: ${vars.prior}` : '',
    vars.historyText ? `Conversation:\n${vars.historyText}` : '',
    vars.userText ? `Latest user: ${String(vars.userText).slice(0, 160)}` : '',
    vars.assistantText ? `Latest assistant: ${String(vars.assistantText).slice(0, 160)}` : '',
    'New 2-4 word title only:',
  ].filter(Boolean);
  
  return `${systemPrompt}\n\n${lines.join('\n')}`;
}

