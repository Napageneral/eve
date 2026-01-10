package engine

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/brandtty/eve/internal/db"
	"github.com/brandtty/eve/internal/encoding"
	"github.com/brandtty/eve/internal/gemini"
	"github.com/brandtty/eve/internal/queue"
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
	metrics      *AnalysisMetrics
	writer       *TxBatchWriter
}

// NewAnalysisJobHandler creates a new analysis job handler
func NewAnalysisJobHandler(warehouseDB *sql.DB, geminiClient *gemini.Client, model string) JobHandler {
	return NewAnalysisJobHandlerWithMetrics(warehouseDB, geminiClient, model, nil)
}

// NewAnalysisJobHandlerWithMetrics creates a new analysis job handler that records aggregated metrics.
func NewAnalysisJobHandlerWithMetrics(warehouseDB *sql.DB, geminiClient *gemini.Client, model string, metrics *AnalysisMetrics) JobHandler {
	return NewAnalysisJobHandlerWithPipeline(warehouseDB, geminiClient, model, metrics, nil)
}

// NewAnalysisJobHandlerWithPipeline creates a new analysis job handler with an optional DB writer stage.
func NewAnalysisJobHandlerWithPipeline(warehouseDB *sql.DB, geminiClient *gemini.Client, model string, metrics *AnalysisMetrics, writer *TxBatchWriter) JobHandler {
	h := &AnalysisJobHandler{
		warehouseDB:  warehouseDB,
		geminiClient: geminiClient,
		model:        model,
		metrics:      metrics,
		writer:       writer,
	}
	return func(ctx context.Context, job *queue.Job) error {
		return h.handleJob(ctx, job.PayloadJSON)
	}
}

