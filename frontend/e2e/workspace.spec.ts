import { test, expect, Page } from "@playwright/test";

const user = { id: 1, email: "admin@example.com", role: "admin", is_active: true, must_change_password: false };
const project = { id: 1, name: "ERP", organization_id: 1, created_by_id: 1, workspace_slug: "erp", phase: "build", created_at: "2026-01-01T00:00:00Z", instances: [] };
const instance = { id: 1, project_id: 1, erp_type: "odoo", url: "http://odoo.test", db_name: "erp", username: "admin", environment: "staging", hosting_type: "on_premise", auth_method: "xmlrpc", status: "connected", is_active: true, version_info: {}, capabilities: {}, bridge_status: "connected", last_tested_at: null, last_error: null, created_at: "2026-01-01T00:00:00Z" };
const run = (status: string) => ({ id: "run-1", project_id: 1, requested_by_id: 1, status, prompt: "Inspect ERP", support_id: "support", error_category: null, error_message: null, retryable: false, input_tokens: 10, output_tokens: 4, cost_usd: 0.01, created_at: "2026-01-01T00:00:00Z", started_at: null, finished_at: null, task_graph: null, active_task_id: null, question: null });
const envelope = (sequence: number, type: string, payload: object) => `id: ${sequence}\ndata: ${JSON.stringify({ id: sequence, run_id: "run-1", sequence, type, payload, created_at: "2026-01-01T00:00:00Z" })}\n\n`;

async function baseRoutes(page: Page, handler: (path: string, method: string) => { status?: number; json?: unknown; body?: string; contentType?: string } | undefined) {
  await page.route("http://localhost:8001/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const custom = handler(path, route.request().method());
    if (custom) return route.fulfill(custom);
    if (path === "/auth/me") return route.fulfill({ json: { user, csrf_token: "csrf" } });
    if (path === "/projects/1") return route.fulfill({ json: project });
    if (path === "/projects/1/instances") return route.fulfill({ json: [instance] });
    if (path === "/projects/1/workspace/tree") return route.fulfill({ json: [] });
    if (path === "/projects/1/workspace/diff") return route.fulfill({ json: { diff: "" } });
    if (path === "/projects/1/artifacts" || path === "/projects/1/deployments") return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: `Unhandled ${path}` } });
  });
}

test("streams a run and preserves it when starting a new chat", async ({ page }) => {
  let created = false;
  await baseRoutes(page, (path, method) => {
    if (path === "/projects/1/runs" && method === "GET") return { json: created ? [run("succeeded")] : [] };
    if (path === "/projects/1/runs" && method === "POST") { created = true; return { status: 201, json: run("queued") }; }
    if (path === "/runs/run-1/stream") return { contentType: "text/event-stream", body: envelope(1, "message.delta", { text: "ERP inspected." }) + envelope(2, "run.completed", { status: "succeeded" }) };
    if (path === "/runs/run-1") return { json: run("succeeded") };
  });
  await page.goto("/projects/1");
  await page.getByPlaceholder(/Ask the agent/).fill("Inspect ERP");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("ERP inspected.")).toBeVisible();
  await page.getByRole("button", { name: /New Chat/ }).click();
  await expect(page.getByText("ERP inspected.")).toHaveCount(0);
  await page.getByRole("button", { name: /History/ }).click();
  await expect(page.getByText("Inspect ERP")).toBeVisible();
});

test("restores and answers a paused clarification", async ({ page }) => {
  const waiting = { ...run("awaiting_question"), question: { id: "q1", run_id: "run-1", question: "Which module?", options: ["Sales"], answer: null, status: "pending", created_at: "2026-01-01T00:00:00Z", expires_at: "2099-01-01T00:00:00Z" } };
  await baseRoutes(page, (path, method) => {
    if (path === "/projects/1/runs") return { json: [waiting] };
    if (path === "/runs/run-1/events") return { json: [{ id: 1, run_id: "run-1", sequence: 1, event_type: "question.required", payload: { question_id: "q1", question: "Which module?", options: ["Sales"], expires_at: "2099-01-01T00:00:00Z" }, created_at: "2026-01-01T00:00:00Z" }] };
    if (path === "/runs/run-1/question" && method === "POST") return { json: run("queued") };
    if (path === "/runs/run-1/stream") return { contentType: "text/event-stream", body: envelope(2, "question.answered", { question_id: "q1", answer: "Sales" }) + envelope(3, "message.delta", { text: "Sales selected." }) + envelope(4, "run.completed", { status: "succeeded" }) };
    if (path === "/runs/run-1") return { json: run("succeeded") };
  });
  await page.goto("/projects/1");
  await expect(page.getByText("Which module?")).toBeVisible();
  await page.getByRole("button", { name: "Sales" }).click();
  await page.getByRole("button", { name: "Submit Answer" }).click();
  await expect(page.getByText("Sales selected.")).toBeVisible();
});

test("displays diagnostic failure card with retry button on worker unavailable run", async ({ page }) => {
  const failedRun = {
    ...run("interrupted"),
    error_category: "WorkerUnavailable",
    error_message: "Background worker process is unavailable. Check worker process health or retry.",
    retryable: true,
  };
  let retried = false;
  await baseRoutes(page, (path, method) => {
    if (path === "/projects/1/runs" && method === "GET") return { json: [failedRun] };
    if (path === "/runs/run-1/events") return { json: [] };
    if (path === "/runs/run-1/retry" && method === "POST") {
      retried = true;
      return { status: 201, json: run("queued") };
    }
    if (path === "/runs/run-1/stream") return { contentType: "text/event-stream", body: envelope(1, "run.completed", { status: "succeeded" }) };
    if (path === "/runs/run-1") return { json: run("succeeded") };
  });

  await page.goto("/projects/1");
  await expect(page.getByRole("alert").getByText("WorkerUnavailable")).toBeVisible();
  await expect(page.getByRole("alert").getByText("Background worker process is unavailable")).toBeVisible();
  await page.getByRole("button", { name: "Retry Run" }).click();
  await expect(page.getByRole("button", { name: "Retry Run" })).toHaveCount(0);
});

