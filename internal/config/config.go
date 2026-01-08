package config

import (
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
func Load() *Config {
	appDir := GetAppDir()

	cfg := &Config{
		AppDir:        appDir,
		EveDBPath:     filepath.Join(appDir, "eve.db"),
		QueueDBPath:   filepath.Join(appDir, "eve-queue.db"),
		ConfigPath:    filepath.Join(appDir, "config.json"),
		GeminiAPIKey:  getEnv("GEMINI_API_KEY", ""),
		AnalysisModel: getEnv("EVE_GEMINI_ANALYSIS_MODEL", "gemini-3.0-flash"),
		EmbedModel:    getEnv("EVE_GEMINI_EMBED_MODEL", "text-embedding-005"),
	}

	return cfg
}

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}
