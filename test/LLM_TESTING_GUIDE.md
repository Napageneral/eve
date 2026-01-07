# LLM Testing Guide - ChatStats E2E Tests

**This guide is specifically for AI agents making changes to ChatStats**

---

## Critical Rule for LLMs

**BEFORE you declare victory on ANY change to the Eve agent system, user flows, or core app behavior:**

1. ✅ Run the E2E test: `npm run test:e2e`
2. ✅ Verify it passes completely
3. ✅ Review the captured responses for quality

**If the test fails, you MUST iterate and fix until it passes.**

---

## Why This Test Exists

The E2E test validates **all 5 core communication interfaces** in one flow:

1. **User → IA**: Frontend POST to Eve → IA.chat() streams response
2. **IA → User**: IA yields messages → SSE → Frontend displays
3. **IA → EA**: IA spawns execution agent with callback
4. **EA → IA**: EA completes → callback invokes IA.handleAgentMessage()
5. **Trigger → EA**: TriggerScheduler spawns EA with callback

**Without this test**, you cannot verify that triggers properly invoke the interaction agent.

---

## How to Run the Test

### Quick Start - Fast Iteration (30 seconds)

For rapid development on the unified communication flow:

```bash
cd app
npm run test:quick
```

**Runtime: ~30 seconds** - Tests only Trigger → EA → IA → User flow

**See `test/QUICK_TEST_GUIDE.md` for details**

### Full E2E Test (10+ minutes)

For complete system validation before committing:

```bash
cd app
npm run test:e2e
```

That's it! The test handles everything:
- Cleans app state (same as `npm run dev-clean`)
- Builds TypeScript
- Starts all dev servers (frontend, artifact-runner, eve)
- Launches Electron
- Runs complete user flow
- Cleans up afterward

### What the Test Does

**Complete user journey (2-3 minutes):**

1. **Clean State** - Wipes databases, localStorage, Redis, Celery state
2. **Build** - Compiles TypeScript, removes stale dist/
3. **Start Services** - Frontend (3030), Artifact Runner (5173), Eve (3031)
4. **Launch Electron** - Opens app with clean userData directory
5. **Onboarding** - Clicks through 4 steps (intro, disk access, intro 2, paywall)
6. **Sidecar** - Opens sidecar, dismisses intro overlay
7. **Eve Chat** - Opens Eve chat panel
8. **Message 1** - Sends "gift ideas for my mom", waits for response
9. **Message 2** - Sends "tell me a joke in 1 minute", waits for trigger confirmation
10. **Trigger Fires** - Waits 90 seconds for trigger to execute
11. **Trigger Response** - Verifies Eve responds with the joke
12. **Cleanup** - Closes app, stops all services

### Expected Output

```
🎉 TEST PASSED - All interfaces validated!

Validated:
  ✅ Clean wipe → Fresh state
  ✅ Onboarding → Sidecar ready
  ✅ User → IA → EA → IA → User (gift ideas)
  ✅ Trigger creation (joke request)
  ✅ Trigger → EA → IA → User (joke delivery)

Captured responses:
  1. Gift ideas: [Eve's actual response about gift ideas]
  2. Trigger confirmation: [Eve confirming trigger set for 1 minute]
  3. Triggered joke: [The actual joke Eve tells after trigger fires]
```

---

## Debugging Test Failures

### Common Failures

#### 1. "Electron app failed to start"

**Symptom:**
```
❌ ELECTRON APP FAILED TO START
Common causes:
  1. TypeScript not compiled
  2. Port conflicts
  3. Redis/Celery/Backend crashes
```

**Fix:**
```bash
# Manually run preparation steps
npm run build:tsc
npm run kill-ports

# Verify services start normally
npm run dev-clean
# Then re-run test
npm run test:e2e
```

#### 2. "Dev servers not ready"

**Symptom:** Test hangs on "Waiting for dev servers..."

**Fix:** One of the dev servers isn't responding:
```bash
# Check which port isn't responding
curl http://127.0.0.1:3030  # Frontend
curl http://127.0.0.1:5173  # Artifact runner
curl http://127.0.0.1:3031/api/test  # Eve

# Kill all processes and retry
npm run kill-ports
npm run test:e2e
```

#### 3. "Shows paywall instead of onboarding"

**Symptom:** Test fails with screenshot showing "Unlock Eve" paywall

**Root Cause:** localStorage wasn't properly cleared

**Fix:**
- Test uses isolated userData directory (`.test-user-data/`)
- If this persists, manually clear:
```bash
rm -rf app/.test-user-data
npm run test:e2e
```

#### 4. "Eve panel doesn't open"

**Symptom:**
```
[Test]   - Eve messages container visible: false
[Test]   - Still showing inbox: true
```

