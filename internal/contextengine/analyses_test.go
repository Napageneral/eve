package contextengine

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// setupTestAnalysesDB creates a synthetic test database with conversations and facets
// Matches the schema used by the consolidated analysis query
func setupTestAnalysesDB(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-analyses.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("failed to create test db: %v", err)
	}
	defer db.Close()

	// Create schema matching the TS reference
	schema := `
		CREATE TABLE chats (id INTEGER PRIMARY KEY, display_name TEXT);
		CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT, is_me INTEGER DEFAULT 0);
		CREATE TABLE chat_participants (id INTEGER PRIMARY KEY, chat_id INTEGER, contact_id INTEGER);
		CREATE TABLE conversations (id INTEGER PRIMARY KEY, chat_id INTEGER, start_time TEXT, end_time TEXT, summary TEXT);
		CREATE TABLE messages (id INTEGER PRIMARY KEY, guid TEXT, conversation_id INTEGER, timestamp TEXT, sender_id INTEGER, text TEXT);
		CREATE TABLE topics (id INTEGER PRIMARY KEY, conversation_id INTEGER, contact_id INTEGER, chat_id INTEGER, title TEXT);
		CREATE TABLE entities (id INTEGER PRIMARY KEY, conversation_id INTEGER, contact_id INTEGER, chat_id INTEGER, title TEXT);
		CREATE TABLE emotions (id INTEGER PRIMARY KEY, conversation_id INTEGER, contact_id INTEGER, chat_id INTEGER, emotion_type TEXT);
		CREATE TABLE humor_items (id INTEGER PRIMARY KEY, conversation_id INTEGER, contact_id INTEGER, chat_id INTEGER, snippet TEXT);
	`
	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	// Insert test data: 3 conversations with different facets and times
	now := time.Now()
	yesterday := now.AddDate(0, 0, -1).Format(time.RFC3339)
	lastWeek := now.AddDate(0, 0, -8).Format(time.RFC3339)
	lastMonth := now.AddDate(0, 0, -35).Format(time.RFC3339)

	// Contacts
	db.Exec(`INSERT INTO contacts (id, name, is_me) VALUES (1, 'Alice', 0), (2, 'Bob', 0), (999, 'Me', 1)`)

	// Chats
	db.Exec(`INSERT INTO chats (id, display_name) VALUES (1, 'Chat with Alice'), (2, 'Chat with Bob')`)

	// Chat participants
	db.Exec(`INSERT INTO chat_participants (id, chat_id, contact_id) VALUES (1, 1, 1), (2, 2, 2)`)

	// Conversations
	db.Exec(`INSERT INTO conversations (id, chat_id, start_time, end_time, summary) VALUES (1, 1, ?, ?, 'Discussion about work')`, yesterday, yesterday)
	db.Exec(`INSERT INTO conversations (id, chat_id, start_time, end_time, summary) VALUES (2, 1, ?, ?, 'Health checkup')`, lastWeek, lastWeek)
	db.Exec(`INSERT INTO conversations (id, chat_id, start_time, end_time, summary) VALUES (3, 2, ?, ?, 'Weekend plans')`, lastMonth, lastMonth)

	// Messages (required by consolidated query)
	db.Exec(`INSERT INTO messages (id, guid, conversation_id, timestamp, sender_id, text) VALUES (1, 'msg1', 1, ?, 1, 'Hello')`, yesterday)
	db.Exec(`INSERT INTO messages (id, guid, conversation_id, timestamp, sender_id, text) VALUES (2, 'msg2', 2, ?, 1, 'Hi')`, lastWeek)
	db.Exec(`INSERT INTO messages (id, guid, conversation_id, timestamp, sender_id, text) VALUES (3, 'msg3', 3, ?, 2, 'Hey')`, lastMonth)

	// Facets for conversation 1 (Work, Alice, Happy, joke)
	db.Exec(`INSERT INTO topics (id, conversation_id, contact_id, chat_id, title) VALUES (1, 1, 1, 1, 'Work')`)
	db.Exec(`INSERT INTO entities (id, conversation_id, contact_id, chat_id, title) VALUES (1, 1, 1, 1, 'Alice')`)
	db.Exec(`INSERT INTO emotions (id, conversation_id, contact_id, chat_id, emotion_type) VALUES (1, 1, 1, 1, 'Happy')`)
	db.Exec(`INSERT INTO humor_items (id, conversation_id, contact_id, chat_id, snippet) VALUES (1, 1, 1, 1, 'A funny joke about work')`)

	// Facets for conversation 2 (Health, Doctor, Anxious)
	db.Exec(`INSERT INTO topics (id, conversation_id, contact_id, chat_id, title) VALUES (2, 2, 1, 1, 'Health')`)
	db.Exec(`INSERT INTO entities (id, conversation_id, contact_id, chat_id, title) VALUES (2, 2, 1, 1, 'Doctor')`)
	db.Exec(`INSERT INTO emotions (id, conversation_id, contact_id, chat_id, emotion_type) VALUES (2, 2, 1, 1, 'Anxious')`)

	// Facets for conversation 3 (Weekend, Bob, Excited)
	db.Exec(`INSERT INTO topics (id, conversation_id, contact_id, chat_id, title) VALUES (3, 3, 2, 2, 'Weekend')`)
	db.Exec(`INSERT INTO entities (id, conversation_id, contact_id, chat_id, title) VALUES (3, 3, 2, 2, 'Bob')`)
	db.Exec(`INSERT INTO emotions (id, conversation_id, contact_id, chat_id, emotion_type) VALUES (3, 3, 2, 2, 'Excited')`)

	return dbPath
}

