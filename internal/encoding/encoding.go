package encoding

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// Reaction type to emoji mapping
var ReactionEmojis = map[int]string{
	2000: "❤️", // Love
	2001: "👍",  // Like
	2002: "👎",  // Dislike
	2003: "😂",  // Laugh
	2004: "‼️", // Emphasis
	2005: "❓",  // Question
}

// Attachment represents a message attachment
type Attachment struct {
	ID        int    `json:"id"`
	MimeType  string `json:"mime_type"`
	FileName  string `json:"file_name"`
	IsSticker bool   `json:"is_sticker"`
}

// Reaction represents a message reaction
type Reaction struct {
	ReactionType int    `json:"reaction_type"`
	SenderID     int    `json:"sender_id"`
	SenderName   string `json:"sender_name"`
	IsFromMe     bool   `json:"is_from_me"`
}

// Message represents a conversation message
type Message struct {
	ID             int          `json:"id"`
	GUID           string       `json:"guid"`
	Timestamp      time.Time    `json:"timestamp"`
	SenderID       *int         `json:"sender_id"`
	SenderName     string       `json:"sender_name"`
	Content        string       `json:"content"`
	IsFromMe       bool         `json:"is_from_me"`
	Attachments    []Attachment `json:"attachments"`
	Reactions      []Reaction   `json:"reactions"`
	ConversationID int          `json:"conversation_id"`
	ChatID         int          `json:"chat_id"`
}

// Conversation represents a grouped set of messages
type Conversation struct {
	ID        int       `json:"id"`
	ChatID    int       `json:"chat_id"`
	StartTime time.Time `json:"start_time"`
	EndTime   time.Time `json:"end_time"`
	Messages  []Message `json:"messages"`
}

// EncodeOptions controls what gets included in the encoding
type EncodeOptions struct {
	IncludeSender      bool
	IncludeAttachments bool
	IncludeReactions   bool
	IncludeStartDate   bool
	IncludeSendTime    bool
}

// DefaultEncodeOptions returns sensible defaults
func DefaultEncodeOptions() EncodeOptions {
	return EncodeOptions{
		IncludeSender:      true,
		IncludeAttachments: true,
		IncludeReactions:   true,
		IncludeStartDate:   false,
		IncludeSendTime:    false,
	}
}

// formatTimestamp formats a timestamp as a readable string
func formatTimestamp(t time.Time, includeDate bool) string {
	if includeDate {
		// Format: "Monday Oct 27, 2025 - 3:45pm"
		return t.Format("Monday Jan 2, 2006 - 3:04pm")
	}
	// Format: "3:45pm"
	return t.Format("3:04pm")
}

// formatReactions formats reactions for display
func formatReactions(reactions []Reaction) string {
	if len(reactions) == 0 {
		return ""
	}

	reactionCounts := make(map[string]int)
	for _, r := range reactions {
		if emoji, ok := ReactionEmojis[r.ReactionType]; ok {
			reactionCounts[emoji]++
		}
	}

	if len(reactionCounts) == 0 {
		return ""
	}

	var parts []string
	for emoji, count := range reactionCounts {
		if count > 1 {
			parts = append(parts, fmt.Sprintf("%s(%d)", emoji, count))
		} else {
			parts = append(parts, emoji)
		}
	}

	// Sort for determinism
	sort.Strings(parts)

	return fmt.Sprintf("[%s]", strings.Join(parts, ", "))
}

// EncodeMessage encodes a single message into text format
func EncodeMessage(message Message, options EncodeOptions) string {
	var parts []string

	// Add timestamp if requested
	if options.IncludeSendTime {
		timeStr := formatTimestamp(message.Timestamp, false)
		parts = append(parts, fmt.Sprintf("[%s]", timeStr))
	}

	// Add sender name
	if options.IncludeSender {
		sender := message.SenderName
		if sender == "" {
			sender = "Unknown"
		}
		parts = append(parts, fmt.Sprintf("%s:", sender))
	}

	// Add message text
	if message.Content != "" {
		parts = append(parts, message.Content)
	}

	// Add attachments
	if options.IncludeAttachments && len(message.Attachments) > 0 {
		var attTexts []string
		for _, att := range message.Attachments {
			if strings.HasPrefix(att.MimeType, "image/") {
				attTexts = append(attTexts, "[Image]")
			} else {
				filename := att.FileName
				if filename == "" {
					filename = "Unknown file"
				}
				attTexts = append(attTexts, fmt.Sprintf("[Attachment: %s]", filename))
			}
		}
		if len(attTexts) > 0 {
			parts = append(parts, strings.Join(attTexts, " "))
		}
	}

	// Add reactions
	if options.IncludeReactions && len(message.Reactions) > 0 {
		reactionText := formatReactions(message.Reactions)
		if reactionText != "" {
			parts = append(parts, reactionText)
		}
	}

	return strings.Join(parts, " ")
}

// EncodeConversation encodes a conversation into text format
func EncodeConversation(conversation Conversation, options EncodeOptions) string {
	// Sort messages by timestamp
	messages := make([]Message, len(conversation.Messages))
	copy(messages, conversation.Messages)
	sort.Slice(messages, func(i, j int) bool {
		return messages[i].Timestamp.Before(messages[j].Timestamp)
	})

	var encodedLines []string

	// Optional date header
	if options.IncludeStartDate && len(messages) > 0 {
		dateHeader := formatTimestamp(messages[0].Timestamp, true)
		encodedLines = append(encodedLines, fmt.Sprintf("=== %s ===", dateHeader))
	}

	// Encode each message
	for _, msg := range messages {
		encodedMsg := EncodeMessage(msg, options)
		if encodedMsg != "" {
			encodedLines = append(encodedLines, encodedMsg)
		}
	}

	return strings.Join(encodedLines, "\n")
}
