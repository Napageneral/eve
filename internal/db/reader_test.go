package db

import (
	"database/sql"
	"os"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func setupReaderTestDB(t *testing.T) (*sql.DB, func()) {
	tmpfile, err := os.CreateTemp("", "test-warehouse-*.db")
	if err != nil {
		t.Fatalf("Failed to create temp file: %v", err)
	}
	tmpfile.Close()

	db, err := sql.Open("sqlite3", tmpfile.Name())
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}

	// Create schema
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
			end_time TIMESTAMP NOT NULL,
			FOREIGN KEY (chat_id) REFERENCES chats(id)
		);

		CREATE TABLE messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_id INTEGER NOT NULL,
			sender_id INTEGER,
			content TEXT,
			timestamp TIMESTAMP NOT NULL,
			is_from_me BOOLEAN DEFAULT 0,
			guid TEXT UNIQUE NOT NULL,
			conversation_id INTEGER,
			FOREIGN KEY (conversation_id) REFERENCES conversations(id)
		);

		CREATE TABLE attachments (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			message_id INTEGER NOT NULL,
			mime_type TEXT,
			file_name TEXT,
			is_sticker BOOLEAN DEFAULT 0,
			FOREIGN KEY (message_id) REFERENCES messages(id)
		);

		CREATE TABLE reactions (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			original_message_guid TEXT NOT NULL,
			reaction_type INTEGER,
			sender_id INTEGER,
			is_from_me BOOLEAN DEFAULT 0,
			FOREIGN KEY (original_message_guid) REFERENCES messages(guid)
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

func TestGetConversation_Basic(t *testing.T) {
	db, cleanup := setupReaderTestDB(t)
	defer cleanup()

	// Insert test data
	_, err := db.Exec(`
		INSERT INTO contacts (id, name) VALUES (1, 'Alice'), (2, 'Bob');
		INSERT INTO chats (id, chat_identifier) VALUES (1, 'chat-1');
		INSERT INTO conversations (id, chat_id, start_time, end_time)
		VALUES (1, 1, '2025-10-27 15:00:00', '2025-10-27 16:00:00');
		INSERT INTO messages (id, chat_id, sender_id, content, timestamp, is_from_me, guid, conversation_id)
		VALUES
			(1, 1, 1, 'Hello', '2025-10-27 15:30:00', 0, 'msg-1', 1),
			(2, 1, 2, 'Hi there', '2025-10-27 15:31:00', 0, 'msg-2', 1);
	`)
	if err != nil {
		t.Fatalf("Failed to insert test data: %v", err)
	}

	reader := NewConversationReader(db)
	conv, err := reader.GetConversation(1)
	if err != nil {
		t.Fatalf("Failed to get conversation: %v", err)
	}

	if conv.ID != 1 {
		t.Errorf("Expected ID 1, got %d", conv.ID)
	}
	if conv.ChatID != 1 {
		t.Errorf("Expected ChatID 1, got %d", conv.ChatID)
	}
	if len(conv.Messages) != 2 {
		t.Fatalf("Expected 2 messages, got %d", len(conv.Messages))
	}

	// Check first message
	if conv.Messages[0].Content != "Hello" {
		t.Errorf("Expected content 'Hello', got %q", conv.Messages[0].Content)
	}
	if conv.Messages[0].SenderName != "Alice" {
		t.Errorf("Expected sender 'Alice', got %q", conv.Messages[0].SenderName)
	}

	// Check second message
	if conv.Messages[1].Content != "Hi there" {
		t.Errorf("Expected content 'Hi there', got %q", conv.Messages[1].Content)
	}
	if conv.Messages[1].SenderName != "Bob" {
		t.Errorf("Expected sender 'Bob', got %q", conv.Messages[1].SenderName)
	}
}

func TestGetConversation_WithAttachments(t *testing.T) {
	db, cleanup := setupReaderTestDB(t)
	defer cleanup()

	// Insert test data
	_, err := db.Exec(`
		INSERT INTO contacts (id, name) VALUES (1, 'Alice');
		INSERT INTO chats (id, chat_identifier) VALUES (1, 'chat-1');
		INSERT INTO conversations (id, chat_id, start_time, end_time)
		VALUES (1, 1, '2025-10-27 15:00:00', '2025-10-27 16:00:00');
		INSERT INTO messages (id, chat_id, sender_id, content, timestamp, is_from_me, guid, conversation_id)
		VALUES (1, 1, 1, 'Check this', '2025-10-27 15:30:00', 0, 'msg-1', 1);
		INSERT INTO attachments (id, message_id, mime_type, file_name)
		VALUES
			(1, 1, 'image/png', 'photo.png'),
			(2, 1, 'application/pdf', 'doc.pdf');
	`)
	if err != nil {
		t.Fatalf("Failed to insert test data: %v", err)
	}

	reader := NewConversationReader(db)
	conv, err := reader.GetConversation(1)
	if err != nil {
		t.Fatalf("Failed to get conversation: %v", err)
	}

	if len(conv.Messages) != 1 {
		t.Fatalf("Expected 1 message, got %d", len(conv.Messages))
	}

	msg := conv.Messages[0]
	if len(msg.Attachments) != 2 {
		t.Fatalf("Expected 2 attachments, got %d", len(msg.Attachments))
	}

	if msg.Attachments[0].MimeType != "image/png" {
		t.Errorf("Expected mime_type 'image/png', got %q", msg.Attachments[0].MimeType)
	}
	if msg.Attachments[1].FileName != "doc.pdf" {
		t.Errorf("Expected file_name 'doc.pdf', got %q", msg.Attachments[1].FileName)
	}
}

func TestGetConversation_WithReactions(t *testing.T) {
	db, cleanup := setupReaderTestDB(t)
	defer cleanup()

	// Insert test data
	_, err := db.Exec(`
		INSERT INTO contacts (id, name) VALUES (1, 'Alice'), (2, 'Bob');
		INSERT INTO chats (id, chat_identifier) VALUES (1, 'chat-1');
		INSERT INTO conversations (id, chat_id, start_time, end_time)
		VALUES (1, 1, '2025-10-27 15:00:00', '2025-10-27 16:00:00');
		INSERT INTO messages (id, chat_id, sender_id, content, timestamp, is_from_me, guid, conversation_id)
		VALUES (1, 1, 1, 'Great!', '2025-10-27 15:30:00', 0, 'msg-1', 1);
		INSERT INTO reactions (original_message_guid, reaction_type, sender_id)
		VALUES
			('msg-1', 2000, 2),
			('msg-1', 2001, 2);
	`)
	if err != nil {
		t.Fatalf("Failed to insert test data: %v", err)
	}

	reader := NewConversationReader(db)
	conv, err := reader.GetConversation(1)
	if err != nil {
		t.Fatalf("Failed to get conversation: %v", err)
	}

	if len(conv.Messages) != 1 {
		t.Fatalf("Expected 1 message, got %d", len(conv.Messages))
	}

	msg := conv.Messages[0]
	if len(msg.Reactions) != 2 {
		t.Fatalf("Expected 2 reactions, got %d", len(msg.Reactions))
	}

	// Check reaction types
	reactionTypes := make(map[int]bool)
	for _, r := range msg.Reactions {
		reactionTypes[r.ReactionType] = true
	}

	if !reactionTypes[2000] || !reactionTypes[2001] {
		t.Errorf("Expected reaction types 2000 and 2001, got %v", reactionTypes)
	}
}

func TestParseTimestamp(t *testing.T) {
	tests := []struct {
		input    string
		expected time.Time
		wantErr  bool
	}{
		{
			input:    "2025-10-27 15:30:00",
			expected: time.Date(2025, 10, 27, 15, 30, 0, 0, time.UTC),
			wantErr:  false,
		},
		{
			input:    "2025-10-27T15:30:00",
			expected: time.Date(2025, 10, 27, 15, 30, 0, 0, time.UTC),
			wantErr:  false,
		},
		{
			input:   "invalid",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			result, err := parseTimestamp(tt.input)
			if tt.wantErr {
				if err == nil {
					t.Error("Expected error, got nil")
				}
				return
			}

			if err != nil {
				t.Errorf("Unexpected error: %v", err)
			}

			// Compare year, month, day, hour, minute, second
			if result.Year() != tt.expected.Year() ||
				result.Month() != tt.expected.Month() ||
				result.Day() != tt.expected.Day() ||
				result.Hour() != tt.expected.Hour() ||
				result.Minute() != tt.expected.Minute() ||
				result.Second() != tt.expected.Second() {
				t.Errorf("Expected %v, got %v", tt.expected, result)
			}
		})
	}
}
