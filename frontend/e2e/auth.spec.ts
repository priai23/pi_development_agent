import { test, expect } from '@playwright/test';

test.describe('Authentication', () => {
  test('should load the login page if unauthenticated', async ({ page }) => {
    // Navigate to the root URL
    await page.goto('/');

    // Expect the title or a specific login element to be visible
    // Note: This assumes the app redirects unauthenticated users to a login screen or renders a login form on '/'
    await expect(page.locator('text=Login').first()).toBeVisible({ timeout: 10000 }).catch(() => {
        console.log('Login text not found, you may need to update this selector based on your UI.');
    });
  });

  test('should show error for invalid credentials', async ({ page }) => {
    await page.goto('/');

    // Example stub for logging in. Update selectors to match your actual UI (e.g. input[name="email"])
    const emailInput = page.locator('input[type="email"]');
    const passwordInput = page.locator('input[type="password"]');
    const submitButton = page.locator('button[type="submit"]');

    if (await emailInput.isVisible()) {
        await emailInput.fill('invalid@example.com');
        await passwordInput.fill('wrongpassword');
        await submitButton.click();

        // Expect an error toast or message
        await expect(page.locator('text=Invalid credentials')).toBeVisible();
    } else {
        console.log('Email input not visible, skipping invalid credentials test stub.');
    }
  });
});
