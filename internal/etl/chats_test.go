package etl

import (
	"database/sql"
	"path/filepath"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

// createTestChatDBWithChats creates a chat.db with test chats
func createTestChatDBWithChats(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test chat.db: %v", err)
	}
	defer db.Close()

	// Create minimal chat table
	schema := `
		CREATE TABLE chat (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_identifier TEXT NOT NULL,
			display_name TEXT,
			service_name TEXT,
			style INTEGER
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Insert test chats: mix of 1:1 and group chats
	testChats := []struct {
		identifier  string
		displayName string
		service     string
		style       int
	}{
		{"chat123456", "John Doe", "iMessage", 45},     // 1:1 chat
		{"chat789012", "Team Alpha", "iMessage", 43},   // Group chat
		{"chat345678", "Jane Smith", "SMS", 45},        // SMS 1:1
		{"chat901234", "Project Team", "iMessage", 43}, // Group chat
		{"chat567890", "", "iMessage", 45},             // 1:1 with no display name
	}

	for _, chat := range testChats {
		_, err := db.Exec(
			"INSERT INTO chat (chat_identifier, display_name, service_name, style) VALUES (?, ?, ?, ?)",
			chat.identifier, chat.displayName, chat.service, chat.style,
		)
		if err != nil {
			t.Fatalf("Failed to insert test chat: %v", err)
		}
	}

	return dbPath
}

// createTestWarehouseDBWithChats creates an eve.db with the chats schema
func createTestWarehouseDBWithChats(t *testing.T) *sql.DB {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test eve.db: %v", err)
	}

	// Create chats schema
	schema := `
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

		CREATE INDEX idx_chats_identifier ON chats(chat_identifier);
		CREATE INDEX idx_chats_created_date ON chats(created_date);
		CREATE INDEX idx_chats_last_message_date ON chats(last_message_date);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create warehouse schema: %v", err)
	}

	return db
}

func TestSyncChats(t *testing.T) {
	chatDBPath := createTestChatDBWithChats(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithChats(t)
	defer warehouseDB.Close()

	// Sync chats
	count, err := SyncChats(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync chats: %v", err)
	}

	if count != 5 {
		t.Errorf("Expected 5 chats synced, got %d", count)
	}

	// Verify chats table
	var chatCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM chats").Scan(&chatCount)
	if err != nil {
		t.Fatalf("Failed to count chats: %v", err)
	}

	if chatCount != 5 {
		t.Errorf("Expected 5 chats, got %d", chatCount)
	}

	// Verify group vs 1:1 chats
	var groupCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM chats WHERE is_group = 1").Scan(&groupCount)
	if err != nil {
		t.Fatalf("Failed to count group chats: %v", err)
	}

	if groupCount != 2 {
		t.Errorf("Expected 2 group chats, got %d", groupCount)
	}

	var oneOnOneCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM chats WHERE is_group = 0").Scan(&oneOnOneCount)
	if err != nil {
		t.Fatalf("Failed to count 1:1 chats: %v", err)
	}

	if oneOnOneCount != 3 {
		t.Errorf("Expected 3 1:1 chats, got %d", oneOnOneCount)
	}

	// Verify chat_identifier uniqueness
	var uniqueIdentifierCount int
	err = warehouseDB.QueryRow("SELECT COUNT(DISTINCT chat_identifier) FROM chats").Scan(&uniqueIdentifierCount)
	if err != nil {
		t.Fatalf("Failed to count unique identifiers: %v", err)
	}

	if uniqueIdentifierCount != 5 {
		t.Errorf("Expected 5 unique identifiers, got %d", uniqueIdentifierCount)
	}
}

func TestSyncChats_Idempotent(t *testing.T) {
	chatDBPath := createTestChatDBWithChats(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithChats(t)
	defer warehouseDB.Close()

	// Sync chats twice
	count1, err := SyncChats(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync chats (first): %v", err)
	}

	count2, err := SyncChats(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync chats (second): %v", err)
	}

	if count1 != count2 {
		t.Errorf("Expected same count on second sync, got %d vs %d", count1, count2)
	}

	// Verify no duplicates
	var chatCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM chats").Scan(&chatCount)
	if err != nil {
		t.Fatalf("Failed to count chats: %v", err)
	}

	if chatCount != 5 {
		t.Errorf("Expected 5 chats after idempotent sync, got %d", chatCount)
	}

	// Verify UNIQUE constraint on chat_identifier works
	var identifierCount int
	err = warehouseDB.QueryRow("SELECT COUNT(DISTINCT chat_identifier) FROM chats").Scan(&identifierCount)
	if err != nil {
		t.Fatalf("Failed to count identifiers: %v", err)
	}

	if identifierCount != 5 {
		t.Errorf("Expected 5 unique identifiers, got %d", identifierCount)
	}
}

func TestSyncChats_Empty(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "empty_chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create empty chat.db: %v", err)
	}

	// Create schema but no data
	_, err = db.Exec("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT, service_name TEXT, style INTEGER)")
	if err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}
	db.Close()

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithChats(t)
	defer warehouseDB.Close()

	// Sync empty chats
	count, err := SyncChats(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync empty chats: %v", err)
	}

	if count != 0 {
		t.Errorf("Expected 0 chats synced, got %d", count)
	}

	// Verify no chats created
	var chatCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM chats").Scan(&chatCount)
	if err != nil {
		t.Fatalf("Failed to count chats: %v", err)
	}

	if chatCount != 0 {
		t.Errorf("Expected 0 chats, got %d", chatCount)
	}
}

