const { test, expect } = require('@playwright/test');
const { signIn } = require('./helpers');

test.describe('Floating Review Guide', () => {
  test('HR sees the current next step and can open its workspace', async ({ page }) => {
    await signIn(page, 'HR');
    const launcher = page.getByRole('button', { name: 'Open Review Guide' });
    await expect(launcher).toBeVisible();
    await expect(launcher.locator('..')).toHaveCSS('position', 'fixed');
    await launcher.click();
    const guide = page.getByRole('dialog', { name: 'Review Guide' });
    await expect(guide).toBeVisible();
    await expect(guide.getByText('Set up the next review cycle')).toBeVisible();
    await guide.getByRole('button', { name: 'Who can see feedback?' }).click();
    await expect(guide.getByText(/Peer reviewer names are hidden/)).toBeVisible();
    await guide.getByRole('link', { name: 'Open workspace →' }).click();
    await expect(page).toHaveURL(/\/review-cycles$/);
  });

  test('employee gets role-specific writing guidance without HR links', async ({ page }) => {
    await signIn(page, 'Employee');
    await page.getByRole('button', { name: 'Open Review Guide' }).click();
    const guide = page.getByRole('dialog', { name: 'Review Guide' });
    await guide.getByRole('textbox', { name: 'Ask the Review Guide a question' }).fill('How should I write my reflection?');
    await guide.getByRole('button', { name: 'Send question' }).click();
    await expect(guide.getByText(/what you did, what changed and what evidence/)).toBeVisible();
    await expect(guide.getByRole('link', { name: 'Review cycles' })).toHaveCount(0);
  });

  test('guide fits a phone viewport and closes with Escape', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 720 });
    await signIn(page, 'Supervisor');
    const launcher = page.getByRole('button', { name: 'Open Review Guide' });
    await launcher.click();
    const guide = page.getByRole('dialog', { name: 'Review Guide' });
    await expect(guide).toBeVisible();
    const box = await guide.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    expect(box.y + box.height).toBeLessThanOrEqual(720);
    await page.keyboard.press('Escape');
    await expect(guide).toBeHidden();
    await expect(launcher).toBeFocused();
  });
});
