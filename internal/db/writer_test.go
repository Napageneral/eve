package db

import (
	"database/sql"
	"os"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func setupTestDB(t *testing.T) *sql.DB {
	tmpFile, err := os.CreateTemp("", "writer-test-*.db")
	if err != nil {
		t.Fatalf("failed to create temp file: %v", err)
	}
	tmpFile.Close()
	t.Cleanup(func() { os.Remove(tmpFile.Name()) })

	db, err := sql.Open("sqlite3", tmpFile.Name())
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	// Create test table
	_, err = db.Exec(`
		CREATE TABLE test_data (
			id TEXT PRIMARY KEY,
			value TEXT NOT NULL
		)
	`)
	if err != nil {
		t.Fatalf("failed to create test table: %v", err)
	}

	return db
}

func TestWriter_Write_SingleOp(t *testing.T) {
	db := setupTestDB(t)

	writer := NewWriter(db, WriterConfig{
		BatchSize:     10,
		FlushInterval: 100 * time.Millisecond,
	})
	defer writer.Close()

	err := writer.Write("INSERT INTO test_data (id, value) VALUES (?, ?)", "test-1", "value-1")
	if err != nil {
		t.Fatalf("Write failed: %v", err)
	}

	// Flush immediately
	err = writer.Flush()
	if err != nil {
		t.Fatalf("Flush failed: %v", err)
	}

	// Verify data was written
	var value string
	err = db.QueryRow("SELECT value FROM test_data WHERE id = ?", "test-1").Scan(&value)
	if err != nil {
		t.Fatalf("failed to query data: %v", err)
	}

	if value != "value-1" {
		t.Errorf("expected value-1, got %s", value)
	}
}

func TestWriter_Write_BatchFlush(t *testing.T) {
	db := setupTestDB(t)

	writer := NewWriter(db, WriterConfig{
		BatchSize:     5,
		FlushInterval: 1 * time.Second,
	})
	defer writer.Close()

	// Write 5 operations (should trigger auto-flush)
	for i := 0; i < 5; i++ {
		err := writer.Write("INSERT INTO test_data (id, value) VALUES (?, ?)",
			"test-"+string(rune('0'+i)), "value")
		if err != nil {
			t.Fatalf("Write failed: %v", err)
		}
	}

	// Give time for auto-flush
	time.Sleep(200 * time.Millisecond)

	// Verify all data was written
	var count int
	err := db.QueryRow("SELECT COUNT(*) FROM test_data").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query count: %v", err)
	}

	if count != 5 {
		t.Errorf("expected 5 rows, got %d", count)
	}
}

func TestWriter_Write_TimerFlush(t *testing.T) {
	db := setupTestDB(t)

	writer := NewWriter(db, WriterConfig{
		BatchSize:     100,
		FlushInterval: 200 * time.Millisecond,
	})
	defer writer.Close()

	// Write 2 operations (below batch size)
	for i := 0; i < 2; i++ {
		err := writer.Write("INSERT INTO test_data (id, value) VALUES (?, ?)",
			"test-"+string(rune('0'+i)), "value")
		if err != nil {
			t.Fatalf("Write failed: %v", err)
		}
	}

	// Wait for timer flush
	time.Sleep(300 * time.Millisecond)

	// Verify data was written
	var count int
	err := db.QueryRow("SELECT COUNT(*) FROM test_data").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query count: %v", err)
	}

	if count != 2 {
		t.Errorf("expected 2 rows, got %d", count)
	}
}

func TestWriter_Write_Transaction(t *testing.T) {
	db := setupTestDB(t)

	writer := NewWriter(db, WriterConfig{
		BatchSize:     10,
		FlushInterval: 1 * time.Second,
	})
	defer writer.Close()

	// Write multiple operations
	writer.Write("INSERT INTO test_data (id, value) VALUES (?, ?)", "test-1", "value-1")
	writer.Write("INSERT INTO test_data (id, value) VALUES (?, ?)", "test-2", "value-2")

	// Flush
	err := writer.Flush()
	if err != nil {
		t.Fatalf("Flush failed: %v", err)
	}

	// Verify both were written atomically
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM test_data").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query count: %v", err)
	}

	if count != 2 {
		t.Errorf("expected 2 rows, got %d", count)
	}
}

func TestWriter_Close_FlushesRemaining(t *testing.T) {
	db := setupTestDB(t)

	writer := NewWriter(db, WriterConfig{
		BatchSize:     100,
		FlushInterval: 10 * time.Second, // Long interval
	})

	// Write operations
	for i := 0; i < 3; i++ {
		err := writer.Write("INSERT INTO test_data (id, value) VALUES (?, ?)",
			"test-"+string(rune('0'+i)), "value")
		if err != nil {
			t.Fatalf("Write failed: %v", err)
		}
	}

	// Close should flush
	err := writer.Close()
	if err != nil {
		t.Fatalf("Close failed: %v", err)
	}

	// Verify data was written
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM test_data").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query count: %v", err)
	}

	if count != 3 {
		t.Errorf("expected 3 rows, got %d", count)
	}
}

func TestWriter_Write_Idempotency(t *testing.T) {
	db := setupTestDB(t)

	writer := NewWriter(db, WriterConfig{
		BatchSize:     10,
		FlushInterval: 100 * time.Millisecond,
	})
	defer writer.Close()

	// Use INSERT OR REPLACE for idempotency
	for i := 0; i < 2; i++ {
		err := writer.Write("INSERT OR REPLACE INTO test_data (id, value) VALUES (?, ?)",
			"test-1", "value-updated")
		if err != nil {
			t.Fatalf("Write failed: %v", err)
		}
	}

	err := writer.Flush()
	if err != nil {
		t.Fatalf("Flush failed: %v", err)
	}

	// Verify only one row exists
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM test_data WHERE id = ?", "test-1").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query count: %v", err)
	}

	if count != 1 {
		t.Errorf("expected 1 row (idempotent), got %d", count)
	}

	// Verify value is updated
	var value string
	err = db.QueryRow("SELECT value FROM test_data WHERE id = ?", "test-1").Scan(&value)
	if err != nil {
		t.Fatalf("failed to query value: %v", err)
	}

	if value != "value-updated" {
		t.Errorf("expected value-updated, got %s", value)
	}
}
