package encoding

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func createTestDB(t *testing.T) (string, int64) {
	t.Helper()

	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("failed to create test database: %v", err)
	}
	defer db.Close()

	// Create simplified message table schema
	_, err = db.Exec(`
		CREATE TABLE handle (
			ROWID INTEGER PRIMARY KEY,
			id TEXT,
			display_name TEXT
		);

		CREATE TABLE contact (
			ROWID INTEGER PRIMARY KEY,
			phone_number TEXT,
			display_name TEXT
		);

		CREATE TABLE message (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			guid TEXT,
			text TEXT,
			handle_id INTEGER,
			date INTEGER,
			is_from_me INTEGER,
			FOREIGN KEY (handle_id) REFERENCES handle(ROWID)
		);
	`)
	if err != nil {
		t.Fatalf("failed to create tables: %v", err)
	}

	// Insert test data
	// Insert handle for Bob
	_, err = db.Exec(`INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')`)
	if err != nil {
		t.Fatalf("failed to insert handle: %v", err)
	}

	// Insert contact for Bob
	_, err = db.Exec(`INSERT INTO contact (phone_number, display_name) VALUES ('+15551234567', 'Bob')`)
	if err != nil {
		t.Fatalf("failed to insert contact: %v", err)
	}

	// Insert messages
	// iMessage date format: nanoseconds since 2001-01-01
	// We'll use simplified timestamps for testing
	baseTime := time.Date(2025, 1, 10, 10, 0, 0, 0, time.UTC)
	ref := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
	baseNano := int64(baseTime.Sub(ref).Nanoseconds())

	// Message 1: from me
	result, err := db.Exec(`
		INSERT INTO message (guid, text, handle_id, date, is_from_me)
		VALUES ('msg-1', 'Hello Bob!', NULL, ?, 1)
	`, baseNano)
	if err != nil {
		t.Fatalf("failed to insert message 1: %v", err)
	}
	msg1ID, _ := result.LastInsertId()

	// Message 2: from Bob
	_, err = db.Exec(`
		INSERT INTO message (guid, text, handle_id, date, is_from_me)
		VALUES ('msg-2', 'Hi Alice!', 1, ?, 0)
	`, baseNano+60*1e9) // 1 minute later
	if err != nil {
		t.Fatalf("failed to insert message 2: %v", err)
	}

	// Message 3: from me
	_, err = db.Exec(`
		INSERT INTO message (guid, text, handle_id, date, is_from_me)
		VALUES ('msg-3', 'How are you?', NULL, ?, 1)
	`, baseNano+120*1e9) // 2 minutes later
	if err != nil {
		t.Fatalf("failed to insert message 3: %v", err)
	}

	return dbPath, msg1ID
}

func TestLoadConversation(t *testing.T) {
	dbPath, conversationID := createTestDB(t)

	conv, err := LoadConversation(dbPath, conversationID)
	if err != nil {
		t.Fatalf("LoadConversation failed: %v", err)
	}

	if conv == nil {
		t.Fatal("Expected non-nil conversation")
	}

	if conv.ID != conversationID {
		t.Errorf("Expected conversation ID %d, got %d", conversationID, conv.ID)
	}

	if len(conv.Messages) == 0 {
		t.Error("Expected at least one message")
	}
}

func TestEncodeMessage(t *testing.T) {
	msg := Message{
		SenderName: "Alice",
		Text:       "Hello world!",
		Timestamp:  time.Date(2025, 1, 10, 15, 30, 0, 0, time.UTC),
	}

	opts := DefaultEncodeOptions()
	encoded := EncodeMessage(msg, opts)

	if !strings.Contains(encoded, "Alice:") {
		t.Error("Expected encoded message to contain sender name")
	}

	if !strings.Contains(encoded, "Hello world!") {
		t.Error("Expected encoded message to contain text")
	}
}

