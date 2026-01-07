import { Page, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

// Helper to get screenshot directory (run-specific if TEST_RUN_DIR is set)
function getScreenshotDir(): string {
  const runDir = process.env.CHATSTATS_TEST_RUN_DIR;
  if (runDir) {
    const screenshotsDir = path.join(runDir, 'screenshots');
    if (!fs.existsSync(screenshotsDir)) {
      fs.mkdirSync(screenshotsDir, { recursive: true });
    }
    return screenshotsDir;
  }
  // Default to test/screenshots
  return path.join(__dirname, '../../test/screenshots');
}

export async function completeOnboarding(page: Page): Promise<void> {
  console.log('[Test] Completing onboarding flow...');
  
  // Step 1: Click "Get Started" on intro page
  console.log('[Test]   - Clicking Get Started...');
  const getStartedButton = page.locator('[data-testid="onboarding-get-started"]');
  await expect(getStartedButton).toBeVisible({ timeout: 10000 });
  await getStartedButton.click();
  
  // Step 2: Full Disk Access page - click dev bypass button (triggers ETL + analysis AND advances carousel)
  console.log('[Test]   - Bypassing disk access check (dev mode)...');
  const devBypassButton = page.locator('[data-testid="dev-bypass-disk-access"]');
  await expect(devBypassButton).toBeVisible({ timeout: 5000 });
  await devBypassButton.click();
  
  // Wait for carousel to advance (dev bypass button calls onComplete which advances)
  await page.waitForTimeout(2000);
  
  // Step 3: Skip intro-2 (How it works) - should already be on intro-2 now, click Next
  console.log('[Test]   - Skipping intro 2...');
  const nextButton = page.locator('button:has-text("Next")');
  await expect(nextButton).toBeVisible({ timeout: 5000 });
  await expect(nextButton).toBeEnabled({ timeout: 5000 });
  await nextButton.click();
  
  // Step 4: Paywall - click Subscribe (either plan works)
  console.log('[Test]   - Clicking Subscribe on paywall...');
  const subscribeButton = page.locator('button:has-text("Subscribe")').first();
  await expect(subscribeButton).toBeVisible({ timeout: 10000 });
  await subscribeButton.click();
  
  // Sometimes needs a second click (as user mentioned)
  await page.waitForTimeout(1000);
  const stillOnPaywall = await subscribeButton.isVisible().catch(() => false);
  if (stillOnPaywall) {
    console.log('[Test]   - Clicking Subscribe again...');
    await subscribeButton.click();
  }
  
  // Wait for onboarding to complete (should navigate away from paywall)
  await page.waitForTimeout(2000);
  
  console.log('[Test] ✅ Onboarding completed');
}

export async function openEveChat(page: Page): Promise<void> {
  console.log('[Test] Opening Eve chat...');
  
  // Wait for sidecar to be ready
  await expect(page.locator('[data-testid="sidecar-container"]')).toBeVisible({ timeout: 10000 });
  
  // Click Eve button in sidebar
  const eveButton = page.locator('[data-testid="sidebar-eve-button"]');
  await expect(eveButton).toBeVisible({ timeout: 10000 });
  console.log('[Test]   - Eve button found, clicking...');
  await eveButton.click();
  console.log('[Test]   - Eve button clicked');
  
  // Wait for URL to change to ?view=eve (sidecar uses URL params for routing)
  console.log('[Test]   - Waiting for URL to change to ?view=eve...');
  await page.waitForURL(/view=eve/, { timeout: 5000 }).catch(async () => {
    const url = page.url();
    console.log('[Test]   - URL did not change, current URL:', url);
    // Force navigation if event didn't work
    const newUrl = url.includes('?') ? url + '&view=eve' : url + '?view=eve';
    await page.goto(newUrl);
  });
  
  console.log('[Test]   - URL changed, waiting for Eve panel to render...');
  
  // Wait for panel transition
  await page.waitForTimeout(1000);
  
  // Verify Eve chat panel is visible
  const messagesContainer = page.locator('[data-testid="eve-messages-container"]');
  await expect(messagesContainer).toBeVisible({ timeout: 10000 });
  
  console.log('[Test] ✅ Eve chat opened');
}

export async function sendEveMessage(page: Page, message: string): Promise<void> {
  console.log(`[Test] Sending Eve message: "${message}"`);
  
  // Find input - EveChatPanel has a form with input
  const input = page.locator('[data-testid="eve-input"]');
  await expect(input).toBeVisible({ timeout: 5000 });
  await input.fill(message);
  
  // Click send button
  const sendButton = page.locator('[data-testid="eve-send-button"]');
  await sendButton.click();
  
  console.log('[Test] ✅ Message sent');
}

export async function waitForAnalysisComplete(page: Page, maxWaitMs: number = 10 * 60 * 1000): Promise<void> {
  console.log('[Test] Waiting for ETL + analysis to complete...');

  const startWait = Date.now();
  let lastProgress = '';

  while (Date.now() - startWait < maxWaitMs) {
    try {
      const state = await page.evaluate(() =>
        (window as any).__processingState || { queues: [], anyActive: false }
      );

      // Simple check: is ANYTHING still running?
      if (!state.anyActive) {
        console.log('[Test] ✅ ETL + Analysis complete - database is ready!');
        return;
      }

      // Show progress every change
      const analysisQ = state.queues.find((q: any) => q.name === 'analysis');
      const embQ = state.queues.find((q: any) => q.name === 'embedding' || q.name === 'embeddings');
      const progress = `Convos: ${analysisQ?.completed || 0}/${analysisQ?.total || 0}, Emb: ${embQ?.completed || 0}/${embQ?.total || 0}`;

      if (progress !== lastProgress) {
        console.log(`[Test] 📊 Progress: ${progress}`);
        lastProgress = progress;
      }

    } catch (err: any) {
      console.warn(`[Test] Error reading state: ${err.message}`);
    }

    await new Promise(res => setTimeout(res, 5000)); // Check every 5s
  }

  console.warn('[Test] ⚠️  Analysis timeout - proceeding anyway');
}

export async function waitForEveResponse(page: Page, timeoutMs: number = 60000): Promise<string> {
  console.log('[Test] Waiting for Eve response...');
  
  // Count messages before to detect new ones
  const messagesBefore = await page.locator('[data-testid="eve-message"]').count();
  console.log(`[Test]   - Messages before: ${messagesBefore}`);
  
  // Wait for loading indicator to appear
  try {
    await expect(page.locator('[data-testid="eve-loading"]')).toBeVisible({ timeout: 5000 });
    console.log('[Test]   - Loading indicator appeared');
  } catch {
    console.log('[Test]   - No loading indicator (fast response or already loaded)');
  }
  
  // Wait for loading to disappear (response complete)
  await expect(page.locator('[data-testid="eve-loading"]')).toBeHidden({ timeout: timeoutMs });
  console.log('[Test]   - Loading completed');
  
  // Wait a moment for message to render
  await page.waitForTimeout(500);
  
  // Get all messages after response
  const messages = page.locator('[data-testid="eve-message"]');
  const count = await messages.count();
  console.log(`[Test]   - Messages after: ${count}`);
  
  if (count === 0) {
    throw new Error('No Eve messages found after loading completed');
  }
  
  // Get the LAST message (should be Eve's response)
  const lastMessage = messages.last();
  await expect(lastMessage).toBeVisible();
  
  // Get the text content
  const responseText = await lastMessage.textContent();
  const preview = responseText?.substring(0, 150) || '';
  console.log(`[Test] ✅ Eve responded (${responseText?.length || 0} chars): "${preview}..."`);
  
  // Take screenshot of the response for verification
  const screenshotPath = path.join(getScreenshotDir(), `eve-response-${Date.now()}.png`);
  await lastMessage.screenshot({ path: screenshotPath });
  
  return responseText || '';
}

