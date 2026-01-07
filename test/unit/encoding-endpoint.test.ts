/**
 * Encoding Endpoint Unit Test (Eve CLI)
 *
 * Tests the /engine/encode endpoint that the compute plane uses to encode
 * conversations for analysis/embeddings.
 *
 * This suite creates a tiny central.db fixture so it runs without any user data.
 */

import * as path from 'path';
import * as os from 'os';
import * as fs from 'fs/promises';
import { fileURLToPath } from 'url';
import { Database } from 'bun:sqlite';
import { startContextEngineServer } from '../helpers/context-engine.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '../..');

async function createCentralDbFixture(appDir: string) {
  const dbPath = path.join(appDir, 'central.db');
  const db = new Database(dbPath);

  db.exec(`
    CREATE TABLE IF NOT EXISTS conversations (
      id INTEGER PRIMARY KEY,
      chat_id INTEGER NOT NULL,
      start_time TEXT,
      end_time TEXT
    );

    CREATE TABLE IF NOT EXISTS contacts (
      id INTEGER PRIMARY KEY,
      name TEXT,
      nickname TEXT,
      is_me INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY,
      conversation_id INTEGER NOT NULL,
      chat_id INTEGER NOT NULL,
      timestamp TEXT NOT NULL,
      guid TEXT,
      sender_id INTEGER,
      content TEXT,
      is_from_me INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS attachments (
      id INTEGER PRIMARY KEY,
      message_id INTEGER NOT NULL,
      mime_type TEXT,
      filename TEXT,
      file_name TEXT,
      is_sticker INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS reactions (
      id INTEGER PRIMARY KEY,
      original_message_guid TEXT NOT NULL,
      sender_id INTEGER,
      reaction_type INTEGER,
      associated_message_type INTEGER,
      is_from_me INTEGER DEFAULT 0
    );
  `);

  // Seed a single conversation with two messages and one reaction.
  db.exec(`
    INSERT INTO conversations (id, chat_id, start_time, end_time)
    VALUES (1, 1, '2026-01-01T00:00:00Z', '2026-01-01T00:05:00Z');

    INSERT INTO contacts (id, name, nickname, is_me) VALUES
      (1, 'Alice', NULL, 0),
      (2, 'Me', NULL, 1);

    INSERT INTO messages (id, conversation_id, chat_id, timestamp, guid, sender_id, content, is_from_me) VALUES
      (1, 1, 1, '2026-01-01T00:00:01Z', 'm1', 1, 'hello', 0),
      (2, 1, 1, '2026-01-01T00:00:05Z', 'm2', 2, 'hi', 1);

    INSERT INTO reactions (id, original_message_guid, sender_id, reaction_type, associated_message_type, is_from_me)
    VALUES (1, 'm1', 2, 2001, NULL, 1);
  `);

  db.close();
}

async function runTests() {
  let passed = 0;
  let failed = 0;

  console.log('\n' + '='.repeat(70));
  console.log('  ENCODING ENDPOINT UNIT TESTS');
  console.log('='.repeat(70) + '\n');

  const appDir = await fs.mkdtemp(path.join(os.tmpdir(), 'eve-test-appdir-'));
  await createCentralDbFixture(appDir);

  const server = await startContextEngineServer({ repoRoot, appDir });

  try {
    // Test 1: Missing parameters
    console.log('Test 1: Encoding endpoint rejects missing parameters');
    try {
      const response = await fetch(`http://127.0.0.1:${server.port}/engine/encode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });

      const data = await response.json();
      if (response.ok) throw new Error('Should have rejected missing params');
      if (response.status !== 400) throw new Error(`Wrong status code: ${response.status}`);
      if (!String(data.error || '').includes('Missing required fields')) throw new Error('Wrong error message');

      console.log('✅ PASSED - Missing params rejected');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Missing params:', err.message);
      failed++;
    }

    // Test 2: Unknown conversation returns empty success
    console.log('\nTest 2: Unknown conversation returns empty result');
    try {
      const response = await fetch(`http://127.0.0.1:${server.port}/engine/encode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: 99999, chat_id: 1 }),
      });
      if (!response.ok) throw new Error(`Unexpected status: ${response.status}`);
      const data = await response.json();
      if (!data.success) throw new Error('Expected success=true');
      if (data.message_count !== 0) throw new Error('Expected message_count=0');
      if (data.token_count !== 0) throw new Error('Expected token_count=0');
      if (data.encoded_text !== '') throw new Error('Expected empty encoded_text');
      console.log('✅ PASSED - Empty result returned');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Unknown conversation:', err.message);
      failed++;
    }

    // Test 3: Real encoding works (alias endpoint)
    console.log('\nTest 3: Encoding alias endpoint returns encoded text');
    try {
      const response = await fetch(`http://127.0.0.1:${server.port}/api/engine/encode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: 1, chat_id: 1 }),
      });
      if (!response.ok) throw new Error(`Alias endpoint failed: ${response.status}`);
      const data = await response.json();

      if (!data.success) throw new Error('Expected success=true');
      if (data.message_count !== 2) throw new Error(`Expected message_count=2, got ${data.message_count}`);
      if (typeof data.token_count !== 'number') throw new Error('token_count not a number');
      if (!String(data.encoded_text || '').includes('Alice: hello')) throw new Error('encoded_text missing message');
      if (!String(data.encoded_text || '').includes('👍')) throw new Error('encoded_text missing reaction emoji');

      console.log('✅ PASSED - Encoding output validated');
      passed++;
    } catch (err: any) {
      console.error('❌ FAILED - Alias endpoint:', err.message);
      failed++;
    }
  } finally {
    await server.stop();
    await fs.rm(appDir, { recursive: true, force: true });
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


