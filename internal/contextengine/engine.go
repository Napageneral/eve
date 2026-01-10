package contextengine

import (
	"fmt"

	"github.com/Napageneral/eve/internal/resources"
)

// Engine compiles context packs into hidden parts + visible prompt
type Engine struct {
	loader *resources.Loader
	dbPath string
}

// NewEngine creates a new context engine
func NewEngine(loader *resources.Loader, dbPath string) *Engine {
	return &Engine{
		loader: loader,
		dbPath: dbPath,
	}
}

// Execute compiles a context pack and returns the result
func (e *Engine) Execute(request ExecuteRequest) (interface{}, error) {
	// Validate request
	if request.PromptID == "" {
		return ExecuteError{
			Kind:    "ValidationError",
			Message: "prompt_id is required",
		}, nil
	}

	// Load prompt
	prompt, err := e.loader.LoadPrompt(request.PromptID)
	if err != nil {
		return ExecuteError{
			Kind:    "PromptNotFound",
			Message: fmt.Sprintf("prompt not found: %s", request.PromptID),
			Suggest: []map[string]interface{}{
				{"action": "list_prompts", "description": "Run 'eve prompt list' to see available prompts"},
			},
		}, nil
	}

	// Determine pack ID
	packID := request.OverridePackID
	if packID == "" {
		// Get default pack from prompt frontmatter
		// Try direct access first
		if defaultPack, ok := prompt.Frontmatter["default_pack"].(string); ok {
			packID = defaultPack
		} else if contextMap, ok := prompt.Frontmatter["context"].(map[string]interface{}); ok {
			// Try nested context.default_pack
			if defaultPack, ok := contextMap["default_pack"].(string); ok {
				packID = defaultPack
			}
		}

		if packID == "" {
			return ExecuteError{
				Kind:    "ValidationError",
				Message: "no pack specified and prompt has no default_pack",
			}, nil
		}
	}

	// Load pack
	pack, err := e.loader.LoadPack(packID)
	if err != nil {
		return ExecuteError{
			Kind:    "PackNotFound",
			Message: fmt.Sprintf("pack not found: %s", packID),
			Suggest: []map[string]interface{}{
				{"action": "list_packs", "description": "Run 'eve pack list' to see available packs"},
			},
		}, nil
	}

	// Build retrieval context
	context := RetrievalContext{
		SourceChat: request.SourceChat,
		Vars:       request.Vars,
		DBPath:     e.dbPath,
	}
	if context.Vars == nil {
		context.Vars = make(map[string]interface{})
	}

	// Compile slices
	parts, execErr := e.compileSlices(pack.Slices, context, packID)
	if execErr != nil {
		return *execErr, nil
	}

	// Calculate total tokens
	totalTokens := 0
	for _, part := range parts {
		totalTokens += part.estTokens
	}

	// Check budget (v1: just error if over budget, no fitting logic yet)
	budget := request.BudgetTokens
	if budget == 0 {
		budget = 300000 // Default 300k tokens
	}
	safetyFactor := 0.90 // Default safety factor
	effectiveBudget := int(float64(budget) * safetyFactor)

	if totalTokens > effectiveBudget {
		return ExecuteError{
			Kind:          "BudgetExceeded",
			Message:       fmt.Sprintf("context exceeds budget: %d tokens > %d budget (safety factor: %.2f)", totalTokens, effectiveBudget, safetyFactor),
			CurrentTokens: totalTokens,
			Budget:        effectiveBudget,
			Tried:         []string{"safety_margin_applied"},
			Suggest: []map[string]interface{}{
				{"action": "pick_alternative", "description": "Try an alternative pack with less context"},
				{"action": "reduce_time_range", "description": "Use a shorter time range (e.g., month instead of year)"},
			},
		}, nil
	}

	// Build ledger
	ledger := ContextLedger{
		TotalTokens: totalTokens,
		Items:       make([]LedgerItem, len(parts)),
	}
	for i, part := range parts {
		ledger.Items[i] = LedgerItem{
			Slice:     part.name,
			PackID:    part.packID,
			EstTokens: part.estTokens,
			Why:       part.why,
			Encoding:  part.encoding,
		}
	}

	// Build hidden parts
	hiddenParts := make([]HiddenPart, len(parts))
	for i, part := range parts {
		hiddenParts[i] = HiddenPart{
			Name: part.name,
			Text: part.text,
		}
	}

	// Get visible prompt (substitute variables)
	visiblePrompt := prompt.Body
	visiblePrompt = substituteInPrompt(visiblePrompt, context.Vars)

	// Build execution info from prompt frontmatter
	execution := ExecutionInfo{
		Mode:       "direct",
		ResultType: "document",
	}

	// Extract execution info from frontmatter if available
	if execMap, ok := prompt.Frontmatter["execution"].(map[string]interface{}); ok {
		if mode, ok := execMap["mode"].(string); ok {
			execution.Mode = mode
		}
		if resultType, ok := execMap["result_type"].(string); ok {
			execution.ResultType = resultType
		}
		if resultTitle, ok := execMap["result_title"].(string); ok {
			execution.ResultTitle = resultTitle
		}
		if temp, ok := execMap["temperature"].(float64); ok {
			execution.Temperature = temp
		}
		if maxTokens, ok := execMap["max_tokens"].(int); ok {
			execution.MaxTokens = maxTokens
		}
		if modelPrefs, ok := execMap["model_preferences"].([]interface{}); ok {
			execution.ModelPreferences = make([]string, len(modelPrefs))
			for i, m := range modelPrefs {
				if str, ok := m.(string); ok {
					execution.ModelPreferences[i] = str
				}
			}
		}
	}

	return ExecuteResult{
		Ledger:        ledger,
		HiddenParts:   hiddenParts,
		VisiblePrompt: visiblePrompt,
		Execution:     execution,
	}, nil
}

// compileSlices compiles all slices in a pack
func (e *Engine) compileSlices(slices []resources.Slice, context RetrievalContext, packID string) ([]compiledPart, *ExecuteError) {
	parts := make([]compiledPart, 0, len(slices))

	for _, slice := range slices {
		// Get retrieval adapter
		adapter, ok := retrievalAdapters[slice.Retrieval]
		if !ok {
			execErr := &ExecuteError{
				Kind:    "ValidationError",
				Message: fmt.Sprintf("unknown retrieval function: %s", slice.Retrieval),
			}
			return nil, execErr
		}

		// Substitute variables in params
		resolvedParams, err := substituteVariables(slice.Params, context)
		if err != nil {
			execErr := &ExecuteError{
				Kind:    "MissingVariable",
				Message: fmt.Sprintf("failed to resolve variables in slice %s: %s", slice.Name, err.Error()),
			}
			return nil, execErr
		}

		// Execute retrieval
		result, err := adapter(resolvedParams, context)
		if err != nil {
			execErr := &ExecuteError{
				Kind:    "RetrievalError",
				Message: fmt.Sprintf("failed to retrieve slice %s: %s", slice.Name, err.Error()),
			}
			return nil, execErr
		}

		// Use estimated tokens as baseline, or actual tokens if higher
		// This handles cases where estimated_tokens is set in pack but actual retrieval is dynamic
		estTokens := slice.EstimatedTokens
		if result.ActualTokens > estTokens {
			estTokens = result.ActualTokens
		}

		parts = append(parts, compiledPart{
			name:      slice.Name,
			text:      result.Text,
			estTokens: estTokens,
			packID:    packID,
			why:       slice.WhyInclude,
			encoding:  "", // Can be extracted from pack if needed
		})
	}

	return parts, nil
}
