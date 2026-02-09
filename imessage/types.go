// Package imessage provides direct read-only access to Apple's iMessage chat.db.
package imessage

import "database/sql"

// ChatDB provides read-only access to Apple's chat.db
type ChatDB struct {
	db   *sql.DB
	path string
}

// Handle represents a contact handle from chat.db
type Handle struct {
	ROWID int64
	ID    string // phone number or email
}

// Chat represents a chat from chat.db
type Chat struct {
	ROWID          int64
	ChatIdentifier string
	DisplayName    sql.NullString
	ServiceName    sql.NullString
	Style          int // 43 = group chat, 45 = 1:1
}

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
	ChatID                int64
	ChatIdentifier        string
	GroupActionType       sql.NullInt64
	OtherHandleID         sql.NullInt64
	GroupTitle            sql.NullString
	ItemType              sql.NullInt64
	MessageActionType     sql.NullInt64
}

// Attachment represents an attachment from chat.db
type Attachment struct {
	ROWID       int64
	GUID        string
	CreatedDate int64 // Apple timestamp
	Filename    sql.NullString
	UTI         sql.NullString
	MimeType    sql.NullString
	TotalBytes  sql.NullInt64
	IsSticker   bool
	MessageGUID string
}

// Reaction represents a reaction extracted from chat.db messages
type Reaction struct {
	ROWID                 int64
	GUID                  string
	AssociatedMessageGUID string
	HandleID              sql.NullInt64
	Date                  int64
	IsFromMe              bool
	ReactionType          int // 2000-2005 (legacy) or 0 (modern text-based)
	Text                  sql.NullString // "Loved ...", "Liked ...", etc. (modern format)
	ChatID                int64
	ChatIdentifier        string
}

// GroupAction represents a group membership change from chat.db
type GroupAction struct {
	GUID              string
	ChatIdentifier    string
	OtherHandleID     sql.NullInt64
	ActionType        int
	ItemType          sql.NullInt64
	MessageActionType sql.NullInt64
	GroupTitle        sql.NullString
	Date              int64
	IsFromMe          bool
}

// ChatParticipant represents a (chat_identifier, handle_id) link
type ChatParticipant struct {
	ChatIdentifier string
	HandleID       int64
}
