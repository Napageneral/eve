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
				"dry_run":        syncDryRun,
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

			// Update watermark if not dry-run and we found messages
			if !syncDryRun && messageCount.MaxRowID > 0 {
				if err := etl.SetWatermark(warehouseDB, "chatdb", "message_rowid", &messageCount.MaxRowID, nil); err != nil {
					return printErrorJSON(fmt.Errorf("failed to update watermark: %w", err))
				}
				output["watermark_updated"] = true
			} else {
				output["watermark_updated"] = false
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

			// Create engine with config
			engineCfg := engine.DefaultConfig()
			if runWorkerCount > 0 {
				engineCfg.WorkerCount = runWorkerCount
			}

			eng := engine.New(q, engineCfg)

			// Register fake job handler for testing
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

			// Run engine
			startTime := time.Now()
			stats, err := eng.Run(ctx)
			duration := time.Since(startTime)

			if err != nil {
				return printErrorJSON(fmt.Errorf("compute run failed: %w", err))
			}

			// Output stats
			output := map[string]interface{}{
				"ok":        true,
				"succeeded": stats.Succeeded,
				"failed":    stats.Failed,
				"skipped":   stats.Skipped,
				"duration":  duration.Seconds(),
			}
			return printJSON(output)
		},
	}

	computeRunCmd.Flags().IntVar(&runWorkerCount, "workers", 0, "Number of concurrent workers (default: 10)")
	computeRunCmd.Flags().IntVar(&runTimeout, "timeout", 0, "Timeout in seconds (0 = no timeout)")

	computeCmd.AddCommand(computeStatusCmd)
	computeCmd.AddCommand(computeRunCmd)

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
