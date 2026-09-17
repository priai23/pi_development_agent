import { expect, test } from '@playwright/test';

test('creates an admin through the real disposable backend and reaches projects', async ({ page }) => {
  await page.goto('/setup');
  await expect(page.getByRole('heading', { name: 'Initial Setup' })).toBeVisible();

  await page.getByLabel('Administrator Email').fill('browser-admin@example.com');
  await page.getByLabel('Password (min 12 characters)').fill('correct-horse-battery-staple');
  await page.getByLabel('Confirm Password').fill('correct-horse-battery-staple');
  await page.getByRole('button', { name: 'Create Admin Account & Launch' }).click();

  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole('heading', { name: 'Implementation Projects' })).toBeVisible();
  expect(await page.locator('body').innerText()).not.toContain('Backend unavailable');
});
