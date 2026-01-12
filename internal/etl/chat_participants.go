package etl

import (
	"database/sql"
	"fmt"
)

// ChatParticipant represents a (chat_identifier, handle_id) link from chat.db.
type ChatParticipant struct {
	ChatIdentifier string
	HandleID       int64
}

// SyncChatParticipants populates the warehouse chat_participants table.
//
// Why this exists:
// - chat.db supports chat_handle_join mapping chat ROWIDs to handle ROWIDs.
// - Eve's warehouse schema includes chat_participants, and higher-level query code expects it.
// - chat.chat_identifier is NOT unique in chat.db (multiple chat rows can share an identifier),
//   but the warehouse schema currently treats chat_identifier as UNIQUE.
//   So we map participants by chat_identifier -> canonical warehouse chats.id.
//
// Note: contacts.id in the warehouse is the chat.db handle ROWID (see SyncHandles).
func SyncChatParticipants(chatDB *ChatDB, warehouseDB *sql.DB) (int, error) {
	participants, err := chatDB.GetChatParticipants()
	if err != nil {
		return 0, fmt.Errorf("failed to read chat participants: %w", err)
	}
	if len(participants) == 0 {
		return 0, nil
	}

	tx, err := warehouseDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Build chat_identifier -> warehouse chat id mapping
	chatMap, err := buildChatIdentifierMap(tx)
	if err != nil {
		return 0, fmt.Errorf("failed to build chat map: %w", err)
	}

	synced := 0
	for _, p := range participants {
		warehouseChatID, ok := chatMap[p.ChatIdentifier]
		if !ok {
			// Chat wasn't loaded; should be rare if SyncChats ran
			continue
		}
		if _, err := tx.Exec(
			`INSERT OR IGNORE INTO chat_participants (chat_id, contact_id) VALUES (?, ?)`,
			warehouseChatID,
			p.HandleID,
		); err != nil {
			return 0, fmt.Errorf("failed to insert chat_participant (chat=%d, contact=%d): %w", warehouseChatID, p.HandleID, err)
		}
		synced++
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("failed to commit transaction: %w", err)
	}
	return synced, nil
}

// GetChatParticipants extracts (chat_identifier, handle_id) from chat.db.
func (c *ChatDB) GetChatParticipants() ([]ChatParticipant, error) {
	query := `
		SELECT ch.chat_identifier, chj.handle_id
		FROM chat_handle_join chj
		JOIN chat ch ON ch.ROWID = chj.chat_id
		WHERE ch.chat_identifier IS NOT NULL AND ch.chat_identifier != ''
		ORDER BY ch.chat_identifier, chj.handle_id
	`

	rows, err := c.db.Query(query)
	if err != nil {
		return nil, fmt.Errorf("failed to query chat participants: %w", err)
	}
	defer rows.Close()

	var out []ChatParticipant
	for rows.Next() {
		var p ChatParticipant
		if err := rows.Scan(&p.ChatIdentifier, &p.HandleID); err != nil {
			return nil, fmt.Errorf("failed to scan chat participant: %w", err)
		}
		out = append(out, p)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating chat participants: %w", err)
	}
	return out, nil
}

func buildChatIdentifierMap(tx *sql.Tx) (map[string]int64, error) {
	rows, err := tx.Query(`SELECT chat_identifier, id FROM chats`)
	if err != nil {
		return nil, fmt.Errorf("failed to query chats: %w", err)
	}
	defer rows.Close()

	m := make(map[string]int64)
	for rows.Next() {
		var ident string
		var id int64
		if err := rows.Scan(&ident, &id); err != nil {
			return nil, fmt.Errorf("failed to scan chat map: %w", err)
		}
		// chat_identifier is unique in warehouse, so 1:1
		m[ident] = id
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating chat map: %w", err)
	}
	return m, nil
}

