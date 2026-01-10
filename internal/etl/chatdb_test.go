package etl

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// createTestChatDB creates a minimal chat.db for testing
func createTestChatDB(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test chat.db: %v", err)
	}
	defer db.Close()

	// Create minimal schema matching macOS Messages DB
	schema := `
		CREATE TABLE message (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			guid TEXT UNIQUE NOT NULL,
			text TEXT,
			handle_id INTEGER,
			date INTEGER,
			is_from_me INTEGER DEFAULT 0
		);

		CREATE TABLE chat (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			guid TEXT UNIQUE NOT NULL,
			chat_identifier TEXT
		);

		CREATE TABLE handle (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			id TEXT UNIQUE NOT NULL
		);

		CREATE TABLE chat_message_join (
			chat_id INTEGER,
			message_id INTEGER,
			PRIMARY KEY (chat_id, message_id)
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Insert test data
	// Apple timestamp: nanoseconds since 2001-01-01
	appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
	now := time.Now()
	timestamp := now.Sub(appleEpoch).Nanoseconds()

	// Insert some messages
	for i := 1; i <= 5; i++ {
		_, err := db.Exec(
			"INSERT INTO message (guid, text, date, is_from_me) VALUES (?, ?, ?, ?)",
			"test-guid-"+string(rune(i)),
			"Test message "+string(rune(i)),
			timestamp+(int64(i)*1000000000), // Add seconds
			i%2,
		)
		if err != nil {
			t.Fatalf("Failed to insert test message: %v", err)
		}
	}

	// Insert test chats
	_, err = db.Exec("INSERT INTO chat (guid, chat_identifier) VALUES ('chat-1', 'chat:123456')")
	if err != nil {
		t.Fatalf("Failed to insert test chat: %v", err)
	}

	// Insert test handles
	_, err = db.Exec("INSERT INTO handle (id) VALUES ('+1234567890')")
	if err != nil {
		t.Fatalf("Failed to insert test handle: %v", err)
	}

	return dbPath
}

func TestOpenChatDB(t *testing.T) {
	dbPath := createTestChatDB(t)

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	if chatDB.db == nil {
		t.Fatal("Expected non-nil database connection")
	}
}

func TestOpenChatDB_NotFound(t *testing.T) {
	_, err := OpenChatDB("/nonexistent/path/chat.db")
	if err == nil {
		t.Fatal("Expected error when opening nonexistent chat.db")
	}
}

func TestCountMessages(t *testing.T) {
	dbPath := createTestChatDB(t)

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	count, err := chatDB.CountMessages(0)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if count.TotalMessages != 5 {
		t.Errorf("Expected 5 messages, got %d", count.TotalMessages)
	}

	if count.MaxRowID != 5 {
		t.Errorf("Expected max ROWID 5, got %d", count.MaxRowID)
	}

	if count.OldestDate.IsZero() {
		t.Error("Expected non-zero oldest date")
	}

	if count.NewestDate.IsZero() {
		t.Error("Expected non-zero newest date")
	}

	if !count.NewestDate.After(count.OldestDate) {
		t.Error("Expected newest date to be after oldest date")
	}
}

func TestCountMessages_WithSinceRowID(t *testing.T) {
	dbPath := createTestChatDB(t)

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	// Count messages with ROWID > 2
	count, err := chatDB.CountMessages(2)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if count.TotalMessages != 3 {
		t.Errorf("Expected 3 messages (ROWID > 2), got %d", count.TotalMessages)
	}

	if count.MaxRowID != 5 {
		t.Errorf("Expected max ROWID 5, got %d", count.MaxRowID)
	}
}

func TestCountMessages_Empty(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "empty_chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create empty chat.db: %v", err)
	}

	// Create schema but no data
	_, err = db.Exec("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER)")
	if err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}
	db.Close()

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	count, err := chatDB.CountMessages(0)
	if err != nil {
		t.Fatalf("Failed to count messages: %v", err)
	}

	if count.TotalMessages != 0 {
		t.Errorf("Expected 0 messages, got %d", count.TotalMessages)
	}

	if count.MaxRowID != 0 {
		t.Errorf("Expected max ROWID 0, got %d", count.MaxRowID)
	}
}

func TestGetChatCount(t *testing.T) {
	dbPath := createTestChatDB(t)

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	count, err := chatDB.GetChatCount()
	if err != nil {
		t.Fatalf("Failed to get chat count: %v", err)
	}

	if count != 1 {
		t.Errorf("Expected 1 chat, got %d", count)
	}
}

func TestGetHandleCount(t *testing.T) {
	dbPath := createTestChatDB(t)

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	count, err := chatDB.GetHandleCount()
	if err != nil {
		t.Fatalf("Failed to get handle count: %v", err)
	}

	if count != 1 {
		t.Errorf("Expected 1 handle, got %d", count)
	}
}

func TestGetChatDBPath(t *testing.T) {
	// Test default path
	os.Unsetenv("EVE_SOURCE_CHAT_DB")
	os.Unsetenv("CHATSTATS_SOURCE_CHAT_DB")

	path := GetChatDBPath()
	if path == "" {
		t.Error("Expected non-empty default path")
	}

	// Should contain Messages/chat.db
	if !filepath.IsAbs(path) {
		t.Error("Expected absolute path")
	}

	// Test env override
	testPath := "/test/path/chat.db"
	os.Setenv("EVE_SOURCE_CHAT_DB", testPath)
	defer os.Unsetenv("EVE_SOURCE_CHAT_DB")

	path = GetChatDBPath()
	if path != testPath {
		t.Errorf("Expected %s, got %s", testPath, path)
	}
}