**Root Cause:** URL routing not working (event listener failed)

**Current Fix:** Test force-navigates to `?view=eve` if URL doesn't change

**If still failing:** Check Sidecar.tsx event listener registration (line ~106)

#### 5. "Captured responses are wrong"

**Symptom:** All 3 responses say the same thing or are just the user's input

**Root Cause:** `waitForEveResponse()` might be grabbing wrong message element

**Fix:** Check `test/helpers/interactions.ts` lines 104-118:
```typescript
const messages = page.locator('[data-testid="eve-message"]');
const lastMessage = messages.last();
```

Ensure it's getting the last **assistant** message, not the last user message.

---

## Test File Locations

```
app/test/
├── e2e/
│   └── eve-trigger-flow.spec.ts    # Main test
├── helpers/
│   ├── electron-app.ts             # App lifecycle (clean, build, launch)
│   └── interactions.ts             # UI interactions (onboarding, Eve chat)
├── screenshots/                    # Debug screenshots
├── test-results/                   # Playwright artifacts (auto-generated)
├── playwright.config.ts            # Playwright configuration
└── README.md                       # Detailed technical docs
```

---

## When to Run This Test

### Development Workflow (Recommended)

**During active development:**
1. Make changes to communication flow
2. `npm run build:tsc` (rebuild TypeScript)
3. `npm run test:quick` (~30s - fast feedback)
4. Iterate until quick test passes
5. `npm run test:e2e` (~10min - full validation)
6. Commit

**The quick test (`npm run test:quick`) is perfect for rapid iteration!**

### Must Run (Full E2E)

Run `npm run test:e2e` BEFORE declaring victory when:

1. **Modifying Eve agents** - Any changes to IA, EA, trigger logic
2. **Changing notification flows** - SSE, callbacks, message delivery
3. **Updating agent tools** - `send_message_to_agent`, `complete_task`, etc.
4. **Refactoring communication** - Any changes to the 5 core interfaces
5. **Cleaning up dead code** - Ensure you didn't break working paths

**Tip: Use `npm run test:quick` first, then `npm run test:e2e` for final validation**

### Should Run

Consider running when:

- Modifying onboarding flow
- Changing sidecar routing
- Updating Eve chat UI
- Modifying trigger scheduler

### Can Skip

Skip for unrelated changes:

- Pure UI styling (no logic)
- Documentation updates
- Backend-only changes (if frontend/eve unchanged)

---

## Interpreting Results

### Success Criteria

✅ **Test passes** - Exit code 0, see "🎉 TEST PASSED"

✅ **Captured responses are meaningful:**
- Response 1: Actual gift ideas (not just echoing input)
- Response 2: Confirms trigger set for 1 minute
- Response 3: An actual joke (not the request)

✅ **No critical console errors** - Some warnings OK, but no red errors

### Failure Modes

❌ **Test hangs** - Timeout on one of the wait steps

❌ **Electron crashes** - App fails to start or closes unexpectedly  

❌ **Wrong UI shown** - Paywall instead of onboarding, inbox instead of Eve

❌ **Eve doesn't respond** - Messages sent but no response received

❌ **Trigger doesn't fire** - Wait 90s but no third response

---

## Advanced: Extending the Test

### Adding New Test Cases

Create new test files in `test/e2e/`:

```typescript
import { test, expect } from '@playwright/test';
import { cleanAppState, launchApp, stopDevServers } from '../helpers/electron-app';
import { completeOnboarding } from '../helpers/interactions';

test.describe('My New Feature', () => {
  test('feature works end-to-end', async () => {
    await cleanAppState();
    const { app, mainWindow, getSidecarWindow } = await launchApp();
    
    try {
      await completeOnboarding(mainWindow);
      const sidecar = await getSidecarWindow();
      
      // Your test logic here
      
    } finally {
      await app.close();
      stopDevServers();
    }
  });
});
```

### Helper Functions

**Available in `test/helpers/interactions.ts`:**

```typescript
// Onboarding
await completeOnboarding(mainWindow);

// Sidecar
await dismissSidecarIntro(sidecarWindow);

// Eve chat
await openEveChat(sidecarWindow);
await sendEveMessage(sidecarWindow, 'your message');
const response = await waitForEveResponse(sidecarWindow, timeoutMs);
```

**Available in `test/helpers/electron-app.ts`:**

```typescript
// Lifecycle
await cleanAppState();
const { app, mainWindow, getSidecarWindow } = await launchApp();
stopDevServers();
```

---

## Log Capture - Critical for Debugging

**All Electron/Eve/Backend logs are captured automatically in test output!**

When you run `npm run test:e2e`, the test captures:

### Eve Agent Activity

