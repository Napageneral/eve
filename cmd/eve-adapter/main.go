// eve-adapter is the Nexus adapter binary for iMessage via Eve.
//
// It uses Eve's warehouse ETL pipeline (chat.db → eve.db) to provide
// normalized, contact-resolved iMessage data through the Nexus adapter protocol.
//
// Usage:
//
//	eve-adapter info
//	eve-adapter monitor --account default
//	eve-adapter send --account default --to "+14155551234" --text "Hello"
//	eve-adapter backfill --account default --since 2026-01-01
//	eve-adapter health --account default
//	eve-adapter accounts list
package main

import (
	"context"
	"database/sql"
	"fmt"
	"mime"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	nexadapter "github.com/nexus-project/adapter-sdk-go"

	"github.com/Napageneral/eve/internal/config"
	"github.com/Napageneral/eve/internal/etl"
	"github.com/Napageneral/eve/internal/migrate"

	_ "github.com/mattn/go-sqlite3"
)

func main() {
	nexadapter.Run(nexadapter.Adapter{
		Info:     eveInfo,
		Monitor:  eveMonitor,
		Send:     eveSend,
		Backfill: eveBackfill,
		Health:   eveHealth,
		Accounts: eveAccounts,
	})
}

// ---------- Info ----------

func eveInfo() *nexadapter.AdapterInfo {
	return &nexadapter.AdapterInfo{
		Platform: "imessage",
		Name:     "eve",
		Version:  "1.0.0",
		Supports: []nexadapter.Capability{
			nexadapter.CapMonitor,
			nexadapter.CapSend,
			nexadapter.CapBackfill,
			nexadapter.CapHealth,
		},
		MultiAccount: false,
		PlatformCapabilities: nexadapter.ChannelCapabilities{
			TextLimit:             4000,
			SupportsMarkdown:      false,
			SupportsTables:        false,
			SupportsCodeBlocks:    false,
			SupportsEmbeds:        false,
			SupportsThreads:       false,
			SupportsReactions:     true,
			SupportsPolls:         false,
			SupportsButtons:       false,
			SupportsEdit:          false,
			SupportsDelete:        false,
			SupportsMedia:         true,
			SupportsVoiceNotes:    true,
			SupportsStreamingEdit: false,
		},
	}
}

// ---------- Monitor ----------

func eveMonitor(ctx context.Context, account string, emit nexadapter.EmitFunc) error {
	warehouseDB, err := openWarehouse()
	if err != nil {
		return err
	}
	defer warehouseDB.Close()

	chatDB, chatErr := openChatDB()
	if chatDB != nil {
		defer chatDB.Close()
	}
	if chatErr != nil {
		nexadapter.LogInfo("monitor: cannot open chat.db (sync disabled): %v", chatErr)
	}

	meIdentifier := getMeIdentifier(warehouseDB)

	// Start from the current max message ID so we only emit NEW messages.
	var lastSeenID int64
	if err := warehouseDB.QueryRow("SELECT COALESCE(MAX(id), 0) FROM messages").Scan(&lastSeenID); err != nil {
		return fmt.Errorf("failed to get initial cursor: %w", err)
	}

	var lastSeenReactionID int64
	if err := warehouseDB.QueryRow("SELECT COALESCE(MAX(id), 0) FROM reactions").Scan(&lastSeenReactionID); err != nil {
		return fmt.Errorf("failed to get initial reaction cursor: %w", err)
	}

	var lastSeenMembershipID int64
	if err := warehouseDB.QueryRow("SELECT COALESCE(MAX(id), 0) FROM membership_events").Scan(&lastSeenMembershipID); err != nil {
		return fmt.Errorf("failed to get initial membership cursor: %w", err)
	}

	nexadapter.LogInfo(
		"monitor starting from message=%d reaction=%d membership=%d",
		lastSeenID,
		lastSeenReactionID,
		lastSeenMembershipID,
	)

	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			nexadapter.LogInfo("monitor shutting down")
			return nil
		case <-ticker.C:
		}

		// Step 1: Run incremental ETL sync (chat.db → eve.db).
		if chatDB != nil {
			// Use a lookback window because chat.db writes are not always "atomic":
			// a message row can appear before its chat_message_join row, and strict
			// ROWID watermarks can permanently skip messages. Warehouse inserts are
			// idempotent via guid UNIQUE constraints, so reprocessing is safe.
			const lookbackRowIDs int64 = 5000
			sinceRowID := getWatermarkRowID(warehouseDB)
			syncSinceRowID := sinceRowID
			if syncSinceRowID > lookbackRowIDs {
				syncSinceRowID -= lookbackRowIDs
			} else {
				syncSinceRowID = 0
			}

			syncResult, err := etl.FullSync(chatDB, warehouseDB, syncSinceRowID)
			if err != nil {
				nexadapter.LogError("sync failed: %v", err)
			} else if syncResult.MaxMessageRowID > 0 {
				if err := etl.SetWatermark(warehouseDB, "chatdb", "message_rowid", &syncResult.MaxMessageRowID, nil); err != nil {
					nexadapter.LogError("failed to update watermark: %v", err)
				}
			}
		}

		// Step 2: Query warehouse for messages newer than our cursor.
		events, newLastID, err := queryNewMessages(warehouseDB, lastSeenID, meIdentifier)
		if err != nil {
			nexadapter.LogError("failed to query new messages: %v", err)
		} else {
			for _, event := range events {
				emit(event)
			}
			if newLastID > lastSeenID {
				nexadapter.LogDebug(
					"emitted %d message events (cursor %d → %d)",
					len(events),
					lastSeenID,
					newLastID,
				)
				lastSeenID = newLastID
			}
		}

		reactions, newLastReactionID, err := queryNewReactions(warehouseDB, lastSeenReactionID, meIdentifier)
		if err != nil {
			nexadapter.LogError("failed to query new reactions: %v", err)
		} else {
			for _, event := range reactions {
				emit(event)
			}
			if newLastReactionID > lastSeenReactionID {
				nexadapter.LogDebug(
					"emitted %d reaction events (cursor %d → %d)",
					len(reactions),
					lastSeenReactionID,
					newLastReactionID,
				)
				lastSeenReactionID = newLastReactionID
			}
		}

		membership, newLastMembershipID, err := queryNewMembershipEvents(
			warehouseDB,
			lastSeenMembershipID,
			meIdentifier,
		)
		if err != nil {
			nexadapter.LogError("failed to query new membership events: %v", err)
		} else {
			for _, event := range membership {
				emit(event)
			}
			if newLastMembershipID > lastSeenMembershipID {
				nexadapter.LogDebug(
					"emitted %d membership events (cursor %d → %d)",
					len(membership),
					lastSeenMembershipID,
					newLastMembershipID,
				)
				lastSeenMembershipID = newLastMembershipID
			}
		}
	}
}

