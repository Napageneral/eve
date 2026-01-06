#!/usr/bin/env node
import { ContextEngine } from './index.js';
import * as path from 'path';

const baseDir = path.join(__dirname, '..');

const commands = {
  'prompts:list': async () => {
    const engine = new ContextEngine(baseDir);
    const { promptCount, errors } = await engine.initialize();

    if (errors.length > 0) {
      console.error('Errors during initialization:');
      errors.forEach(err => console.error(`  - ${err}`));
      process.exit(1);
    }

    const prompts = engine.getAllPrompts();
    console.log(`\nFound ${promptCount} prompts:\n`);
    prompts.forEach(p => {
      console.log(`  ${p.frontmatter.id}`);
      console.log(`    Name: ${p.frontmatter.name}`);
      console.log(`    Category: ${p.frontmatter.category}`);
      console.log(`    Pack: ${p.frontmatter.context.default_pack}`);
      console.log(`    Mode: ${p.frontmatter.execution.mode}`);
      console.log('');
    });
  },

  'prompts:inspect': async (id: string) => {
    if (!id) {
      console.error('Usage: prompts:inspect <prompt-id>');
      process.exit(1);
    }

    const engine = new ContextEngine(baseDir);
    await engine.initialize();

    const prompt = engine.getPrompt(id);
    if (!prompt) {
      console.error(`Prompt not found: ${id}`);
      process.exit(1);
    }

    console.log('\n' + JSON.stringify(prompt.frontmatter, null, 2));
    console.log('\n--- Prompt Body ---\n');
    console.log(prompt.body);
    console.log('\n--- End ---\n');
  },

  'prompts:dry-run': async (id: string, ...args: string[]) => {
    if (!id) {
      console.error('Usage: prompts:dry-run <prompt-id> --chat <chat-id> --budget <tokens>');
      process.exit(1);
    }

    const chatIdx = args.indexOf('--chat');
    const budgetIdx = args.indexOf('--budget');

    const sourceChat = chatIdx !== -1 ? parseInt(args[chatIdx + 1]) : undefined;
    const budgetTokens = budgetIdx !== -1 ? parseInt(args[budgetIdx + 1]) : 180000;

    const engine = new ContextEngine(baseDir);
    await engine.initialize();

    console.log(`\nDry-run: ${id}`);
    console.log(`  Chat: ${sourceChat || 'none'}`);
    console.log(`  Budget: ${budgetTokens} tokens\n`);

    const result = await engine.execute({
      promptId: id,
      sourceChat,
      budgetTokens,
    });

    if ('kind' in result) {
      console.error('❌ Execution failed:');
      console.error(JSON.stringify(result, null, 2));
      process.exit(1);
    }

    console.log('✅ Success!\n');
    console.log('Context Ledger:');
    console.log(`  Total: ${result.ledger.totalTokens} tokens\n`);
    result.ledger.items.forEach(item => {
      console.log(`  • ${item.slice} (${item.packId})`);
      console.log(`    Tokens: ${item.estTokens}`);
      if (item.why) console.log(`    Why: ${item.why}`);
      if (item.encoding) console.log(`    Encoding: ${item.encoding}`);
      console.log('');
    });

    console.log('Hidden Parts:');
    result.hiddenParts.forEach(part => {
      console.log(`  • ${part.name}: ${part.text.length} chars`);
    });

    console.log('\nExecution:');
    console.log(`  Mode: ${result.execution.mode}`);
    console.log(`  Result Type: ${result.execution.resultType}`);
    console.log('');
  },

  'packs:list': async () => {
    const engine = new ContextEngine(baseDir);
    const { packCount, errors } = await engine.initialize();

    if (errors.length > 0) {
      console.error('Errors during initialization:');
      errors.forEach(err => console.error(`  - ${err}`));
      process.exit(1);
    }

    const packs = engine.getAllPacks();
    console.log(`\nFound ${packCount} packs:\n`);
    packs.forEach(p => {
      console.log(`  ${p.spec.id}`);
      console.log(`    Name: ${p.spec.name}`);
      console.log(`    Category: ${p.spec.category}`);
      console.log(`    Flexibility: ${p.spec.flexibility}`);
      console.log(`    Slices: ${p.spec.slices.length}`);
      console.log(`    Est. Tokens: ${p.spec.total_estimated_tokens || 'unknown'}`);
      console.log('');
    });
  },

  'packs:inspect': async (id: string) => {
    if (!id) {
      console.error('Usage: packs:inspect <pack-id>');
      process.exit(1);
    }

    const engine = new ContextEngine(baseDir);
    await engine.initialize();

    const pack = engine.getPack(id);
    if (!pack) {
      console.error(`Pack not found: ${id}`);
      process.exit(1);
    }

    console.log('\n' + JSON.stringify(pack.spec, null, 2));
    console.log('');
  },
};

const [,, command, ...args] = process.argv;

if (!command || !(command in commands)) {
  console.log('Available commands:');
  Object.keys(commands).forEach(cmd => console.log(`  - ${cmd}`));
  process.exit(1);
}

const commandFn = commands[command as keyof typeof commands];
if (typeof commandFn === 'function') {
  (commandFn as any)(...args).catch((err: any) => {
    console.error('Command failed:', err);
    process.exit(1);
  });
}

