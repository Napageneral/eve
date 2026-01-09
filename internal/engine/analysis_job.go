package engine

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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
	if payload.EvePromptID == "" {
		payload.EvePromptID = "convo-all-v1"
	}

	// Read conversation from database
	reader := db.NewConversationReader(h.warehouseDB)
	conversation, err := reader.GetConversation(payload.ConversationID)
	if err != nil {
		return fmt.Errorf("failed to read conversation: %w", err)
	}

	// Encode conversation
	opts := encoding.DefaultEncodeOptions()
	encodedText := encoding.EncodeConversation(*conversation, opts)

	// Build prompt (full quality: all messages, no truncation, no output caps)
	var promptText string
	switch payload.EvePromptID {
	case "convo-all-v1":
		promptText, err = buildConvoAllV1Prompt(encodedText)
		if err != nil {
			return fmt.Errorf("failed to build prompt %q: %w", payload.EvePromptID, err)
		}
	default:
		return fmt.Errorf("unsupported eve_prompt_id: %s", payload.EvePromptID)
	}

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
	// Reduce safety-related empty outputs for benign classification/extraction tasks.
	req.SafetySettings = []gemini.SafetySetting{
		{Category: "HARM_CATEGORY_HARASSMENT", Threshold: "BLOCK_NONE"},
		{Category: "HARM_CATEGORY_HATE_SPEECH", Threshold: "BLOCK_NONE"},
		{Category: "HARM_CATEGORY_SEXUALLY_EXPLICIT", Threshold: "BLOCK_NONE"},
		{Category: "HARM_CATEGORY_DANGEROUS_CONTENT", Threshold: "BLOCK_NONE"},
	}
	// Force JSON output matching convo-all-v1 schema (improves correctness + throughput).
	// This workload is structured extraction, not deep reasoning; minimize thinking to maximize throughput.
	req.GenerationConfig = &gemini.GenerationConfig{
		ThinkingConfig:   &gemini.ThinkingConfig{ThinkingLevel: "minimal"},
		ResponseMimeType: "application/json",
		ResponseSchema:   convoAllV1ResponseSchema,
	}

	// Call Gemini for analysis
	resp, err := h.geminiClient.GenerateContent(h.model, req)
	if err != nil {
		return fmt.Errorf("gemini analysis failed: %w", err)
	}

	// Extract raw text from response
	outputText := extractTextFromResponse(resp)
	if strings.TrimSpace(outputText) == "" {
		return fmt.Errorf("empty model output (finishReasons=%s safetyRatings=%s)", summarizeFinishReasons(resp), summarizeSafetyRatings(resp))
	}

	// Parse structured output and persist
	switch payload.EvePromptID {
	case "convo-all-v1":
		parsed, err := parseConvoAllV1Output(outputText)
		if err != nil {
			return fmt.Errorf("failed to parse convo-all-v1 JSON: %w", err)
		}
		if err := h.persistConvoAllV1(payload.ConversationID, conversation.ChatID, payload.EvePromptID, parsed, resp); err != nil {
			return fmt.Errorf("failed to persist analysis: %w", err)
		}
	default:
		return fmt.Errorf("unsupported eve_prompt_id: %s", payload.EvePromptID)
	}

	return nil
}

func summarizeFinishReasons(resp *gemini.GenerateContentResponse) string {
	if resp == nil || len(resp.Candidates) == 0 {
		return "[]"
	}
	reasons := make([]string, 0, len(resp.Candidates))
	for _, c := range resp.Candidates {
		if c.FinishReason != "" {
			reasons = append(reasons, c.FinishReason)
		}
	}
	b, _ := json.Marshal(reasons)
	return string(b)
}

func summarizeSafetyRatings(resp *gemini.GenerateContentResponse) string {
	if resp == nil || len(resp.Candidates) == 0 {
		return "[]"
	}
	type r struct {
		Category    string `json:"category"`
		Probability string `json:"probability"`
	}
	var out []r
	for _, c := range resp.Candidates {
		for _, sr := range c.SafetyRatings {
			out = append(out, r{Category: sr.Category, Probability: sr.Probability})
		}
	}
	b, _ := json.Marshal(out)
	return string(b)
}

