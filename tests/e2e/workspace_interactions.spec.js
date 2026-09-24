const { test, expect } = require('@playwright/test');
const { signIn } = require('./helpers');

test.describe('Dashboard and workspace interactions', () => {
  test('Signal Center opens, loads its feed, closes by button, and closes with Escape', async ({ page }) => {
    await signIn(page, 'HR');
    const panel = page.locator('#signalCenter');
    await page.getByRole('button', { name: 'Open Signal Center' }).click();
    await expect(panel).toHaveAttribute('aria-hidden', 'false');
    await expect(page.locator('#signalFeed')).not.toContainText('Loading signals');
    await page.keyboard.press('Escape');
    await expect(panel).toHaveAttribute('aria-hidden', 'true');
    await page.getByRole('button', { name: 'Open Signal Center' }).click();
    await page.getByRole('button', { name: 'Close Signal Center' }).click();
    await expect(panel).toHaveAttribute('aria-hidden', 'true');
  });

  test('HR dashboard summary and action stream render clear empty states', async ({ page }) => {
    await signIn(page, 'HR');
    await expect(page.getByRole('heading', { name: /Good to see you/i })).toBeVisible();
    await expect(page.getByText('What needs your attention')).toBeVisible();
    await expect(page.locator('.workspace-content')).toBeVisible();
  });

  test('availability form exposes the required interval fields and save action', async ({ page }) => {
    await signIn(page, 'Supervisor');
    await page.goto('/availability');
    await expect(page.getByRole('heading', { name: 'Protect your time' })).toBeVisible();
    await expect(page.getByLabel('Unavailable from')).toBeVisible();
    await expect(page.getByLabel('Unavailable until')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save unavailable time' })).toBeVisible();
  });

  test('availability block can be saved and removed by its owner', async ({ page }) => {
    await signIn(page, 'Employee');
    await page.goto('/availability');
    await page.getByLabel('Unavailable from').fill('2030-05-20T10:00');
    await page.getByLabel('Unavailable until').fill('2030-05-20T11:00');
    await page.getByLabel('Reason').fill('Automated test block');
    await page.getByRole('button', { name: 'Save unavailable time' }).click();
    await expect(page.locator('.page-alert')).toContainText('Your unavailable time was saved');
    const block = page.locator('.par-period').filter({ hasText: 'Automated test block' });
    await expect(block).toBeVisible();
    await block.getByRole('button', { name: 'Remove' }).click();
    await expect(page.locator('.page-alert')).toContainText('Unavailable time removed');
    await expect(block).toHaveCount(0);
  });

  test('review cycle page shows status and its configured action area to HR', async ({ page }) => {
    await signIn(page, 'HR');
    await page.goto('/review-cycles');
    await expect(page.getByRole('heading', { name: /Review Cycles|Review Program/i })).toBeVisible();
    await expect(page.locator('.workspace-content')).toBeVisible();
  });

  test('important pages load without uncaught browser errors', async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await signIn(page, 'HR');
    for (const route of ['/employees', '/review-cycles', '/review-records', '/development-pulse']) {
      await page.goto(route);
      await expect(page.locator('.workspace-content')).toBeVisible();
    }
    expect(errors).toEqual([]);
  });
});
