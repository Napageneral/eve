// Update document prompt builder
// Dynamic TypeScript function for Eve

export function buildUpdateDocument(vars: {
  currentContent: string | null;
  type: 'text' | 'code' | 'sheet';
}): string {
  if (vars.type === 'text') {
    return `Improve the following contents of the document based on the given prompt.

${vars.currentContent ?? ''}
`;
  }
  
  if (vars.type === 'code') {
    return `Improve the following code snippet based on the given prompt.

${vars.currentContent ?? ''}
`;
  }
  
  if (vars.type === 'sheet') {
    return `Improve the following spreadsheet based on the given prompt.

${vars.currentContent ?? ''}
`;
  }
  
  return '';
}

