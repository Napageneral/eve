package db

import (
	"database/sql"
	"fmt"
	"time"

	"github.com/tylerchilds/eve/internal/encoding"
)

// ConversationReader reads conversations and their messages from the warehouse
type ConversationReader struct {
	db *sql.DB
}

// NewConversationReader creates a new conversation reader
func NewConversationReader(db *sql.DB) *ConversationReader {
	return &ConversationReader{db: db}
}

// GetConversation retrieves a conversation with all its messages, attachments, and reactions
func (r *ConversationReader) GetConversation(conversationID int) (*encoding.Conversation, error) {
	// Get conversation metadata
	var conv encoding.Conversation
	err := r.db.QueryRow(`
		SELECT id, chat_id, start_time, end_time
		FROM conversations
		WHERE id = ?
	`, conversationID).Scan(
		&conv.ID,
		&conv.ChatID,
		&conv.StartTime,
		&conv.EndTime,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to get conversation: %w", err)
	}

	// Get messages for this conversation
	messages, err := r.getMessages(conversationID)
	if err != nil {
		return nil, fmt.Errorf("failed to get messages: %w", err)
	}

	conv.Messages = messages
	return &conv, nil
}

// getMessages retrieves all messages for a conversation with their attachments and reactions
func (r *ConversationReader) getMessages(conversationID int) ([]encoding.Message, error) {
	rows, err := r.db.Query(`
		SELECT
			m.id, m.guid, m.timestamp, m.sender_id, m.content, m.is_from_me,
			m.conversation_id, m.chat_id,
			COALESCE(c.name, c.nickname, '') as sender_name
		FROM messages m
		LEFT JOIN contacts c ON m.sender_id = c.id
		WHERE m.conversation_id = ?
		ORDER BY m.timestamp
	`, conversationID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var messages []encoding.Message
	for rows.Next() {
		var msg encoding.Message
		var senderID sql.NullInt64
		var content sql.NullString
		var timestamp string

		err := rows.Scan(
			&msg.ID,
			&msg.GUID,
			&timestamp,
			&senderID,
			&content,
			&msg.IsFromMe,
			&msg.ConversationID,
			&msg.ChatID,
			&msg.SenderName,
		)
		if err != nil {
			return nil, err
		}

		// Parse timestamp
		msg.Timestamp, err = parseTimestamp(timestamp)
		if err != nil {
			return nil, fmt.Errorf("failed to parse timestamp: %w", err)
		}

		if senderID.Valid {
			id := int(senderID.Int64)
			msg.SenderID = &id
		}

		if content.Valid {
			msg.Content = content.String
		}

		// Get attachments for this message
		attachments, err := r.getAttachments(msg.ID)
		if err != nil {
			return nil, fmt.Errorf("failed to get attachments for message %d: %w", msg.ID, err)
		}
		msg.Attachments = attachments

		// Get reactions for this message
		reactions, err := r.getReactions(msg.GUID)
		if err != nil {
			return nil, fmt.Errorf("failed to get reactions for message %s: %w", msg.GUID, err)
		}
		msg.Reactions = reactions

		messages = append(messages, msg)
	}

	return messages, rows.Err()
}

// getAttachments retrieves all attachments for a message
func (r *ConversationReader) getAttachments(messageID int) ([]encoding.Attachment, error) {
	rows, err := r.db.Query(`
		SELECT id, mime_type, file_name, is_sticker
		FROM attachments
		WHERE message_id = ?
	`, messageID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var attachments []encoding.Attachment
	for rows.Next() {
		var att encoding.Attachment
		var mimeType, fileName sql.NullString

		err := rows.Scan(
			&att.ID,
			&mimeType,
			&fileName,
			&att.IsSticker,
		)
		if err != nil {
			return nil, err
		}

		if mimeType.Valid {
			att.MimeType = mimeType.String
		}
		if fileName.Valid {
			att.FileName = fileName.String
		}

		attachments = append(attachments, att)
	}

	return attachments, rows.Err()
}

// getReactions retrieves all reactions for a message
func (r *ConversationReader) getReactions(messageGUID string) ([]encoding.Reaction, error) {
	rows, err := r.db.Query(`
		SELECT
			r.reaction_type, r.sender_id, r.is_from_me,
			COALESCE(c.name, c.nickname, '') as sender_name
		FROM reactions r
		LEFT JOIN contacts c ON r.sender_id = c.id
		WHERE r.original_message_guid = ?
	`, messageGUID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var reactions []encoding.Reaction
	for rows.Next() {
		var react encoding.Reaction
		var senderID sql.NullInt64
		var isFromMe sql.NullBool

		err := rows.Scan(
			&react.ReactionType,
			&senderID,
			&isFromMe,
			&react.SenderName,
		)
		if err != nil {
			return nil, err
		}

		if senderID.Valid {
			react.SenderID = int(senderID.Int64)
		}
		if isFromMe.Valid {
			react.IsFromMe = isFromMe.Bool
		}

		reactions = append(reactions, react)
	}

	return reactions, rows.Err()
}

// parseTimestamp parses SQLite timestamp formats
func parseTimestamp(value string) (time.Time, error) {
	// Try common SQLite timestamp formats
	formats := []string{
		time.RFC3339,
		"2006-01-02 15:04:05",
		"2006-01-02T15:04:05",
		"2006-01-02 15:04:05.999999999",
		"2006-01-02T15:04:05.999999999",
	}

	for _, format := range formats {
		t, err := time.Parse(format, value)
		if err == nil {
			return t, nil
		}
	}

	return time.Time{}, fmt.Errorf("unable to parse timestamp: %s", value)
}
