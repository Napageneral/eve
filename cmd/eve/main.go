package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"github.com/Napageneral/eve/internal/config"
	"github.com/Napageneral/eve/internal/db"
	"github.com/Napageneral/eve/internal/encoding"
	"github.com/Napageneral/eve/internal/engine"
	"github.com/Napageneral/eve/internal/etl"
	"github.com/Napageneral/eve/internal/gemini"
	"github.com/Napageneral/eve/internal/migrate"
	"github.com/Napageneral/eve/internal/queue"
	"github.com/Napageneral/eve/internal/resources"
	_ "github.com/mattn/go-sqlite3"
	"github.com/spf13/cobra"
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
		Short: "Eve - Personal Intelligence Engine for iMessage",
		Long: `Eve is a personal intelligence engine for your iMessage conversations.

QUICK UTILITY (imsg-compatible):
  eve chats              List your chats
  eve contacts           List/search contacts
  eve messages           Query messages with powerful filters
  eve send               Send messages via iMessage/SMS
  eve watch              Stream incoming messages in real-time

INTELLIGENCE ENGINE (the real product):
  eve analyze            Queue AI analysis for conversations
  eve insights           Query analysis results (topics, entities, emotions)
  eve search             Semantic search using AI embeddings
  eve prompt             Explore analysis prompts
  eve pack               Explore context packs

SETUP & OPERATIONS:
  eve init               Initialize Eve databases
  eve sync               Sync from macOS Messages database
  eve compute run        Process analysis/embedding queue

Run 'eve help' for a comprehensive guide, or 'eve <command> --help' for command details.`,
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

	// Help command - comprehensive guide for users and agents
	helpCmd := &cobra.Command{
		Use:   "guide",
		Short: "Comprehensive guide to Eve's capabilities",
		Long:  "Display a comprehensive guide explaining Eve's capabilities and usage patterns.",
		Run: func(cmd *cobra.Command, args []string) {
			helpText := `
╔══════════════════════════════════════════════════════════════════════════════╗
║                    EVE - Personal Intelligence Engine                        ║
╚══════════════════════════════════════════════════════════════════════════════╝

Eve is TWO things:

  1. A QUICK UTILITY for reading and sending iMessages (replaces imsg)
  2. A PERSONAL INTELLIGENCE ENGINE for understanding your relationships

================================================================================
                           QUICK START (5 minutes)
================================================================================

  # Setup (one time)
  eve init                           # Create databases
  eve sync                           # Import your messages (~1 min for 100k msgs)

  # Basic usage
  eve contacts --top 5               # Your most messaged contacts
  eve messages --contact "Mom"       # Recent messages with someone
  eve send --contact "Mom" --text "Love you!"

================================================================================
                              UTILITY COMMANDS
================================================================================

CHATS - List your conversations
  eve chats                          # All chats, most recent first
  eve chats --limit 10               # Limit results
  eve chats --search "Family"        # Search by name

CONTACTS - Find people
  eve contacts                       # All contacts
  eve contacts --search "John"       # Search by name
  eve contacts --top 10              # Top 10 by message count

MESSAGES - Query message history
  eve messages --contact "Casey"     # Messages with a contact
  eve messages --chat-id 2           # Messages in a specific chat
  eve messages --since 2026-01-01    # Date filtering
  eve messages --search "dinner"     # Content search
  eve messages --format jsonl        # Streaming output (imsg-compatible)

  # Combine filters:
  eve messages --contact "Mom" --since 2026-01-01 --search "birthday"

ATTACHMENTS - List media and files
  eve messages attachments --chat-id 2
  eve messages attachments --type image
  eve attachments --type video       # Also works as top-level command

SEND - Send messages
  eve send --to "+14155551212" --text "Hello!"
  eve send --contact "Casey" --text "Hey!"
  eve send --contact "Casey" --text "Check this" --file ~/photo.jpg

WATCH - Stream incoming messages (for automation)
  eve watch                          # All new messages
  eve watch --chat-id 2              # Specific chat only

================================================================================
                         INTELLIGENCE ENGINE
================================================================================

This is where Eve shines. The intelligence features help you understand your
relationships and yourself through your conversations.

THE SELF-DISCOVERY JOURNEY:

  Step 1: Analyze your closest relationships
  ─────────────────────────────────────────────
  eve analyze --contact "Casey"      # Your best friend
  eve analyze --contact "Mom"        # Family
  eve analyze --contact "..."        # Top 3-5 relationships

  Step 2: Process the analysis queue
  ─────────────────────────────────────────────
  eve compute run                    # Let it process (may take a while)
  eve compute status                 # Check progress

  Step 3: Explore the insights
  ─────────────────────────────────────────────
  eve insights --chat-id 2           # Overview for a chat
  eve insights topics --chat-id 2    # What you talk about
  eve insights entities --chat-id 2  # People, places, things mentioned
  eve insights emotions --chat-id 2  # Emotional patterns
  eve insights humor --chat-id 2     # Funny moments

  Step 4: Semantic search
  ─────────────────────────────────────────────
  eve search "when did we talk about moving"
  eve search "restaurant recommendations" --chat-id 2

PROMPTS & PACKS - The analysis framework
  eve prompt list                    # See available analysis prompts
  eve prompt show convo-all-v1       # View a specific prompt
  eve pack list                      # See context packs
  eve encode conversation --id 123   # Encode a conversation for LLM input

================================================================================
                              OPERATIONS
================================================================================

SETUP:
  eve init                           # Create databases
  eve sync                           # Sync from chat.db
  eve sync --dry-run                 # Preview what would sync

COMPUTE ENGINE:
  eve compute status                 # Queue status
  eve compute run                    # Process jobs
  eve compute run --workers 20       # More parallelism

INFO:
  eve whoami                         # Your name, phone, email
  eve paths                          # Data file locations
  eve version                        # Version info

ADVANCED - Raw SQL:
  eve db query --sql "SELECT COUNT(*) FROM messages"
  eve db query --sql "SELECT * FROM topics LIMIT 10"

================================================================================
                           ENVIRONMENT VARIABLES
================================================================================

  GEMINI_API_KEY      Required for semantic search and AI analysis
  EVE_APP_DIR         Override default data directory
  EVE_SOURCE_CHAT_DB  Override chat.db path

================================================================================
                              TIPS FOR AGENTS
================================================================================

When using Eve as an AI agent:

  1. Use --format jsonl for machine-readable streaming output
  2. Use eve whoami to learn about the user
  3. Use eve contacts --top 5 to understand their social graph
  4. Use eve insights to provide relationship insights
  5. Use eve search for semantic queries about past conversations
  6. Always confirm before sending messages

Example agent workflow:
  "What did Casey and I talk about last week?"
  → eve messages --contact "Casey" --since 2026-01-03 --until 2026-01-10
  → eve insights topics --contact "Casey"
  → eve search "last week's plans" --chat-id <casey's chat>

For more: https://github.com/Napageneral/eve
`
			fmt.Println(helpText)
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
	var syncFull bool

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
			if !syncFull {
				wm, err := etl.GetWatermark(warehouseDB, "chatdb", "message_rowid")
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to get watermark: %w", err))
				}
				if wm != nil && wm.ValueInt.Valid {
					sinceRowID = wm.ValueInt.Int64
				}
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
				"full":                syncFull,
				"chat_db_path":        chatDBPath,
				"handles_synced":      syncResult.HandlesCount,
				"chats_synced":        syncResult.ChatsCount,
				"messages_synced":     syncResult.MessagesCount,
				"reactions_synced":    syncResult.ReactionsCount,
				"membership_synced":   syncResult.MembershipCount,
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
	syncCmd.Flags().BoolVar(&syncFull, "full", false, "Force full resync (reprocess all messages; repairs orphan chat_ids and rebuilds conversations)")

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

	// Chats command - list chats with names
	var chatsLimit int
	var chatsSearch string
	var chatsJSON bool

	chatsCmd := &cobra.Command{
		Use:   "chats",
		Short: "List chats sorted by recent activity",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer db.Close()

			// Build query with optional search filter
			query := `
				SELECT 
					c.id,
					COALESCE(c.chat_name, '') as chat_name,
					c.chat_identifier,
					c.is_group,
					c.service_name,
					COALESCE(c.total_messages, 0) as total_messages,
					c.last_message_date,
					c.created_date
				FROM chats c
			`
			queryArgs := []interface{}{}

			if chatsSearch != "" {
				query += ` WHERE c.chat_name LIKE ? OR c.chat_identifier LIKE ?`
				searchPattern := "%" + chatsSearch + "%"
				queryArgs = append(queryArgs, searchPattern, searchPattern)
			}

			query += ` ORDER BY c.last_message_date DESC NULLS LAST, c.id DESC`

			if chatsLimit > 0 {
				query += fmt.Sprintf(" LIMIT %d", chatsLimit)
			}

			rows, err := db.Query(query, queryArgs...)
			if err != nil {
				return printErrorJSON(fmt.Errorf("query failed: %w", err))
			}
			defer rows.Close()

			type chatRow struct {
				ID              int64      `json:"id"`
				ChatName        string     `json:"chat_name"`
				ChatIdentifier  string     `json:"chat_identifier"`
				IsGroup         bool       `json:"is_group"`
				ServiceName     string     `json:"service_name"`
				TotalMessages   int        `json:"total_messages"`
				LastMessageDate *time.Time `json:"last_message_date,omitempty"`
				CreatedDate     *time.Time `json:"created_date,omitempty"`
			}

			var chats []chatRow
			for rows.Next() {
				var chat chatRow
				var lastMsgDate, createdDate sql.NullString

				err := rows.Scan(
					&chat.ID,
					&chat.ChatName,
					&chat.ChatIdentifier,
					&chat.IsGroup,
					&chat.ServiceName,
					&chat.TotalMessages,
					&lastMsgDate,
					&createdDate,
				)
				if err != nil {
					return printErrorJSON(fmt.Errorf("scan failed: %w", err))
				}

				if lastMsgDate.Valid {
					t, _ := time.Parse(time.RFC3339, lastMsgDate.String)
					chat.LastMessageDate = &t
				}
				if createdDate.Valid {
					t, _ := time.Parse(time.RFC3339, createdDate.String)
					chat.CreatedDate = &t
				}

				chats = append(chats, chat)
			}

			if err := rows.Err(); err != nil {
				return printErrorJSON(fmt.Errorf("rows error: %w", err))
			}

			return printJSON(map[string]interface{}{
				"ok":    true,
				"count": len(chats),
				"chats": chats,
			})
		},
	}

	chatsCmd.Flags().IntVar(&chatsLimit, "limit", 0, "Limit number of results (0 = no limit)")
	chatsCmd.Flags().StringVar(&chatsSearch, "search", "", "Filter chats by name or identifier")
	chatsCmd.Flags().BoolVar(&chatsJSON, "json", true, "Output as JSON (always true)")

	// Contacts command - search and list contacts
	var contactsLimit int
	var contactsSearch string
	var contactsTop int

	contactsCmd := &cobra.Command{
		Use:   "contacts",
		Short: "List and search contacts",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer db.Close()

			// Build query
			query := `
				SELECT 
					c.id,
					COALESCE(c.name, '') as name,
					c.is_me,
					c.data_source,
					(SELECT COUNT(*) FROM messages m WHERE m.sender_id = c.id) as message_count
				FROM contacts c
				WHERE c.name IS NOT NULL AND c.name != ''
			`
			queryArgs := []interface{}{}

			if contactsSearch != "" {
				query += ` AND c.name LIKE ?`
				queryArgs = append(queryArgs, "%"+contactsSearch+"%")
			}

			if contactsTop > 0 {
				query += ` ORDER BY message_count DESC LIMIT ?`
				queryArgs = append(queryArgs, contactsTop)
			} else {
				query += ` ORDER BY c.name ASC`
				if contactsLimit > 0 {
					query += fmt.Sprintf(" LIMIT %d", contactsLimit)
				}
			}

			rows, err := db.Query(query, queryArgs...)
			if err != nil {
				return printErrorJSON(fmt.Errorf("query failed: %w", err))
			}
			defer rows.Close()

			type contactRow struct {
				ID           int64  `json:"id"`
				Name         string `json:"name"`
				IsMe         bool   `json:"is_me"`
				DataSource   string `json:"data_source"`
				MessageCount int    `json:"message_count"`
			}

			var contacts []contactRow
			for rows.Next() {
				var contact contactRow
				err := rows.Scan(
					&contact.ID,
					&contact.Name,
					&contact.IsMe,
					&contact.DataSource,
					&contact.MessageCount,
				)
				if err != nil {
					return printErrorJSON(fmt.Errorf("scan failed: %w", err))
				}
				contacts = append(contacts, contact)
			}

			return printJSON(map[string]interface{}{
				"ok":       true,
				"count":    len(contacts),
				"contacts": contacts,
			})
		},
	}

	contactsCmd.Flags().IntVar(&contactsLimit, "limit", 0, "Limit number of results")
	contactsCmd.Flags().StringVar(&contactsSearch, "search", "", "Search contacts by name")
	contactsCmd.Flags().IntVar(&contactsTop, "top", 0, "Show top N contacts by message count")

	// Messages command - query messages with filters
	var msgsChatID int
	var msgsContact string
	var msgsSince string
	var msgsUntil string
	var msgsSearch string
	var msgsLimit int
	var msgsFormat string
	var msgsAttachments bool

	messagesCmd := &cobra.Command{
		Use:   "messages",
		Short: "Query messages with filters",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer db.Close()

			// Resolve contact to chat_id if specified
			targetChatID := msgsChatID
			if msgsContact != "" && targetChatID == 0 {
				// Find contact by name (fuzzy match)
				var contactID int64
				err := db.QueryRow(`
					SELECT id FROM contacts 
					WHERE name LIKE ? 
					ORDER BY 
						CASE WHEN name = ? THEN 0 ELSE 1 END,
						LENGTH(name)
					LIMIT 1
				`, "%"+msgsContact+"%", msgsContact).Scan(&contactID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("contact '%s' not found", msgsContact))
				}

				// Find chat(s) with this contact
				err = db.QueryRow(`
					SELECT DISTINCT m.chat_id 
					FROM messages m 
					WHERE m.sender_id = ? 
					LIMIT 1
				`, contactID).Scan(&targetChatID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("no messages found for contact '%s'", msgsContact))
				}
			}

			// Build query
			query := `
				SELECT 
					m.id,
					m.timestamp,
					m.content,
					m.is_from_me,
					m.chat_id,
					COALESCE(c.name, '') as sender_name
				FROM messages m
				LEFT JOIN contacts c ON m.sender_id = c.id
				WHERE 1=1
			`
			queryArgs := []interface{}{}

			if targetChatID > 0 {
				query += ` AND m.chat_id = ?`
				queryArgs = append(queryArgs, targetChatID)
			}

			if msgsSince != "" {
				// Parse date (supports YYYY-MM-DD or ISO8601)
				sinceTime, err := parseDate(msgsSince)
				if err != nil {
					return printErrorJSON(fmt.Errorf("invalid --since date: %w", err))
				}
				query += ` AND m.timestamp >= ?`
				queryArgs = append(queryArgs, sinceTime.Format(time.RFC3339))
			}

			if msgsUntil != "" {
				untilTime, err := parseDate(msgsUntil)
				if err != nil {
					return printErrorJSON(fmt.Errorf("invalid --until date: %w", err))
				}
				query += ` AND m.timestamp < ?`
				queryArgs = append(queryArgs, untilTime.Format(time.RFC3339))
			}

			if msgsSearch != "" {
				query += ` AND m.content LIKE ?`
				queryArgs = append(queryArgs, "%"+msgsSearch+"%")
			}

			query += ` ORDER BY m.timestamp DESC`

			if msgsLimit > 0 {
				query += fmt.Sprintf(" LIMIT %d", msgsLimit)
			} else {
				query += " LIMIT 100" // Default limit
			}

			rows, err := db.Query(query, queryArgs...)
			if err != nil {
				return printErrorJSON(fmt.Errorf("query failed: %w", err))
			}
			defer rows.Close()

			type attachmentInfo struct {
				ID       int64  `json:"id"`
				FileName string `json:"file_name"`
				MimeType string `json:"mime_type"`
			}

			type messageRow struct {
				ID          int64            `json:"id"`
				Timestamp   string           `json:"timestamp"`
				Content     string           `json:"content"`
				IsFromMe    bool             `json:"is_from_me"`
				ChatID      int64            `json:"chat_id"`
				SenderName  string           `json:"sender_name"`
				Attachments []attachmentInfo `json:"attachments,omitempty"`
			}

			var messages []messageRow
			for rows.Next() {
				var msg messageRow
				var content sql.NullString
				err := rows.Scan(
					&msg.ID,
					&msg.Timestamp,
					&content,
					&msg.IsFromMe,
					&msg.ChatID,
					&msg.SenderName,
				)
				if err != nil {
					return printErrorJSON(fmt.Errorf("scan failed: %w", err))
				}
				if content.Valid {
					msg.Content = content.String
				}
				if msg.IsFromMe {
					msg.SenderName = "Me"
				}
				messages = append(messages, msg)
			}

			// Load attachments if requested
			if msgsAttachments {
				for i := range messages {
					attRows, err := db.Query(`
						SELECT id, COALESCE(file_name, ''), COALESCE(mime_type, '')
						FROM attachments WHERE message_id = ?
					`, messages[i].ID)
					if err != nil {
						continue
					}
					for attRows.Next() {
						var att attachmentInfo
						attRows.Scan(&att.ID, &att.FileName, &att.MimeType)
						messages[i].Attachments = append(messages[i].Attachments, att)
					}
					attRows.Close()
				}
			}

			// Output based on format
			if msgsFormat == "jsonl" {
				// JSONL format - one JSON object per line (imsg-compatible)
				type jsonlRow struct {
					ID          int64            `json:"id"`
					GUID        string           `json:"guid,omitempty"`
					Timestamp   string           `json:"created_at"`
					Content     string           `json:"text"`
					IsFromMe    bool             `json:"is_from_me"`
					ChatID      int64            `json:"chat_id"`
					Sender      string           `json:"sender"`
					Attachments []attachmentInfo `json:"attachments,omitempty"`
				}
				for _, msg := range messages {
					row := jsonlRow{
						ID:          msg.ID,
						Timestamp:   msg.Timestamp,
						Content:     msg.Content,
						IsFromMe:    msg.IsFromMe,
						ChatID:      msg.ChatID,
						Sender:      msg.SenderName,
						Attachments: msg.Attachments,
					}
					data, _ := json.Marshal(row)
					fmt.Println(string(data))
				}
				return nil
			}

			// Default: wrapped JSON
			return printJSON(map[string]interface{}{
				"ok":       true,
				"count":    len(messages),
				"messages": messages,
			})
		},
	}

	messagesCmd.Flags().IntVar(&msgsChatID, "chat-id", 0, "Filter by chat ID")
	messagesCmd.Flags().StringVar(&msgsContact, "contact", "", "Filter by contact name")
	messagesCmd.Flags().StringVar(&msgsSince, "since", "", "Start date (YYYY-MM-DD or ISO8601)")
	messagesCmd.Flags().StringVar(&msgsUntil, "until", "", "End date (YYYY-MM-DD or ISO8601)")
	messagesCmd.Flags().StringVar(&msgsSearch, "search", "", "Search message content")
	messagesCmd.Flags().IntVar(&msgsLimit, "limit", 100, "Limit number of results")
	messagesCmd.Flags().StringVar(&msgsFormat, "format", "json", "Output format: json (default) or jsonl (streaming)")
	messagesCmd.Flags().BoolVar(&msgsAttachments, "attachments", false, "Include attachment metadata")

	// Messages attachments subcommand
	var msgsAttChatID int
	var msgsAttMessageID int64
	var msgsAttType string
	var msgsAttLimit int

	messagesAttachmentsCmd := &cobra.Command{
		Use:   "attachments",
		Short: "List attachments from messages",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer db.Close()

			query := `
				SELECT 
					a.id,
					a.message_id,
					a.file_name,
					a.mime_type,
					a.size,
					a.is_sticker,
					m.chat_id
				FROM attachments a
				JOIN messages m ON a.message_id = m.id
				WHERE 1=1
			`
			queryArgs := []interface{}{}

			if msgsAttChatID > 0 {
				query += ` AND m.chat_id = ?`
				queryArgs = append(queryArgs, msgsAttChatID)
			}

			if msgsAttMessageID > 0 {
				query += ` AND a.message_id = ?`
				queryArgs = append(queryArgs, msgsAttMessageID)
			}

			if msgsAttType != "" {
				query += ` AND a.mime_type LIKE ?`
				queryArgs = append(queryArgs, msgsAttType+"%")
			}

			query += ` ORDER BY a.id DESC`

			if msgsAttLimit > 0 {
				query += fmt.Sprintf(" LIMIT %d", msgsAttLimit)
			} else {
				query += " LIMIT 100"
			}

			rows, err := db.Query(query, queryArgs...)
			if err != nil {
				return printErrorJSON(fmt.Errorf("query failed: %w", err))
			}
			defer rows.Close()

			type attachmentRow struct {
				ID        int64  `json:"id"`
				MessageID int64  `json:"message_id"`
				FileName  string `json:"file_name"`
				MimeType  string `json:"mime_type"`
				Size      int64  `json:"size"`
				IsSticker bool   `json:"is_sticker"`
				ChatID    int64  `json:"chat_id"`
			}

			var attachments []attachmentRow
			for rows.Next() {
				var att attachmentRow
				var fileName, mimeType sql.NullString
				var size sql.NullInt64

				err := rows.Scan(
					&att.ID,
					&att.MessageID,
					&fileName,
					&mimeType,
					&size,
					&att.IsSticker,
					&att.ChatID,
				)
				if err != nil {
					return printErrorJSON(fmt.Errorf("scan failed: %w", err))
				}

				if fileName.Valid {
					att.FileName = fileName.String
				}
				if mimeType.Valid {
					att.MimeType = mimeType.String
				}
				if size.Valid {
					att.Size = size.Int64
				}

				attachments = append(attachments, att)
			}

			return printJSON(map[string]interface{}{
				"ok":          true,
				"count":       len(attachments),
				"attachments": attachments,
			})
		},
	}

	messagesAttachmentsCmd.Flags().IntVar(&msgsAttChatID, "chat-id", 0, "Filter by chat ID")
	messagesAttachmentsCmd.Flags().Int64Var(&msgsAttMessageID, "message-id", 0, "Filter by message ID")
	messagesAttachmentsCmd.Flags().StringVar(&msgsAttType, "type", "", "Filter by MIME type prefix (e.g., 'image', 'video')")
	messagesAttachmentsCmd.Flags().IntVar(&msgsAttLimit, "limit", 100, "Limit number of results")

	messagesCmd.AddCommand(messagesAttachmentsCmd)

	// History command - imsg-compatible message history
	var historyChatID int
	var historyLimit int
	var historyStart string
	var historyEnd string
	var historyAttachments bool

	historyCmd := &cobra.Command{
		Use:        "history",
		Short:      "Show message history (alias for 'messages --format jsonl')",
		Deprecated: "use 'eve messages --format jsonl' instead",
		RunE: func(cmd *cobra.Command, args []string) error {
			if historyChatID == 0 {
				return printErrorJSON(fmt.Errorf("--chat-id is required"))
			}

			cfg := config.Load()

			db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer db.Close()

			// Build query
			query := `
				SELECT 
					m.id,
					m.guid,
					m.timestamp,
					m.content,
					m.is_from_me,
					m.chat_id,
					COALESCE(c.name, '') as sender_name
				FROM messages m
				LEFT JOIN contacts c ON m.sender_id = c.id
				WHERE m.chat_id = ?
			`
			queryArgs := []interface{}{historyChatID}

			if historyStart != "" {
				startTime, err := parseDate(historyStart)
				if err != nil {
					return printErrorJSON(fmt.Errorf("invalid --start date: %w", err))
				}
				query += ` AND m.timestamp >= ?`
				queryArgs = append(queryArgs, startTime.Format(time.RFC3339))
			}

			if historyEnd != "" {
				endTime, err := parseDate(historyEnd)
				if err != nil {
					return printErrorJSON(fmt.Errorf("invalid --end date: %w", err))
				}
				query += ` AND m.timestamp < ?`
				queryArgs = append(queryArgs, endTime.Format(time.RFC3339))
			}

			query += ` ORDER BY m.timestamp DESC`

			if historyLimit > 0 {
				query += fmt.Sprintf(" LIMIT %d", historyLimit)
			} else {
				query += " LIMIT 50"
			}

			rows, err := db.Query(query, queryArgs...)
			if err != nil {
				return printErrorJSON(fmt.Errorf("query failed: %w", err))
			}
			defer rows.Close()

			type attachmentInfo struct {
				ID       int64  `json:"id"`
				FileName string `json:"file_name"`
				MimeType string `json:"mime_type"`
			}

			type historyRow struct {
				ID          int64            `json:"id"`
				GUID        string           `json:"guid"`
				Timestamp   string           `json:"created_at"`
				Content     string           `json:"text"`
				IsFromMe    bool             `json:"is_from_me"`
				ChatID      int64            `json:"chat_id"`
				Sender      string           `json:"sender"`
				Attachments []attachmentInfo `json:"attachments,omitempty"`
			}

			var messages []historyRow
			for rows.Next() {
				var msg historyRow
				var content sql.NullString
				err := rows.Scan(
					&msg.ID,
					&msg.GUID,
					&msg.Timestamp,
					&content,
					&msg.IsFromMe,
					&msg.ChatID,
					&msg.Sender,
				)
				if err != nil {
					return printErrorJSON(fmt.Errorf("scan failed: %w", err))
				}
				if content.Valid {
					msg.Content = content.String
				}
				if msg.IsFromMe {
					msg.Sender = "Me"
				}
				messages = append(messages, msg)
			}

			// Load attachments if requested
			if historyAttachments {
				for i := range messages {
					attRows, err := db.Query(`
						SELECT id, COALESCE(file_name, ''), COALESCE(mime_type, '')
						FROM attachments WHERE message_id = ?
					`, messages[i].ID)
					if err != nil {
						continue
					}
					for attRows.Next() {
						var att attachmentInfo
						attRows.Scan(&att.ID, &att.FileName, &att.MimeType)
						messages[i].Attachments = append(messages[i].Attachments, att)
					}
					attRows.Close()
				}
			}

			// Output in imsg-compatible format (one JSON per line for streaming)
			for _, msg := range messages {
				data, _ := json.Marshal(msg)
				fmt.Println(string(data))
			}
			return nil
		},
	}

	historyCmd.Flags().IntVar(&historyChatID, "chat-id", 0, "Chat ID (required)")
	historyCmd.Flags().IntVar(&historyLimit, "limit", 50, "Limit number of messages")
	historyCmd.Flags().StringVar(&historyStart, "start", "", "Start date (ISO8601)")
	historyCmd.Flags().StringVar(&historyEnd, "end", "", "End date (ISO8601)")
	historyCmd.Flags().BoolVar(&historyAttachments, "attachments", false, "Include attachment metadata")

	// Send command - send messages via AppleScript
	var sendTo string
	var sendChatID int
	var sendContact string
	var sendText string
	var sendFile string
	var sendService string

	sendCmd := &cobra.Command{
		Use:   "send",
		Short: "Send a message via iMessage/SMS",
		RunE: func(cmd *cobra.Command, args []string) error {
			if sendText == "" && sendFile == "" {
				return printErrorJSON(fmt.Errorf("--text or --file is required"))
			}

			// Resolve recipient
			recipient := sendTo
			if sendContact != "" && recipient == "" {
				// Resolve contact name to phone/email
				cfg := config.Load()
				db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
				}
				defer db.Close()

				// Find contact and their identifier
				var identifier string
				err = db.QueryRow(`
					SELECT ci.identifier 
					FROM contacts c
					JOIN contact_identifiers ci ON ci.contact_id = c.id
					WHERE c.name LIKE ?
					ORDER BY 
						CASE WHEN c.name = ? THEN 0 ELSE 1 END,
						LENGTH(c.name)
					LIMIT 1
				`, "%"+sendContact+"%", sendContact).Scan(&identifier)
				if err != nil {
					return printErrorJSON(fmt.Errorf("contact '%s' not found or has no identifier", sendContact))
				}
				recipient = identifier
			}

			if sendChatID > 0 && recipient == "" {
				// Get chat identifier from chat ID
				cfg := config.Load()
				db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
				}
				defer db.Close()

				err = db.QueryRow(`SELECT chat_identifier FROM chats WHERE id = ?`, sendChatID).Scan(&recipient)
				if err != nil {
					return printErrorJSON(fmt.Errorf("chat ID %d not found", sendChatID))
				}
			}

			if recipient == "" {
				return printErrorJSON(fmt.Errorf("--to, --chat-id, or --contact is required"))
			}

			// Build AppleScript
			var script string
			if sendFile != "" {
				// Send with attachment
				script = fmt.Sprintf(`
					tell application "Messages"
						set targetService to 1st account whose service type = iMessage
						set targetBuddy to participant "%s" of targetService
						send "%s" to targetBuddy
						send POSIX file "%s" to targetBuddy
					end tell
				`, escapeAppleScript(recipient), escapeAppleScript(sendText), escapeAppleScript(sendFile))
			} else {
				// Text only
				script = fmt.Sprintf(`
					tell application "Messages"
						set targetService to 1st account whose service type = iMessage
						set targetBuddy to participant "%s" of targetService
						send "%s" to targetBuddy
					end tell
				`, escapeAppleScript(recipient), escapeAppleScript(sendText))
			}

			// Execute AppleScript
			execCmd := exec.Command("osascript", "-e", script)
			output, err := execCmd.CombinedOutput()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to send message: %s (output: %s)", err, string(output)))
			}

			return printJSON(map[string]interface{}{
				"ok":        true,
				"sent":      true,
				"recipient": recipient,
				"text":      sendText,
				"file":      sendFile,
			})
		},
	}

	sendCmd.Flags().StringVar(&sendTo, "to", "", "Recipient phone number or email")
	sendCmd.Flags().IntVar(&sendChatID, "chat-id", 0, "Send to existing chat by ID")
	sendCmd.Flags().StringVar(&sendContact, "contact", "", "Send to contact by name")
	sendCmd.Flags().StringVar(&sendText, "text", "", "Message text")
	sendCmd.Flags().StringVar(&sendFile, "file", "", "Path to attachment file")
	sendCmd.Flags().StringVar(&sendService, "service", "imessage", "Service: imessage or sms")

	// Watch command - stream incoming messages
	var watchChatID int
	var watchSinceRowID int64
	var watchPollInterval int

	watchCmd := &cobra.Command{
		Use:   "watch",
		Short: "Stream incoming messages in real-time",
		RunE: func(cmd *cobra.Command, args []string) error {
			// Get chat.db path
			chatDBPath := etl.GetChatDBPath()
			if chatDBPath == "" {
				return printErrorJSON(fmt.Errorf("failed to determine chat.db path"))
			}

			// Open chat.db for reading
			chatDB, err := sql.Open("sqlite3", chatDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open chat.db: %w", err))
			}
			defer chatDB.Close()

			// Get initial max rowid if not specified
			lastRowID := watchSinceRowID
			if lastRowID == 0 {
				err := chatDB.QueryRow("SELECT COALESCE(MAX(ROWID), 0) FROM message").Scan(&lastRowID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to get max rowid: %w", err))
				}
			}

			// Print startup message to stderr so stdout stays clean for JSON
			fmt.Fprintf(os.Stderr, "Watching for new messages (since rowid %d)...\n", lastRowID)

			// Setup signal handling
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()

			sigChan := make(chan os.Signal, 1)
			signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
			go func() {
				<-sigChan
				cancel()
			}()

			// Poll interval
			pollDuration := time.Duration(watchPollInterval) * time.Millisecond
			if pollDuration < 100*time.Millisecond {
				pollDuration = 250 * time.Millisecond
			}

			ticker := time.NewTicker(pollDuration)
			defer ticker.Stop()

			for {
				select {
				case <-ctx.Done():
					return nil
				case <-ticker.C:
					// Query for new messages
					query := `
						SELECT 
							m.ROWID,
							m.guid,
							m.text,
							m.is_from_me,
							m.date,
							COALESCE(h.id, '') as sender_id,
							COALESCE(c.ROWID, 0) as chat_id,
							COALESCE(c.display_name, '') as chat_name
						FROM message m
						LEFT JOIN handle h ON m.handle_id = h.ROWID
						LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
						LEFT JOIN chat c ON cmj.chat_id = c.ROWID
						WHERE m.ROWID > ?
					`
					queryArgs := []interface{}{lastRowID}

					if watchChatID > 0 {
						query += ` AND c.ROWID = ?`
						queryArgs = append(queryArgs, watchChatID)
					}

					query += ` ORDER BY m.ROWID ASC`

					rows, err := chatDB.Query(query, queryArgs...)
					if err != nil {
						fmt.Fprintf(os.Stderr, "Query error: %v\n", err)
						continue
					}

					for rows.Next() {
						var rowID int64
						var guid, text, senderID, chatName string
						var isFromMe bool
						var dateNano int64
						var chatID int64

						err := rows.Scan(&rowID, &guid, &text, &isFromMe, &dateNano, &senderID, &chatID, &chatName)
						if err != nil {
							fmt.Fprintf(os.Stderr, "Scan error: %v\n", err)
							continue
						}

						// Convert Apple timestamp to ISO8601
						appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
						timestamp := appleEpoch.Add(time.Duration(dateNano) * time.Nanosecond)

						// Output JSON event
						event := map[string]interface{}{
							"event":      "message",
							"rowid":      rowID,
							"guid":       guid,
							"text":       text,
							"is_from_me": isFromMe,
							"timestamp":  timestamp.Format(time.RFC3339),
							"sender":     senderID,
							"chat_id":    chatID,
							"chat_name":  chatName,
						}

						data, _ := json.Marshal(event)
						fmt.Println(string(data))

						if rowID > lastRowID {
							lastRowID = rowID
						}
					}
					rows.Close()
				}
			}
		},
	}

	watchCmd.Flags().IntVar(&watchChatID, "chat-id", 0, "Filter to specific chat")
	watchCmd.Flags().Int64Var(&watchSinceRowID, "since-rowid", 0, "Start watching from this rowid (0 = current)")
	watchCmd.Flags().IntVar(&watchPollInterval, "poll", 250, "Poll interval in milliseconds")

	// Search command - semantic search using embeddings
	var searchChatID int
	var searchLimit int

	searchCmd := &cobra.Command{
		Use:   "search [query]",
		Short: "Semantic search across conversations using embeddings",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			query := strings.Join(args, " ")
			if query == "" {
				return printErrorJSON(fmt.Errorf("search query is required"))
			}

			cfg := config.Load()
			if cfg.GeminiAPIKey == "" {
				return printErrorJSON(fmt.Errorf("GEMINI_API_KEY is required for semantic search"))
			}

			// Open warehouse database
			warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer warehouseDB.Close()

			// Generate embedding for query
			geminiClient := gemini.NewClient(cfg.GeminiAPIKey)
			req := gemini.EmbedContentRequest{
				Model: cfg.EmbedModel,
				Content: gemini.Content{
					Parts: []gemini.Part{{Text: query}},
				},
			}

			resp, err := geminiClient.EmbedContent(&req)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to generate query embedding: %w", err))
			}

			if resp.Embedding == nil || len(resp.Embedding.Values) == 0 {
				return printErrorJSON(fmt.Errorf("empty embedding response"))
			}

			queryEmbedding := resp.Embedding.Values

			// Load conversation embeddings from database
			embQuery := `
				SELECT e.entity_id, e.embedding_blob, c.chat_id, ch.chat_name
				FROM embeddings e
				JOIN conversations c ON e.entity_id = c.id
				JOIN chats ch ON c.chat_id = ch.id
				WHERE e.entity_type = 'conversation'
			`
			embArgs := []interface{}{}

			if searchChatID > 0 {
				embQuery += ` AND c.chat_id = ?`
				embArgs = append(embArgs, searchChatID)
			}

			rows, err := warehouseDB.Query(embQuery, embArgs...)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to query embeddings: %w", err))
			}
			defer rows.Close()

			type searchResult struct {
				ConversationID int64   `json:"conversation_id"`
				ChatID         int64   `json:"chat_id"`
				ChatName       string  `json:"chat_name"`
				Score          float64 `json:"score"`
				Snippet        string  `json:"snippet,omitempty"`
			}

			var results []searchResult

			for rows.Next() {
				var convID, chatID int64
				var embeddingBlob []byte
				var chatName sql.NullString

				err := rows.Scan(&convID, &embeddingBlob, &chatID, &chatName)
				if err != nil {
					continue
				}

				// Convert blob to float64 slice
				convEmbedding, err := blobToFloat64Slice(embeddingBlob)
				if err != nil {
					continue
				}

				// Compute cosine similarity
				score := cosineSimilarity(queryEmbedding, convEmbedding)

				name := ""
				if chatName.Valid {
					name = chatName.String
				}

				results = append(results, searchResult{
					ConversationID: convID,
					ChatID:         chatID,
					ChatName:       name,
					Score:          score,
				})
			}

			// Sort by score descending
			for i := 0; i < len(results)-1; i++ {
				for j := i + 1; j < len(results); j++ {
					if results[j].Score > results[i].Score {
						results[i], results[j] = results[j], results[i]
					}
				}
			}

			// Limit results
			limit := searchLimit
			if limit <= 0 {
				limit = 10
			}
			if len(results) > limit {
				results = results[:limit]
			}

			// Load snippets for top results
			for i := range results {
				var snippet string
				err := warehouseDB.QueryRow(`
					SELECT GROUP_CONCAT(content, ' | ')
					FROM (
						SELECT content FROM messages 
						WHERE conversation_id = ? AND content IS NOT NULL AND content != ''
						ORDER BY timestamp DESC LIMIT 3
					)
				`, results[i].ConversationID).Scan(&snippet)
				if err == nil && snippet != "" {
					if len(snippet) > 200 {
						snippet = snippet[:200] + "..."
					}
					results[i].Snippet = snippet
				}
			}

			return printJSON(map[string]interface{}{
				"ok":      true,
				"query":   query,
				"count":   len(results),
				"results": results,
			})
		},
	}

	searchCmd.Flags().IntVar(&searchChatID, "chat-id", 0, "Limit search to specific chat")
	searchCmd.Flags().IntVar(&searchLimit, "limit", 10, "Maximum number of results")

	// Attachments command - list and export attachments
	var attChatID int
	var attMessageID int64
	var attType string
	var attLimit int

	attachmentsCmd := &cobra.Command{
		Use:   "attachments",
		Short: "List attachments (alias for 'messages attachments')",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer db.Close()

			query := `
				SELECT 
					a.id,
					a.message_id,
					a.file_name,
					a.mime_type,
					a.size,
					a.is_sticker,
					m.chat_id
				FROM attachments a
				JOIN messages m ON a.message_id = m.id
				WHERE 1=1
			`
			queryArgs := []interface{}{}

			if attChatID > 0 {
				query += ` AND m.chat_id = ?`
				queryArgs = append(queryArgs, attChatID)
			}

			if attMessageID > 0 {
				query += ` AND a.message_id = ?`
				queryArgs = append(queryArgs, attMessageID)
			}

			if attType != "" {
				query += ` AND a.mime_type LIKE ?`
				queryArgs = append(queryArgs, attType+"%")
			}

			query += ` ORDER BY a.id DESC`

			if attLimit > 0 {
				query += fmt.Sprintf(" LIMIT %d", attLimit)
			} else {
				query += " LIMIT 100"
			}

			rows, err := db.Query(query, queryArgs...)
			if err != nil {
				return printErrorJSON(fmt.Errorf("query failed: %w", err))
			}
			defer rows.Close()

			type attachmentRow struct {
				ID        int64  `json:"id"`
				MessageID int64  `json:"message_id"`
				FileName  string `json:"file_name"`
				MimeType  string `json:"mime_type"`
				Size      int64  `json:"size"`
				IsSticker bool   `json:"is_sticker"`
				ChatID    int64  `json:"chat_id"`
			}

			var attachments []attachmentRow
			for rows.Next() {
				var att attachmentRow
				var fileName, mimeType sql.NullString
				var size sql.NullInt64

				err := rows.Scan(
					&att.ID,
					&att.MessageID,
					&fileName,
					&mimeType,
					&size,
					&att.IsSticker,
					&att.ChatID,
				)
				if err != nil {
					return printErrorJSON(fmt.Errorf("scan failed: %w", err))
				}

				if fileName.Valid {
					att.FileName = fileName.String
				}
				if mimeType.Valid {
					att.MimeType = mimeType.String
				}
				if size.Valid {
					att.Size = size.Int64
				}

				attachments = append(attachments, att)
			}

			return printJSON(map[string]interface{}{
				"ok":          true,
				"count":       len(attachments),
				"attachments": attachments,
			})
		},
	}

	attachmentsCmd.Flags().IntVar(&attChatID, "chat-id", 0, "Filter by chat ID")
	attachmentsCmd.Flags().Int64Var(&attMessageID, "message-id", 0, "Filter by message ID")
	attachmentsCmd.Flags().StringVar(&attType, "type", "", "Filter by MIME type prefix (e.g., 'image', 'video')")
	attachmentsCmd.Flags().IntVar(&attLimit, "limit", 100, "Limit number of results")

	// Analyze command - queue analysis jobs
	var analyzeChatID int
	var analyzeConvoID int
	var analyzeContact string
	var analyzeAutoCompute bool

	analyzeCmd := &cobra.Command{
		Use:   "analyze",
		Short: "Queue conversation analysis jobs",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			// Open warehouse and queue databases
			warehouseDB, err := sql.Open("sqlite3", cfg.EveDBPath)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open warehouse database: %w", err))
			}
			defer warehouseDB.Close()

			queueDB, err := sql.Open("sqlite3", cfg.QueueDBPath)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open queue database: %w", err))
			}
			defer queueDB.Close()

			q := queue.New(queueDB)

			var conversationIDs []int

			if analyzeConvoID > 0 {
				// Single conversation
				conversationIDs = append(conversationIDs, analyzeConvoID)
			} else if analyzeChatID > 0 {
				// All conversations in chat
				rows, err := warehouseDB.Query(`SELECT id FROM conversations WHERE chat_id = ?`, analyzeChatID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to query conversations: %w", err))
				}
				for rows.Next() {
					var id int
					rows.Scan(&id)
					conversationIDs = append(conversationIDs, id)
				}
				rows.Close()
			} else if analyzeContact != "" {
				// Find contact and their chats
				var contactID int64
				err := warehouseDB.QueryRow(`
					SELECT id FROM contacts WHERE name LIKE ? LIMIT 1
				`, "%"+analyzeContact+"%").Scan(&contactID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("contact '%s' not found", analyzeContact))
				}

				rows, err := warehouseDB.Query(`
					SELECT DISTINCT c.id 
					FROM conversations c
					JOIN messages m ON m.conversation_id = c.id
					WHERE m.sender_id = ?
				`, contactID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("failed to query conversations: %w", err))
				}
				for rows.Next() {
					var id int
					rows.Scan(&id)
					conversationIDs = append(conversationIDs, id)
				}
				rows.Close()
			} else {
				return printErrorJSON(fmt.Errorf("--chat-id, --conversation-id, or --contact is required"))
			}

			// Queue analysis jobs
			enqueued := 0
			for _, convID := range conversationIDs {
				err := q.Enqueue(queue.EnqueueOptions{
					Type:     "analysis",
					Key:      fmt.Sprintf("analysis:conversation:%d:convo-all-v1", convID),
					Priority: 20,
					Payload: map[string]interface{}{
						"conversation_id": convID,
						"eve_prompt_id":   "convo-all-v1",
					},
					MaxAttempts: 3,
				})
				if err == nil {
					enqueued++
				}
			}

			result := map[string]interface{}{
				"ok":                  true,
				"conversations_found": len(conversationIDs),
				"jobs_enqueued":       enqueued,
			}

			// Auto-start daemon if requested and jobs were enqueued
			if analyzeAutoCompute && enqueued > 0 {
				pid, err := ensureDaemonRunning(cfg)
				if err != nil {
					result["daemon_error"] = err.Error()
					result["message"] = "Jobs queued but failed to start daemon. Run 'eve daemon start' manually."
				} else {
					result["daemon_pid"] = pid
					result["message"] = "Jobs queued and daemon is processing"
				}
			} else if enqueued > 0 {
				result["message"] = "Run 'eve compute run' or 'eve daemon start' to process queued jobs"
			} else {
				result["message"] = "No new jobs to queue"
			}

			return printJSON(result)
		},
	}

	analyzeCmd.Flags().IntVar(&analyzeChatID, "chat-id", 0, "Analyze all conversations in chat")
	analyzeCmd.Flags().IntVar(&analyzeConvoID, "conversation-id", 0, "Analyze single conversation")
	analyzeCmd.Flags().StringVar(&analyzeContact, "contact", "", "Analyze all conversations with contact")
	analyzeCmd.Flags().BoolVar(&analyzeAutoCompute, "auto-compute", true, "Automatically start compute daemon if not running")

	// Insights command - query analysis results
	var insightsChatID int
	var insightsContact string
	var insightsType string

	insightsCmd := &cobra.Command{
		Use:   "insights [type]",
		Short: "Query analysis results (topics, entities, emotions, humor)",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			db, err := sql.Open("sqlite3", cfg.EveDBPath+"?mode=ro")
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to open database: %w", err))
			}
			defer db.Close()

			// Determine insight type
			insightType := insightsType
			if len(args) > 0 {
				insightType = args[0]
			}

			// Resolve contact to chat_id if needed
			targetChatID := insightsChatID
			if insightsContact != "" && targetChatID == 0 {
				var contactID int64
				err := db.QueryRow(`SELECT id FROM contacts WHERE name LIKE ? LIMIT 1`, "%"+insightsContact+"%").Scan(&contactID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("contact '%s' not found", insightsContact))
				}
				err = db.QueryRow(`SELECT DISTINCT chat_id FROM messages WHERE sender_id = ? LIMIT 1`, contactID).Scan(&targetChatID)
				if err != nil {
					return printErrorJSON(fmt.Errorf("no messages found for contact"))
				}
			}

			switch insightType {
			case "topics":
				return queryInsights(db, "topics", "title", targetChatID)
			case "entities":
				return queryInsights(db, "entities", "title", targetChatID)
			case "emotions":
				return queryInsights(db, "emotions", "emotion_type", targetChatID)
			case "humor":
				return queryInsights(db, "humor_items", "snippet", targetChatID)
			default:
				// Summary view
				var topicsCount, entitiesCount, emotionsCount, humorCount int

				if targetChatID > 0 {
					db.QueryRow(`SELECT COUNT(*) FROM topics WHERE chat_id = ?`, targetChatID).Scan(&topicsCount)
					db.QueryRow(`SELECT COUNT(*) FROM entities WHERE chat_id = ?`, targetChatID).Scan(&entitiesCount)
					db.QueryRow(`SELECT COUNT(*) FROM emotions WHERE chat_id = ?`, targetChatID).Scan(&emotionsCount)
					db.QueryRow(`SELECT COUNT(*) FROM humor_items WHERE chat_id = ?`, targetChatID).Scan(&humorCount)
				} else {
					db.QueryRow(`SELECT COUNT(*) FROM topics`).Scan(&topicsCount)
					db.QueryRow(`SELECT COUNT(*) FROM entities`).Scan(&entitiesCount)
					db.QueryRow(`SELECT COUNT(*) FROM emotions`).Scan(&emotionsCount)
					db.QueryRow(`SELECT COUNT(*) FROM humor_items`).Scan(&humorCount)
				}

				return printJSON(map[string]interface{}{
					"ok":      true,
					"chat_id": targetChatID,
					"summary": map[string]int{
						"topics":      topicsCount,
						"entities":    entitiesCount,
						"emotions":    emotionsCount,
						"humor_items": humorCount,
					},
					"hint": "Use 'eve insights topics', 'eve insights entities', etc. for details",
				})
			}
		},
	}

	insightsCmd.Flags().IntVar(&insightsChatID, "chat-id", 0, "Filter by chat ID")
	insightsCmd.Flags().StringVar(&insightsContact, "contact", "", "Filter by contact name")
	insightsCmd.Flags().StringVar(&insightsType, "type", "", "Insight type: topics, entities, emotions, humor")

	// Daemon command - background compute processor
	daemonCmd := &cobra.Command{
		Use:   "daemon",
		Short: "Manage the background compute daemon",
		Long:  "The daemon automatically processes queued analysis and embedding jobs.",
	}

	daemonStartCmd := &cobra.Command{
		Use:   "start",
		Short: "Start the background compute daemon",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			// Check if already running
			if pid, running := isDaemonRunning(cfg); running {
				return printJSON(map[string]interface{}{
					"ok":      true,
					"status":  "already_running",
					"pid":     pid,
					"message": "Daemon is already running",
				})
			}

			// Start daemon in background
			exePath, err := os.Executable()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to get executable path: %w", err))
			}

			// Create log file
			logPath := cfg.AppDir + "/daemon.log"
			logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to create log file: %w", err))
			}

			// Start the compute process
			daemonProc := exec.Command(exePath, "compute", "run", "--workers", "10", "--timeout", "0")
			daemonProc.Stdout = logFile
			daemonProc.Stderr = logFile
			daemonProc.Env = os.Environ()

			if err := daemonProc.Start(); err != nil {
				logFile.Close()
				return printErrorJSON(fmt.Errorf("failed to start daemon: %w", err))
			}

			// Write PID file
			pidPath := cfg.AppDir + "/daemon.pid"
			if err := os.WriteFile(pidPath, []byte(fmt.Sprintf("%d", daemonProc.Process.Pid)), 0644); err != nil {
				return printErrorJSON(fmt.Errorf("failed to write PID file: %w", err))
			}

			// Detach
			logFile.Close()

			return printJSON(map[string]interface{}{
				"ok":       true,
				"status":   "started",
				"pid":      daemonProc.Process.Pid,
				"log_path": logPath,
				"message":  "Daemon started successfully",
			})
		},
	}

	daemonStopCmd := &cobra.Command{
		Use:   "stop",
		Short: "Stop the background compute daemon",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			pid, running := isDaemonRunning(cfg)
			if !running {
				return printJSON(map[string]interface{}{
					"ok":      true,
					"status":  "not_running",
					"message": "Daemon is not running",
				})
			}

			// Kill the process
			process, err := os.FindProcess(pid)
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to find process: %w", err))
			}

			if err := process.Signal(syscall.SIGTERM); err != nil {
				return printErrorJSON(fmt.Errorf("failed to stop daemon: %w", err))
			}

			// Remove PID file
			pidPath := cfg.AppDir + "/daemon.pid"
			os.Remove(pidPath)

			return printJSON(map[string]interface{}{
				"ok":      true,
				"status":  "stopped",
				"pid":     pid,
				"message": "Daemon stopped successfully",
			})
		},
	}

	daemonStatusCmd := &cobra.Command{
		Use:   "status",
		Short: "Check daemon status",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			pid, running := isDaemonRunning(cfg)

			if running {
				// Get queue stats too
				queueDB, err := sql.Open("sqlite3", cfg.QueueDBPath)
				var queueStats map[string]interface{}
				if err == nil {
					q := queue.New(queueDB)
					stats, statsErr := q.GetStats()
					queueDB.Close()
					if statsErr == nil {
						queueStats = map[string]interface{}{
							"pending":   stats.Pending,
							"leased":    stats.Leased,
							"succeeded": stats.Succeeded,
							"failed":    stats.Failed,
						}
					}
				}

				return printJSON(map[string]interface{}{
					"ok":      true,
					"running": true,
					"pid":     pid,
					"queue":   queueStats,
				})
			}

			return printJSON(map[string]interface{}{
				"ok":      true,
				"running": false,
				"message": "Daemon is not running. Start with 'eve daemon start'",
			})
		},
	}

	// Launchd install/uninstall: robust "always running" daemon for macOS
	var daemonInstallWorkers int
	var daemonInstallStoreKey bool
	var daemonInstallPrintOnly bool

	daemonInstallCmd := &cobra.Command{
		Use:   "install",
		Short: "Install a macOS LaunchAgent to keep compute running",
		Long: "Installs a user LaunchAgent (launchd) that runs:\n" +
			"  eve compute run --workers <N> --timeout 0\n\n" +
			"This is the most robust way to ensure compute automatically runs when jobs exist.",
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg := config.Load()

			if runtime.GOOS != "darwin" {
				return printErrorJSON(fmt.Errorf("launchd install is only supported on macOS"))
			}

			exePath, err := os.Executable()
			if err != nil {
				return printErrorJSON(fmt.Errorf("failed to get executable path: %w", err))
			}

			if daemonInstallWorkers <= 0 {
				daemonInstallWorkers = 10
			}

			plistPath, err := launchAgentPlistPath()
			if err != nil {
				return printErrorJSON(err)
			}

			// Optionally persist GEMINI_API_KEY to config.json so launchd doesn't depend on shell env.
			storedKey := false
			if daemonInstallStoreKey {
				storedKey, err = ensureConfigHasGeminiAPIKey(cfg)
				if err != nil {
					return printErrorJSON(err)
				}
			}

			plist := renderLaunchAgentPlist(launchAgentLabel(), exePath, daemonInstallWorkers, cfg.AppDir)

			if daemonInstallPrintOnly {
				return printJSON(map[string]interface{}{
					"ok":          true,
					"mode":        "print-only",
					"plist_path":  plistPath,
					"label":       launchAgentLabel(),
					"workers":     daemonInstallWorkers,
					"stored_key":  storedKey,
					"plist":       plist,
					"next_steps":  "Run without --print-only to install and start it",
					"config_path": cfg.ConfigPath,
					"daemon_logs": filepath.Join(cfg.AppDir, "launchd.log"),
					"daemon_errs": filepath.Join(cfg.AppDir, "launchd.err.log"),
				})
			}

			if err := os.MkdirAll(filepath.Dir(plistPath), 0755); err != nil {
				return printErrorJSON(fmt.Errorf("failed to create LaunchAgents dir: %w", err))
			}
			if err := os.WriteFile(plistPath, []byte(plist), 0644); err != nil {
				return printErrorJSON(fmt.Errorf("failed to write plist: %w", err))
			}

			uid := os.Getuid()
			if err := launchctlBootstrap(uid, plistPath); err != nil {
				return printErrorJSON(fmt.Errorf("failed to load LaunchAgent: %w", err))
			}

			return printJSON(map[string]interface{}{
				"ok":          true,
				"status":      "installed",
				"label":       launchAgentLabel(),
				"plist_path":  plistPath,
				"workers":     daemonInstallWorkers,
				"stored_key":  storedKey,
				"config_path": cfg.ConfigPath,
				"logs": map[string]string{
					"stdout": filepath.Join(cfg.AppDir, "launchd.log"),
					"stderr": filepath.Join(cfg.AppDir, "launchd.err.log"),
				},
			})
		},
	}
	daemonInstallCmd.Flags().IntVar(&daemonInstallWorkers, "workers", 10, "Compute workers to run under launchd")
	daemonInstallCmd.Flags().BoolVar(&daemonInstallStoreKey, "store-key", false, "Persist GEMINI_API_KEY into Eve config.json so launchd can run without shell env")
	daemonInstallCmd.Flags().BoolVar(&daemonInstallPrintOnly, "print-only", false, "Print the LaunchAgent plist instead of installing it")

	daemonUninstallCmd := &cobra.Command{
		Use:   "uninstall",
		Short: "Uninstall the macOS LaunchAgent",
		RunE: func(cmd *cobra.Command, args []string) error {
			if runtime.GOOS != "darwin" {
				return printErrorJSON(fmt.Errorf("launchd uninstall is only supported on macOS"))
			}
			plistPath, err := launchAgentPlistPath()
			if err != nil {
				return printErrorJSON(err)
			}

			uid := os.Getuid()
			_ = launchctlBootout(uid, plistPath) // ignore error; could already be unloaded
			_ = os.Remove(plistPath)

			return printJSON(map[string]interface{}{
				"ok":         true,
				"status":     "uninstalled",
				"label":      launchAgentLabel(),
				"plist_path": plistPath,
			})
		},
	}

	daemonCmd.AddCommand(daemonStartCmd)
	daemonCmd.AddCommand(daemonStopCmd)
	daemonCmd.AddCommand(daemonStatusCmd)
	daemonCmd.AddCommand(daemonInstallCmd)
	daemonCmd.AddCommand(daemonUninstallCmd)

	rootCmd.AddCommand(helpCmd)
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
	rootCmd.AddCommand(chatsCmd)
	rootCmd.AddCommand(contactsCmd)
	rootCmd.AddCommand(messagesCmd)
	rootCmd.AddCommand(historyCmd)
	rootCmd.AddCommand(sendCmd)
	rootCmd.AddCommand(watchCmd)
	rootCmd.AddCommand(searchCmd)
	rootCmd.AddCommand(attachmentsCmd)
	rootCmd.AddCommand(analyzeCmd)
	rootCmd.AddCommand(insightsCmd)
	rootCmd.AddCommand(daemonCmd)

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

