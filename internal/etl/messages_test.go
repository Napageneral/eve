package etl

import (
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// createTestChatDBWithMessages creates a chat.db with test messages
func createTestChatDBWithMessages(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test chat.db: %v", err)
	}
	defer db.Close()

	// Create minimal chat.db schema
	schema := `
		CREATE TABLE handle (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			id TEXT NOT NULL
		);

		CREATE TABLE chat (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_identifier TEXT NOT NULL,
			display_name TEXT,
			service_name TEXT,
			style INTEGER
		);

		CREATE TABLE message (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			guid TEXT UNIQUE NOT NULL,
			text TEXT,
			attributedBody BLOB,
			handle_id INTEGER,
			date INTEGER,
			is_from_me INTEGER DEFAULT 0,
			type INTEGER DEFAULT 0,
			service TEXT,
			associated_message_guid TEXT,
			reply_to_guid TEXT
		);

		CREATE TABLE chat_message_join (
			chat_id INTEGER NOT NULL,
			message_id INTEGER NOT NULL,
			message_date INTEGER DEFAULT 0,
			PRIMARY KEY (chat_id, message_id)
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Insert test handles
	handles := []struct {
		id string
	}{
		{"+15551234567"},
		{"alice@example.com"},
		{"+15559876543"},
	}

	for _, h := range handles {
		_, err := db.Exec("INSERT INTO handle (id) VALUES (?)", h.id)
		if err != nil {
			t.Fatalf("Failed to insert handle: %v", err)
		}
	}

	// Insert test chats
	chats := []struct {
		identifier string
		name       string
		service    string
		style      int
	}{
		{"chat001", "Alice", "iMessage", 45},
		{"chat002", "Team Chat", "iMessage", 43},
	}

	for _, c := range chats {
		_, err := db.Exec(
			"INSERT INTO chat (chat_identifier, display_name, service_name, style) VALUES (?, ?, ?, ?)",
			c.identifier, c.name, c.service, c.style,
		)
		if err != nil {
			t.Fatalf("Failed to insert chat: %v", err)
		}
	}

	// Calculate Apple timestamps (nanoseconds since 2001-01-01)
	appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
	baseTime := time.Date(2024, 1, 1, 12, 0, 0, 0, time.UTC)
	toAppleNano := func(t time.Time) int64 {
		return t.Sub(appleEpoch).Nanoseconds()
	}

	// Insert test messages
	messages := []struct {
		guid                  string
		text                  string
		attributedBody        []byte
		handleID              *int64
		date                  int64
		isFromMe              int
		msgType               int
		service               string
		associatedMessageGUID *string
		replyToGUID           *string
		chatID                int64
	}{
		{
			guid:     "msg-001",
			text:     "Hello, how are you?",
			// attributedBody is present in real chat.db but omitted here for simplicity
			handleID: ptr(int64(1)),
			date:     toAppleNano(baseTime),
			isFromMe: 0,
			msgType:  0,
			service:  "iMessage",
			chatID:   1,
		},
		{
			guid:     "msg-002",
			text:     "I'm doing great, thanks!",
			// is_from_me, so no handle_id
			handleID: nil, // is_from_me, so no handle_id
			date:     toAppleNano(baseTime.Add(1 * time.Minute)),
			isFromMe: 1,
			msgType:  0,
			service:  "iMessage",
			chatID:   1,
		},
		{
			guid:     "msg-003",
			text:     "Team meeting at 3pm",
			handleID: ptr(int64(2)),
			date:     toAppleNano(baseTime.Add(2 * time.Hour)),
			isFromMe: 0,
			msgType:  0,
			service:  "iMessage",
			chatID:   2,
		},
		{
			guid:        "msg-004",
			text:        "Sounds good!",
			handleID:    ptr(int64(3)),
			date:        toAppleNano(baseTime.Add(2*time.Hour + 5*time.Minute)),
			isFromMe:    0,
			msgType:     0,
			service:     "iMessage",
			replyToGUID: ptr("msg-003"),
			chatID:      2,
		},
		{
			guid:     "msg-005",
			text:     "", // Empty message (e.g., reaction or attachment only)
			// No attributedBody => should remain empty after ETL
			handleID: ptr(int64(1)),
			date:     toAppleNano(baseTime.Add(3 * time.Hour)),
			isFromMe: 0,
			msgType:  2, // Non-standard type
			service:  "SMS",
			chatID:   1,
		},
		{
			guid: "msg-006",
			text: "", // text missing
			// attributedBody contains a synthetic typedstream-ish payload that our decoder should extract
			attributedBody: []byte("NSStringABCDEFHello from attributedBody123456789012NSDictionary...NSNumber"),
			handleID:       ptr(int64(1)),
			date:           toAppleNano(baseTime.Add(4 * time.Hour)),
			isFromMe:       0,
			msgType:        0,
			service:        "iMessage",
			chatID:         1,
		},
	}

	for _, m := range messages {
		handleID := sql.NullInt64{Valid: m.handleID != nil}
		if m.handleID != nil {
			handleID.Int64 = *m.handleID
		}

		associatedGUID := sql.NullString{Valid: m.associatedMessageGUID != nil}
		if m.associatedMessageGUID != nil {
			associatedGUID.String = *m.associatedMessageGUID
		}

		replyGUID := sql.NullString{Valid: m.replyToGUID != nil}
		if m.replyToGUID != nil {
			replyGUID.String = *m.replyToGUID
		}

		result, err := db.Exec(
			`INSERT INTO message (guid, text, attributedBody, handle_id, date, is_from_me, type, service, associated_message_guid, reply_to_guid)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			m.guid, m.text, m.attributedBody, handleID, m.date, m.isFromMe, m.msgType, m.service, associatedGUID, replyGUID,
		)
		if err != nil {
			t.Fatalf("Failed to insert message: %v", err)
		}

		messageID, _ := result.LastInsertId()

		// Insert into chat_message_join
		_, err = db.Exec(
			"INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (?, ?, ?)",
			m.chatID, messageID, m.date,
		)
		if err != nil {
			t.Fatalf("Failed to insert chat_message_join: %v", err)
		}
	}

	return dbPath
}

