# E2E Testing with Playwright

**End-to-end tests for ChatStats Electron app**

---

## Quick Start

```bash
cd app

# Install Playwright (first time only)
npm install
npx playwright install

# Build TypeScript
npm run build:tsc

# Run tests (headless)
npm run test:e2e

# Run tests (headed - see what's happening)
npm run test:e2e:headed

# Debug tests (step through with Playwright Inspector)
npm run test:e2e:debug
```

---

## Available Tests

### `eve-trigger-flow.spec.ts` - Complete Eve Trigger Flow

**What it tests:**
- Clean app state
- Onboarding skip (dev mode)
- Sidecar intro dismissal
- Eve chat interaction
- EA spawning and analysis
- Trigger creation
- Trigger execution via callback
- Real-time notification delivery via SSE

**Duration:** ~4.5 minutes

**Validates all 5 communication interfaces:**
1. User → IA (HTTP POST → SSE)
2. IA → User (pendingYields → SSE)
3. IA → EA (spawn + callback)
4. EA → IA (complete → callback → handleAgentMessage)
5. Trigger → EA → IA (scheduler + callback → SSE)

**User Flow:**
```
1. Clean wipe
2. Launch app
3. Skip onboarding (dev button)
4. Dismiss sidecar intro
5. Click Eve button
6. Send: "gift ideas for my mom"
7. Wait for analysis response
8. Send: "tell me a joke in 1 minute"
9. Wait for trigger confirmation
10. Wait for trigger to fire
11. Verify joke appears automatically
```

**Expected Outputs:**
- Response 1: Analysis of mom conversations with gift ideas
- Response 2: Confirmation that trigger is set
- Response 3: Joke delivered automatically after ~1 minute

---

## Test Helpers

### `helpers/electron-app.ts`

**`cleanAppState()`** - Wipes all app data (same as `npm run dev-clean`)
- Kills running services
- Clears databases
- Clears Redis
- Clears localStorage
- Removes WAL files

**`launchApp()`** - Launches Electron app in test mode
- Returns `app`, `mainWindow`, and `getSidecarWindow()` helper
- Sets `NODE_ENV=development` for dev skip buttons
- Sets `PLAYWRIGHT_TEST=true` flag

### `helpers/interactions.ts`

**`completeOnboarding(page)`** - Clicks dev skip button

**`dismissSidecarIntro(page)`** - Dismisses sidecar overlay (if visible)

**`openEveChat(page)`** - Clicks Eve button in sidebar

**`sendEveMessage(page, message)`** - Types message and clicks send

**`waitForEveResponse(page, timeout)`** - Waits for loading to finish, returns response text

---

## Test Data IDs

All test IDs use the pattern `data-testid="component-action"`:

**Onboarding:**
- `onboarding-get-started` - Get Started button
- `dev-skip-onboarding` - Dev skip button (dev mode only)

**Sidecar:**
- `sidecar-container` - Main sidecar div
- `sidecar-intro-dismiss` - "Let's go" button in intro overlay
- `sidebar-eve-button` - Eve button in collapsed sidebar

**Eve Chat:**
- `eve-back-button` - Back to inbox button
- `eve-messages-container` - Messages scroll container
- `eve-message` - Individual message wrapper
- `eve-loading` - Loading indicator
- `eve-input` - Message input textarea
- `eve-send-button` - Send button

**Existing (in MultimodalInput):**
- `MultimodalInput` - Main chat textarea
- `send-button` - Send button

---

## Debugging Failed Tests

### Run with Headed Mode

```bash
npm run test:e2e:headed
```

Watch the test execute in real-time to see where it fails.

### Run with Debug Mode

```bash
npm run test:e2e:debug
```

Playwright Inspector lets you:
- Step through each action
- Inspect element selectors
- See screenshots at each step
- Modify selectors on the fly

### Check Screenshots

Failed tests automatically save screenshots to `test/screenshots/`.

### Check Logs

Electron logs appear in the terminal where you run the test. Look for:
- `[TriggerScheduler]` - Trigger execution logs
- `[InteractionAgent]` - IA invocation logs
- `[ExecutionAgent]` - EA execution logs
- `[Test]` - Test helper logs

### Common Issues

**Test ID not found:**
- Component didn't render (check if parent is visible first)
- Test ID typo (verify exact match)
- Element hidden (check CSS display/visibility)

**Timeout waiting for response:**
- Eve service not running (check port 3031)
- Backend not running (check port 8000)
- Analysis taking longer than expected (increase timeout)

**Clean wipe failed:**
- Services still running (manually kill: `pkill -f celery`)
- Permissions issue (check file paths)
- Redis not running (start Redis: `redis-server`)

**Sidecar window not opening:**
- Onboarding didn't complete successfully
- Check logs for errors in `finishOnboarding()`
- Verify `localStorage.onboardingCompleted` is set

---

## Writing New Tests

### Test Structure

```typescript
import { test, expect } from '@playwright/test';
import { cleanAppState, launchApp } from '../helpers/electron-app';

test.describe('My Test Suite', () => {
  test('my test case', async () => {
    // Always start with clean state
    await cleanAppState();
    
    const { app, mainWindow, getSidecarWindow } = await launchApp();
    
    try {
      // Your test logic here
      
      // Always validate outcomes with expect()
      expect(something).toBeTruthy();
      
    } finally {
      // Always close app
      await app.close();
    }
  });
});
```

### Best Practices

1. **Always clean state first** - Prevents test pollution
2. **Use meaningful timeouts** - Eve operations can be slow
3. **Capture screenshots on key steps** - Visual validation
4. **Log progress** - Makes debugging easier
5. **Validate backend state** - Not just UI (can use IPC to query DB)
6. **Close app in finally block** - Ensures cleanup even on failure

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      
      - name: Install dependencies
        run: cd app && npm install
      
      - name: Install Playwright
        run: cd app && npx playwright install
      
      - name: Build app
        run: cd app && npm run build:tsc
      
      - name: Run E2E tests
        run: cd app && npm run test:e2e
      
      - uses: actions/upload-artifact@v3
        if: failure()
        with:
          name: test-results
          path: app/test/screenshots/
```

---

## Future Test Ideas

### Quick Smoke Tests (<1 min)
- App launches without crash
- Onboarding loads
- Sidecar opens
- Basic UI interactions

### Core User Flows (~5 min each)
- Complete analysis workflow
- Document creation and viewing
- Multiple trigger scheduling
- Trigger cancellation
- Error handling (no data, failed analysis)

### Edge Cases
- Network failures
- Service crashes
- Invalid input
- Concurrent operations
- Multi-window state sync

### Performance Tests
- Response time tracking
- Memory leak detection
- UI responsiveness under load
- Large dataset handling

---

## Related Documentation

- **[Eve TESTING.md](../../eve/TESTING.md)** - CLI-based testing for Eve service
- **[Eve AGENTS.md](../../eve/agents/AGENTS.md)** - Communication interfaces documentation
- **[Playwright Docs](https://playwright.dev)** - Official Playwright documentation






