package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
)

// Config holds the Eve application configuration
type Config struct {
	AppDir        string
	EveDBPath     string
	QueueDBPath   string
	ConfigPath    string
	GeminiAPIKey  string
	AnalysisModel string
	// AnalysisThinkingLevel configures Gemini 3 "thinking_level" (e.g. minimal/low/high).
	// Leave empty to use provider defaults.
	AnalysisThinkingLevel string
	// AnalysisMaxMessages limits how many messages are included in a conversation analysis prompt
	// (keeps the most recent N). 0 means no limit.
	AnalysisMaxMessages int
	// AnalysisMaxOutputTokens caps analysis output length (0 means provider default).
	AnalysisMaxOutputTokens int
	EmbedModel    string
}

// FileConfig represents the JSON structure of config.json
type FileConfig struct {
	GeminiAPIKey  string `json:"gemini_api_key,omitempty"`
	AnalysisModel string `json:"analysis_model,omitempty"`
	AnalysisThinkingLevel string `json:"analysis_thinking_level,omitempty"`
	AnalysisMaxMessages int `json:"analysis_max_messages,omitempty"`
	AnalysisMaxOutputTokens int `json:"analysis_max_output_tokens,omitempty"`
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
	// - EVE_GEMINI_ANALYSIS_THINKING_LEVEL
	// - EVE_GEMINI_EMBED_MODEL
	analysisModel := "gemini-3-flash-preview"
	analysisThinkingLevel := ""
	analysisMaxMessages := 40
	analysisMaxOutputTokens := 256
	embedModel := "gemini-embedding-001"
	geminiAPIKey := ""

	// Load from config.json if it exists
	fileCfg := loadFileConfig(configPath)
	if fileCfg != nil {
		if fileCfg.AnalysisModel != "" {
			analysisModel = fileCfg.AnalysisModel
		}
		if fileCfg.AnalysisThinkingLevel != "" {
			analysisThinkingLevel = fileCfg.AnalysisThinkingLevel
		}
		if fileCfg.AnalysisMaxMessages > 0 {
			analysisMaxMessages = fileCfg.AnalysisMaxMessages
		}
		if fileCfg.AnalysisMaxOutputTokens > 0 {
			analysisMaxOutputTokens = fileCfg.AnalysisMaxOutputTokens
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
	if envThinking := os.Getenv("EVE_GEMINI_ANALYSIS_THINKING_LEVEL"); envThinking != "" {
		analysisThinkingLevel = envThinking
	}
	if envMaxMsgs := os.Getenv("EVE_ANALYSIS_MAX_MESSAGES"); envMaxMsgs != "" {
		if v, err := strconv.Atoi(envMaxMsgs); err == nil {
			analysisMaxMessages = v
		}
	}
	if envMaxOut := os.Getenv("EVE_ANALYSIS_MAX_OUTPUT_TOKENS"); envMaxOut != "" {
		if v, err := strconv.Atoi(envMaxOut); err == nil {
			analysisMaxOutputTokens = v
		}
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
		AnalysisThinkingLevel: analysisThinkingLevel,
		AnalysisMaxMessages: analysisMaxMessages,
		AnalysisMaxOutputTokens: analysisMaxOutputTokens,
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
