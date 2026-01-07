# ChatStats E2E Testing Strategy

**Testing for AI agent features that need full system integration.**

---

## Philosophy: Test What Matters

ChatStats uses **End-to-End (E2E) tests** as the primary testing strategy because:

1. **AI features are inherently integration-heavy** - They span LLMs, agents, context retrieval, streaming, and UI
2. **Unit tests are fragile for AI** - Mocking LLM responses creates brittle tests that don't catch real issues
3. **E2E tests give confidence** - If the full flow works, the feature works

**Trade-off:** E2E tests are slower, but they catch real bugs that unit tests miss.

---

## The Testing Strategy

### Rapid Iteration Loop (Development)

**When building a feature:**

1. **Create a focused quick test** (30-90 seconds)
   - Starts the full app (Electron + Backend + Eve)
   - Tests ONLY the feature you're building
   - Skips unrelated slow operations (analysis pipeline, document generation, etc.)
   - See: `e2e/eve-trigger-quick.spec.ts` as reference

2. **Iterate rapidly**
   ```bash
   npm run build:tsc      # Rebuild TypeScript
   npm run test:quick     # Run your quick test (30s)
   ```

3. **When feature works, run full E2E**
   ```bash
   npm run test:e2e       # Full system validation (10+ min)
   ```

4. **Commit only when full E2E passes**

---

## Existing Tests

### Quick Tests (30-90 seconds)

**File:** `e2e/eve-trigger-quick.spec.ts`

**Purpose:** Rapid iteration on Eve communication flow

**Tests:**
1. Trigger → EA → IA → User (10-second trigger)
2. User → IA → User (simple message)

**What it skips:**
- Analysis pipeline (~3 min)
- ExecutionAgent documents (~4 min)
- Background processing

**When to use:**
- Developing trigger/agent features
- Testing SSE bridge
- Debugging message queue
- Testing Eve communication

**Run:**
```bash
npm run test:quick          # Headless
npm run test:quick:headed   # See browser
```

---

### Full E2E Test (10+ minutes)

**File:** `e2e/eve-trigger-flow.spec.ts`

**Purpose:** Complete system validation before committing

**Tests:**
1. Clean wipe → Fresh state
2. Onboarding → Workflow triggers
3. ETL + Historic analysis
4. ~1500 conversation analyses
5. Embeddings indexed
6. ExecutionAgent documents created (3 expected)
7. Eve chat (User → IA → EA → User)
8. Trigger creation and firing

**When to use:**
- Before committing changes
- After major refactors
- Before releasing

**Run:**
```bash
npm run test:e2e
npm run test:e2e:headed     # Debug mode
npm run test:e2e:debug      # Playwright inspector
```

---

## Creating Your Own Quick Test

### Template Pattern

```typescript
import { test, expect } from '@playwright/test';
import { cleanAppState, launchApp, stopDevServers } from '../helpers/electron-app';
import { completeOnboarding, sendEveMessage, waitForEveResponse } from '../helpers/interactions';

test.describe('My Feature Test (Quick)', () => {
  test('my feature works end-to-end', async () => {
    console.log('\n⚡ My Feature Quick Test\n');
    
    // Step 1: Clean wipe
    await cleanAppState();
    
    // Step 2: Launch app (starts everything: Electron + Backend + Eve)
    const { app, mainWindow, getSidecarWindow } = await launchApp();
    
    try {
      // Step 3: Complete onboarding (uses dev skip for speed)
      await completeOnboarding(mainWindow);
      
      // Step 4: Get sidecar window
      const sidecarWindow = await getSidecarWindow();
      
      // Step 5: Test your feature
      await sendEveMessage(sidecarWindow, 'your test message');
      const response = await waitForEveResponse(sidecarWindow, 30 * 1000);
      
      // Step 6: Assert expectations
      expect(response).toBeTruthy();
      expect(response.length).toBeGreaterThan(10);
      
      console.log('\n🎉 TEST PASSED!\n');
      
    } finally {
      await app.close();
      stopDevServers();
    }
  });
});
```

### Key Guidelines

