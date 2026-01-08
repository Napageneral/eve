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
	HandleID              sql.NullInt64
	Date                  int64 // Apple timestamp (nanoseconds since 2001-01-01)
	IsFromMe              bool
	MessageType           int
	ServiceName           sql.NullString
	AssociatedMessageGUID sql.NullString
	ReplyToGUID           sql.NullString
	ChatID                int64 // From chat_message_join
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

	// Insert messages
	for _, msg := range messages {
		if err := insertMessage(tx, &msg); err != nil {
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
			m.handle_id,
			m.date,
			m.is_from_me,
			m.type,
			m.service,
			m.associated_message_guid,
			m.reply_to_guid,
			cmj.chat_id
		FROM message m
		INNER JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
		WHERE m.ROWID > ?
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
			&msg.HandleID,
			&msg.Date,
			&msg.IsFromMe,
			&msg.MessageType,
			&msg.ServiceName,
			&msg.AssociatedMessageGUID,
			&msg.ReplyToGUID,
			&msg.ChatID,
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
func insertMessage(tx *sql.Tx, msg *Message) error {
	// Convert Apple timestamp to Go time
	// Apple epoch: 2001-01-01 00:00:00 UTC
	appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
	timestamp := appleEpoch.Add(time.Duration(msg.Date) * time.Nanosecond)

	// Extract nullable fields
	content := ""
	if msg.Text.Valid {
		content = msg.Text.String
	}

	var senderID *int64
	if msg.HandleID.Valid && msg.HandleID.Int64 > 0 {
		// handle_id from chat.db maps to contact_id in eve.db
		senderID = &msg.HandleID.Int64
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
			content = excluded.content,
			timestamp = excluded.timestamp,
			is_from_me = excluded.is_from_me,
			message_type = excluded.message_type,
			service_name = excluded.service_name,
			associated_message_guid = excluded.associated_message_guid,
			reply_to_guid = excluded.reply_to_guid
	`

	if _, err := tx.Exec(query,
		msg.ChatID,
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