// escapeAppleScript escapes a string for use in AppleScript
func escapeAppleScript(s string) string {
	// Escape backslashes first, then quotes
	s = strings.ReplaceAll(s, "\\", "\\\\")
	s = strings.ReplaceAll(s, "\"", "\\\"")
	return s
}

// parseDate parses various date formats: YYYY-MM-DD, ISO8601, or relative
func parseDate(s string) (time.Time, error) {
	// Try ISO8601 first
	if t, err := time.Parse(time.RFC3339, s); err == nil {
		return t, nil
	}
	// Try date only (YYYY-MM-DD)
	if t, err := time.Parse("2006-01-02", s); err == nil {
		return t, nil
	}
	// Try datetime without timezone
	if t, err := time.Parse("2006-01-02T15:04:05", s); err == nil {
		return t, nil
	}
	return time.Time{}, fmt.Errorf("unrecognized date format: %s (use YYYY-MM-DD or ISO8601)", s)
}

// blobToFloat64Slice converts a byte slice back to float64 slice
func blobToFloat64Slice(blob []byte) ([]float64, error) {
	if len(blob)%8 != 0 {
		return nil, fmt.Errorf("invalid blob length: %d (must be multiple of 8)", len(blob))
	}

	values := make([]float64, len(blob)/8)
	for i := 0; i < len(values); i++ {
		bits := uint64(blob[i*8]) | uint64(blob[i*8+1])<<8 | uint64(blob[i*8+2])<<16 | uint64(blob[i*8+3])<<24 |
			uint64(blob[i*8+4])<<32 | uint64(blob[i*8+5])<<40 | uint64(blob[i*8+6])<<48 | uint64(blob[i*8+7])<<56
		values[i] = float64frombits(bits)
	}
	return values, nil
}