// createTestWarehouseDBWithMessages creates an eve.db with the messages schema
func createTestWarehouseDBWithMessages(t *testing.T) *sql.DB {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test eve.db: %v", err)
	}

	// Create schema with all required tables
	schema := `
		CREATE TABLE contacts (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT,
			nickname TEXT,
			avatar BLOB,
			last_updated TIMESTAMP,
			data_source TEXT,
			is_me BOOLEAN DEFAULT 0
		);

		CREATE TABLE chats (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_identifier TEXT UNIQUE NOT NULL,
			chat_name TEXT,
			created_date TIMESTAMP,
			last_message_date TIMESTAMP,
			is_group BOOLEAN DEFAULT 0,
			service_name TEXT,
			is_blocked BOOLEAN DEFAULT 0,
			total_messages INTEGER DEFAULT 0 NOT NULL,
			last_embedding_update TIMESTAMP,
			wrapped_in_progress BOOLEAN DEFAULT 0,
			wrapped_done BOOLEAN DEFAULT 0
		);

		CREATE TABLE messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_id INTEGER NOT NULL,
			sender_id INTEGER,
			content TEXT,
			timestamp TIMESTAMP NOT NULL,
			is_from_me BOOLEAN DEFAULT 0,
			message_type INTEGER,
			service_name TEXT,
			guid TEXT UNIQUE NOT NULL,
			associated_message_guid TEXT,
			reply_to_guid TEXT,
			conversation_id INTEGER,
			FOREIGN KEY (chat_id) REFERENCES chats(id),
			FOREIGN KEY (sender_id) REFERENCES contacts(id)
		);

		CREATE INDEX idx_messages_chat_id ON messages(chat_id);
		CREATE INDEX idx_messages_sender_id ON messages(sender_id);
		CREATE INDEX idx_messages_timestamp ON messages(timestamp);
		CREATE INDEX idx_messages_guid ON messages(guid);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create warehouse schema: %v", err)
	}

	// Seed chats so SyncMessages can map chat_identifier -> warehouse chat id.
	_, err = db.Exec(`
		INSERT INTO chats (id, chat_identifier, chat_name, is_group, service_name, created_date)
		VALUES
			(1, 'chat001', 'Alice', 0, 'iMessage', CURRENT_TIMESTAMP),
			(2, 'chat002', 'Team Chat', 1, 'iMessage', CURRENT_TIMESTAMP)
	`)
	if err != nil {
		t.Fatalf("Failed to seed warehouse chats: %v", err)
	}

	return db
}

func ptr[T any](v T) *T {
	return &v
}

func TestSyncMessages(t *testing.T) {
	chatDBPath := createTestChatDBWithMessages(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithMessages(t)
	defer warehouseDB.Close()

	// Sync messages (no watermark)
	count, err := SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync messages: %v", err)
	}

	if count != 6 {
		t.Errorf("Expected 6 messages synced, got %d", count)
	}

	// Verify messages table
	var msgCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&msgCount)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if msgCount != 6 {
		t.Errorf("Expected 6 messages, got %d", msgCount)
	}
}

func TestSyncMessages_Idempotent(t *testing.T) {
	chatDBPath := createTestChatDBWithMessages(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithMessages(t)
	defer warehouseDB.Close()

	// Sync messages twice
	count1, err := SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync messages (first): %v", err)
	}

	count2, err := SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync messages (second): %v", err)
	}

	if count1 != count2 {
		t.Errorf("Expected same count on second sync, got %d vs %d", count1, count2)
	}

	// Verify no duplicates
	var msgCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&msgCount)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if msgCount != 6 {
		t.Errorf("Expected 6 messages after idempotent sync, got %d", msgCount)
	}

	// Verify UNIQUE constraint on guid works
	var guidCount int
	err = warehouseDB.QueryRow("SELECT COUNT(DISTINCT guid) FROM messages").Scan(&guidCount)
	if err != nil {
		t.Fatalf("Failed to count unique guids: %v", err)
	}

	if guidCount != 6 {
		t.Errorf("Expected 6 unique guids, got %d", guidCount)
	}
}

func TestSyncMessages_IncrementalSync(t *testing.T) {
	chatDBPath := createTestChatDBWithMessages(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithMessages(t)
	defer warehouseDB.Close()

	// First sync: only messages with ROWID > 2
	count, err := SyncMessages(chatDB, warehouseDB, 2)
	if err != nil {
		t.Fatalf("Failed to sync messages with watermark: %v", err)
	}

	if count != 4 {
		t.Errorf("Expected 4 messages synced (ROWID > 2), got %d", count)
	}

	// Verify only 3 messages in warehouse
	var msgCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&msgCount)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if msgCount != 4 {
		t.Errorf("Expected 4 messages, got %d", msgCount)
	}

	// Second sync: all messages
	count, err = SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync all messages: %v", err)
	}

	if count != 6 {
		t.Errorf("Expected 6 messages synced, got %d", count)
	}

	// Verify all 5 messages now in warehouse
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&msgCount)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if msgCount != 6 {
		t.Errorf("Expected 6 messages after full sync, got %d", msgCount)
	}
}

func TestSyncMessages_AppleTimestamp(t *testing.T) {
	chatDBPath := createTestChatDBWithMessages(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithMessages(t)
	defer warehouseDB.Close()

	// Sync messages
	_, err = SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync messages: %v", err)
	}

	// Verify timestamp conversion
	// Expected: 2024-01-01 12:00:00 UTC
	var timestamp time.Time
	query := "SELECT timestamp FROM messages WHERE guid = 'msg-001'"
	err = warehouseDB.QueryRow(query).Scan(&timestamp)
	if err != nil {
		t.Fatalf("Failed to query timestamp: %v", err)
	}

	expected := time.Date(2024, 1, 1, 12, 0, 0, 0, time.UTC)
	if !timestamp.Equal(expected) {
		t.Errorf("Expected timestamp %v, got %v", expected, timestamp)
	}
}

func TestSyncMessages_ForeignKeyMapping(t *testing.T) {
	chatDBPath := createTestChatDBWithMessages(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithMessages(t)
	defer warehouseDB.Close()

	// Sync messages
	_, err = SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync messages: %v", err)
	}

	// Verify chat_id mapping
	var chatID int64
	query := "SELECT chat_id FROM messages WHERE guid = 'msg-001'"
	err = warehouseDB.QueryRow(query).Scan(&chatID)
	if err != nil {
		t.Fatalf("Failed to query chat_id: %v", err)
	}

	if chatID != 1 {
		t.Errorf("Expected chat_id 1, got %d", chatID)
	}

	// Verify sender_id mapping (handle_id → contact_id)
	var senderID sql.NullInt64
	query = "SELECT sender_id FROM messages WHERE guid = 'msg-001'"
	err = warehouseDB.QueryRow(query).Scan(&senderID)
	if err != nil {
		t.Fatalf("Failed to query sender_id: %v", err)
	}

	if !senderID.Valid || senderID.Int64 != 1 {
		t.Errorf("Expected sender_id 1, got Valid=%v Int64=%d", senderID.Valid, senderID.Int64)
	}

	// Verify is_from_me messages have no sender_id
	query = "SELECT sender_id FROM messages WHERE guid = 'msg-002'"
	err = warehouseDB.QueryRow(query).Scan(&senderID)
	if err != nil {
		t.Fatalf("Failed to query sender_id for is_from_me: %v", err)
	}

	if senderID.Valid {
		t.Errorf("Expected NULL sender_id for is_from_me message, got %d", senderID.Int64)
	}
}

func TestSyncMessages_NullableFields(t *testing.T) {
	chatDBPath := createTestChatDBWithMessages(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithMessages(t)
	defer warehouseDB.Close()

	// Sync messages
	_, err = SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync messages: %v", err)
	}

	// Verify empty content
	var content string
	query := "SELECT content FROM messages WHERE guid = 'msg-005'"
	err = warehouseDB.QueryRow(query).Scan(&content)
	if err != nil {
		t.Fatalf("Failed to query content: %v", err)
	}

	if content != "" {
		t.Errorf("Expected empty content, got %q", content)
	}

	// Verify attributedBody decoding
	query = "SELECT content FROM messages WHERE guid = 'msg-006'"
	err = warehouseDB.QueryRow(query).Scan(&content)
	if err != nil {
		t.Fatalf("Failed to query content for attributedBody message: %v", err)
	}
	if content != "Hello from attributedBody" {
		t.Errorf("Expected decoded content, got %q", content)
	}

	// Verify reply_to_guid
	var replyToGUID sql.NullString
	query = "SELECT reply_to_guid FROM messages WHERE guid = 'msg-004'"
	err = warehouseDB.QueryRow(query).Scan(&replyToGUID)
	if err != nil {
		t.Fatalf("Failed to query reply_to_guid: %v", err)
	}

	if !replyToGUID.Valid || replyToGUID.String != "msg-003" {
		t.Errorf("Expected reply_to_guid 'msg-003', got Valid=%v String=%q", replyToGUID.Valid, replyToGUID.String)
	}
}

func TestSyncMessages_Empty(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "empty_chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create empty chat.db: %v", err)
	}

	// Create schema but no data
	schema := `
		CREATE TABLE message (
			ROWID INTEGER PRIMARY KEY,
			guid TEXT UNIQUE NOT NULL,
			text TEXT,
			attributedBody BLOB,
			handle_id INTEGER,
			date INTEGER,
			is_from_me INTEGER,
			type INTEGER,
			service TEXT,
			associated_message_guid TEXT,
			reply_to_guid TEXT
		);
		CREATE TABLE chat_message_join (
			chat_id INTEGER,
			message_id INTEGER,
			message_date INTEGER,
			PRIMARY KEY (chat_id, message_id)
		);

		CREATE TABLE chat (
			ROWID INTEGER PRIMARY KEY,
			chat_identifier TEXT NOT NULL
		);
	`
	_, err = db.Exec(schema)
	if err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}
	db.Close()

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithMessages(t)
	defer warehouseDB.Close()

	// Sync empty messages
	count, err := SyncMessages(chatDB, warehouseDB, 0)
	if err != nil {
		t.Fatalf("Failed to sync empty messages: %v", err)
	}

	if count != 0 {
		t.Errorf("Expected 0 messages synced, got %d", count)
	}

	// Verify no messages created
	var msgCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&msgCount)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if msgCount != 0 {
		t.Errorf("Expected 0 messages, got %d", msgCount)
	}
}

func TestGetMessages(t *testing.T) {
	chatDBPath := createTestChatDBWithMessages(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	// Get all messages
	messages, err := chatDB.GetMessages(0)
	if err != nil {
		t.Fatalf("Failed to get messages: %v", err)
	}

	if len(messages) != 6 {
		t.Errorf("Expected 6 messages, got %d", len(messages))
	}

	// Verify ROWID sequence
	for i, msg := range messages {
		expectedROWID := int64(i + 1)
		if msg.ROWID != expectedROWID {
			t.Errorf("Expected ROWID %d, got %d", expectedROWID, msg.ROWID)
		}

		if msg.GUID == "" {
			t.Errorf("Expected non-empty GUID for message %d", msg.ROWID)
		}

		if msg.ChatID == 0 {
			t.Errorf("Expected non-zero chat_id for message %d", msg.ROWID)
		}
	}

	// Verify is_from_me values
	fromMeCount := 0
	for _, msg := range messages {
		if msg.IsFromMe {
			fromMeCount++
		}
	}

	if fromMeCount != 1 {
		t.Errorf("Expected 1 is_from_me message, got %d", fromMeCount)
	}
}
