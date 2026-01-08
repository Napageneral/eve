package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/spf13/cobra"
	"github.com/tylerchilds/eve/internal/config"
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

	rootCmd.AddCommand(versionCmd)
	rootCmd.AddCommand(pathsCmd)

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
