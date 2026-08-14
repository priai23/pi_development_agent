export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export type User = { id: number; email: string; role: "admin" | "member"; is_active: boolean; must_change_password: boolean };
export type AuthState = { user: User; csrf_token: string };
export type SetupStatus = { needs_setup: boolean; user_count: number };
export type Organization = { id: number; name: string; created_at: string; monthly_budget_usd: number | null; budget_warning_percent: number };
export type Instance = { id: number; project_id: number; erp_type: string; url: string; db_name: string | null; username: string | null; environment: "staging" | "production"; hosting_type: "on_premise" | "odoo_sh"; auth_method: "json2" | "xmlrpc"; status: string; is_active: boolean; version_info: Record<string, unknown>; capabilities: Record<string, unknown>; bridge_status: string; last_tested_at: string | null; last_error: string | null; created_at: string };
export type Project = { id: number; name: string; organization_id: number; created_by_id: number; workspace_slug: string; phase: string; created_at: string; instances: Instance[] };
export type ChatMessage = { id?: number; role: "user" | "agent"; content: string; created_at?: string };
export type PendingAction = { id: string; tool: string; risk_class: string; preview: Record<string, unknown> };
export type HostPolicy = { id: number; hostname_pattern: string; allow_private_network: boolean; require_https: boolean; is_active: boolean; created_at: string };
export type AuditEvent = { id: number; event_type: string; project_id: number | null; user_id: number | null; risk_class: string | null; result: string | null; support_id: string | null; created_at: string };
export type AgentRun = { id: string; project_id: number; status: string; prompt: string; support_id: string; error_message: string | null; retryable: boolean; cost_usd: number; created_at: string };
export type ToolEvent = { id: number; run_id: string; sequence: number; event_type: string; payload: Record<string, unknown>; created_at: string };
export type Step = { tool: string; label: string; status: "running" | "done" | "failed"; result?: string; startedAt: number; elapsed?: number };
export type AgentQuestion = { question: string; options: string[] };
export type FinalReport = { outcome: "SUCCESS" | "PARTIAL" | "FAILED"; done: string[]; verification: string; errors: string; pending_approvals: string };

export type WorkspaceEntry = { path: string; type: "file" | "directory"; size?: number };
export type Requirement = { id: number; title: string; description: string; acceptance_criteria: string; status: string };
export type Artifact = { id: string; name: string; version: string; digest: string; path: string; status: string; created_at: string };
export type Deployment = { id: string; instance_id: number; artifact_id: string; environment: string; status: string; requested_by_id: number; approved_by_id: number | null; rollback_plan: string; logs: string; external_job_id: string | null; created_at: string };
export type AgentMemory = { id: number; project_id: number | null; category: string; key: string; content: string; confidence: number; usage_count: number; created_at: string; updated_at: string };


let csrfToken = "";

export function setCsrfToken(value: string) { csrfToken = value; }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (init.method && !["GET", "HEAD"].includes(init.method) && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(response.status, body.detail || "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function getSetupStatus(): Promise<SetupStatus> {
  return apiFetch<SetupStatus>("/auth/setup-status");
}

export async function setupAdmin(email: string, password: string): Promise<AuthState> {
  return apiFetch<AuthState>("/auth/setup-admin", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function deleteRun(runId: string): Promise<void> {
  return apiFetch<void>(`/runs/${runId}`, { method: "DELETE" });
}



export async function streamRequest(path: string, body: unknown, onChunk: (text: string) => void): Promise<void> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`${API_URL}${path}`, { method: "POST", credentials: "include", headers, body: JSON.stringify(body) });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(response.status, payload.detail || "Request failed");
  }
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
}

export async function followRun(runId: string, onEvent: (event: ToolEvent) => void): Promise<AgentRun> {
  const response = await fetch(`${API_URL}/runs/${runId}/stream`, { credentials: "include" });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(response.status, body.detail || "Failed to connect to stream");
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body from stream");
  
  const decoder = new TextDecoder();
  let buffer = "";
  let shouldStop = false;

  try {
    while (!shouldStop) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const chunk = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");

        if (!chunk.startsWith("data: ")) continue;
        
        try {
          const payload = JSON.parse(chunk.slice(6));
          onEvent({
            id: 0,
            run_id: runId,
            sequence: payload.sequence,
            event_type: payload.type,
            payload: payload.payload,
            created_at: new Date().toISOString()
          });
          if (payload.type === "approval.required") {
            shouldStop = true;
            break;
          }
        } catch {
          // ignore parse errors for partial/corrupted chunks
        }
      }
    }
  } catch {
    // Gracefully handle stream interruptions
  } finally {
    try { reader.releaseLock(); } catch { /* ignore */ }
  }

  return apiFetch<AgentRun>(`/runs/${runId}`);
}

export function pendingFrom(content: string): PendingAction | null {
  const marker = content.indexOf("_ACTION_PENDING_||");
  if (marker < 0) return null;
  try { return JSON.parse(content.slice(marker + "_ACTION_PENDING_||".length).trim()) as PendingAction; }
  catch { return null; }
}

export function visibleContent(content: string): string { return content.split("_ACTION_PENDING_||", 1)[0].trim(); }
