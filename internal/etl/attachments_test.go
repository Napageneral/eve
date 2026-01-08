package etl

import (
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// createTestChatDBWithAttachments creates a chat.db with test attachments
func createTestChatDBWithAttachments(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test chat.db: %v", err)
	}
	defer db.Close()

	// Create minimal chat.db schema
	schema := `
		CREATE TABLE message (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			guid TEXT UNIQUE NOT NULL,
			text TEXT,
			handle_id INTEGER,
			date INTEGER,
			is_from_me INTEGER DEFAULT 0,
			type INTEGER DEFAULT 0,
			service TEXT
		);

		CREATE TABLE attachment (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			guid TEXT UNIQUE NOT NULL,
			created_date INTEGER,
			filename TEXT,
			uti TEXT,
			mime_type TEXT,
			total_bytes INTEGER,
			is_sticker INTEGER DEFAULT 0
		);

		CREATE TABLE message_attachment_join (
			message_id INTEGER NOT NULL,
			attachment_id INTEGER NOT NULL,
			PRIMARY KEY (message_id, attachment_id)
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Calculate Apple timestamps (nanoseconds since 2001-01-01)
	appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
	baseTime := time.Date(2024, 6, 15, 14, 30, 0, 0, time.UTC)
	toAppleNano := func(t time.Time) int64 {
		return t.Sub(appleEpoch).Nanoseconds()
	}

	// Insert test messages
	messages := []struct {
		guid string
		text string
		date int64
	}{
		{"msg-001", "Check out this photo", toAppleNano(baseTime)},
		{"msg-002", "Here's the document", toAppleNano(baseTime.Add(1 * time.Hour))},
		{"msg-003", "Nice sticker!", toAppleNano(baseTime.Add(2 * time.Hour))},
		{"msg-004", "Plain text message", toAppleNano(baseTime.Add(3 * time.Hour))},
	}

	for _, m := range messages {
		_, err := db.Exec(
			"INSERT INTO message (guid, text, date, is_from_me, type, service) VALUES (?, ?, ?, 0, 0, 'iMessage')",
			m.guid, m.text, m.date,
		)
		if err != nil {
			t.Fatalf("Failed to insert message: %v", err)
		}
	}

	// Insert test attachments
	attachments := []struct {
		guid        string
		createdDate int64
		filename    string
		uti         string
		mimeType    string
		totalBytes  int64
		isSticker   int
		messageID   int64
	}{
		{
			guid:        "att-001",
			createdDate: toAppleNano(baseTime),
			filename:    "photo.jpg",
			uti:         "public.jpeg",
			mimeType:    "image/jpeg",
			totalBytes:  1024000,
			isSticker:   0,
			messageID:   1,
		},
		{
			guid:        "att-002",
			createdDate: toAppleNano(baseTime.Add(1 * time.Hour)),
			filename:    "document.pdf",
			uti:         "com.adobe.pdf",
			mimeType:    "application/pdf",
			totalBytes:  512000,
			isSticker:   0,
			messageID:   2,
		},
		{
			guid:        "att-003",
			createdDate: toAppleNano(baseTime.Add(2 * time.Hour)),
			filename:    "",
			uti:         "public.sticker",
			mimeType:    "image/png",
			totalBytes:  50000,
			isSticker:   1,
			messageID:   3,
		},
		{
			guid:        "att-004",
			createdDate: toAppleNano(baseTime.Add(3 * time.Hour)),
			filename:    "video.mp4",
			uti:         "public.mpeg-4",
			mimeType:    "video/mp4",
			totalBytes:  5120000,
			isSticker:   0,
			messageID:   1, // Multiple attachments on same message
		},
	}

	for _, a := range attachments {
		result, err := db.Exec(
			`INSERT INTO attachment (guid, created_date, filename, uti, mime_type, total_bytes, is_sticker)
			VALUES (?, ?, ?, ?, ?, ?, ?)`,
			a.guid, a.createdDate, a.filename, a.uti, a.mimeType, a.totalBytes, a.isSticker,
		)
		if err != nil {
			t.Fatalf("Failed to insert attachment: %v", err)
		}

		attachmentID, _ := result.LastInsertId()

		// Insert into message_attachment_join
		_, err = db.Exec(
			"INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
			a.messageID, attachmentID,
		)
		if err != nil {
			t.Fatalf("Failed to insert message_attachment_join: %v", err)
		}
	}

	return dbPath
}

// createTestWarehouseDBWithAttachments creates an eve.db with the attachments schema
func createTestWarehouseDBWithAttachments(t *testing.T) *sql.DB {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test eve.db: %v", err)
	}

	// Create schema with all required tables
	schema := `
		CREATE TABLE messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			chat_id INTEGER NOT NULL,
			sender_id INTEGER,
			content TEXT,
			timestamp TIMESTAMP NOT NULL,
			is_from_me BOOLEAN DEFAULT 0,
			message_type INTEGER,
			service_name TEXT,
			guid TEXT UNIQUE NOT NULL
		);

		CREATE TABLE attachments (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			message_id INTEGER NOT NULL,
			file_name TEXT,
			mime_type TEXT,
			size INTEGER,
			created_date TIMESTAMP,
			is_sticker BOOLEAN DEFAULT 0,
			guid TEXT UNIQUE NOT NULL,
			uti TEXT,
			FOREIGN KEY (message_id) REFERENCES messages(id)
		);

		CREATE INDEX idx_attachments_message_id ON attachments(message_id);
		CREATE INDEX idx_attachments_guid ON attachments(guid);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create warehouse schema: %v", err)
	}

	// Insert test messages (these must exist for FK references)
	baseTime := time.Date(2024, 6, 15, 14, 30, 0, 0, time.UTC)

	messages := []struct {
		guid      string
		content   string
		timestamp time.Time
	}{
		{"msg-001", "Check out this photo", baseTime},
		{"msg-002", "Here's the document", baseTime.Add(1 * time.Hour)},
		{"msg-003", "Nice sticker!", baseTime.Add(2 * time.Hour)},
		{"msg-004", "Plain text message", baseTime.Add(3 * time.Hour)},
	}

	for _, m := range messages {
		_, err := db.Exec(
			`INSERT INTO messages (chat_id, content, timestamp, is_from_me, message_type, service_name, guid)
			VALUES (1, ?, ?, 0, 0, 'iMessage', ?)`,
			m.content, m.timestamp, m.guid,
		)
		if err != nil {
			t.Fatalf("Failed to insert message: %v", err)
		}
	}

	return db
}

