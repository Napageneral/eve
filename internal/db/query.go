package db

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	_ "github.com/mattn/go-sqlite3"
)

// QueryResult represents the result of a database query
type QueryResult struct {
	OK       bool                     `json:"ok"`
	RowCount int                      `json:"row_count"`
	Rows     []map[string]interface{} `json:"rows,omitempty"`
	Error    string                   `json:"error,omitempty"`
}

// DBType represents different database types
type DBType string

const (
	DBTypeWarehouse DBType = "warehouse"
	DBTypeQueue     DBType = "queue"
	DBTypePath      DBType = "path"
)

// QueryOptions contains options for executing a query
type QueryOptions struct {
	SQL        string
	DBSpec     string // warehouse, queue, or path:/abs/file.db
	AllowWrite bool
}

// Execute runs a SQL query with safety checks
func Execute(opts QueryOptions) QueryResult {
	// Parse database spec
	dbPath, err := parseDatabaseSpec(opts.DBSpec)
	if err != nil {
		return QueryResult{
			OK:    false,
			Error: fmt.Sprintf("invalid database spec: %v", err),
		}
	}

	// Check SQL safety
	if !opts.AllowWrite {
		if err := checkSQLSafety(opts.SQL); err != nil {
			return QueryResult{
				OK:    false,
				Error: fmt.Sprintf("query not allowed: %v", err),
			}
		}
	}

	// Open database
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return QueryResult{
			OK:    false,
			Error: fmt.Sprintf("failed to open database: %v", err),
		}
	}
	defer db.Close()

	// Execute query
	rows, err := db.Query(opts.SQL)
	if err != nil {
		return QueryResult{
			OK:    false,
			Error: fmt.Sprintf("query failed: %v", err),
		}
	}
	defer rows.Close()

	// Get column names
	columns, err := rows.Columns()
	if err != nil {
		return QueryResult{
			OK:    false,
			Error: fmt.Sprintf("failed to get columns: %v", err),
		}
	}

	// Fetch all rows
	var results []map[string]interface{}
	for rows.Next() {
		// Create a slice of interface{} to hold each column's value
		values := make([]interface{}, len(columns))
		valuePtrs := make([]interface{}, len(columns))
		for i := range values {
			valuePtrs[i] = &values[i]
		}

		// Scan the row
		if err := rows.Scan(valuePtrs...); err != nil {
			return QueryResult{
				OK:    false,
				Error: fmt.Sprintf("failed to scan row: %v", err),
			}
		}

		// Create a map for this row
		rowMap := make(map[string]interface{})
		for i, col := range columns {
			val := values[i]
			// Convert []byte to string for better JSON serialization
			if b, ok := val.([]byte); ok {
				rowMap[col] = string(b)
			} else {
				rowMap[col] = val
			}
		}
		results = append(results, rowMap)
	}

	if err := rows.Err(); err != nil {
		return QueryResult{
			OK:    false,
			Error: fmt.Sprintf("row iteration error: %v", err),
		}
	}

	return QueryResult{
		OK:       true,
		RowCount: len(results),
		Rows:     results,
	}
}

// parseDatabaseSpec converts a db spec (warehouse, queue, path:...) to a file path
func parseDatabaseSpec(spec string) (string, error) {
	if spec == "" || spec == "warehouse" {
		// Default to warehouse - use macOS Application Support
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("failed to get home directory: %w", err)
		}
		return filepath.Join(home, "Library", "Application Support", "Eve", "eve.db"), nil
	}

	if spec == "queue" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("failed to get home directory: %w", err)
		}
		return filepath.Join(home, "Library", "Application Support", "Eve", "eve-queue.db"), nil
	}

	if strings.HasPrefix(spec, "path:") {
		path := strings.TrimPrefix(spec, "path:")
		if !filepath.IsAbs(path) {
			return "", fmt.Errorf("path must be absolute: %s", path)
		}
		return path, nil
	}

	return "", fmt.Errorf("invalid database spec: %s (expected warehouse, queue, or path:/abs/path)", spec)
}

// checkSQLSafety validates that SQL is read-only (SELECT or WITH only)
func checkSQLSafety(sqlQuery string) error {
	// Trim whitespace and convert to uppercase for checking
	trimmed := strings.TrimSpace(sqlQuery)
	upper := strings.ToUpper(trimmed)

	// Allow SELECT and WITH statements
	if strings.HasPrefix(upper, "SELECT") || strings.HasPrefix(upper, "WITH") {
		return nil
	}

	// Block all other statements
	return fmt.Errorf("only SELECT and WITH statements are allowed without --write flag")
}
