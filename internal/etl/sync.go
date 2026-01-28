package etl

import (
	"database/sql"
	"fmt"
)

// SyncResult contains statistics from a full ETL sync operation
type SyncResult struct {
	HandlesCount       int
	ChatsCount         int
	MessagesCount      int
	ReactionsCount     int
	MembershipCount    int
	AttachmentsCount   int
	ConversationsCount int
	MaxMessageRowID    int64
}

// FullSync runs the complete ETL pipeline:
// 1. Sync handles from chat.db to contacts + contact_identifiers
// 2. Sync chats from chat.db to chats
// 2b. Sync chat participants from chat.db to chat_participants
// 3. Sync messages from chat.db to messages (incremental via watermark)
// 4. Sync attachments from chat.db to attachments
// 5. Build conversations from messages
// 6. Update watermark
//
// Returns SyncResult with counts and the max message ROWID for watermark updates
func FullSync(chatDB *ChatDB, warehouseDB *sql.DB, sinceRowID int64) (*SyncResult, error) {
	result := &SyncResult{}

	// Step 1: Sync handles (must run first for FK references)
	handlesCount, err := SyncHandles(chatDB, warehouseDB)
	if err != nil {
		return nil, fmt.Errorf("handle sync failed: %w", err)
	}
	result.HandlesCount = handlesCount

	// Step 1b: Hydrate contact names from AddressBook (best-effort; no hard failure)
	// This resolves contacts.name for handle-based contacts so queries/encoding can show real names.
	_, _ = HydrateContactNamesFromAddressBook(warehouseDB)

	// Step 2: Sync chats (must run before messages for FK references)
	chatsCount, err := SyncChats(chatDB, warehouseDB)
	if err != nil {
		return nil, fmt.Errorf("chat sync failed: %w", err)
	}
	result.ChatsCount = chatsCount

	// Step 2b: Sync chat participants (best-effort, but should normally succeed)
	_, _ = SyncChatParticipants(chatDB, warehouseDB)

	// Step 3: Sync messages (incremental via watermark)
	messagesCount, err := SyncMessages(chatDB, warehouseDB, sinceRowID)
	if err != nil {
		return nil, fmt.Errorf("message sync failed: %w", err)
	}
	result.MessagesCount = messagesCount

	// Step 4: Sync reactions (requires messages to exist first)
	reactionsCount, err := SyncReactions(chatDB, warehouseDB, sinceRowID)
	if err != nil {
		return nil, fmt.Errorf("reaction sync failed: %w", err)
	}
	result.ReactionsCount = reactionsCount

	// Step 4b: Sync membership events (requires messages to exist first)
	membershipCount, err := SyncMembershipEvents(chatDB, warehouseDB, sinceRowID)
	if err != nil {
		return nil, fmt.Errorf("membership sync failed: %w", err)
	}
	result.MembershipCount = membershipCount

	// Step 5: Sync attachments (requires messages to exist first)
	attachmentsCount, err := SyncAttachments(chatDB, warehouseDB)
	if err != nil {
		return nil, fmt.Errorf("attachment sync failed: %w", err)
	}
	result.AttachmentsCount = attachmentsCount

	// Step 6: Build conversations from messages
	conversationsCount, err := BuildConversations(warehouseDB)
	if err != nil {
		return nil, fmt.Errorf("conversation building failed: %w", err)
	}
	result.ConversationsCount = conversationsCount

	// Step 7: Get max message ROWID for watermark update
	maxRowID, err := chatDB.GetMaxMessageRowID()
	if err != nil {
		return nil, fmt.Errorf("failed to get max message ROWID: %w", err)
	}
	result.MaxMessageRowID = maxRowID

	return result, nil
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
