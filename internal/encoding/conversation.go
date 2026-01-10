package encoding

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// Message represents a conversation message
type Message struct {
	ID          int64
	GUID        string
	Timestamp   time.Time
	SenderName  string
	Text        string
	IsFromMe    bool
	Attachments []Attachment
	Reactions   []Reaction
}

// Attachment represents a message attachment
type Attachment struct {
	ID        int64
	MimeType  string
	FileName  string
	IsSticker bool
}

// Reaction represents a reaction to a message
type Reaction struct {
	ReactionType int
	SenderName   string
	IsFromMe     bool
}

// Conversation represents a conversation with messages
type Conversation struct {
	ID        int64
	ChatID    int64
	StartTime time.Time
	EndTime   time.Time
	Messages  []Message
}

// EncodeOptions controls what information is included in the encoded output
type EncodeOptions struct {
	IncludeSender      bool
	IncludeAttachments bool
	IncludeReactions   bool
	IncludeStartDate   bool
	IncludeSendTime    bool
}

// DefaultEncodeOptions returns the default encoding options
func DefaultEncodeOptions() EncodeOptions {
	return EncodeOptions{
		IncludeSender:      true,
		IncludeAttachments: true,
		IncludeReactions:   true,
		IncludeStartDate:   false,
		IncludeSendTime:    false,
	}
}

// EncodeResult contains the encoded transcript and metadata
type EncodeResult struct {
	Success      bool   `json:"success"`
	FilePath     string `json:"file_path,omitempty"`
	TokenCount   int    `json:"token_count"`
	MessageCount int    `json:"message_count"`
	EncodedText  string `json:"encoded_text,omitempty"`
	Error        string `json:"error,omitempty"`
}

// LoadConversation loads a conversation from the database
func LoadConversation(dbPath string, conversationID int64) (*Conversation, error) {
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open database: %w", err)
	}
	defer db.Close()

	// Load conversation metadata
	var conv Conversation
	conv.ID = conversationID

	// Load messages (simplified query - adjust based on actual schema)
	query := `
		SELECT
			m.ROWID as id,
			m.guid,
			datetime(m.date / 1000000000 + strftime('%s', '2001-01-01'), 'unixepoch') as timestamp,
			COALESCE(c.display_name, h.id, 'Unknown') as sender_name,
			COALESCE(m.text, '') as text,
			m.is_from_me
		FROM message m
		LEFT JOIN handle h ON m.handle_id = h.ROWID
		LEFT JOIN contact c ON h.id = c.phone_number
		WHERE m.ROWID = ?
		ORDER BY m.date ASC
	`

	rows, err := db.Query(query, conversationID)
	if err != nil {
		return nil, fmt.Errorf("failed to query messages: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var msg Message
		var timestampStr string
		var isFromMe int

		err := rows.Scan(
			&msg.ID,
			&msg.GUID,
			&timestampStr,
			&msg.SenderName,
			&msg.Text,
			&isFromMe,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to scan message: %w", err)
		}

		msg.IsFromMe = isFromMe == 1
		msg.Timestamp, _ = time.Parse("2006-01-02 15:04:05", timestampStr)
		conv.Messages = append(conv.Messages, msg)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("row iteration error: %w", err)
	}

	if len(conv.Messages) == 0 {
		return nil, fmt.Errorf("no messages found for conversation %d", conversationID)
	}

	// Set conversation time bounds
	conv.StartTime = conv.Messages[0].Timestamp
	conv.EndTime = conv.Messages[len(conv.Messages)-1].Timestamp

	return &conv, nil
}

// EncodeMessage encodes a single message into text format
func EncodeMessage(msg Message, opts EncodeOptions) string {
	var parts []string

	// Add timestamp if requested
	if opts.IncludeSendTime {
		timeStr := msg.Timestamp.Format("3:04pm")
		parts = append(parts, fmt.Sprintf("[%s]", timeStr))
	}

	// Add sender name
	if opts.IncludeSender {
		parts = append(parts, fmt.Sprintf("%s:", msg.SenderName))
	}

	// Add message text
	if msg.Text != "" {
		parts = append(parts, msg.Text)
	}

	// Add attachments
	if opts.IncludeAttachments && len(msg.Attachments) > 0 {
		for _, att := range msg.Attachments {
			if strings.HasPrefix(att.MimeType, "image/") {
				parts = append(parts, "[Image]")
			} else {
				fileName := att.FileName
				if fileName == "" {
					fileName = "Unknown file"
				}
				parts = append(parts, fmt.Sprintf("[Attachment: %s]", fileName))
			}
		}
	}

	// Add reactions
	if opts.IncludeReactions && len(msg.Reactions) > 0 {
		reactionCounts := make(map[string]int)
		for _, r := range msg.Reactions {
			emoji := reactionTypeToEmoji(r.ReactionType)
			if emoji != "" {
				reactionCounts[emoji]++
			}
		}

		if len(reactionCounts) > 0 {
			var reactionParts []string
			for emoji, count := range reactionCounts {
				if count > 1 {
					reactionParts = append(reactionParts, fmt.Sprintf("%s(%d)", emoji, count))
				} else {
					reactionParts = append(reactionParts, emoji)
				}
			}
			parts = append(parts, fmt.Sprintf("[%s]", strings.Join(reactionParts, ", ")))
		}
	}

	return strings.Join(parts, " ")
}