func TestSyncAttachments(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync attachments
	count, err := SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments: %v", err)
	}

	if count != 4 {
		t.Errorf("Expected 4 attachments synced, got %d", count)
	}

	// Verify attachments table
	var attCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM attachments").Scan(&attCount)
	if err != nil {
		t.Fatalf("Failed to count attachments: %v", err)
	}

	if attCount != 4 {
		t.Errorf("Expected 4 attachments, got %d", attCount)
	}
}

func TestSyncAttachments_Idempotent(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync attachments twice
	count1, err := SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments (first): %v", err)
	}

	count2, err := SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments (second): %v", err)
	}

	if count1 != count2 {
		t.Errorf("Expected same count on second sync, got %d vs %d", count1, count2)
	}

	// Verify no duplicates
	var attCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM attachments").Scan(&attCount)
	if err != nil {
		t.Fatalf("Failed to count attachments: %v", err)
	}

	if attCount != 4 {
		t.Errorf("Expected 4 attachments after idempotent sync, got %d", attCount)
	}

	// Verify UNIQUE constraint on guid works
	var guidCount int
	err = warehouseDB.QueryRow("SELECT COUNT(DISTINCT guid) FROM attachments").Scan(&guidCount)
	if err != nil {
		t.Fatalf("Failed to count unique guids: %v", err)
	}

	if guidCount != 4 {
		t.Errorf("Expected 4 unique guids, got %d", guidCount)
	}
}

func TestSyncAttachments_AppleTimestamp(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync attachments
	_, err = SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments: %v", err)
	}

	// Verify timestamp conversion
	// Expected: 2024-06-15 14:30:00 UTC
	var createdDate time.Time
	query := "SELECT created_date FROM attachments WHERE guid = 'att-001'"
	err = warehouseDB.QueryRow(query).Scan(&createdDate)
	if err != nil {
		t.Fatalf("Failed to query created_date: %v", err)
	}

	expected := time.Date(2024, 6, 15, 14, 30, 0, 0, time.UTC)
	if !createdDate.Equal(expected) {
		t.Errorf("Expected created_date %v, got %v", expected, createdDate)
	}
}

func TestSyncAttachments_MessageMapping(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync attachments
	_, err = SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments: %v", err)
	}

	// Verify message_id mapping for att-001 (should map to msg-001 → message id 1)
	var messageID int64
	query := "SELECT message_id FROM attachments WHERE guid = 'att-001'"
	err = warehouseDB.QueryRow(query).Scan(&messageID)
	if err != nil {
		t.Fatalf("Failed to query message_id: %v", err)
	}

	if messageID != 1 {
		t.Errorf("Expected message_id 1, got %d", messageID)
	}

	// Verify att-002 maps to msg-002 → message id 2
	query = "SELECT message_id FROM attachments WHERE guid = 'att-002'"
	err = warehouseDB.QueryRow(query).Scan(&messageID)
	if err != nil {
		t.Fatalf("Failed to query message_id for att-002: %v", err)
	}

	if messageID != 2 {
		t.Errorf("Expected message_id 2, got %d", messageID)
	}
}

