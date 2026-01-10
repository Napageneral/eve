package contextengine

import "fmt"

// registry of retrieval adapters
var retrievalAdapters = map[string]RetrievalAdapter{
	"static_snippet":      staticSnippetAdapter,
	"convos_context_data": convosContextDataAdapter,
}

// staticSnippetAdapter returns static text from params.text
// This is the simplest retrieval adapter - just returns the provided text
func staticSnippetAdapter(params map[string]interface{}, context RetrievalContext) (RetrievalResult, error) {
	text, ok := params["text"].(string)
	if !ok {
		return RetrievalResult{}, fmt.Errorf("static_snippet requires 'text' parameter as string")
	}

	// Estimate tokens: rough approximation of length / 4
	actualTokens := len(text) / 4

	return RetrievalResult{
		Text:         text,
		ActualTokens: actualTokens,
	}, nil
}
