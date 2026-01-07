/**
 * Context Engine Unit Test (Eve CLI)
 *
 * Validates the Context Engine HTTP server without any Electron/agent runtime.
 *
 * What it checks:
 * - /health comes up
 * - prompt registry loads (test-v1 exists)
 * - /engine/execute assembles static context (no central.db required)
 */

import * as path from 'path';
import { fileURLToPath } from 'url';
import { startContextEngineServer } from '../helpers/context-engine.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '../..');

async function runTests() {
  let passed = 0;
  let failed = 0;

  console.log('\n' + '='.repeat(70));
  console.log('  CONTEXT ENGINE UNIT TESTS');
  console.log('='.repeat(70) + '\n');

  const server = await startContextEngineServer({ repoRoot });

  try {
    // Test 1: Health check
    console.log('Test 1: Health check works');
    try {
      const response = await fetch(`http://127.0.0.1:${server.port}/health`);
      if (!response.ok) throw new Error(`Health check failed: ${response.status}`);

      const data = await response.json();
      if (data.status !== 'ok') throw new Error('Health status not ok');
      if (data.initialized !== true) throw new Error('Engine not initialized');

      console.log('✅ PASSED - Health check');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Health check:', err.message);
      failed++;
    }

    // Test 2: Prompts list includes test-v1
    console.log('\nTest 2: Prompt registry loads (test-v1 exists)');
    try {
      const response = await fetch(`http://127.0.0.1:${server.port}/engine/prompts`);
      if (!response.ok) throw new Error(`Prompts endpoint failed: ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data.prompts)) throw new Error('Missing prompts array');
      if (!data.prompts.some((p: any) => p.id === 'test-v1')) throw new Error('test-v1 not found');

      console.log('✅ PASSED - Prompt registry loaded');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Prompt registry:', err.message);
      failed++;
    }

    // Test 3: Execute works with static pack (no DB)
    console.log('\nTest 3: /engine/execute assembles static context');
    try {
      const response = await fetch(`http://127.0.0.1:${server.port}/engine/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          promptId: 'test-v1',
          budgetTokens: 10_000,
        }),
      });
      if (!response.ok) throw new Error(`/engine/execute failed: ${response.status}`);
      const data = await response.json();

      if (!data.hiddenParts || !Array.isArray(data.hiddenParts)) throw new Error('Missing hiddenParts');
      const testCtx = data.hiddenParts.find((p: any) => p.name === 'TEST_CONTEXT');
      if (!testCtx) throw new Error('Missing TEST_CONTEXT hidden part');
      if (!String(testCtx.text).includes('This is test context.')) throw new Error('TEST_CONTEXT content mismatch');

      if (!String(data.visiblePrompt || '').includes('# Test Prompt')) throw new Error('visiblePrompt mismatch');

      console.log('✅ PASSED - Execute assembled context');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Execute:', err.message);
      failed++;
    }
  } finally {
    await server.stop();
  }

  console.log('\n' + '='.repeat(70));
  console.log(`  TEST SUMMARY: ${passed} passed, ${failed} failed`);
  console.log('='.repeat(70) + '\n');

  if (failed > 0) process.exit(1);
}

runTests().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});

