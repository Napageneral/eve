# Integration Tests

**Focused tests for specific system components** without the full E2E overhead.

These tests are faster than E2E tests but more realistic than unit tests.

---

## SSE Connection Survival Test

**File:** `sse-connection-survival.test.ts`

**Purpose:** Verify that Eve's SSE endpoint keeps connections alive for 60+ seconds.

**Why it matters:** Triggers can fire 30-60 seconds after creation. If the SSE connection drops, trigger messages can't be delivered.

**Run:**
```bash
npm run test:sse-survival
```

**What it tests:**
1. Starts Eve service
2. Connects to SSE endpoint
3. Monitors connection for 65 seconds
4. Verifies heartbeats arrive every 15s
5. Reports any gaps or connection drops

**Expected output:**
```
✅ PASS - SSE connection survived 65s with healthy heartbeats
   Duration: 65s
   Received heartbeats: 4 (expected 4)
   Max gap between events: 15s
   ✅ No suspicious gaps detected
```

**Success criteria:**
- Connection lasts 60+ seconds
- Heartbeats arrive every 15s (±tolerance)
- No gaps > 22s (1.5x heartbeat interval)

**Failure indicators:**
- "Stream ended after Xs" (connection closed prematurely)
- "Missing X heartbeats" (heartbeat mechanism broken)
- "Gap exceeds 1.5x heartbeat interval" (connection stalled)

---

## When to Use Integration Tests

**Use integration tests when:**
- Testing a specific component (SSE, queue, database)
- Need faster iteration than full E2E (seconds vs minutes)
- Component can run independently of full app

**Use E2E tests when:**
- Testing complete user flows
- Need UI validation
- Testing cross-system integration

**Use unit tests when:**
- Testing pure functions
- Testing business logic
- No external dependencies needed

---

## Adding New Integration Tests

1. Create file in `test/integration/`
2. Use `.test.ts` suffix
3. Add npm script to `package.json`
4. Document in this README
5. Keep tests focused (test one thing well)

**Template:**
```typescript
/**
 * Your Test Name
 * 
 * What this test validates and why it matters.
 * 
 * Run: npm run test:your-test
 */

async function runTest(): Promise<void> {
  try {
    // Setup
    // Test
    // Verify
    // Report
  } finally {
    // Cleanup
  }
}

runTest()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
```

---

## Debugging Integration Tests

**Test hangs?**
- Check if services are already running (port conflicts)
- Increase startup timeout
- Check logs for errors

**Test fails intermittently?**
- Increase timing tolerances
- Check for race conditions
- Verify cleanup between runs

**Connection issues?**
- Check firewall settings
- Verify ports are available
- Test with `curl` or browser first

---

## Integration Test Philosophy

**Integration tests bridge the gap between unit tests (too isolated) and E2E tests (too slow).**

They let you:
- ✅ Test real component behavior
- ✅ Iterate rapidly (seconds not minutes)
- ✅ Debug specific issues in isolation
- ✅ Build confidence before full E2E

**But they can't replace E2E tests!** Always run full E2E before committing.


