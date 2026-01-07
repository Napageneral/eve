# Quick Iteration Test Guide

## TL;DR - Fast Testing

For rapid iteration on the **unified communication flow** (Trigger → EA → IA → User):

```bash
npm run test:quick
```

**Runtime: ~30 seconds** (vs 10+ minutes for full E2E test)

---

## What It Tests

The quick test validates **only the communication interfaces** we care about:

### Test 1: Trigger → EA → IA → User
1. Creates a 10-second trigger
2. Waits for trigger to fire
3. Verifies message reaches user via:
   - TriggerScheduler spawns EA with callback
   - Callback → iaMessageQueue.enqueue()
   - Queue → IA.handleAgentMessage()
   - IA → SSE → Direct EventSource → Frontend

### Test 2: User → IA → SSE → User  
1. Sends simple message ("what time is it?")
2. Verifies IA responds via SSE bridge
3. No analysis pipeline needed

---

## What It Skips

To stay fast, the quick test skips:

- ❌ Analysis pipeline (~3+ minutes)
- ❌ ExecutionAgent document generation (~4+ minutes)
- ❌ Gift ideas flow (not needed for trigger testing)
- ❌ Background processing verification

**Use the full E2E test (`npm run test:e2e`) before committing!**

---

## Usage

### Run the test
```bash
cd app
npm run test:quick
```

### Debug mode (see browser)
```bash
npm run test:quick:headed
```

### Expected output
```
⚡ Fast Trigger Flow Test (30s)

[Test] ✅ Sidecar opened with Eve view
[Test] ✅ Eve chat ready
[Test] ✅ Trigger confirmed: I'll remind you to check...
[Test] Waiting 20 seconds for trigger to fire...
[Test] ✅ Triggered reminder received: Time to check your phone!

🎉 FAST TEST PASSED - Unified Flow Verified!

Validated Flow:
  ✅ Trigger → TriggerScheduler spawns EA with callback
  ✅ EA completes → callback fires → notifyCompletion()
  ✅ Callback → iaMessageQueue.enqueue()
  ✅ Queue → IA.handleAgentMessage()
  ✅ IA → SSE stream → Direct EventSource
  ✅ EventSource → Frontend → User sees message
```

---

## When to Use

### Quick Test (30s) - For rapid iteration
- ✅ Testing Trigger → EA → IA → User flow
- ✅ Verifying SSE bridge works
- ✅ Debugging message queue
- ✅ Checking callback registration
- ✅ Fast feedback loop during development

### Full E2E Test (10+ min) - Before committing
- ✅ Verifying complete system integration
- ✅ Testing analysis pipeline
- ✅ Validating ExecutionAgent document generation
- ✅ Ensuring no regressions in existing flows
- ✅ Final validation before push

---

## Iteration Loop

**Fast development cycle:**

1. Make code changes
2. `npm run build:tsc` (rebuild TypeScript)
3. `npm run test:quick` (30s validation)
4. Repeat until quick test passes
5. `npm run test:e2e` (final full validation)
6. Commit

**Don't skip step 5!** The quick test doesn't verify analysis pipeline or document generation.

---

## Debugging

### Test hangs on "Waiting for trigger to fire"

**Check Eve logs for callback registration:**
```bash
# Look for these in test output:
[TriggerScheduler] Registered callback for agent AGENT_ID
[Callbacks] Callback fired for agent AGENT_ID
[IAMessageQueue] Enqueued message from agent
```

### No response received

**Check SSE connection:**
```bash
# Look for:
[useEveNotifications] ✅ Connected to Eve SSE
[IAQueue] Streamed 1 message(s) to SSE
```

### Test passes but no message visible

**Check frontend SSE handler:**
```bash
# Look for:
[Sidecar] 🔔 Eve notification received via SSE
[Sidecar] ✅ Eve message added to store
```

---

## Files

**Test file:**
- `test/e2e/eve-trigger-quick.spec.ts` - Fast iteration test (30s)

**Compare to:**
- `test/e2e/eve-trigger-flow.spec.ts` - Full E2E test (10+ min)

**Shared helpers:**
- `test/helpers/electron-app.ts` - App lifecycle
- `test/helpers/interactions.ts` - UI interactions

---

## Screenshots

Quick test generates 5 screenshots in `test/screenshots/`:

```
quick-01-trigger-request.png    - Trigger creation message sent
quick-02-trigger-confirmed.png  - IA confirms trigger created
quick-03-triggered-reminder.png - Triggered message delivered
quick-04-simple-request.png     - Simple user message
quick-05-simple-response.png    - IA response via SSE
```

---

## Success Criteria

✅ **Both tests pass** (takes ~60 seconds total)

✅ **Responses are meaningful:**
- Test 1: Actual trigger confirmation + reminder
- Test 2: Actual IA response (not error message)

✅ **No critical console errors**

❌ **If quick test fails, don't run full E2E** - Fix the issue first!

---

**Remember: Quick test = fast iteration, Full E2E = final validation before commit.**