func TestSyncAttachments_MultipleAttachmentsPerMessage(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync attachments
	_, err = SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments: %v", err)
	}

	// Verify message 1 has 2 attachments (att-001 and att-004)
	var attCount int
	query := "SELECT COUNT(*) FROM attachments WHERE message_id = 1"
	err = warehouseDB.QueryRow(query).Scan(&attCount)
	if err != nil {
		t.Fatalf("Failed to count attachments for message 1: %v", err)
	}

	if attCount != 2 {
		t.Errorf("Expected 2 attachments for message 1, got %d", attCount)
	}
}

func TestSyncAttachments_StickerFlag(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync attachments
	_, err = SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments: %v", err)
	}

	// Verify att-003 is a sticker
	var isSticker bool
	query := "SELECT is_sticker FROM attachments WHERE guid = 'att-003'"
	err = warehouseDB.QueryRow(query).Scan(&isSticker)
	if err != nil {
		t.Fatalf("Failed to query is_sticker: %v", err)
	}

	if !isSticker {
		t.Errorf("Expected is_sticker = true for att-003")
	}

	// Verify att-001 is not a sticker
	query = "SELECT is_sticker FROM attachments WHERE guid = 'att-001'"
	err = warehouseDB.QueryRow(query).Scan(&isSticker)
	if err != nil {
		t.Fatalf("Failed to query is_sticker for att-001: %v", err)
	}

	if isSticker {
		t.Errorf("Expected is_sticker = false for att-001")
	}
}

func TestSyncAttachments_NullableFields(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync attachments
	_, err = SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync attachments: %v", err)
	}

	// Verify att-003 has empty filename (stickers often don't have filenames)
	var filename string
	query := "SELECT file_name FROM attachments WHERE guid = 'att-003'"
	err = warehouseDB.QueryRow(query).Scan(&filename)
	if err != nil {
		t.Fatalf("Failed to query file_name: %v", err)
	}

	if filename != "" {
		t.Errorf("Expected empty file_name for att-003, got %q", filename)
	}

	// Verify att-001 has proper fields
	var mimeType, uti string
	var size sql.NullInt64
	query = "SELECT mime_type, uti, size FROM attachments WHERE guid = 'att-001'"
	err = warehouseDB.QueryRow(query).Scan(&mimeType, &uti, &size)
	if err != nil {
		t.Fatalf("Failed to query attachment fields: %v", err)
	}

	if mimeType != "image/jpeg" {
		t.Errorf("Expected mime_type 'image/jpeg', got %q", mimeType)
	}

	if uti != "public.jpeg" {
		t.Errorf("Expected uti 'public.jpeg', got %q", uti)
	}

	if !size.Valid || size.Int64 != 1024000 {
		t.Errorf("Expected size 1024000, got Valid=%v Int64=%d", size.Valid, size.Int64)
	}
}

func TestSyncAttachments_Empty(t *testing.T) {
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
			guid TEXT UNIQUE NOT NULL
		);
		CREATE TABLE attachment (
			ROWID INTEGER PRIMARY KEY,
			guid TEXT UNIQUE NOT NULL,
			created_date INTEGER,
			filename TEXT,
			uti TEXT,
			mime_type TEXT,
			total_bytes INTEGER,
			is_sticker INTEGER
		);
		CREATE TABLE message_attachment_join (
			message_id INTEGER,
			attachment_id INTEGER,
			PRIMARY KEY (message_id, attachment_id)
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

	warehouseDB := createTestWarehouseDBWithAttachments(t)
	defer warehouseDB.Close()

	// Sync empty attachments
	count, err := SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync empty attachments: %v", err)
	}

	if count != 0 {
		t.Errorf("Expected 0 attachments synced, got %d", count)
	}

	// Verify no attachments created
	var attCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM attachments").Scan(&attCount)
	if err != nil {
		t.Fatalf("Failed to count attachments: %v", err)
	}

	if attCount != 0 {
		t.Errorf("Expected 0 attachments, got %d", attCount)
	}
}

func TestGetAttachments(t *testing.T) {
	chatDBPath := createTestChatDBWithAttachments(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	// Get all attachments
	attachments, err := chatDB.GetAttachments()
	if err != nil {
		t.Fatalf("Failed to get attachments: %v", err)
	}

	if len(attachments) != 4 {
		t.Errorf("Expected 4 attachments, got %d", len(attachments))
	}

	// Verify ROWID sequence
	for i, att := range attachments {
		expectedROWID := int64(i + 1)
		if att.ROWID != expectedROWID {
			t.Errorf("Expected ROWID %d, got %d", expectedROWID, att.ROWID)
		}

		if att.GUID == "" {
			t.Errorf("Expected non-empty GUID for attachment %d", att.ROWID)
		}

		if att.MessageGUID == "" {
			t.Errorf("Expected non-empty MessageGUID for attachment %d", att.ROWID)
		}
	}

	// Verify sticker count
	stickerCount := 0
	for _, att := range attachments {
		if att.IsSticker {
			stickerCount++
		}
	}

	if stickerCount != 1 {
		t.Errorf("Expected 1 sticker, got %d", stickerCount)
	}
}
