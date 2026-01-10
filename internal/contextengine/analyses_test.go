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

func setupAnalysesDB(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-analyses.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("failed to create test db: %v", err)
	}
	defer db.Close()

	schema := `
		CREATE TABLE chat (id INTEGER PRIMARY KEY);
		CREATE TABLE chat_participants (chat_id INTEGER, contact_id INTEGER);
		CREATE TABLE contact (id INTEGER PRIMARY KEY, phone_number TEXT, given_name TEXT);
		CREATE TABLE conversations (id INTEGER PRIMARY KEY, chat_id INTEGER, start_time TEXT, summary TEXT);
		CREATE TABLE topics (conversation_id INTEGER, title TEXT);
		CREATE TABLE entities (conversation_id INTEGER, title TEXT);
		CREATE TABLE emotions (conversation_id INTEGER, emotion_type TEXT);
		CREATE TABLE humor (conversation_id INTEGER, description TEXT);
	`
	if _, err := db.Exec(schema); err != nil {
		t.Fatal(err)
	}

	// Insert test data
	db.Exec(`INSERT INTO contact VALUES (1, '+1234567890', 'Alice')`)
	db.Exec(`INSERT INTO chat VALUES (1)`)
	db.Exec(`INSERT INTO chat_participants VALUES (1, 1)`)

	yesterday := time.Now().AddDate(0, 0, -1).Format(time.RFC3339)
	db.Exec(`INSERT INTO conversations VALUES (1, 1, ?, 'Test summary')`, yesterday)
	db.Exec(`INSERT INTO topics VALUES (1, 'Topic A'), (1, 'Topic B')`)
	db.Exec(`INSERT INTO entities VALUES (1, 'Alice'), (1, 'Bob')`)
	db.Exec(`INSERT INTO emotions VALUES (1, 'Happy')`)
	db.Exec(`INSERT INTO humor VALUES (1, 'Funny joke')`)

	return dbPath
}

func TestAnalysesAdapter(t *testing.T) {
	dbPath := setupAnalysesDB(t)
	defer os.Remove(dbPath)

	params := map[string]interface{}{
		"chat_ids":  []interface{}{float64(1)},
		"time":      map[string]interface{}{"preset": "all"},
		"token_max": 10000,
		"include":   []interface{}{"summary", "topics", "entities", "emotions", "humor"},
	}

	result, err := analysesContextDataAdapter(params, RetrievalContext{DBPath: dbPath, Vars: make(map[string]interface{})})
	if err != nil {
		t.Fatalf("failed: %v", err)
	}

	if result.Text == "" {
		t.Error("expected non-empty text")
	}

	if !strings.Contains(result.Text, "Summary: Test summary") {
		t.Error("expected summary")
	}

	if !strings.Contains(result.Text, "Topics:") {
		t.Error("expected topics")
	}
}
