package imessage

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// Sync reads from chat.db and writes directly to commsDB
// This is the main entry point for the Comms adapter
func Sync(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, opts SyncOptions) (*SyncResult, error) {
	startTime := time.Now()
	result := &SyncResult{
		Perf: make(map[string]string),
	}

	adapterName := opts.AdapterName
	if adapterName == "" {
		adapterName = "imessage"
	}

	// Set SQLite pragmas for performance
	if err := setPragmas(commsDB, opts.Full); err != nil {
		return nil, err
	}

	// Sync handles → contacts
	handlesStart := time.Now()
	handlesSynced, handleMap, meContactID, err := syncHandles(ctx, chatDB, commsDB, opts.MeContactID)
	if err != nil {
		return nil, fmt.Errorf("failed to sync handles: %w", err)
	}
	result.HandlesSynced = handlesSynced
	result.Perf["handles"] = time.Since(handlesStart).String()

	// Sync chats → threads
	chatsStart := time.Now()
	chatsSynced, err := syncChats(ctx, chatDB, commsDB, adapterName)
	if err != nil {
		return nil, fmt.Errorf("failed to sync chats: %w", err)
	}
	result.ChatsSynced = chatsSynced
	result.Perf["chats"] = time.Since(chatsStart).String()

	// Sync messages → events
	messagesStart := time.Now()
	messagesSynced, maxRowID, err := syncMessages(ctx, chatDB, commsDB, opts.SinceRowID, adapterName, handleMap, meContactID)
	if err != nil {
		return nil, fmt.Errorf("failed to sync messages: %w", err)
	}
	result.MessagesSynced = messagesSynced
	result.MaxMessageRowID = maxRowID
	result.Perf["messages"] = time.Since(messagesStart).String()

	// Sync membership events → events with content_types=["membership"]
	membershipStart := time.Now()
	membershipSynced, err := syncMembershipEvents(ctx, chatDB, commsDB, opts.SinceRowID, adapterName, handleMap, meContactID)
	if err != nil {
		return nil, fmt.Errorf("failed to sync membership events: %w", err)
	}
	result.MembershipSynced = membershipSynced
	result.Perf["membership"] = time.Since(membershipStart).String()

	// Sync reactions → events with content_types=["reaction"]
	// Must happen BEFORE attachments since some attachments belong to reaction messages
	reactionsStart := time.Now()
	reactionsSynced, err := syncReactions(ctx, chatDB, commsDB, opts.SinceRowID, adapterName, handleMap, meContactID)
	if err != nil {
		return nil, fmt.Errorf("failed to sync reactions: %w", err)
	}
	result.ReactionsSynced = reactionsSynced
	result.Perf["reactions"] = time.Since(reactionsStart).String()

	// Sync attachments (after messages AND reactions, due to FK constraint)
	attachmentsStart := time.Now()
	attachmentsSynced, err := syncAttachments(ctx, chatDB, commsDB, opts.SinceRowID, adapterName)
	if err != nil {
		return nil, fmt.Errorf("failed to sync attachments: %w", err)
	}
	result.AttachmentsSynced = attachmentsSynced
	result.Perf["attachments"] = time.Since(attachmentsStart).String()

	result.Duration = time.Since(startTime)
	result.Perf["total"] = result.Duration.String()
	return result, nil
}

func setPragmas(db *sql.DB, full bool) error {
	if _, err := db.Exec("PRAGMA foreign_keys = ON"); err != nil {
		return fmt.Errorf("failed to enable foreign keys: %w", err)
	}
	_, _ = db.Exec("PRAGMA busy_timeout = 5000")
	_, _ = db.Exec("PRAGMA journal_mode = WAL")
	_, _ = db.Exec("PRAGMA synchronous = NORMAL")

	if full {
		_, _ = db.Exec("PRAGMA synchronous = OFF")
		_, _ = db.Exec("PRAGMA temp_store = MEMORY")
		_, _ = db.Exec("PRAGMA cache_size = -200000")
		_, _ = db.Exec("PRAGMA mmap_size = 268435456")
		_, _ = db.Exec("PRAGMA wal_autocheckpoint = 1000000")
	}
	_, _ = db.Exec("PRAGMA defer_foreign_keys = ON")
	return nil
}

