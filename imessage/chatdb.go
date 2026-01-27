package imessage

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "github.com/mattn/go-sqlite3"
)

// GetChatDBPath returns the path to the macOS Messages chat.db
func GetChatDBPath() string {
	// Check for env override first
	if override := os.Getenv("EVE_SOURCE_CHAT_DB"); override != "" {
		return os.ExpandEnv(override)
	}
	if override := os.Getenv("CHATSTATS_SOURCE_CHAT_DB"); override != "" {
		return os.ExpandEnv(override)
	}

	// Default macOS location
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, "Library", "Messages", "chat.db")
}

// OpenChatDB opens the chat.db with read-only optimized pragmas
func OpenChatDB(path string) (*ChatDB, error) {
	// Check if file exists
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil, fmt.Errorf("chat.db not found at %s", path)
	}

	// Open with read-only URI mode
	// Note: Don't use immutable=1 for live macOS Messages DB (uses WAL)
	uri := fmt.Sprintf("file:%s?mode=ro", path)
	db, err := sql.Open("sqlite3", uri)
	if err != nil {
		return nil, fmt.Errorf("failed to open chat.db: %w", err)
	}

	// Set read-only pragmas for performance
	pragmas := []string{
		"PRAGMA query_only=ON",
		"PRAGMA synchronous=OFF",
		"PRAGMA journal_mode=OFF",
		"PRAGMA temp_store=MEMORY",
		"PRAGMA cache_size=-262144",  // 256MB cache
		"PRAGMA mmap_size=268435456", // 256MB memory map
	}

	for _, pragma := range pragmas {
		if _, err := db.Exec(pragma); err != nil {
			// Ignore pragma errors (some may not be supported)
			continue
		}
	}

	return &ChatDB{db: db, path: path}, nil
}

// Close closes the chat.db connection
func (c *ChatDB) Close() error {
	if c.db != nil {
		return c.db.Close()
	}
	return nil
}

// Path returns the path to the chat.db file
func (c *ChatDB) Path() string {
	return c.path
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

// GetMessages reads messages from chat.db with optional watermark
// Excludes reactions (both legacy type 2000-2005 and modern text-based)
// but includes sticker replies and other attachment replies
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
			m.group_action_type,
			m.other_handle,
			m.group_title,
			m.item_type,
			m.message_action_type,
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
			&msg.GroupActionType,
			&msg.OtherHandleID,
			&msg.GroupTitle,
			&msg.ItemType,
			&msg.MessageActionType,
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

// GetReactions reads reaction messages from chat.db
// Reactions are stored differently across macOS/iOS versions:
// - Older: type 2000-2005 (love, like, dislike, laugh, emphasis, question)
// - Newer: type 0 with text starting with "Loved", "Liked", etc.
func (c *ChatDB) GetReactions(sinceRowID int64) ([]Reaction, error) {
	query := `
		SELECT
			m.ROWID,
			m.guid,
			m.associated_message_guid,
			m.handle_id,
			m.date,
			m.is_from_me,
			m.type,
			m.text,
			cmj.chat_id,
			c.chat_identifier
		FROM message m
		INNER JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
		INNER JOIN chat c ON c.ROWID = cmj.chat_id
		WHERE m.ROWID > ?
		  AND m.associated_message_guid IS NOT NULL
		  AND m.associated_message_guid != ''
		  AND (
		    -- Legacy format: type 2000-2005
		    (m.type >= 2000 AND m.type <= 2005)
		    OR
		    -- Modern format: type 0 with reaction text patterns
		    (m.type = 0 AND (
		      m.text LIKE 'Loved %' OR
		      m.text LIKE 'Liked %' OR
		      m.text LIKE 'Disliked %' OR
		      m.text LIKE 'Laughed at %' OR
		      m.text LIKE 'Emphasized %' OR
		      m.text LIKE 'Questioned %'
		    ))
		  )
		ORDER BY m.ROWID
	`

	rows, err := c.db.Query(query, sinceRowID)
	if err != nil {
		return nil, fmt.Errorf("failed to query reactions: %w", err)
	}
	defer rows.Close()

	var reactions []Reaction
	for rows.Next() {
		var r Reaction
		if err := rows.Scan(
			&r.ROWID,
			&r.GUID,
			&r.AssociatedMessageGUID,
			&r.HandleID,
			&r.Date,
			&r.IsFromMe,
			&r.ReactionType,
			&r.Text,
			&r.ChatID,
			&r.ChatIdentifier,
		); err != nil {
			return nil, fmt.Errorf("failed to scan reaction: %w", err)
		}
		reactions = append(reactions, r)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating reactions: %w", err)
	}

	return reactions, nil
}

// GetAttachments reads attachments from chat.db
// Only returns attachments for messages that have a chat association and will be synced as events
// (excludes attachments for type 1 messages with associated_message_guid - these are edits/stickers)
func (c *ChatDB) GetAttachments(sinceMessageRowID int64) ([]Attachment, error) {
	query := `
		SELECT
			a.ROWID,
			a.guid,
			a.created_date,
			a.filename,
			a.uti,
			a.mime_type,
			a.total_bytes,
			a.is_sticker,
			m.guid as message_guid
		FROM attachment a
		INNER JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
		INNER JOIN message m ON maj.message_id = m.ROWID
		INNER JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
		WHERE m.ROWID > ?
		  AND NOT (
		    m.type = 1 
		    AND m.associated_message_guid IS NOT NULL 
		    AND m.associated_message_guid != ''
		  )
		ORDER BY a.ROWID
	`

	rows, err := c.db.Query(query, sinceMessageRowID)
	if err != nil {
		return nil, fmt.Errorf("failed to query attachments: %w", err)
	}
	defer rows.Close()

	var attachments []Attachment
	for rows.Next() {
		var att Attachment
		if err := rows.Scan(
			&att.ROWID,
			&att.GUID,
			&att.CreatedDate,
			&att.Filename,
			&att.UTI,
			&att.MimeType,
			&att.TotalBytes,
			&att.IsSticker,
			&att.MessageGUID,
		); err != nil {
			return nil, fmt.Errorf("failed to scan attachment: %w", err)
		}
		attachments = append(attachments, att)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating attachments: %w", err)
	}

	return attachments, nil
}

// GetChatParticipants extracts (chat_identifier, handle_id) from chat.db
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

// GetMaxMessageRowID returns the maximum ROWID from the message table
func (c *ChatDB) GetMaxMessageRowID() (int64, error) {
	var maxRowID int64
	query := `SELECT COALESCE(MAX(ROWID), 0) FROM message`

	err := c.db.QueryRow(query).Scan(&maxRowID)
	if err != nil {
		return 0, fmt.Errorf("failed to query max message ROWID: %w", err)
	}

	return maxRowID, nil
}

// CountMessages returns message count statistics
func (c *ChatDB) CountMessages(sinceRowID int64) (total int, maxRowID int64, err error) {
	query := `
		SELECT COUNT(*), COALESCE(MAX(ROWID), 0)
		FROM message
		WHERE ROWID > ?
	`

	err = c.db.QueryRow(query, sinceRowID).Scan(&total, &maxRowID)
	if err != nil {
		return 0, 0, fmt.Errorf("failed to count messages: %w", err)
	}

	return total, maxRowID, nil
}
