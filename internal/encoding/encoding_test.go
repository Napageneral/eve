package encoding

import (
	"strings"
	"testing"
	"time"
)

func TestEncodeMessage_Basic(t *testing.T) {
	msg := Message{
		ID:         1,
		GUID:       "test-guid",
		Timestamp:  time.Date(2025, 10, 27, 15, 45, 0, 0, time.UTC),
		SenderName: "Alice",
		Content:    "Hello, world!",
		IsFromMe:   false,
	}

	opts := DefaultEncodeOptions()
	encoded := EncodeMessage(msg, opts)

	expected := "Alice: Hello, world!"
	if encoded != expected {
		t.Errorf("Expected %q, got %q", expected, encoded)
	}
}

func TestEncodeMessage_WithTimestamp(t *testing.T) {
	msg := Message{
		ID:         1,
		GUID:       "test-guid",
		Timestamp:  time.Date(2025, 10, 27, 15, 45, 0, 0, time.UTC),
		SenderName: "Alice",
		Content:    "Hello!",
		IsFromMe:   false,
	}

	opts := DefaultEncodeOptions()
	opts.IncludeSendTime = true
	encoded := EncodeMessage(msg, opts)

	if !strings.Contains(encoded, "[3:45pm]") {
		t.Errorf("Expected timestamp in output, got %q", encoded)
	}
	if !strings.Contains(encoded, "Alice:") {
		t.Errorf("Expected sender in output, got %q", encoded)
	}
	if !strings.Contains(encoded, "Hello!") {
		t.Errorf("Expected content in output, got %q", encoded)
	}
}

func TestEncodeMessage_WithAttachments(t *testing.T) {
	msg := Message{
		ID:         1,
		GUID:       "test-guid",
		Timestamp:  time.Date(2025, 10, 27, 15, 45, 0, 0, time.UTC),
		SenderName: "Bob",
		Content:    "Check this out",
		Attachments: []Attachment{
			{ID: 1, MimeType: "image/png", FileName: "photo.png"},
			{ID: 2, MimeType: "application/pdf", FileName: "document.pdf"},
		},
	}

	opts := DefaultEncodeOptions()
	encoded := EncodeMessage(msg, opts)

	if !strings.Contains(encoded, "Bob: Check this out") {
		t.Errorf("Expected basic message, got %q", encoded)
	}
	if !strings.Contains(encoded, "[Image]") {
		t.Errorf("Expected [Image] for image attachment, got %q", encoded)
	}
	if !strings.Contains(encoded, "[Attachment: document.pdf]") {
		t.Errorf("Expected [Attachment: document.pdf], got %q", encoded)
	}
}

func TestEncodeMessage_WithReactions(t *testing.T) {
	msg := Message{
		ID:         1,
		GUID:       "test-guid",
		Timestamp:  time.Date(2025, 10, 27, 15, 45, 0, 0, time.UTC),
		SenderName: "Charlie",
		Content:    "This is great!",
		Reactions: []Reaction{
			{ReactionType: 2000, SenderID: 2, SenderName: "Alice"}, // Love
			{ReactionType: 2001, SenderID: 3, SenderName: "Bob"},   // Like
			{ReactionType: 2001, SenderID: 4, SenderName: "Dave"},  // Like
		},
	}

	opts := DefaultEncodeOptions()
	encoded := EncodeMessage(msg, opts)

	if !strings.Contains(encoded, "Charlie: This is great!") {
		t.Errorf("Expected basic message, got %q", encoded)
	}
	// Should have reactions in brackets
	if !strings.Contains(encoded, "[") || !strings.Contains(encoded, "]") {
		t.Errorf("Expected reactions in brackets, got %q", encoded)
	}
	// Should have like count
	if !strings.Contains(encoded, "👍(2)") {
		t.Errorf("Expected 👍(2) for 2 likes, got %q", encoded)
	}
}

func TestEncodeConversation_OrdersMessages(t *testing.T) {
	conv := Conversation{
		ID:        1,
		ChatID:    10,
		StartTime: time.Date(2025, 10, 27, 15, 0, 0, 0, time.UTC),
		EndTime:   time.Date(2025, 10, 27, 16, 0, 0, 0, time.UTC),
		Messages: []Message{
			{
				ID:         3,
				Timestamp:  time.Date(2025, 10, 27, 15, 50, 0, 0, time.UTC),
				SenderName: "Charlie",
				Content:    "Third message",
			},
			{
				ID:         1,
				Timestamp:  time.Date(2025, 10, 27, 15, 30, 0, 0, time.UTC),
				SenderName: "Alice",
				Content:    "First message",
			},
			{
				ID:         2,
				Timestamp:  time.Date(2025, 10, 27, 15, 40, 0, 0, time.UTC),
				SenderName: "Bob",
				Content:    "Second message",
			},
		},
	}

	opts := DefaultEncodeOptions()
	encoded := EncodeConversation(conv, opts)

	lines := strings.Split(encoded, "\n")
	if len(lines) != 3 {
		t.Errorf("Expected 3 lines, got %d", len(lines))
	}

	if !strings.Contains(lines[0], "Alice: First message") {
		t.Errorf("Expected first line to be Alice's message, got %q", lines[0])
	}
	if !strings.Contains(lines[1], "Bob: Second message") {
		t.Errorf("Expected second line to be Bob's message, got %q", lines[1])
	}
	if !strings.Contains(lines[2], "Charlie: Third message") {
		t.Errorf("Expected third line to be Charlie's message, got %q", lines[2])
	}
}

func TestEncodeConversation_WithDateHeader(t *testing.T) {
	conv := Conversation{
		ID:        1,
		ChatID:    10,
		StartTime: time.Date(2025, 10, 27, 15, 0, 0, 0, time.UTC),
		EndTime:   time.Date(2025, 10, 27, 16, 0, 0, 0, time.UTC),
		Messages: []Message{
			{
				ID:         1,
				Timestamp:  time.Date(2025, 10, 27, 15, 30, 0, 0, time.UTC),
				SenderName: "Alice",
				Content:    "Hello",
			},
		},
	}

	opts := DefaultEncodeOptions()
	opts.IncludeStartDate = true
	encoded := EncodeConversation(conv, opts)

	lines := strings.Split(encoded, "\n")
	if len(lines) < 2 {
		t.Errorf("Expected at least 2 lines (header + message), got %d", len(lines))
	}

	if !strings.HasPrefix(lines[0], "===") {
		t.Errorf("Expected date header to start with ===, got %q", lines[0])
	}
}

func TestFormatReactions_DeterministicOrder(t *testing.T) {
	reactions := []Reaction{
		{ReactionType: 2003}, // Laugh
		{ReactionType: 2001}, // Like
		{ReactionType: 2000}, // Love
		{ReactionType: 2001}, // Like (duplicate)
	}

	result1 := formatReactions(reactions)
	result2 := formatReactions(reactions)

	if result1 != result2 {
		t.Errorf("formatReactions not deterministic: %q != %q", result1, result2)
	}

	// Should contain both emojis
	if !strings.Contains(result1, "❤️") {
		t.Errorf("Expected love emoji, got %q", result1)
	}
	if !strings.Contains(result1, "👍(2)") {
		t.Errorf("Expected 2 likes, got %q", result1)
	}
}