// ---------- Send ----------

func eveSend(ctx context.Context, req nexadapter.SendRequest) (*nexadapter.DeliveryResult, error) {
	target := strings.TrimSpace(req.To)
	if target == "" {
		target = recipientFromThreadID(req.ThreadID)
	}
	if target == "" {
		return &nexadapter.DeliveryResult{
			Success: false,
			Error: &nexadapter.DeliveryError{
				Type:    "content_rejected",
				Message: "--to is required (or provide --thread)",
				Retry:   false,
			},
		}, nil
	}
	if strings.TrimSpace(req.ReplyToID) != "" {
		return &nexadapter.DeliveryResult{
			Success: false,
			Error: &nexadapter.DeliveryError{
				Type:    "content_rejected",
				Message: "reply_to_id is not supported by the imessage adapter",
				Retry:   false,
			},
		}, nil
	}

	result := nexadapter.SendWithChunking(req.Text, 4000, func(chunk string) (string, error) {
		if err := sendAppleScript(ctx, target, chunk, req.Media); err != nil {
			return "", err
		}
		return fmt.Sprintf("imessage:sent:%d", time.Now().UnixNano()), nil
	})

	return result, nil
}

func recipientFromThreadID(threadID string) string {
	trimmed := strings.TrimSpace(threadID)
	if trimmed == "" {
		return ""
	}
	return strings.TrimPrefix(trimmed, "imessage:")
}

func sendAppleScript(ctx context.Context, recipient, text, media string) error {
	var script string
	if media != "" {
		script = fmt.Sprintf(`tell application "Messages"
	set targetService to 1st account whose service type = iMessage
	set targetBuddy to participant "%s" of targetService
	send "%s" to targetBuddy
	send POSIX file "%s" to targetBuddy
end tell`, escapeAppleScript(recipient), escapeAppleScript(text), escapeAppleScript(media))
	} else {
		script = fmt.Sprintf(`tell application "Messages"
	set targetService to 1st account whose service type = iMessage
	set targetBuddy to participant "%s" of targetService
	send "%s" to targetBuddy
end tell`, escapeAppleScript(recipient), escapeAppleScript(text))
	}

	cmd := exec.CommandContext(ctx, "osascript", "-e", script)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("AppleScript failed: %s (output: %s)", err, string(output))
	}
	return nil
}

// ---------- Backfill ----------

