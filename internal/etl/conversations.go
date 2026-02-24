package etl

import (
	"database/sql"
	"fmt"
	"time"
)

// Conversation represents a group of messages within a time window
type Conversation struct {
	ID           int64
	ChatID       int64
	InitiatorID  *int64
	StartTime    time.Time
	EndTime      time.Time
	MessageCount int
	GapThreshold int // in seconds
}

// MessageForConversation represents minimal message data needed for conversation grouping
type MessageForConversation struct {
	ID        int64
	ChatID    int64
	SenderID  *int64
	Timestamp time.Time
}

const (
	// Default gap threshold: 3 hours
	// A new conversation starts when the gap between messages is > 3 hours
	DefaultGapThresholdSeconds = 3 * 60 * 60 // 10800 seconds
)

// BuildConversations groups messages into conversations based on time gaps
// Returns the number of conversations created
func BuildConversations(warehouseDB *sql.DB) (int, error) {
	// Clear existing conversation assignments
	if err := clearConversationAssignments(warehouseDB); err != nil {
		return 0, fmt.Errorf("failed to clear conversation assignments: %w", err)
	}

	// Get all messages grouped by chat_id, ordered by timestamp
	messagesByChat, err := getMessagesForConversations(warehouseDB)
	if err != nil {
		return 0, fmt.Errorf("failed to read messages: %w", err)
	}

	if len(messagesByChat) == 0 {
		return 0, nil
	}

	// Begin transaction
	tx, err := warehouseDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	totalConversations := 0

	// Process each chat
	for chatID, messages := range messagesByChat {
		conversations := groupMessagesIntoConversations(messages, DefaultGapThresholdSeconds)

		// Insert conversations and update message references
		for _, conv := range conversations {
			convID, err := insertConversation(tx, &conv)
			if err != nil {
				return 0, fmt.Errorf("failed to insert conversation for chat %d: %w", chatID, err)
			}

			// Update messages to reference this conversation
			if err := assignMessagesToConversation(tx, conv.MessageIDs, convID); err != nil {
				return 0, fmt.Errorf("failed to assign messages to conversation %d: %w", convID, err)
			}

			totalConversations++
		}
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("failed to commit transaction: %w", err)
	}

	return totalConversations, nil
}

// conversationGroup holds messages that belong to a single conversation
type conversationGroup struct {
	ChatID       int64
	InitiatorID  *int64
	StartTime    time.Time
	EndTime      time.Time
	MessageCount int
	MessageIDs   []int64
}

// groupMessagesIntoConversations splits messages into conversations based on time gap
func groupMessagesIntoConversations(messages []MessageForConversation, gapThresholdSeconds int) []conversationGroup {
	if len(messages) == 0 {
		return nil
	}

	var conversations []conversationGroup
	gapThreshold := time.Duration(gapThresholdSeconds) * time.Second

	// Start first conversation
	currentConv := conversationGroup{
		ChatID:       messages[0].ChatID,
		InitiatorID:  messages[0].SenderID,
		StartTime:    messages[0].Timestamp,
		EndTime:      messages[0].Timestamp,
		MessageCount: 1,
		MessageIDs:   []int64{messages[0].ID},
	}

	// Process remaining messages
	for i := 1; i < len(messages); i++ {
		msg := messages[i]
		timeSinceLast := msg.Timestamp.Sub(currentConv.EndTime)

		if timeSinceLast > gapThreshold {
			// Gap is too large, finalize current conversation and start new one
			conversations = append(conversations, currentConv)

			currentConv = conversationGroup{
				ChatID:       msg.ChatID,
				InitiatorID:  msg.SenderID,
				StartTime:    msg.Timestamp,
				EndTime:      msg.Timestamp,
				MessageCount: 1,
				MessageIDs:   []int64{msg.ID},
			}
		} else {
			// Continue current conversation
			currentConv.EndTime = msg.Timestamp
			currentConv.MessageCount++
			currentConv.MessageIDs = append(currentConv.MessageIDs, msg.ID)
		}
	}

	// Don't forget the last conversation
	conversations = append(conversations, currentConv)

	return conversations
}

// getMessagesForConversations reads all messages grouped by chat_id
func getMessagesForConversations(db *sql.DB) (map[int64][]MessageForConversation, error) {
	query := `
		SELECT m.id, m.chat_id, c.id, m.timestamp
		FROM messages m
		JOIN chats ch ON m.chat_id = ch.id
		LEFT JOIN contacts c ON m.sender_id = c.id
		ORDER BY m.chat_id, m.timestamp
	`

	rows, err := db.Query(query)
	if err != nil {
		return nil, fmt.Errorf("failed to query messages: %w", err)
	}
	defer rows.Close()

	messagesByChat := make(map[int64][]MessageForConversation)

	for rows.Next() {
		var msg MessageForConversation
		var senderID sql.NullInt64

		if err := rows.Scan(&msg.ID, &msg.ChatID, &senderID, &msg.Timestamp); err != nil {
			return nil, fmt.Errorf("failed to scan message: %w", err)
		}

		if senderID.Valid {
			msg.SenderID = &senderID.Int64
		}

		messagesByChat[msg.ChatID] = append(messagesByChat[msg.ChatID], msg)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating messages: %w", err)
	}

	return messagesByChat, nil
}

// insertConversation inserts a conversation and returns its ID
func insertConversation(tx *sql.Tx, conv *conversationGroup) (int64, error) {
	query := `
		INSERT INTO conversations (
			chat_id,
			initiator_id,
			start_time,
			end_time,
			message_count,
			gap_threshold
		) VALUES (?, ?, ?, ?, ?, ?)
	`

	result, err := tx.Exec(query,
		conv.ChatID,
		conv.InitiatorID,
		conv.StartTime,
		conv.EndTime,
		conv.MessageCount,
		DefaultGapThresholdSeconds,
	)
	if err != nil {
		return 0, fmt.Errorf("failed to insert conversation: %w", err)
	}

	id, err := result.LastInsertId()
	if err != nil {
		return 0, fmt.Errorf("failed to get conversation ID: %w", err)
	}

	return id, nil
}

// assignMessagesToConversation updates message records to reference a conversation
func assignMessagesToConversation(tx *sql.Tx, messageIDs []int64, conversationID int64) error {
	query := `UPDATE messages SET conversation_id = ? WHERE id = ?`

	for _, msgID := range messageIDs {
		if _, err := tx.Exec(query, conversationID, msgID); err != nil {
			return fmt.Errorf("failed to update message %d: %w", msgID, err)
		}
	}

	return nil
}

// clearConversationAssignments removes existing conversation assignments
// This allows BuildConversations to be idempotent by rebuilding from scratch
func clearConversationAssignments(db *sql.DB) error {
	// Clear conversation references from messages
	if _, err := db.Exec(`UPDATE messages SET conversation_id = NULL`); err != nil {
		return fmt.Errorf("failed to clear message conversation_id: %w", err)
	}

	// Delete all conversations
	if _, err := db.Exec(`DELETE FROM conversations`); err != nil {
		return fmt.Errorf("failed to delete conversations: %w", err)
	}

	return nil
}