// float64frombits converts uint64 bits to float64
func float64frombits(b uint64) float64 {
	return *(*float64)(unsafe.Pointer(&b))
}

// cosineSimilarity computes cosine similarity between two vectors
func cosineSimilarity(a, b []float64) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0
	}

	var dotProduct, normA, normB float64
	for i := range a {
		dotProduct += a[i] * b[i]
		normA += a[i] * a[i]
		normB += b[i] * b[i]
	}

	if normA == 0 || normB == 0 {
		return 0
	}

	return dotProduct / (sqrt(normA) * sqrt(normB))
}

// sqrt computes square root (avoiding math import for simplicity)
func sqrt(x float64) float64 {
	if x < 0 {
		return 0
	}
	z := x / 2
	for i := 0; i < 10; i++ {
		z = z - (z*z-x)/(2*z)
	}
	return z
}

// queryInsights queries a specific insight table and returns results
func queryInsights(db *sql.DB, table, column string, chatID int) error {
	query := fmt.Sprintf(`
		SELECT %s, COUNT(*) as count
		FROM %s
	`, column, table)

	queryArgs := []interface{}{}
	if chatID > 0 {
		query += ` WHERE chat_id = ?`
		queryArgs = append(queryArgs, chatID)
	}

	query += fmt.Sprintf(` GROUP BY %s ORDER BY count DESC LIMIT 50`, column)

	rows, err := db.Query(query, queryArgs...)
	if err != nil {
		return printErrorJSON(fmt.Errorf("query failed: %w", err))
	}
	defer rows.Close()

	type insightRow struct {
		Value string `json:"value"`
		Count int    `json:"count"`
	}

	var insights []insightRow
	for rows.Next() {
		var insight insightRow
		var value sql.NullString
		err := rows.Scan(&value, &insight.Count)
		if err != nil {
			continue
		}
		if value.Valid {
			insight.Value = value.String
		}
		insights = append(insights, insight)
	}

	return printJSON(map[string]interface{}{
		"ok":       true,
		"type":     table,
		"chat_id":  chatID,
		"count":    len(insights),
		"insights": insights,
	})
}

