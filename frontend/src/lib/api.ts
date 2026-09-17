export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export type User = { id: number; email: string; role: "admin" | "member"; is_active: boolean; must_change_password: boolean };
export type AuthState = { user: User; csrf_token: string };
export type SetupStatus = { needs_setup: boolean; user_count: number };
export type Organization = { id: number; name: string; created_at: string; monthly_budget_usd: number | null; budget_warning_percent: number };
export type Instance = { id: number; project_id: number; erp_type: string; url: string; db_name: string | null; username: string | null; environment: "staging" | "production"; hosting_type: "on_premise" | "odoo_sh"; auth_method: "json2" | "xmlrpc"; status: string; is_active: boolean; version_info: Record<string, unknown>; capabilities: Record<string, unknown>; bridge_status: string; last_tested_at: string | null; last_error: string | null; created_at: string };
export type Project = { id: number; name: string; organization_id: number; created_by_id: number; workspace_slug: string; phase: string; created_at: string; instances: Instance[] };
export type ChatMessage = { id?: number | string; role: "user" | "agent" | "system"; content: string; created_at?: string };
export type PendingAction = { id: string; run_id?: string; tool: string; risk_class: string; preview: Record<string, unknown>; arguments?: Record<string, unknown>; expires_at?: string; status?: string };
export type HostPolicy = { id: number; hostname_pattern: string; allow_private_network: boolean; require_https: boolean; is_active: boolean; created_at: string };
export type AuditEvent = { id: number; event_type: string; project_id: number | null; user_id: number | null; risk_class: string | null; result: string | null; support_id: string | null; created_at: string };
export type AgentQuestion = { id: string; run_id: string; question: string; options: string[]; answer: string | null; status: string; created_at: string; expires_at: string };
export type TaskReport = { task_id?: string; outcome: "SUCCESS" | "PARTIAL" | "FAILED"; done: string[]; verification: string; errors: string };
export type TaskGraphNode = { task_id: string; title: string; status: string; risk_class: number; retry_count: number; max_retries?: number; heartbeat_at: string | null; result?: TaskReport | null };
export type AgentRun = { id: string; project_id: number; status: string; intent?: "read_only" | "write"; prompt: string; thread_id?: string; support_id: string; error_category?: string | null; error_message: string | null; retryable: boolean; input_tokens?: number; output_tokens?: number; cost_usd: number; created_at: string; started_at?: string | null; finished_at?: string | null; task_graph?: TaskGraphNode[] | null; active_task_id?: string | null; question?: AgentQuestion | null; planner_model?: string | null; fallback_model?: string | null; workspace_base_revision?: string | null };
export type AgentSubtask = { id: string; parent_run_id: string; project_id: number; task_id: string; title: string; role: string; thread_id: string; status: string; prompt: string; result: TaskReport | null; error_message: string | null; retry_count: number; created_at: string; started_at: string | null; heartbeat_at: string | null; finished_at: string | null };
export type AgentSchedule = { id: string; project_id: number; requested_by_id: number; prompt: string; interval_seconds: number; enabled: boolean; next_run_at: string; last_run_at: string | null; last_run_id: string | null; last_error: string | null; created_at: string; updated_at: string };
export type ToolEvent = { id: number; run_id: string; sequence: number; event_type: string; payload: Record<string, unknown>; created_at: string };
export type Step = { tool: string; label: string; status: "running" | "done" | "failed" | "cancelled"; outcome?: "succeeded" | "failed" | "unknown"; result?: string; startedAt: number; elapsed?: number; category?: "inspect" | "edit" | "verify" | "run" };
export type FinalReport = { scope?: "task" | "run"; outcome: "SUCCESS" | "PARTIAL" | "FAILED"; done: string[]; verification: string; errors: string; pending_approvals: string };
export type ConnectionState = "connecting" | "connected" | "retrying" | "paused" | "disconnected";
export type WorkspaceEntry = { path: string; type: "file" | "directory"; size?: number };
export type WorkspaceSearchResult = { path: string; line: number; text: string };
export type WorkspaceStatus = { branch: string; clean: boolean; changes: Array<{ index: string; worktree: string; path: string }>; head_revision: string | null; branches: string[] };
export type Requirement = { id: number; title: string; description: string; acceptance_criteria: string; status: string };
export type Artifact = { id: string; name: string; version: string; digest: string; path: string; workspace_slug?: string | null; status: string; created_at: string };
export type Deployment = { id: string; instance_id: number; artifact_id: string; environment: string; status: string; requested_by_id: number; approved_by_id: number | null; rollback_plan: string; logs: string; external_job_id: string | null; created_at: string };
export type TerminalSession = { id: string; project_id: number; run_id?: string | null; requested_by_id: number; command: string; cwd: string; status: string; output: string; exit_code: number | null; created_at: string; finished_at: string | null };
export type AcceptanceCheck = {
  id: string;
  kind: string;
  task_id?: string | null;
  spec_target: Record<string, unknown>;
  required: boolean;
  status: "pending" | "running" | "passed" | "failed" | "skipped";
  result_detail?: string | null;
  evidence?: Array<{ kind: string; ref: string; digest: string; summary: string }>;
  evaluated_at?: string | null;
};

