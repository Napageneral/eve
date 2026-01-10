package resources

import (
	"embed"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

//go:embed embedded_prompts embedded_packs
var embeddedFS embed.FS

// Loader manages loading prompts and packs from embedded FS or override directory
type Loader struct {
	resourcesDir string
}

// NewLoader creates a new resource loader
// If resourcesDir is empty, uses embedded resources only
func NewLoader(resourcesDir string) *Loader {
	return &Loader{
		resourcesDir: resourcesDir,
	}
}

// Prompt represents a parsed prompt with YAML frontmatter and markdown body
type Prompt struct {
	ID           string                 `yaml:"id"`
	Name         string                 `yaml:"name"`
	Version      string                 `yaml:"version"`
	Category     string                 `yaml:"category"`
	Tags         []string               `yaml:"tags"`
	Frontmatter  map[string]interface{} `yaml:",inline"`
	Body         string                 `yaml:"-"`
	RelativePath string                 `yaml:"-"`
}

// Pack represents a parsed context pack
type Pack struct {
	ID                   string                 `yaml:"id"`
	Name                 string                 `yaml:"name"`
	Version              string                 `yaml:"version"`
	Category             string                 `yaml:"category"`
	Tags                 []string               `yaml:"tags"`
	Description          string                 `yaml:"description"`
	Flexibility          string                 `yaml:"flexibility"`
	TotalEstimatedTokens int                    `yaml:"total_estimated_tokens"`
	Slices               []Slice                `yaml:"slices"`
	RelativePath         string                 `yaml:"-"`
	Raw                  map[string]interface{} `yaml:",inline"`
}

// Slice represents a single slice in a context pack
type Slice struct {
	Name            string                 `yaml:"name"`
	Retrieval       string                 `yaml:"retrieval"`
	Params          map[string]interface{} `yaml:"params"`
	EstimatedTokens int                    `yaml:"estimated_tokens"`
	WhyInclude      string                 `yaml:"why_include"`
}

// ListPrompts returns all available prompts
func (l *Loader) ListPrompts() ([]Prompt, error) {
	var prompts []Prompt

	err := l.walkFiles("embedded_prompts", ".prompt.md", func(path string, content []byte) error {
		prompt, err := parsePrompt(path, content)
		if err != nil {
			return fmt.Errorf("parsing %s: %w", path, err)
		}
		prompts = append(prompts, prompt)
		return nil
	})

	return prompts, err
}

// LoadPrompt loads a specific prompt by ID
func (l *Loader) LoadPrompt(id string) (*Prompt, error) {
	prompts, err := l.ListPrompts()
	if err != nil {
		return nil, err
	}

	for _, p := range prompts {
		if p.ID == id {
			return &p, nil
		}
	}

	return nil, fmt.Errorf("prompt not found: %s", id)
}

// ListPacks returns all available context packs
func (l *Loader) ListPacks() ([]Pack, error) {
	var packs []Pack

	err := l.walkFiles("embedded_packs", ".pack.yaml", func(path string, content []byte) error {
		pack, err := parsePack(path, content)
		if err != nil {
			return fmt.Errorf("parsing %s: %w", path, err)
		}
		packs = append(packs, pack)
		return nil
	})

	return packs, err
}

// LoadPack loads a specific pack by ID
func (l *Loader) LoadPack(id string) (*Pack, error) {
	packs, err := l.ListPacks()
	if err != nil {
		return nil, err
	}

	for _, p := range packs {
		if p.ID == id {
			return &p, nil
		}
	}

	return nil, fmt.Errorf("pack not found: %s", id)
}

// walkFiles walks through files in the given base directory with the specified extension
func (l *Loader) walkFiles(baseDir, extension string, fn func(path string, content []byte) error) error {
	// Try override directory first
	if l.resourcesDir != "" {
		// Map embedded_prompts -> prompts, embedded_packs -> packs for override directory
		subdir := strings.TrimPrefix(baseDir, "embedded_")
		overridePath := filepath.Join(l.resourcesDir, subdir)
		if info, err := os.Stat(overridePath); err == nil && info.IsDir() {
			return l.walkOSFiles(overridePath, baseDir, extension, fn)
		}
	}

	// Fall back to embedded FS
	return l.walkEmbeddedFiles(baseDir, extension, fn)
}

// walkOSFiles walks files from the OS filesystem
func (l *Loader) walkOSFiles(rootPath, baseDir, extension string, fn func(path string, content []byte) error) error {
	return filepath.WalkDir(rootPath, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}

		if d.IsDir() || !strings.HasSuffix(path, extension) {
			return nil
		}

		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}

		relPath, _ := filepath.Rel(rootPath, path)
		fullPath := filepath.Join(baseDir, relPath)

		return fn(fullPath, content)
	})
}

// walkEmbeddedFiles walks files from the embedded FS
func (l *Loader) walkEmbeddedFiles(baseDir, extension string, fn func(path string, content []byte) error) error {
	return fs.WalkDir(embeddedFS, baseDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}

		if d.IsDir() || !strings.HasSuffix(path, extension) {
			return nil
		}

		content, err := embeddedFS.ReadFile(path)
		if err != nil {
			return err
		}

		return fn(path, content)
	})
}

// parsePrompt parses a prompt file with YAML frontmatter and markdown body
func parsePrompt(path string, content []byte) (Prompt, error) {
	var prompt Prompt
	prompt.RelativePath = path

	// Split on --- delimiter
	parts := strings.SplitN(string(content), "---", 3)
	if len(parts) < 3 {
		return prompt, fmt.Errorf("invalid prompt format: missing YAML frontmatter delimiters")
	}

	// Parse YAML frontmatter
	yamlContent := parts[1]
	if err := yaml.Unmarshal([]byte(yamlContent), &prompt); err != nil {
		return prompt, fmt.Errorf("parsing YAML frontmatter: %w", err)
	}

	// Store markdown body
	prompt.Body = strings.TrimSpace(parts[2])

	return prompt, nil
}

// parsePack parses a pack YAML file
func parsePack(path string, content []byte) (Pack, error) {
	var pack Pack
	pack.RelativePath = path

	if err := yaml.Unmarshal(content, &pack); err != nil {
		return pack, fmt.Errorf("parsing YAML: %w", err)
	}

	return pack, nil
}
