package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/spf13/cobra"
	"github.com/tylerchilds/eve/internal/config"
	"github.com/tylerchilds/eve/internal/engine"
	"github.com/tylerchilds/eve/internal/etl"
	"github.com/tylerchilds/eve/internal/gemini"
	"github.com/tylerchilds/eve/internal/migrate"
	"github.com/tylerchilds/eve/internal/queue"
)

var version = "0.1.0-dev"

func main() {
	rootCmd := &cobra.Command{
		Use:   "eve",
		Short: "Eve - Single binary for iMessage analysis and embeddings",
	}

	versionCmd := &cobra.Command{
		Use:   "version",
		Short: "Print version information",
		RunE: func(cmd *cobra.Command, args []string) error {
			output := map[string]interface{}{
				"version": version,
				"go":      "1.23",
			}
			return printJSON(output)
		},
	}

	pathsCmd := &cobra.Command{
		Use:   "paths",
		Short: "Print Eve application paths",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()
			output := map[string]interface{}{
				"app_dir":       cfg.AppDir,
				"eve_db_path":   cfg.EveDBPath,
				"queue_db_path": cfg.QueueDBPath,
				"config_path":   cfg.ConfigPath,
			}
			return printJSON(output)
		},
	}

	initCmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize Eve databases",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			// Ensure app directory exists
			if err := os.MkdirAll(cfg.AppDir, 0755); err != nil {
				return printErrorJSON(fmt.Errorf("failed to create app directory: %w", err))
			}

			// Run warehouse migrations
			if err := migrate.MigrateWarehouse(cfg.EveDBPath); err != nil {
				return printErrorJSON(fmt.Errorf("warehouse migration failed: %w", err))
			}

			// Run queue migrations
			if err := migrate.MigrateQueue(cfg.QueueDBPath); err != nil {
				return printErrorJSON(fmt.Errorf("queue migration failed: %w", err))
			}

			output := map[string]interface{}{
				"ok":            true,
				"app_dir":       cfg.AppDir,
				"eve_db_path":   cfg.EveDBPath,
				"queue_db_path": cfg.QueueDBPath,
				"message":       "Databases initialized successfully",
			}
			return printJSON(output)
		},
	}

	// Sync command
	var syncDryRun bool

	syncCmd := &cobra.Command{
		Use:   "sync",
		Short: "Sync data from macOS Messages database",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			// Get chat.db path
			chatDBPath := etl.GetChatDBPath()
			if chatDBPath == "" {
				return printErrorJSON(fmt.Errorf("failed to determine chat.db path"))
			}

			// Check if chat.db exists
			if _, err := os.Stat(chatDBPath); os.IsNotExist(err) {
				return printErrorJSON(fmt.Errorf("chat.db not found at %s", chatDBPath))
			}

			// Open chat.db
			chatDB, err := etl.OpenChatDB(chatDBPath)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open chat.db: %w", err))
			}
			defer chatDB.Close()

			// Open warehouse database
			warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open warehouse database: %w", err))
			}
			defer warehouseDB.Close()

			// Get watermark for incremental sync
			var sinceRowID int64 = 0
			wm, err := etl.GetWatermark(warehouseDB, "chatdb", "message_rowid")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to get watermark: %w", err))
			}
			if wm != nil && wm.ValueInt.Valid {
				sinceRowID = wm.ValueInt.Int64
			}

			// If dry-run, only count messages
			if syncDryRun {
				// Count messages
				messageCount, err := chatDB.CountMessages(sinceRowID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to count messages: %w", err))
				}

				// Get chat and handle counts
				chatCount, err := chatDB.GetChatCount()
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to count chats: %w", err))
				}

				handleCount, err := chatDB.GetHandleCount()
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to count handles: %w", err))
				}

				// Output JSON (no message text, only counts)
				output := map[string]interface{}{
					"ok":             true,
					"dry_run":        true,
					"chat_db_path":   chatDBPath,
					"messages_found": messageCount.TotalMessages,
					"chats_found":    chatCount,
					"handles_found":  handleCount,
					"since_rowid":    sinceRowID,
					"max_rowid":      messageCount.MaxRowID,
				}

				if !messageCount.OldestDate.IsZero() {
					output["oldest_message_date"] = messageCount.OldestDate.Format(time.RFC3339)
				}
				if !messageCount.NewestDate.IsZero() {
					output["newest_message_date"] = messageCount.NewestDate.Format(time.RFC3339)
				}

				return printJSON(output)
			}

			// Run full ETL pipeline
			syncResult, err := etl.FullSync(chatDB, warehouseDB, sinceRowID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("sync failed: %w", err))
			}

			// Update watermark if we synced messages
			if syncResult.MaxMessageRowID > 0 {
				if err := etl.SetWatermark(warehouseDB, "chatdb", "message_rowid", &syncResult.MaxMessageRowID, nil); err != nil {
					return printErrorJSON(fmt.Errorf("failed to update watermark: %w", err))
				}
			}

			// Output JSON (no message text, only counts)
			output := map[string]interface{}{
				"ok":                  true,
				"dry_run":             false,
				"chat_db_path":        chatDBPath,
				"handles_synced":      syncResult.HandlesCount,
				"chats_synced":        syncResult.ChatsCount,
				"messages_synced":     syncResult.MessagesCount,
				"attachments_synced":  syncResult.AttachmentsCount,
				"conversations_built": syncResult.ConversationsCount,
				"since_rowid":         sinceRowID,
				"max_rowid":           syncResult.MaxMessageRowID,
				"watermark_updated":   syncResult.MaxMessageRowID > 0,
			}

			return printJSON(output)
		},
	}

	syncCmd.Flags().BoolVar(&syncDryRun, "dry-run", false, "Count messages without updating watermark")

	// Compute command group
	computeCmd := &cobra.Command{
		Use:   "compute",
		Short: "Compute engine operations",
	}

	computeStatusCmd := &cobra.Command{
		Use:   "status",
		Short: "Show queue backlog statistics",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			// Open queue database
			db, err := sql.Open("sqlite3", cfg.QueueDBPath)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open queue database: %w", err))
			}
			defer db.Close()

			// Get queue stats
			q := queue.New(db)
			stats, err := q.GetStats()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to get queue stats: %w", err))
			}

			return printJSON(stats)
		},
	}

	var runWorkerCount int
	var runTimeout int

	computeRunCmd := &cobra.Command{
		Use:   "run",
		Short: "Run compute engine to process queued jobs",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			// Open queue database with WAL mode and busy timeout
			queueDB, err := sql.Open("sqlite3", cfg.QueueDBPath+"?_journal_mode=WAL&_busy_timeout=5000")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open queue database: %w", err))
			}
			defer queueDB.Close()

			// Set connection pool limits for better concurrency
			queueDB.SetMaxOpenConns(25)
			queueDB.SetMaxIdleConns(10)

			// Create queue
			q := queue.New(queueDB)

			// Open warehouse database for handlers
			warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath+"?_journal_mode=WAL&_busy_timeout=5000")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open warehouse database: %w", err))
			}
			defer warehouseDB.Close()

			// Create Gemini client
			geminiClient := gemini.NewClient(cfg.GeminiAPIKey)

			// Create engine with config
			engineCfg := engine.DefaultConfig()
			if runWorkerCount > 0 {
				engineCfg.WorkerCount = runWorkerCount
			}

			eng := engine.New(q, engineCfg)

			// Register job handlers
			eng.RegisterHandler("fake", engine.FakeJobHandler)
			eng.RegisterHandler("analysis", engine.NewAnalysisJobHandler(warehouseDB, geminiClient, cfg.AnalysisModel))
			eng.RegisterHandler("embedding", engine.NewEmbeddingJobHandler(warehouseDB, geminiClient, cfg.EmbedModel))

			// Setup context with cancellation
			var ctx context.Context
			var cancel context.CancelFunc

			if runTimeout > 0 {
				ctx, cancel = context.WithTimeout(context.Background(), time.Duration(runTimeout)*time.Second)
				defer cancel()
			} else {
				// Setup signal handling for graceful shutdown
				ctx, cancel = context.WithCancel(context.Background())
				defer cancel()

				sigChan := make(chan os.Signal, 1)
				signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

				go func() {
					<-sigChan
					cancel()
				}()
			}

			// Run engine
			startTime := time.Now()
			stats, err := eng.Run(ctx)
			duration := time.Since(startTime)

			if err != nil {
				return printErrorJSON(fmt.Errorf("compute run failed: %w", err))
			}

			throughput := 0.0
			if duration.Seconds() > 0 {
				throughput = float64(stats.Succeeded) / duration.Seconds()
			}

			// Output stats
			output := map[string]interface{}{
				"ok":        true,
				"succeeded": stats.Succeeded,
				"failed":    stats.Failed,
				"skipped":   stats.Skipped,
				"duration":  duration.Seconds(),
				"throughput_jobs_per_s": throughput,
				"workers":   engineCfg.WorkerCount,
				"analysis_model": cfg.AnalysisModel,
				"embed_model": cfg.EmbedModel,
			}
			return printJSON(output)
		},
	}

	computeRunCmd.Flags().IntVar(&runWorkerCount, "workers", 0, "Number of concurrent workers (default: 10)")
	computeRunCmd.Flags().IntVar(&runTimeout, "timeout", 0, "Timeout in seconds (0 = no timeout)")

	// Compute test: run convo-all-v1 against all Casey conversations and report facet counts.
	var caseyWorkers int
	var caseyLimit int
	computeTestCaseyCmd := &cobra.Command{
		Use:   "test-casey-convo-all",
		Short: "Run convo-all-v1 analysis for all Casey Adams conversations and output aggregate facet counts",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()
			if cfg.GeminiAPIKey == "" {
				return printErrorJSON(fmt.Errorf("GEMINI_API_KEY is required"))
			}

			// Open warehouse DB (read conversation IDs + write facets)
			warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath+"?_journal_mode=WAL&_busy_timeout=5000")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open warehouse database: %w", err))
			}
			defer warehouseDB.Close()

			// Resolve Casey contact_id
			var caseyContactID int
			err = warehouseDB.QueryRow(`SELECT id FROM contacts WHERE name = 'Casey Adams' LIMIT 1`).Scan(&caseyContactID)
			if err == sql.ErrNoRows {
				err = warehouseDB.QueryRow(`SELECT id FROM contacts WHERE name LIKE '%Casey Adams%' LIMIT 1`).Scan(&caseyContactID)
			}
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to resolve Casey contact id: %w", err))
			}

			// Find the one-on-one chat with Casey by inbound volume from that contact.
			var caseyChatID int
			err = warehouseDB.QueryRow(`
				SELECT chat_id
				FROM messages
				WHERE sender_id = ?
				GROUP BY chat_id
				ORDER BY COUNT(*) DESC
				LIMIT 1
			`, caseyContactID).Scan(&caseyChatID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to resolve Casey chat id: %w", err))
			}

			// Load all conversation IDs for that chat.
			rows, err := warehouseDB.Query(`SELECT id FROM conversations WHERE chat_id = ? ORDER BY id`, caseyChatID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to read conversations for chat %d: %w", caseyChatID, err))
			}
			var conversationIDs []int
			for rows.Next() {
				var id int
				if err := rows.Scan(&id); err != nil {
					rows.Close()
					return printErrorJSON(fmt.Errorf("failed to scan conversation id: %w", err))
				}
				conversationIDs = append(conversationIDs, id)
			}
			rows.Close()
			if len(conversationIDs) == 0 {
				return printErrorJSON(fmt.Errorf("no conversations found for Casey chat_id=%d", caseyChatID))
			}
			conversationsFoundTotal := len(conversationIDs)
			if caseyLimit > 0 && len(conversationIDs) > caseyLimit {
				conversationIDs = conversationIDs[:caseyLimit]
			}

			// Reset prior analysis/facets so counts reflect this run.
			// If --limit is used, only reset the selected subset (so we don't wipe the whole chat).
			tx, err := warehouseDB.Begin()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to begin reset transaction: %w", err))
			}
			if caseyLimit > 0 {
				// Build (?,?,...) IN clause for selected conversation IDs (<= 999).
				placeholders := make([]string, 0, len(conversationIDs))
				args := make([]interface{}, 0, len(conversationIDs))
				for _, id := range conversationIDs {
					placeholders = append(placeholders, "?")
					args = append(args, id)
				}
				inClause := strings.Join(placeholders, ",")

				// Clear prior convo-all analysis rows for selected conversations.
				clearAnalysesSQL := fmt.Sprintf(
					`DELETE FROM conversation_analyses WHERE eve_prompt_id = 'convo-all-v1' AND conversation_id IN (%s)`,
					inClause,
				)
				if _, err := tx.Exec(clearAnalysesSQL, args...); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear conversation_analyses: %w", err))
				}

				// Clear facet rows for selected conversations.
				for _, table := range []string{"entities", "topics", "emotions", "humor_items"} {
					clearFacetSQL := fmt.Sprintf(`DELETE FROM %s WHERE conversation_id IN (%s)`, table, inClause)
					if _, err := tx.Exec(clearFacetSQL, args...); err != nil {
						tx.Rollback()
						return printErrorJSON(fmt.Errorf("failed to clear %s: %w", table, err))
					}
				}
			} else {
				// Full chat reset.
				if _, err := tx.Exec(`
					DELETE FROM conversation_analyses
					WHERE eve_prompt_id = 'convo-all-v1'
					  AND conversation_id IN (SELECT id FROM conversations WHERE chat_id = ?)
				`, caseyChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear conversation_analyses: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM entities WHERE chat_id = ?`, caseyChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear entities: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM topics WHERE chat_id = ?`, caseyChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear topics: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM emotions WHERE chat_id = ?`, caseyChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear emotions: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM humor_items WHERE chat_id = ?`, caseyChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear humor_items: %w", err))
				}
			}
			if err := tx.Commit(); err != nil {
				return printErrorJSON(fmt.Errorf("failed to commit reset transaction: %w", err))
			}

			// Use an ephemeral queue DB so this test is repeatable and doesn't touch the main queue.
			tmp, err := os.CreateTemp("", "eve-queue-casey-*.db")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to create temp queue db: %w", err))
			}
			tmpPath := tmp.Name()
			tmp.Close()
			defer os.Remove(tmpPath)

			if err := migrate.MigrateQueue(tmpPath); err != nil {
				return printErrorJSON(fmt.Errorf("failed to migrate temp queue db: %w", err))
			}

			queueDB, err := sql.Open("sqlite3", tmpPath+"?_journal_mode=WAL&_busy_timeout=5000")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open temp queue db: %w", err))
			}
			defer queueDB.Close()
			queueDB.SetMaxOpenConns(25)
			queueDB.SetMaxIdleConns(10)

			q := queue.New(queueDB)

			// Enqueue analysis jobs (convo-all-v1) for every conversation in the Casey chat.
			enqueued := 0
			for _, convID := range conversationIDs {
				err := q.Enqueue(queue.EnqueueOptions{
					Type: "analysis",
					Key:  fmt.Sprintf("analysis:conversation:%d:convo-all-v1", convID),
					Payload: engine.AnalysisJobPayload{
						ConversationID: convID,
						EvePromptID:    "convo-all-v1",
					},
					MaxAttempts: 3,
				})
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to enqueue conversation %d: %w", convID, err))
				}
				enqueued++
			}

			// Run compute engine (analysis only)
			geminiClient := gemini.NewClient(cfg.GeminiAPIKey)
			engineCfg := engine.DefaultConfig()
			if caseyWorkers > 0 {
				engineCfg.WorkerCount = caseyWorkers
			}
			engineCfg.LeaseOwner = fmt.Sprintf("casey-test-%d", time.Now().UnixNano())

			metrics := engine.NewAnalysisMetrics()

			eng := engine.New(q, engineCfg)
			eng.RegisterHandler("analysis", engine.NewAnalysisJobHandlerWithMetrics(warehouseDB, geminiClient, cfg.AnalysisModel, metrics))

			startTime := time.Now()
			stats, err := eng.Run(context.Background())
			duration := time.Since(startTime)
			if err != nil {
				return printErrorJSON(fmt.Errorf("compute run failed: %w", err))
			}

			throughput := 0.0
			if duration.Seconds() > 0 {
				throughput = float64(stats.Succeeded) / duration.Seconds()
			}

			// Collect dead jobs (final failures) with last_error for debugging (no message text).
			type deadJob struct {
				ConversationID int    `json:"conversation_id"`
				Attempts       int    `json:"attempts"`
				LastError      string `json:"last_error"`
			}
			var deadJobs []deadJob
			deadRows, err := queueDB.Query(`SELECT payload_json, attempts, COALESCE(last_error, '') FROM jobs WHERE type='analysis' AND state='dead'`)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to query dead jobs: %w", err))
			}
			for deadRows.Next() {
				var payloadJSON string
				var attempts int
				var lastErr string
				if err := deadRows.Scan(&payloadJSON, &attempts, &lastErr); err != nil {
					deadRows.Close()
					return printErrorJSON(fmt.Errorf("failed to scan dead job: %w", err))
				}
				var p engine.AnalysisJobPayload
				if err := json.Unmarshal([]byte(payloadJSON), &p); err != nil {
					// If the payload is malformed, still surface the error.
					deadJobs = append(deadJobs, deadJob{
						ConversationID: 0,
						Attempts:       attempts,
						LastError:      "invalid payload_json: " + err.Error(),
					})
					continue
				}
				deadJobs = append(deadJobs, deadJob{
					ConversationID: p.ConversationID,
					Attempts:       attempts,
					LastError:      lastErr,
				})
			}
			deadRows.Close()

			// Error histogram (exact last_error strings -> count).
			errCounts := map[string]int{}
			for _, dj := range deadJobs {
				key := dj.LastError
				if key == "" {
					key = "(empty last_error)"
				}
				errCounts[key]++
			}

			// Aggregate facet counts (no message text).
			var topicsTotal, entitiesTotal, emotionsTotal, humorTotal int
			if caseyLimit > 0 {
				placeholders := make([]string, 0, len(conversationIDs))
				args := make([]interface{}, 0, len(conversationIDs))
				for _, id := range conversationIDs {
					placeholders = append(placeholders, "?")
					args = append(args, id)
				}
				inClause := strings.Join(placeholders, ",")
				qCount := func(table string) (int, error) {
					var c int
					sql := fmt.Sprintf(`SELECT COUNT(*) FROM %s WHERE conversation_id IN (%s)`, table, inClause)
					if err := warehouseDB.QueryRow(sql, args...).Scan(&c); err != nil {
						return 0, err
					}
					return c, nil
				}
				if v, err := qCount("topics"); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count topics: %w", err))
				} else {
					topicsTotal = v
				}
				if v, err := qCount("entities"); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count entities: %w", err))
				} else {
					entitiesTotal = v
				}
				if v, err := qCount("emotions"); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count emotions: %w", err))
				} else {
					emotionsTotal = v
				}
				if v, err := qCount("humor_items"); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count humor_items: %w", err))
				} else {
					humorTotal = v
				}
			} else {
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM topics WHERE chat_id = ?`, caseyChatID).Scan(&topicsTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count topics: %w", err))
				}
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM entities WHERE chat_id = ?`, caseyChatID).Scan(&entitiesTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count entities: %w", err))
				}
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM emotions WHERE chat_id = ?`, caseyChatID).Scan(&emotionsTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count emotions: %w", err))
				}
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM humor_items WHERE chat_id = ?`, caseyChatID).Scan(&humorTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count humor_items: %w", err))
				}
			}

			// Count statuses from the warehouse.
			statusCounts := map[string]int{}
			var srows *sql.Rows
			if caseyLimit > 0 {
				placeholders := make([]string, 0, len(conversationIDs))
				args := make([]interface{}, 0, len(conversationIDs))
				for _, id := range conversationIDs {
					placeholders = append(placeholders, "?")
					args = append(args, id)
				}
				inClause := strings.Join(placeholders, ",")
				sql := fmt.Sprintf(`
					SELECT status, COUNT(*) AS c
					FROM conversation_analyses
					WHERE eve_prompt_id = 'convo-all-v1'
					  AND conversation_id IN (%s)
					GROUP BY status
				`, inClause)
				srows, err = warehouseDB.Query(sql, args...)
			} else {
				srows, err = warehouseDB.Query(`
					SELECT status, COUNT(*) AS c
					FROM conversation_analyses
					WHERE eve_prompt_id = 'convo-all-v1'
					  AND conversation_id IN (SELECT id FROM conversations WHERE chat_id = ?)
					GROUP BY status
				`, caseyChatID)
			}
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to query analysis status counts: %w", err))
			}
			for srows.Next() {
				var status string
				var c int
				if err := srows.Scan(&status, &c); err != nil {
					srows.Close()
					return printErrorJSON(fmt.Errorf("failed to scan analysis status: %w", err))
				}
				statusCounts[status] = c
			}
			srows.Close()

			// List blocked conversations (IDs only).
			type blockedConvo struct {
				ConversationID int    `json:"conversation_id"`
				Reason         string `json:"reason"`
			}
			var blocked []blockedConvo
			var brows *sql.Rows
			if caseyLimit > 0 {
				placeholders := make([]string, 0, len(conversationIDs))
				args := make([]interface{}, 0, len(conversationIDs))
				for _, id := range conversationIDs {
					placeholders = append(placeholders, "?")
					args = append(args, id)
				}
				inClause := strings.Join(placeholders, ",")
				sql := fmt.Sprintf(`
					SELECT conversation_id, COALESCE(blocked_reason, '')
					FROM conversation_analyses
					WHERE eve_prompt_id = 'convo-all-v1'
					  AND status = 'blocked'
					  AND conversation_id IN (%s)
					ORDER BY conversation_id
				`, inClause)
				brows, err = warehouseDB.Query(sql, args...)
			} else {
				brows, err = warehouseDB.Query(`
					SELECT conversation_id, COALESCE(blocked_reason, '')
					FROM conversation_analyses
					WHERE eve_prompt_id = 'convo-all-v1'
					  AND status = 'blocked'
					  AND conversation_id IN (SELECT id FROM conversations WHERE chat_id = ?)
					ORDER BY conversation_id
				`, caseyChatID)
			}
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to query blocked conversations: %w", err))
			}
			for brows.Next() {
				var cid int
				var reason string
				if err := brows.Scan(&cid, &reason); err != nil {
					brows.Close()
					return printErrorJSON(fmt.Errorf("failed to scan blocked conversation: %w", err))
				}
				blocked = append(blocked, blockedConvo{ConversationID: cid, Reason: reason})
			}
			brows.Close()

			output := map[string]interface{}{
				"ok": true,
				"casey_contact_id": caseyContactID,
				"casey_chat_id": caseyChatID,
				"conversations_total": conversationsFoundTotal,
				"conversations_selected": len(conversationIDs),
				"analysis_prompt_id": "convo-all-v1",
				"analysis_model": cfg.AnalysisModel,
				"run": map[string]interface{}{
					"workers": engineCfg.WorkerCount,
					"duration": duration.Seconds(),
					"succeeded": stats.Succeeded,
					"failed": stats.Failed,
					"skipped": stats.Skipped,
					"throughput_convos_per_s": throughput,
					"enqueued": enqueued,
				},
				"analysis_status_counts": statusCounts,
				"blocked_conversations": map[string]interface{}{
					"count": len(blocked),
					"sample": func() []blockedConvo {
						if len(blocked) <= 20 {
							return blocked
						}
						return blocked[:20]
					}(),
				},
				"metrics": json.RawMessage(metrics.SnapshotJSON()),
				"errors": map[string]interface{}{
					"dead_jobs": len(deadJobs),
					"dead_jobs_sample": func() []deadJob {
						if len(deadJobs) <= 20 {
							return deadJobs
						}
						return deadJobs[:20]
					}(),
					"dead_last_error_counts": errCounts,
				},
				"facets": map[string]interface{}{
					"topics": topicsTotal,
					"entities": entitiesTotal,
					"emotions": emotionsTotal,
					"humor_items": humorTotal,
				},
			}
			return printJSON(output)
		},
	}
	computeTestCaseyCmd.Flags().IntVar(&caseyWorkers, "workers", 800, "Number of concurrent workers")
	computeTestCaseyCmd.Flags().IntVar(&caseyLimit, "limit", 0, "Limit number of conversations analyzed (0 = all)")

	// Compute test: embed Casey conversations + facet rows and report throughput.
	var caseyEmbedWorkers int
	computeTestCaseyEmbeddingsCmd := &cobra.Command{
		Use:   "test-casey-embeddings",
		Short: "Embed all Casey conversations + facet rows (topics/entities/emotions/humor) and output aggregate counts + throughput",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()
			if cfg.GeminiAPIKey == "" {
				return printErrorJSON(fmt.Errorf("GEMINI_API_KEY is required"))
			}

			warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath+"?_journal_mode=WAL&_busy_timeout=5000")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open warehouse database: %w", err))
			}
			defer warehouseDB.Close()

			// Resolve Casey contact_id
			var caseyContactID int
			err = warehouseDB.QueryRow(`SELECT id FROM contacts WHERE name = 'Casey Adams' LIMIT 1`).Scan(&caseyContactID)
			if err == sql.ErrNoRows {
				err = warehouseDB.QueryRow(`SELECT id FROM contacts WHERE name LIKE '%Casey Adams%' LIMIT 1`).Scan(&caseyContactID)
			}
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to resolve Casey contact id: %w", err))
			}

			// Find Casey chat_id by inbound volume.
			var caseyChatID int
			err = warehouseDB.QueryRow(`
				SELECT chat_id
				FROM messages
				WHERE sender_id = ?
				GROUP BY chat_id
				ORDER BY COUNT(*) DESC
				LIMIT 1
			`, caseyContactID).Scan(&caseyChatID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to resolve Casey chat id: %w", err))
			}

			// Load conversation IDs
			rows, err := warehouseDB.Query(`SELECT id FROM conversations WHERE chat_id = ? ORDER BY id`, caseyChatID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to read conversations for chat %d: %w", caseyChatID, err))
			}
			var conversationIDs []int
			for rows.Next() {
				var id int
				if err := rows.Scan(&id); err != nil {
					rows.Close()
					return printErrorJSON(fmt.Errorf("failed to scan conversation id: %w", err))
				}
				conversationIDs = append(conversationIDs, id)
			}
			rows.Close()
			if len(conversationIDs) == 0 {
				return printErrorJSON(fmt.Errorf("no conversations found for Casey chat_id=%d", caseyChatID))
			}

			// Load facet IDs (must exist from prior convo-all run).
			type facetIDs struct {
				Entities []int
				Topics   []int
				Emotions []int
				Humor    []int
			}
			facet := facetIDs{}

			loadIDs := func(sqlStr string) ([]int, error) {
				r, err := warehouseDB.Query(sqlStr, caseyChatID)
				if err != nil {
					return nil, err
				}
				defer r.Close()
				var ids []int
				for r.Next() {
					var id int
					if err := r.Scan(&id); err != nil {
						return nil, err
					}
					ids = append(ids, id)
				}
				return ids, r.Err()
			}

			if ids, err := loadIDs(`SELECT id FROM entities WHERE chat_id = ? ORDER BY id`); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load entity ids: %w", err))
			} else {
				facet.Entities = ids
			}
			if ids, err := loadIDs(`SELECT id FROM topics WHERE chat_id = ? ORDER BY id`); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load topic ids: %w", err))
			} else {
				facet.Topics = ids
			}
			if ids, err := loadIDs(`SELECT id FROM emotions WHERE chat_id = ? ORDER BY id`); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load emotion ids: %w", err))
			} else {
				facet.Emotions = ids
			}
			if ids, err := loadIDs(`SELECT id FROM humor_items WHERE chat_id = ? ORDER BY id`); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load humor_item ids: %w", err))
			} else {
				facet.Humor = ids
			}

			// Temp queue DB for repeatable embedding benchmark.
			tmp, err := os.CreateTemp("", "eve-queue-casey-embeddings-*.db")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to create temp queue db: %w", err))
			}
			tmpPath := tmp.Name()
			tmp.Close()
			defer os.Remove(tmpPath)

			if err := migrate.MigrateQueue(tmpPath); err != nil {
				return printErrorJSON(fmt.Errorf("failed to migrate temp queue db: %w", err))
			}

			queueDB, err := sql.Open("sqlite3", tmpPath+"?_journal_mode=WAL&_busy_timeout=5000")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open temp queue db: %w", err))
			}
			defer queueDB.Close()
			queueDB.SetMaxOpenConns(25)
			queueDB.SetMaxIdleConns(10)

			q := queue.New(queueDB)

			// Enqueue conversation embeddings
			enqueuedConversation := 0
			for _, convID := range conversationIDs {
				if err := q.Enqueue(queue.EnqueueOptions{
					Type: "embedding",
					Key:  fmt.Sprintf("embedding:conversation:%d:%s", convID, cfg.EmbedModel),
					Payload: engine.EmbeddingJobPayload{
						EntityType: "conversation",
						EntityID:   convID,
					},
					MaxAttempts: 3,
				}); err != nil {
					return printErrorJSON(fmt.Errorf("failed to enqueue conversation embedding %d: %w", convID, err))
				}
				enqueuedConversation++
			}

			// Enqueue facet embeddings (these are the “analysis tags” you asked for)
			enqueuedFacets := map[string]int{"entity": 0, "topic": 0, "emotion": 0, "humor_item": 0}
			for _, id := range facet.Entities {
				if err := q.Enqueue(queue.EnqueueOptions{
					Type: "embedding",
					Key:  fmt.Sprintf("embedding:entity:%d:%s", id, cfg.EmbedModel),
					Payload: engine.EmbeddingJobPayload{EntityType: "entity", EntityID: id},
					MaxAttempts: 3,
				}); err != nil {
					return printErrorJSON(fmt.Errorf("failed to enqueue entity embedding %d: %w", id, err))
				}
				enqueuedFacets["entity"]++
			}
			for _, id := range facet.Topics {
				if err := q.Enqueue(queue.EnqueueOptions{
					Type: "embedding",
					Key:  fmt.Sprintf("embedding:topic:%d:%s", id, cfg.EmbedModel),
					Payload: engine.EmbeddingJobPayload{EntityType: "topic", EntityID: id},
					MaxAttempts: 3,
				}); err != nil {
					return printErrorJSON(fmt.Errorf("failed to enqueue topic embedding %d: %w", id, err))
				}
				enqueuedFacets["topic"]++
			}
			for _, id := range facet.Emotions {
				if err := q.Enqueue(queue.EnqueueOptions{
					Type: "embedding",
					Key:  fmt.Sprintf("embedding:emotion:%d:%s", id, cfg.EmbedModel),
					Payload: engine.EmbeddingJobPayload{EntityType: "emotion", EntityID: id},
					MaxAttempts: 3,
				}); err != nil {
					return printErrorJSON(fmt.Errorf("failed to enqueue emotion embedding %d: %w", id, err))
				}
				enqueuedFacets["emotion"]++
			}
			for _, id := range facet.Humor {
				if err := q.Enqueue(queue.EnqueueOptions{
					Type: "embedding",
					Key:  fmt.Sprintf("embedding:humor_item:%d:%s", id, cfg.EmbedModel),
					Payload: engine.EmbeddingJobPayload{EntityType: "humor_item", EntityID: id},
					MaxAttempts: 3,
				}); err != nil {
					return printErrorJSON(fmt.Errorf("failed to enqueue humor_item embedding %d: %w", id, err))
				}
				enqueuedFacets["humor_item"]++
			}

			geminiClient := gemini.NewClient(cfg.GeminiAPIKey)
			engineCfg := engine.DefaultConfig()
			if caseyEmbedWorkers > 0 {
				engineCfg.WorkerCount = caseyEmbedWorkers
			}
			engineCfg.LeaseOwner = fmt.Sprintf("casey-embeddings-%d", time.Now().UnixNano())

			eng := engine.New(q, engineCfg)
			eng.RegisterHandler("embedding", engine.NewEmbeddingJobHandler(warehouseDB, geminiClient, cfg.EmbedModel))

			startTime := time.Now()
			stats, err := eng.Run(context.Background())
			duration := time.Since(startTime)
			if err != nil {
				return printErrorJSON(fmt.Errorf("compute run failed: %w", err))
			}

			throughput := 0.0
			if duration.Seconds() > 0 {
				throughput = float64(stats.Succeeded) / duration.Seconds()
			}

			// Counts of embeddings present after run (no vectors printed).
			var convEmbCount int
			if err := warehouseDB.QueryRow(`
				SELECT COUNT(*) FROM embeddings
				WHERE entity_type = 'conversation'
				  AND entity_id IN (SELECT id FROM conversations WHERE chat_id = ?)
				  AND model = ?
			`, caseyChatID, cfg.EmbedModel).Scan(&convEmbCount); err != nil {
				return printErrorJSON(fmt.Errorf("failed to count conversation embeddings: %w", err))
			}

			var entityEmb, topicEmb, emotionEmb, humorEmb int
			if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM embeddings WHERE entity_type='entity' AND model=?`, cfg.EmbedModel).Scan(&entityEmb); err != nil {
				return printErrorJSON(fmt.Errorf("failed to count entity embeddings: %w", err))
			}
			if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM embeddings WHERE entity_type='topic' AND model=?`, cfg.EmbedModel).Scan(&topicEmb); err != nil {
				return printErrorJSON(fmt.Errorf("failed to count topic embeddings: %w", err))
			}
			if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM embeddings WHERE entity_type='emotion' AND model=?`, cfg.EmbedModel).Scan(&emotionEmb); err != nil {
				return printErrorJSON(fmt.Errorf("failed to count emotion embeddings: %w", err))
			}
			if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM embeddings WHERE entity_type='humor_item' AND model=?`, cfg.EmbedModel).Scan(&humorEmb); err != nil {
				return printErrorJSON(fmt.Errorf("failed to count humor_item embeddings: %w", err))
			}

			output := map[string]interface{}{
				"ok": true,
				"casey_contact_id": caseyContactID,
				"casey_chat_id": caseyChatID,
				"embed_model": cfg.EmbedModel,
				"conversations_total": len(conversationIDs),
				"facet_rows_total": map[string]int{
					"entities": len(facet.Entities),
					"topics": len(facet.Topics),
					"emotions": len(facet.Emotions),
					"humor_items": len(facet.Humor),
				},
				"run": map[string]interface{}{
					"workers": engineCfg.WorkerCount,
					"duration": duration.Seconds(),
					"succeeded": stats.Succeeded,
					"failed": stats.Failed,
					"skipped": stats.Skipped,
					"throughput_embeddings_per_s": throughput,
					"enqueued": map[string]int{
						"conversation": enqueuedConversation,
						"entity": enqueuedFacets["entity"],
						"topic": enqueuedFacets["topic"],
						"emotion": enqueuedFacets["emotion"],
						"humor_item": enqueuedFacets["humor_item"],
					},
				},
				"embeddings_present": map[string]int{
					"conversation": convEmbCount,
					"entity": entityEmb,
					"topic": topicEmb,
					"emotion": emotionEmb,
					"humor_item": humorEmb,
				},
			}

			return printJSON(output)
		},
	}
	computeTestCaseyEmbeddingsCmd.Flags().IntVar(&caseyEmbedWorkers, "workers", 800, "Number of concurrent workers")

	computeCmd.AddCommand(computeStatusCmd)
	computeCmd.AddCommand(computeRunCmd)
	computeCmd.AddCommand(computeTestCaseyCmd)
	computeCmd.AddCommand(computeTestCaseyEmbeddingsCmd)

	rootCmd.AddCommand(versionCmd)
	rootCmd.AddCommand(pathsCmd)
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(syncCmd)
	rootCmd.AddCommand(computeCmd)

	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

func printJSON(data interface{}) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(data); err != nil {
		return fmt.Errorf("failed to encode JSON: %w", err)
	}
	return nil
}

func printErrorJSON(err error) error {
	output := map[string]interface{}{
		"ok":    false,
		"error": err.Error(),
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if encErr := encoder.Encode(output); encErr != nil {
		return fmt.Errorf("failed to encode error JSON: %w", encErr)
	}
	return err
}