func eveBackfill(ctx context.Context, account string, since time.Time, emit nexadapter.EmitFunc) error {
	warehouseDB, err := openWarehouse()
	if err != nil {
		return err
	}
	defer warehouseDB.Close()

	chatDB, chatErr := openChatDB()
	if chatDB != nil {
		defer chatDB.Close()
	}
	if chatErr != nil {
		nexadapter.LogInfo("backfill: cannot open chat.db (will emit from warehouse only): %v", chatErr)
	}

	meIdentifier := getMeIdentifier(warehouseDB)

	// Best-effort: Ensure warehouse is up to date before backfilling.
	if chatDB != nil {
		nexadapter.LogInfo("running sync before backfill...")
		const lookbackRowIDs int64 = 5000
		sinceRowID := getWatermarkRowID(warehouseDB)
		syncSinceRowID := sinceRowID
		if syncSinceRowID > lookbackRowIDs {
			syncSinceRowID -= lookbackRowIDs
		} else {
			syncSinceRowID = 0
		}

		syncResult, err := etl.FullSync(chatDB, warehouseDB, syncSinceRowID)
		if err != nil {
			nexadapter.LogInfo("pre-backfill sync failed (continuing with existing warehouse): %v", err)
		} else if syncResult.MaxMessageRowID > 0 {
			_ = etl.SetWatermark(warehouseDB, "chatdb", "message_rowid", &syncResult.MaxMessageRowID, nil)
		}
	}

	nexadapter.LogInfo("sync complete, starting backfill from %s", since.Format(time.RFC3339))

	// Paginated query — process in batches of 5000 to keep memory bounded.
	const batchSize = 5000
	totalEmitted := 0

	// Messages
	{
		var lastID int64
		for {
			select {
			case <-ctx.Done():
				nexadapter.LogInfo("backfill cancelled after %d events", totalEmitted)
				return nil
			default:
			}

			events, newLastID, err := queryMessagesSince(warehouseDB, since, lastID, batchSize, meIdentifier)
			if err != nil {
				return fmt.Errorf("backfill message query failed: %w", err)
			}
			if len(events) == 0 {
				break
			}
			for _, event := range events {
				emit(event)
			}
			totalEmitted += len(events)
			lastID = newLastID
			nexadapter.LogDebug("backfill progress: %d events emitted", totalEmitted)
		}
	}

	// Reactions
	{
		var lastID int64
		for {
			select {
			case <-ctx.Done():
				nexadapter.LogInfo("backfill cancelled after %d events", totalEmitted)
				return nil
			default:
			}

			events, newLastID, err := queryReactionsSince(warehouseDB, since, lastID, batchSize, meIdentifier)
			if err != nil {
				return fmt.Errorf("backfill reaction query failed: %w", err)
			}
			if len(events) == 0 {
				break
			}
			for _, event := range events {
				emit(event)
			}
			totalEmitted += len(events)
			lastID = newLastID
			nexadapter.LogDebug("backfill progress: %d events emitted", totalEmitted)
		}
	}

	// Membership events
	{
		var lastID int64
		for {
			select {
			case <-ctx.Done():
				nexadapter.LogInfo("backfill cancelled after %d events", totalEmitted)
				return nil
			default:
			}

			events, newLastID, err := queryMembershipEventsSince(
				warehouseDB,
				since,
				lastID,
				batchSize,
				meIdentifier,
			)
			if err != nil {
				return fmt.Errorf("backfill membership query failed: %w", err)
			}
			if len(events) == 0 {
				break
			}
			for _, event := range events {
				emit(event)
			}
			totalEmitted += len(events)
			lastID = newLastID
			nexadapter.LogDebug("backfill progress: %d events emitted", totalEmitted)
		}
	}

	nexadapter.LogInfo("backfill complete: %d events emitted", totalEmitted)
	return nil
}

// ---------- Health ----------

func eveHealth(_ context.Context, _ string) (*nexadapter.AdapterHealth, error) {
	// Check chat.db accessibility.
	chatDBPath := etl.GetChatDBPath()
	if chatDBPath == "" {
		return &nexadapter.AdapterHealth{
			Connected: false,
			Account:   "default",
			Error:     "cannot determine chat.db path",
		}, nil
	}

	chatDB, err := etl.OpenChatDB(chatDBPath)
	if err != nil {
		return &nexadapter.AdapterHealth{
			Connected: false,
			Account:   "default",
			Error:     fmt.Sprintf("cannot open chat.db: %v", err),
			Details:   map[string]any{"chat_db_path": chatDBPath},
		}, nil
	}
	chatDB.Close()

	// Check warehouse accessibility.
	cfg := config.Load()
	warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
	if err != nil {
		return &nexadapter.AdapterHealth{
			Connected: false,
			Account:   "default",
			Error:     fmt.Sprintf("cannot open eve.db: %v", err),
			Details:   map[string]any{"chat_db_path": chatDBPath, "warehouse_path": cfg.EveDBPath},
		}, nil
	}
	defer warehouseDB.Close()

	// Get latest message timestamp and count.
	var lastEventAt int64
	var lastTS sql.NullString
	_ = warehouseDB.QueryRow("SELECT MAX(timestamp) FROM messages").Scan(&lastTS)
	if lastTS.Valid {
		lastEventAt = parseTimestampMs(lastTS.String)
	}

	var msgCount int64
	_ = warehouseDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&msgCount)

	return &nexadapter.AdapterHealth{
		Connected:   true,
		Account:     "default",
		LastEventAt: lastEventAt,
		Details: map[string]any{
			"chat_db_path":   chatDBPath,
			"warehouse_path": cfg.EveDBPath,
			"message_count":  msgCount,
		},
	}, nil
}

// ---------- Accounts ----------

func eveAccounts(_ context.Context) ([]nexadapter.AdapterAccount, error) {
	return []nexadapter.AdapterAccount{
		{
			ID:          "default",
			DisplayName: getFullName(),
			Status:      "active",
		},
	}, nil
}

// =====================================================================
// Warehouse query helpers
// =====================================================================

