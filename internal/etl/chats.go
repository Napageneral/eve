package etl

import (
	"database/sql"
	"fmt"
)

// Chat represents a chat from chat.db
type Chat struct {
	ROWID          int64
	ChatIdentifier string
	DisplayName    sql.NullString
	ServiceName    sql.NullString
	Style          int // 43 = group chat, 45 = 1:1
}

// SyncChats copies chats from chat.db to chats table in eve.db
// Returns the number of chats synced
func SyncChats(chatDB *ChatDB, warehouseDB *sql.DB) (int, error) {
	// Read all chats from chat.db
	chats, err := chatDB.GetChats()
	if err != nil {
		return 0, fmt.Errorf("failed to read chats: %w", err)
	}

	// Begin transaction for atomic writes
	tx, err := warehouseDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Insert chats
	for _, chat := range chats {
		if err := insertChat(tx, &chat); err != nil {
			return 0, fmt.Errorf("failed to insert chat %d: %w", chat.ROWID, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("failed to commit transaction: %w", err)
	}

	return len(chats), nil
}

// GetChats reads all chats from chat.db
func (c *ChatDB) GetChats() ([]Chat, error) {
	query := `
		SELECT ROWID, chat_identifier, display_name, service_name, style
		FROM chat
		ORDER BY ROWID
	`

	rows, err := c.db.Query(query)
	if err != nil {
		return nil, fmt.Errorf("failed to query chats: %w", err)
	}
	defer rows.Close()

	var chats []Chat
	for rows.Next() {
		var ch Chat
		if err := rows.Scan(&ch.ROWID, &ch.ChatIdentifier, &ch.DisplayName, &ch.ServiceName, &ch.Style); err != nil {
			return nil, fmt.Errorf("failed to scan chat: %w", err)
		}
		chats = append(chats, ch)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating chats: %w", err)
	}

	return chats, nil
}

// insertChat inserts a chat into the chats table
// Uses the chat ROWID as the chat id for foreign key consistency
func insertChat(tx *sql.Tx, chat *Chat) error {
	// Determine if this is a group chat
	// In iMessage, style = 43 is group chat, style = 45 is 1:1
	isGroup := chat.Style == 43

	// Get nullable values
	chatName := ""
	if chat.DisplayName.Valid {
		chatName = chat.DisplayName.String
	}

	serviceName := ""
	if chat.ServiceName.Valid {
		serviceName = chat.ServiceName.String
	}

	// Insert into chats table
	// Idempotent via chat_identifier UNIQUE constraint
	query := `
		INSERT INTO chats (id, chat_identifier, chat_name, service_name, is_group, created_date)
		VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
		ON CONFLICT(chat_identifier) DO UPDATE SET
			chat_name = excluded.chat_name,
			service_name = excluded.service_name,
			is_group = excluded.is_group
	`

	if _, err := tx.Exec(query, chat.ROWID, chat.ChatIdentifier, chatName, serviceName, isGroup); err != nil {
		return fmt.Errorf("failed to insert chat: %w", err)
	}

	return nil
}
