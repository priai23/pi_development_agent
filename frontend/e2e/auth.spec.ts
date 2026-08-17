import { test, expect, Page } from "@playwright/test";

const user = { id: 1, email: "admin@example.com", role: "admin", is_active: true, must_change_password: false };

async function unauthenticated(page: Page, needsSetup = false) {
  await page.route("http://localhost:8001/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/setup-status") return route.fulfill({ json: { needs_setup: needsSetup, user_count: needsSetup ? 0 : 1 } });
    if (path === "/auth/login") return route.fulfill({ status: 401, json: { detail: "Invalid credentials" } });
    return route.fulfill({ status: 401, json: { detail: "Authentication required" } });
  });
}

test("redirects an unauthenticated visitor to a working login form", async ({ page }) => {
  await unauthenticated(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("shows the backend error for invalid credentials", async ({ page }) => {
  await unauthenticated(page);
  await page.goto("/login");
  await page.getByPlaceholder("Email").fill("invalid@example.com");
  await page.getByPlaceholder("Password").fill("wrongpassword");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid credentials")).toBeVisible();
});

test("allows first-time setup without an existing session", async ({ page }) => {
  let created = false;
  await page.route("http://localhost:8001/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/setup-status") return route.fulfill({ json: { needs_setup: !created, user_count: created ? 1 : 0 } });
    if (path === "/auth/setup-admin") { created = true; return route.fulfill({ json: { user, csrf_token: "csrf" } }); }
    if (path === "/auth/me" && created) return route.fulfill({ json: { user, csrf_token: "csrf" } });
    if (path === "/projects" || path === "/organizations") return route.fulfill({ json: [] });
    return route.fulfill({ status: 401, json: { detail: "Authentication required" } });
  });
  await page.goto("/setup");
  await expect(page.getByRole("heading", { name: "Initial Setup" })).toBeVisible();
  await page.getByLabel("Administrator Email").fill("admin@example.com");
  await page.getByLabel("Password (min 12 characters)").fill("correct-horse-battery");
  await page.getByLabel("Confirm Password").fill("correct-horse-battery");
  await page.getByRole("button", { name: "Create Admin Account & Launch" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
});
