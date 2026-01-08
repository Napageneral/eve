package etl

import (
	"database/sql"
	"fmt"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// Watermark represents a sync progress marker
type Watermark struct {
	Source    string
	Name      string
	ValueInt  sql.NullInt64
	ValueText sql.NullString
	UpdatedTS int64
}

// GetWatermark retrieves a watermark from the warehouse DB
func GetWatermark(db *sql.DB, source, name string) (*Watermark, error) {
	query := `
		SELECT source, name, value_int, value_text, updated_ts
		FROM watermarks
		WHERE source = ? AND name = ?
	`

	var wm Watermark
	err := db.QueryRow(query, source, name).Scan(
		&wm.Source,
		&wm.Name,
		&wm.ValueInt,
		&wm.ValueText,
		&wm.UpdatedTS,
	)

	if err == sql.ErrNoRows {
		// No watermark exists yet
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("failed to get watermark: %w", err)
	}

	return &wm, nil
}

// SetWatermark upserts a watermark in the warehouse DB
func SetWatermark(db *sql.DB, source, name string, valueInt *int64, valueText *string) error {
	now := time.Now().Unix()

	query := `
		INSERT INTO watermarks (source, name, value_int, value_text, updated_ts)
		VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(source, name) DO UPDATE SET
			value_int = excluded.value_int,
			value_text = excluded.value_text,
			updated_ts = excluded.updated_ts
	`

	args := []interface{}{source, name}

	if valueInt != nil {
		args = append(args, *valueInt)
	} else {
		args = append(args, nil)
	}

	if valueText != nil {
		args = append(args, *valueText)
	} else {
		args = append(args, nil)
	}

	args = append(args, now)

	_, err := db.Exec(query, args...)
	if err != nil {
		return fmt.Errorf("failed to set watermark: %w", err)
	}

	return nil
}