// warehouseRow holds a single row from the warehouse messages join query.
type warehouseRow struct {
	ID               int64
	SenderContactID  sql.NullInt64
	Content          sql.NullString
	Timestamp        sql.NullString
	IsFromMe         bool
	GUID             string
	ServiceName      sql.NullString
	ReplyToGUID      sql.NullString
	ChatID           int64
	SenderName       sql.NullString
	SenderIdentifier sql.NullString
	ChatIdentifier   string
	IsGroup          bool
	ChatName         sql.NullString
}

// Base query joining messages → contacts → contact_identifiers → chats.
// The sender_identifier subquery picks the primary (or first) identifier
// for the contact, giving us a phone number or email for the sender.
const warehouseMessageQuery = `
SELECT
	m.id, m.sender_id, m.content, m.timestamp, m.is_from_me, m.guid,
	m.service_name, m.reply_to_guid, m.chat_id,
	c.name,
	(SELECT ci.identifier FROM contact_identifiers ci
	 WHERE ci.contact_id = m.sender_id
	 ORDER BY ci.is_primary DESC LIMIT 1),
	ch.chat_identifier, ch.is_group, ch.chat_name
FROM messages m
LEFT JOIN contacts c ON m.sender_id = c.id
LEFT JOIN chats ch ON m.chat_id = ch.id
`

// queryNewMessages returns events for messages with id > sinceID.
func queryNewMessages(
	db *sql.DB,
	sinceID int64,
	meIdentifier string,
) ([]nexadapter.NexusEvent, int64, error) {
	rows, err := db.Query(warehouseMessageQuery+"WHERE m.id > ? ORDER BY m.id", sinceID)
	if err != nil {
		return nil, sinceID, fmt.Errorf("query failed: %w", err)
	}
	defer rows.Close()

	var (
		messageRows []warehouseRow
		firstID     int64
		lastID      = sinceID
	)

	for rows.Next() {
		var row warehouseRow
		if err := scanWarehouseRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		if firstID == 0 {
			firstID = row.ID
		}
		messageRows = append(messageRows, row)
		lastID = row.ID
	}

	if err := rows.Err(); err != nil {
		return nil, lastID, err
	}

	attachmentsByMessageID := map[int64][]nexadapter.Attachment{}
	if len(messageRows) > 0 {
		var err error
		attachmentsByMessageID, err = queryAttachmentsForMessageIDRange(db, firstID, lastID)
		if err != nil {
			return nil, lastID, err
		}
	}

	events := make([]nexadapter.NexusEvent, 0, len(messageRows))
	for _, row := range messageRows {
		events = append(events, convertWarehouseMessage(row, attachmentsByMessageID[row.ID], meIdentifier))
	}

	return events, lastID, nil
}

// queryMessagesSince returns events for messages with timestamp >= since AND id > afterID, paginated.
func queryMessagesSince(
	db *sql.DB,
	since time.Time,
	afterID int64,
	limit int,
	meIdentifier string,
) ([]nexadapter.NexusEvent, int64, error) {
	// Format since in the same style go-sqlite3 uses for storage ("2006-01-02 15:04:05+00:00").
	sinceStr := since.UTC().Format("2006-01-02 15:04:05+00:00")

	q := warehouseMessageQuery + "WHERE m.timestamp >= ? AND m.id > ? ORDER BY m.id LIMIT ?"
	rows, err := db.Query(q, sinceStr, afterID, limit)
	if err != nil {
		return nil, afterID, fmt.Errorf("query failed: %w", err)
	}
	defer rows.Close()

	var (
		messageRows []warehouseRow
		firstID     int64
		lastID      = afterID
	)

	for rows.Next() {
		var row warehouseRow
		if err := scanWarehouseRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		if firstID == 0 {
			firstID = row.ID
		}
		messageRows = append(messageRows, row)
		lastID = row.ID
	}

	if err := rows.Err(); err != nil {
		return nil, lastID, err
	}

	attachmentsByMessageID := map[int64][]nexadapter.Attachment{}
	if len(messageRows) > 0 {
		var err error
		attachmentsByMessageID, err = queryAttachmentsForMessageIDRange(db, firstID, lastID)
		if err != nil {
			return nil, lastID, err
		}
	}

	events := make([]nexadapter.NexusEvent, 0, len(messageRows))
	for _, row := range messageRows {
		events = append(events, convertWarehouseMessage(row, attachmentsByMessageID[row.ID], meIdentifier))
	}

	return events, lastID, nil
}

func scanWarehouseRow(rows *sql.Rows, row *warehouseRow) error {
	return rows.Scan(
		&row.ID, &row.SenderContactID, &row.Content, &row.Timestamp, &row.IsFromMe, &row.GUID,
		&row.ServiceName, &row.ReplyToGUID, &row.ChatID,
		&row.SenderName, &row.SenderIdentifier,
		&row.ChatIdentifier, &row.IsGroup, &row.ChatName,
	)
}

// =====================================================================
// NexusEvent conversion
// =====================================================================

// cachedFullName stores the local user's full name (from `id -F`).
// Resolved once, used for all is_from_me messages.
var cachedFullName string

