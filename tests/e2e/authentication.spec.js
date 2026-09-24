const { test, expect } = require('@playwright/test');
const { signIn } = require('./helpers');

test.describe('Authentication and session security', () => {
  test('password visibility toggles without changing the password value', async ({ page }) => {
    await page.goto('/');
    const password = page.getByLabel('Password', { exact: true });
    await password.fill('Example-Pass-2026!');
    await page.getByRole('button', { name: 'Show password' }).click();
    await expect(password).toHaveAttribute('type', 'text');
    await expect(password).toHaveValue('Example-Pass-2026!');
    await page.getByRole('button', { name: 'Hide password' }).click();
    await expect(password).toHaveAttribute('type', 'password');
  });

  test('non-Altrium email is blocked before authentication is sent', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Email address').fill('person@gmail.com');
    await page.getByLabel('Password', { exact: true }).fill('Example-Pass-2026!');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.locator('#emailError')).toContainText('@altrium.com');
    await expect(page).toHaveURL(/\/$/);
  });

  test('invalid credentials show a generic error and keep the sign-in form available', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Email address').fill('unknown@altrium.com');
    await page.getByLabel('Password', { exact: true }).fill('Incorrect-Pass-2026!');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.locator('.login-error')).toContainText('Invalid email or password');
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible();
  });

  test('login form includes a CSRF token', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#loginForm input[name="csrf_token"]')).toHaveValue(/.+/);
    await expect(page.locator('meta[name="csrf-token"]')).toHaveAttribute('content', /.+/);
  });

  test('logout blocks use of the previous authenticated session', async ({ page }) => {
    await signIn(page, 'Employee');
    await page.getByRole('link', { name: /logout/i }).click();
    await expect(page).toHaveURL(/\/$/);
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/\/$/);
  });
});