// handleJob processes an analysis job
func (h *AnalysisJobHandler) handleJob(ctx context.Context, payloadJSON string) error {
	overallStart := time.Now()
	var (
		dbReadDur     time.Duration
		encodeDur     time.Duration
		promptDur     time.Duration
		apiDur        time.Duration
		parseDur      time.Duration
		dbWriteDur    time.Duration
		outcome       = "error"
		blockedReason string
	)
	defer func() {
		h.metrics.Record(AnalysisMetricEvent{
			DBRead:        dbReadDur,
			Encode:        encodeDur,
			Prompt:        promptDur,
			APICall:       apiDur,
			Parse:         parseDur,
			DBWrite:       dbWriteDur,
			Overall:       time.Since(overallStart),
			Outcome:       outcome,
			BlockedReason: blockedReason,
		})
	}()

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
	t0 := time.Now()
	conversation, err := reader.GetConversation(payload.ConversationID)
	if err != nil {
		return fmt.Errorf("failed to read conversation: %w", err)
	}
	dbReadDur = time.Since(t0)

	// Encode conversation
	opts := encoding.DefaultEncodeOptions()
	t1 := time.Now()
	encodedText := encoding.EncodeConversation(conversation, opts)
	encodeDur = time.Since(t1)

	// Build prompt (full quality: all messages, no truncation, no output caps)
	var promptText string
	switch payload.EvePromptID {
	case "convo-all-v1":
		t2 := time.Now()
		promptText, err = buildConvoAllV1Prompt(encodedText)
		promptDur = time.Since(t2)
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
	t3 := time.Now()
	resp, err := h.geminiClient.GenerateContentWithContext(ctx, h.model, req)
	apiDur = time.Since(t3)
	if err != nil {
		return fmt.Errorf("gemini analysis failed: %w", err)
	}

	// Extract raw text from response
	outputText := extractTextFromResponse(resp)
	if strings.TrimSpace(outputText) == "" {
		// If the provider explicitly blocked the prompt, persist as "blocked" (not an error).
		if reason, msg, ok := inferBlockedReason(resp); ok {
			blockedReason = reason
			tw := time.Now()
			if err := h.persistBlockedAnalysis(ctx, payload.ConversationID, conversation.ChatID, payload.EvePromptID, resp, reason, msg); err != nil {
				return fmt.Errorf("failed to persist blocked analysis: %w", err)
			}
			dbWriteDur = time.Since(tw)
			outcome = "blocked"
			return nil
		}
		return fmt.Errorf("empty model output (promptFeedback=%s finishReasons=%s safetyRatings=%s)", summarizePromptFeedback(resp), summarizeFinishReasons(resp), summarizeSafetyRatings(resp))
	}

	// Parse structured output and persist
	switch payload.EvePromptID {
	case "convo-all-v1":
		t4 := time.Now()
		parsed, err := parseConvoAllV1Output(outputText)
		parseDur = time.Since(t4)
		if err != nil {
			return fmt.Errorf("failed to parse convo-all-v1 JSON: %w", err)
		}
		tw := time.Now()
		if err := h.persistConvoAllV1(ctx, payload.ConversationID, conversation.ChatID, payload.EvePromptID, parsed, resp); err != nil {
			return fmt.Errorf("failed to persist analysis: %w", err)
		}
		dbWriteDur = time.Since(tw)
	default:
		return fmt.Errorf("unsupported eve_prompt_id: %s", payload.EvePromptID)
	}

	outcome = "ok"
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
		// If the prompt was blocked, safety ratings may appear on promptFeedback instead.
		if resp != nil && resp.PromptFeedback != nil && len(resp.PromptFeedback.SafetyRatings) > 0 {
			b, _ := json.Marshal(resp.PromptFeedback.SafetyRatings)
			return string(b)
		}
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

func summarizePromptFeedback(resp *gemini.GenerateContentResponse) string {
	if resp == nil || resp.PromptFeedback == nil {
		return "null"
	}
	b, _ := json.Marshal(resp.PromptFeedback)
	return string(b)
}

type convoAllV1Output struct {
	Summary  string `json:"summary"`
	Entities []struct {
		ParticipantName string               `json:"participant_name"`
		Entities        []convoAllV1NameItem `json:"entities"`
	} `json:"entities"`
	Topics []struct {
		ParticipantName string               `json:"participant_name"`
		Topics          []convoAllV1NameItem `json:"topics"`
	} `json:"topics"`
	Emotions []struct {
		ParticipantName string               `json:"participant_name"`
		Emotions        []convoAllV1NameItem `json:"emotions"`
	} `json:"emotions"`
	Humor []struct {
		ParticipantName string                `json:"participant_name"`
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
	promptTemplate, err := getConvoAllV1PromptBody()
	if err != nil {
		return "", err
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

var (
	convoAllV1PromptOnce sync.Once
	convoAllV1PromptBody string
	convoAllV1PromptErr  error
)

func getConvoAllV1PromptBody() (string, error) {
	convoAllV1PromptOnce.Do(func() {
		// Preferred source of truth is the TS prompt file (agent-readable).
		// For packaged binaries, we fall back to an embedded string.
		body, err := loadPromptBodyFromRepo("ts/eve/prompts/analysis/convo-all-v1.prompt.md")
		if err != nil {
			convoAllV1PromptBody = convoAllV1FallbackBody
			convoAllV1PromptErr = nil
			return
		}
		convoAllV1PromptBody = body
		convoAllV1PromptErr = nil
	})
	return convoAllV1PromptBody, convoAllV1PromptErr
}

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

func (h *AnalysisJobHandler) persistConvoAllV1(ctx context.Context, conversationID int, chatID int, evePromptID string, parsed *convoAllV1Output, resp *gemini.GenerateContentResponse) error {
	resultJSON, err := json.Marshal(resp)
	if err != nil {
		return fmt.Errorf("failed to marshal result: %w", err)
	}

	apply := func(tx *sql.Tx) error {
		return h.applyConvoAllV1Tx(tx, conversationID, chatID, evePromptID, parsed, string(resultJSON))
	}
	if h.writer != nil {
		return h.writer.Submit(ctx, apply)
	}
	tx, err := h.warehouseDB.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()
	if err := apply(tx); err != nil {
		return err
	}
	return tx.Commit()
}

func (h *AnalysisJobHandler) persistBlockedAnalysis(ctx context.Context, conversationID int, chatID int, evePromptID string, resp *gemini.GenerateContentResponse, blockReason string, blockReasonMessage string) error {
	resultJSON, err := json.Marshal(resp)
	if err != nil {
		return fmt.Errorf("failed to marshal result: %w", err)
	}
	apply := func(tx *sql.Tx) error {
		return h.applyBlockedTx(tx, conversationID, evePromptID, string(resultJSON), blockReason, blockReasonMessage)
	}
	if h.writer != nil {
		return h.writer.Submit(ctx, apply)
	}
	tx, err := h.warehouseDB.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()
	if err := apply(tx); err != nil {
		return err
	}
	return tx.Commit()
}

func (h *AnalysisJobHandler) applyBlockedTx(tx *sql.Tx, conversationID int, evePromptID string, resultJSON string, blockReason string, blockReasonMessage string) error {
	// Insert completion (even though there's no output, we keep promptFeedback metadata)
	var completionID int64
	err := tx.QueryRow(`
		INSERT INTO completions (conversation_id, model, result, created_at)
		VALUES (?, ?, ?, ?)
		RETURNING id
	`, conversationID, h.model, resultJSON, time.Now()).Scan(&completionID)
	if err != nil {
		return fmt.Errorf("failed to insert completion: %w", err)
	}

	// Ensure idempotency for this prompt by replacing any existing row(s).
	if _, err := tx.Exec(`DELETE FROM conversation_analyses WHERE conversation_id = ? AND eve_prompt_id = ?`, conversationID, evePromptID); err != nil {
		return fmt.Errorf("failed to clear previous conversation_analyses row: %w", err)
	}

	if _, err := tx.Exec(`
		INSERT INTO conversation_analyses (
			conversation_id, eve_prompt_id, status, completion_id,
			blocked_reason, blocked_reason_message, blocked_at,
			created_at, updated_at
		) VALUES (?, ?, 'blocked', ?, ?, ?, ?, ?, ?)
	`, conversationID, evePromptID, completionID,
		blockReason, blockReasonMessage, time.Now(),
		time.Now(), time.Now(),
	); err != nil {
		return fmt.Errorf("failed to insert blocked conversation_analyses: %w", err)
	}

	// Clear any prior facets so the DB doesn't look "analyzed".
	for _, table := range []string{"entities", "topics", "emotions", "humor_items"} {
		if _, err := tx.Exec(fmt.Sprintf("DELETE FROM %s WHERE conversation_id = ?", table), conversationID); err != nil {
			return fmt.Errorf("failed to clear %s for conversation: %w", table, err)
		}
	}

	return nil
}

func (h *AnalysisJobHandler) applyConvoAllV1Tx(tx *sql.Tx, conversationID int, chatID int, evePromptID string, parsed *convoAllV1Output, resultJSON string) error {
	// Insert completion
	var completionID int64
	err := tx.QueryRow(`
		INSERT INTO completions (conversation_id, model, result, created_at)
		VALUES (?, ?, ?, ?)
		RETURNING id
	`, conversationID, h.model, resultJSON, time.Now()).Scan(&completionID)
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

	// Multi-row inserts (chunked) for facets to reduce per-row statement overhead.
	var entityRows [][]interface{}
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
			entityRows = append(entityRows, []interface{}{conversationID, chatID, contactID, title})
		}
	}
	if err := execInsertOrIgnoreMany(tx, "entities", []string{"conversation_id", "chat_id", "contact_id", "title"}, entityRows); err != nil {
		return fmt.Errorf("failed to insert entities: %w", err)
	}

	var topicRows [][]interface{}
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
			topicRows = append(topicRows, []interface{}{conversationID, chatID, contactID, title})
		}
	}
	if err := execInsertOrIgnoreMany(tx, "topics", []string{"conversation_id", "chat_id", "contact_id", "title"}, topicRows); err != nil {
		return fmt.Errorf("failed to insert topics: %w", err)
	}

	var emotionRows [][]interface{}
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
			emotionRows = append(emotionRows, []interface{}{conversationID, chatID, contactID, typ})
		}
	}
	if err := execInsertOrIgnoreMany(tx, "emotions", []string{"conversation_id", "chat_id", "contact_id", "emotion_type"}, emotionRows); err != nil {
		return fmt.Errorf("failed to insert emotions: %w", err)
	}

	var humorRows [][]interface{}
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
			humorRows = append(humorRows, []interface{}{conversationID, chatID, contactID, snippet})
		}
	}
	if err := execInsertOrIgnoreMany(tx, "humor_items", []string{"conversation_id", "chat_id", "contact_id", "snippet"}, humorRows); err != nil {
		return fmt.Errorf("failed to insert humor_items: %w", err)
	}

	return nil
}