func TestEncodeMessageWithTime(t *testing.T) {
	msg := Message{
		SenderName: "Alice",
		Text:       "Hello!",
		Timestamp:  time.Date(2025, 1, 10, 15, 30, 0, 0, time.UTC),
	}

	opts := DefaultEncodeOptions()
	opts.IncludeSendTime = true
	encoded := EncodeMessage(msg, opts)

	if !strings.Contains(encoded, "[3:30pm]") {
		t.Errorf("Expected timestamp in encoded message, got: %s", encoded)
	}
}

func TestEncodeConversation(t *testing.T) {
	conv := &Conversation{
		ID:     1,
		ChatID: 1,
		Messages: []Message{
			{
				ID:         1,
				SenderName: "Alice",
				Text:       "Hello",
				Timestamp:  time.Date(2025, 1, 10, 10, 0, 0, 0, time.UTC),
			},
			{
				ID:         2,
				SenderName: "Bob",
				Text:       "Hi there",
				Timestamp:  time.Date(2025, 1, 10, 10, 1, 0, 0, time.UTC),
			},
		},
	}

	opts := DefaultEncodeOptions()
	encoded := EncodeConversation(conv, opts)

	lines := strings.Split(encoded, "\n")
	if len(lines) != 2 {
		t.Errorf("Expected 2 lines, got %d", len(lines))
	}

	if !strings.Contains(lines[0], "Alice: Hello") {
		t.Errorf("First line should contain 'Alice: Hello', got: %s", lines[0])
	}

	if !strings.Contains(lines[1], "Bob: Hi there") {
		t.Errorf("Second line should contain 'Bob: Hi there', got: %s", lines[1])
	}
}

func TestEncodeConversationToFile(t *testing.T) {
	dbPath, conversationID := createTestDB(t)

	outputPath := filepath.Join(t.TempDir(), "output.txt")
	result := EncodeConversationToFile(dbPath, conversationID, outputPath)

	if !result.Success {
		t.Errorf("EncodeConversationToFile failed: %s", result.Error)
	}

	if result.FilePath != outputPath {
		t.Errorf("Expected FilePath %s, got %s", outputPath, result.FilePath)
	}

	if result.MessageCount == 0 {
		t.Error("Expected MessageCount > 0")
	}

	// Check file exists
	content, err := os.ReadFile(outputPath)
	if err != nil {
		t.Errorf("Failed to read output file: %v", err)
	}

	if len(content) == 0 {
		t.Error("Output file is empty")
	}

	// Verify content contains expected text
	contentStr := string(content)
	if !strings.Contains(contentStr, "Hello Bob!") {
		t.Errorf("Expected content to contain 'Hello Bob!', got: %s", contentStr)
	}
}

func TestEncodeConversationToString(t *testing.T) {
	dbPath, conversationID := createTestDB(t)

	result := EncodeConversationToString(dbPath, conversationID)

	if !result.Success {
		t.Errorf("EncodeConversationToString failed: %s", result.Error)
	}

	if result.EncodedText == "" {
		t.Error("Expected non-empty EncodedText")
	}

	if result.MessageCount == 0 {
		t.Error("Expected MessageCount > 0")
	}

	// Verify content
	if !strings.Contains(result.EncodedText, "Hello Bob!") {
		t.Errorf("Expected encoded text to contain 'Hello Bob!', got: %s", result.EncodedText)
	}
}

func TestReactionTypeToEmoji(t *testing.T) {
	tests := []struct {
		reactionType int
		expected     string
	}{
		{2000, "❤️"},
		{2001, "👍"},
		{2002, "👎"},
		{2003, "😂"},
		{2004, "‼️"},
		{2005, "❓"},
		{9999, ""}, // Unknown reaction
	}

	for _, tt := range tests {
		t.Run(string(rune(tt.reactionType)), func(t *testing.T) {
			result := reactionTypeToEmoji(tt.reactionType)
			if result != tt.expected {
				t.Errorf("reactionTypeToEmoji(%d) = %s, want %s", tt.reactionType, result, tt.expected)
			}
		})
	}
}
