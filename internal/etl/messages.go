package etl

import (
	"database/sql"
	"fmt"
	"time"
)

// Message represents a message from chat.db
type Message struct {
	ROWID                 int64
	GUID                  string
	Text                  sql.NullString
	AttributedBody        []byte
	HandleID              sql.NullInt64
	Date                  int64 // Apple timestamp (nanoseconds since 2001-01-01)
	IsFromMe              bool
	MessageType           int
	ServiceName           sql.NullString
	AssociatedMessageGUID sql.NullString
	ReplyToGUID           sql.NullString
	ChatID                int64  // Source chat ROWID from chat_message_join
	ChatIdentifier        string // From chat.chat_identifier (not unique in chat.db)
}

// SyncMessages copies messages from chat.db to messages table in eve.db
// Supports incremental sync via sinceRowID watermark
// Returns the number of messages synced
func SyncMessages(chatDB *ChatDB, warehouseDB *sql.DB, sinceRowID int64) (int, error) {
	// Read messages from chat.db (incremental if sinceRowID > 0)
	messages, err := chatDB.GetMessages(sinceRowID)
	if err != nil {
		return 0, fmt.Errorf("failed to read messages: %w", err)
	}

	if len(messages) == 0 {
		return 0, nil
	}

	// Begin transaction for atomic writes
	tx, err := warehouseDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	handleMap, err := loadWarehouseHandleMap(tx, chatDB)
	if err != nil {
		return 0, err
	}

	// Insert messages
	for _, msg := range messages {
		if err := insertMessage(tx, &msg, handleMap); err != nil {
			return 0, fmt.Errorf("failed to insert message %d: %w", msg.ROWID, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("failed to commit transaction: %w", err)
	}

	return len(messages), nil
}

// GetMessages reads messages from chat.db with optional watermark
// Messages are joined with chat_message_join to get the chat_id
func (c *ChatDB) GetMessages(sinceRowID int64) ([]Message, error) {
	query := `
		SELECT
			m.ROWID,
			m.guid,
			m.text,
			m.attributedBody,
			m.handle_id,
			m.date,
			m.is_from_me,
			m.type,
			m.service,
			m.associated_message_guid,
			m.reply_to_guid,
			cmj.chat_id,
			c.chat_identifier
		FROM message m
		INNER JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
		INNER JOIN chat c ON c.ROWID = cmj.chat_id
		WHERE m.ROWID > ?
		  AND (m.type < 2000 OR m.type > 2005 OR m.type IS NULL)
		  AND NOT (
		    -- Exclude modern text-based reactions (Loved, Liked, etc.)
		    m.type = 0
		    AND m.associated_message_guid IS NOT NULL
		    AND m.associated_message_guid != ''
		    AND m.text IS NOT NULL
		    AND m.text != ''
		    AND (
		      m.text LIKE 'Loved %' OR
		      m.text LIKE 'Liked %' OR
		      m.text LIKE 'Disliked %' OR
		      m.text LIKE 'Laughed at %' OR
		      m.text LIKE 'Emphasized %' OR
		      m.text LIKE 'Questioned %'
		    )
		  )
		  AND (m.group_action_type IS NULL OR m.group_action_type = 0)
		ORDER BY m.ROWID
	`

	rows, err := c.db.Query(query, sinceRowID)
	if err != nil {
		return nil, fmt.Errorf("failed to query messages: %w", err)
	}
	defer rows.Close()

	var messages []Message
	for rows.Next() {
		var msg Message
		if err := rows.Scan(
			&msg.ROWID,
			&msg.GUID,
			&msg.Text,
			&msg.AttributedBody,
			&msg.HandleID,
			&msg.Date,
			&msg.IsFromMe,
			&msg.MessageType,
			&msg.ServiceName,
			&msg.AssociatedMessageGUID,
			&msg.ReplyToGUID,
			&msg.ChatID,
			&msg.ChatIdentifier,
		); err != nil {
			return nil, fmt.Errorf("failed to scan message: %w", err)
		}
		messages = append(messages, msg)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating messages: %w", err)
	}

	return messages, nil
}

// insertMessage inserts a message into the messages table
// Converts Apple timestamp to Unix timestamp
// Maps handle_id to sender_id (contact foreign key)
func insertMessage(tx *sql.Tx, msg *Message, handleMap map[int64]int64) error {
	// Convert Apple timestamp to Go time
	// Apple epoch: 2001-01-01 00:00:00 UTC
	appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
	timestamp := appleEpoch.Add(time.Duration(msg.Date) * time.Nanosecond)

	// Extract nullable fields
	content := ""
	if msg.Text.Valid {
		content = msg.Text.String
	}
	if content == "" && len(msg.AttributedBody) > 0 {
		content = decodeAttributedBody(msg.AttributedBody)
	}
	content = cleanMessageContent(content)

	var senderID *int64
	if msg.HandleID.Valid && msg.HandleID.Int64 > 0 {
		if contactID, ok := handleMap[msg.HandleID.Int64]; ok {
			senderID = &contactID
		}
	}

	serviceName := ""
	if msg.ServiceName.Valid {
		serviceName = msg.ServiceName.String
	}

	var associatedMessageGUID *string
	if msg.AssociatedMessageGUID.Valid && msg.AssociatedMessageGUID.String != "" {
		associatedMessageGUID = &msg.AssociatedMessageGUID.String
	}

	var replyToGUID *string
	if msg.ReplyToGUID.Valid && msg.ReplyToGUID.String != "" {
		replyToGUID = &msg.ReplyToGUID.String
	}

	// IMPORTANT:
	// chat.db can contain multiple chat rows with the same chat_identifier.
	// Eve's warehouse schema currently enforces chats.chat_identifier as UNIQUE, which
	// means only one "canonical" chat row exists in the warehouse for that identifier.
	//
	// Therefore, we must map source chat ROWID -> canonical warehouse chats.id
	// via chat_identifier; otherwise messages can reference non-existent chats rows.
	var warehouseChatID int64
	if err := tx.QueryRow(`SELECT id FROM chats WHERE chat_identifier = ?`, msg.ChatIdentifier).Scan(&warehouseChatID); err != nil {
		return fmt.Errorf("failed to map chat_identifier to warehouse chat id (chat_identifier=%q): %w", msg.ChatIdentifier, err)
	}

	// Insert into messages table
	// Idempotent via guid UNIQUE constraint
	query := `
		INSERT INTO messages (
			chat_id,
			sender_id,
			content,
			timestamp,
			is_from_me,
			message_type,
			service_name,
			guid,
			associated_message_guid,
			reply_to_guid
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(guid) DO UPDATE SET
			chat_id = excluded.chat_id,
			sender_id = excluded.sender_id,
			content = excluded.content,
			timestamp = excluded.timestamp,
			is_from_me = excluded.is_from_me,
			message_type = excluded.message_type,
			service_name = excluded.service_name,
			associated_message_guid = excluded.associated_message_guid,
			reply_to_guid = excluded.reply_to_guid
	`

	if _, err := tx.Exec(query,
		warehouseChatID,
		senderID,
		content,
		timestamp,
		msg.IsFromMe,
		msg.MessageType,
		serviceName,
		msg.GUID,
		associatedMessageGUID,
		replyToGUID,
	); err != nil {
		return fmt.Errorf("failed to insert message: %w", err)
	}

	return nil
}
