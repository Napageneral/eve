// Package imessage provides direct access to Apple's iMessage chat.db
// and sync capabilities to write into a Comms-compatible database schema.
package imessage

import (
	"database/sql"
	"time"
)

// SyncResult contains statistics from a sync operation
type SyncResult struct {
	HandlesSynced     int
	ChatsSynced       int
	MessagesSynced    int
	MembershipSynced  int
	AttachmentsSynced int
	ReactionsSynced   int
	MaxMessageRowID   int64
	Duration          time.Duration
	Perf              map[string]string
}

// SyncOptions configures sync behavior
type SyncOptions struct {
	// SinceRowID is the watermark for incremental sync (0 = full sync)
	SinceRowID int64

	// MeContactID is the comms contact ID for the current user (optional)
	// If empty, we'll try to find/create one
	MeContactID string

	// AdapterName is the source_adapter value to use (default: "imessage")
	AdapterName string

	// Full enables aggressive SQLite pragmas for bulk import
	Full bool
}

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

// AppleEpoch is the reference point for Apple timestamps (2001-01-01 00:00:00 UTC)
var AppleEpoch = time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)

// AppleTimestampToUnix converts Apple nanosecond timestamp to Unix seconds
func AppleTimestampToUnix(appleNanos int64) int64 {
	t := AppleEpoch.Add(time.Duration(appleNanos) * time.Nanosecond)
	return t.Unix()
}

// ReactionTypeToEmoji converts iMessage reaction type to emoji
func ReactionTypeToEmoji(reactionType int) string {
	switch reactionType {
	case 2000:
		return "❤️" // love
	case 2001:
		return "👍" // like
	case 2002:
		return "👎" // dislike
	case 2003:
		return "😂" // laugh
	case 2004:
		return "‼️" // emphasis
	case 2005:
		return "❓" // question
	default:
		return ""
	}
}

// ReactionTextToEmoji extracts emoji from modern text-based reactions
// e.g., "Loved "hello"" -> "❤️"
func ReactionTextToEmoji(text string) string {
	if len(text) == 0 {
		return ""
	}
	switch {
	case len(text) >= 5 && text[:5] == "Loved":
		return "❤️"
	case len(text) >= 5 && text[:5] == "Liked":
		return "👍"
	case len(text) >= 8 && text[:8] == "Disliked":
		return "👎"
	case len(text) >= 10 && text[:10] == "Laughed at":
		return "😂"
	case len(text) >= 10 && text[:10] == "Emphasized":
		return "‼️"
	case len(text) >= 10 && text[:10] == "Questioned":
		return "❓"
	default:
		return ""
	}
}
