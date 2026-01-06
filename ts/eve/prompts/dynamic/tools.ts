// Tools prompts
// Dynamic TypeScript functions for Eve

export function buildReportGuidance(vars: { titleHint?: string; schemaJson?: any }): string {
  const titleHint = vars.titleHint ? `\nSuggested title: "${vars.titleHint}"` : '';
  const schemaHint = vars.schemaJson ? `\n\nWhen possible, reflect this JSON schema in your structure:\n${JSON.stringify(vars.schemaJson, null, 2)}` : '';
  return (
    'Use the following compiled report prompt. Stream your answer and create a document artifact with the full report.' +
    ' If a schema is provided, format headings/sections to match it.' +
    titleHint +
    schemaHint +
    '\n\n'
  );
}

export function buildWriterSuggestions(): string {
  return 'You are a help writing assistant. Given a piece of writing, offer up to 5 suggestions that replace full sentences; include a short description.';
}