type convoAllV1Output struct {
	Summary  string `json:"summary"`
	Entities []struct {
		ParticipantName string `json:"participant_name"`
		Entities        []convoAllV1NameItem `json:"entities"`
	} `json:"entities"`
	Topics []struct {
		ParticipantName string `json:"participant_name"`
		Topics          []convoAllV1NameItem `json:"topics"`
	} `json:"topics"`
	Emotions []struct {
		ParticipantName string `json:"participant_name"`
		Emotions        []convoAllV1NameItem `json:"emotions"`
	} `json:"emotions"`
	Humor []struct {
		ParticipantName string `json:"participant_name"`
		Humor           []convoAllV1HumorItem `json:"humor"`
	} `json:"humor"`
}

type convoAllV1NameItem struct {
	Name string `json:"name"`
}

func (n *convoAllV1NameItem) UnmarshalJSON(b []byte) error {
	// Accept either:
	// - {"name": "..."} (preferred)
	// - "..." (common model fallback)
	var s string
	if err := json.Unmarshal(b, &s); err == nil {
		n.Name = s
		return nil
	}
	type obj convoAllV1NameItem
	var o obj
	if err := json.Unmarshal(b, &o); err != nil {
		return err
	}
	*n = convoAllV1NameItem(o)
	return nil
}

type convoAllV1HumorItem struct {
	Message string `json:"message"`
}

func (m *convoAllV1HumorItem) UnmarshalJSON(b []byte) error {
	// Accept either:
	// - {"message": "..."} (preferred)
	// - "..." (common model fallback)
	var s string
	if err := json.Unmarshal(b, &s); err == nil {
		m.Message = s
		return nil
	}
	type obj convoAllV1HumorItem
	var o obj
	if err := json.Unmarshal(b, &o); err != nil {
		return err
	}
	*m = convoAllV1HumorItem(o)
	return nil
}

// extractTextFromResponse extracts the text content from Gemini response
func extractTextFromResponse(resp *gemini.GenerateContentResponse) string {
	if resp == nil || len(resp.Candidates) == 0 {
		return ""
	}

	for _, candidate := range resp.Candidates {
		for _, part := range candidate.Content.Parts {
			if strings.TrimSpace(part.Text) != "" {
				return part.Text
			}
		}
	}

	return ""
}

func buildConvoAllV1Prompt(conversationText string) (string, error) {
	// Preferred source of truth is the TS prompt file (agent-readable).
	// For packaged binaries, we fall back to an embedded string.
	promptTemplate, err := loadPromptBodyFromRepo("ts/eve/prompts/analysis/convo-all-v1.prompt.md")
	if err != nil {
		// Fallback: keep behavior working even if repo files aren't present.
		promptTemplate = convoAllV1FallbackBody
	}

	// The prompt uses a triple-brace variable for raw insertion.
	return strings.ReplaceAll(promptTemplate, "{{{conversation_text}}}", conversationText), nil
}

const convoAllV1FallbackBody = `# Conversation-Wide Analysis

You are an expert conversation analyzer. Analyze the following conversation
chunk and extract the information below.

1) A short summary (10–50 words).
2) A list of **entities** – each item is {"participant_name": …, "entities": [{"name": …}, …]}
3) A list of **topics**   – {"participant_name": …, "topics":  [{"name": …}, …]}
4) A list of **emotions** – {"participant_name": …, "emotions":[{"name": …}, …]}
5) A list of **humor**    – {"participant_name": …, "humor":   [{"message": …}, …]}

Guidelines
* The input lines look like Name: message text.
* Omit participants from a category if they have no items.
* Return valid JSON with the top-level keys exactly: summary, entities, topics, emotions, humor.

Conversation chunk:
{{{conversation_text}}}
`

