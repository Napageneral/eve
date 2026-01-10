package contextengine

import (
	"fmt"
	"regexp"
	"strings"
)

// substituteVariables replaces {{variable}} templates in params with actual values from context
// Handles strings, arrays, and nested objects recursively
func substituteVariables(params map[string]interface{}, context RetrievalContext) (map[string]interface{}, error) {
	result := make(map[string]interface{})
	for key, value := range params {
		resolved, err := resolveValue(value, context)
		if err != nil {
			return nil, err
		}
		result[key] = resolved
	}
	return result, nil
}

// resolveValue resolves a single value, handling strings, arrays, and nested objects
//
// Variable syntax:
// - Pure variable: {{varName}} → replaced with value
// - Template string: "{{var1}} : {{var2}}" → both vars replaced
//
// Built-in variables:
// - {{source_chat}} → context.SourceChat
//
// User variables:
// - {{user_var}} → context.Vars["user_var"]
func resolveValue(value interface{}, context RetrievalContext) (interface{}, error) {
	// Handle template strings with embedded variables
	if str, ok := value.(string); ok {
		// Check if it's a pure variable: {{varName}}
		if strings.HasPrefix(str, "{{") && strings.HasSuffix(str, "}}") && strings.Count(str, "{{") == 1 {
			varName := strings.TrimSpace(str[2 : len(str)-2])
			resolved, err := getVariable(varName, context)
			if err != nil {
				return nil, err
			}
			return resolved, nil
		}

		// Handle template strings with embedded variables: "{{var1}} : Some Text"
		if strings.Contains(str, "{{") {
			re := regexp.MustCompile(`\{\{([^}]+)\}\}`)
			result := str
			var lastErr error

			result = re.ReplaceAllStringFunc(result, func(match string) string {
				varName := strings.TrimSpace(match[2 : len(match)-2])
				resolved, err := getVariable(varName, context)
				if err != nil {
					lastErr = err
					return match
				}
				return fmt.Sprintf("%v", resolved)
			})

			if lastErr != nil {
				return nil, lastErr
			}

			return result, nil
		}

		// Plain string, no variables
		return str, nil
	}

	// Recursively handle arrays
	if arr, ok := value.([]interface{}); ok {
		result := make([]interface{}, len(arr))
		for i, v := range arr {
			resolved, err := resolveValue(v, context)
			if err != nil {
				return nil, err
			}
			result[i] = resolved
		}
		return result, nil
	}

	// Recursively handle nested objects
	if obj, ok := value.(map[string]interface{}); ok {
		result := make(map[string]interface{})
		for k, v := range obj {
			resolved, err := resolveValue(v, context)
			if err != nil {
				return nil, err
			}
			result[k] = resolved
		}
		return result, nil
	}

	// Primitives (numbers, booleans, nil) pass through
	return value, nil
}

// getVariable retrieves a variable value from the context
func getVariable(name string, context RetrievalContext) (interface{}, error) {
	// Built-in variables
	if name == "source_chat" {
		return context.SourceChat, nil
	}

	// User variables
	if val, ok := context.Vars[name]; ok {
		return val, nil
	}

	return nil, fmt.Errorf("variable %s not found in context", name)
}

// substituteInPrompt replaces {{variable}} templates in the prompt body
// Supports both {{var}} and {{{var}}} syntax
func substituteInPrompt(text string, vars map[string]interface{}) string {
	result := text

	for key, value := range vars {
		// Replace {{{variable_name}}}
		triplePattern := regexp.MustCompile(`\{\{\{` + regexp.QuoteMeta(key) + `\}\}\}`)
		result = triplePattern.ReplaceAllString(result, fmt.Sprintf("%v", value))

		// Replace {{variable_name}}
		doublePattern := regexp.MustCompile(`\{\{` + regexp.QuoteMeta(key) + `\}\}`)
		result = doublePattern.ReplaceAllString(result, fmt.Sprintf("%v", value))
	}

	return result
}
