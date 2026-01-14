package imessage

import (
	"context"
	"database/sql"
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

	// Sync handles → persons/identities
	handlesStart := time.Now()
	handlesSynced, handleMap, mePersonID, err := syncHandles(ctx, chatDB, commsDB, opts.MePersonID)
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
	messagesSynced, maxRowID, err := syncMessages(ctx, chatDB, commsDB, opts.SinceRowID, adapterName, handleMap, mePersonID)
	if err != nil {
		return nil, fmt.Errorf("failed to sync messages: %w", err)
	}
	result.MessagesSynced = messagesSynced
	result.MaxMessageRowID = maxRowID
	result.Perf["messages"] = time.Since(messagesStart).String()

	// Sync reactions → events with content_types=["reaction"]
	// Must happen BEFORE attachments since some attachments belong to reaction messages
	reactionsStart := time.Now()
	reactionsSynced, err := syncReactions(ctx, chatDB, commsDB, opts.SinceRowID, adapterName, handleMap, mePersonID)
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

// syncHandles syncs handles from chat.db to comms persons/identities
// Returns: count synced, handleID→personID map, mePersonID
func syncHandles(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, existingMePersonID string) (int, map[int64]string, string, error) {
	_ = ctx

	// Get or create "me" person
	mePersonID := existingMePersonID
	if mePersonID == "" {
		_ = commsDB.QueryRow("SELECT id FROM persons WHERE is_me = 1 LIMIT 1").Scan(&mePersonID)
	}
	if mePersonID == "" {
		mePersonID = uuid.New().String()
		now := time.Now().Unix()
		_, err := commsDB.Exec(`
			INSERT INTO persons (id, canonical_name, is_me, created_at, updated_at)
			VALUES (?, ?, 1, ?, ?)
		`, mePersonID, "Me", now, now)
		if err != nil {
			return 0, nil, "", fmt.Errorf("failed to create me person: %w", err)
		}
	}

	// Read handles from chat.db
	handles, err := chatDB.GetHandles()
	if err != nil {
		return 0, nil, mePersonID, err
	}

	if len(handles) == 0 {
		return 0, make(map[int64]string), mePersonID, nil
	}

	// Bulk write in a single transaction
	tx, err := commsDB.Begin()
	if err != nil {
		return 0, nil, mePersonID, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmtInsertPerson, err := tx.Prepare(`
		INSERT INTO persons (id, canonical_name, is_me, created_at, updated_at)
		VALUES (?, ?, 0, ?, ?)
	`)
	if err != nil {
		return 0, nil, mePersonID, fmt.Errorf("prepare insert person: %w", err)
	}
	defer stmtInsertPerson.Close()

	stmtInsertIdentity, err := tx.Prepare(`
		INSERT INTO identities (id, person_id, channel, identifier, created_at)
		VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(channel, identifier) DO NOTHING
	`)
	if err != nil {
		return 0, nil, mePersonID, fmt.Errorf("prepare insert identity: %w", err)
	}
	defer stmtInsertIdentity.Close()

	handleMap := make(map[int64]string) // chat.db handle ROWID → comms person_id
	personsCreated := 0

	for _, handle := range handles {
		normalized, identifierType := NormalizeIdentifier(handle.ID)
		if normalized == "" {
			continue
		}

		// For phone numbers, use E.164 format in identities table
		identifier := normalized
		if identifierType == "phone" {
			identifier = NormalizePhoneE164(handle.ID)
		}

		// Check if person already exists by identifier
		var personID string
		row := tx.QueryRow(`
			SELECT person_id FROM identities
			WHERE channel = ? AND identifier = ?
		`, identifierType, identifier)
		if err := row.Scan(&personID); err != nil && err != sql.ErrNoRows {
			return personsCreated, handleMap, mePersonID, fmt.Errorf("failed to query identity: %w", err)
		}

		// If not found, create new person
		if personID == "" {
			personID = uuid.New().String()
			now := time.Now().Unix()

			// Use identifier as name initially
			canonicalName := normalized
			if _, err := stmtInsertPerson.Exec(personID, canonicalName, now, now); err == nil {
				personsCreated++
			}

			// Create identity
			identityID := uuid.New().String()
			_, _ = stmtInsertIdentity.Exec(identityID, personID, identifierType, identifier, now)
		}

		handleMap[handle.ROWID] = personID
	}

	if err := tx.Commit(); err != nil {
		return personsCreated, handleMap, mePersonID, fmt.Errorf("commit tx: %w", err)
	}

	return personsCreated, handleMap, mePersonID, nil
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
func syncMessages(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, sinceRowID int64, adapterName string, handleMap map[int64]string, mePersonID string) (int, int64, error) {
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
		INSERT OR IGNORE INTO events (
			id, timestamp, channel, content_types, content,
			direction, thread_id, reply_to, source_adapter, source_id
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`)
	if err != nil {
		return 0, 0, fmt.Errorf("prepare insert event: %w", err)
	}
	defer stmtInsertEvent.Close()

	stmtInsertParticipant, err := tx.Prepare(`
		INSERT OR IGNORE INTO event_participants (event_id, person_id, role)
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
			if personID, ok := handleMap[msg.HandleID.Int64]; ok && personID != "" {
				role := "sender"
				if msg.IsFromMe {
					role = "recipient"
				}
				_, _ = stmtInsertParticipant.Exec(eventID, personID, role)
			}
		}
		if msg.IsFromMe && mePersonID != "" {
			_, _ = stmtInsertParticipant.Exec(eventID, mePersonID, "sender")
		} else if !msg.IsFromMe && mePersonID != "" {
			_, _ = stmtInsertParticipant.Exec(eventID, mePersonID, "recipient")
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
	}

	if err := tx.Commit(); err != nil {
		return created, fmt.Errorf("commit tx: %w", err)
	}

	return created, nil
}

// syncReactions syncs reactions from chat.db to comms events
func syncReactions(ctx context.Context, chatDB *ChatDB, commsDB *sql.DB, sinceRowID int64, adapterName string, handleMap map[int64]string, mePersonID string) (int, error) {
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
		INSERT OR IGNORE INTO event_participants (event_id, person_id, role)
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

		res, err := stmtInsertEvent.Exec(
			eventID, timestamp, "imessage", contentTypesReaction, reactionContent,
			direction, threadID, replyTo, adapterName, r.GUID,
		)
		if err != nil {
			return created, fmt.Errorf("insert reaction event: %w", err)
		}
		if n, _ := res.RowsAffected(); n == 1 {
			created++
		}

		// Add participants
		if r.HandleID.Valid {
			if personID, ok := handleMap[r.HandleID.Int64]; ok && personID != "" {
				_, _ = stmtInsertParticipant.Exec(eventID, personID, "sender")
			}
		}
		if r.IsFromMe && mePersonID != "" {
			_, _ = stmtInsertParticipant.Exec(eventID, mePersonID, "sender")
		}
	}

	if err := tx.Commit(); err != nil {
		return created, fmt.Errorf("commit tx: %w", err)
	}

	return created, nil
}