export type RunSpecification = {
  id: number;
  run_id: string;
  snapshot_id?: string | null;
  requirements: Array<{ id: string; title: string; description?: string; targets?: string[] }>;
  changes: Array<{ kind: string; target: string }>;
  digest: string;
  status: string;
  created_at: string;
  acceptance_checks: AcceptanceCheck[];
};

export type AgentMemory = { id: number; project_id: number | null; category: string; key: string; content: string; confidence: number; usage_count: number; created_at: string; updated_at: string; evidence_type?: string | null; evidence_ref_id?: string | null; verified_at?: string | null };
export type PermissionGrant = { id: string; project_id: number | null; user_id: number | null; resource: string; decision: string; expires_at: string | null; created_by_id: number; created_at: string };

let csrfToken = "";
export function setCsrfToken(value: string) { csrfToken = value; }

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

export function errorMessage(body: unknown, fallback = "Request failed"): string {
  if (typeof body === "string" && body.trim()) return body;
  if (!body || typeof body !== "object") return fallback;
  const value = body as Record<string, unknown>;
  const detail = value.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => errorMessage(item, "")).filter(Boolean).join("; ") || fallback;
  if (detail && typeof detail === "object") {
    const nested = detail as Record<string, unknown>;
    const message = typeof nested.message === "string" ? nested.message : errorMessage(nested, "");
    return `${message || fallback}${nested.support_id ? ` · Support ${String(nested.support_id)}` : ""}`;
  }
  if (typeof value.message === "string") return `${value.message}${value.support_id ? ` · Support ${String(value.support_id)}` : ""}`;
  if (typeof value.msg === "string") return value.msg;
  return fallback;
}

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const text = await response.text().catch(() => "");
  let body: unknown = text;
  try { body = text ? JSON.parse(text) : null; } catch { /* plain text */ }
  if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("auth:unauthorized"));
  return new ApiError(response.status, errorMessage(body, fallback));
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("ngrok-skip-browser-warning", "1");
  if (init.body) headers.set("Content-Type", "application/json");
  if (init.method && !["GET", "HEAD"].includes(init.method) && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, `Backend unavailable at ${API_URL}. Start the API service and retry.`);
  }
  if (!response.ok) throw await responseError(response, "Request failed");
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function getSetupStatus(): Promise<SetupStatus> { return apiFetch<SetupStatus>("/auth/setup-status"); }
export async function setupAdmin(email: string, password: string): Promise<AuthState> { return apiFetch<AuthState>("/auth/setup-admin", { method: "POST", body: JSON.stringify({ email, password }) }); }
export async function deleteRun(runId: string): Promise<void> { return apiFetch<void>(`/runs/${runId}`, { method: "DELETE" }); }

type SSEEnvelope = { id?: number; run_id?: string; sequence: number; type: string; payload?: Record<string, unknown>; created_at?: string };

