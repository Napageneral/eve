package engine

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/tylerchilds/eve/internal/db"
	"github.com/tylerchilds/eve/internal/encoding"
	"github.com/tylerchilds/eve/internal/gemini"
	"github.com/tylerchilds/eve/internal/queue"
)

// AnalysisJobPayload is the payload for conversation analysis jobs
type AnalysisJobPayload struct {
	ConversationID int    `json:"conversation_id"`
	EvePromptID    string `json:"eve_prompt_id"` // e.g., "convo-all-v1"
}

// AnalysisJobHandler processes conversation analysis jobs
type AnalysisJobHandler struct {
	warehouseDB  *sql.DB
	geminiClient *gemini.Client
	model        string
}

// NewAnalysisJobHandler creates a new analysis job handler
func NewAnalysisJobHandler(warehouseDB *sql.DB, geminiClient *gemini.Client, model string) JobHandler {
	h := &AnalysisJobHandler{
		warehouseDB:  warehouseDB,
		geminiClient: geminiClient,
		model:        model,
	}
	return func(ctx context.Context, job *queue.Job) error {
		return h.handleJob(ctx, job.PayloadJSON)
	}
}

// handleJob processes an analysis job
func (h *AnalysisJobHandler) handleJob(ctx context.Context, payloadJSON string) error {
	// Parse payload
	var payload AnalysisJobPayload
	if err := json.Unmarshal([]byte(payloadJSON), &payload); err != nil {
		return fmt.Errorf("failed to parse payload: %w", err)
	}

	// Read conversation from database
	reader := db.NewConversationReader(h.warehouseDB)
	conversation, err := reader.GetConversation(payload.ConversationID)
	if err != nil {
		return fmt.Errorf("failed to read conversation: %w", err)
	}

	// Encode conversation
	opts := encoding.DefaultEncodeOptions()
	opts.IncludeSendTime = true
	encodedText := encoding.EncodeConversation(*conversation, opts)

	// Build simple analysis prompt
	promptText := buildAnalysisPrompt(encodedText)

	// Build Gemini request
	req := &gemini.GenerateContentRequest{
		Contents: []gemini.Content{
			{
				Role: "user",
				Parts: []gemini.Part{
					{Text: promptText},
				},
			},
		},
	}

	// Call Gemini for analysis
	resp, err := h.geminiClient.GenerateContent(h.model, req)
	if err != nil {
		return fmt.Errorf("gemini analysis failed: %w", err)
	}

	// Parse result to get analysis text
	analysisText := extractTextFromResponse(resp)

	// Persist to database
	err = h.persistAnalysis(payload.ConversationID, payload.EvePromptID, analysisText, resp)
	if err != nil {
		return fmt.Errorf("failed to persist analysis: %w", err)
	}

	return nil
}

// buildAnalysisPrompt creates a simple analysis prompt
func buildAnalysisPrompt(conversationText string) string {
	return fmt.Sprintf(`Analyze the following conversation and provide insights about:
1. Main topics discussed
2. Emotional tone
3. Key entities or people mentioned
4. Any action items or commitments

Conversation:
%s

Provide your analysis in a structured format.`, conversationText)
}

// extractTextFromResponse extracts the text content from Gemini response
func extractTextFromResponse(resp *gemini.GenerateContentResponse) string {
	if resp == nil || len(resp.Candidates) == 0 {
		return ""
	}

	candidate := resp.Candidates[0]
	if len(candidate.Content.Parts) == 0 {
		return ""
	}

	return candidate.Content.Parts[0].Text
}

// persistAnalysis saves the analysis result to the database
func (h *AnalysisJobHandler) persistAnalysis(conversationID int, evePromptID string, analysisText string, resp *gemini.GenerateContentResponse) error {
	// First, create a completion record
	resultJSON, err := json.Marshal(resp)
	if err != nil {
		return fmt.Errorf("failed to marshal result: %w", err)
	}

	// Insert completion
	var completionID int64
	err = h.warehouseDB.QueryRow(`
		INSERT INTO completions (conversation_id, model, result, created_at)
		VALUES (?, ?, ?, ?)
		RETURNING id
	`, conversationID, h.model, string(resultJSON), time.Now()).Scan(&completionID)
	if err != nil {
		return fmt.Errorf("failed to insert completion: %w", err)
	}

	// Insert or update conversation_analysis
	_, err = h.warehouseDB.Exec(`
		INSERT INTO conversation_analyses (
			conversation_id, eve_prompt_id, status, completion_id, created_at, updated_at
		)
		VALUES (?, ?, 'completed', ?, ?, ?)
		ON CONFLICT (conversation_id, prompt_template_id) DO UPDATE SET
			status = 'completed',
			completion_id = excluded.completion_id,
			updated_at = excluded.updated_at,
			retry_count = 0,
			error_message = NULL
	`, conversationID, evePromptID, completionID, time.Now(), time.Now())

	if err != nil {
		return fmt.Errorf("failed to insert/update conversation_analysis: %w", err)
	}

	return nil
}
