package etl

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// ChatDB handles read-only access to the macOS Messages chat.db
type ChatDB struct {
	db *sql.DB
}

// MessageCount contains statistics about messages in chat.db
type MessageCount struct {
	TotalMessages int       `json:"total_messages"`
	MaxRowID      int64     `json:"max_rowid"`
	OldestDate    time.Time `json:"oldest_date,omitempty"`
	NewestDate    time.Time `json:"newest_date,omitempty"`
}

// GetChatDBPath returns the path to the macOS Messages chat.db
func GetChatDBPath() string {
	// Check for env override first
	if override := os.Getenv("EVE_SOURCE_CHAT_DB"); override != "" {
		return os.ExpandEnv(override)
	}
	if override := os.Getenv("CHATSTATS_SOURCE_CHAT_DB"); override != "" {
		return os.ExpandEnv(override)
	}

	// Default macOS location
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, "Library", "Messages", "chat.db")
}

// OpenChatDB opens the chat.db with read-only optimized pragmas
func OpenChatDB(path string) (*ChatDB, error) {
	// Check if file exists
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil, fmt.Errorf("chat.db not found at %s", path)
	}

	// Open with read-only URI mode
	// Note: Don't use immutable=1 for live macOS Messages DB (uses WAL)
	uri := fmt.Sprintf("file:%s?mode=ro", path)
	db, err := sql.Open("sqlite3", uri)
	if err != nil {
		return nil, fmt.Errorf("failed to open chat.db: %w", err)
	}

	// Set read-only pragmas for performance
	pragmas := []string{
		"PRAGMA query_only=ON",
		"PRAGMA synchronous=OFF",
		"PRAGMA journal_mode=OFF",
		"PRAGMA temp_store=MEMORY",
		"PRAGMA cache_size=-262144",  // 256MB cache
		"PRAGMA mmap_size=268435456", // 256MB memory map
	}

	for _, pragma := range pragmas {
		if _, err := db.Exec(pragma); err != nil {
			// Ignore pragma errors (some may not be supported)
			continue
		}
	}

	return &ChatDB{db: db}, nil
}

// Close closes the chat.db connection
func (c *ChatDB) Close() error {
	if c.db != nil {
		return c.db.Close()
	}
	return nil
}

// CountMessages returns statistics about messages in chat.db
// If sinceRowID > 0, only counts messages with ROWID > sinceRowID
func (c *ChatDB) CountMessages(sinceRowID int64) (*MessageCount, error) {
	query := `
		SELECT
			COUNT(*) as total,
			COALESCE(MAX(ROWID), 0) as max_rowid,
			MIN(date) as oldest_date,
			MAX(date) as newest_date
		FROM message
	`

	args := []interface{}{}
	if sinceRowID > 0 {
		query += " WHERE ROWID > ?"
		args = append(args, sinceRowID)
	}

	var count MessageCount
	var oldestNano, newestNano sql.NullInt64

	err := c.db.QueryRow(query, args...).Scan(
		&count.TotalMessages,
		&count.MaxRowID,
		&oldestNano,
		&newestNano,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to count messages: %w", err)
	}

	// Convert Apple timestamps (nanoseconds since 2001-01-01) to Go time
	// Apple epoch: 2001-01-01 00:00:00 UTC
	appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)

	if oldestNano.Valid && oldestNano.Int64 > 0 {
		count.OldestDate = appleEpoch.Add(time.Duration(oldestNano.Int64) * time.Nanosecond)
	}
	if newestNano.Valid && newestNano.Int64 > 0 {
		count.NewestDate = appleEpoch.Add(time.Duration(newestNano.Int64) * time.Nanosecond)
	}

	return &count, nil
}

// GetChatCount returns the number of chats in chat.db
func (c *ChatDB) GetChatCount() (int, error) {
	var count int
	err := c.db.QueryRow("SELECT COUNT(*) FROM chat").Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("failed to count chats: %w", err)
	}
	return count, nil
}

// GetHandleCount returns the number of handles (contacts) in chat.db
func (c *ChatDB) GetHandleCount() (int, error) {
	var count int
	err := c.db.QueryRow("SELECT COUNT(*) FROM handle").Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("failed to count handles: %w", err)
	}
	return count, nil
}