// TestAnalysesAdapter_Basic verifies basic analyses retrieval
func TestAnalysesAdapter_Basic(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 100000,
		"include":   []interface{}{"summary", "topics", "entities", "emotions", "humor"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	if result.Text == "" {
		t.Fatal("expected non-empty text")
	}

	// Should include conversation 1 and 2 (both in chat 1)
	if !strings.Contains(result.Text, "Conversation 1") {
		t.Error("expected conversation 1")
	}
	if !strings.Contains(result.Text, "Conversation 2") {
		t.Error("expected conversation 2")
	}

	// Should not include conversation 3 (chat 2)
	if strings.Contains(result.Text, "Conversation 3") {
		t.Error("did not expect conversation 3")
	}

	// Verify content
	if !strings.Contains(result.Text, "Summary: Discussion about work") {
		t.Error("expected summary for conv 1")
	}
	if !strings.Contains(result.Text, "Topics: Work") {
		t.Error("expected topics for conv 1")
	}
	if !strings.Contains(result.Text, "Entities: Alice") {
		t.Error("expected entities for conv 1")
	}
	if !strings.Contains(result.Text, "Emotions: Happy") {
		t.Error("expected emotions for conv 1")
	}
	if !strings.Contains(result.Text, "Humor:") {
		t.Error("expected humor section")
	}
	if !strings.Contains(result.Text, "A funny joke about work") {
		t.Error("expected humor content")
	}
}

// TestAnalysesAdapter_TimeFiltering verifies time preset filtering
func TestAnalysesAdapter_TimeFiltering(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "week"},
		"token_max": 100000,
		"include":   []interface{}{"summary"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Should include conversation 1 (yesterday) but not conversation 2 (last week, 8 days ago)
	if !strings.Contains(result.Text, "Conversation 1") {
		t.Error("expected conversation 1 (yesterday)")
	}
	if strings.Contains(result.Text, "Conversation 2") {
		t.Error("did not expect conversation 2 (8 days ago)")
	}
}

// TestAnalysesAdapter_ContactIDExpansion verifies contact_ids expansion to chat_ids
func TestAnalysesAdapter_ContactIDExpansion(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"contact_ids": []interface{}{float64(2)}, // Bob -> chat 2
		"time":        map[string]interface{}{"preset": "all"},
		"token_max":   100000,
		"include":     []interface{}{"summary"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Should include conversation 3 (chat 2 with Bob)
	if !strings.Contains(result.Text, "Conversation 3") {
		t.Error("expected conversation 3 (Bob's chat)")
	}

	// Should not include conversations 1 or 2 (chat 1 with Alice)
	if strings.Contains(result.Text, "Conversation 1") || strings.Contains(result.Text, "Conversation 2") {
		t.Error("did not expect conversations from chat 1")
	}
}

// TestAnalysesAdapter_TokenMax verifies token budget
func TestAnalysesAdapter_TokenMax(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 50, // Very small budget
		"include":   []interface{}{"summary", "topics", "entities", "emotions"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Should include at least conversation 1, but may stop before conversation 2
	if !strings.Contains(result.Text, "Conversation 1") {
		t.Error("expected at least conversation 1")
	}
}

// TestAnalysesAdapter_OrderTimeDesc verifies descending time order
func TestAnalysesAdapter_OrderTimeDesc(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "all"},
		"order":     "timeDesc",
		"token_max": 100000,
		"include":   []interface{}{"summary"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Find positions of conversations in output
	pos1 := strings.Index(result.Text, "Conversation 1")
	pos2 := strings.Index(result.Text, "Conversation 2")

	if pos1 == -1 || pos2 == -1 {
		t.Fatal("expected both conversations")
	}

	// Conversation 1 (yesterday) should appear before conversation 2 (last week) in DESC order
	if pos1 > pos2 {
		t.Error("expected conversation 1 (most recent) before conversation 2 in DESC order")
	}
}

// TestAnalysesAdapter_FacetFiltering_Entities verifies entity filtering
func TestAnalysesAdapter_FacetFiltering_Entities(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 100000,
		"match":     map[string]interface{}{"entities": []interface{}{"Alice"}},
		"include":   []interface{}{"summary"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Should include conversation 1 (has Alice entity)
	if !strings.Contains(result.Text, "Conversation 1") {
		t.Error("expected conversation 1 (has Alice entity)")
	}

	// Should not include conversation 2 (has Doctor entity, not Alice)
	if strings.Contains(result.Text, "Conversation 2") {
		t.Error("did not expect conversation 2 (no Alice entity)")
	}
}

// TestAnalysesAdapter_FacetFiltering_Topics verifies topic filtering
func TestAnalysesAdapter_FacetFiltering_Topics(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 100000,
		"match":     map[string]interface{}{"topics": []interface{}{"Health"}},
		"include":   []interface{}{"summary"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Should include conversation 2 (has Health topic)
	if !strings.Contains(result.Text, "Conversation 2") {
		t.Error("expected conversation 2 (has Health topic)")
	}

	// Should not include conversation 1 (has Work topic, not Health)
	if strings.Contains(result.Text, "Conversation 1") {
		t.Error("did not expect conversation 1 (no Health topic)")
	}
}

// TestAnalysesAdapter_FacetFiltering_Emotions verifies emotion filtering
func TestAnalysesAdapter_FacetFiltering_Emotions(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(2)},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 100000,
		"match":     map[string]interface{}{"emotions": []interface{}{"Excited"}},
		"include":   []interface{}{"summary"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Should include conversation 3 (has Excited emotion)
	if !strings.Contains(result.Text, "Conversation 3") {
		t.Error("expected conversation 3 (has Excited emotion)")
	}
}

// TestAnalysesAdapter_IncludeFiltering verifies include parameter
func TestAnalysesAdapter_IncludeFiltering(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 100000,
		"include":   []interface{}{"summary"}, // Only summary, no facets
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	// Should include summary
	if !strings.Contains(result.Text, "Summary:") {
		t.Error("expected summary")
	}

	// Should NOT include facets
	if strings.Contains(result.Text, "Topics:") || strings.Contains(result.Text, "Entities:") || strings.Contains(result.Text, "Emotions:") || strings.Contains(result.Text, "Humor:") {
		t.Error("did not expect facets when include=[summary]")
	}
}

// TestAnalysesAdapter_EmptyResult verifies graceful empty result
func TestAnalysesAdapter_EmptyResult(t *testing.T) {
	dbPath := setupTestAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(999)}, // Non-existent chat
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 100000,
		"include":   []interface{}{"summary"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	if result.Text != "" {
		t.Error("expected empty result for non-existent chat")
	}
}

// TestAnalysesAdapter_NoDBPath verifies error when no DB path provided
func TestAnalysesAdapter_NoDBPath(t *testing.T) {
	params := map[string]interface{}{
		"chat_ids": []interface{}{float64(1)},
	}

	_, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: "", Vars: make(map[string]interface{})})
	if err == nil {
		t.Error("expected error when no DB path provided")
	}
}
