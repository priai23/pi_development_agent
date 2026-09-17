import { test, expect } from "@playwright/test";

const sessions = [
  { id: "current", created_at: "2026-09-14T08:00:00Z", last_seen_at: "2026-09-14T10:00:00Z", expires_at: "2026-09-21T08:00:00Z", is_current: true },
  { id: "other", created_at: "2026-09-13T08:00:00Z", last_seen_at: "2026-09-13T10:00:00Z", expires_at: "2026-09-20T08:00:00Z", is_current: false },
];

async function accountApi(page: import("@playwright/test").Page) {
  let current = [...sessions];
  let passwordChanged = false;
  await page.route("http://localhost:8001/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/me") return route.fulfill({ json: { user: { id: 1, email: "admin@example.com", role: "admin", is_active: true, must_change_password: false }, csrf_token: "csrf" } });
    if (path === "/auth/sessions" && route.request().method() === "GET") return route.fulfill({ json: current });
    if (path === "/auth/change-password") {
      passwordChanged = true;
      current = current.filter((session) => session.is_current);
      return route.fulfill({ status: 204, body: "" });
    }
    if (path.startsWith("/auth/sessions/") && route.request().method() === "DELETE") {
      current = current.filter((session) => session.id !== path.split("/").pop());
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ json: [] });
  });
  return { changed: () => passwordChanged };
}

test.beforeEach(async ({ page }) => {
  await accountApi(page);
  await page.goto("/account");
});

test("labels the current browser session and revokes another session", async ({ page }) => {
  await expect(page.getByText("This device")).toBeVisible();
  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(page.getByText("This device")).toBeVisible();
  await expect(page.getByText("Last active")).toHaveCount(1);
});

test("changes the password while preserving the current session", async ({ page }) => {
  const api = await accountApi(page);
  await page.getByPlaceholder("Current password").fill("OldPassword123!");
  await page.getByPlaceholder("New password").fill("NewPassword123!");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText("Password changed and other sessions were revoked.")).toBeVisible();
  await expect(page.getByText("This device")).toBeVisible();
  expect(api.changed()).toBe(true);
});
