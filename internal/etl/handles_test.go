package etl

import (
	"database/sql"
	"path/filepath"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

// createTestChatDBWithHandles creates a chat.db with test handles
func createTestChatDBWithHandles(t *testing.T) string {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test chat.db: %v", err)
	}
	defer db.Close()

	// Create minimal handle table
	schema := `
		CREATE TABLE handle (
			ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
			id TEXT UNIQUE NOT NULL
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Insert test handles: mix of phones and emails
	testHandles := []string{
		"+1234567890",
		"test@example.com",
		"+9876543210",
		"another@test.com",
		"+5555555555",
	}

	for _, handle := range testHandles {
		_, err := db.Exec("INSERT INTO handle (id) VALUES (?)", handle)
		if err != nil {
			t.Fatalf("Failed to insert test handle: %v", err)
		}
	}

	return dbPath
}

// createTestWarehouseDBWithContacts creates an eve.db with the contacts schema
func createTestWarehouseDBWithContacts(t *testing.T) *sql.DB {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test eve.db: %v", err)
	}

	// Create contacts and contact_identifiers schema
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

		CREATE TABLE contact_identifiers (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			contact_id INTEGER NOT NULL,
			identifier TEXT NOT NULL,
			type TEXT NOT NULL,
			is_primary BOOLEAN DEFAULT 0,
			last_used TIMESTAMP,
			FOREIGN KEY (contact_id) REFERENCES contacts(id),
			UNIQUE(identifier, type)
		);

		CREATE INDEX idx_contact_identifiers_contact ON contact_identifiers(contact_id);
		CREATE INDEX idx_contact_identifiers_identifier ON contact_identifiers(identifier);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create warehouse schema: %v", err)
	}

	return db
}

func TestSyncHandles(t *testing.T) {
	chatDBPath := createTestChatDBWithHandles(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithContacts(t)
	defer warehouseDB.Close()

	// Sync handles
	count, err := SyncHandles(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync handles: %v", err)
	}

	if count != 5 {
		t.Errorf("Expected 5 handles synced, got %d", count)
	}

	// Verify contacts table
	var contactCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contacts").Scan(&contactCount)
	if err != nil {
		t.Fatalf("Failed to count contacts: %v", err)
	}

	if contactCount != 5 {
		t.Errorf("Expected 5 contacts, got %d", contactCount)
	}

	// Verify all contacts have data_source = 'chat.db'
	var wrongSource int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contacts WHERE data_source != 'chat.db'").Scan(&wrongSource)
	if err != nil {
		t.Fatalf("Failed to check data_source: %v", err)
	}

	if wrongSource != 0 {
		t.Errorf("Expected 0 contacts with wrong data_source, got %d", wrongSource)
	}

	// Verify contact_identifiers table
	var identifierCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contact_identifiers").Scan(&identifierCount)
	if err != nil {
		t.Fatalf("Failed to count contact_identifiers: %v", err)
	}

	if identifierCount != 5 {
		t.Errorf("Expected 5 contact_identifiers, got %d", identifierCount)
	}

	// Verify phone vs email type
	var phoneCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contact_identifiers WHERE type = 'phone'").Scan(&phoneCount)
	if err != nil {
		t.Fatalf("Failed to count phones: %v", err)
	}

	if phoneCount != 3 {
		t.Errorf("Expected 3 phone identifiers, got %d", phoneCount)
	}

	var emailCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contact_identifiers WHERE type = 'email'").Scan(&emailCount)
	if err != nil {
		t.Fatalf("Failed to count emails: %v", err)
	}

	if emailCount != 2 {
		t.Errorf("Expected 2 email identifiers, got %d", emailCount)
	}
}

func TestSyncHandles_Idempotent(t *testing.T) {
	chatDBPath := createTestChatDBWithHandles(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithContacts(t)
	defer warehouseDB.Close()

	// Sync handles twice
	count1, err := SyncHandles(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync handles (first): %v", err)
	}

	count2, err := SyncHandles(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync handles (second): %v", err)
	}

	if count1 != count2 {
		t.Errorf("Expected same count on second sync, got %d vs %d", count1, count2)
	}

	// Verify no duplicates in contacts
	var contactCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contacts").Scan(&contactCount)
	if err != nil {
		t.Fatalf("Failed to count contacts: %v", err)
	}

	if contactCount != 5 {
		t.Errorf("Expected 5 contacts after idempotent sync, got %d", contactCount)
	}

	// Verify no duplicates in contact_identifiers
	var identifierCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contact_identifiers").Scan(&identifierCount)
	if err != nil {
		t.Fatalf("Failed to count contact_identifiers: %v", err)
	}

	if identifierCount != 5 {
		t.Errorf("Expected 5 contact_identifiers after idempotent sync, got %d", identifierCount)
	}
}

func TestSyncHandles_Empty(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "empty_chat.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create empty chat.db: %v", err)
	}

	// Create schema but no data
	_, err = db.Exec("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
	if err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}
	db.Close()

	chatDB, err := OpenChatDB(dbPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithContacts(t)
	defer warehouseDB.Close()

	// Sync empty handles
	count, err := SyncHandles(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync empty handles: %v", err)
	}

	if count != 0 {
		t.Errorf("Expected 0 handles synced, got %d", count)
	}

	// Verify no contacts created
	var contactCount int
	err = warehouseDB.QueryRow("SELECT COUNT(*) FROM contacts").Scan(&contactCount)
	if err != nil {
		t.Fatalf("Failed to count contacts: %v", err)
	}

	if contactCount != 0 {
		t.Errorf("Expected 0 contacts, got %d", contactCount)
	}
}

func TestGetHandles(t *testing.T) {
	chatDBPath := createTestChatDBWithHandles(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	handles, err := chatDB.GetHandles()
	if err != nil {
		t.Fatalf("Failed to get handles: %v", err)
	}

	if len(handles) != 5 {
		t.Errorf("Expected 5 handles, got %d", len(handles))
	}

	// Verify ROWID sequence
	for i, handle := range handles {
		expectedROWID := int64(i + 1)
		if handle.ROWID != expectedROWID {
			t.Errorf("Expected ROWID %d, got %d", expectedROWID, handle.ROWID)
		}

		if handle.ID == "" {
			t.Errorf("Expected non-empty ID for handle %d", handle.ROWID)
		}
	}
}

func TestDetermineIdentifierType(t *testing.T) {
	tests := []struct {
		identifier string
		expected   string
	}{
		{"+1234567890", "phone"},
		{"test@example.com", "email"},
		{"user@domain.org", "email"},
		{"+44 20 7946 0958", "phone"},
		{"555-1234", "phone"},
		{"simple", "phone"}, // No @, defaults to phone
	}

	for _, tt := range tests {
		result := determineIdentifierType(tt.identifier)
		if result != tt.expected {
			t.Errorf("determineIdentifierType(%q) = %q, expected %q", tt.identifier, result, tt.expected)
		}
	}
}

func TestInsertHandle_ContactIDMapping(t *testing.T) {
	chatDBPath := createTestChatDBWithHandles(t)
	chatDB, err := OpenChatDB(chatDBPath)
	if err != nil {
		t.Fatalf("Failed to open chat.db: %v", err)
	}
	defer chatDB.Close()

	warehouseDB := createTestWarehouseDBWithContacts(t)
	defer warehouseDB.Close()

	// Sync handles
	_, err = SyncHandles(chatDB, warehouseDB)
	if err != nil {
		t.Fatalf("Failed to sync handles: %v", err)
	}

	// Verify that contact.id matches handle.ROWID
	// This is critical for foreign key references
	handles, err := chatDB.GetHandles()
	if err != nil {
		t.Fatalf("Failed to get handles: %v", err)
	}

	for _, handle := range handles {
		var contactID int64
		var dataSource string
		query := "SELECT id, data_source FROM contacts WHERE id = ?"
		err := warehouseDB.QueryRow(query, handle.ROWID).Scan(&contactID, &dataSource)
		if err != nil {
			t.Fatalf("Failed to find contact for handle ROWID %d: %v", handle.ROWID, err)
		}

		if contactID != handle.ROWID {
			t.Errorf("Contact ID mismatch: handle ROWID %d, contact ID %d", handle.ROWID, contactID)
		}

		if dataSource != "chat.db" {
			t.Errorf("Expected data_source 'chat.db', got %q", dataSource)
		}
	}
}