// convoAllV1ResponseSchema is a Gemini "Schema" (not full JSON Schema).
// It intentionally avoids unsupported JSON Schema fields like additionalProperties.
var convoAllV1ResponseSchema = map[string]any{
	"type": "OBJECT",
	"properties": map[string]any{
		"summary": map[string]any{
			"type": "STRING",
		},
		"entities": map[string]any{
			"type": "ARRAY",
			"items": map[string]any{
				"type": "OBJECT",
				"properties": map[string]any{
					"participant_name": map[string]any{"type": "STRING"},
					"entities": map[string]any{
						"type": "ARRAY",
						"items": map[string]any{
							"type":       "OBJECT",
							"properties": map[string]any{"name": map[string]any{"type": "STRING"}},
							"required":   []string{"name"},
						},
					},
				},
				"required": []string{"participant_name", "entities"},
			},
		},
		"topics": map[string]any{
			"type": "ARRAY",
			"items": map[string]any{
				"type": "OBJECT",
				"properties": map[string]any{
					"participant_name": map[string]any{"type": "STRING"},
					"topics": map[string]any{
						"type": "ARRAY",
						"items": map[string]any{
							"type":       "OBJECT",
							"properties": map[string]any{"name": map[string]any{"type": "STRING"}},
							"required":   []string{"name"},
						},
					},
				},
				"required": []string{"participant_name", "topics"},
			},
		},
		"emotions": map[string]any{
			"type": "ARRAY",
			"items": map[string]any{
				"type": "OBJECT",
				"properties": map[string]any{
					"participant_name": map[string]any{"type": "STRING"},
					"emotions": map[string]any{
						"type": "ARRAY",
						"items": map[string]any{
							"type":       "OBJECT",
							"properties": map[string]any{"name": map[string]any{"type": "STRING"}},
							"required":   []string{"name"},
						},
					},
				},
				"required": []string{"participant_name", "emotions"},
			},
		},
		"humor": map[string]any{
			"type": "ARRAY",
			"items": map[string]any{
				"type": "OBJECT",
				"properties": map[string]any{
					"participant_name": map[string]any{"type": "STRING"},
					"humor": map[string]any{
						"type": "ARRAY",
						"items": map[string]any{
							"type":       "OBJECT",
							"properties": map[string]any{"message": map[string]any{"type": "STRING"}},
							"required":   []string{"message"},
						},
					},
				},
				"required": []string{"participant_name", "humor"},
			},
		},
	},
	"required": []string{"summary", "entities", "topics", "emotions", "humor"},
}

func loadPromptBodyFromRepo(relPath string) (string, error) {
	// Try relative to CWD first.
	if cwd, err := os.Getwd(); err == nil {
		if body, err := readPromptBody(filepath.Join(cwd, relPath)); err == nil {
			return body, nil
		}
	}

	// Try relative to executable location.
	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		if body, err := readPromptBody(filepath.Join(exeDir, relPath)); err == nil {
			return body, nil
		}
	}

	return "", fmt.Errorf("prompt file not found: %s", relPath)
}

func readPromptBody(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}

	// Strip YAML frontmatter if present (--- ... ---).
	s := string(b)
	if strings.HasPrefix(s, "---") {
		parts := strings.SplitN(s, "---", 3)
		// parts[0] = "" (before first ---), parts[1] = frontmatter, parts[2] = body
		if len(parts) == 3 {
			return strings.TrimLeft(parts[2], "\r\n"), nil
		}
	}

	return s, nil
}

