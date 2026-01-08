package migrate

import (
	"database/sql"
	"embed"
	"fmt"
	"path"
	"sort"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

//go:embed sql/queue/*.sql
var queueMigrations embed.FS

//go:embed sql/warehouse/*.sql
var warehouseMigrations embed.FS

// MigrateQueue runs all queue database migrations
func MigrateQueue(dbPath string) error {
	return runMigrations(dbPath, queueMigrations, "sql/queue")
}

// MigrateWarehouse runs all warehouse database migrations
func MigrateWarehouse(dbPath string) error {
	return runMigrations(dbPath, warehouseMigrations, "sql/warehouse")
}

// runMigrations executes migration files in order
func runMigrations(dbPath string, fs embed.FS, migrationDir string) error {
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return fmt.Errorf("failed to open database: %w", err)
	}
	defer db.Close()

	// Enable foreign keys
	if _, err := db.Exec("PRAGMA foreign_keys = ON"); err != nil {
		return fmt.Errorf("failed to enable foreign keys: %w", err)
	}

	// Create migrations tracking table
	if err := createMigrationsTable(db); err != nil {
		return fmt.Errorf("failed to create migrations table: %w", err)
	}

	// Get list of migration files
	entries, err := fs.ReadDir(migrationDir)
	if err != nil {
		return fmt.Errorf("failed to read migration directory: %w", err)
	}

	// Filter and sort SQL files
	var migrationFiles []string
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".sql") {
			migrationFiles = append(migrationFiles, entry.Name())
		}
	}
	sort.Strings(migrationFiles)

	// Execute each migration
	for _, filename := range migrationFiles {
		if err := executeMigration(db, fs, path.Join(migrationDir, filename), filename); err != nil {
			return fmt.Errorf("migration %s failed: %w", filename, err)
		}
	}

	return nil
}

// createMigrationsTable creates the schema_migrations tracking table
func createMigrationsTable(db *sql.DB) error {
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version TEXT PRIMARY KEY,
			applied_ts INTEGER NOT NULL
		)
	`)
	return err
}

// executeMigration runs a single migration if it hasn't been applied
func executeMigration(db *sql.DB, fs embed.FS, filePath, filename string) error {
	// Check if already applied
	var exists bool
	err := db.QueryRow("SELECT 1 FROM schema_migrations WHERE version = ?", filename).Scan(&exists)
	if err == nil {
		// Already applied
		return nil
	}
	if err != sql.ErrNoRows {
		return fmt.Errorf("failed to check migration status: %w", err)
	}

	// Read migration file
	content, err := fs.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("failed to read migration file: %w", err)
	}

	// Begin transaction
	tx, err := db.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Execute migration
	if _, err := tx.Exec(string(content)); err != nil {
		return fmt.Errorf("failed to execute migration: %w", err)
	}

	// Record migration
	if _, err := tx.Exec(
		"INSERT INTO schema_migrations (version, applied_ts) VALUES (?, ?)",
		filename,
		getCurrentTimestamp(),
	); err != nil {
		return fmt.Errorf("failed to record migration: %w", err)
	}

	// Commit transaction
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("failed to commit migration: %w", err)
	}

	return nil
}

// getCurrentTimestamp returns current Unix timestamp
func getCurrentTimestamp() int64 {
	return time.Now().Unix()
}