**✅ DO:**
- Start the full app (`launchApp()`)
- Test the complete user flow
- Skip unrelated slow operations (e.g., don't wait for analysis if testing triggers)
- Use generous timeouts (30s for responses, longer if needed)
- Take screenshots at key points for debugging
- Clean up in `finally` block

**❌ DON'T:**
- Mock LLM responses (defeats the purpose)
- Test implementation details (test user-visible behavior)
- Run multiple features in one test (keep focused)
- Skip the cleanup step

---

## Test Helpers

### App Lifecycle (`helpers/electron-app.ts`)

```typescript
cleanAppState()         // Wipe databases, reset state
launchApp()             // Start Electron + Backend + Eve
stopDevServers()        // Kill backend/Eve processes
getSidecarWindow()      // Get sidecar window reference
```

### User Interactions (`helpers/interactions.ts`)

```typescript
completeOnboarding(window)              // Fast dev-mode onboarding
openEveChat(window)                     // Navigate to Eve chat
sendEveMessage(window, text)            // Send message to Eve
waitForEveResponse(window, timeout)     // Wait for Eve's response
```

---

## Debugging Tests

### Test Fails - Where to Look?

**1. Test hangs on "Waiting for response"**

Check Eve logs for:
```bash
[EveBridge] SSE connection established
[IAMessageQueue] Enqueued message
[IAMessageQueue] Processing agent message
```

**2. Response is empty or wrong**

- Check screenshots in `test/screenshots/`
- Look for frontend errors in test output
- Check backend logs for analysis errors

**3. App won't start**

- Check backend startup logs
- Verify ports are available (3000, 8000, 3344)
- Try `npm run clean` to reset build artifacts

### Visual Debugging

Run tests in headed mode:

```bash
npm run test:quick:headed      # See browser during test
npm run test:e2e:debug         # Playwright inspector
```

### Log Output

Tests automatically capture logs from:
- Electron main process
- Electron renderer
- Backend API server
- Eve service

Look for `[ELECTRON]`, `[BACKEND]`, `[EVE]` prefixes in output.

---

## What Gets Tested

### Communication Flows

**All 5 core interfaces:**

1. **User → Frontend → IA**
   - POST `/api/chat` → InteractionAgent.chat()

2. **IA → Frontend → User**
   - SSE stream → Eve bridge → Electron IPC → Frontend

3. **IA → EA → callback → IA**
   - `send_message_to_agent` tool → callback registration

4. **Trigger → EA → callback → IA**
   - TriggerScheduler → ExecutionAgent with callback

5. **EA → callback → IA → User**
   - ExecutionAgent completion → IA message queue → SSE stream

### Data Integrity

- Triggers created in database
- ExecutionAgent documents created
- Session history persisted
- Analysis results stored

### UI Behavior

- Sidecar opens with correct view
- Messages appear in chat
- Loading states work
- Screenshots validate visual state

---

## When to Run Tests

### During Development

**Quick test cycle:**
```bash
# Make changes
npm run build:tsc
npm run test:quick
# Repeat until passing
```

**Frequency:** Every few code changes (30s per run)

### Before Committing

**Full validation:**
```bash
npm run build:tsc
npm run test:e2e
```

**Frequency:** Once before commit (10+ min)

### CI/CD (Future)

- Run full E2E on pull requests
- Run nightly for regression detection
- Fail builds if tests fail

---

## Test Configuration

**Playwright config:** `playwright.config.ts`

```typescript
timeout: 15 * 60 * 1000,           // 15 min max per test
expect: { timeout: 10 * 1000 },    // 10s for assertions
workers: 1,                        // Serial execution only
```

**Why serial?** Tests start full Electron app and can't run in parallel.

**Timeout strategy:**
- Individual assertions: 10s default
- Response waits: 30s-3min depending on operation
- Full test: 15min max (safety net)

---

## Common Pitfalls

### ❌ Test passes but feature doesn't work in real app

**Cause:** Test might be checking wrong thing

**Fix:** Verify test actually exercises the full user flow

### ❌ Test is flaky (passes sometimes)

**Cause:** Timing issues, race conditions

**Fix:** 
- Increase timeouts
- Wait for specific conditions (not arbitrary delays)
- Check for `await request.is_disconnected()` in SSE endpoints

### ❌ Tests take forever

**Cause:** Waiting for slow operations you don't need

**Fix:**
- Create a quick test that skips unrelated operations
- Don't wait for analysis if testing triggers
- Use shorter trigger times (10s not 1min)

---

## Future Improvements

### Planned

- [ ] Snapshot testing for LLM responses (store expected response patterns)
- [ ] Performance benchmarking (track test duration over time)
- [ ] CI/CD integration (GitHub Actions)
- [ ] Visual regression testing (Percy/Chromatic)

### Nice to Have

- [ ] Test coverage metrics (which flows are tested?)
- [ ] Parallel test execution (challenging with Electron)
- [ ] Mock LLM mode for faster iteration (trade-off: less realistic)

---

## Related Documentation

- **[LLM_TESTING_GUIDE.md](./LLM_TESTING_GUIDE.md)** - Detailed guide for LLM-specific testing patterns
- **[QUICK_TEST_GUIDE.md](./QUICK_TEST_GUIDE.md)** - Quick reference for fast iteration
- **[README.md](./README.md)** - General test infrastructure overview

---

## ODU Server Quick Test (Rapid Iteration)

**Location:** `eve/test-odu-quick.ts`

**Purpose:** Ultra-fast validation of ODU server endpoints without Electron overhead

**Runtime:** ~5 seconds

**What it tests:**
- Health endpoint (server is running)
- `/engine/encode` endpoint (conversation encoding)
- `/api/chat-odu` endpoint (broker message routing)
- SSE stream connection (real-time notifications)

**When to use:**
- Debugging ODU server migration issues
- Validating broker communication
- Testing encoding endpoint
- Rapid iteration on server endpoints

**Run:**
```bash
cd app/eve
bun run test-odu-quick.ts
```

**Success output:**
```
🚀 ODU Server Quick Test
============================================================
📊 Test 1: Health Check                    ✅ PASS
📊 Test 2: Encode Endpoint                 ✅ PASS
📊 Test 3: Chat Endpoint                   ✅ PASS
📊 Test 4: SSE Connection                  ✅ PASS
============================================================
✅ ALL TESTS PASSED (4/4)
🎉 ODU server is working correctly!
```

**Debugging with aggressive logging:**

The ODU server includes verbose logging for debugging. Check logs at:
```bash
tail -f /tmp/odu-server-verbose.log

# Filter for specific components:
grep -E "\[ENCODE\]|\[EveAPI\]|\[BROKER\]|\[EVE-IA\]" /tmp/odu-server-verbose.log
```

**Quick iteration loop for ODU issues:**
```bash
# 1. Make changes to server code
# 2. Rebuild
npm run build:tsc

# 3. Restart server with verbose logging
pkill -f "bun.*server.ts"
cd eve && BUN_RUNTIME_TRANSPILER_CACHE_PATH=0 bun run --bun server.ts &> /tmp/odu-server-verbose.log &

# 4. Run quick test
sleep 3
bun run test-odu-quick.ts

# 5. Check logs if test fails
grep -E "\[ENCODE\]|\[EveAPI\]|\[BROKER\]" /tmp/odu-server-verbose.log | tail -50
```

---

## Integration Tests

**Location:** `test/integration/`

Integration tests validate specific subsystems without full Electron app overhead.

### SSE Reliability Test

**File:** `sse-reliability.test.ts`

**Purpose:** Validate Eve's SSE endpoint stays alive permanently

**What it tests:**
- SSE connection accepts connections reliably
- Heartbeats sent every 15 seconds
- Connection stays alive indefinitely (runs until Ctrl+C)
- Detects ANY connection drops immediately
- Auto-reconnects with exponential backoff if dropped

**Run:**
```bash
npm run test:sse-reliability  # Press Ctrl+C to stop
```

**When to use:**
- Debugging SSE connection issues
- Validating heartbeat timing
- Testing reconnection logic
- Ensuring permanent connection stability

### IA Queue SSE Test

**File:** `ia-queue-sse.test.ts`

**Purpose:** End-to-end message delivery validation (IAQueue → SSE → Frontend)

**What it tests:**
- IAQueue enqueues agent messages correctly
- Messages delivered via SSE to frontend
- SSE connection stays alive during trigger execution (75+ seconds)
- Trigger fires and message delivered successfully
- Complete flow: Trigger → EA → IA → SSE → User

**Run:**
```bash
npm run test:ia-queue-sse
```

**When to use:**
- Debugging trigger message delivery failures
- Validating SSE connection during long-running operations
- Testing IAQueue → SSE integration
- Ensuring triggers can deliver messages after 60+ second delays

**Success criteria:**
- 3+ heartbeats (indicates 45+ seconds connection uptime)
- 3 messages delivered: user message + ACK + trigger message
- Zero connection drops

---

## Summary

**Key Takeaway:** Create a quick test for your feature, iterate rapidly, then validate with full E2E before committing.

**Quick Test Loop:**
```bash
# 1. Create focused test (30-90s)
# 2. Iterate
npm run build:tsc && npm run test:quick
# 3. Validate
npm run test:e2e
# 4. Commit
```

**Remember:** E2E tests are the source of truth. If the full flow works, ship it! ✅


