const { test, expect } = require('@playwright/test');
const { signIn } = require('./helpers');

const rolePages = [
  ['HR', ['/dashboard', '/employees', '/review-cycles', '/review-records', '/development-pulse']],
  ['Supervisor', ['/dashboard', '/my-team', '/availability', '/review-history']],
  ['Manager', ['/dashboard', '/availability', '/review-history']],
  ['Employee', ['/dashboard', '/availability', '/development-pulse', '/review-history']],
];

test.describe('Responsive layout across roles', () => {
  for (const [role, routes] of rolePages) {
    test(`${role} pages fit phone, tablet, and desktop viewports`, async ({ page }) => {
      await signIn(page, role);
      for (const width of [320, 390, 768, 1280]) {
        await page.setViewportSize({ width, height: 900 });
        for (const route of routes) {
          await page.goto(route);
          await expect(page.locator('.workspace-content')).toBeVisible();
          const metrics = await page.evaluate(() => ({
            client: document.documentElement.clientWidth,
            scroll: document.documentElement.scrollWidth,
          }));
          expect(metrics.scroll - metrics.client, `${role} ${route} at ${width}px`).toBeLessThanOrEqual(2);
        }
      }
    });
  }
});
