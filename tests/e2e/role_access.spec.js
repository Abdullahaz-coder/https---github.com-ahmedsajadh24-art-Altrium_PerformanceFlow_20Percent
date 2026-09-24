const { test, expect } = require('@playwright/test');
const { signIn } = require('./helpers');

const roleNavigation = {
  HR: ['Dashboard', 'Employees', 'Review Cycles', 'Records', 'Development', 'Password', 'Logout'],
  Supervisor: ['Dashboard', 'My Team', 'Development', 'Availability', 'Review History', 'Password', 'Logout'],
  Manager: ['Dashboard', 'Availability', 'Review History', 'Password', 'Logout'],
  Employee: ['Dashboard', 'Development', 'Availability', 'Review History', 'Password', 'Logout'],
};

test.describe('Role based navigation and workspaces', () => {
  for (const [role, links] of Object.entries(roleNavigation)) {
    test(`${role} sees only their configured navigation`, async ({ page }) => {
      await signIn(page, role);
      const navigation = page.getByRole('navigation', { name: 'Main navigation' });
      const mainLinks = links.filter(label => !['Password', 'Logout'].includes(label));
      for (const label of mainLinks) await expect(navigation.getByRole('link', { name: label })).toBeVisible();
      const utilityNavigation = page.locator('.nav-bottom');
      for (const label of ['Password', 'Logout']) {
        await expect(utilityNavigation.getByRole('link', { name: label })).toBeVisible();
      }
      for (const restricted of ['Employees', 'Review Cycles', 'Records']) {
        if (!links.includes(restricted)) await expect(navigation.getByRole('link', { name: restricted })).toHaveCount(0);
      }
    });
  }

  test('HR can open the employee directory and search matching profiles', async ({ page }) => {
    await signIn(page, 'HR');
    await page.goto('/employees');
    await expect(page.getByRole('heading', { name: 'Employee Performance Profiles' })).toBeVisible();
    const employeeRow = page.locator('.employee-table tbody tr').filter({ hasText: 'E2E Employee' });
    await expect(employeeRow).toBeVisible();
    await page.getByPlaceholder('Search employees...').fill('no-such-profile');
    await expect(employeeRow).toBeHidden();
    await expect(page.getByRole('status')).toContainText('No employee profiles match your search');
    await page.getByPlaceholder('Search employees...').fill('e2e employee');
    await expect(employeeRow).toBeVisible();
    await expect(page.getByRole('status')).toBeHidden();
  });

  test('employee cannot open HR employee, cycle, or records workspaces directly', async ({ page }) => {
    await signIn(page, 'Employee');
    for (const route of ['/employees', '/review-cycles', '/review-records']) {
      await page.goto(route);
      await expect(page.getByRole('heading', { name: /Employee Performance Profiles|Review Cycles|Records Vault/ })).toHaveCount(0);
      await expect(page.locator('body')).toHaveClass(/role-employee/);
    }
  });

  test('supervisor cannot access HR records directly', async ({ page }) => {
    await signIn(page, 'Supervisor');
    await page.goto('/review-records');
    await expect(page.getByRole('heading', { name: 'Records Vault' })).toHaveCount(0);
    await expect(page.locator('body')).toHaveClass(/role-supervisor/);
  });

  test('employee profile form filters supervisors by selected department and role', async ({ page }) => {
    await signIn(page, 'HR');
    await page.goto('/employees');
    await page.getByRole('button', { name: /add employee/i }).click();
    const role = page.locator('#accountRole');
    const supervisor = page.locator('#supervisor');
    await expect(page.locator('#supervisorField')).toBeVisible();
    await page.locator('#department').selectOption({ label: 'Operations' });
    await expect(supervisor.locator('option').filter({ hasText: 'Sarah Perera' })).toHaveCount(1);
    await page.locator('#department').selectOption({ label: 'Finance' });
    await expect(supervisor.locator('option').filter({ hasText: 'Sarah Perera' })).toHaveCount(0);
    await role.selectOption('Manager');
    await expect(page.locator('#supervisorField')).toBeHidden();
  });

  test('HR can create an employee profile with a department-matched supervisor', async ({ page }) => {
    await signIn(page, 'HR');
    await page.goto('/employees');
    await page.getByRole('button', { name: /add employee/i }).click();
    await page.locator('#fullName').fill('Automation Sample Employee');
    await page.locator('#employeeCode').fill('E2E-NEW-001');
    await page.locator('#hireDate').fill('2026-08-01');
    await page.locator('#employeeEmail').fill('automation.sample@altrium.com');
    await page.locator('#department').selectOption({ label: 'Operations' });
    await page.locator('#jobTitle').fill('Operations Analyst');
    await page.locator('#accountRole').selectOption('Employee');
    const supervisor = page.locator('#supervisor');
    const supervisorId = await supervisor.locator('option').filter({ hasText: 'Sarah Perera' }).getAttribute('value');
    await supervisor.selectOption(supervisorId);
    await page.locator('#temporaryPassword').fill('Automation-Temp-2026!');
    await page.getByRole('button', { name: 'Create Account' }).click();
    await expect(page.locator('.page-alert')).toContainText('Automation Sample Employee');
    await expect(page.locator('.employee-table tbody tr').filter({ hasText: 'E2E-NEW-001' })).toBeVisible();
  });

  test('workflow navigation keeps exactly one current-page marker', async ({ page }) => {
    await signIn(page, 'Supervisor');
    for (const route of ['/my-team', '/availability', '/review-history']) {
      await page.goto(route);
      await expect(page.locator('nav[aria-label="Main navigation"] a[aria-current="page"]')).toHaveCount(1);
    }
  });
});
