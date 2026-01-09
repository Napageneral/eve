package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
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

			// Reset prior facet rows for this chat so counts reflect this run.
			tx, err := warehouseDB.Begin()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to begin reset transaction: %w", err))
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

			eng := engine.New(q, engineCfg)
			eng.RegisterHandler("analysis", engine.NewAnalysisJobHandler(warehouseDB, geminiClient, cfg.AnalysisModel))

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

			// Aggregate facet counts (no message text).
			var topicsTotal, entitiesTotal, emotionsTotal, humorTotal int
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

			output := map[string]interface{}{
				"ok": true,
				"casey_contact_id": caseyContactID,
				"casey_chat_id": caseyChatID,
				"conversations_total": len(conversationIDs),
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

	computeCmd.AddCommand(computeStatusCmd)
	computeCmd.AddCommand(computeRunCmd)
	computeCmd.AddCommand(computeTestCaseyCmd)

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
