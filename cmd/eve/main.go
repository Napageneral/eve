package main

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"

	"github.com/spf13/cobra"
	"github.com/tylerchilds/eve/internal/contextengine"
	"github.com/tylerchilds/eve/internal/db"
	"github.com/tylerchilds/eve/internal/encoding"
	"github.com/tylerchilds/eve/internal/resources"
)

var (
	version   = "dev"
	commit    = "none"
	buildDate = "unknown"

	// Global flag for resources directory override
	resourcesDir string
)

func main() {
	rootCmd := &cobra.Command{
		Use:   "eve",
		Short: "Eve - CLI-first personal communications database",
		Long: `Eve ingests iMessage + contacts into a local SQLite database (eve.db),
then optionally runs high-throughput conversation analysis + embeddings.`,
	}

	// Add global --resources-dir flag with env var fallback
	rootCmd.PersistentFlags().StringVar(&resourcesDir, "resources-dir", os.Getenv("EVE_RESOURCES_DIR"), "Override directory for prompts and packs (default: embedded resources)")

	// eve version
	versionCmd := &cobra.Command{
		Use:   "version",
		Short: "Print version information",
		Run: func(cmd *cobra.Command, args []string) {
			info := map[string]string{
				"version":    version,
				"commit":     commit,
				"build_date": buildDate,
				"go_version": runtime.Version(),
				"os":         runtime.GOOS,
				"arch":       runtime.GOARCH,
			}
			out, _ := json.MarshalIndent(info, "", "  ")
			fmt.Println(string(out))
		},
	}

	// eve init (placeholder)
	initCmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize Eve: run ETL to populate eve.db",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println(`{"status": "not_implemented", "message": "eve init coming soon"}`)
			return nil
		},
	}

	// eve db (subcommand group)
	dbCmd := &cobra.Command{
		Use:   "db",
		Short: "Database operations",
	}

	// eve db query
	var sqlQuery string
	var dbSpec string
	var allowWrite bool
	dbQueryCmd := &cobra.Command{
		Use:   "query",
		Short: "Execute SQL against eve.db (read-only by default)",
		RunE: func(cmd *cobra.Command, args []string) error {
			if sqlQuery == "" {
				return fmt.Errorf("--sql flag is required")
			}

			result := db.Execute(db.QueryOptions{
				SQL:        sqlQuery,
				DBSpec:     dbSpec,
				AllowWrite: allowWrite,
			})

			out, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(out))
			return nil
		},
	}
	dbQueryCmd.Flags().StringVar(&sqlQuery, "sql", "", "SQL query to execute (required)")
	dbQueryCmd.Flags().StringVar(&dbSpec, "db", "warehouse", "Database to query: warehouse, queue, or path:/abs/file.db")
	dbQueryCmd.Flags().BoolVar(&allowWrite, "write", false, "Allow write operations (INSERT/UPDATE/DELETE/ALTER)")
	dbCmd.AddCommand(dbQueryCmd)

	// eve compute (subcommand group)
	computeCmd := &cobra.Command{
		Use:   "compute",
		Short: "Compute plane operations (analysis, embeddings)",
	}

	// eve compute run (placeholder)
	computeRunCmd := &cobra.Command{
		Use:   "run",
		Short: "Run the compute engine to process queued jobs",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Println(`{"status": "not_implemented", "message": "eve compute run coming soon"}`)
			return nil
		},
	}
	computeCmd.AddCommand(computeRunCmd)

	// eve prompt (subcommand group)
	promptCmd := &cobra.Command{
		Use:   "prompt",
		Short: "Prompt resource operations",
	}

	// eve prompt list
	promptListCmd := &cobra.Command{
		Use:   "list",
		Short: "List available prompts",
		RunE: func(cmd *cobra.Command, args []string) error {
			loader := resources.NewLoader(resourcesDir)
			prompts, err := loader.ListPrompts()
			if err != nil {
				return fmt.Errorf("failed to list prompts: %w", err)
			}

			result := map[string]interface{}{
				"count":   len(prompts),
				"prompts": prompts,
			}
			out, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(out))
			return nil
		},
	}

	// eve prompt show
	promptShowCmd := &cobra.Command{
		Use:   "show [id]",
		Short: "Show a specific prompt by ID",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			loader := resources.NewLoader(resourcesDir)
			prompt, err := loader.LoadPrompt(args[0])
			if err != nil {
				return fmt.Errorf("failed to load prompt: %w", err)
			}

			out, _ := json.MarshalIndent(prompt, "", "  ")
			fmt.Println(string(out))
			return nil
		},
	}

	promptCmd.AddCommand(promptListCmd)
	promptCmd.AddCommand(promptShowCmd)

	// eve pack (subcommand group)
	packCmd := &cobra.Command{
		Use:   "pack",
		Short: "Context pack operations",
	}

	// eve pack list
	packListCmd := &cobra.Command{
		Use:   "list",
		Short: "List available context packs",
		RunE: func(cmd *cobra.Command, args []string) error {
			loader := resources.NewLoader(resourcesDir)
			packs, err := loader.ListPacks()
			if err != nil {
				return fmt.Errorf("failed to list packs: %w", err)
			}

			result := map[string]interface{}{
				"count": len(packs),
				"packs": packs,
			}
			out, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(out))
			return nil
		},
	}

	// eve pack show
	packShowCmd := &cobra.Command{
		Use:   "show [id]",
		Short: "Show a specific pack by ID",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			loader := resources.NewLoader(resourcesDir)
			pack, err := loader.LoadPack(args[0])
			if err != nil {
				return fmt.Errorf("failed to load pack: %w", err)
			}

			out, _ := json.MarshalIndent(pack, "", "  ")
			fmt.Println(string(out))
			return nil
		},
	}

	packCmd.AddCommand(packListCmd)
	packCmd.AddCommand(packShowCmd)

	// eve encode (subcommand group)
	encodeCmd := &cobra.Command{
		Use:   "encode",
		Short: "Encoding operations",
	}

	// eve encode conversation
	var encodeStdout bool
	var encodeOutput string
	var encodeDBPath string
	encodeConvoCmd := &cobra.Command{
		Use:   "conversation --conversation-id <id>",
		Short: "Encode a conversation into LLM-ready text",
		RunE: func(cmd *cobra.Command, args []string) error {
			conversationIDStr, _ := cmd.Flags().GetString("conversation-id")
			if conversationIDStr == "" {
				return fmt.Errorf("--conversation-id flag is required")
			}

			var conversationID int64
			if _, err := fmt.Sscanf(conversationIDStr, "%d", &conversationID); err != nil {
				return fmt.Errorf("invalid conversation ID: %s", conversationIDStr)
			}

			// Determine database path
			dbPath := encodeDBPath
			if dbPath == "" {
				home, _ := os.UserHomeDir()
				dbPath = fmt.Sprintf("%s/.config/eve/eve.db", home)
			}

			var result encoding.EncodeResult

			if encodeStdout {
				// Print transcript to stdout
				result = encoding.EncodeConversationToString(dbPath, conversationID)
				if !result.Success {
					return fmt.Errorf(result.Error)
				}
				fmt.Println(result.EncodedText)
			} else {
				// Write to file
				outputPath := encodeOutput
				if outputPath == "" {
					outputPath = encoding.GetDefaultOutputPath(conversationID)
				}

				result = encoding.EncodeConversationToFile(dbPath, conversationID, outputPath)
				if !result.Success {
					return fmt.Errorf(result.Error)
				}

				// Output metadata as JSON (no message text)
				out, _ := json.MarshalIndent(result, "", "  ")
				fmt.Println(string(out))
			}

			return nil
		},
	}
	encodeConvoCmd.Flags().String("conversation-id", "", "Conversation ID to encode (required)")
	encodeConvoCmd.Flags().BoolVar(&encodeStdout, "stdout", false, "Print transcript to stdout instead of writing to file")
	encodeConvoCmd.Flags().StringVar(&encodeOutput, "output", "", "Output file path (default: ~/.config/eve/tmp/conversation_<id>.txt)")
	encodeConvoCmd.Flags().StringVar(&encodeDBPath, "db", "", "Database path (default: ~/.config/eve/eve.db)")
	encodeCmd.AddCommand(encodeConvoCmd)

	// eve context (subcommand group)
	contextCmd := &cobra.Command{
		Use:   "context",
		Short: "Context engine operations",
	}

	// eve context compile
	var contextPromptID string
	var contextPackID string
	var contextSourceChat int
	var contextVarsJSON string
	var contextBudget int
	contextCompileCmd := &cobra.Command{
		Use:   "compile --prompt <id>",
		Short: "Compile a context pack into hidden parts + visible prompt",
		RunE: func(cmd *cobra.Command, args []string) error {
			if contextPromptID == "" {
				return fmt.Errorf("--prompt flag is required")
			}

			// Parse vars from JSON if provided
			vars := make(map[string]interface{})
			if contextVarsJSON != "" {
				if err := json.Unmarshal([]byte(contextVarsJSON), &vars); err != nil {
					return fmt.Errorf("failed to parse --vars JSON: %w", err)
				}
			}

			// Determine database path
			home, _ := os.UserHomeDir()
			dbPath := fmt.Sprintf("%s/.config/eve/eve.db", home)

			// Create engine
			loader := resources.NewLoader(resourcesDir)
			engine := contextengine.NewEngine(loader, dbPath)

			// Build request
			request := contextengine.ExecuteRequest{
				PromptID:       contextPromptID,
				SourceChat:     contextSourceChat,
				Vars:           vars,
				OverridePackID: contextPackID,
				BudgetTokens:   contextBudget,
			}

			// Execute
			result, err := engine.Execute(request)
			if err != nil {
				return fmt.Errorf("compilation failed: %w", err)
			}

			// Output JSON (result is either ExecuteResult or ExecuteError)
			out, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(out))
			return nil
		},
	}
	contextCompileCmd.Flags().StringVar(&contextPromptID, "prompt", "", "Prompt ID to compile (required)")
	contextCompileCmd.Flags().StringVar(&contextPackID, "pack", "", "Override pack ID (default: use prompt's default_pack)")
	contextCompileCmd.Flags().IntVar(&contextSourceChat, "source-chat", 0, "Source chat ID for context")
	contextCompileCmd.Flags().StringVar(&contextVarsJSON, "vars", "", "Variables as JSON object (e.g. '{\"name\":\"value\"}')")
	contextCompileCmd.Flags().IntVar(&contextBudget, "budget", 0, "Token budget (default: 300000)")
	contextCmd.AddCommand(contextCompileCmd)

	rootCmd.AddCommand(versionCmd)
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(dbCmd)
	rootCmd.AddCommand(computeCmd)
	rootCmd.AddCommand(promptCmd)
	rootCmd.AddCommand(packCmd)
	rootCmd.AddCommand(encodeCmd)
	rootCmd.AddCommand(contextCmd)

	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}
