const { expect } = require('@playwright/test');

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

module.exports = { accounts, signIn };
