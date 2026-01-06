import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';
import matter from 'gray-matter';
import { watch } from 'fs';
import { promptFrontmatterSchema, type PromptFrontmatter } from './schemas/promptFrontmatter.zod.js';
import { packSpecSchema, type PackSpec } from './schemas/packSpec.zod.js';

export interface LoadedPrompt {
  frontmatter: PromptFrontmatter;
  body: string;
  filePath: string;
}

export interface LoadedPack {
  spec: PackSpec;
  filePath: string;
}

export class Registry {
  private prompts: Map<string, LoadedPrompt> = new Map();
  private packs: Map<string, LoadedPack> = new Map();
  private promptsDir: string;
  private packsDir: string;
  private watchers: fs.FSWatcher[] = [];

  constructor(baseDir: string) {
    this.promptsDir = path.join(baseDir, 'prompts');
    this.packsDir = path.join(baseDir, 'context-packs');
  }

  async load(): Promise<{ promptCount: number; packCount: number; errors: string[] }> {
    const errors: string[] = [];
    const promptCount = await this.loadPrompts(errors);
    const packCount = await this.loadPacks(errors);
    return { promptCount, packCount, errors };
  }

  private async loadPrompts(errors: string[]): Promise<number> {
    this.prompts.clear();

    if (!fs.existsSync(this.promptsDir)) {
      errors.push(`Prompts directory not found: ${this.promptsDir}`);
      return 0;
    }

    const files = this.findFiles(this.promptsDir, '.prompt.md');
    
    for (const filePath of files) {
      try {
        const content = fs.readFileSync(filePath, 'utf-8');
        // Configure gray-matter to use yaml.load (js-yaml 4.x removed safeLoad)
        const { data, content: body } = matter(content, {
          engines: {
            yaml: (s: string) => yaml.load(s) as any
          }
        });
        const frontmatter = promptFrontmatterSchema.parse(data);

        this.prompts.set(frontmatter.id, {
          frontmatter,
          body: body.trim(),
          filePath,
        });
      } catch (err: any) {
        errors.push(`Failed to load prompt ${filePath}: ${err.message}`);
      }
    }

    return this.prompts.size;
  }

  private async loadPacks(errors: string[]): Promise<number> {
    this.packs.clear();

    if (!fs.existsSync(this.packsDir)) {
      errors.push(`Packs directory not found: ${this.packsDir}`);
      return 0;
    }

    const files = this.findFiles(this.packsDir, '.pack.yaml');
    
    for (const filePath of files) {
      try {
        const content = fs.readFileSync(filePath, 'utf-8');
        const data = yaml.load(content);
        const spec = packSpecSchema.parse(data);

        this.packs.set(spec.id, {
          spec,
          filePath,
        });
      } catch (err: any) {
        errors.push(`Failed to load pack ${filePath}: ${err.message}`);
      }
    }

    return this.packs.size;
  }

  private findFiles(dir: string, ext: string): string[] {
    const results: string[] = [];

    if (!fs.existsSync(dir)) {
      return results;
    }

    const entries = fs.readdirSync(dir, { withFileTypes: true });

    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);

      if (entry.isDirectory()) {
        results.push(...this.findFiles(fullPath, ext));
      } else if (entry.isFile() && entry.name.endsWith(ext)) {
        results.push(fullPath);
      }
    }

    return results;
  }

  getPrompt(id: string): LoadedPrompt | undefined {
    return this.prompts.get(id);
  }

  getPack(id: string): LoadedPack | undefined {
    return this.packs.get(id);
  }

  getAllPrompts(): LoadedPrompt[] {
    return Array.from(this.prompts.values());
  }

  getAllPacks(): LoadedPack[] {
    return Array.from(this.packs.values());
  }

  startWatching(onChange: () => void): void {
    this.stopWatching();

    if (fs.existsSync(this.promptsDir)) {
      const promptWatcher = watch(
        this.promptsDir,
        { recursive: true },
        async (eventType, filename) => {
          if (filename && filename.endsWith('.prompt.md')) {
            console.log(`[Registry] Prompt changed: ${filename}`);
            await this.load();
            onChange();
          }
        }
      );
      this.watchers.push(promptWatcher);
    }

    if (fs.existsSync(this.packsDir)) {
      const packWatcher = watch(
        this.packsDir,
        { recursive: true },
        async (eventType, filename) => {
          if (filename && filename.endsWith('.pack.yaml')) {
            console.log(`[Registry] Pack changed: ${filename}`);
            await this.load();
            onChange();
          }
        }
      );
      this.watchers.push(packWatcher);
    }
  }

  stopWatching(): void {
    for (const watcher of this.watchers) {
      watcher.close();
    }
    this.watchers = [];
  }
}

