package contextengine

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// setupTestDB creates a synthetic test database with known conversations
func setupTestConvosDB(t *testing.T) (string, func()) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("failed to create test database: %v", err)
	}
	defer db.Close()

	// Create schema
	schema := `
		CREATE TABLE contacts (
			id INTEGER PRIMARY KEY,
			name TEXT,
			is_me INTEGER DEFAULT 0
		);

		CREATE TABLE conversations (
			id INTEGER PRIMARY KEY,
			chat_id INTEGER,
			start_time TEXT,
			end_time TEXT
		);

		CREATE TABLE messages (
			id INTEGER PRIMARY KEY,
			conversation_id INTEGER,
			guid TEXT,
			timestamp TEXT,
			sender_id INTEGER,
			text TEXT
		);

		CREATE TABLE attachments (
			id INTEGER PRIMARY KEY,
			message_id INTEGER,
			mime_type TEXT,
			file_name TEXT,
			is_sticker INTEGER DEFAULT 0
		);

		CREATE TABLE reactions (
			id INTEGER PRIMARY KEY,
			original_message_guid TEXT,
			reaction_type INTEGER,
			sender_id INTEGER
		);

		CREATE TABLE chat_participants (
			id INTEGER PRIMARY KEY,
			chat_id INTEGER,
			contact_id INTEGER
		);

		CREATE TABLE entities (
			id INTEGER PRIMARY KEY,
			conversation_id INTEGER,
			title TEXT
		);

		CREATE TABLE topics (
			id INTEGER PRIMARY KEY,
			conversation_id INTEGER,
			title TEXT
		);

		CREATE TABLE emotions (
			id INTEGER PRIMARY KEY,
			conversation_id INTEGER,
			emotion_type TEXT
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	// Insert test data
	// Contacts
	_, err = db.Exec(`
		INSERT INTO contacts (id, name, is_me) VALUES
		(1, 'Alice', 0),
		(2, 'Bob', 0),
		(3, 'Me', 1)
	`)
	if err != nil {
		t.Fatalf("failed to insert contacts: %v", err)
	}

	// Conversations
	now := time.Now()
	yesterday := now.AddDate(0, 0, -1)
	lastWeek := now.AddDate(0, 0, -7)
	lastMonth := now.AddDate(0, 0, -30)

	_, err = db.Exec(`
		INSERT INTO conversations (id, chat_id, start_time, end_time) VALUES
		(1, 100, ?, ?),
		(2, 100, ?, ?),
		(3, 200, ?, ?)
	`, lastMonth.Format(time.RFC3339), lastMonth.Add(time.Hour).Format(time.RFC3339),
		lastWeek.Format(time.RFC3339), lastWeek.Add(time.Hour).Format(time.RFC3339),
		yesterday.Format(time.RFC3339), yesterday.Add(time.Hour).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert conversations: %v", err)
	}

	// Messages for conversation 1 (chat 100, last month)
	_, err = db.Exec(`
		INSERT INTO messages (id, conversation_id, guid, timestamp, sender_id, text) VALUES
		(1, 1, 'msg-1', ?, 1, 'Hello from Alice'),
		(2, 1, 'msg-2', ?, 3, 'Hi Alice!')
	`, lastMonth.Format(time.RFC3339), lastMonth.Add(time.Minute).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert messages for conv 1: %v", err)
	}

	// Messages for conversation 2 (chat 100, last week)
	_, err = db.Exec(`
		INSERT INTO messages (id, conversation_id, guid, timestamp, sender_id, text) VALUES
		(3, 2, 'msg-3', ?, 1, 'How are you?'),
		(4, 2, 'msg-4', ?, 3, 'I am good!')
	`, lastWeek.Format(time.RFC3339), lastWeek.Add(time.Minute).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert messages for conv 2: %v", err)
	}

	// Messages for conversation 3 (chat 200, yesterday)
	_, err = db.Exec(`
		INSERT INTO messages (id, conversation_id, guid, timestamp, sender_id, text) VALUES
		(5, 3, 'msg-5', ?, 2, 'Hey Bob here'),
		(6, 3, 'msg-6', ?, 3, 'Hi Bob!')
	`, yesterday.Format(time.RFC3339), yesterday.Add(time.Minute).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert messages for conv 3: %v", err)
	}

	// Chat participants (for contact_ids expansion)
	_, err = db.Exec(`
		INSERT INTO chat_participants (id, chat_id, contact_id) VALUES
		(1, 100, 1),
		(2, 200, 2)
	`)
	if err != nil {
		t.Fatalf("failed to insert chat_participants: %v", err)
	}

	// Entities for conversation 1
	_, err = db.Exec(`
		INSERT INTO entities (id, conversation_id, title) VALUES
		(1, 1, 'Work')
	`)
	if err != nil {
		t.Fatalf("failed to insert entities: %v", err)
	}

	// Topics for conversation 2
	_, err = db.Exec(`
		INSERT INTO topics (id, conversation_id, title) VALUES
		(1, 2, 'Health')
	`)
	if err != nil {
		t.Fatalf("failed to insert topics: %v", err)
	}

	// Emotions for conversation 3
	_, err = db.Exec(`
		INSERT INTO emotions (id, conversation_id, emotion_type) VALUES
		(1, 3, 'Happy')
	`)
	if err != nil {
		t.Fatalf("failed to insert emotions: %v", err)
	}

	cleanup := func() {
		os.Remove(dbPath)
	}

	return dbPath, cleanup
}

func TestConvosContextAdapter_Basic(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	params := map[string]interface{}{
		"chat_ids":  []interface{}{100},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	if result.Text == "" {
		t.Fatal("expected non-empty text")
	}

	// Should contain messages from chat 100 (conversations 1 and 2)
	if !strings.Contains(result.Text, "Hello from Alice") {
		t.Error("expected to find 'Hello from Alice'")
	}
	if !strings.Contains(result.Text, "How are you?") {
		t.Error("expected to find 'How are you?'")
	}

	// Should NOT contain messages from chat 200
	if strings.Contains(result.Text, "Hey Bob here") {
		t.Error("should not contain messages from chat 200")
	}

	// Check token estimation
	if result.ActualTokens <= 0 {
		t.Error("expected positive token count")
	}
}

func TestConvosContextAdapter_TimeFiltering(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Test "week" preset - should only get last week's conversation
	params := map[string]interface{}{
		"chat_ids":  []interface{}{100},
		"time":      map[string]interface{}{"preset": "week"},
		"token_max": 10000,
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should contain last week's message
	if !strings.Contains(result.Text, "How are you?") {
		t.Error("expected to find 'How are you?' from last week")
	}

	// Should NOT contain last month's message (outside week window)
	if strings.Contains(result.Text, "Hello from Alice") {
		t.Error("should not contain 'Hello from Alice' from last month")
	}
}

func TestConvosContextAdapter_ContactIDExpansion(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Use contact_ids instead of chat_ids
	// Contact 1 (Alice) is in chat 100
	params := map[string]interface{}{
		"contact_ids": []interface{}{1},
		"time":        map[string]interface{}{"preset": "all"},
		"token_max":   10000,
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should contain messages from chat 100 (Alice's chat)
	if !strings.Contains(result.Text, "Hello from Alice") {
		t.Error("expected to find 'Hello from Alice'")
	}

	// Should NOT contain messages from chat 200 (Bob's chat)
	if strings.Contains(result.Text, "Hey Bob here") {
		t.Error("should not contain messages from chat 200")
	}
}

func TestConvosContextAdapter_TokenMax(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Set reasonable token_max to force truncation after first conversation
	// Each test conversation has ~2 messages of ~15 chars each = ~8 tokens
	// So token_max of 50 should allow 1-2 conversations but not all 3
	params := map[string]interface{}{
		"chat_ids":  []interface{}{100},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 50, // Should fit 1-2 conversations
		"order":     "timeAsc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should get some text (at least one conversation)
	if result.Text == "" {
		t.Fatal("expected some text with token_max=50")
	}

	// Token count should be within budget (roughly)
	if result.ActualTokens > 60 { // Allow some slack
		t.Errorf("expected tokens <= 60, got %d", result.ActualTokens)
	}

	// Should NOT get all conversations (would be > 100 tokens)
	if result.ActualTokens > 100 {
		t.Error("expected truncation, but got all conversations")
	}
}

func TestConvosContextAdapter_OrderTimeDesc(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Order by timeDesc - should get newest first
	params := map[string]interface{}{
		"chat_ids":  []interface{}{100},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
		"order":     "timeDesc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Find positions of the two conversations
	posLastWeek := strings.Index(result.Text, "How are you?")
	posLastMonth := strings.Index(result.Text, "Hello from Alice")

	if posLastWeek == -1 || posLastMonth == -1 {
		t.Fatal("expected to find both conversations")
	}

	// With timeDesc, last week should come before last month
	if posLastWeek >= posLastMonth {
		t.Error("expected last week's conversation to come before last month's with timeDesc order")
	}
}

func TestConvosContextAdapter_FacetFiltering_Entities(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Filter by entity "Work" - should only get conversation 1
	params := map[string]interface{}{
		"chat_ids":  []interface{}{100},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
		"match": map[string]interface{}{
			"entities": []interface{}{"Work"},
		},
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should contain conversation 1 (has "Work" entity)
	if !strings.Contains(result.Text, "Hello from Alice") {
		t.Error("expected to find 'Hello from Alice' (has Work entity)")
	}

	// Should NOT contain conversation 2 (no "Work" entity)
	if strings.Contains(result.Text, "How are you?") {
		t.Error("should not contain 'How are you?' (no Work entity)")
	}
}

func TestConvosContextAdapter_FacetFiltering_Topics(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Filter by topic "Health" - should only get conversation 2
	params := map[string]interface{}{
		"chat_ids":  []interface{}{100},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
		"match": map[string]interface{}{
			"topics": []interface{}{"Health"},
		},
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should contain conversation 2 (has "Health" topic)
	if !strings.Contains(result.Text, "How are you?") {
		t.Error("expected to find 'How are you?' (has Health topic)")
	}

	// Should NOT contain conversation 1 (no "Health" topic)
	if strings.Contains(result.Text, "Hello from Alice") {
		t.Error("should not contain 'Hello from Alice' (no Health topic)")
	}
}

func TestConvosContextAdapter_FacetFiltering_Emotions(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Filter by emotion "Happy" - should only get conversation 3
	params := map[string]interface{}{
		"chat_ids":  []interface{}{200},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
		"match": map[string]interface{}{
			"emotions": []interface{}{"Happy"},
		},
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should contain conversation 3 (has "Happy" emotion)
	if !strings.Contains(result.Text, "Hey Bob here") {
		t.Error("expected to find 'Hey Bob here' (has Happy emotion)")
	}
}

func TestConvosContextAdapter_EncodeOptions(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Test with include_sender = false
	params := map[string]interface{}{
		"chat_ids":  []interface{}{100},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
		"encode": map[string]interface{}{
			"include_sender": false,
		},
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should NOT contain sender names followed by colon
	if strings.Contains(result.Text, "Alice:") {
		t.Error("should not contain 'Alice:' when include_sender is false")
	}
	if strings.Contains(result.Text, "Me:") {
		t.Error("should not contain 'Me:' when include_sender is false")
	}

	// Should still contain the message text
	if !strings.Contains(result.Text, "Hello from Alice") {
		t.Error("should still contain message text")
	}
}

func TestConvosContextAdapter_EmptyResult(t *testing.T) {
	dbPath, cleanup := setupTestConvosDB(t)
	defer cleanup()

	// Query for non-existent chat
	params := map[string]interface{}{
		"chat_ids":  []interface{}{999},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
	}

	context := RetrievalContext{
		DBPath: dbPath,
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should return empty text
	if result.Text != "" {
		t.Error("expected empty text for non-existent chat")
	}

	// Token count should be zero
	if result.ActualTokens != 0 {
		t.Errorf("expected zero tokens, got %d", result.ActualTokens)
	}
}

func TestParseConvosParams(t *testing.T) {
	params := map[string]interface{}{
		"chat_ids":    []interface{}{100, 200},
		"contact_ids": []interface{}{1, 2},
		"time": map[string]interface{}{
			"preset":     "week",
			"start_date": "2025-01-01T00:00:00Z",
			"end_date":   "2025-01-07T00:00:00Z",
		},
		"token_max": 5000,
		"order":     "timeDesc",
		"match": map[string]interface{}{
			"entities": []interface{}{"Work", "Home"},
			"topics":   []interface{}{"Health"},
			"emotions": []interface{}{"Happy", "Sad"},
		},
		"encode": map[string]interface{}{
			"include_sender":      false,
			"include_attachments": false,
			"include_reactions":   true,
		},
	}

	parsed := parseConvosParams(params)

	// Check chat_ids
	if len(parsed.ChatIDs) != 2 || parsed.ChatIDs[0] != 100 || parsed.ChatIDs[1] != 200 {
		t.Errorf("unexpected chat_ids: %v", parsed.ChatIDs)
	}

	// Check contact_ids
	if len(parsed.ContactIDs) != 2 || parsed.ContactIDs[0] != 1 || parsed.ContactIDs[1] != 2 {
		t.Errorf("unexpected contact_ids: %v", parsed.ContactIDs)
	}

	// Check time
	if parsed.Time.Preset != "week" {
		t.Errorf("unexpected preset: %s", parsed.Time.Preset)
	}
	if parsed.Time.StartDate != "2025-01-01T00:00:00Z" {
		t.Errorf("unexpected start_date: %s", parsed.Time.StartDate)
	}
	if parsed.Time.EndDate != "2025-01-07T00:00:00Z" {
		t.Errorf("unexpected end_date: %s", parsed.Time.EndDate)
	}

	// Check token_max
	if parsed.TokenMax != 5000 {
		t.Errorf("unexpected token_max: %d", parsed.TokenMax)
	}

	// Check order
	if parsed.Order != "timeDesc" {
		t.Errorf("unexpected order: %s", parsed.Order)
	}

	// Check match
	if len(parsed.Match.Entities) != 2 || parsed.Match.Entities[0] != "Work" {
		t.Errorf("unexpected entities: %v", parsed.Match.Entities)
	}
	if len(parsed.Match.Topics) != 1 || parsed.Match.Topics[0] != "Health" {
		t.Errorf("unexpected topics: %v", parsed.Match.Topics)
	}
	if len(parsed.Match.Emotions) != 2 || parsed.Match.Emotions[0] != "Happy" {
		t.Errorf("unexpected emotions: %v", parsed.Match.Emotions)
	}

	// Check encode
	if parsed.Encode.IncludeSender != false {
		t.Error("expected include_sender to be false")
	}
	if parsed.Encode.IncludeAttachments != false {
		t.Error("expected include_attachments to be false")
	}
	if parsed.Encode.IncludeReactions != true {
		t.Error("expected include_reactions to be true")
	}
}

func TestResolveTime(t *testing.T) {
	tests := []struct {
		name   string
		params TimeParams
		check  func(start, end string) bool
	}{
		{
			name:   "preset day",
			params: TimeParams{Preset: "day"},
			check: func(start, end string) bool {
				// Start should be ~1 day ago, end should be now
				startTime, _ := time.Parse(time.RFC3339, start)
				endTime, _ := time.Parse(time.RFC3339, end)
				diff := endTime.Sub(startTime)
				return diff > 23*time.Hour && diff < 25*time.Hour
			},
		},
		{
			name:   "preset week",
			params: TimeParams{Preset: "week"},
			check: func(start, end string) bool {
				startTime, _ := time.Parse(time.RFC3339, start)
				endTime, _ := time.Parse(time.RFC3339, end)
				diff := endTime.Sub(startTime)
				return diff > 6*24*time.Hour && diff < 8*24*time.Hour
			},
		},
		{
			name:   "preset all",
			params: TimeParams{Preset: "all"},
			check: func(start, end string) bool {
				return start == "1970-01-01T00:00:00Z" && end == "3000-01-01T00:00:00Z"
			},
		},
		{
			name: "custom dates",
			params: TimeParams{
				StartDate: "2025-01-01T00:00:00Z",
				EndDate:   "2025-01-07T00:00:00Z",
			},
			check: func(start, end string) bool {
				return start == "2025-01-01T00:00:00Z" && end == "2025-01-07T00:00:00Z"
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			start, end := resolveTime(tt.params)
			if !tt.check(start, end) {
				t.Errorf("time resolution failed: start=%s, end=%s", start, end)
			}
		})
	}
}
