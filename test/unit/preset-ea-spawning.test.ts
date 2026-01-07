/**
 * Context Definitions / Selections Unit Test (Eve CLI)
 *
 * Validates the compatibility endpoints used by agent harnesses that want to
 * discover available context definitions and create selections.
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
  console.log('  CONTEXT DEFINITIONS / SELECTIONS UNIT TESTS');
  console.log('='.repeat(70) + '\n');

  const server = await startContextEngineServer({ repoRoot });

  try {
    // Test 1: Definitions list
    console.log('Test 1: GET /api/context/definitions returns definitions');
    try {
      const res = await fetch(`http://127.0.0.1:${server.port}/api/context/definitions`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error('Expected array');
      if (!data.some((d: any) => d.name === 'Convos')) throw new Error('Missing Convos definition');
      console.log('✅ PASSED - Definitions listed');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Definitions:', err.message);
      failed++;
    }

    // Test 2: Definitions filter by name
    console.log('\nTest 2: GET /api/context/definitions?name=Convos filters correctly');
    try {
      const res = await fetch(`http://127.0.0.1:${server.port}/api/context/definitions?name=Convos`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!Array.isArray(data) || data.length !== 1) throw new Error('Expected single-item array');
      if (data[0].retrieval_function_ref !== 'convos_context_data') throw new Error('Wrong retrieval fn');
      console.log('✅ PASSED - Definitions filter works');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Definitions filter:', err.message);
      failed++;
    }

    // Test 3: Create selection without resolving (should not touch DB)
    console.log('\nTest 3: POST /api/context/selections returns id without resolving');
    try {
      const res = await fetch(`http://127.0.0.1:${server.port}/api/context/selections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          context_definition_id: 4,
          parameter_values: {},
          resolve_now: false,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.success) throw new Error('Expected success=true');
      if (typeof data.context_selection_id !== 'number') throw new Error('Expected numeric context_selection_id');
      console.log('✅ PASSED - Selection created (compat id)');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Create selection:', err.message);
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