// EncodeConversation encodes a conversation into text format
func EncodeConversation(conv *Conversation, opts EncodeOptions) string {
	var lines []string

	// Sort messages by timestamp (should already be sorted, but ensure it)
	messages := make([]Message, len(conv.Messages))
	copy(messages, conv.Messages)
	sort.Slice(messages, func(i, j int) bool {
		return messages[i].Timestamp.Before(messages[j].Timestamp)
	})

	// Optional date header
	if opts.IncludeStartDate && len(messages) > 0 {
		startTime := messages[0].Timestamp
		dateHeader := startTime.Format("Monday Jan 2, 2006 - 3:04pm")
		lines = append(lines, fmt.Sprintf("=== %s ===", dateHeader))
	}

	// Encode each message
	for _, msg := range messages {
		encodedMsg := EncodeMessage(msg, opts)
		if encodedMsg != "" {
			lines = append(lines, encodedMsg)
		}
	}

	return strings.Join(lines, "\n")
}

// EncodeConversationToFile encodes a conversation and writes it to a file
func EncodeConversationToFile(dbPath string, conversationID int64, outputPath string) EncodeResult {
	// Load conversation
	conv, err := LoadConversation(dbPath, conversationID)
	if err != nil {
		return EncodeResult{
			Success: false,
			Error:   fmt.Sprintf("failed to load conversation: %v", err),
		}
	}

	// Encode conversation
	opts := DefaultEncodeOptions()
	encodedText := EncodeConversation(conv, opts)

	// Write to file
	if err := os.WriteFile(outputPath, []byte(encodedText), 0644); err != nil {
		return EncodeResult{
			Success: false,
			Error:   fmt.Sprintf("failed to write file: %v", err),
		}
	}

	// Count tokens (rough estimate: ~4 chars per token)
	tokenCount := len(encodedText) / 4

	return EncodeResult{
		Success:      true,
		FilePath:     outputPath,
		TokenCount:   tokenCount,
		MessageCount: len(conv.Messages),
	}
}

// EncodeConversationToString encodes a conversation and returns it as a string
func EncodeConversationToString(dbPath string, conversationID int64) EncodeResult {
	// Load conversation
	conv, err := LoadConversation(dbPath, conversationID)
	if err != nil {
		return EncodeResult{
			Success: false,
			Error:   fmt.Sprintf("failed to load conversation: %v", err),
		}
	}

	// Encode conversation
	opts := DefaultEncodeOptions()
	encodedText := EncodeConversation(conv, opts)

	// Count tokens (rough estimate: ~4 chars per token)
	tokenCount := len(encodedText) / 4

	return EncodeResult{
		Success:      true,
		EncodedText:  encodedText,
		TokenCount:   tokenCount,
		MessageCount: len(conv.Messages),
	}
}

// reactionTypeToEmoji converts iMessage reaction types to emoji
func reactionTypeToEmoji(reactionType int) string {
	// iMessage reaction types (from iMessage database)
	reactionMap := map[int]string{
		2000: "❤️", // Love
		2001: "👍",  // Like
		2002: "👎",  // Dislike
		2003: "😂",  // Laugh
		2004: "‼️", // Emphasize
		2005: "❓",  // Question
	}

	if emoji, ok := reactionMap[reactionType]; ok {
		return emoji
	}
	return ""
}

// GetDefaultOutputPath returns a default output path for encoded conversation
func GetDefaultOutputPath(conversationID int64) string {
	home, _ := os.UserHomeDir()
	tmpDir := filepath.Join(home, ".config", "eve", "tmp")
	os.MkdirAll(tmpDir, 0755)
	return filepath.Join(tmpDir, fmt.Sprintf("conversation_%d.txt", conversationID))
}
