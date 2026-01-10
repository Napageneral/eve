package resources

import (
	"os"
	"path/filepath"
	"testing"
)

func TestExportResources(t *testing.T) {
	// Create temp directory
	tmpDir := t.TempDir()

	// Create loader with embedded resources
	loader := NewLoader("")

	// Export resources
	promptCount, packCount, err := loader.ExportResources(tmpDir)
	if err != nil {
		t.Fatalf("ExportResources failed: %v", err)
	}

	// Check counts
	if promptCount == 0 {
		t.Error("expected non-zero prompt count")
	}

	if packCount == 0 {
		t.Error("expected non-zero pack count")
	}

	t.Logf("Exported %d prompts and %d packs", promptCount, packCount)

	// Verify that prompts directory was created
	promptsDir := filepath.Join(tmpDir, "prompts")
	if _, err := os.Stat(promptsDir); os.IsNotExist(err) {
		t.Error("prompts directory was not created")
	}

	// Verify that packs directory was created
	packsDir := filepath.Join(tmpDir, "packs")
	if _, err := os.Stat(packsDir); os.IsNotExist(err) {
		t.Error("packs directory was not created")
	}

	// Verify that a known file exists (test-v1.prompt.md)
	testPromptPath := filepath.Join(promptsDir, "test", "test-v1.prompt.md")
	if _, err := os.Stat(testPromptPath); os.IsNotExist(err) {
		t.Error("test-v1.prompt.md was not exported")
	}

	// Verify that a known pack exists (test-static.pack.yaml)
	testPackPath := filepath.Join(packsDir, "test-static.pack.yaml")
	if _, err := os.Stat(testPackPath); os.IsNotExist(err) {
		t.Error("test-static.pack.yaml was not exported")
	}
}