func TestGetChats(t *testing.T) {
	chatDBPath := createTestChatDBWithChats(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	chats, err := chatDB.GetChats()
	if err != nil {
		t.Fatalf("Failed to get chats: %v", err)
	}

	if len(chats) != 5 {
		t.Errorf("Expected 5 chats, got %d", len(chats))
	}

	// Verify ROWID sequence
	for i, chat := range chats {
		expectedROWID := int64(i + 1)
		if chat.ROWID != expectedROWID {
			t.Errorf("Expected ROWID %d, got %d", expectedROWID, chat.ROWID)
		}

		if chat.ChatIdentifier == "" {
			t.Errorf("Expected non-empty chat_identifier for chat %d", chat.ROWID)
		}
	}

	// Verify style values
	groupCount := 0
	oneOnOneCount := 0
	for _, chat := range chats {
		if chat.Style == 43 {
			groupCount++
		} else if chat.Style == 45 {
			oneOnOneCount++
		}
	}

	if groupCount != 2 {
		t.Errorf("Expected 2 group chats (style=43), got %d", groupCount)
	}

	if oneOnOneCount != 3 {
		t.Errorf("Expected 3 1:1 chats (style=45), got %d", oneOnOneCount)
	}
}

func TestInsertChat_ChatIDMapping(t *testing.T) {
	chatDBPath := createTestChatDBWithChats(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithChats(t)
	defer warehouseDB.Close()

	// Sync chats
	_, err = SyncChats(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync chats: %v", err)
	}

	// Verify that chats.id matches chat.ROWID
	// This is critical for foreign key references in messages
	chats, err := chatDB.GetChats()
	if err != nil {
		t.Fatalf("Failed to get chats: %v", err)
	}

	for _, chat := range chats {
		var chatID int64
		var chatIdentifier string
		var isGroup bool
		query := "SELECT id, chat_identifier, is_group FROM chats WHERE id = ?"
		err := warehouseDB.QueryRow(query, chat.ROWID).Scan(&chatID, &chatIdentifier, &isGroup)
		if err != nil {
			t.Fatalf("Failed to find chat for ROWID %d: %v", chat.ROWID, err)
		}

		if chatID != chat.ROWID {
			t.Errorf("Chat ID mismatch: chat.db ROWID %d, eve.db ID %d", chat.ROWID, chatID)
		}

		if chatIdentifier != chat.ChatIdentifier {
			t.Errorf("Chat identifier mismatch: expected %q, got %q", chat.ChatIdentifier, chatIdentifier)
		}

		expectedIsGroup := chat.Style == 43
		if isGroup != expectedIsGroup {
			t.Errorf("is_group mismatch for chat %d: expected %v, got %v", chat.ROWID, expectedIsGroup, isGroup)
		}
	}
}

func TestInsertChat_NullableFields(t *testing.T) {
	chatDBPath := createTestChatDBWithChats(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithChats(t)
	defer warehouseDB.Close()

	// Sync chats
	_, err = SyncChats(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync chats: %v", err)
	}

	// Verify that empty display_name is handled correctly
	// chat567890 has empty display_name
	var chatName sql.NullString
	query := "SELECT chat_name FROM chats WHERE chat_identifier = 'chat567890'"
	err = warehouseDB.QueryRow(query).Scan(&chatName)
	if err != nil {
		t.Fatalf("Failed to query chat with empty display_name: %v", err)
	}

	// Empty string should be stored as empty string (not NULL)
	if !chatName.Valid || chatName.String != "" {
		t.Errorf("Expected empty string for chat_name, got Valid=%v, String=%q", chatName.Valid, chatName.String)
	}
}
