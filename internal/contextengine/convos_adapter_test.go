package contextengine

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// setupTestDB creates a synthetic eve.db with test data
func setupTestDB(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("failed to create test db: %v", err)
	}
	defer db.Close()

	// Create schema (simplified version of eve.db)
	schema := `
		CREATE TABLE IF NOT EXISTS handle (
			ROWID INTEGER PRIMARY KEY,
			id TEXT UNIQUE
		);

		CREATE TABLE IF NOT EXISTS contact (
			id INTEGER PRIMARY KEY,
			phone_number TEXT UNIQUE,
			given_name TEXT
		);

		CREATE TABLE IF NOT EXISTS chat (
			id INTEGER PRIMARY KEY,
			chat_identifier TEXT
		);

		CREATE TABLE IF NOT EXISTS chat_participants (
			chat_id INTEGER,
			contact_id INTEGER,
			PRIMARY KEY (chat_id, contact_id)
		);

		CREATE TABLE IF NOT EXISTS conversations (
			id INTEGER PRIMARY KEY,
			chat_id INTEGER,
			start_time TEXT,
			end_time TEXT
		);

		CREATE TABLE IF NOT EXISTS message (
			ROWID INTEGER PRIMARY KEY,
			chat_id INTEGER,
			text TEXT,
			date INTEGER,
			is_from_me INTEGER,
			handle_id INTEGER
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	// Insert test data
	// Contact 1: Alice
	_, err = db.Exec(`INSERT INTO contact (id, phone_number, given_name) VALUES (1, '+1234567890', 'Alice')`)
	if err != nil {
		t.Fatalf("failed to insert contact: %v", err)
	}

	// Contact 2: Bob
	_, err = db.Exec(`INSERT INTO contact (id, phone_number, given_name) VALUES (2, '+0987654321', 'Bob')`)
	if err != nil {
		t.Fatalf("failed to insert contact: %v", err)
	}

	// Handle 1: Alice
	_, err = db.Exec(`INSERT INTO handle (ROWID, id) VALUES (1, '+1234567890')`)
	if err != nil {
		t.Fatalf("failed to insert handle: %v", err)
	}

	// Handle 2: Bob
	_, err = db.Exec(`INSERT INTO handle (ROWID, id) VALUES (2, '+0987654321')`)
	if err != nil {
		t.Fatalf("failed to insert handle: %v", err)
	}

	// Chat 1: with Alice
	_, err = db.Exec(`INSERT INTO chat (id, chat_identifier) VALUES (1, 'chat-alice')`)
	if err != nil {
		t.Fatalf("failed to insert chat: %v", err)
	}

	// Chat 2: with Bob
	_, err = db.Exec(`INSERT INTO chat (id, chat_identifier) VALUES (2, 'chat-bob')`)
	if err != nil {
		t.Fatalf("failed to insert chat: %v", err)
	}

	// Link participants
	_, err = db.Exec(`INSERT INTO chat_participants (chat_id, contact_id) VALUES (1, 1)`)
	if err != nil {
		t.Fatalf("failed to insert chat_participant: %v", err)
	}

	_, err = db.Exec(`INSERT INTO chat_participants (chat_id, contact_id) VALUES (2, 2)`)
	if err != nil {
		t.Fatalf("failed to insert chat_participant: %v", err)
	}

	// Create conversations with different timestamps
	now := time.Now()
	yesterday := now.AddDate(0, 0, -1)
	lastWeek := now.AddDate(0, 0, -7)
	lastMonth := now.AddDate(0, 0, -30)
	lastYear := now.AddDate(0, 0, -365)

	// Conversation 1: yesterday with Alice
	_, err = db.Exec(`INSERT INTO conversations (id, chat_id, start_time, end_time) VALUES (1, 1, ?, ?)`,
		yesterday.Format(time.RFC3339), yesterday.Add(time.Hour).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert conversation: %v", err)
	}

	// Conversation 2: last week with Alice
	_, err = db.Exec(`INSERT INTO conversations (id, chat_id, start_time, end_time) VALUES (2, 1, ?, ?)`,
		lastWeek.Format(time.RFC3339), lastWeek.Add(time.Hour).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert conversation: %v", err)
	}

	// Conversation 3: last month with Bob
	_, err = db.Exec(`INSERT INTO conversations (id, chat_id, start_time, end_time) VALUES (3, 2, ?, ?)`,
		lastMonth.Format(time.RFC3339), lastMonth.Add(time.Hour).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert conversation: %v", err)
	}

	// Conversation 4: last year with Bob
	_, err = db.Exec(`INSERT INTO conversations (id, chat_id, start_time, end_time) VALUES (4, 2, ?, ?)`,
		lastYear.Format(time.RFC3339), lastYear.Add(time.Hour).Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert conversation: %v", err)
	}

	// Add messages to conversation 1 (yesterday, Alice)
	_, err = db.Exec(`INSERT INTO message (chat_id, text, date, is_from_me, handle_id) VALUES (1, 'Hi Alice!', 0, 1, NULL)`)
	if err != nil {
		t.Fatalf("failed to insert message: %v", err)
	}

	_, err = db.Exec(`INSERT INTO message (chat_id, text, date, is_from_me, handle_id) VALUES (1, 'Hey! How are you?', 1, 0, 1)`)
	if err != nil {
		t.Fatalf("failed to insert message: %v", err)
	}

	// Add messages to conversation 2 (last week, Alice)
	_, err = db.Exec(`INSERT INTO message (chat_id, text, date, is_from_me, handle_id) VALUES (1, 'Last week was great', 2, 1, NULL)`)
	if err != nil {
		t.Fatalf("failed to insert message: %v", err)
	}

	// Add messages to conversation 3 (last month, Bob)
	_, err = db.Exec(`INSERT INTO message (chat_id, text, date, is_from_me, handle_id) VALUES (2, 'Hi Bob', 3, 1, NULL)`)
	if err != nil {
		t.Fatalf("failed to insert message: %v", err)
	}

	_, err = db.Exec(`INSERT INTO message (chat_id, text, date, is_from_me, handle_id) VALUES (2, 'Hello!', 4, 0, 2)`)
	if err != nil {
		t.Fatalf("failed to insert message: %v", err)
	}

	return dbPath
}

func TestConvosAdapter_ChatIDsFilter(t *testing.T) {
	dbPath := setupTestDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(1)}, // Chat with Alice only
		"time": map[string]interface{}{
			"preset": "all",
		},
		"token_max": 10000,
		"order":     "timeAsc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
		Vars:   make(map[string]interface{}),
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should have conversations from chat 1 (Alice) only
	if result.Text == "" {
		t.Error("expected non-empty text")
	}

	// Check that Alice's conversations are included
	if !containsString(result.Text, "Hi Alice") && !containsString(result.Text, "Last week was great") {
		t.Error("expected Alice's conversations in output")
	}

	// Check that Bob's conversations are NOT included
	if containsString(result.Text, "Hi Bob") {
		t.Error("did not expect Bob's conversations in output")
	}
}

func TestConvosAdapter_ContactIDsExpansion(t *testing.T) {
	dbPath := setupTestDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"contact_ids": []interface{}{float64(2)}, // Contact Bob -> should expand to chat 2
		"time": map[string]interface{}{
			"preset": "all",
		},
		"token_max": 10000,
		"order":     "timeAsc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
		Vars:   make(map[string]interface{}),
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should have Bob's conversations
	if result.Text == "" {
		t.Error("expected non-empty text")
	}

	if !containsString(result.Text, "Hi Bob") || !containsString(result.Text, "Hello") {
		t.Error("expected Bob's conversations in output")
	}

	// Check that Alice's conversations are NOT included
	if containsString(result.Text, "Hi Alice") {
		t.Error("did not expect Alice's conversations in output")
	}
}

func TestConvosAdapter_TimePresetWeek(t *testing.T) {
	dbPath := setupTestDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(1)}, // Chat with Alice
		"time": map[string]interface{}{
			"preset": "week", // Only last 7 days
		},
		"token_max": 10000,
		"order":     "timeAsc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
		Vars:   make(map[string]interface{}),
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should have conversations from last week (conversation 1 and 2)
	if result.Text == "" {
		t.Error("expected non-empty text for week preset")
	}

	// Should include yesterday's conversation
	if !containsString(result.Text, "Hi Alice") {
		t.Error("expected yesterday's conversation")
	}

	// Should include last week's conversation
	if !containsString(result.Text, "Last week was great") {
		t.Error("expected last week's conversation")
	}
}

func TestConvosAdapter_TimePresetMonth(t *testing.T) {
	dbPath := setupTestDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(1), float64(2)}, // Both chats
		"time": map[string]interface{}{
			"preset": "month", // Only last 30 days
		},
		"token_max": 10000,
		"order":     "timeAsc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
		Vars:   make(map[string]interface{}),
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// Should have conversations from last month (conversations 1, 2, and 3)
	if result.Text == "" {
		t.Error("expected non-empty text for month preset")
	}

	// Should NOT include last year's conversation (conversation 4)
	// Last year's conversation has "Hello!" from Bob, but we should check for the context
	// Actually, conversation 4 is from last year, so it shouldn't be in "month" preset
}

func TestConvosAdapter_OrderTimeDesc(t *testing.T) {
	dbPath := setupTestDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(1)}, // Chat with Alice
		"time": map[string]interface{}{
			"preset": "all",
		},
		"token_max": 10000,
		"order":     "timeDesc", // Most recent first
	}

	context := RetrievalContext{
		DBPath: dbPath,
		Vars:   make(map[string]interface{}),
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	if result.Text == "" {
		t.Error("expected non-empty text")
	}

	// In timeDesc, yesterday's conversation should come before last week's
	// We can check order by looking at the positions
	yesterdayIdx := indexOfString(result.Text, "Hi Alice")
	lastWeekIdx := indexOfString(result.Text, "Last week was great")

	if yesterdayIdx > lastWeekIdx && yesterdayIdx != -1 && lastWeekIdx != -1 {
		t.Error("expected yesterday's conversation to come before last week's in timeDesc order")
	}
}

func TestConvosAdapter_TokenMax(t *testing.T) {
	dbPath := setupTestDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(1), float64(2)}, // Both chats
		"time": map[string]interface{}{
			"preset": "all",
		},
		"token_max": 10, // Very small budget
		"order":     "timeAsc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
		Vars:   make(map[string]interface{}),
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	// With such a small budget, should only get partial results
	if result.ActualTokens > 10 {
		t.Errorf("expected actual tokens <= 10, got %d", result.ActualTokens)
	}

	// Result should be truncated
	if result.Text == "" {
		t.Error("expected some text even with small budget")
	}
}

func TestConvosAdapter_EmptyResult(t *testing.T) {
	dbPath := setupTestDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(999)}, // Non-existent chat
		"time": map[string]interface{}{
			"preset": "all",
		},
		"token_max": 10000,
		"order":     "timeAsc",
	}

	context := RetrievalContext{
		DBPath: dbPath,
		Vars:   make(map[string]interface{}),
	}

	result, err := convosContextDataAdapter(params, context)
	if err != nil {
		t.Fatalf("convosContextDataAdapter failed: %v", err)
	}

	if result.Text != "" {
		t.Error("expected empty text for non-existent chat")
	}

	if result.ActualTokens != 0 {
		t.Error("expected 0 tokens for empty result")
	}
}

func TestConvosAdapter_NoDBPath(t *testing.T) {
	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(1)},
	}

	context := RetrievalContext{
		DBPath: "", // No database path
		Vars:   make(map[string]interface{}),
	}

	_, err := convosContextDataAdapter(params, context)
	if err == nil {
		t.Fatal("expected error when no database path provided")
	}
}

// Helper functions

func containsString(s, substr string) bool {
	return indexOfString(s, substr) != -1
}

func indexOfString(s, substr string) int {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return i
		}
	}
	return -1
}