// isDaemonRunning checks if the daemon is running by reading PID file and checking process
func isDaemonRunning(cfg *config.Config) (int, bool) {
	pidPath := cfg.AppDir + "/daemon.pid"
	data, err := os.ReadFile(pidPath)
	if err != nil {
		return 0, false
	}

	var pid int
	if _, err := fmt.Sscanf(string(data), "%d", &pid); err != nil {
		return 0, false
	}

	// Check if process exists
	process, err := os.FindProcess(pid)
	if err != nil {
		return 0, false
	}

	// On Unix, FindProcess always succeeds, so we need to send signal 0 to check
	err = process.Signal(syscall.Signal(0))
	if err != nil {
		// Process doesn't exist, clean up stale PID file
		os.Remove(pidPath)
		return 0, false
	}

	return pid, true
}

// ensureDaemonRunning starts the daemon if it's not already running
func ensureDaemonRunning(cfg *config.Config) (int, error) {
	if pid, running := isDaemonRunning(cfg); running {
		return pid, nil
	}

	// Start daemon
	exePath, err := os.Executable()
	if err != nil {
		return 0, fmt.Errorf("failed to get executable path: %w", err)
	}

	// Create log file
	logPath := cfg.AppDir + "/daemon.log"
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return 0, fmt.Errorf("failed to create log file: %w", err)
	}

	// Start the compute process
	daemonProc := exec.Command(exePath, "compute", "run", "--workers", "10", "--timeout", "0")
	daemonProc.Stdout = logFile
	daemonProc.Stderr = logFile
	daemonProc.Env = os.Environ()

	if err := daemonProc.Start(); err != nil {
		logFile.Close()
		return 0, fmt.Errorf("failed to start daemon: %w", err)
	}

	// Write PID file
	pidPath := cfg.AppDir + "/daemon.pid"
	if err := os.WriteFile(pidPath, []byte(fmt.Sprintf("%d", daemonProc.Process.Pid)), 0644); err != nil {
		return 0, fmt.Errorf("failed to write PID file: %w", err)
	}

	logFile.Close()
	return daemonProc.Process.Pid, nil
}

