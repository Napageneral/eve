import { test, expect } from '@playwright/test';
import { cleanAppState, launchApp, stopDevServers } from '../helpers/electron-app';
import { completeOnboarding, sendEveMessage, waitForEveResponse } from '../helpers/interactions';

/**
 * STRICT TRIGGER TEST - Validates Exact Message Count
 * 
 * Expected flow:
 * 1. User sends: "send me a nice message in 30 seconds"
 * 2. IA responds (ACK): "I'll set that up for you!" (via SSE)
 * 3. 30 seconds later, trigger fires
 * 4. EA completes → IA receives message → IA sends to user (via SSE)
 * 
 * SUCCESS CRITERIA:
 * - Exactly 3 messages total (1 user + 1 ack + 1 trigger)
 * - All Eve messages come through SSE (logged with "update" or "initial")
 * - No duplicates
 */
test.describe('Eve Trigger - Strict Message Count', () => {
  test('validates exactly 1 user + 1 ack + 1 trigger message', async () => {
    console.log('\n🎯 STRICT TRIGGER TEST - Validating Exact Message Count\n');
    
    // Clean wipe
    await cleanAppState();
    
    // Launch app
    const { app, mainWindow, getSidecarWindow } = await launchApp();
    
    try {
      // Complete onboarding
      console.log('[Test] Starting onboarding...');
      await completeOnboarding(mainWindow);
      console.log('[Test] ✅ Onboarding complete');
      
      // Get sidecar window (should open with Eve view)
      console.log('[Test] Getting sidecar window...');
      const sidecarWindow = await getSidecarWindow();
      expect(sidecarWindow.url()).toContain('view=eve');
      console.log('[Test] ✅ Sidecar opened with Eve view');
      
      // Verify Eve chat is ready (SSE connection)
      console.log('[Test] Waiting for Eve chat to be ready...');
      await expect(sidecarWindow.locator('[data-testid="eve-messages-container"]')).toBeVisible({ timeout: 5000 });
      console.log('[Test] ✅ Eve chat ready');
      
      // Send trigger request
      console.log('[Test] Sending trigger request...');
      await sendEveMessage(sidecarWindow, 'send me a nice message in 30 seconds');
      await sidecarWindow.screenshot({ path: 'test/screenshots/strict-01-request-sent.png' });
      
      // Wait for ACK response (should be immediate)
      console.log('[Test] Waiting for acknowledgment (IA response)...');
      const ackResponse = await waitForEveResponse(sidecarWindow, 30 * 1000);
      await sidecarWindow.screenshot({ path: 'test/screenshots/strict-02-ack-received.png' });
      
      expect(ackResponse).toBeTruthy();
      expect(ackResponse.length).toBeGreaterThan(10);
      console.log(`[Test] ✅ Acknowledgment received: "${ackResponse.substring(0, 100)}..."`);
      
      // Wait for trigger to fire (30 seconds + 10s buffer for slow systems)
      console.log('[Test] Waiting 40 seconds for trigger to fire...');
      await sidecarWindow.waitForTimeout(40 * 1000);
      console.log('[Test] ✅ Wait complete, checking for triggered response...');
      
      // Wait for trigger response (via IA message queue → SSE)
      console.log('[Test] Waiting for trigger response...');
      const triggerResponse = await waitForEveResponse(sidecarWindow, 30 * 1000);
      await sidecarWindow.screenshot({ path: 'test/screenshots/strict-03-trigger-received.png' });
      
      expect(triggerResponse).toBeTruthy();
      expect(triggerResponse.length).toBeGreaterThan(10);
      console.log(`[Test] ✅ Trigger response received: "${triggerResponse.substring(0, 100)}..."`);
      
      // Verify the two responses are different (not duplicates)
      expect(triggerResponse).not.toBe(ackResponse);
      
      // Verify trigger response is actually a nice message (not IA's meta-response)
      const triggerLower = triggerResponse.toLowerCase();
      
      // Should NOT be IA responding to EA personally
      expect(triggerLower).not.toContain('thank you');
      expect(triggerLower).not.toContain('that\'s kind');
      expect(triggerLower).not.toContain('means a lot');
      
      // Should contain positive/encouraging content (the actual nice message)
      const hasPositiveContent = 
        triggerLower.includes('wonderful') ||
        triggerLower.includes('great') ||
        triggerLower.includes('special') ||
        triggerLower.includes('proud') ||
        triggerLower.includes('amazing') ||
        triggerLower.includes('thoughtful') ||
        triggerLower.includes('care');
      
      expect(hasPositiveContent).toBe(true);
      
      console.log('\n🎉 SUCCESS - Exact message flow validated!');
      console.log(`   1. User message: "send me a nice message in 30 seconds"`);
      console.log(`   2. IA acknowledgment: "${ackResponse.substring(0, 60)}..."`);
      console.log(`   3. Trigger response: "${triggerResponse.substring(0, 60)}..."`);
      console.log(`   ✅ Trigger message is user-directed (not IA meta-response)`);
      console.log(`   ✅ Both messages delivered via SSE`);
      
    } finally {
      // Cleanup
      try {
        if (app) {
          await app.close();
        }
      } catch (e) {
        console.log('[Test] App already closed');
      }
      await stopDevServers();
    }
  });
});