export function parseSSE(buffer: string): { records: SSEEnvelope[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  const records: SSEEnvelope[] = [];
  for (const block of blocks) {
    const data = block.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
    if (!data) continue;
    try {
      const parsed = JSON.parse(data) as SSEEnvelope;
      if (Number.isFinite(Number(parsed.sequence)) && typeof parsed.type === "string") records.push(parsed);
    } catch { /* keep the cursor unchanged for malformed records */ }
  }
  return { records, rest };
}

function toToolEvent(runId: string, envelope: SSEEnvelope): ToolEvent {
  return { id: Number(envelope.id || 0), run_id: envelope.run_id || runId, sequence: Number(envelope.sequence), event_type: envelope.type, payload: envelope.payload || {}, created_at: envelope.created_at || new Date().toISOString() };
}

const PAUSE_OR_TERMINAL_EVENTS = new Set(["approval.required", "question.required", "run.completed", "run.failed", "run.cancelled", "run.expired", "run.interrupted"]);
const STOPPED_STATUSES = new Set(["succeeded", "failed", "cancelled", "expired", "interrupted", "awaiting_approval", "awaiting_question"]);

function waitToRetry(delay: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(done, delay);
    function done() { cleanup(); resolve(); }
    function abort() { cleanup(); reject(new DOMException("Aborted", "AbortError")); }
    function cleanup() { globalThis.clearTimeout(timer); if (typeof window !== "undefined") window.removeEventListener("online", done); signal?.removeEventListener("abort", abort); }
    if (typeof window !== "undefined") window.addEventListener("online", done, { once: true });
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function readChunkWithTimeout<T>(
  reader: ReadableStreamDefaultReader<T>,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<ReadableStreamReadResult<T>> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof globalThis.setTimeout> | null = null;
    let settled = false;

    const onAbort = () => {
      cleanup();
      reject(new DOMException("Aborted", "AbortError"));
    };

    const cleanup = () => {
      if (timer !== null) {
        globalThis.clearTimeout(timer);
        timer = null;
      }
      signal?.removeEventListener("abort", onAbort);
    };

    if (signal?.aborted) {
      return reject(new DOMException("Aborted", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });

    timer = globalThis.setTimeout(() => {
      if (!settled) {
        settled = true;
        cleanup();
        reject(new Error(`Stream read timeout after ${timeoutMs}ms`));
      }
    }, timeoutMs);

    reader.read().then(
      (result) => {
        if (!settled) {
          settled = true;
          cleanup();
          resolve(result);
        }
      },
      (err) => {
        if (!settled) {
          settled = true;
          cleanup();
          reject(err);
        }
      },
    );
  });
}

export async function followRun(
  runId: string,
  onEvent: (event: ToolEvent) => void,
  options: { after?: number; signal?: AbortSignal; onConnectionState?: (state: ConnectionState) => void; maxRetries?: number; readTimeoutMs?: number } = {},
): Promise<AgentRun> {
  let sequence = options.after || 0;
  let retries = 0;
  // Keep following active runs across transient API/proxy/worker restarts.
  // Callers can still pass maxRetries for bounded flows and tests.
  const maxRetries = options.maxRetries ?? Number.POSITIVE_INFINITY;
  const readTimeoutMs = options.readTimeoutMs ?? 45_000;
  while (true) {
    options.signal?.throwIfAborted();
    options.onConnectionState?.(retries ? "retrying" : "connecting");
    try {
      const response = await fetch(`${API_URL}/runs/${runId}/stream?after=${sequence}`, { credentials: "include", signal: options.signal });
      if (!response.ok) throw await responseError(response, "Failed to connect to stream");
      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body from stream");
      options.onConnectionState?.("connected");
      const decoder = new TextDecoder();
      let buffer = "";
      let paused = false;
      try {
        while (!paused) {
          const { done, value } = await readChunkWithTimeout(reader, readTimeoutMs, options.signal);
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSSE(buffer);
          buffer = parsed.rest;
          for (const envelope of parsed.records) {
            const incoming = Number(envelope.sequence);
            if (incoming <= sequence) continue;
            if (incoming > sequence + 1) {
              const missing = await apiFetch<ToolEvent[]>(`/runs/${runId}/events?after=${sequence}`);
              for (const event of missing) {
                if (event.sequence >= incoming) break;
                if (event.sequence > sequence) { sequence = event.sequence; onEvent(event); }
              }
              if (incoming > sequence + 1) throw new Error(`Event stream gap after sequence ${sequence}`);
            }
            if (incoming <= sequence) continue;
            sequence = incoming;
            onEvent(toToolEvent(runId, envelope));
            if (PAUSE_OR_TERMINAL_EVENTS.has(envelope.type)) { paused = true; break; }
          }
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
      const current = await apiFetch<AgentRun>(`/runs/${runId}`);
      if (STOPPED_STATUSES.has(current.status)) {
        options.onConnectionState?.(current.status.startsWith("awaiting_") ? "paused" : "connected");
        return current;
      }
      retries = 0;
    } catch (caught) {
      if (options.signal?.aborted || (caught instanceof DOMException && caught.name === "AbortError")) throw caught;
      if (caught instanceof ApiError && [401, 403, 404].includes(caught.status)) throw caught;
      retries += 1;
      if (retries > maxRetries) { options.onConnectionState?.("disconnected"); throw new Error("Connection lost. Retry to continue this run."); }
      options.onConnectionState?.("retrying");
      await waitToRetry(Math.min(1000 * 2 ** (retries - 1), 8000) + Math.floor(Math.random() * 250), options.signal);
    }
  }
}

export async function fetchThreadTranscript(
  projectId: number,
  threadId: string
): Promise<ChatMessage[]> {
  return apiFetch(`/projects/${projectId}/threads/${encodeURIComponent(threadId)}/transcript`);
}

export async function deleteThread(projectId: number, threadId: string): Promise<void> {
  return apiFetch(`/projects/${projectId}/threads/${encodeURIComponent(threadId)}`, { method: "DELETE" });
}
