package main

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"

	"github.com/spf13/cobra"
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

	// eve db query (placeholder)
	var sqlQuery string
	var queryLimit int
	dbQueryCmd := &cobra.Command{
		Use:   "query",
		Short: "Execute read-only SQL against eve.db",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Printf(`{"status": "not_implemented", "sql": %q}`, sqlQuery)
			fmt.Println()
			return nil
		},
	}
	dbQueryCmd.Flags().StringVar(&sqlQuery, "sql", "", "SQL query to execute")
	dbQueryCmd.Flags().IntVar(&queryLimit, "limit", 100, "Max rows to return")
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

	// eve encode conversation (placeholder)
	encodeConvoCmd := &cobra.Command{
		Use:   "conversation [conversation_id]",
		Short: "Encode a conversation into LLM-ready text",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Printf(`{"status": "not_implemented", "conversation_id": %q}`, args[0])
			fmt.Println()
			return nil
		},
	}
	encodeCmd.AddCommand(encodeConvoCmd)

	rootCmd.AddCommand(versionCmd)
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(dbCmd)
	rootCmd.AddCommand(computeCmd)
	rootCmd.AddCommand(promptCmd)
	rootCmd.AddCommand(packCmd)
	rootCmd.AddCommand(encodeCmd)

	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}
