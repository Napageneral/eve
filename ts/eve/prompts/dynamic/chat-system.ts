// Chat system prompt builder
// Dynamic TypeScript function for Eve

export type RequestHints = {
  longitude?: number;
  latitude?: number;
  city?: string;
  country?: string;
};

export function buildChatSystem(vars: {
  modelId: string;
  hints?: RequestHints;
  plan?: string;
}): string {
  const regularPrompt = 'You are a friendly assistant! Keep your responses concise and helpful.';
  
  const requestHintsText = (requestHints?: RequestHints): string => {
    if (!requestHints) return '';
    const { latitude, longitude, city, country } = requestHints;
    const lines: string[] = ["About the origin of user's request:"];
    if (typeof latitude === 'number') lines.push(`- lat: ${latitude}`);
    if (typeof longitude === 'number') lines.push(`- lon: ${longitude}`);
    if (city) lines.push(`- city: ${city}`);
    if (country) lines.push(`- country: ${country}`);
    return lines.join('\n');
  };
  
  const base = [regularPrompt];
  if (vars.hints) base.push(requestHintsText(vars.hints));
  
  // Note: artifactsRules are now in artifact-rules-full always-on pack
  // Model check for reasoning models (don't include artifact rules)
  const excludeArtifactRules = vars.modelId === 'chat-model-reasoning';
  
  const body = base.filter(Boolean).join('\n\n');
  const plan = (vars.plan && vars.plan.trim()) ? `\n\n${vars.plan.trim()}` : '';
  
  return body + plan;
}

