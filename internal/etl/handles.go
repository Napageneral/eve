package etl

import (
	"database/sql"
	"fmt"
	"strings"
)

// Handle represents a contact from chat.db
type Handle struct {
	ROWID int64
	ID    string // phone number or email
}

// SyncHandles copies handles from chat.db to contacts + contact_identifiers in eve.db
// Returns the number of handles synced
func SyncHandles(chatDB *ChatDB, warehouseDB *sql.DB) (int, error) {
	// Read all handles from chat.db
	handles, err := chatDB.GetHandles()
	if err != nil {
		return 0, fmt.Errorf("failed to read handles: %w", err)
	}

	// Begin transaction for atomic writes
	tx, err := warehouseDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Insert handles into contacts and contact_identifiers
	for _, handle := range handles {
		if err := insertHandle(tx, &handle); err != nil {
			return 0, fmt.Errorf("failed to insert handle %d: %w", handle.ROWID, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("failed to commit transaction: %w", err)
	}

	return len(handles), nil
}

// GetHandles reads all handles from chat.db
func (c *ChatDB) GetHandles() ([]Handle, error) {
	query := `
		SELECT ROWID, id
		FROM handle
		ORDER BY ROWID
	`

	rows, err := c.db.Query(query)
	if err != nil {
		return nil, fmt.Errorf("failed to query handles: %w", err)
	}
	defer rows.Close()

	var handles []Handle
	for rows.Next() {
		var h Handle
		if err := rows.Scan(&h.ROWID, &h.ID); err != nil {
			return nil, fmt.Errorf("failed to scan handle: %w", err)
		}
		handles = append(handles, h)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating handles: %w", err)
	}

	return handles, nil
}

// insertHandle inserts a handle into contacts and contact_identifiers
// Uses the handle ROWID as the contact_id for foreign key consistency
func insertHandle(tx *sql.Tx, handle *Handle) error {
	normalized, identifierType := normalizeIdentifier(handle.ID)
	if normalized == "" {
		// Skip empty identifiers
		return nil
	}

	// Insert into contacts table
	// Use handle ROWID as contact id to maintain foreign key references
	// Idempotent: ON CONFLICT DO NOTHING since we're using explicit id
	contactQuery := `
		INSERT INTO contacts (id, name, data_source, last_updated)
		VALUES (?, ?, 'chat.db', CURRENT_TIMESTAMP)
		ON CONFLICT(id) DO UPDATE SET
			-- Keep an existing \"real\" name if present; otherwise default to the identifier
			name = CASE
			         WHEN contacts.name IS NULL OR contacts.name = '' THEN excluded.name
			         ELSE contacts.name
			       END,
			last_updated = CURRENT_TIMESTAMP
	`

	if _, err := tx.Exec(contactQuery, handle.ROWID, normalized); err != nil {
		return fmt.Errorf("failed to insert contact: %w", err)
	}

	// Insert into contact_identifiers table
	// Idempotent: INSERT OR IGNORE (requires unique constraint to be added to schema later)
	// For now, check if exists first
	var existingID int64
	checkQuery := `SELECT id FROM contact_identifiers WHERE contact_id = ? AND identifier = ?`
	err := tx.QueryRow(checkQuery, handle.ROWID, normalized).Scan(&existingID)

	if err == sql.ErrNoRows {
		// Doesn't exist, insert it
		identifierQuery := `
			INSERT INTO contact_identifiers (contact_id, identifier, type, is_primary, last_used)
			VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
		`
		if _, err := tx.Exec(identifierQuery, handle.ROWID, normalized, identifierType); err != nil {
			return fmt.Errorf("failed to insert contact_identifier: %w", err)
		}
	} else if err != nil {
		return fmt.Errorf("failed to check existing contact_identifier: %w", err)
	} else {
		// Exists, update last_used
		updateQuery := `UPDATE contact_identifiers SET last_used = CURRENT_TIMESTAMP WHERE id = ?`
		if _, err := tx.Exec(updateQuery, existingID); err != nil {
			return fmt.Errorf("failed to update contact_identifier: %w", err)
		}
	}

	return nil
}

// determineIdentifierType is kept for tests/compat, but normalizeIdentifier should be preferred.
// NOTE: This does NOT normalize; it only classifies.
func determineIdentifierType(identifier string) string {
	if strings.Contains(identifier, "@") {
		return "email"
	}
	return "phone"
}