// syncHandles syncs handles from chat.db to comms contacts.
// Returns: count synced, handleID→contactID map, meContactID
func syncHandles(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, existingMeContactID string) (int, map[int64]string, string, error) {
	_ = ctx

	// Get or create "me" contact
	meContactID := existingMeContactID
	if meContactID == "" {
		meIdentifier := "imessage:me"
		var contactID string
		err := commsDB.QueryRow(`
			SELECT contact_id FROM contact_identifiers
			WHERE type = 'human' AND normalized = ?
		`, meIdentifier).Scan(&contactID)
		if err == nil && contactID != "" {
			meContactID = contactID
		} else if err != nil && err != sql.ErrNoRows {
			return 0, nil, "", fmt.Errorf("failed to query me contact: %w", err)
		} else {
			meContactID = uuid.New().String()
			now := time.Now().Unix()
			if _, err := commsDB.Exec(`
				INSERT INTO contacts (id, display_name, source, created_at, updated_at)
				VALUES (?, ?, ?, ?, ?)
			`, meContactID, "Me", "imessage", now, now); err != nil {
				return 0, nil, "", fmt.Errorf("failed to create me contact: %w", err)
			}
			_, _ = commsDB.Exec(`
				INSERT INTO contact_identifiers (id, contact_id, type, value, normalized, created_at, last_seen_at)
				VALUES (?, ?, 'human', ?, ?, ?, ?)
				ON CONFLICT(type, normalized) DO UPDATE SET last_seen_at = excluded.last_seen_at
			`, uuid.New().String(), meContactID, meIdentifier, meIdentifier, now, now)
		}
	}

	// Read handles from chat.db
	handles, err := chatDB.GetHandles()
	if err != nil {
		return 0, nil, meContactID, err
	}

	if len(handles) == 0 {
		return 0, make(map[int64]string), meContactID, nil
	}

	// Bulk write in a single transaction
	tx, err := commsDB.Begin()
	if err != nil {
		return 0, nil, meContactID, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	handleMap := make(map[int64]string) // chat.db handle ROWID → comms contact_id
	contactsCreated := 0

	for _, handle := range handles {
		normalized, identifierType := NormalizeIdentifier(handle.ID)
		if normalized == "" {
			continue
		}

		var contactID string
		err := tx.QueryRow(`
			SELECT contact_id FROM contact_identifiers
			WHERE type = ? AND normalized = ?
		`, identifierType, normalized).Scan(&contactID)
		if err != nil && err != sql.ErrNoRows {
			return contactsCreated, handleMap, meContactID, fmt.Errorf("failed to query contact identifier: %w", err)
		}
		if contactID == "" {
			contactID = uuid.New().String()
			now := time.Now().Unix()
			if _, err := tx.Exec(`
				INSERT INTO contacts (id, display_name, source, created_at, updated_at)
				VALUES (?, ?, ?, ?, ?)
			`, contactID, normalized, "imessage", now, now); err != nil {
				return contactsCreated, handleMap, meContactID, fmt.Errorf("insert contact: %w", err)
			}
			if _, err := tx.Exec(`
				INSERT INTO contact_identifiers (id, contact_id, type, value, normalized, created_at, last_seen_at)
				VALUES (?, ?, ?, ?, ?, ?, ?)
				ON CONFLICT(type, normalized) DO UPDATE SET last_seen_at = excluded.last_seen_at
			`, uuid.New().String(), contactID, identifierType, handle.ID, normalized, now, now); err != nil {
				return contactsCreated, handleMap, meContactID, fmt.Errorf("insert contact identifier: %w", err)
			}
			contactsCreated++
		}

		handleMap[handle.ROWID] = contactID
	}

	if err := tx.Commit(); err != nil {
		return contactsCreated, handleMap, meContactID, fmt.Errorf("commit tx: %w", err)
	}

	return contactsCreated, handleMap, meContactID, nil
}

// syncChats syncs chats from chat.db to comms threads
func syncChats(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, adapterName string) (int, error) {
	_ = ctx

	chats, err := chatDB.GetChats()
	if err != nil {
		return 0, err
	}

	if len(chats) == 0 {
		return 0, nil
	}

	tx, err := commsDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmt, err := tx.Prepare(`
		INSERT INTO threads (id, channel, name, source_adapter, source_id, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(source_adapter, source_id) DO UPDATE SET
			name = excluded.name,
			updated_at = excluded.updated_at
	`)
	if err != nil {
		return 0, fmt.Errorf("prepare insert thread: %w", err)
	}
	defer stmt.Close()

	created := 0
	for _, chat := range chats {
		threadName := chat.ChatIdentifier
		if chat.DisplayName.Valid && chat.DisplayName.String != "" {
			threadName = chat.DisplayName.String
		}

		// Deterministic thread ID
		threadID := adapterName + ":" + chat.ChatIdentifier
		now := time.Now().Unix()

		res, err := stmt.Exec(
			threadID,
			"imessage",
			threadName,
			adapterName,
			chat.ChatIdentifier,
			now,
			now,
		)
		if err != nil {
			return created, fmt.Errorf("upsert thread: %w", err)
		}
		if n, _ := res.RowsAffected(); n > 0 {
			created++
		}
	}

	if err := tx.Commit(); err != nil {
		return created, fmt.Errorf("commit tx: %w", err)
	}

	return created, nil
}

// syncMessages syncs messages from chat.db to comms events
func syncMessages(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, sinceRowID int64, adapterName string, handleMap map[int64]string, meContactID string) (int, int64, error) {
	_ = ctx

	messages, err := chatDB.GetMessages(sinceRowID)
	if err != nil {
		return 0, 0, err
	}

	if len(messages) == 0 {
		return 0, sinceRowID, nil
	}

	tx, err := commsDB.Begin()
	if err != nil {
		return 0, 0, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmtInsertEvent, err := tx.Prepare(`
		INSERT INTO events (
			id, timestamp, channel, content_types, content,
			direction, thread_id, reply_to, source_adapter, source_id, metadata_json
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(source_adapter, source_id) DO UPDATE SET
			channel = excluded.channel,
			content_types = excluded.content_types,
			content = excluded.content,
			direction = excluded.direction,
			thread_id = excluded.thread_id,
			reply_to = excluded.reply_to,
			metadata_json = excluded.metadata_json,
			timestamp = excluded.timestamp
	`)
	if err != nil {
		return 0, 0, fmt.Errorf("prepare insert event: %w", err)
	}
	defer stmtInsertEvent.Close()

	stmtInsertParticipant, err := tx.Prepare(`
		INSERT OR IGNORE INTO event_participants (event_id, contact_id, role)
		VALUES (?, ?, ?)
	`)
	if err != nil {
		return 0, 0, fmt.Errorf("prepare insert participant: %w", err)
	}
	defer stmtInsertParticipant.Close()

	const contentTypesText = `["text"]`
	const contentTypesAttachment = `["attachment"]`
	const contentTypesTextAttachment = `["text","attachment"]`

	created := 0
	var maxRowID int64

	for _, msg := range messages {
		if msg.ROWID > maxRowID {
			maxRowID = msg.ROWID
		}

		if isMembershipMessage(msg) {
			continue
		}
		if isReactionMessage(msg) {
			continue
		}

		// Extract content
		content := ""
		if msg.Text.Valid {
			content = msg.Text.String
		}
		if content == "" && len(msg.AttributedBody) > 0 {
			content = DecodeAttributedBody(msg.AttributedBody)
		}
		content = CleanMessageContent(content)

		// Convert timestamp
		timestamp := AppleTimestampToUnix(msg.Date)

		// Content types (we'll need to check for attachments separately)
		contentTypesJSON := contentTypesText
		if content == "" {
			contentTypesJSON = contentTypesAttachment
		}

		// Direction
		direction := "received"
		if msg.IsFromMe {
			direction = "sent"
		}

		// Thread ID
		threadID := adapterName + ":" + msg.ChatIdentifier

		// Reply to
		replyTo := ""
		if msg.ReplyToGUID.Valid && msg.ReplyToGUID.String != "" {
			replyTo = adapterName + ":" + msg.ReplyToGUID.String
		}

		// Event ID (deterministic)
		eventID := adapterName + ":" + msg.GUID

		res, err := stmtInsertEvent.Exec(
			eventID, timestamp, "imessage", contentTypesJSON, content,
			direction, threadID, replyTo, adapterName, msg.GUID,
		)
		if err != nil {
			return created, maxRowID, fmt.Errorf("insert event: %w", err)
		}
		if n, _ := res.RowsAffected(); n == 1 {
			created++
		}

		// Add participants
		if msg.HandleID.Valid {
			if contactID, ok := handleMap[msg.HandleID.Int64]; ok && contactID != "" {
				role := "sender"
				if msg.IsFromMe {
					role = "recipient"
				}
				_, _ = stmtInsertParticipant.Exec(eventID, contactID, role)
			}
		}
		if msg.IsFromMe && meContactID != "" {
			_, _ = stmtInsertParticipant.Exec(eventID, meContactID, "sender")
		} else if !msg.IsFromMe && meContactID != "" {
			_, _ = stmtInsertParticipant.Exec(eventID, meContactID, "recipient")
		}
	}

	if err := tx.Commit(); err != nil {
		return created, maxRowID, fmt.Errorf("commit tx: %w", err)
	}

	return created, maxRowID, nil
}

// syncAttachments syncs attachments from chat.db to comms attachments
func syncAttachments(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, sinceRowID int64, adapterName string) (int, error) {
	_ = ctx

	attachments, err := chatDB.GetAttachments(sinceRowID)
	if err != nil {
		return 0, err
	}

	if len(attachments) == 0 {
		return 0, nil
	}

	tx, err := commsDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmt, err := tx.Prepare(`
		INSERT INTO attachments (
			id, event_id, filename, mime_type, size_bytes,
			media_type, storage_uri, storage_type, content_hash,
			source_id, metadata_json, created_at
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			filename = excluded.filename,
			mime_type = excluded.mime_type,
			size_bytes = excluded.size_bytes,
			media_type = excluded.media_type,
			storage_uri = excluded.storage_uri
	`)
	if err != nil {
		return 0, fmt.Errorf("prepare insert attachment: %w", err)
	}
	defer stmt.Close()

	created := 0
	for _, att := range attachments {
		// Build event ID from message GUID
		eventID := adapterName + ":" + att.MessageGUID

		// Convert timestamp
		createdAt := AppleTimestampToUnix(att.CreatedDate)

		// Derive media type
		mediaType := DeriveMediaType(att.MimeType.String, att.IsSticker)

		// Storage URI (placeholder for now)
		storageURI := ""
		if att.Filename.Valid && att.Filename.String != "" {
			storageURI = "eve://" + att.GUID
		}

		// Metadata JSON
		metadataJSON := fmt.Sprintf(`{"uti":"%s","is_sticker":%v}`, att.UTI.String, att.IsSticker)

		// Deterministic attachment ID
		attachmentID := adapterName + ":" + att.GUID

		res, err := stmt.Exec(
			attachmentID,
			eventID,
			att.Filename.String,
			att.MimeType.String,
			att.TotalBytes.Int64,
			mediaType,
			storageURI,
			"local",
			"",
			att.GUID,
			metadataJSON,
			createdAt,
		)
		if err != nil {
			return created, fmt.Errorf("upsert attachment %s (event_id=%s, msg_guid=%s): %w", attachmentID, eventID, att.MessageGUID, err)
		}
		if n, _ := res.RowsAffected(); n > 0 {
			created++
		}

		if err := updateEventContentTypes(tx, eventID, mediaType); err != nil {
			return created, fmt.Errorf("update event content types (event_id=%s): %w", eventID, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return created, fmt.Errorf("commit tx: %w", err)
	}

	return created, nil
}

// syncReactions syncs reactions from chat.db to comms events
func syncReactions(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, sinceRowID int64, adapterName string, handleMap map[int64]string, meContactID string) (int, error) {
	_ = ctx

	reactions, err := chatDB.GetReactions(sinceRowID)
	if err != nil {
		return 0, err
	}

	if len(reactions) == 0 {
		return 0, nil
	}

	tx, err := commsDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmtInsertEvent, err := tx.Prepare(`
		INSERT OR IGNORE INTO events (
			id, timestamp, channel, content_types, content,
			direction, thread_id, reply_to, source_adapter, source_id
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`)
	if err != nil {
		return 0, fmt.Errorf("prepare insert event: %w", err)
	}
	defer stmtInsertEvent.Close()

	stmtInsertParticipant, err := tx.Prepare(`
		INSERT OR IGNORE INTO event_participants (event_id, contact_id, role)
		VALUES (?, ?, ?)
	`)
	if err != nil {
		return 0, fmt.Errorf("prepare insert participant: %w", err)
	}
	defer stmtInsertParticipant.Close()

	const contentTypesReaction = `["reaction"]`

	created := 0
	for _, r := range reactions {
		// Convert timestamp
		timestamp := AppleTimestampToUnix(r.Date)

		// Get emoji for reaction - try text-based first (modern format), then type-based (legacy)
		reactionContent := ""
		if r.Text.Valid && r.Text.String != "" {
			reactionContent = ReactionTextToEmoji(r.Text.String)
		}
		if reactionContent == "" && r.ReactionType >= 2000 && r.ReactionType <= 2005 {
			reactionContent = ReactionTypeToEmoji(r.ReactionType)
		}
		if reactionContent == "" {
			// Skip unknown reaction types
			continue
		}

		// Direction
		direction := "received"
		if r.IsFromMe {
			direction = "sent"
		}

		// Thread ID
		threadID := adapterName + ":" + r.ChatIdentifier

		// Reply to (the original message this reaction is for)
		// The associated_message_guid might have a prefix like "p:0/" that needs to be stripped
		originalGUID := r.AssociatedMessageGUID
		if idx := len(originalGUID) - 36; idx > 0 && len(originalGUID) > 36 {
			// UUID is 36 chars; strip any prefix
			originalGUID = originalGUID[idx:]
		}
		replyTo := adapterName + ":" + originalGUID

		// Event ID (deterministic)
		eventID := adapterName + ":" + r.GUID

		metadata := map[string]any{
			"reaction_type":           r.ReactionType,
			"associated_message_guid": originalGUID,
			"associated_message_type": r.ReactionType,
		}
		if r.Text.Valid && r.Text.String != "" {
			metadata["reaction_text"] = r.Text.String
		}
		if reactionContent != "" {
			metadata["associated_message_emoji"] = reactionContent
		}
		if r.AssociatedMessageGUID != originalGUID {
			metadata["associated_message_guid_raw"] = r.AssociatedMessageGUID
		}
		metadataJSON, err := json.Marshal(metadata)
		if err != nil {
			return created, fmt.Errorf("marshal reaction metadata: %w", err)
		}

		res, err := stmtInsertEvent.Exec(
			eventID, timestamp, "imessage", contentTypesReaction, reactionContent,
			direction, threadID, replyTo, adapterName, r.GUID, string(metadataJSON),
		)
		if err != nil {
			return created, fmt.Errorf("insert reaction event: %w", err)
		}
		if n, _ := res.RowsAffected(); n == 1 {
			created++
		}

		// Add participants
		if r.HandleID.Valid {
			if contactID, ok := handleMap[r.HandleID.Int64]; ok && contactID != "" {
				_, _ = stmtInsertParticipant.Exec(eventID, contactID, "sender")
			}
		}
		if r.IsFromMe && meContactID != "" {
			_, _ = stmtInsertParticipant.Exec(eventID, meContactID, "sender")
		}
	}

	if err := tx.Commit(); err != nil {
		return created, fmt.Errorf("commit tx: %w", err)
	}

	return created, nil
}

func syncMembershipEvents(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, sinceRowID int64, adapterName string, handleMap map[int64]string, meContactID string) (int, error) {
	_ = ctx

	messages, err := chatDB.GetMessages(sinceRowID)
	if err != nil {
		return 0, err
	}
	if len(messages) == 0 {
		return 0, nil
	}

	tx, err := commsDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmtInsertEvent, err := tx.Prepare(`
		INSERT INTO events (
			id, timestamp, channel, content_types, content,
			direction, thread_id, reply_to, source_adapter, source_id, metadata_json
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(source_adapter, source_id) DO UPDATE SET
			channel = excluded.channel,
			content_types = excluded.content_types,
			content = excluded.content,
			direction = excluded.direction,
			thread_id = excluded.thread_id,
			reply_to = excluded.reply_to,
			metadata_json = excluded.metadata_json,
			timestamp = excluded.timestamp
	`)
	if err != nil {
		return 0, fmt.Errorf("prepare insert membership event: %w", err)
	}
	defer stmtInsertEvent.Close()

	stmtInsertParticipant, err := tx.Prepare(`
		INSERT OR IGNORE INTO event_participants (event_id, contact_id, role)
		VALUES (?, ?, ?)
	`)
	if err != nil {
		return 0, fmt.Errorf("prepare insert participant: %w", err)
	}
	defer stmtInsertParticipant.Close()

	const contentTypesMembership = `["membership"]`

	created := 0
	for _, msg := range messages {
		if !isMembershipMessage(msg) {
			continue
		}

		action := mapGroupActionType(msg.GroupActionType.Int64)
		content := ""
		if action != "unknown" {
			content = action
		}

		timestamp := AppleTimestampToUnix(msg.Date)
		direction := "received"
		if msg.IsFromMe {
			direction = "sent"
		}

		threadID := adapterName + ":" + msg.ChatIdentifier
		eventID := adapterName + ":" + msg.GUID

		metadata := map[string]any{
			"action":            action,
			"group_action_type": msg.GroupActionType.Int64,
		}
		if msg.ItemType.Valid {
			metadata["item_type"] = msg.ItemType.Int64
		}
		if msg.MessageActionType.Valid {
			metadata["message_action_type"] = msg.MessageActionType.Int64
		}
		if msg.GroupTitle.Valid && msg.GroupTitle.String != "" {
			metadata["group_title"] = msg.GroupTitle.String
		}
		if msg.OtherHandleID.Valid {
			metadata["other_handle_id"] = msg.OtherHandleID.Int64
			if contactID, ok := handleMap[msg.OtherHandleID.Int64]; ok && contactID != "" {
				metadata["other_contact_id"] = contactID
				_, _ = stmtInsertParticipant.Exec(eventID, contactID, "member")
			}
		}

		metadataJSON, err := json.Marshal(metadata)
		if err != nil {
			return created, fmt.Errorf("marshal membership metadata: %w", err)
		}

		res, err := stmtInsertEvent.Exec(
			eventID, timestamp, "imessage", contentTypesMembership, content,
			direction, threadID, "", adapterName, msg.GUID, string(metadataJSON),
		)
		if err != nil {
			return created, fmt.Errorf("insert membership event: %w", err)
		}
		if n, _ := res.RowsAffected(); n == 1 {
			created++
		}

		if msg.HandleID.Valid {
			if contactID, ok := handleMap[msg.HandleID.Int64]; ok && contactID != "" {
				_, _ = stmtInsertParticipant.Exec(eventID, contactID, "sender")
			}
		}
		if msg.IsFromMe && meContactID != "" {
			_, _ = stmtInsertParticipant.Exec(eventID, meContactID, "sender")
		}
	}

	if err := tx.Commit(); err != nil {
		return created, fmt.Errorf("commit tx: %w", err)
	}

	return created, nil
}

func isMembershipMessage(msg Message) bool {
	return msg.GroupActionType.Valid && msg.GroupActionType.Int64 != 0
}

func isReactionMessage(msg Message) bool {
	if !msg.AssociatedMessageGUID.Valid || msg.AssociatedMessageGUID.String == "" {
		return false
	}
	if msg.MessageType >= 2000 && msg.MessageType <= 2005 {
		return true
	}
	if msg.Text.Valid && ReactionTextToEmoji(msg.Text.String) != "" {
		return true
	}
	return false
}

func mapGroupActionType(actionType int64) string {
	switch actionType {
	case 1:
		return "added"
	case 3:
		return "removed"
	default:
		return "unknown"
	}
}

func updateEventContentTypes(tx *sql.Tx, eventID string, mediaType string) error {
	if mediaType == "" {
		return nil
	}

	var currentJSON string
	err := tx.QueryRow(`SELECT content_types FROM events WHERE id = ?`, eventID).Scan(&currentJSON)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}

	var types []string
	if err := json.Unmarshal([]byte(currentJSON), &types); err != nil {
		return nil
	}

	types = appendContentType(types, "attachment")
	types = appendContentType(types, mediaType)

	updatedJSON, err := json.Marshal(types)
	if err != nil {
		return err
	}
	if string(updatedJSON) == currentJSON {
		return nil
	}

	_, err = tx.Exec(`UPDATE events SET content_types = ? WHERE id = ?`, string(updatedJSON), eventID)
	return err
}

func appendContentType(existing []string, value string) []string {
	if value == "" {
		return existing
	}
	for _, item := range existing {
		if item == value {
			return existing
		}
	}
	return append(existing, value)
}
