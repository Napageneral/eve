package contextengine

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/Napageneral/eve/internal/resources"
)

func TestStaticSnippetAdapter(t *testing.T) {
	params := map[string]interface{}{
		"text": "This is test context.\nIt contains sample data.",
	}
	context := RetrievalContext{
		Vars: make(map[string]interface{}),
	}

	result, err := staticSnippetAdapter(params, context)
	if err != nil {
		t.Fatalf("staticSnippetAdapter failed: %v", err)
	}

	expected := "This is test context.\nIt contains sample data."
	if result.Text != expected {
		t.Errorf("expected text %q, got %q", expected, result.Text)
	}

	// Check token estimation
	if result.ActualTokens == 0 {
		t.Errorf("expected non-zero token count")
	}
}

func TestVariableSubstitution_PureVariable(t *testing.T) {
	params := map[string]interface{}{
		"text": "{{conversation_text}}",
	}
	context := RetrievalContext{
		Vars: map[string]interface{}{
			"conversation_text": "Hello, world!",
		},
	}

	resolved, err := substituteVariables(params, context)
	if err != nil {
		t.Fatalf("substituteVariables failed: %v", err)
	}

	if resolved["text"] != "Hello, world!" {
		t.Errorf("expected 'Hello, world!', got %v", resolved["text"])
	}
}

func TestVariableSubstitution_TemplateString(t *testing.T) {
	params := map[string]interface{}{
		"text": "User {{user_name}} said: {{message}}",
	}
	context := RetrievalContext{
		Vars: map[string]interface{}{
			"user_name": "Alice",
			"message":   "Hello!",
		},
	}

	resolved, err := substituteVariables(params, context)
	if err != nil {
		t.Fatalf("substituteVariables failed: %v", err)
	}

	expected := "User Alice said: Hello!"
	if resolved["text"] != expected {
		t.Errorf("expected %q, got %v", expected, resolved["text"])
	}
}

func TestVariableSubstitution_BuiltIn(t *testing.T) {
	params := map[string]interface{}{
		"chat_id": "{{source_chat}}",
	}
	context := RetrievalContext{
		SourceChat: 42,
		Vars:       make(map[string]interface{}),
	}

	resolved, err := substituteVariables(params, context)
	if err != nil {
		t.Fatalf("substituteVariables failed: %v", err)
	}

	if resolved["chat_id"] != 42 {
		t.Errorf("expected 42, got %v", resolved["chat_id"])
	}
}

func TestVariableSubstitution_MissingVariable(t *testing.T) {
	params := map[string]interface{}{
		"text": "{{missing_var}}",
	}
	context := RetrievalContext{
		Vars: make(map[string]interface{}),
	}

	_, err := substituteVariables(params, context)
	if err == nil {
		t.Fatal("expected error for missing variable, got nil")
	}
}

func TestContextEngineCompile_TestStaticPack(t *testing.T) {
	// Use embedded resources
	loader := resources.NewLoader("")
	engine := NewEngine(loader, "")

	// Use the test-v1 prompt which references test-static pack
	request := ExecuteRequest{
		PromptID: "test-v1",
		Vars:     make(map[string]interface{}),
	}

	result, err := engine.Execute(request)
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	// Check if result is an error
	if execErr, ok := result.(ExecuteError); ok {
		t.Fatalf("execution returned error: %s - %s", execErr.Kind, execErr.Message)
	}

	// Should be an ExecuteResult
	execResult, ok := result.(ExecuteResult)
	if !ok {
		t.Fatalf("expected ExecuteResult, got %T", result)
	}

	// Verify ledger
	if execResult.Ledger.TotalTokens == 0 {
		t.Error("expected non-zero total tokens in ledger")
	}

	// Verify hidden parts (v1: no always_on support, so just the main pack)
	if len(execResult.HiddenParts) == 0 {
		t.Error("expected at least one hidden part")
	}

	// Check that we have the TEST_CONTEXT slice
	foundTestContext := false
	for _, part := range execResult.HiddenParts {
		if part.Name == "TEST_CONTEXT" {
			foundTestContext = true
			if part.Text == "" {
				t.Error("TEST_CONTEXT part has empty text")
			}
		}
	}

	if !foundTestContext {
		t.Error("expected TEST_CONTEXT slice in hidden parts")
	}

	// Verify visible prompt is present
	if execResult.VisiblePrompt == "" {
		t.Error("expected non-empty visible prompt")
	}
}

