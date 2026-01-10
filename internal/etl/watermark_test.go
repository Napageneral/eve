package etl

import (
	"database/sql"
	"path/filepath"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func createTestWarehouseDB(t *testing.T) *sql.DB {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "eve.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to create test warehouse DB: %v", err)
	}

	// Create watermarks table
	schema := `
		CREATE TABLE watermarks (
			source TEXT NOT NULL,
			name TEXT NOT NULL,
			value_int INTEGER,
			value_text TEXT,
			updated_ts INTEGER NOT NULL,
			PRIMARY KEY (source, name)
		);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create watermarks table: %v", err)
	}

	return db
}

func TestGetWatermark_NotExists(t *testing.T) {
	db := createTestWarehouseDB(t)
	defer db.Close()

	wm, err := GetWatermark(db, "chatdb", "message_rowid")
	if err != nil {
		t.Fatalf("Unexpected error: %v", err)
	}

	if wm != nil {
		t.Error("Expected nil watermark for non-existent entry")
	}
}

func TestSetWatermark_Int(t *testing.T) {
	db := createTestWarehouseDB(t)
	defer db.Close()

	valueInt := int64(12345)
	err := SetWatermark(db, "chatdb", "message_rowid", &valueInt, nil)
	if err != nil {
		t.Fatalf("Failed to set watermark: %v", err)
	}

	// Retrieve and verify
	wm, err := GetWatermark(db, "chatdb", "message_rowid")
	if err != nil {
		t.Fatalf("Failed to get watermark: %v", err)
	}

	if wm == nil {
		t.Fatal("Expected watermark to exist")
	}

	if !wm.ValueInt.Valid || wm.ValueInt.Int64 != 12345 {
		t.Errorf("Expected value_int=12345, got %v", wm.ValueInt)
	}

	if wm.ValueText.Valid {
		t.Error("Expected value_text to be NULL")
	}

	if wm.Source != "chatdb" {
		t.Errorf("Expected source=chatdb, got %s", wm.Source)
	}

	if wm.Name != "message_rowid" {
		t.Errorf("Expected name=message_rowid, got %s", wm.Name)
	}
}

func TestSetWatermark_Text(t *testing.T) {
	db := createTestWarehouseDB(t)
	defer db.Close()

	valueText := "2024-01-08T12:00:00Z"
	err := SetWatermark(db, "chatdb", "last_sync", nil, &valueText)
	if err != nil {
		t.Fatalf("Failed to set watermark: %v", err)
	}

	// Retrieve and verify
	wm, err := GetWatermark(db, "chatdb", "last_sync")
	if err != nil {
		t.Fatalf("Failed to get watermark: %v", err)
	}

	if wm == nil {
		t.Fatal("Expected watermark to exist")
	}

	if wm.ValueInt.Valid {
		t.Error("Expected value_int to be NULL")
	}

	if !wm.ValueText.Valid || wm.ValueText.String != "2024-01-08T12:00:00Z" {
		t.Errorf("Expected value_text=2024-01-08T12:00:00Z, got %v", wm.ValueText)
	}
}

func TestSetWatermark_Update(t *testing.T) {
	db := createTestWarehouseDB(t)
	defer db.Close()

	// Insert initial watermark
	valueInt := int64(100)
	err := SetWatermark(db, "chatdb", "message_rowid", &valueInt, nil)
	if err != nil {
		t.Fatalf("Failed to set initial watermark: %v", err)
	}

	// Update watermark
	valueInt = 200
	err = SetWatermark(db, "chatdb", "message_rowid", &valueInt, nil)
	if err != nil {
		t.Fatalf("Failed to update watermark: %v", err)
	}

	// Verify update
	wm, err := GetWatermark(db, "chatdb", "message_rowid")
	if err != nil {
		t.Fatalf("Failed to get watermark: %v", err)
	}

	if !wm.ValueInt.Valid || wm.ValueInt.Int64 != 200 {
		t.Errorf("Expected value_int=200, got %v", wm.ValueInt)
	}

	// Verify only one row exists
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM watermarks WHERE source = ? AND name = ?",
		"chatdb", "message_rowid").Scan(&count)
	if err != nil {
		t.Fatalf("Failed to count watermarks: %v", err)
	}

	if count != 1 {
		t.Errorf("Expected 1 watermark row, got %d", count)
	}
}

func TestSetWatermark_BothValues(t *testing.T) {
	db := createTestWarehouseDB(t)
	defer db.Close()

	valueInt := int64(500)
	valueText := "checkpoint-abc"
	err := SetWatermark(db, "chatdb", "combined", &valueInt, &valueText)
	if err != nil {
		t.Fatalf("Failed to set watermark: %v", err)
	}

	wm, err := GetWatermark(db, "chatdb", "combined")
	if err != nil {
		t.Fatalf("Failed to get watermark: %v", err)
	}

	if !wm.ValueInt.Valid || wm.ValueInt.Int64 != 500 {
		t.Errorf("Expected value_int=500, got %v", wm.ValueInt)
	}

	if !wm.ValueText.Valid || wm.ValueText.String != "checkpoint-abc" {
		t.Errorf("Expected value_text=checkpoint-abc, got %v", wm.ValueText)
	}
}

func TestWatermark_MultipleEntries(t *testing.T) {
	db := createTestWarehouseDB(t)
	defer db.Close()

	// Insert multiple watermarks
	v1 := int64(100)
	v2 := int64(200)
	v3 := "text-value"

	err := SetWatermark(db, "chatdb", "message_rowid", &v1, nil)
	if err != nil {
		t.Fatalf("Failed to set watermark 1: %v", err)
	}

	err = SetWatermark(db, "chatdb", "chat_rowid", &v2, nil)
	if err != nil {
		t.Fatalf("Failed to set watermark 2: %v", err)
	}

	err = SetWatermark(db, "addressbook", "last_sync", nil, &v3)
	if err != nil {
		t.Fatalf("Failed to set watermark 3: %v", err)
	}

	// Verify each can be retrieved independently
	wm1, _ := GetWatermark(db, "chatdb", "message_rowid")
	wm2, _ := GetWatermark(db, "chatdb", "chat_rowid")
	wm3, _ := GetWatermark(db, "addressbook", "last_sync")

	if wm1.ValueInt.Int64 != 100 {
		t.Errorf("Expected wm1 value_int=100, got %d", wm1.ValueInt.Int64)
	}

	if wm2.ValueInt.Int64 != 200 {
		t.Errorf("Expected wm2 value_int=200, got %d", wm2.ValueInt.Int64)
	}

	if wm3.ValueText.String != "text-value" {
		t.Errorf("Expected wm3 value_text=text-value, got %s", wm3.ValueText.String)
	}
}

// Verify watermark SQL injection safety
func TestWatermark_SQLInjection(t *testing.T) {
	db := createTestWarehouseDB(t)
	defer db.Close()

	// Try to inject SQL via source/name fields
	maliciousSource := "chatdb'; DROP TABLE watermarks; --"
	maliciousName := "rowid' OR '1'='1"

	v := int64(123)
	err := SetWatermark(db, maliciousSource, maliciousName, &v, nil)
	if err != nil {
		t.Fatalf("Failed to set watermark: %v", err)
	}

	// Table should still exist
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM watermarks").Scan(&count)
	if err != nil {
		t.Fatal("Watermarks table was dropped or damaged")
	}

	// Should be able to retrieve with exact strings
	wm, err := GetWatermark(db, maliciousSource, maliciousName)
	if err != nil {
		t.Fatalf("Failed to get watermark: %v", err)
	}

	if wm == nil || wm.ValueInt.Int64 != 123 {
		t.Error("SQL injection protection failed")
	}
}
