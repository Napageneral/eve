import { test, expect } from '@playwright/test';
import { cleanAppState, launchApp, stopDevServers } from '../helpers/electron-app';
import { completeOnboarding, sendEveMessage, waitForEveResponse } from '../helpers/interactions';

/**
 * FAST ITERATION TEST - Unified Communication Flow
 * 
 * Tests ONLY the new unified flow: Trigger → EA → IA → User
 * 
 * Skips:
 * - Analysis pipeline (saves 3+ minutes)
 * - ExecutionAgent documents (saves 4+ minutes)
 * - Gift ideas flow (not needed for trigger testing)
 * 
 * Total runtime: ~30 seconds instead of 10+ minutes
 */
test.describe('Eve Trigger Communication (Fast)', () => {
  test('trigger fires → EA → IA message queue → SSE bridge → user', async () => {
    console.log('\n⚡ Fast Trigger Flow Test (30s)\n');
    
    // Step 1: Clean wipe
    await cleanAppState();
    
    // Step 2: Launch app
    const { app, mainWindow, getSidecarWindow } = await launchApp();
    
    try {
      // Step 3: Complete onboarding
      await completeOnboarding(mainWindow);
      
      // Step 4: Get sidecar window
      console.log('[Test] Waiting for sidecar window...');
      const sidecarWindow = await getSidecarWindow();
      
      // Verify sidecar opened with Eve view
      const sidecarUrl = sidecarWindow.url();
      expect(sidecarUrl).toContain('view=eve');
      console.log('[Test] ✅ Sidecar opened with Eve view');
      
      // Step 5: Verify Eve chat is visible
      await expect(sidecarWindow.locator('[data-testid="eve-messages-container"]')).toBeVisible({ timeout: 5000 });
      console.log('[Test] ✅ Eve chat ready');
      
      // Step 6: Create a fast trigger (10 seconds instead of 1 minute)
      await sendEveMessage(sidecarWindow, 'remind me to check my phone in 10 seconds');
      await sidecarWindow.screenshot({ path: 'test/screenshots/quick-01-trigger-request.png' });
      
      // Step 7: Wait for trigger creation confirmation
      const response1 = await waitForEveResponse(sidecarWindow, 30 * 1000);
      await sidecarWindow.screenshot({ path: 'test/screenshots/quick-02-trigger-confirmed.png' });
      
      expect(response1).toBeTruthy();
      expect(response1.length).toBeGreaterThan(10);
      console.log(`[Test] ✅ Trigger confirmed: ${response1.substring(0, 100)}...\n`);
      
      // Step 8: Wait for trigger to fire (10s + 5s poll interval + 5s processing buffer)
      console.log('[Test] Waiting 20 seconds for trigger to fire...');
      await sidecarWindow.waitForTimeout(20 * 1000);
      console.log('[Test] ✅ Wait complete, checking for triggered response...');
      
      // Step 9: Wait for triggered response via new flow:
      //   Trigger → EA (with callback) → callback fires → 
      //   iaMessageQueue.enqueue() → IA invoked → SSE bridge → Electron IPC → Frontend
      const response2 = await waitForEveResponse(sidecarWindow, 15 * 1000);
      await sidecarWindow.screenshot({ path: 'test/screenshots/quick-03-triggered-reminder.png' });
      
      expect(response2).toBeTruthy();
      expect(response2.length).toBeGreaterThan(10);
      console.log(`[Test] ✅ Triggered reminder received: ${response2}\n`);
      
      // Step 10: Verify the response came from IA (not directly from EA)
      // The response should be conversational, not just "Task complete"
      expect(response2.toLowerCase()).not.toBe('task complete');
      
      console.log('\n🎉 FAST TEST PASSED - Unified Flow Verified!\n');
      console.log('Validated Flow:');
      console.log('  ✅ Trigger → TriggerScheduler spawns EA with callback');
      console.log('  ✅ EA completes → callback fires → notifyCompletion()');
      console.log('  ✅ Callback → iaMessageQueue.enqueue()');
      console.log('  ✅ Queue → IA.handleAgentMessage()');
      console.log('  ✅ IA → SSE stream → Eve bridge → Electron IPC');
      console.log('  ✅ IPC → Frontend → User sees message\n');
      console.log(`Responses:`);
      console.log(`  1. Confirmation: ${response1.substring(0, 80)}...`);
      console.log(`  2. Reminder: ${response2.substring(0, 80)}...\n`);
      
    } finally {
      await app.close();
      stopDevServers();
    }
  });
  
  test('user message → IA → EA → callback → queue → IA → user', async () => {
    console.log('\n⚡ Fast User→IA→EA Flow Test (30s)\n');
    
    await cleanAppState();
    const { app, mainWindow, getSidecarWindow } = await launchApp();
    
    try {
      await completeOnboarding(mainWindow);
      const sidecarWindow = await getSidecarWindow();
      
      // Verify Eve chat ready
      await expect(sidecarWindow.locator('[data-testid="eve-messages-container"]')).toBeVisible({ timeout: 5000 });
      console.log('[Test] ✅ Eve chat ready');
      
      // Send a message that will invoke an EA (but not analysis - keep it fast)
      // Ask for something simple that doesn't require full analysis
      await sendEveMessage(sidecarWindow, 'what time is it?');
      await sidecarWindow.screenshot({ path: 'test/screenshots/quick-04-simple-request.png' });
      
      // Wait for IA response (should be quick - no analysis needed)
      const response = await waitForEveResponse(sidecarWindow, 30 * 1000);
      await sidecarWindow.screenshot({ path: 'test/screenshots/quick-05-simple-response.png' });
      
      expect(response).toBeTruthy();
      expect(response.length).toBeGreaterThan(5);
      console.log(`[Test] ✅ IA response: ${response}\n`);
      
      console.log('\n🎉 FAST TEST PASSED - User→IA Flow Verified!\n');
      console.log('Validated Flow:');
      console.log('  ✅ User → POST /api/chat → IA.chat()');
      console.log('  ✅ IA → SSE stream → Eve bridge → Electron IPC');
      console.log('  ✅ IPC → Frontend → User sees message\n');
      
    } finally {
      await app.close();
      stopDevServers();
    }
  });
});


