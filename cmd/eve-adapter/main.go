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
	"os"
	"os/exec"
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
		Channel: "imessage",
		Name:    "eve",
		Version: "1.0.0",
		Supports: []nexadapter.Capability{
			nexadapter.CapMonitor,
			nexadapter.CapSend,
			nexadapter.CapBackfill,
			nexadapter.CapHealth,
		},
		MultiAccount: false,
		ChannelCapabilities: nexadapter.ChannelCapabilities{
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
	chatDB, warehouseDB, err := openDatabases()
	if err != nil {
		return err
	}
	defer chatDB.Close()
	defer warehouseDB.Close()

	// Start from the current max message ID so we only emit NEW messages.
	var lastSeenID int64
	if err := warehouseDB.QueryRow("SELECT COALESCE(MAX(id), 0) FROM messages").Scan(&lastSeenID); err != nil {
		return fmt.Errorf("failed to get initial cursor: %w", err)
	}

	nexadapter.LogInfo("monitor starting from message ID %d", lastSeenID)

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
		sinceRowID := getWatermarkRowID(warehouseDB)

		syncResult, err := etl.FullSync(chatDB, warehouseDB, sinceRowID)
		if err != nil {
			nexadapter.LogError("sync failed: %v", err)
			continue
		}

		// Update watermark.
		if syncResult.MaxMessageRowID > 0 {
			if err := etl.SetWatermark(warehouseDB, "chatdb", "message_rowid", &syncResult.MaxMessageRowID, nil); err != nil {
				nexadapter.LogError("failed to update watermark: %v", err)
			}
		}

		// Step 2: Query warehouse for messages newer than our cursor.
		events, newLastID, err := queryNewMessages(warehouseDB, lastSeenID)
		if err != nil {
			nexadapter.LogError("failed to query new messages: %v", err)
			continue
		}

		// Step 3: Emit events.
		for _, event := range events {
			emit(event)
		}

		if newLastID > lastSeenID {
			nexadapter.LogDebug("emitted %d events (cursor %d → %d)", len(events), lastSeenID, newLastID)
			lastSeenID = newLastID
		}
	}
}

// ---------- Send ----------

