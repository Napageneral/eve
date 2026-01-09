package engine

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"github.com/tylerchilds/eve/internal/gemini"
	"github.com/tylerchilds/eve/internal/queue"
)

func setupAnalysisTestDB(t *testing.T) (*sql.DB, func()) {
	tmpfile, err := os.CreateTemp("", "test-analysis-*.db")
	if err != nil {
		t.Fatalf("Failed to create temp file: %v", err)
	}
	tmpfile.Close()

	db, err := sql.Open("sqlite3", tmpfile.Name())
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}

	// Create full schema
	schema := `
		CREATE TABLE contacts (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT,
			nickname TEXT
		);

		CREATE TABLE chats (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_identifier TEXT UNIQUE NOT NULL
		);

		CREATE TABLE conversations (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_id INTEGER NOT NULL,
			start_time TIMESTAMP NOT NULL,
			end_time TIMESTAMP NOT NULL
		);

		CREATE TABLE messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_id INTEGER NOT NULL,
			sender_id INTEGER,
			content TEXT,
			timestamp TIMESTAMP NOT NULL,
			is_from_me BOOLEAN DEFAULT 0,
			guid TEXT UNIQUE NOT NULL,
			conversation_id INTEGER
		);

		CREATE TABLE attachments (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			message_id INTEGER NOT NULL,
			mime_type TEXT,
			file_name TEXT,
			is_sticker BOOLEAN DEFAULT 0
		);

		CREATE TABLE reactions (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			original_message_guid TEXT NOT NULL,
			reaction_type INTEGER,
			sender_id INTEGER,
			is_from_me BOOLEAN DEFAULT 0
		);

		CREATE TABLE completions (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			conversation_id INTEGER,
			model TEXT,
			result TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);

		CREATE TABLE conversation_analyses (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			conversation_id INTEGER NOT NULL,
			prompt_template_id INTEGER,
			eve_prompt_id TEXT,
			status TEXT NOT NULL DEFAULT 'pending',
			completion_id INTEGER,
			error_message TEXT,
			retry_count INTEGER DEFAULT 0,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			UNIQUE(conversation_id, prompt_template_id)
		);
	`

	_, err = db.Exec(schema)
	if err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	cleanup := func() {
		db.Close()
		os.Remove(tmpfile.Name())
	}

	return db, cleanup
}

// Create a test-friendly Gemini client that uses a fake server
func newTestGeminiClient(t *testing.T) (*gemini.Client, *httptest.Server) {
	fakeServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Return a fake successful response
		response := gemini.GenerateContentResponse{
			Candidates: []gemini.Candidate{
				{
					Content: gemini.Content{
						Parts: []gemini.Part{
							{Text: "Test analysis: Topics include lunch plans. Tone is casual."},
						},
					},
				},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}))

	// Create a client with a custom HTTP client that redirects to our test server
	client := gemini.NewClient("test-api-key")

	// Replace the HttpClient transport to redirect requests to our test server
	originalTransport := client.HttpClient.Transport
	client.HttpClient.Transport = &testTransport{
		testServer: fakeServer,
		original:   originalTransport,
	}

	return client, fakeServer
}

// testTransport redirects requests to the test server
type testTransport struct {
	testServer *httptest.Server
	original   http.RoundTripper
}

func (t *testTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Redirect all requests to test server
	req.URL.Scheme = "http"
	req.URL.Host = strings.TrimPrefix(t.testServer.URL, "http://")
	return t.testServer.Client().Transport.RoundTrip(req)
}

