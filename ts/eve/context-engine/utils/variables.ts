/**
 * Variable Resolution Utilities
 * 
 * Resolves {{variable}} template strings in pack parameters.
 * Consolidated from duplicated code in retrieval adapters.
 */

export interface ResolverContext {
  sourceChat?: number;
  vars: Record<string, any>;
}

/**
 * Resolves all variables in a params object.
 * Recursively handles nested objects and arrays.
 */
export function resolveVariables(
  params: Record<string, any>,
  context: ResolverContext
): Record<string, any> {
  const resolved: Record<string, any> = {};
  for (const [key, value] of Object.entries(params)) {
    resolved[key] = resolveValue(value, context);
  }
  return resolved;
}

/**
 * Resolves a single value, handling strings, arrays, and nested objects.
 * 
 * Variable syntax:
 * - Pure variable: {{varName}} → replaced with value
 * - Template string: "{{var1}} : {{var2}}" → both vars replaced
 * 
 * Built-in variables:
 * - {{source_chat}} → context.sourceChat
 * 
 * User variables:
 * - {{user_var}} → context.vars['user_var']
 */
export function resolveValue(value: any, context: ResolverContext): any {
  // Handle template strings with embedded variables
  if (typeof value === 'string') {
    // Check if it's a pure variable: {{varName}}
    if (value.startsWith('{{') && value.endsWith('}}')) {
      const varName = value.slice(2, -2).trim();
      if (varName === 'source_chat') return context.sourceChat;
      if (varName in context.vars) return context.vars[varName];
      throw new Error(`Variable ${varName} not found in context`);
    }
    
    // Handle template strings with embedded variables: "{{var1}} : Some Text"
    if (value.includes('{{')) {
      return value.replace(/\{\{([^}]+)\}\}/g, (match, varName) => {
        const trimmed = varName.trim();
        if (trimmed === 'source_chat') return String(context.sourceChat);
        if (trimmed in context.vars) return String(context.vars[trimmed]);
        throw new Error(`Variable ${trimmed} not found in context`);
      });
    }
    
    // Plain string, no variables
    return value;
  }

  // Recursively handle arrays
  if (Array.isArray(value)) {
    return value.map((v) => resolveValue(v, context));
  }

  // Recursively handle nested objects
  if (typeof value === 'object' && value !== null) {
    const resolved: Record<string, any> = {};
    for (const [k, v] of Object.entries(value)) {
      resolved[k] = resolveValue(v, context);
    }
    return resolved;
  }

  // Primitives (numbers, booleans, null) pass through
  return value;
}








