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

func createTestDB(t *testing.T) (string, int) {
	t.Helper()

	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("failed to create test database: %v", err)
	}
	defer db.Close()

	// Create eve.db schema
	_, err = db.Exec(`
		CREATE TABLE contacts (
			id INTEGER PRIMARY KEY,
			name TEXT,
			is_me INTEGER DEFAULT 0
		);

		CREATE TABLE chats (
			id INTEGER PRIMARY KEY,
			chat_name TEXT,
			is_group INTEGER DEFAULT 0
		);

		CREATE TABLE conversations (
			id INTEGER PRIMARY KEY,
			chat_id INTEGER,
			start_time TEXT,
			end_time TEXT,
			summary TEXT
		);

		CREATE TABLE messages (
			id INTEGER PRIMARY KEY,
			guid TEXT,
			conversation_id INTEGER,
			chat_id INTEGER,
			sender_id INTEGER,
			content TEXT,
			timestamp TEXT
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
			message_id INTEGER,
			original_message_guid TEXT,
			reaction_type INTEGER,
			sender_id INTEGER
		);
	`)
	if err != nil {
		t.Fatalf("failed to create tables: %v", err)
	}

	// Insert test data
	// Contacts
	_, err = db.Exec(`
		INSERT INTO contacts (id, name, is_me) VALUES (1, 'Me', 1);
		INSERT INTO contacts (id, name) VALUES (2, 'Bob');
	`)
	if err != nil {
		t.Fatalf("failed to insert contacts: %v", err)
	}

	// Chat
	_, err = db.Exec(`INSERT INTO chats (id, chat_name) VALUES (1, 'Chat with Bob')`)
	if err != nil {
		t.Fatalf("failed to insert chat: %v", err)
	}

	// Conversation
	result, err := db.Exec(`
		INSERT INTO conversations (id, chat_id, start_time, end_time)
		VALUES (1, 1, '2025-01-10T10:00:00Z', '2025-01-10T10:02:00Z')
	`)
	if err != nil {
		t.Fatalf("failed to insert conversation: %v", err)
	}
	convID, _ := result.LastInsertId()

	// Messages
	_, err = db.Exec(`
		INSERT INTO messages (id, guid, conversation_id, chat_id, sender_id, content, timestamp)
		VALUES (1, 'msg-1', 1, 1, 1, 'Hello Bob!', '2025-01-10T10:00:00Z');
		INSERT INTO messages (id, guid, conversation_id, chat_id, sender_id, content, timestamp)
		VALUES (2, 'msg-2', 1, 1, 2, 'Hi Alice!', '2025-01-10T10:01:00Z');
		INSERT INTO messages (id, guid, conversation_id, chat_id, sender_id, content, timestamp)
		VALUES (3, 'msg-3', 1, 1, 1, 'How are you?', '2025-01-10T10:02:00Z');
	`)
	if err != nil {
		t.Fatalf("failed to insert messages: %v", err)
	}

	return dbPath, int(convID)
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

	if len(conv.Messages) != 3 {
		t.Errorf("Expected 3 messages, got %d", len(conv.Messages))
	}

	// Check first message
	if conv.Messages[0].Text != "Hello Bob!" {
		t.Errorf("Expected first message 'Hello Bob!', got %s", conv.Messages[0].Text)
	}

	// Check sender name (from contacts table)
	if conv.Messages[0].SenderName != "Me" {
		t.Errorf("Expected sender 'Me', got %s", conv.Messages[0].SenderName)
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

	if result.MessageCount != 3 {
		t.Errorf("Expected MessageCount 3, got %d", result.MessageCount)
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

	// Verify sender names are resolved
	if !strings.Contains(contentStr, "Me:") {
		t.Errorf("Expected content to contain 'Me:', got: %s", contentStr)
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