func execInsertOrIgnoreMany(tx *sql.Tx, table string, columns []string, rows [][]interface{}) error {
	if tx == nil {
		return fmt.Errorf("nil tx")
	}
	if len(rows) == 0 {
		return nil
	}
	if len(columns) == 0 {
		return fmt.Errorf("no columns")
	}

	// SQLite variable limit is typically 999; keep some headroom.
	const maxVars = 900
	ncols := len(columns)
	maxRows := maxVars / ncols
	if maxRows < 1 {
		maxRows = 1
	}

	for start := 0; start < len(rows); start += maxRows {
		end := start + maxRows
		if end > len(rows) {
			end = len(rows)
		}
		chunk := rows[start:end]

		var b strings.Builder
		b.Grow(128 + len(chunk)*ncols*3)
		b.WriteString("INSERT OR IGNORE INTO ")
		b.WriteString(table)
		b.WriteString(" (")
		for i, c := range columns {
			if i > 0 {
				b.WriteString(",")
			}
			b.WriteString(c)
		}
		b.WriteString(") VALUES ")

		args := make([]interface{}, 0, len(chunk)*ncols)
		for i, row := range chunk {
			if len(row) != ncols {
				return fmt.Errorf("row has %d values, want %d", len(row), ncols)
			}
			if i > 0 {
				b.WriteString(",")
			}
			b.WriteString("(")
			for j := 0; j < ncols; j++ {
				if j > 0 {
					b.WriteString(",")
				}
				b.WriteString("?")
				args = append(args, row[j])
			}
			b.WriteString(")")
		}

		if _, err := tx.Exec(b.String(), args...); err != nil {
			return err
		}
	}

	return nil
}

func inferBlockedReason(resp *gemini.GenerateContentResponse) (reason string, message string, ok bool) {
	if resp == nil {
		return "", "", false
	}
	if resp.PromptFeedback != nil && resp.PromptFeedback.BlockReason != "" {
		return resp.PromptFeedback.BlockReason, resp.PromptFeedback.BlockReasonMessage, true
	}
	for _, c := range resp.Candidates {
		switch c.FinishReason {
		case "PROHIBITED_CONTENT", "SAFETY":
			return c.FinishReason, "", true
		}
	}
	return "", "", false
}
