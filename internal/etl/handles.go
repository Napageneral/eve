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

// isNumericOrEmpty checks if a name is empty, numeric-only, or just the identifier itself
func isNumericOrEmpty(name string, identifier string) bool {
	if name == "" {
		return true
	}
	// Check if name is just digits (numeric)
	allDigits := true
	for _, r := range name {
		if r < '0' || r > '9' {
			allDigits = false
			break
		}
	}
	if allDigits && len(name) > 0 {
		return true
	}
	// Check if name is the same as the identifier (not a real name)
	if name == identifier {
		return true
	}
	return false
}

// insertHandle inserts a handle into contacts and contact_identifiers
// Uses normalization and deduplication: looks up existing contact by normalized identifier
// before creating a new one. Reuses contact_id if found and updates name if needed.
func insertHandle(tx *sql.Tx, handle *Handle) error {
	normalized, identifierType := normalizeIdentifier(handle.ID)
	if normalized == "" {
		// Skip empty identifiers
		return nil
	}

	// Lookup existing contact by normalized identifier
	var existingContactID sql.NullInt64
	var existingName sql.NullString
	lookupQuery := `
		SELECT ci.contact_id, c.name
		FROM contact_identifiers ci
		JOIN contacts c ON ci.contact_id = c.id
		WHERE ci.identifier = ? AND ci.type = ?
		LIMIT 1
	`
	err := tx.QueryRow(lookupQuery, normalized, identifierType).Scan(&existingContactID, &existingName)

	var contactID int64
	var shouldUpdateName bool
	var newName string

	if err == sql.ErrNoRows {
		// No existing contact found, create new one
		contactID = handle.ROWID
		newName = normalized // Default to normalized identifier
		shouldUpdateName = false
	} else if err != nil {
		return fmt.Errorf("failed to lookup existing contact: %w", err)
	} else {
		// Found existing contact, reuse it
		contactID = existingContactID.Int64
		existingNameStr := ""
		if existingName.Valid {
			existingNameStr = existingName.String
		}

		// Determine if we should update the name
		// Update if existing name is empty/numeric and new handle has a better name
		// For now, we'll use the normalized identifier as the name (chat.db doesn't provide names)
		// But we'll check if existing name is numeric/empty and update it
		if isNumericOrEmpty(existingNameStr, normalized) {
			shouldUpdateName = true
			newName = normalized
		} else {
			shouldUpdateName = false
			newName = existingNameStr
		}
	}

	// Insert or update contact
	if err == sql.ErrNoRows {
		// New contact - insert
		contactQuery := `
			INSERT INTO contacts (id, name, data_source, last_updated)
			VALUES (?, ?, 'chat.db', CURRENT_TIMESTAMP)
			ON CONFLICT(id) DO UPDATE SET
				name = CASE
					WHEN contacts.name IS NULL OR contacts.name = '' OR 
					     (SELECT 1 FROM (SELECT 1) WHERE contacts.name GLOB '[0-9]*' AND contacts.name NOT GLOB '*[^0-9]*') THEN excluded.name
					ELSE contacts.name
				END,
				last_updated = CURRENT_TIMESTAMP
		`
		if _, err := tx.Exec(contactQuery, contactID, newName); err != nil {
			return fmt.Errorf("failed to insert contact: %w", err)
		}
	} else if shouldUpdateName {
		// Existing contact - update name if needed
		updateQuery := `
			UPDATE contacts
			SET name = ?, last_updated = CURRENT_TIMESTAMP
			WHERE id = ?
		`
		if _, err := tx.Exec(updateQuery, newName, contactID); err != nil {
			return fmt.Errorf("failed to update contact name: %w", err)
		}
	}

	// Insert into contact_identifiers table
	// Use ON CONFLICT to handle UNIQUE(identifier, type) constraint gracefully
	// If identifier already exists, update last_used and ensure contact_id matches
	identifierQuery := `
		INSERT INTO contact_identifiers (contact_id, identifier, type, is_primary, last_used)
		VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
		ON CONFLICT(identifier, type) DO UPDATE SET
			contact_id = excluded.contact_id,
			last_used = CURRENT_TIMESTAMP
	`
	if _, err := tx.Exec(identifierQuery, contactID, normalized, identifierType); err != nil {
		return fmt.Errorf("failed to insert/update contact_identifier: %w", err)
	}

	return nil
}

// insertHandleLegacy inserts a handle into contacts and contact_identifiers
// Uses the handle ROWID as the contact_id for foreign key consistency
func insertHandleLegacy(tx *sql.Tx, handle *Handle) error {
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