func convertWarehouseMessage(
	row warehouseRow,
	attachments []nexadapter.Attachment,
	meIdentifier string,
) nexadapter.NexusEvent {
	peerKind := "dm"
	if row.IsGroup {
		peerKind = "group"
	}

	// Parse timestamp.
	var timestampMs int64
	if row.Timestamp.Valid {
		timestampMs = parseTimestampMs(row.Timestamp.String)
	}

	content := ""
	if row.Content.Valid {
		content = row.Content.String
	}

	// Determine sender.
	// In chat.db, is_from_me messages have handle_id pointing to the recipient
	// (in DMs) or NULL (in groups). The warehouse inherits this, so sender_id
	// is wrong for outgoing messages. We correct it here.
	var senderID, senderName string
	if row.IsFromMe {
		senderID = meIdentifier
		if senderID == "" {
			senderID = "me"
		}
		senderName = getFullName()
	} else {
		if row.SenderIdentifier.Valid {
			senderID = row.SenderIdentifier.String
		}
		if row.SenderName.Valid {
			senderName = row.SenderName.String
		}
	}

	serviceName := ""
	if row.ServiceName.Valid {
		serviceName = row.ServiceName.String
	}

	threadID := deriveThreadID(row.ChatIdentifier, row.ChatID)

	b := nexadapter.NewEvent("imessage", "imessage:"+row.GUID).
		WithTimestampUnixMs(timestampMs).
		WithContent(content).
		WithContentType("text").
		WithSender(senderID, senderName).
		WithContainer(row.ChatIdentifier, peerKind).
		WithThread(threadID).
		WithAccount("default").
		WithMetadata("is_from_me", row.IsFromMe).
		WithMetadata("chat_id", row.ChatID).
		WithMetadata("service", serviceName)

	if row.SenderContactID.Valid {
		b.WithMetadata("sender_handle_id", row.SenderContactID.Int64)
	}

	if row.ReplyToGUID.Valid && row.ReplyToGUID.String != "" {
		replyTo := "imessage:" + row.ReplyToGUID.String
		b.WithReplyTo(replyTo)
		b.WithMetadata("reply_to", replyTo)
	}

	for _, att := range attachments {
		b.WithAttachment(att)
	}

	return b.Build()
}

func deriveThreadID(chatIdentifier string, chatID int64) string {
	chatIdentifier = strings.TrimSpace(chatIdentifier)
	if chatIdentifier != "" {
		return "imessage:" + chatIdentifier
	}
	return fmt.Sprintf("imessage:chat_id:%d", chatID)
}

