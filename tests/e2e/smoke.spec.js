const { test, expect } = require('@playwright/test');

const accounts = {
  HR: ['hr@altrium.com', 'TestOnly-HR-2026!'],
  Supervisor: ['supervisor@altrium.com', 'TestOnly-Supervisor-2026!'],
  Manager: ['manager@altrium.com', 'TestOnly-Manager-2026!'],
  Employee: ['employee.e2e@altrium.com', 'TestOnly-Employee-2026!'],
};

async function signIn(page, role) {
  const [email, password] = accounts[role];
  await page.goto('/');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.locator('body')).toHaveClass(new RegExp(`role-${role.toLowerCase()}`));
}

test('login page shows and hides the password', async ({ page }) => {
  await page.goto('/');
  const password = page.getByLabel('Password', { exact: true });
  await expect(password).toHaveAttribute('type', 'password');
  await page.getByRole('button', { name: 'Show password' }).click();
  await expect(password).toHaveAttribute('type', 'text');
  await page.getByRole('button', { name: 'Hide password' }).click();
  await expect(password).toHaveAttribute('type', 'password');
});

test('login rejects email outside altrium.com', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Email address').fill('person@gmail.com');
  await page.getByLabel('Password', { exact: true }).fill('SomePassword123!');
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page.locator('#emailError')).toContainText('@altrium.com');
  await expect(page).toHaveURL(/\/$/);
});

test('HR can sign in and open employee and cycle workspaces', async ({ page }) => {
  await signIn(page, 'HR');
  await page.getByRole('navigation', { name: 'Main navigation' })
    .getByRole('link', { name: 'Employees' }).click();
  await expect(page.getByRole('heading', { name: 'Employee Performance Profiles' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Main navigation' })
    .getByRole('link', { name: 'Review Cycles' }).click();
  await expect(page).toHaveURL(/\/review-cycles$/);
});

test('Supervisor can open My Team and Availability', async ({ page }) => {
  await signIn(page, 'Supervisor');
  await page.getByRole('navigation', { name: 'Main navigation' })
    .getByRole('link', { name: 'My Team' }).click();
  await expect(page).toHaveURL(/\/my-team$/);
  await page.getByRole('navigation', { name: 'Main navigation' })
    .getByRole('link', { name: 'Availability' }).click();
  await expect(page).toHaveURL(/\/availability$/);
});

test('Manager can sign in and see approvals workspace', async ({ page }) => {
  await signIn(page, 'Manager');
  await expect(page.getByRole('heading', { name: /Good to see you/i })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Main navigation' })
    .getByRole('link', { name: 'Availability' })).toBeVisible();
});

test('Employee can open Development but not the HR directory', async ({ page }) => {
  await signIn(page, 'Employee');
  await page.getByRole('navigation', { name: 'Main navigation' })
    .getByRole('link', { name: 'Development' }).click();
  await expect(page).toHaveURL(/\/development-pulse$/);
  await page.goto('/employees');
  await expect(page.getByRole('heading', { name: 'Employee Performance Profiles' })).not.toBeVisible();
});

test('Signal Center opens and closes from the dashboard', async ({ page }) => {
  await signIn(page, 'HR');
  await page.getByRole('button', { name: 'Open Signal Center' }).click();
  await expect(page.locator('#signalCenter')).toHaveAttribute('aria-hidden', 'false');
  await page.getByRole('button', { name: 'Close Signal Center' }).click();
  await expect(page.locator('#signalCenter')).toHaveAttribute('aria-hidden', 'true');
});

test('Logout ends the browser session', async ({ page }) => {
  await signIn(page, 'Employee');
  await page.getByRole('link', { name: /Logout/i }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.goto('/dashboard');
  await expect(page).toHaveURL(/\/$/);
});

test('mobile workspaces remain readable without page-level horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, 'Supervisor');
  for (const route of ['/dashboard', '/my-team', '/availability']) {
    await page.goto(route);
    await expect(page.locator('.workspace-content > section').first()).toBeVisible();
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${route} should not scroll sideways`).toBeLessThanOrEqual(2);
  }
});
