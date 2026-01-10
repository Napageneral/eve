package etl

import (
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// createTestWarehouseDBForConversations creates an eve.db with test messages
func createTestWarehouseDBForConversations(t *testing.T) *sql.DB {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test warehouse db: %v", err)
	}

	// Create minimal warehouse schema
	schema := `
		CREATE TABLE chats (
			id INTEGER PRIMARY KEY,
			chat_identifier TEXT UNIQUE NOT NULL,
			chat_name TEXT,
			is_group BOOLEAN DEFAULT 0
		);

		CREATE TABLE contacts (
			id INTEGER PRIMARY KEY,
			name TEXT
		);

		CREATE TABLE conversations (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_id INTEGER NOT NULL,
			initiator_id INTEGER,
			start_time TIMESTAMP NOT NULL,
			end_time TIMESTAMP NOT NULL,
			message_count INTEGER NOT NULL DEFAULT 0,
			gap_threshold INTEGER,
			FOREIGN KEY (chat_id) REFERENCES chats(id),
			FOREIGN KEY (initiator_id) REFERENCES contacts(id)
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
			FOREIGN KEY (chat_id) REFERENCES chats(id),
			FOREIGN KEY (sender_id) REFERENCES contacts(id),
			FOREIGN KEY (conversation_id) REFERENCES conversations(id)
		);

		CREATE INDEX idx_messages_chat_timestamp ON messages(chat_id, timestamp);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	return db
}

// insertTestChat inserts a test chat
func insertTestChat(t *testing.T, db *sql.DB, id int64, identifier string, name string, isGroup bool) {
	_, err := db.Exec(
		"INSERT INTO chats (id, chat_identifier, chat_name, is_group) VALUES (?, ?, ?, ?)",
		id, identifier, name, isGroup,
	)
	if err != nil {
		t.Fatalf("Failed to insert chat: %v", err)
	}
}

// insertTestContact inserts a test contact
func insertTestContact(t *testing.T, db *sql.DB, id int64, name string) {
	_, err := db.Exec(
		"INSERT INTO contacts (id, name) VALUES (?, ?)",
		id, name,
	)
	if err != nil {
		t.Fatalf("Failed to insert contact: %v", err)
	}
}

// insertTestMessage inserts a test message
func insertTestMessage(t *testing.T, db *sql.DB, chatID int64, senderID *int64, timestamp time.Time, guid string, content string) {
	_, err := db.Exec(
		"INSERT INTO messages (chat_id, sender_id, content, timestamp, guid) VALUES (?, ?, ?, ?, ?)",
		chatID, senderID, content, timestamp, guid,
	)
	if err != nil {
		t.Fatalf("Failed to insert message: %v", err)
	}
}

func TestBuildConversations_SingleConversation(t *testing.T) {
	db := createTestWarehouseDBForConversations(t)
	defer db.Close()

	// Setup: one chat, one contact, three messages within 3-hour window
	insertTestChat(t, db, 1, "chat001", "Alice", false)
	insertTestContact(t, db, 1, "Alice")

	baseTime := time.Date(2024, 1, 1, 10, 0, 0, 0, time.UTC)
	sender := int64(1)

	insertTestMessage(t, db, 1, &sender, baseTime, "msg1", "Hello")
	insertTestMessage(t, db, 1, &sender, baseTime.Add(30*time.Minute), "msg2", "How are you?")
	insertTestMessage(t, db, 1, &sender, baseTime.Add(60*time.Minute), "msg3", "Great!")

	// Execute
	count, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("BuildConversations failed: %v", err)
	}

	// Verify
	if count != 1 {
		t.Errorf("Expected 1 conversation, got %d", count)
	}

	// Check conversation record
	var convID int64
	var chatID int64
	var initiatorID sql.NullInt64
	var startTime, endTime time.Time
	var messageCount int

	err = db.QueryRow(`
		SELECT id, chat_id, initiator_id, start_time, end_time, message_count
		FROM conversations
	`).Scan(&convID, &chatID, &initiatorID, &startTime, &endTime, &messageCount)

	if err != nil {
		t.Fatalf("Failed to query conversation: %v", err)
	}

	if chatID != 1 {
		t.Errorf("Expected chat_id 1, got %d", chatID)
	}

	if !initiatorID.Valid || initiatorID.Int64 != 1 {
		t.Errorf("Expected initiator_id 1, got %v", initiatorID)
	}

	if !startTime.Equal(baseTime) {
		t.Errorf("Expected start_time %v, got %v", baseTime, startTime)
	}

	if !endTime.Equal(baseTime.Add(60 * time.Minute)) {
		t.Errorf("Expected end_time %v, got %v", baseTime.Add(60*time.Minute), endTime)
	}

	if messageCount != 3 {
		t.Errorf("Expected message_count 3, got %d", messageCount)
	}

	// Verify all messages are assigned to the conversation
	var assignedCount int
	err = db.QueryRow(`SELECT COUNT(*) FROM messages WHERE conversation_id = ?`, convID).Scan(&assignedCount)
	if err != nil {
		t.Fatalf("Failed to count assigned messages: %v", err)
	}

	if assignedCount != 3 {
		t.Errorf("Expected 3 messages assigned to conversation, got %d", assignedCount)
	}
}

func TestBuildConversations_MultipleConversationsWithGap(t *testing.T) {
	db := createTestWarehouseDBForConversations(t)
	defer db.Close()

	// Setup: one chat, messages with >3 hour gap
	insertTestChat(t, db, 1, "chat001", "Alice", false)
	insertTestContact(t, db, 1, "Alice")

	baseTime := time.Date(2024, 1, 1, 10, 0, 0, 0, time.UTC)
	sender := int64(1)

	// First conversation
	insertTestMessage(t, db, 1, &sender, baseTime, "msg1", "Morning message")
	insertTestMessage(t, db, 1, &sender, baseTime.Add(30*time.Minute), "msg2", "Follow up")

	// Gap of 4 hours (> 3 hour threshold)
	// Second conversation
	insertTestMessage(t, db, 1, &sender, baseTime.Add(4*time.Hour), "msg3", "Afternoon message")
	insertTestMessage(t, db, 1, &sender, baseTime.Add(4*time.Hour+15*time.Minute), "msg4", "Another one")

	// Execute
	count, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("BuildConversations failed: %v", err)
	}

	// Verify
	if count != 2 {
		t.Errorf("Expected 2 conversations, got %d", count)
	}

	// Check first conversation
	var conv1MessageCount int
	err = db.QueryRow(`
		SELECT message_count FROM conversations WHERE start_time = ? ORDER BY start_time
	`, baseTime).Scan(&conv1MessageCount)

	if err != nil {
		t.Fatalf("Failed to query first conversation: %v", err)
	}

	if conv1MessageCount != 2 {
		t.Errorf("Expected first conversation to have 2 messages, got %d", conv1MessageCount)
	}

	// Check second conversation
	var conv2MessageCount int
	err = db.QueryRow(`
		SELECT message_count FROM conversations WHERE start_time = ? ORDER BY start_time
	`, baseTime.Add(4*time.Hour)).Scan(&conv2MessageCount)

	if err != nil {
		t.Fatalf("Failed to query second conversation: %v", err)
	}

	if conv2MessageCount != 2 {
		t.Errorf("Expected second conversation to have 2 messages, got %d", conv2MessageCount)
	}
}

func TestBuildConversations_MultipleChats(t *testing.T) {
	db := createTestWarehouseDBForConversations(t)
	defer db.Close()

	// Setup: two chats, each with their own conversation
	insertTestChat(t, db, 1, "chat001", "Alice", false)
	insertTestChat(t, db, 2, "chat002", "Bob", false)
	insertTestContact(t, db, 1, "Alice")
	insertTestContact(t, db, 2, "Bob")

	baseTime := time.Date(2024, 1, 1, 10, 0, 0, 0, time.UTC)
	sender1 := int64(1)
	sender2 := int64(2)

	// Chat 1 messages
	insertTestMessage(t, db, 1, &sender1, baseTime, "msg1", "Alice message 1")
	insertTestMessage(t, db, 1, &sender1, baseTime.Add(30*time.Minute), "msg2", "Alice message 2")

	// Chat 2 messages
	insertTestMessage(t, db, 2, &sender2, baseTime, "msg3", "Bob message 1")
	insertTestMessage(t, db, 2, &sender2, baseTime.Add(45*time.Minute), "msg4", "Bob message 2")

	// Execute
	count, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("BuildConversations failed: %v", err)
	}

	// Verify
	if count != 2 {
		t.Errorf("Expected 2 conversations (one per chat), got %d", count)
	}

	// Check both chats have conversations
	var chat1Convs, chat2Convs int
	db.QueryRow("SELECT COUNT(*) FROM conversations WHERE chat_id = 1").Scan(&chat1Convs)
	db.QueryRow("SELECT COUNT(*) FROM conversations WHERE chat_id = 2").Scan(&chat2Convs)

	if chat1Convs != 1 {
		t.Errorf("Expected 1 conversation for chat 1, got %d", chat1Convs)
	}

	if chat2Convs != 1 {
		t.Errorf("Expected 1 conversation for chat 2, got %d", chat2Convs)
	}
}

func TestBuildConversations_EmptyDatabase(t *testing.T) {
	db := createTestWarehouseDBForConversations(t)
	defer db.Close()

	// No messages in database
	count, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("BuildConversations failed: %v", err)
	}

	if count != 0 {
		t.Errorf("Expected 0 conversations for empty database, got %d", count)
	}
}

func TestBuildConversations_Idempotent(t *testing.T) {
	db := createTestWarehouseDBForConversations(t)
	defer db.Close()

	// Setup: one chat with messages
	insertTestChat(t, db, 1, "chat001", "Alice", false)
	insertTestContact(t, db, 1, "Alice")

	baseTime := time.Date(2024, 1, 1, 10, 0, 0, 0, time.UTC)
	sender := int64(1)

	insertTestMessage(t, db, 1, &sender, baseTime, "msg1", "Test message")

	// Run once
	count1, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("First BuildConversations failed: %v", err)
	}

	if count1 != 1 {
		t.Errorf("Expected 1 conversation on first run, got %d", count1)
	}

	// Run again (idempotent)
	count2, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("Second BuildConversations failed: %v", err)
	}

	if count2 != 1 {
		t.Errorf("Expected 1 conversation on second run, got %d", count2)
	}

	// Verify only one conversation exists
	var totalConvs int
	db.QueryRow("SELECT COUNT(*) FROM conversations").Scan(&totalConvs)

	if totalConvs != 1 {
		t.Errorf("Expected 1 conversation total after idempotent runs, got %d", totalConvs)
	}
}

func TestBuildConversations_EdgeCase3HourBoundary(t *testing.T) {
	db := createTestWarehouseDBForConversations(t)
	defer db.Close()

	// Setup: messages exactly at and beyond the 3-hour boundary
	insertTestChat(t, db, 1, "chat001", "Alice", false)
	insertTestContact(t, db, 1, "Alice")

	baseTime := time.Date(2024, 1, 1, 10, 0, 0, 0, time.UTC)
	sender := int64(1)

	insertTestMessage(t, db, 1, &sender, baseTime, "msg1", "First message")
	// Exactly 3 hours later (should be same conversation, gap not > 3 hours)
	insertTestMessage(t, db, 1, &sender, baseTime.Add(3*time.Hour), "msg2", "At boundary")
	// Just over 3 hours from msg2 (should be new conversation)
	insertTestMessage(t, db, 1, &sender, baseTime.Add(6*time.Hour+1*time.Second), "msg3", "Just over")

	// Execute
	count, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("BuildConversations failed: %v", err)
	}

	// Verify: first two messages in one conversation (gap = 3 hours), third in new conversation
	if count != 2 {
		t.Errorf("Expected 2 conversations (boundary test), got %d", count)
	}

	// Verify first conversation has 2 messages
	var conv1MessageCount int
	err = db.QueryRow(`
		SELECT message_count FROM conversations WHERE start_time = ? ORDER BY start_time
	`, baseTime).Scan(&conv1MessageCount)

	if err != nil {
		t.Fatalf("Failed to query first conversation: %v", err)
	}

	if conv1MessageCount != 2 {
		t.Errorf("Expected first conversation to have 2 messages, got %d", conv1MessageCount)
	}

	// Verify second conversation has 1 message
	var conv2MessageCount int
	err = db.QueryRow(`
		SELECT message_count FROM conversations WHERE start_time = ? ORDER BY start_time
	`, baseTime.Add(6*time.Hour+1*time.Second)).Scan(&conv2MessageCount)

	if err != nil {
		t.Fatalf("Failed to query second conversation: %v", err)
	}

	if conv2MessageCount != 1 {
		t.Errorf("Expected second conversation to have 1 message, got %d", conv2MessageCount)
	}
}

func TestBuildConversations_NullSenderID(t *testing.T) {
	db := createTestWarehouseDBForConversations(t)
	defer db.Close()

	// Setup: messages with NULL sender_id (is_from_me messages)
	insertTestChat(t, db, 1, "chat001", "Alice", false)

	baseTime := time.Date(2024, 1, 1, 10, 0, 0, 0, time.UTC)

	// Message with NULL sender (is_from_me)
	insertTestMessage(t, db, 1, nil, baseTime, "msg1", "My message")
	insertTestMessage(t, db, 1, nil, baseTime.Add(30*time.Minute), "msg2", "Another from me")

	// Execute
	count, err := BuildConversations(db)
	if err != nil {
		t.Fatalf("BuildConversations failed: %v", err)
	}

	// Verify
	if count != 1 {
		t.Errorf("Expected 1 conversation with NULL sender, got %d", count)
	}

	// Check initiator_id is NULL for is_from_me conversation
	var initiatorID sql.NullInt64
	err = db.QueryRow("SELECT initiator_id FROM conversations").Scan(&initiatorID)
	if err != nil {
		t.Fatalf("Failed to query conversation: %v", err)
	}

	if initiatorID.Valid {
		t.Errorf("Expected NULL initiator_id for is_from_me conversation, got %d", initiatorID.Int64)
	}
}