func queryAttachmentsForMessageIDRange(
	db *sql.DB,
	minMessageID int64,
	maxMessageID int64,
) (map[int64][]nexadapter.Attachment, error) {
	rows, err := db.Query(`
		SELECT message_id, file_name, mime_type, size, guid
		FROM attachments
		WHERE message_id >= ? AND message_id <= ?
		ORDER BY message_id, id
	`, minMessageID, maxMessageID)
	if err != nil {
		return nil, fmt.Errorf("attachments query failed: %w", err)
	}
	defer rows.Close()

	out := make(map[int64][]nexadapter.Attachment)
	for rows.Next() {
		var (
			messageID int64
			fileName  sql.NullString
			mimeType  sql.NullString
			size      sql.NullInt64
			guid      string
		)

		if err := rows.Scan(&messageID, &fileName, &mimeType, &size, &guid); err != nil {
			return nil, fmt.Errorf("attachments scan failed: %w", err)
		}

		fullPath := strings.TrimSpace(fileName.String)
		baseName := ""
		if fullPath != "" {
			baseName = filepath.Base(fullPath)
		}

		ct := strings.TrimSpace(strings.ToLower(mimeType.String))
		if ct == "" && baseName != "" {
			guessed := strings.ToLower(strings.TrimSpace(mime.TypeByExtension(filepath.Ext(baseName))))
			if guessed != "" {
				if semi := strings.IndexByte(guessed, ';'); semi >= 0 {
					guessed = strings.TrimSpace(guessed[:semi])
				}
				ct = guessed
			}
		}
		if ct == "" {
			ct = "application/octet-stream"
		}

		att := nexadapter.Attachment{
			ID:          "imessage:attachment:" + guid,
			Filename:    baseName,
			ContentType: ct,
		}
		if size.Valid && size.Int64 > 0 {
			att.SizeBytes = size.Int64
		}
		if fullPath != "" {
			att.Path = fullPath
		}

		out[messageID] = append(out[messageID], att)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

// =====================================================================
// Reactions + membership events
// =====================================================================

type warehouseReactionRow struct {
	ID                  int64
	GUID                string
	OriginalMessageGUID string
	Timestamp           sql.NullString
	IsFromMe            bool
	ChatID              int64
	SenderContactID     sql.NullInt64
	ReactionType        sql.NullInt64
	SenderName          sql.NullString
	SenderIdentifier    sql.NullString
	ChatIdentifier      string
	IsGroup             bool
	ChatName            sql.NullString
}

const warehouseReactionQuery = `
SELECT
	r.id, r.guid, r.original_message_guid, r.timestamp, r.is_from_me,
	r.chat_id, r.sender_id, r.reaction_type,
	c.name,
	(SELECT ci.identifier FROM contact_identifiers ci
	 WHERE ci.contact_id = r.sender_id
	 ORDER BY ci.is_primary DESC LIMIT 1),
	ch.chat_identifier, ch.is_group, ch.chat_name
FROM reactions r
LEFT JOIN contacts c ON r.sender_id = c.id
LEFT JOIN chats ch ON r.chat_id = ch.id
`

func scanWarehouseReactionRow(rows *sql.Rows, row *warehouseReactionRow) error {
	return rows.Scan(
		&row.ID,
		&row.GUID,
		&row.OriginalMessageGUID,
		&row.Timestamp,
		&row.IsFromMe,
		&row.ChatID,
		&row.SenderContactID,
		&row.ReactionType,
		&row.SenderName,
		&row.SenderIdentifier,
		&row.ChatIdentifier,
		&row.IsGroup,
		&row.ChatName,
	)
}

func queryNewReactions(db *sql.DB, sinceID int64, meIdentifier string) ([]nexadapter.NexusEvent, int64, error) {
	rows, err := db.Query(warehouseReactionQuery+"WHERE r.id > ? ORDER BY r.id", sinceID)
	if err != nil {
		return nil, sinceID, fmt.Errorf("reaction query failed: %w", err)
	}
	defer rows.Close()

	var events []nexadapter.NexusEvent
	lastID := sinceID

	for rows.Next() {
		var row warehouseReactionRow
		if err := scanWarehouseReactionRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		events = append(events, convertWarehouseReaction(row, meIdentifier))
		lastID = row.ID
	}

	return events, lastID, rows.Err()
}

func queryReactionsSince(
	db *sql.DB,
	since time.Time,
	afterID int64,
	limit int,
	meIdentifier string,
) ([]nexadapter.NexusEvent, int64, error) {
	sinceStr := since.UTC().Format("2006-01-02 15:04:05+00:00")
	rows, err := db.Query(
		warehouseReactionQuery+"WHERE r.timestamp >= ? AND r.id > ? ORDER BY r.id LIMIT ?",
		sinceStr,
		afterID,
		limit,
	)
	if err != nil {
		return nil, afterID, fmt.Errorf("reaction query failed: %w", err)
	}
	defer rows.Close()

	var events []nexadapter.NexusEvent
	lastID := afterID

	for rows.Next() {
		var row warehouseReactionRow
		if err := scanWarehouseReactionRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		events = append(events, convertWarehouseReaction(row, meIdentifier))
		lastID = row.ID
	}

	return events, lastID, rows.Err()
}

func convertWarehouseReaction(row warehouseReactionRow, meIdentifier string) nexadapter.NexusEvent {
	peerKind := "dm"
	if row.IsGroup {
		peerKind = "group"
	}

	// Parse timestamp.
	var timestampMs int64
	if row.Timestamp.Valid {
		timestampMs = parseTimestampMs(row.Timestamp.String)
	}

	peerID := strings.TrimSpace(row.ChatIdentifier)
	if peerID == "" {
		peerID = fmt.Sprintf("chat_id:%d", row.ChatID)
	}

	// Determine sender.
	var senderID, senderName string
	if row.IsFromMe {
		senderID = meIdentifier
		if senderID == "" {
			senderID = "me"
		}
		senderName = getFullName()
	} else {
		if row.SenderIdentifier.Valid {
			senderID = row.SenderIdentifier.String
		}
		if row.SenderName.Valid {
			senderName = row.SenderName.String
		}
	}
	if strings.TrimSpace(senderID) == "" {
		senderID = "unknown"
	}

	threadID := deriveThreadID(row.ChatIdentifier, row.ChatID)
	replyTo := "imessage:" + row.OriginalMessageGUID
	emoji := mapReactionType(row.ReactionType.Int64)

	b := nexadapter.NewEvent("imessage", "imessage:reaction:"+row.GUID).
		WithTimestampUnixMs(timestampMs).
		WithContent(emoji).
		WithContentType("reaction").
		WithSender(senderID, senderName).
		WithContainer(peerID, peerKind).
		WithThread(threadID).
		WithReplyTo(replyTo).
		WithAccount("default").
		WithMetadata("is_from_me", row.IsFromMe).
		WithMetadata("chat_id", row.ChatID).
		WithMetadata("reaction_type", row.ReactionType.Int64).
		WithMetadata("original_guid", row.OriginalMessageGUID).
		WithMetadata("reply_to", replyTo)

	if row.SenderContactID.Valid {
		b.WithMetadata("sender_handle_id", row.SenderContactID.Int64)
	}

	return b.Build()
}

type warehouseMembershipRow struct {
	ID                int64
	GUID              string
	ActorID           sql.NullInt64
	MemberID          sql.NullInt64
	ActionType        sql.NullInt64
	ItemType          sql.NullInt64
	MessageActionType sql.NullInt64
	GroupTitle        sql.NullString
	Timestamp         sql.NullString
	IsFromMe          bool
	ChatID            int64
	ActorName         sql.NullString
	ActorIdentifier   sql.NullString
	ChatIdentifier    string
	IsGroup           bool
	ChatName          sql.NullString
}

const warehouseMembershipQuery = `
SELECT
	me.id, me.guid,
	me.actor_id, me.member_id, me.action_type, me.item_type, me.message_action_type,
	me.group_title, me.timestamp, me.is_from_me, me.chat_id,
	actor.name,
	(SELECT ci.identifier FROM contact_identifiers ci
	 WHERE ci.contact_id = me.actor_id
	 ORDER BY ci.is_primary DESC LIMIT 1),
	ch.chat_identifier, ch.is_group, ch.chat_name
FROM membership_events me
LEFT JOIN contacts actor ON me.actor_id = actor.id
LEFT JOIN chats ch ON me.chat_id = ch.id
`

func scanWarehouseMembershipRow(rows *sql.Rows, row *warehouseMembershipRow) error {
	return rows.Scan(
		&row.ID,
		&row.GUID,
		&row.ActorID,
		&row.MemberID,
		&row.ActionType,
		&row.ItemType,
		&row.MessageActionType,
		&row.GroupTitle,
		&row.Timestamp,
		&row.IsFromMe,
		&row.ChatID,
		&row.ActorName,
		&row.ActorIdentifier,
		&row.ChatIdentifier,
		&row.IsGroup,
		&row.ChatName,
	)
}

func queryNewMembershipEvents(
	db *sql.DB,
	sinceID int64,
	meIdentifier string,
) ([]nexadapter.NexusEvent, int64, error) {
	rows, err := db.Query(warehouseMembershipQuery+"WHERE me.id > ? ORDER BY me.id", sinceID)
	if err != nil {
		return nil, sinceID, fmt.Errorf("membership query failed: %w", err)
	}
	defer rows.Close()

	var events []nexadapter.NexusEvent
	lastID := sinceID

	for rows.Next() {
		var row warehouseMembershipRow
		if err := scanWarehouseMembershipRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		events = append(events, convertWarehouseMembership(row, meIdentifier))
		lastID = row.ID
	}

	return events, lastID, rows.Err()
}

func queryMembershipEventsSince(
	db *sql.DB,
	since time.Time,
	afterID int64,
	limit int,
	meIdentifier string,
) ([]nexadapter.NexusEvent, int64, error) {
	sinceStr := since.UTC().Format("2006-01-02 15:04:05+00:00")
	rows, err := db.Query(
		warehouseMembershipQuery+"WHERE me.timestamp >= ? AND me.id > ? ORDER BY me.id LIMIT ?",
		sinceStr,
		afterID,
		limit,
	)
	if err != nil {
		return nil, afterID, fmt.Errorf("membership query failed: %w", err)
	}
	defer rows.Close()

	var events []nexadapter.NexusEvent
	lastID := afterID

	for rows.Next() {
		var row warehouseMembershipRow
		if err := scanWarehouseMembershipRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		events = append(events, convertWarehouseMembership(row, meIdentifier))
		lastID = row.ID
	}

	return events, lastID, rows.Err()
}

func convertWarehouseMembership(row warehouseMembershipRow, meIdentifier string) nexadapter.NexusEvent {
	peerKind := "dm"
	if row.IsGroup {
		peerKind = "group"
	}

	// Parse timestamp.
	var timestampMs int64
	if row.Timestamp.Valid {
		timestampMs = parseTimestampMs(row.Timestamp.String)
	}

	peerID := strings.TrimSpace(row.ChatIdentifier)
	if peerID == "" {
		peerID = fmt.Sprintf("chat_id:%d", row.ChatID)
	}

	action := mapGroupActionType(row.ActionType.Int64)

	// Determine sender.
	var senderID, senderName string
	if row.IsFromMe {
		senderID = meIdentifier
		if senderID == "" {
			senderID = "me"
		}
		senderName = getFullName()
	} else {
		if row.ActorIdentifier.Valid {
			senderID = row.ActorIdentifier.String
		}
		if row.ActorName.Valid {
			senderName = row.ActorName.String
		}
	}
	if strings.TrimSpace(senderID) == "" {
		senderID = "unknown"
	}

	threadID := deriveThreadID(row.ChatIdentifier, row.ChatID)

	b := nexadapter.NewEvent("imessage", "imessage:membership:"+row.GUID).
		WithTimestampUnixMs(timestampMs).
		WithContent(action).
		WithContentType("membership").
		WithSender(senderID, senderName).
		WithContainer(peerID, peerKind).
		WithThread(threadID).
		WithAccount("default").
		WithMetadata("is_from_me", row.IsFromMe).
		WithMetadata("chat_id", row.ChatID).
		WithMetadata("action", action).
		WithMetadata("group_action_type", row.ActionType.Int64).
		WithMetadata("membership_rowid", row.ID)

	if row.ActorID.Valid {
		b.WithMetadata("actor_handle_id", row.ActorID.Int64)
	}
	if row.MemberID.Valid {
		b.WithMetadata("member_handle_id", row.MemberID.Int64)
	}
	if row.ItemType.Valid {
		b.WithMetadata("item_type", row.ItemType.Int64)
	}
	if row.MessageActionType.Valid {
		b.WithMetadata("message_action_type", row.MessageActionType.Int64)
	}
	if row.GroupTitle.Valid && strings.TrimSpace(row.GroupTitle.String) != "" {
		b.WithMetadata("group_title", strings.TrimSpace(row.GroupTitle.String))
	}

	return b.Build()
}

func mapReactionType(reactionType int64) string {
	// iMessage reaction types (from iMessage database)
	reactionMap := map[int64]string{
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

func mapGroupActionType(actionType int64) string {
	switch actionType {
	case 1:
		return "removed"
	case 3:
		return "added"
	default:
		return "unknown"
	}
}

// =====================================================================
// Database helpers
// =====================================================================

// openWarehouse opens Eve's warehouse database (eve.db, read-write),
// running migrations if needed.
func openWarehouse() (*sql.DB, error) {
	cfg := config.Load()

	// Auto-initialize warehouse if it doesn't exist.
	if err := os.MkdirAll(cfg.AppDir, 0755); err != nil {
		return nil, fmt.Errorf("failed to create app directory: %w", err)
	}
	if err := migrate.MigrateWarehouse(cfg.EveDBPath); err != nil {
		return nil, fmt.Errorf("warehouse migration failed: %w", err)
	}

	// Open warehouse.
	warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open warehouse: %w", err)
	}

	// SQLite is single-writer. Use a single pooled connection and a busy timeout
	// so the monitor can ETL + query without flapping on transient locks.
	warehouseDB.SetMaxOpenConns(1)
	warehouseDB.SetMaxIdleConns(1)

	// PRAGMAs apply per-connection; with MaxOpenConns(1) this is sufficient.
	pragmas := []string{
		"PRAGMA foreign_keys=ON;",
		"PRAGMA busy_timeout=10000;",
		"PRAGMA journal_mode=WAL;",
		"PRAGMA synchronous=NORMAL;",
	}
	for _, pragma := range pragmas {
		if _, err := warehouseDB.Exec(pragma); err != nil {
			_ = warehouseDB.Close()
			return nil, fmt.Errorf("failed to set %s: %w", pragma, err)
		}
	}

	return warehouseDB, nil
}

// openChatDB opens chat.db (read-only). This can fail if the binary lacks Full Disk Access.
func openChatDB() (*etl.ChatDB, error) {
	chatDBPath := etl.GetChatDBPath()
	if chatDBPath == "" {
		return nil, fmt.Errorf("cannot determine chat.db path")
	}
	chatDB, err := etl.OpenChatDB(chatDBPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open chat.db: %w", err)
	}
	return chatDB, nil
}

// getWatermarkRowID reads the current chatdb/message_rowid watermark.
func getWatermarkRowID(db *sql.DB) int64 {
	wm, err := etl.GetWatermark(db, "chatdb", "message_rowid")
	if err != nil || wm == nil || !wm.ValueInt.Valid {
		return 0
	}
	return wm.ValueInt.Int64
}

// =====================================================================
// Utility helpers
// =====================================================================

func escapeAppleScript(s string) string {
	s = strings.ReplaceAll(s, "\\", "\\\\")
	s = strings.ReplaceAll(s, "\"", "\\\"")
	return s
}

func getFullName() string {
	if cachedFullName != "" {
		return cachedFullName
	}
	out, err := exec.Command("id", "-F").Output()
	if err != nil {
		return "Unknown"
	}
	cachedFullName = strings.TrimSpace(string(out))
	return cachedFullName
}

// cachedMeIdentifier stores the best-effort local user's identifier from the warehouse.
// This is usually a phone number or email (preferred over "me").
var cachedMeIdentifier string

func getMeIdentifier(db *sql.DB) string {
	if cachedMeIdentifier != "" {
		return cachedMeIdentifier
	}
	if db == nil {
		return ""
	}

	var identifier sql.NullString
	err := db.QueryRow(`
		SELECT ci.identifier
		FROM contacts c
		JOIN contact_identifiers ci ON ci.contact_id = c.id
		WHERE c.is_me = 1
		ORDER BY
			CASE ci.type
				WHEN 'phone' THEN 1
				WHEN 'email' THEN 2
				WHEN 'handle' THEN 3
				ELSE 4
			END,
			ci.is_primary DESC,
			COALESCE(ci.last_used, '') DESC
		LIMIT 1
	`).Scan(&identifier)
	if err != nil {
		return ""
	}

	cachedMeIdentifier = strings.TrimSpace(identifier.String)
	return cachedMeIdentifier
}

// parseTimestampMs parses a warehouse timestamp string into Unix milliseconds.
// Handles the go-sqlite3 storage format: "2006-01-02 15:04:05.999999999+00:00".
func parseTimestampMs(s string) int64 {
	// go-sqlite3 stores time.Time as "2006-01-02 15:04:05.999999999+00:00"
	formats := []string{
		"2006-01-02 15:04:05.999999999+00:00",
		"2006-01-02 15:04:05.999999999-07:00",
		"2006-01-02T15:04:05.999999999Z07:00",
		"2006-01-02 15:04:05+00:00",
		"2006-01-02T15:04:05Z",
		"2006-01-02 15:04:05",
		"2006-01-02",
	}
	for _, f := range formats {
		if t, err := time.Parse(f, s); err == nil {
			return t.UnixMilli()
		}
	}
	return 0
}