func TestContextEngineCompile_WithVariables(t *testing.T) {
	// Create a temporary directory with a custom prompt and pack
	tmpDir := t.TempDir()

	// Create prompts directory
	promptsDir := filepath.Join(tmpDir, "prompts")
	if err := os.MkdirAll(promptsDir, 0755); err != nil {
		t.Fatalf("failed to create prompts dir: %v", err)
	}

	// Write a test prompt with variable substitution
	promptContent := `---
id: test-var-prompt
name: Test Variable Prompt
version: 1.0.0
category: test
default_pack: test-var-pack
execution:
  mode: direct
  result_type: document
---

This is a test prompt for {{user_name}}.`

	promptPath := filepath.Join(promptsDir, "test-var.prompt.md")
	if err := os.WriteFile(promptPath, []byte(promptContent), 0644); err != nil {
		t.Fatalf("failed to write prompt: %v", err)
	}

	// Create packs directory
	packsDir := filepath.Join(tmpDir, "packs")
	if err := os.MkdirAll(packsDir, 0755); err != nil {
		t.Fatalf("failed to create packs dir: %v", err)
	}

	// Write a test pack with variable in params
	packContent := `id: test-var-pack
name: Test Variable Pack
version: 1.0.0
category: test
slices:
  - name: GREETING
    retrieval: static_snippet
    params:
      text: "Hello {{user_name}}, you have {{message_count}} messages."
    estimated_tokens: 50
    why_include: "Test variable substitution"`

	packPath := filepath.Join(packsDir, "test-var.pack.yaml")
	if err := os.WriteFile(packPath, []byte(packContent), 0644); err != nil {
		t.Fatalf("failed to write pack: %v", err)
	}

	// Create engine with override directory
	loader := resources.NewLoader(tmpDir)
	engine := NewEngine(loader, "")

	// Execute with variables
	request := ExecuteRequest{
		PromptID: "test-var-prompt",
		Vars: map[string]interface{}{
			"user_name":     "Alice",
			"message_count": 5,
		},
	}

	result, err := engine.Execute(request)
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	// Check for errors
	if execErr, ok := result.(ExecuteError); ok {
		t.Fatalf("execution returned error: %s - %s", execErr.Kind, execErr.Message)
	}

	execResult, ok := result.(ExecuteResult)
	if !ok {
		t.Fatalf("expected ExecuteResult, got %T", result)
	}

	// Verify visible prompt has substituted variables
	expectedPrompt := "This is a test prompt for Alice."
	if execResult.VisiblePrompt != expectedPrompt {
		t.Errorf("expected visible prompt %q, got %q", expectedPrompt, execResult.VisiblePrompt)
	}

	// Verify hidden part has substituted variables
	if len(execResult.HiddenParts) != 1 {
		t.Fatalf("expected 1 hidden part, got %d", len(execResult.HiddenParts))
	}

	greeting := execResult.HiddenParts[0]
	expectedText := "Hello Alice, you have 5 messages."
	if greeting.Text != expectedText {
		t.Errorf("expected greeting %q, got %q", expectedText, greeting.Text)
	}
}

func TestContextEngineCompile_PromptNotFound(t *testing.T) {
	loader := resources.NewLoader("")
	engine := NewEngine(loader, "")

	request := ExecuteRequest{
		PromptID: "nonexistent-prompt",
	}

	result, err := engine.Execute(request)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	execErr, ok := result.(ExecuteError)
	if !ok {
		t.Fatalf("expected ExecuteError, got %T", result)
	}

	if execErr.Kind != "PromptNotFound" {
		t.Errorf("expected PromptNotFound error, got %s", execErr.Kind)
	}
}

