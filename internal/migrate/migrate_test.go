package migrate

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func TestMigrateQueue(t *testing.T) {
	// Create temp database
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-queue.db")

	// Run migrations
	if err := MigrateQueue(dbPath); err != nil {
		t.Fatalf("MigrateQueue failed: %v", err)
	}

	// Verify database was created
	if _, err := os.Stat(dbPath); os.IsNotExist(err) {
		t.Fatal("Database file was not created")
	}

	// Open database and verify schema
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Verify jobs table exists
	var tableName string
	err = db.QueryRow("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").Scan(&tableName)
	if err != nil {
		t.Fatalf("jobs table does not exist: %v", err)
	}
	if tableName != "jobs" {
		t.Errorf("Expected table name 'jobs', got '%s'", tableName)
	}

	// Verify runs table exists
	err = db.QueryRow("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'").Scan(&tableName)
	if err != nil {
		t.Fatalf("runs table does not exist: %v", err)
	}

	// Verify schema_migrations table exists and has entry
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM schema_migrations WHERE version = '001_init.sql'").Scan(&count)
	if err != nil {
		t.Fatalf("Failed to query schema_migrations: %v", err)
	}
	if count != 1 {
		t.Errorf("Expected 1 migration entry, got %d", count)
	}
}

func TestMigrateWarehouse(t *testing.T) {
	// Create temp database
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-warehouse.db")

	// Run migrations
	if err := MigrateWarehouse(dbPath); err != nil {
		t.Fatalf("MigrateWarehouse failed: %v", err)
	}

	// Verify database was created
	if _, err := os.Stat(dbPath); os.IsNotExist(err) {
		t.Fatal("Database file was not created")
	}

	// Open database and verify schema
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Verify watermarks table exists
	var tableName string
	err = db.QueryRow("SELECT name FROM sqlite_master WHERE type='table' AND name='watermarks'").Scan(&tableName)
	if err != nil {
		t.Fatalf("watermarks table does not exist: %v", err)
	}
	if tableName != "watermarks" {
		t.Errorf("Expected table name 'watermarks', got '%s'", tableName)
	}

	// Verify schema_migrations table exists and has entry
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM schema_migrations WHERE version = '001_watermarks.sql'").Scan(&count)
	if err != nil {
		t.Fatalf("Failed to query schema_migrations: %v", err)
	}
	if count != 1 {
		t.Errorf("Expected 1 migration entry, got %d", count)
	}
}

func TestMigrationIdempotency(t *testing.T) {
	// Create temp database
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-idempotent.db")

	// Run migrations first time
	if err := MigrateQueue(dbPath); err != nil {
		t.Fatalf("First MigrateQueue failed: %v", err)
	}

	// Run migrations second time (should be idempotent)
	if err := MigrateQueue(dbPath); err != nil {
		t.Fatalf("Second MigrateQueue failed: %v", err)
	}

	// Verify only one migration entry exists
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM schema_migrations").Scan(&count)
	if err != nil {
		t.Fatalf("Failed to query schema_migrations: %v", err)
	}
	entries, err := queueMigrations.ReadDir("sql/queue")
	if err != nil {
		t.Fatalf("Failed to read embedded queue migrations: %v", err)
	}
	expected := 0
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		if strings.HasSuffix(e.Name(), ".sql") {
			expected++
		}
	}
	if count != expected {
		t.Errorf("Expected %d migration entries after two runs, got %d", expected, count)
	}
}

func TestJobsTableSchema(t *testing.T) {
	// Create temp database
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-schema.db")

	if err := MigrateQueue(dbPath); err != nil {
		t.Fatalf("MigrateQueue failed: %v", err)
	}

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Test inserting and querying a job
	_, err = db.Exec(`
		INSERT INTO jobs (id, type, key, payload_json, state, run_after_ts, created_ts, updated_ts)
		VALUES ('test-id', 'test-type', 'test-key', '{}', 'pending', 0, 0, 0)
	`)
	if err != nil {
		t.Fatalf("Failed to insert test job: %v", err)
	}

	// Verify job was inserted
	var jobID string
	err = db.QueryRow("SELECT id FROM jobs WHERE key = 'test-key'").Scan(&jobID)
	if err != nil {
		t.Fatalf("Failed to query test job: %v", err)
	}
	if jobID != "test-id" {
		t.Errorf("Expected job ID 'test-id', got '%s'", jobID)
	}

	// Test unique constraint on key
	_, err = db.Exec(`
		INSERT INTO jobs (id, type, key, payload_json, state, run_after_ts, created_ts, updated_ts)
		VALUES ('test-id-2', 'test-type', 'test-key', '{}', 'pending', 0, 0, 0)
	`)
	if err == nil {
		t.Error("Expected unique constraint violation on key, but insert succeeded")
	}
}