Look for these tags in test output:
```
[Eve Activity] [TriggerScheduler] Starting...
[Eve Activity] [ExecutionAgent] Starting...
[Eve Activity] [InteractionAgent] ...
[Eve Activity] [Callbacks] ...
[Eve Activity] POST /api/chat
```

### Frontend/Sidecar Activity

```
[Sidecar Eve Activity] [EveChatPanel] handleSubmit called
[Sidecar Eve Activity] [EveChatPanel] Sending message
[Frontend Eve Activity] sendEveMessage called
```

### Database Operations

```
[Database Issue] Initialized engine with database at...
[Database Issue] unable to open database file
```

### Backend/Celery Errors

```
[Electron Console Error] [CELERY-WORKER-ANALYSIS-0-ERR] ...
[Electron Console Error] [BACKEND] ...
```

### How to Use Logs

**When debugging a test failure:**

1. Run the test: `npm run test:e2e | tee test-output.log`
2. Search for Eve activity:
   ```bash
   grep "Eve Activity" test-output.log
   grep "Sidecar Eve Activity" test-output.log
   ```
3. Check for errors:
   ```bash
   grep "ERROR\|Error\|database" test-output.log
   ```
4. Look for your specific component/feature:
   ```bash
   grep "YourFeatureName" test-output.log
   ```

**The logs tell you exactly what's happening** - same as if you ran `npm run dev` manually!

## Troubleshooting for LLMs

### Test Fails After Your Changes?

**Step 1: Identify the failure point**

Read the error message carefully:
- Which step failed? (onboarding, sidecar, Eve chat, trigger)
- What was the error? (element not found, timeout, crash)
- Check the attached screenshots

**Step 2: Check your changes**

Did you modify:
- Agent communication paths? (triggers, callbacks, SSE)
- UI components? (onboarding, sidecar, Eve chat)
- Routing logic? (URL params, event listeners)

**Step 3: Debug systematically**

Add logging to your changes:
```typescript
console.log('[YourFeature] Debug info:', { ... });
```

Re-run test and check console output.

**Step 4: Iterate until it passes**

Fix the issue, rebuild TypeScript, re-run test:
```bash
cd app
npm run build:tsc
npm run test:e2e
```

### Manual Verification

If automated test is confusing, manually test:

```bash
# Clean state
npm run dev-clean

# App should open to onboarding
# Click through to sidecar
# Open Eve chat
# Send "gift ideas for my mom"
# Send "tell me a joke in 1 minute"
# Wait 90 seconds
# Verify joke appears
```

If manual testing works but automated fails, the issue is in test code (not your changes).

---

## Success Checklist for LLMs

Before marking your task complete:

- [ ] Ran `npm run test:e2e`
- [ ] Test passed (exit code 0)
- [ ] Reviewed captured responses (are they meaningful?)
- [ ] No critical console errors
- [ ] If test failed, iterated until it passed
- [ ] Documented any new test helpers added
- [ ] Cleaned up any debug code before committing

---

## Reference: npm run dev Flow

The test mirrors `npm run dev` exactly:

```bash
npm run dev = 
  npm run kill-ports &&
  NODE_ENV=development npm run clean &&
  npm run build:tsc &&
  concurrently 
    "npm run dev:frontend" 
    "npm run dev:artifact-runner" 
    "npm run dev:eve" 
    "wait-on ... && npm run dev:electron"
```

**Test does the same**, except:
- Skips tsc watch (`watch:tsc`)
- Launches Electron via Playwright instead of `npm run dev:electron`
- Uses isolated userData directory (`.test-user-data/`)
- Programmatic UI interactions instead of manual clicking

---

## Final Note

This test is the **single source of truth** for whether the Eve trigger flow works correctly.

If the test passes, the flow works.  
If the test fails, something is broken.  
**Do not skip this test when changing Eve behavior.**

---

**Questions? Check `test/README.md` for technical details.**


---

## Recently Fixed Issues (Nov 2025)

### Database "unable to open" Error - FIXED

**Problem:** Test was failing with "unable to open database file" error from Eve.

**Root Cause:** `DocumentDatabaseClient` was using `{ create: false }` which prevented database creation in clean test environments.

**Fix (Nov 4, 2025):**
- Changed `eve/database/document-db-client.ts` line 44 to `{ create: true }`
- Added `initializeSchema()` to ensure `chatbot_documents` table exists
- Ensures test environment can create databases from scratch

**Validation:**
```bash
# After fix, you should see REAL responses:
[Test] ✅ Eve responded (107 chars): "let me look through your messages..."

# NOT echoed user input:
[Test] ✅ Eve responded (21 chars): "gift ideas for my mom..."  ← WRONG!
```

**If you see echoed responses:** Eve is erroring. Check logs for `[Sidecar Error]` or database errors.

