package etl

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	_ "github.com/mattn/go-sqlite3"
)

type addressBookContact struct {
	Name       string
	Identifier string
	Type       string // "phone" | "email"
}

func findLiveAddressBooks() ([]string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	root := filepath.Join(home, "Library", "Application Support", "AddressBook")

	var dbs []string
	seen := map[string]struct{}{}

	// Walk the AddressBook directory and pick up all AddressBook-v22.abcddb files.
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return nil
		}
		if d == nil || d.IsDir() {
			return nil
		}
		if d.Name() != "AddressBook-v22.abcddb" {
			return nil
		}
		if _, ok := seen[path]; ok {
			return nil
		}
		seen[path] = struct{}{}
		dbs = append(dbs, path)
		return nil
	})

	return dbs, nil
}

func extractContactsFromAddressBook(dbPath string) ([]addressBookContact, error) {
	conn, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	// Minimal table existence check (matches ChatStats requirements).
	tableExists := func(name string) bool {
		var n string
		err := conn.QueryRow("SELECT name FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1", name).Scan(&n)
		return err == nil && n == name
	}

	if !tableExists("ZABCDRECORD") {
		return nil, nil
	}

	// Pull phones and messaging addresses (emails + iMessage addresses).
	// Note: this intentionally mirrors ChatStats' union query.
	query := `
		SELECT r.ZFIRSTNAME, r.ZLASTNAME, p.ZFULLNUMBER AS identifier
		FROM ZABCDRECORD r
		LEFT JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
		WHERE p.ZFULLNUMBER IS NOT NULL
		UNION
		SELECT r.ZFIRSTNAME, r.ZLASTNAME, m.ZADDRESS AS identifier
		FROM ZABCDRECORD r
		LEFT JOIN ZABCDMESSAGINGADDRESS m ON m.ZOWNER = r.Z_PK
		WHERE m.ZADDRESS IS NOT NULL
	`

	rows, err := conn.Query(query)
	if err != nil {
		// Some AddressBook variants may not have the messaging table; fail soft.
		return nil, nil
	}
	defer rows.Close()

	var out []addressBookContact
	for rows.Next() {
		var first sql.NullString
		var last sql.NullString
		var ident sql.NullString
		if err := rows.Scan(&first, &last, &ident); err != nil {
			return nil, err
		}
		if !ident.Valid {
			continue
		}

		name := cleanContactName(strings.TrimSpace(strings.TrimSpace(first.String) + " " + strings.TrimSpace(last.String)))
		identifier := strings.TrimSpace(ident.String)
		if identifier == "" {
			continue
		}

		// Skip system/carrier contacts (ChatStats heuristic)
		if strings.HasPrefix(name, "#") ||
			strings.HasPrefix(identifier, "#") ||
			strings.Contains(name, "VZ") ||
			strings.Contains(name, "Roadside") ||
			strings.Contains(name, "Assistance") ||
			strings.HasPrefix(name, "*") ||
			strings.HasPrefix(identifier, "*") {
			continue
		}

		norm, typ := normalizeIdentifier(identifier)
		if norm == "" {
			continue
		}
		if name == "" {
			name = norm
		}

		out = append(out, addressBookContact{
			Name:       name,
			Identifier: norm,
			Type:       typ,
		})
	}
	return out, rows.Err()
}

func cleanContactName(name string) string {
	parts := strings.Fields(name)
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if strings.EqualFold(p, "none") {
			continue
		}
		out = append(out, p)
	}
	return strings.Join(out, " ")
}

// hydrateContactNamesFromAddressBooks updates contacts.name for any contact identifiers that match
// AddressBook identifiers. This is how we resolve \"Other\" into real names.
func hydrateContactNamesFromAddressBooks(warehouseDB *sql.DB, dbPaths []string) (int, error) {
	updated := 0

	// Prepared statements for speed
	findStmt, err := warehouseDB.Prepare(`
		SELECT c.id, COALESCE(c.name, '')
		FROM contacts c
		JOIN contact_identifiers ci ON c.id = ci.contact_id
		WHERE ci.identifier = ? AND ci.type = ?
		LIMIT 1
	`)
	if err != nil {
		return 0, err
	}
	defer findStmt.Close()

	updateStmt, err := warehouseDB.Prepare(`
		UPDATE contacts
		SET name = ?, data_source = COALESCE(data_source, 'live_addressbook'), last_updated = CURRENT_TIMESTAMP
		WHERE id = ?
	`)
	if err != nil {
		return 0, err
	}
	defer updateStmt.Close()

	for _, dbPath := range dbPaths {
		contacts, err := extractContactsFromAddressBook(dbPath)
		if err != nil {
			// Fail soft on per-source errors; keep going.
			continue
		}
		for _, c := range contacts {
			var contactID int64
			var existingName string
			if err := findStmt.QueryRow(c.Identifier, c.Type).Scan(&contactID, &existingName); err != nil {
				continue
			}

			if !nameNeedsUpdate(existingName, c.Identifier) {
				continue
			}
			if c.Name == "" || c.Name == existingName {
				continue
			}

			if _, err := updateStmt.Exec(c.Name, contactID); err != nil {
				return updated, fmt.Errorf("failed to update contact name: %w", err)
			}
			updated++
		}
	}

	return updated, nil
}

func nameNeedsUpdate(existingName string, identifier string) bool {
	existingName = strings.TrimSpace(existingName)
	if existingName == "" {
		return true
	}
	if existingName == identifier {
		return true
	}
	// Looks like a phone number? (strip punctuation and check digits)
	clean := existingName
	replacer := strings.NewReplacer("+", "", "-", "", " ", "", "(", "", ")", "")
	clean = replacer.Replace(clean)
	allDigits := clean != "" && isAllDigits(clean)
	return allDigits
}

func isAllDigits(s string) bool {
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// HydrateContactNamesFromAddressBook is the public entrypoint used by ETL.
func HydrateContactNamesFromAddressBook(warehouseDB *sql.DB) (int, error) {
	paths, err := findLiveAddressBooks()
	if err != nil {
		return 0, err
	}
	return hydrateContactNamesFromAddressBooks(warehouseDB, paths)
}