func launchAgentLabel() string {
	return "com.napageneral.eve"
}

func launchAgentPlistPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("failed to resolve home dir: %w", err)
	}
	return filepath.Join(home, "Library", "LaunchAgents", launchAgentLabel()+".plist"), nil
}

func renderLaunchAgentPlist(label, exePath string, workers int, appDir string) string {
	stdoutPath := filepath.Join(appDir, "launchd.log")
	stderrPath := filepath.Join(appDir, "launchd.err.log")
	return fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>%s</string>

  <key>ProgramArguments</key>
  <array>
    <string>%s</string>
    <string>compute</string>
    <string>run</string>
    <string>--workers</string>
    <string>%d</string>
    <string>--timeout</string>
    <string>0</string>
  </array>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>%s</string>
  <key>StandardErrorPath</key>
  <string>%s</string>
</dict>
</plist>
`, label, exePath, workers, stdoutPath, stderrPath)
}

func launchctlBootstrap(uid int, plistPath string) error {
	// Prefer modern launchctl (bootstrap gui/<uid>) for per-user LaunchAgents.
	domain := fmt.Sprintf("gui/%d", uid)
	cmd := exec.Command("launchctl", "bootstrap", domain, plistPath)
	out, err := cmd.CombinedOutput()
	if err == nil {
		return nil
	}
	// Fallback to legacy load -w
	cmd2 := exec.Command("launchctl", "load", "-w", plistPath)
	out2, err2 := cmd2.CombinedOutput()
	if err2 != nil {
		return fmt.Errorf("bootstrap failed: %v (%s); load failed: %v (%s)", err, strings.TrimSpace(string(out)), err2, strings.TrimSpace(string(out2)))
	}
	return nil
}

func launchctlBootout(uid int, plistPath string) error {
	domain := fmt.Sprintf("gui/%d", uid)
	cmd := exec.Command("launchctl", "bootout", domain, plistPath)
	out, err := cmd.CombinedOutput()
	if err == nil {
		return nil
	}
	// Fallback to legacy unload -w
	cmd2 := exec.Command("launchctl", "unload", "-w", plistPath)
	out2, err2 := cmd2.CombinedOutput()
	if err2 != nil {
		return fmt.Errorf("bootout failed: %v (%s); unload failed: %v (%s)", err, strings.TrimSpace(string(out)), err2, strings.TrimSpace(string(out2)))
	}
	return nil
}

func ensureConfigHasGeminiAPIKey(cfg *config.Config) (bool, error) {
	// If GEMINI_API_KEY isn't set, we can't store it.
	key := os.Getenv("GEMINI_API_KEY")
	if key == "" {
		return false, fmt.Errorf("GEMINI_API_KEY is not set; cannot --store-key")
	}

	// Read existing config.json (if any) as a generic object.
	var obj map[string]interface{}
	if data, err := os.ReadFile(cfg.ConfigPath); err == nil {
		_ = json.Unmarshal(data, &obj)
	}
	if obj == nil {
		obj = map[string]interface{}{}
	}

	// If already set, don't overwrite.
	if existing, ok := obj["gemini_api_key"].(string); ok && existing != "" {
		return false, nil
	}

	obj["gemini_api_key"] = key

	if err := os.MkdirAll(filepath.Dir(cfg.ConfigPath), 0755); err != nil {
		return false, fmt.Errorf("failed to create app dir: %w", err)
	}
	data, err := json.MarshalIndent(obj, "", "  ")
	if err != nil {
		return false, fmt.Errorf("failed to marshal config: %w", err)
	}
	if err := os.WriteFile(cfg.ConfigPath, data, 0600); err != nil {
		return false, fmt.Errorf("failed to write config: %w", err)
	}
	return true, nil
}