func TestAnalysisJobHandler_EndToEnd(t *testing.T) {
	// Setup test database
	db, cleanup := setupAnalysisTestDB(t)
	defer cleanup()

	// Insert test data
	_, err := db.Exec(`
		INSERT INTO contacts (id, name) VALUES (1, 'Alice'), (2, 'Bob');
		INSERT INTO chats (id, chat_identifier) VALUES (1, 'chat-1');
		INSERT INTO conversations (id, chat_id, start_time, end_time)
		VALUES (1, 1, '2025-10-27 15:00:00', '2025-10-27 16:00:00');
		INSERT INTO messages (id, chat_id, sender_id, content, timestamp, is_from_me, guid, conversation_id)
		VALUES
			(1, 1, 1, 'Hey, want to grab lunch?', '2025-10-27 15:30:00', 0, 'msg-1', 1),
			(2, 1, 2, 'Sure! How about pizza?', '2025-10-27 15:31:00', 0, 'msg-2', 1),
			(3, 1, 1, 'Sounds good. 12:30?', '2025-10-27 15:32:00', 0, 'msg-3', 1);
	`)
	if err != nil {
		t.Fatalf("Failed to insert test data: %v", err)
	}

	// Create test Gemini client
	client, fakeServer := newTestGeminiClient(t)
	defer fakeServer.Close()

	// Create analysis job handler
		handler := NewAnalysisJobHandler(db, client, "gemini-2.5-flash", "", 0, 0)

	// Create job
	payload := AnalysisJobPayload{
		ConversationID: 1,
		EvePromptID:    "convo-all-v1",
	}
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("Failed to marshal payload: %v", err)
	}

	job := &queue.Job{
		ID:          "test-job-1",
		Type:        "analysis",
		Key:         "analysis:1:convo-all-v1",
		PayloadJSON: string(payloadJSON),
	}

	// Run the handler
	ctx := context.Background()
	err = handler(ctx, job)
	if err != nil {
		t.Fatalf("Handler failed: %v", err)
	}

	// Verify completion was created
	var completionCount int
	err = db.QueryRow(`SELECT COUNT(*) FROM completions WHERE conversation_id = 1`).Scan(&completionCount)
	if err != nil {
		t.Fatalf("Failed to query completions: %v", err)
	}
	if completionCount != 1 {
		t.Errorf("Expected 1 completion, got %d", completionCount)
	}

	// Verify conversation_analysis was created
	var analysisCount int
	var status string
	err = db.QueryRow(`
		SELECT COUNT(*), status
		FROM conversation_analyses
		WHERE conversation_id = 1 AND eve_prompt_id = 'convo-all-v1'
		GROUP BY status
	`).Scan(&analysisCount, &status)
	if err != nil {
		t.Fatalf("Failed to query conversation_analyses: %v", err)
	}
	if analysisCount != 1 {
		t.Errorf("Expected 1 analysis, got %d", analysisCount)
	}
	if status != "completed" {
		t.Errorf("Expected status 'completed', got %q", status)
	}

	// Verify completion result contains expected structure
	var resultJSON string
	err = db.QueryRow(`SELECT result FROM completions WHERE conversation_id = 1`).Scan(&resultJSON)
	if err != nil {
		t.Fatalf("Failed to query completion result: %v", err)
	}

	var result gemini.GenerateContentResponse
	err = json.Unmarshal([]byte(resultJSON), &result)
	if err != nil {
		t.Fatalf("Failed to unmarshal result: %v", err)
	}

	// Check that result has expected structure
	if len(result.Candidates) == 0 {
		t.Errorf("Expected candidates in result")
	}
	if len(result.Candidates) > 0 && len(result.Candidates[0].Content.Parts) > 0 {
		text := result.Candidates[0].Content.Parts[0].Text
		if !strings.Contains(text, "Test analysis") {
			t.Errorf("Expected 'Test analysis' in result text, got: %s", text)
		}
	}
}

func TestAnalysisJobHandler_InvalidPayload(t *testing.T) {
	db, cleanup := setupAnalysisTestDB(t)
	defer cleanup()

	client := gemini.NewClient("fake-api-key")
		handler := NewAnalysisJobHandler(db, client, "gemini-2.5-flash", "", 0, 0)

	job := &queue.Job{
		ID:          "test-job-1",
		Type:        "analysis",
		Key:         "analysis:invalid",
		PayloadJSON: "invalid json",
	}

	ctx := context.Background()
	err := handler(ctx, job)
	if err == nil {
		t.Error("Expected error for invalid JSON, got nil")
	}
}

func TestAnalysisJobHandler_ConversationNotFound(t *testing.T) {
	db, cleanup := setupAnalysisTestDB(t)
	defer cleanup()

	client := gemini.NewClient("fake-api-key")
		handler := NewAnalysisJobHandler(db, client, "gemini-2.5-flash", "", 0, 0)

	payload := AnalysisJobPayload{
		ConversationID: 999, // doesn't exist
		EvePromptID:    "convo-all-v1",
	}
	payloadJSON, _ := json.Marshal(payload)

	job := &queue.Job{
		ID:          "test-job-1",
		Type:        "analysis",
		Key:         "analysis:999",
		PayloadJSON: string(payloadJSON),
	}

	ctx := context.Background()
	err := handler(ctx, job)
	if err == nil {
		t.Error("Expected error for non-existent conversation, got nil")
	}
}

func TestExtractTextFromResponse(t *testing.T) {
	resp := &gemini.GenerateContentResponse{
		Candidates: []gemini.Candidate{
			{
				Content: gemini.Content{
					Parts: []gemini.Part{
						{Text: "Test analysis text"},
					},
				},
			},
		},
	}

	text := extractTextFromResponse(resp)
	if text != "Test analysis text" {
		t.Errorf("Expected 'Test analysis text', got %q", text)
	}
}

func TestExtractTextFromResponse_Empty(t *testing.T) {
	resp := &gemini.GenerateContentResponse{}
	text := extractTextFromResponse(resp)
	if text != "" {
		t.Errorf("Expected empty string, got %q", text)
	}
}

func TestBuildAnalysisPrompt(t *testing.T) {
	convoText := "Alice: Hello\nBob: Hi there"
	prompt := buildAnalysisPrompt(convoText)

	if prompt == "" {
		t.Error("Expected non-empty prompt")
	}
	if !strings.Contains(prompt, convoText) {
		t.Error("Expected prompt to include conversation text")
	}
}
