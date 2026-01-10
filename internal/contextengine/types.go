package contextengine

// ExecuteRequest represents a request to compile a context pack
type ExecuteRequest struct {
	PromptID       string                 `json:"prompt_id,omitempty"`
	SourceChat     int                    `json:"source_chat,omitempty"`
	Vars           map[string]interface{} `json:"vars,omitempty"`
	OverridePackID string                 `json:"override_pack_id,omitempty"`
	BudgetTokens   int                    `json:"budget_tokens,omitempty"`
}

// ExecuteResult represents a successful context compilation
type ExecuteResult struct {
	Ledger        ContextLedger `json:"ledger"`
	HiddenParts   []HiddenPart  `json:"hidden_parts"`
	VisiblePrompt string        `json:"visible_prompt"`
	Execution     ExecutionInfo `json:"execution"`
}

// ExecuteError represents a compilation failure
type ExecuteError struct {
	Kind          string                   `json:"kind"`
	Message       string                   `json:"message"`
	CurrentTokens int                      `json:"current_tokens,omitempty"`
	Budget        int                      `json:"budget,omitempty"`
	Tried         []string                 `json:"tried,omitempty"`
	Suggest       []map[string]interface{} `json:"suggest,omitempty"`
}

// ContextLedger tracks token usage across all slices
type ContextLedger struct {
	TotalTokens int          `json:"total_tokens"`
	Items       []LedgerItem `json:"items"`
}

// LedgerItem represents a single slice's token contribution
type LedgerItem struct {
	Slice     string `json:"slice"`
	PackID    string `json:"pack_id"`
	EstTokens int    `json:"est_tokens"`
	Why       string `json:"why,omitempty"`
	Encoding  string `json:"encoding,omitempty"`
}

// HiddenPart represents compiled context data (not shown to user in prompt)
type HiddenPart struct {
	Name string `json:"name"`
	Text string `json:"text"`
}

// ExecutionInfo contains metadata about how to execute the prompt
type ExecutionInfo struct {
	Mode             string   `json:"mode"`
	ResultType       string   `json:"result_type"`
	ResultTitle      string   `json:"result_title,omitempty"`
	ModelPreferences []string `json:"model_preferences,omitempty"`
	Temperature      float64  `json:"temperature,omitempty"`
	MaxTokens        int      `json:"max_tokens,omitempty"`
}

// RetrievalContext provides context for retrieval adapters
type RetrievalContext struct {
	SourceChat int
	Vars       map[string]interface{}
	DBPath     string
}

// RetrievalResult represents the result of a retrieval operation
type RetrievalResult struct {
	Text         string
	ActualTokens int
}

// RetrievalAdapter is a function that retrieves context data
type RetrievalAdapter func(params map[string]interface{}, context RetrievalContext) (RetrievalResult, error)

// CompiledPart is an internal representation of a compiled slice
type compiledPart struct {
	name      string
	text      string
	estTokens int
	packID    string
	why       string
	encoding  string
}
