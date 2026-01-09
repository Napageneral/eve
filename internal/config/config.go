package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
)

// Config holds the Eve application configuration
type Config struct {
	AppDir        string
	EveDBPath     string
	QueueDBPath   string
	ConfigPath    string
	GeminiAPIKey  string
	AnalysisModel string
	EmbedModel    string
}

// FileConfig represents the JSON structure of config.json
type FileConfig struct {
	GeminiAPIKey  string `json:"gemini_api_key,omitempty"`
	AnalysisModel string `json:"analysis_model,omitempty"`
	EmbedModel    string `json:"embed_model,omitempty"`
}

// GetAppDir returns the Eve application directory for the current OS
func GetAppDir() string {
	switch runtime.GOOS {
	case "darwin":
		home, _ := os.UserHomeDir()
		return filepath.Join(home, "Library", "Application Support", "Eve")
	case "linux":
		home, _ := os.UserHomeDir()
		return filepath.Join(home, ".local", "share", "eve")
	case "windows":
		appData := os.Getenv("APPDATA")
		if appData == "" {
			home, _ := os.UserHomeDir()
			appData = filepath.Join(home, "AppData", "Roaming")
		}
		return filepath.Join(appData, "Eve")
	default:
		home, _ := os.UserHomeDir()
		return filepath.Join(home, ".eve")
	}
}

// Load returns a Config instance with env overrides and defaults
// Precedence: env vars > config.json > defaults
func Load() *Config {
	appDir := GetAppDir()
	configPath := filepath.Join(appDir, "config.json")

	// Start with defaults
	// NOTE: Model IDs must match Google Gemini API v1beta ListModels output.
	// You can always override these with:
	// - EVE_GEMINI_ANALYSIS_MODEL
	// - EVE_GEMINI_EMBED_MODEL
	analysisModel := "gemini-3-flash-preview"
	embedModel := "gemini-embedding-001"
	geminiAPIKey := ""

	// Load from config.json if it exists
	fileCfg := loadFileConfig(configPath)
	if fileCfg != nil {
		if fileCfg.AnalysisModel != "" {
			analysisModel = fileCfg.AnalysisModel
		}
		if fileCfg.EmbedModel != "" {
			embedModel = fileCfg.EmbedModel
		}
		if fileCfg.GeminiAPIKey != "" {
			geminiAPIKey = fileCfg.GeminiAPIKey
		}
	}

	// Env vars override everything
	if envKey := os.Getenv("GEMINI_API_KEY"); envKey != "" {
		geminiAPIKey = envKey
	}
	if envModel := os.Getenv("EVE_GEMINI_ANALYSIS_MODEL"); envModel != "" {
		analysisModel = envModel
	}
	if envEmbed := os.Getenv("EVE_GEMINI_EMBED_MODEL"); envEmbed != "" {
		embedModel = envEmbed
	}

	cfg := &Config{
		AppDir:        appDir,
		EveDBPath:     filepath.Join(appDir, "eve.db"),
		QueueDBPath:   filepath.Join(appDir, "eve-queue.db"),
		ConfigPath:    configPath,
		GeminiAPIKey:  geminiAPIKey,
		AnalysisModel: analysisModel,
		EmbedModel:    embedModel,
	}

	return cfg
}

// loadFileConfig reads and parses config.json if it exists
func loadFileConfig(path string) *FileConfig {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}

	var fc FileConfig
	if err := json.Unmarshal(data, &fc); err != nil {
		return nil
	}

	return &fc
}