func TestContextEngineCompile_PackNotFound(t *testing.T) {
	// Create a temporary directory with a prompt that references a nonexistent pack
	tmpDir := t.TempDir()
	promptsDir := filepath.Join(tmpDir, "prompts")
	if err := os.MkdirAll(promptsDir, 0755); err != nil {
		t.Fatalf("failed to create prompts dir: %v", err)
	}

	promptContent := `---
id: test-missing-pack
name: Test Missing Pack
version: 1.0.0
category: test
default_pack: nonexistent-pack
execution:
  mode: direct
  result_type: document
---

Test prompt.`

	promptPath := filepath.Join(promptsDir, "test-missing.prompt.md")
	if err := os.WriteFile(promptPath, []byte(promptContent), 0644); err != nil {
		t.Fatalf("failed to write prompt: %v", err)
	}

	loader := resources.NewLoader(tmpDir)
	engine := NewEngine(loader, "")

	request := ExecuteRequest{
		PromptID: "test-missing-pack",
	}

	result, err := engine.Execute(request)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	execErr, ok := result.(ExecuteError)
	if !ok {
		t.Fatalf("expected ExecuteError, got %T", result)
	}

	if execErr.Kind != "PackNotFound" {
		t.Errorf("expected PackNotFound error, got %s", execErr.Kind)
	}
}

func TestContextEngineCompile_BudgetExceeded(t *testing.T) {
	// Create a pack with large estimated tokens
	tmpDir := t.TempDir()
	promptsDir := filepath.Join(tmpDir, "prompts")
	if err := os.MkdirAll(promptsDir, 0755); err != nil {
		t.Fatalf("failed to create prompts dir: %v", err)
	}

	promptContent := `---
id: test-budget
name: Test Budget
version: 1.0.0
category: test
default_pack: test-large-pack
execution:
  mode: direct
  result_type: document
---

Test prompt.`

	if err := os.WriteFile(filepath.Join(promptsDir, "test-budget.prompt.md"), []byte(promptContent), 0644); err != nil {
		t.Fatalf("failed to write prompt: %v", err)
	}

	packsDir := filepath.Join(tmpDir, "packs")
	if err := os.MkdirAll(packsDir, 0755); err != nil {
		t.Fatalf("failed to create packs dir: %v", err)
	}

	// Generate a large text that will exceed the budget
	// Budget is 1000, with safety factor 0.90 = 900 effective
	// We need more than 900 tokens = more than 3600 characters
	largeText := string(make([]byte, 10000))
	for i := range largeText {
		largeText = largeText[:i] + "x"
	}
	largeText = ""
	for i := 0; i < 10000; i++ {
		largeText += "x"
	}

	packContent := `id: test-large-pack
name: Test Large Pack
version: 1.0.0
category: test
slices:
  - name: LARGE_CONTEXT
    retrieval: static_snippet
    params:
      text: "` + largeText + `"
    estimated_tokens: 10000
    why_include: "Test budget limit"`

	if err := os.WriteFile(filepath.Join(packsDir, "test-large.pack.yaml"), []byte(packContent), 0644); err != nil {
		t.Fatalf("failed to write pack: %v", err)
	}

	loader := resources.NewLoader(tmpDir)
	engine := NewEngine(loader, "")

	request := ExecuteRequest{
		PromptID:     "test-budget",
		BudgetTokens: 1000, // Very small budget
	}

	result, err := engine.Execute(request)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	execErr, ok := result.(ExecuteError)
	if !ok {
		t.Fatalf("expected ExecuteError for budget exceeded, got %T", result)
	}

	if execErr.Kind != "BudgetExceeded" {
		t.Errorf("expected BudgetExceeded error, got %s", execErr.Kind)
	}

	if execErr.Budget == 0 {
		t.Error("expected budget to be set in error")
	}

	if execErr.CurrentTokens == 0 {
		t.Error("expected current_tokens to be set in error")
	}
}