func eveSend(ctx context.Context, req nexadapter.SendRequest) (*nexadapter.DeliveryResult, error) {
	if req.Target == "" {
		return &nexadapter.DeliveryResult{
			Success: false,
			Error: &nexadapter.DeliveryError{
				Type:    "content_rejected",
				Message: "--to is required",
				Retry:   false,
			},
		}, nil
	}

	result := nexadapter.SendWithChunking(req.Text, 4000, func(chunk string) (string, error) {
		if err := sendAppleScript(ctx, req.Target, chunk, req.Media); err != nil {
			return "", err
		}
		return fmt.Sprintf("imessage:sent:%d", time.Now().UnixNano()), nil
	})

	return result, nil
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
	chatDB, warehouseDB, err := openDatabases()
	if err != nil {
		return err
	}
	defer chatDB.Close()
	defer warehouseDB.Close()

	// Ensure warehouse is up to date before backfilling.
	nexadapter.LogInfo("running sync before backfill...")
	sinceRowID := getWatermarkRowID(warehouseDB)

	syncResult, err := etl.FullSync(chatDB, warehouseDB, sinceRowID)
	if err != nil {
		return fmt.Errorf("pre-backfill sync failed: %w", err)
	}
	if syncResult.MaxMessageRowID > 0 {
		_ = etl.SetWatermark(warehouseDB, "chatdb", "message_rowid", &syncResult.MaxMessageRowID, nil)
	}

	nexadapter.LogInfo("sync complete, starting backfill from %s", since.Format(time.RFC3339))

	// Paginated query — process in batches of 5000 to keep memory bounded.
	const batchSize = 5000
	var lastID int64
	totalEmitted := 0

	for {
		select {
		case <-ctx.Done():
			nexadapter.LogInfo("backfill cancelled after %d events", totalEmitted)
			return nil
		default:
		}

		events, newLastID, err := queryMessagesSince(warehouseDB, since, lastID, batchSize)
		if err != nil {
			return fmt.Errorf("backfill query failed: %w", err)
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
const warehouseQuery = `
SELECT
	m.id, m.content, m.timestamp, m.is_from_me, m.guid,
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
func queryNewMessages(db *sql.DB, sinceID int64) ([]nexadapter.NexusEvent, int64, error) {
	rows, err := db.Query(warehouseQuery+"WHERE m.id > ? ORDER BY m.id", sinceID)
	if err != nil {
		return nil, sinceID, fmt.Errorf("query failed: %w", err)
	}
	defer rows.Close()

	var events []nexadapter.NexusEvent
	lastID := sinceID

	for rows.Next() {
		var row warehouseRow
		if err := scanWarehouseRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		events = append(events, convertWarehouseMessage(row))
		lastID = row.ID
	}

	return events, lastID, rows.Err()
}

// queryMessagesSince returns events for messages with timestamp >= since AND id > afterID, paginated.
func queryMessagesSince(db *sql.DB, since time.Time, afterID int64, limit int) ([]nexadapter.NexusEvent, int64, error) {
	// Format since in the same style go-sqlite3 uses for storage ("2006-01-02 15:04:05+00:00").
	sinceStr := since.UTC().Format("2006-01-02 15:04:05+00:00")

	q := warehouseQuery + "WHERE m.timestamp >= ? AND m.id > ? ORDER BY m.id LIMIT ?"
	rows, err := db.Query(q, sinceStr, afterID, limit)
	if err != nil {
		return nil, afterID, fmt.Errorf("query failed: %w", err)
	}
	defer rows.Close()

	var events []nexadapter.NexusEvent
	lastID := afterID

	for rows.Next() {
		var row warehouseRow
		if err := scanWarehouseRow(rows, &row); err != nil {
			return nil, lastID, err
		}
		events = append(events, convertWarehouseMessage(row))
		lastID = row.ID
	}

	return events, lastID, rows.Err()
}

func scanWarehouseRow(rows *sql.Rows, row *warehouseRow) error {
	return rows.Scan(
		&row.ID, &row.Content, &row.Timestamp, &row.IsFromMe, &row.GUID,
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

func convertWarehouseMessage(row warehouseRow) nexadapter.NexusEvent {
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
		senderID = "me"
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

	b := nexadapter.NewEvent("imessage", "imessage:"+row.GUID).
		WithTimestampUnixMs(timestampMs).
		WithContent(content).
		WithSender(senderID, senderName).
		WithPeer(row.ChatIdentifier, peerKind).
		WithAccount("default").
		WithMetadata("is_from_me", row.IsFromMe).
		WithMetadata("chat_id", row.ChatID).
		WithMetadata("service", serviceName)

	if row.ReplyToGUID.Valid && row.ReplyToGUID.String != "" {
		b.WithReplyTo("imessage:" + row.ReplyToGUID.String)
	}

	return b.Build()
}

// =====================================================================
// Database helpers
// =====================================================================

// openDatabases opens both chat.db (read-only) and the warehouse (read-write),
// running migrations on the warehouse if needed.
func openDatabases() (*etl.ChatDB, *sql.DB, error) {
	cfg := config.Load()

	// Auto-initialize warehouse if it doesn't exist.
	if err := os.MkdirAll(cfg.AppDir, 0755); err != nil {
		return nil, nil, fmt.Errorf("failed to create app directory: %w", err)
	}
	if err := migrate.MigrateWarehouse(cfg.EveDBPath); err != nil {
		return nil, nil, fmt.Errorf("warehouse migration failed: %w", err)
	}

	// Open chat.db.
	chatDBPath := etl.GetChatDBPath()
	if chatDBPath == "" {
		return nil, nil, fmt.Errorf("cannot determine chat.db path")
	}
	chatDB, err := etl.OpenChatDB(chatDBPath)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to open chat.db: %w", err)
	}

	// Open warehouse.
	warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath)
	if err != nil {
		chatDB.Close()
		return nil, nil, fmt.Errorf("failed to open warehouse: %w", err)
	}

	return chatDB, warehouseDB, nil
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
