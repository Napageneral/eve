package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/spf13/cobra"
	"github.com/brandtty/eve/internal/config"
	"github.com/brandtty/eve/internal/db"
	"github.com/brandtty/eve/internal/encoding"
	"github.com/brandtty/eve/internal/engine"
	"github.com/brandtty/eve/internal/etl"
	"github.com/brandtty/eve/internal/gemini"
	"github.com/brandtty/eve/internal/migrate"
	"github.com/brandtty/eve/internal/queue"
	"github.com/brandtty/eve/internal/resources"
)

var version = "0.1.0-dev"

func recommendedSQLitePool(workerCount int) (maxOpen int, maxIdle int) {
	// SQLite performs poorly with extremely high connection counts (each conn has its own page cache).
	// We want enough parallelism for reads, but cap to avoid cache thrash and lock contention.
	if workerCount <= 0 {
		workerCount = 10
	}
	switch {
	case workerCount < 64:
		maxOpen = 64
	case workerCount > 256:
		maxOpen = 256
	default:
		maxOpen = workerCount
	}
	maxIdle = maxOpen / 2
	if maxIdle < 16 {
		maxIdle = 16
	}
	return maxOpen, maxIdle
}

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
			// RPM limits:
			// - If set explicitly (non-zero), use fixed RPM.
			// - If 0, auto-probe RPM using 429/timeout/net signals.
			var analysisRPMCtrl *engine.AutoRPMController
			var embedRPMCtrl *engine.AutoRPMController
			if cfg.AnalysisRPM > 0 {
				geminiClient.SetAnalysisRPM(cfg.AnalysisRPM)
			} else {
				analysisRPMCtrl = engine.NewAutoRPMController(engine.DefaultAutoRPMConfig(), geminiClient.SetAnalysisRPM)
			}
			if cfg.EmbedRPM > 0 {
				geminiClient.SetEmbedRPM(cfg.EmbedRPM)
			} else {
				embedRPMCtrl = engine.NewAutoRPMController(engine.DefaultAutoRPMConfig(), geminiClient.SetEmbedRPM)
			}

			// Create engine with config
			engineCfg := engine.DefaultConfig()
			if runWorkerCount > 0 {
				engineCfg.WorkerCount = runWorkerCount
			}

			// Cap warehouse DB pool to avoid lock/cache thrash at high worker counts.
			maxOpen, maxIdle := recommendedSQLitePool(engineCfg.WorkerCount)
			warehouseDB.SetMaxOpenConns(maxOpen)
			warehouseDB.SetMaxIdleConns(maxIdle)

			eng := engine.New(q, engineCfg)

			// Register job handlers
			eng.RegisterHandler("fake", engine.FakeJobHandler)

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

			// Start RPM auto-controllers now that we have a cancellation context.
			if analysisRPMCtrl != nil {
				analysisRPMCtrl.Start(ctx)
			}
			if embedRPMCtrl != nil {
				embedRPMCtrl.Start(ctx)
			}

			// Serialize DB writes into micro-batched txs to reduce SQLite write contention.
			dbWriter := engine.NewTxBatchWriter(warehouseDB, engine.TxBatchWriterConfig{
				BatchSize:     25,
				FlushInterval: 100 * time.Millisecond,
			})
			dbWriter.Start()
			defer dbWriter.Close()

			// Adaptive in-flight controller: prevents melting bad Wi-Fi/routers by dialing down on 429/timeouts/net resets.
			sem := engine.NewAdaptiveSemaphore(engineCfg.WorkerCount)
			ctrl := engine.NewAdaptiveController(sem, engine.DefaultAdaptiveControllerConfig(engineCfg.WorkerCount))
			ctrl.Start(ctx)

			wrap := func(base engine.JobHandler) engine.JobHandler {
				return func(ctx context.Context, job *queue.Job) error {
					if err := sem.Acquire(ctx); err != nil {
						return err
					}
					start := time.Now()
					err := base(ctx, job)
					ctrl.Observe(time.Since(start), err)
					// Feed RPM auto-controllers by job type (each job corresponds to one API call).
					if job != nil {
						switch job.Type {
						case "analysis":
							if analysisRPMCtrl != nil {
								analysisRPMCtrl.Observe(err)
							}
						case "embedding":
							if embedRPMCtrl != nil {
								embedRPMCtrl.Observe(err)
							}
						}
					}
					sem.Release()
					return err
				}
			}

			eng.RegisterHandler("analysis", wrap(engine.NewAnalysisJobHandlerWithPipeline(warehouseDB, geminiClient, cfg.AnalysisModel, nil, dbWriter)))
			eng.RegisterHandler("embedding", wrap(engine.NewEmbeddingJobHandlerWithPipeline(warehouseDB, geminiClient, cfg.EmbedModel, dbWriter)))

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
				"ok":                    true,
				"succeeded":             stats.Succeeded,
				"failed":                stats.Failed,
				"skipped":               stats.Skipped,
				"duration":              duration.Seconds(),
				"throughput_jobs_per_s": throughput,
				"workers":               engineCfg.WorkerCount,
				"analysis_model":        cfg.AnalysisModel,
				"embed_model":           cfg.EmbedModel,
				"analysis_rpm_config":   cfg.AnalysisRPM,
				"embed_rpm_config":      cfg.EmbedRPM,
				"analysis_rpm_effective": func() int {
					if analysisRPMCtrl != nil {
						return analysisRPMCtrl.CurrentRPM()
					}
					return cfg.AnalysisRPM
				}(),
				"embed_rpm_effective": func() int {
					if embedRPMCtrl != nil {
						return embedRPMCtrl.CurrentRPM()
					}
					return cfg.EmbedRPM
				}(),
				"adaptive_controller": json.RawMessage(ctrl.SnapshotJSON()),
				"analysis_rpm_controller": func() json.RawMessage {
					if analysisRPMCtrl == nil {
						return json.RawMessage("null")
					}
					return analysisRPMCtrl.SnapshotJSON()
				}(),
				"embed_rpm_controller": func() json.RawMessage {
					if embedRPMCtrl == nil {
						return json.RawMessage("null")
					}
					return embedRPMCtrl.SnapshotJSON()
				}(),
			}
			return printJSON(output)
		},
	}

	computeRunCmd.Flags().IntVar(&runWorkerCount, "workers", 0, "Number of concurrent workers (default: 10)")
	computeRunCmd.Flags().IntVar(&runTimeout, "timeout", 0, "Timeout in seconds (0 = no timeout)")

	// Compute test: run convo-all-v1 analysis against a chat and report facet counts.
	var testAnalysisWorkers int
	var testAnalysisLimit int
	var testAnalysisChatID int
	computeTestAnalysisCmd := &cobra.Command{
		Use:   "test-analysis",
		Short: "Run convo-all-v1 analysis for a chat (default: chat with most messages) and output aggregate facet counts",
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

			// Find chat ID - use provided or find chat with most messages
			targetChatID := testAnalysisChatID
			var chatName string
			if targetChatID == 0 {
				// Find the chat with the most messages
				err = warehouseDB.QueryRow(`
					SELECT c.id, COALESCE(c.chat_name, 'Unknown')
					FROM chats c
					ORDER BY c.total_messages DESC
					LIMIT 1
				`).Scan(&targetChatID, &chatName)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to find chat with most messages: %w", err))
				}
			} else {
				// Get chat name for the provided ID
				err = warehouseDB.QueryRow(`SELECT COALESCE(chat_name, 'Unknown') FROM chats WHERE id = ?`, targetChatID).Scan(&chatName)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to find chat %d: %w", targetChatID, err))
				}
			}

			// Load all conversation IDs for that chat.
			rows, err := warehouseDB.Query(`SELECT id FROM conversations WHERE chat_id = ? ORDER BY id`, targetChatID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to read conversations for chat %d: %w", targetChatID, err))
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
				return printErrorJSON(fmt.Errorf("no conversations found for chat_id=%d", targetChatID))
			}
			conversationsFoundTotal := len(conversationIDs)
			if testAnalysisLimit > 0 && len(conversationIDs) > testAnalysisLimit {
				conversationIDs = conversationIDs[:testAnalysisLimit]
			}

			// Reset prior analysis/facets so counts reflect this run.
			// If --limit is used, only reset the selected subset (so we don't wipe the whole chat).
			tx, err := warehouseDB.Begin()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to begin reset transaction: %w", err))
			}
			if testAnalysisLimit > 0 {
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
				`, targetChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear conversation_analyses: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM entities WHERE chat_id = ?`, targetChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear entities: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM topics WHERE chat_id = ?`, targetChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear topics: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM emotions WHERE chat_id = ?`, targetChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear emotions: %w", err))
				}
				if _, err := tx.Exec(`DELETE FROM humor_items WHERE chat_id = ?`, targetChatID); err != nil {
					tx.Rollback()
					return printErrorJSON(fmt.Errorf("failed to clear humor_items: %w", err))
				}
			}
			if err := tx.Commit(); err != nil {
				return printErrorJSON(fmt.Errorf("failed to commit reset transaction: %w", err))
			}

			// Use an ephemeral queue DB so this test is repeatable and doesn't touch the main queue.
			tmp, err := os.CreateTemp("", "eve-queue-test-*.db")
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

			// Enqueue analysis jobs (convo-all-v1) for every conversation in the target chat.
			enqueued := 0
			for _, convID := range conversationIDs {
				err := q.Enqueue(queue.EnqueueOptions{
					Type:     "analysis",
					Key:      fmt.Sprintf("analysis:conversation:%d:convo-all-v1", convID),
					Priority: 20,
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
			var analysisRPMCtrl *engine.AutoRPMController
			if cfg.AnalysisRPM > 0 {
				geminiClient.SetAnalysisRPM(cfg.AnalysisRPM)
			} else {
				analysisRPMCtrl = engine.NewAutoRPMController(engine.DefaultAutoRPMConfig(), geminiClient.SetAnalysisRPM)
				analysisRPMCtrl.Start(context.Background())
			}
			engineCfg := engine.DefaultConfig()
			if testAnalysisWorkers > 0 {
				engineCfg.WorkerCount = testAnalysisWorkers
			}
			engineCfg.LeaseOwner = fmt.Sprintf("test-analysis-%d", time.Now().UnixNano())

			// Cap warehouse DB pool to avoid lock/cache thrash at high worker counts.
			maxOpen, maxIdle := recommendedSQLitePool(engineCfg.WorkerCount)
			warehouseDB.SetMaxOpenConns(maxOpen)
			warehouseDB.SetMaxIdleConns(maxIdle)

			metrics := engine.NewAnalysisMetrics()

			eng := engine.New(q, engineCfg)
			dbWriter := engine.NewTxBatchWriter(warehouseDB, engine.TxBatchWriterConfig{
				BatchSize:     25,
				FlushInterval: 100 * time.Millisecond,
			})
			dbWriter.Start()
			defer dbWriter.Close()
			baseHandler := engine.NewAnalysisJobHandlerWithPipeline(warehouseDB, geminiClient, cfg.AnalysisModel, metrics, dbWriter)
			if analysisRPMCtrl != nil {
				// Feed the auto-RPM controller so it can probe the true sustainable rate.
				base := baseHandler
				baseHandler = func(ctx context.Context, job *queue.Job) error {
					err := base(ctx, job)
					analysisRPMCtrl.Observe(err)
					return err
				}
			}
			eng.RegisterHandler("analysis", baseHandler)

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
			if testAnalysisLimit > 0 {
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
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM topics WHERE chat_id = ?`, targetChatID).Scan(&topicsTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count topics: %w", err))
				}
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM entities WHERE chat_id = ?`, targetChatID).Scan(&entitiesTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count entities: %w", err))
				}
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM emotions WHERE chat_id = ?`, targetChatID).Scan(&emotionsTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count emotions: %w", err))
				}
				if err := warehouseDB.QueryRow(`SELECT COUNT(*) FROM humor_items WHERE chat_id = ?`, targetChatID).Scan(&humorTotal); err != nil {
					return printErrorJSON(fmt.Errorf("failed to count humor_items: %w", err))
				}
			}

			// Count statuses from the warehouse.
			statusCounts := map[string]int{}
			var srows *sql.Rows
			if testAnalysisLimit > 0 {
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
				`, targetChatID)
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
			if testAnalysisLimit > 0 {
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
				`, targetChatID)
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
				"ok":                     true,
				"chat_id":                targetChatID,
				"chat_name":              chatName,
				"conversations_total":    conversationsFoundTotal,
				"conversations_selected": len(conversationIDs),
				"analysis_prompt_id":     "convo-all-v1",
				"analysis_model":         cfg.AnalysisModel,
				"analysis_rpm_config":    cfg.AnalysisRPM,
				"analysis_rpm_effective": func() int {
					if analysisRPMCtrl != nil {
						return analysisRPMCtrl.CurrentRPM()
					}
					return cfg.AnalysisRPM
				}(),
				"run": map[string]interface{}{
					"workers":                 engineCfg.WorkerCount,
					"duration":                duration.Seconds(),
					"succeeded":               stats.Succeeded,
					"failed":                  stats.Failed,
					"skipped":                 stats.Skipped,
					"throughput_convos_per_s": throughput,
					"enqueued":                enqueued,
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
					"topics":      topicsTotal,
					"entities":    entitiesTotal,
					"emotions":    emotionsTotal,
					"humor_items": humorTotal,
				},
			}
			return printJSON(output)
		},
	}
	computeTestAnalysisCmd.Flags().IntVar(&testAnalysisWorkers, "workers", 800, "Number of concurrent workers")
	computeTestAnalysisCmd.Flags().IntVar(&testAnalysisLimit, "limit", 0, "Limit number of conversations analyzed (0 = all)")
	computeTestAnalysisCmd.Flags().IntVar(&testAnalysisChatID, "chat-id", 0, "Chat ID to analyze (0 = chat with most messages)")

	// Compute test: embed conversations + facet rows and report throughput.
	var testEmbedWorkers int
	var testEmbedLimitConvos int
	var testEmbedLimitFacets int
	var testEmbedChatID int
	computeTestEmbeddingsCmd := &cobra.Command{
		Use:   "test-embeddings",
		Short: "Embed conversations + facet rows (default: chat with most messages) and output aggregate counts + throughput",
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

			// Find chat ID - use provided or find chat with most messages
			targetChatID := testEmbedChatID
			var chatName string
			if targetChatID == 0 {
				// Find the chat with the most messages
				err = warehouseDB.QueryRow(`
					SELECT c.id, COALESCE(c.chat_name, 'Unknown')
					FROM chats c
					ORDER BY c.total_messages DESC
					LIMIT 1
				`).Scan(&targetChatID, &chatName)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to find chat with most messages: %w", err))
				}
			} else {
				// Get chat name for the provided ID
				err = warehouseDB.QueryRow(`SELECT COALESCE(chat_name, 'Unknown') FROM chats WHERE id = ?`, targetChatID).Scan(&chatName)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to find chat %d: %w", targetChatID, err))
				}
			}

			// Load conversation IDs
			rows, err := warehouseDB.Query(`SELECT id FROM conversations WHERE chat_id = ? ORDER BY id`, targetChatID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to read conversations for chat %d: %w", targetChatID, err))
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
				return printErrorJSON(fmt.Errorf("no conversations found for chat_id=%d", targetChatID))
			}
			if testEmbedLimitConvos > 0 && len(conversationIDs) > testEmbedLimitConvos {
				conversationIDs = conversationIDs[:testEmbedLimitConvos]
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
				r, err := warehouseDB.Query(sqlStr, targetChatID)
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
				if testEmbedLimitFacets > 0 && len(ids) > testEmbedLimitFacets {
					ids = ids[:testEmbedLimitFacets]
				}
				facet.Entities = ids
			}
			if ids, err := loadIDs(`SELECT id FROM topics WHERE chat_id = ? ORDER BY id`); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load topic ids: %w", err))
			} else {
				if testEmbedLimitFacets > 0 && len(ids) > testEmbedLimitFacets {
					ids = ids[:testEmbedLimitFacets]
				}
				facet.Topics = ids
			}
			if ids, err := loadIDs(`SELECT id FROM emotions WHERE chat_id = ? ORDER BY id`); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load emotion ids: %w", err))
			} else {
				if testEmbedLimitFacets > 0 && len(ids) > testEmbedLimitFacets {
					ids = ids[:testEmbedLimitFacets]
				}
				facet.Emotions = ids
			}
			if ids, err := loadIDs(`SELECT id FROM humor_items WHERE chat_id = ? ORDER BY id`); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load humor_item ids: %w", err))
			} else {
				if testEmbedLimitFacets > 0 && len(ids) > testEmbedLimitFacets {
					ids = ids[:testEmbedLimitFacets]
				}
				facet.Humor = ids
			}

			// Temp queue DB for repeatable embedding benchmark.
			tmp, err := os.CreateTemp("", "eve-queue-test-embeddings-*.db")
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

			geminiClient := gemini.NewClient(cfg.GeminiAPIKey)
			var embedRPMCtrl *engine.AutoRPMController
			if cfg.EmbedRPM > 0 {
				geminiClient.SetEmbedRPM(cfg.EmbedRPM)
			} else {
				embedRPMCtrl = engine.NewAutoRPMController(engine.DefaultAutoRPMConfig(), geminiClient.SetEmbedRPM)
				embedRPMCtrl.Start(context.Background())
			}
			engineCfg := engine.DefaultConfig()
			if testEmbedWorkers > 0 {
				engineCfg.WorkerCount = testEmbedWorkers
			}
			engineCfg.LeaseOwner = fmt.Sprintf("test-embeddings-%d", time.Now().UnixNano())

			// Cap warehouse DB pool to avoid lock/cache thrash at high worker counts.
			maxOpen, maxIdle := recommendedSQLitePool(engineCfg.WorkerCount)
			warehouseDB.SetMaxOpenConns(maxOpen)
			warehouseDB.SetMaxIdleConns(maxIdle)

			// Micro-batch embedding writes to reduce SQLite contention under high concurrency.
			dbWriter := engine.NewTxBatchWriter(warehouseDB, engine.TxBatchWriterConfig{
				BatchSize:     100,
				FlushInterval: 50 * time.Millisecond,
			})
			dbWriter.Start()
			defer dbWriter.Close()

			runPhase := func(phase string, enqueueFn func() (map[string]int, error)) (map[string]interface{}, error) {
				enqueuedByType, err := enqueueFn()
				if err != nil {
					return nil, err
				}
				enqueuedTotal := 0
				for _, v := range enqueuedByType {
					enqueuedTotal += v
				}

				eng := engine.New(q, engineCfg)
				baseHandler := engine.NewEmbeddingJobHandlerWithPipeline(warehouseDB, geminiClient, cfg.EmbedModel, dbWriter)
				if embedRPMCtrl != nil {
					base := baseHandler
					baseHandler = func(ctx context.Context, job *queue.Job) error {
						err := base(ctx, job)
						embedRPMCtrl.Observe(err)
						return err
					}
				}
				eng.RegisterHandler("embedding", baseHandler)

				startTime := time.Now()
				stats, err := eng.Run(context.Background())
				duration := time.Since(startTime)
				if err != nil {
					return nil, err
				}

				throughput := 0.0
				if duration.Seconds() > 0 {
					throughput = float64(stats.Succeeded) / duration.Seconds()
				}

				return map[string]interface{}{
					"phase":                       phase,
					"workers":                     engineCfg.WorkerCount,
					"duration":                    duration.Seconds(),
					"succeeded":                   stats.Succeeded,
					"failed":                      stats.Failed,
					"skipped":                     stats.Skipped,
					"throughput_embeddings_per_s": throughput,
					"enqueued_total":              enqueuedTotal,
					"enqueued_by_type":            enqueuedByType,
				}, nil
			}

			runConversations, err := runPhase("conversation_embeddings", func() (map[string]int, error) {
				enqueuedConversation := 0
				for _, convID := range conversationIDs {
					if err := q.Enqueue(queue.EnqueueOptions{
						Type:     "embedding",
						Key:      fmt.Sprintf("embedding:conversation:%d:%s", convID, cfg.EmbedModel),
						Priority: 30,
						Payload: engine.EmbeddingJobPayload{
							EntityType: "conversation",
							EntityID:   convID,
						},
						MaxAttempts: 3,
					}); err != nil {
						return nil, fmt.Errorf("failed to enqueue conversation embedding %d: %w", convID, err)
					}
					enqueuedConversation++
				}
				return map[string]int{"conversation": enqueuedConversation}, nil
			})
			if err != nil {
				return printErrorJSON(fmt.Errorf("conversation embeddings phase failed: %w", err))
			}

			runFacets, err := runPhase("facet_embeddings", func() (map[string]int, error) {
				enqueuedFacets := map[string]int{"entity": 0, "topic": 0, "emotion": 0, "humor_item": 0}
				for _, id := range facet.Entities {
					if err := q.Enqueue(queue.EnqueueOptions{
						Type:        "embedding",
						Key:         fmt.Sprintf("embedding:entity:%d:%s", id, cfg.EmbedModel),
						Priority:    10,
						Payload:     engine.EmbeddingJobPayload{EntityType: "entity", EntityID: id},
						MaxAttempts: 3,
					}); err != nil {
						return nil, fmt.Errorf("failed to enqueue entity embedding %d: %w", id, err)
					}
					enqueuedFacets["entity"]++
				}
				for _, id := range facet.Topics {
					if err := q.Enqueue(queue.EnqueueOptions{
						Type:        "embedding",
						Key:         fmt.Sprintf("embedding:topic:%d:%s", id, cfg.EmbedModel),
						Priority:    10,
						Payload:     engine.EmbeddingJobPayload{EntityType: "topic", EntityID: id},
						MaxAttempts: 3,
					}); err != nil {
						return nil, fmt.Errorf("failed to enqueue topic embedding %d: %w", id, err)
					}
					enqueuedFacets["topic"]++
				}
				for _, id := range facet.Emotions {
					if err := q.Enqueue(queue.EnqueueOptions{
						Type:        "embedding",
						Key:         fmt.Sprintf("embedding:emotion:%d:%s", id, cfg.EmbedModel),
						Priority:    10,
						Payload:     engine.EmbeddingJobPayload{EntityType: "emotion", EntityID: id},
						MaxAttempts: 3,
					}); err != nil {
						return nil, fmt.Errorf("failed to enqueue emotion embedding %d: %w", id, err)
					}
					enqueuedFacets["emotion"]++
				}
				for _, id := range facet.Humor {
					if err := q.Enqueue(queue.EnqueueOptions{
						Type:        "embedding",
						Key:         fmt.Sprintf("embedding:humor_item:%d:%s", id, cfg.EmbedModel),
						Priority:    10,
						Payload:     engine.EmbeddingJobPayload{EntityType: "humor_item", EntityID: id},
						MaxAttempts: 3,
					}); err != nil {
						return nil, fmt.Errorf("failed to enqueue humor_item embedding %d: %w", id, err)
					}
					enqueuedFacets["humor_item"]++
				}
				return enqueuedFacets, nil
			})
			if err != nil {
				return printErrorJSON(fmt.Errorf("facet embeddings phase failed: %w", err))
			}

			// Counts of embeddings present after run (no vectors printed).
			var convEmbCount int
			if err := warehouseDB.QueryRow(`
				SELECT COUNT(*) FROM embeddings
				WHERE entity_type = 'conversation'
				  AND entity_id IN (SELECT id FROM conversations WHERE chat_id = ?)
				  AND model = ?
			`, targetChatID, cfg.EmbedModel).Scan(&convEmbCount); err != nil {
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
				"ok":               true,
				"chat_id":          targetChatID,
				"chat_name":        chatName,
				"embed_model":      cfg.EmbedModel,
				"embed_rpm_config": cfg.EmbedRPM,
				"embed_rpm_effective": func() int {
					if embedRPMCtrl != nil {
						return embedRPMCtrl.CurrentRPM()
					}
					return cfg.EmbedRPM
				}(),
				"conversations_total": len(conversationIDs),
				"facet_rows_total": map[string]int{
					"entities":    len(facet.Entities),
					"topics":      len(facet.Topics),
					"emotions":    len(facet.Emotions),
					"humor_items": len(facet.Humor),
				},
				"run_conversations": runConversations,
				"run_facets":        runFacets,
				"embeddings_present": map[string]int{
					"conversation": convEmbCount,
					"entity":       entityEmb,
					"topic":        topicEmb,
					"emotion":      emotionEmb,
					"humor_item":   humorEmb,
				},
			}

			return printJSON(output)
		},
	}
	computeTestEmbeddingsCmd.Flags().IntVar(&testEmbedWorkers, "workers", 800, "Number of concurrent workers")
	computeTestEmbeddingsCmd.Flags().IntVar(&testEmbedLimitConvos, "limit-conversations", 0, "Limit number of conversations embedded (0 = all)")
	computeTestEmbeddingsCmd.Flags().IntVar(&testEmbedLimitFacets, "limit-facets", 0, "Limit number of facet rows embedded per type (0 = all)")
	computeTestEmbeddingsCmd.Flags().IntVar(&testEmbedChatID, "chat-id", 0, "Chat ID to embed (0 = chat with most messages)")

	computeCmd.AddCommand(computeStatusCmd)
	computeCmd.AddCommand(computeRunCmd)
	computeCmd.AddCommand(computeTestAnalysisCmd)
	computeCmd.AddCommand(computeTestEmbeddingsCmd)

	// DB command group
	dbCmd := &cobra.Command{
		Use:   "db",
		Short: "Database operations",
	}

	var dbQuerySQL string
	var dbQueryDB string
	var dbQueryWrite bool
	var dbQueryLimit int
	var dbQueryPretty bool

	dbQueryCmd := &cobra.Command{
		Use:   "query",
		Short: "Execute SQL query against Eve database",
		RunE: func(cmd *cobra.Command, args []string) error {
			if dbQuerySQL == "" {
				return printErrorJSON(fmt.Errorf("--sql is required"))
			}

			// Convert db spec to internal format
			dbSpec := "warehouse"
			if dbQueryDB == "queue" {
				dbSpec = "queue"
			} else if dbQueryDB != "" && dbQueryDB != "warehouse" {
				dbSpec = "path:" + dbQueryDB
			}

			result := db.Execute(db.QueryOptions{
				SQL:        dbQuerySQL,
				DBSpec:     dbSpec,
				AllowWrite: dbQueryWrite,
			})

			// Apply limit if specified
			if dbQueryLimit > 0 && len(result.Rows) > dbQueryLimit {
				result.Rows = result.Rows[:dbQueryLimit]
				result.RowCount = dbQueryLimit
			}

			return printJSON(result)
		},
	}

	dbQueryCmd.Flags().StringVar(&dbQuerySQL, "sql", "", "SQL query to execute")
	dbQueryCmd.Flags().StringVar(&dbQueryDB, "db", "warehouse", "Database to query (warehouse, queue, or path)")
	dbQueryCmd.Flags().BoolVar(&dbQueryWrite, "write", false, "Allow write operations")
	dbQueryCmd.Flags().IntVar(&dbQueryLimit, "limit", 0, "Limit number of rows returned")
	dbQueryCmd.Flags().BoolVar(&dbQueryPretty, "pretty", false, "Pretty print JSON output (ignored, always pretty)")

	dbCmd.AddCommand(dbQueryCmd)

	// Prompt commands
	promptCmd := &cobra.Command{
		Use:   "prompt",
		Short: "Manage prompts",
	}

	promptListCmd := &cobra.Command{
		Use:   "list",
		Short: "List available prompts",
		RunE: func(cmd *cobra.Command, args []string) error {
			loader := resources.NewLoader("")
			prompts, err := loader.ListPrompts()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to list prompts: %w", err))
			}

			var output []map[string]interface{}
			for _, p := range prompts {
				output = append(output, map[string]interface{}{
					"id":       p.ID,
					"name":     p.Name,
					"version":  p.Version,
					"category": p.Category,
					"tags":     p.Tags,
				})
			}

			return printJSON(map[string]interface{}{
				"ok":      true,
				"count":   len(prompts),
				"prompts": output,
			})
		},
	}

	var promptShowID string
	promptShowCmd := &cobra.Command{
		Use:   "show",
		Short: "Show a specific prompt",
		RunE: func(cmd *cobra.Command, args []string) error {
			if promptShowID == "" && len(args) > 0 {
				promptShowID = args[0]
			}
			if promptShowID == "" {
				return printErrorJSON(fmt.Errorf("prompt ID is required"))
			}

			loader := resources.NewLoader("")
			prompt, err := loader.LoadPrompt(promptShowID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to load prompt: %w", err))
			}

			return printJSON(map[string]interface{}{
				"ok":       true,
				"id":       prompt.ID,
				"name":     prompt.Name,
				"version":  prompt.Version,
				"category": prompt.Category,
				"tags":     prompt.Tags,
				"body":     prompt.Body,
			})
		},
	}
	promptShowCmd.Flags().StringVar(&promptShowID, "id", "", "Prompt ID to show")

	promptCmd.AddCommand(promptListCmd)
	promptCmd.AddCommand(promptShowCmd)

	// Pack commands
	packCmd := &cobra.Command{
		Use:   "pack",
		Short: "Manage context packs",
	}

	packListCmd := &cobra.Command{
		Use:   "list",
		Short: "List available context packs",
		RunE: func(cmd *cobra.Command, args []string) error {
			loader := resources.NewLoader("")
			packs, err := loader.ListPacks()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to list packs: %w", err))
			}

			var output []map[string]interface{}
			for _, p := range packs {
				output = append(output, map[string]interface{}{
					"id":                     p.ID,
					"name":                   p.Name,
					"version":                p.Version,
					"category":               p.Category,
					"description":            p.Description,
					"total_estimated_tokens": p.TotalEstimatedTokens,
					"slices_count":           len(p.Slices),
				})
			}

			return printJSON(map[string]interface{}{
				"ok":    true,
				"count": len(packs),
				"packs": output,
			})
		},
	}

	var packShowID string
	packShowCmd := &cobra.Command{
		Use:   "show",
		Short: "Show a specific context pack",
		RunE: func(cmd *cobra.Command, args []string) error {
			if packShowID == "" && len(args) > 0 {
				packShowID = args[0]
			}
			if packShowID == "" {
				return printErrorJSON(fmt.Errorf("pack ID is required"))
			}

			loader := resources.NewLoader("")
			pack, err := loader.LoadPack(packShowID)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to load pack: %w", err))
			}

			return printJSON(map[string]interface{}{
				"ok":                     true,
				"id":                     pack.ID,
				"name":                   pack.Name,
				"version":                pack.Version,
				"category":               pack.Category,
				"description":            pack.Description,
				"flexibility":            pack.Flexibility,
				"total_estimated_tokens": pack.TotalEstimatedTokens,
				"slices":                 pack.Slices,
			})
		},
	}
	packShowCmd.Flags().StringVar(&packShowID, "id", "", "Pack ID to show")

	packCmd.AddCommand(packListCmd)
	packCmd.AddCommand(packShowCmd)

	// Encode command
	encodeCmd := &cobra.Command{
		Use:   "encode",
		Short: "Encode data for LLM input",
	}

	var encodeConvoID int
	var encodeStdout bool
	var encodeOutput string

	encodeConvoCmd := &cobra.Command{
		Use:   "conversation",
		Short: "Encode a conversation for LLM input",
		RunE: func(cmd *cobra.Command, args []string) error {
			if encodeConvoID <= 0 {
				return printErrorJSON(fmt.Errorf("--conversation-id is required"))
			}

			cfg := config.Load()

			if encodeStdout {
				result := encoding.EncodeConversationToString(cfg.EveDBPath, encodeConvoID)
				if result.Error != "" {
					return printErrorJSON(fmt.Errorf("%s", result.Error))
				}
				fmt.Println(result.EncodedText)
				return nil
			}

			outputPath := encodeOutput
			if outputPath == "" {
				outputPath = encoding.GetDefaultOutputPath(encodeConvoID)
			}

			result := encoding.EncodeConversationToFile(cfg.EveDBPath, encodeConvoID, outputPath)
			if result.Error != "" {
				return printErrorJSON(fmt.Errorf("%s", result.Error))
			}

			return printJSON(map[string]interface{}{
				"ok":              true,
				"conversation_id": encodeConvoID,
				"output_path":     outputPath,
				"message_count":   result.MessageCount,
				"token_count":     result.TokenCount,
			})
		},
	}

	encodeConvoCmd.Flags().IntVar(&encodeConvoID, "conversation-id", 0, "Conversation ID to encode")
	encodeConvoCmd.Flags().BoolVar(&encodeStdout, "stdout", false, "Output to stdout instead of file")
	encodeConvoCmd.Flags().StringVar(&encodeOutput, "output", "", "Output file path")

	encodeCmd.AddCommand(encodeConvoCmd)

	// Resources command
	resourcesCmd := &cobra.Command{
		Use:   "resources",
		Short: "Manage embedded resources",
	}

	var exportDir string
	resourcesExportCmd := &cobra.Command{
		Use:   "export",
		Short: "Export embedded prompts and packs to a directory",
		RunE: func(cmd *cobra.Command, args []string) error {
			if exportDir == "" {
				return printErrorJSON(fmt.Errorf("--dir is required"))
			}

			loader := resources.NewLoader("")
			promptCount, packCount, err := loader.ExportResources(exportDir)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to export resources: %w", err))
			}

			return printJSON(map[string]interface{}{
				"ok":            true,
				"target_dir":    exportDir,
				"prompts_count": promptCount,
				"packs_count":   packCount,
			})
		},
	}
	resourcesExportCmd.Flags().StringVar(&exportDir, "dir", "", "Target directory for export")

	resourcesCmd.AddCommand(resourcesExportCmd)

	// Whoami command - returns user info (name, phone, email)
	whoamiCmd := &cobra.Command{
		Use:   "whoami",
		Short: "Display information about the current user",
		RunE: func(cmd *cobra.Command, args []string) error {
			// Get macOS user full name
			fullName := ""
			if out, err := exec.Command("id", "-F").Output(); err == nil {
				fullName = strings.TrimSpace(string(out))
			}

			// Get user's accounts from chat.db (phones and emails used for sending)
			chatDBPath := etl.GetChatDBPath()
			var phones []string
			var emails []string

			if chatDBPath != "" {
				chatDB, err := sql.Open("sqlite3", chatDBPath+"?mode=ro")
				if err == nil {
					defer chatDB.Close()

					// Query distinct accounts from outgoing messages
					rows, err := chatDB.Query(`
						SELECT DISTINCT account 
						FROM message 
						WHERE is_from_me = 1 
						  AND account IS NOT NULL 
						  AND account != ''
					`)
					if err == nil {
						for rows.Next() {
							var account string
							if err := rows.Scan(&account); err == nil {
								// Parse account format: P:+1xxx or E:email@example.com
								if strings.HasPrefix(account, "P:") {
									phone := strings.TrimPrefix(account, "P:")
									if phone != "" && !contains(phones, phone) {
										phones = append(phones, phone)
									}
								} else if strings.HasPrefix(account, "E:") {
									email := strings.TrimPrefix(account, "E:")
									if email != "" && !contains(emails, email) {
										emails = append(emails, email)
									}
								}
							}
						}
						rows.Close()
					}
				}
			}

			output := map[string]interface{}{
				"ok":     true,
				"name":   fullName,
				"phones": phones,
				"emails": emails,
			}

			return printJSON(output)
		},
	}

	rootCmd.AddCommand(versionCmd)
	rootCmd.AddCommand(pathsCmd)
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(syncCmd)
	rootCmd.AddCommand(computeCmd)
	rootCmd.AddCommand(dbCmd)
	rootCmd.AddCommand(promptCmd)
	rootCmd.AddCommand(packCmd)
	rootCmd.AddCommand(encodeCmd)
	rootCmd.AddCommand(resourcesCmd)
	rootCmd.AddCommand(whoamiCmd)

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

func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}