func parseConvoAllV1Output(outputText string) (*convoAllV1Output, error) {
	jsonText, err := extractJSONObject(outputText)
	if err != nil {
		return nil, err
	}

	var out convoAllV1Output
	if err := json.Unmarshal([]byte(jsonText), &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func extractJSONObject(text string) (string, error) {
	s := strings.TrimSpace(text)
	if s == "" {
		return "", fmt.Errorf("empty model output")
	}

	// Strip common markdown fences.
	if strings.HasPrefix(s, "```") {
		s = strings.TrimPrefix(s, "```json")
		s = strings.TrimPrefix(s, "```JSON")
		s = strings.TrimPrefix(s, "```")
		s = strings.TrimSuffix(s, "```")
		s = strings.TrimSpace(s)
	}

	start := strings.IndexByte(s, '{')
	end := strings.LastIndexByte(s, '}')
	if start == -1 || end == -1 || end <= start {
		return "", fmt.Errorf("no JSON object found")
	}
	return s[start : end+1], nil
}

func (h *AnalysisJobHandler) persistConvoAllV1(conversationID int, chatID int, evePromptID string, parsed *convoAllV1Output, resp *gemini.GenerateContentResponse) error {
	resultJSON, err := json.Marshal(resp)
	if err != nil {
		return fmt.Errorf("failed to marshal result: %w", err)
	}

	tx, err := h.warehouseDB.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Insert completion
	var completionID int64
	err = tx.QueryRow(`
		INSERT INTO completions (conversation_id, model, result, created_at)
		VALUES (?, ?, ?, ?)
		RETURNING id
	`, conversationID, h.model, string(resultJSON), time.Now()).Scan(&completionID)
	if err != nil {
		return fmt.Errorf("failed to insert completion: %w", err)
	}

	// Update conversation summary
	if parsed.Summary != "" {
		if _, err := tx.Exec(`UPDATE conversations SET summary = ? WHERE id = ?`, parsed.Summary, conversationID); err != nil {
			return fmt.Errorf("failed to update conversation summary: %w", err)
		}
	}

	// Ensure idempotency for this prompt by replacing any existing row(s).
	if _, err := tx.Exec(`DELETE FROM conversation_analyses WHERE conversation_id = ? AND eve_prompt_id = ?`, conversationID, evePromptID); err != nil {
		return fmt.Errorf("failed to clear previous conversation_analyses row: %w", err)
	}
	if _, err := tx.Exec(`
		INSERT INTO conversation_analyses (
			conversation_id, eve_prompt_id, status, completion_id, created_at, updated_at
		) VALUES (?, ?, 'completed', ?, ?, ?)
	`, conversationID, evePromptID, completionID, time.Now(), time.Now()); err != nil {
		return fmt.Errorf("failed to insert conversation_analyses: %w", err)
	}

	// Replace facets for this conversation (full refresh).
	for _, table := range []string{"entities", "topics", "emotions", "humor_items"} {
		if _, err := tx.Exec(fmt.Sprintf("DELETE FROM %s WHERE conversation_id = ?", table), conversationID); err != nil {
			return fmt.Errorf("failed to clear %s for conversation: %w", table, err)
		}
	}

	contactIDs := map[string]*int64{}
	resolve := func(name string) (*int64, error) {
		if name == "" {
			return nil, nil
		}
		if v, ok := contactIDs[name]; ok {
			return v, nil
		}

		var id int64
		// First try exact name match, then nickname.
		if err := tx.QueryRow(`SELECT id FROM contacts WHERE name = ? LIMIT 1`, name).Scan(&id); err == nil {
			contactIDs[name] = &id
			return &id, nil
		}
		if err := tx.QueryRow(`SELECT id FROM contacts WHERE nickname = ? LIMIT 1`, name).Scan(&id); err == nil {
			contactIDs[name] = &id
			return &id, nil
		}
		contactIDs[name] = nil
		return nil, nil
	}

	// Insert entities
	for _, p := range parsed.Entities {
		contactID, err := resolve(p.ParticipantName)
		if err != nil {
			return err
		}
		for _, ent := range p.Entities {
			title := strings.TrimSpace(ent.Name)
			if title == "" {
				continue
			}
			if _, err := tx.Exec(
				`INSERT OR IGNORE INTO entities (conversation_id, chat_id, contact_id, title) VALUES (?, ?, ?, ?)`,
				conversationID, chatID, contactID, title,
			); err != nil {
				return fmt.Errorf("failed to insert entity: %w", err)
			}
		}
	}

	// Insert topics
	for _, p := range parsed.Topics {
		contactID, err := resolve(p.ParticipantName)
		if err != nil {
			return err
		}
		for _, top := range p.Topics {
			title := strings.TrimSpace(top.Name)
			if title == "" {
				continue
			}
			if _, err := tx.Exec(
				`INSERT OR IGNORE INTO topics (conversation_id, chat_id, contact_id, title) VALUES (?, ?, ?, ?)`,
				conversationID, chatID, contactID, title,
			); err != nil {
				return fmt.Errorf("failed to insert topic: %w", err)
			}
		}
	}

	// Insert emotions
	for _, p := range parsed.Emotions {
		contactID, err := resolve(p.ParticipantName)
		if err != nil {
			return err
		}
		for _, emo := range p.Emotions {
			typ := strings.TrimSpace(emo.Name)
			if typ == "" {
				continue
			}
			if _, err := tx.Exec(
				`INSERT OR IGNORE INTO emotions (conversation_id, chat_id, contact_id, emotion_type) VALUES (?, ?, ?, ?)`,
				conversationID, chatID, contactID, typ,
			); err != nil {
				return fmt.Errorf("failed to insert emotion: %w", err)
			}
		}
	}

	// Insert humor items
	for _, p := range parsed.Humor {
		contactID, err := resolve(p.ParticipantName)
		if err != nil {
			return err
		}
		for _, hitem := range p.Humor {
			snippet := strings.TrimSpace(hitem.Message)
			if snippet == "" {
				continue
			}
			if _, err := tx.Exec(
				`INSERT OR IGNORE INTO humor_items (conversation_id, chat_id, contact_id, snippet) VALUES (?, ?, ?, ?)`,
				conversationID, chatID, contactID, snippet,
			); err != nil {
				return fmt.Errorf("failed to insert humor_item: %w", err)
			}
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}
	return nil
}
