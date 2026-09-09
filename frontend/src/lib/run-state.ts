import { AgentQuestion, AgentRun, ChatMessage, ConnectionState, FinalReport, PendingAction, Step, TaskGraphNode, ToolEvent } from "./api";
export { type ChatMessage } from "./api";

export const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancelling", "awaiting_question", "awaiting_approval"]);
export const TERMINAL_RUN_STATUSES = new Set(["succeeded", "failed", "cancelled", "expired", "interrupted"]);

function terminalStepState(status: string): { status: Step["status"]; outcome: NonNullable<Step["outcome"]> } {
  if (status === "succeeded") return { status: "done", outcome: "succeeded" };
  if (status === "cancelled") return { status: "cancelled", outcome: "unknown" };
  return { status: "failed", outcome: "failed" };
}

export type WorkspacePhase =
  | "idle"
  | "submitting"
  | "queued"
  | "connecting"
  | "retrying"
  | "thinking"
  | "executing"
  | "awaiting_question"
  | "awaiting_approval"
  | "recovering"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export type PlanItem = { title: string; status: string };
export type RunViewState = {
  run: AgentRun | null;
  selectedRunId: string | null;
  activeRunId: string | null;
  transcript: ChatMessage[];
  cursor: number;
  steps: Step[];
  question: AgentQuestion | null;
  pending: PendingAction | null;
  finalReport: FinalReport | null;
  taskGraph: TaskGraphNode[] | null;
  activeTaskId: string | null;
  recoveringTaskId: string | null;
  planItems: PlanItem[];
  thinkingText: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  connection: ConnectionState | "idle";
  error: string;
};

export const emptyRunState: RunViewState = {
  run: null, selectedRunId: null, activeRunId: null, transcript: [], cursor: 0, steps: [],
  question: null, pending: null, finalReport: null, taskGraph: null, activeTaskId: null,
  recoveringTaskId: null, planItems: [], thinkingText: "", inputTokens: 0, outputTokens: 0, costUsd: 0,
  connection: "idle", error: "",
};

export function getWorkspacePhase(state: RunViewState, isSubmitting = false): WorkspacePhase {
  if (isSubmitting) return "submitting";
  if (!state.run) return "idle";

  const status = state.run.status;
  if (status === "succeeded") return "succeeded";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  if (status === "interrupted" || status === "expired") return "interrupted";
  if (status === "cancelling") return "cancelling";
  if (status === "awaiting_question" || state.question?.status === "pending") return "awaiting_question";
  if (status === "awaiting_approval" || state.pending?.status === "pending") return "awaiting_approval";
  if (state.recoveringTaskId) return "recovering";

  if (state.connection === "retrying") return "retrying";
  if (status === "queued" && state.steps.length === 0 && !state.thinkingText) return "queued";
  if (state.connection === "connecting" && state.steps.length === 0 && !state.thinkingText) return "connecting";

  const hasRunningStep = state.steps.some((s) => s.status === "running");
  if (hasRunningStep) return "executing";
  if (state.thinkingText) return "thinking";
  if (status === "running") return "thinking";

  return status === "queued" ? "queued" : "idle";
}

function category(tool: string): Step["category"] {
  if (/verify|test|lint|typecheck|compile|build|check/.test(tool)) return "verify";
  if (/write|patch|replace|create_directory/.test(tool)) return "edit";
  if (/read|view|inspect|list|grep|search/.test(tool)) return "inspect";
  return "run";
}

function appendAgent(messages: ChatMessage[], text: string, createdAt: string): ChatMessage[] {
  if (!text) return messages;
  const last = messages[messages.length - 1];
  if (last?.role === "agent") return [...messages.slice(0, -1), { ...last, content: last.content + text }];
  return [...messages, { role: "agent", content: text, created_at: createdAt }];
}

function applyEvent(state: RunViewState, event: ToolEvent): RunViewState {
  if (event.sequence <= state.cursor) return state;
  const next = { ...state, cursor: event.sequence };
  const payload = event.payload;
  switch (event.event_type) {
    case "run.queued":
      if (next.run) next.run = { ...next.run, status: "queued" };
      next.activeRunId = next.run?.id || next.activeRunId;
      next.thinkingText = "";
      next.error = "";
      break;
    case "run.started":
      if (next.run) next.run = { ...next.run, status: "running" };
      next.activeRunId = next.run?.id || next.activeRunId;
      next.error = "";
      break;
    case "message.delta":
      next.transcript = appendAgent(state.transcript, String(payload.text || ""), event.created_at);
      break;
    case "thinking":
      next.thinkingText = String(payload.message || "");
      break;
    case "tool.started": {
      const tool = String(payload.tool || "unknown");
      next.steps = [...state.steps, { tool, label: tool.replace(/_/g, " "), status: "running", outcome: "unknown", startedAt: new Date(event.created_at).getTime(), category: (payload.category as Step["category"]) || category(tool) }];
      break;
    }
    case "tool.completed":
    case "tool.failed": {
      const tool = String(payload.tool || "unknown");
      const index = [...state.steps].reverse().findIndex((step) => step.tool === tool && step.status === "running");
      const steps = [...state.steps];
      const outcome = event.event_type === "tool.failed" || payload.outcome === "failed" ? "failed" : payload.outcome === "succeeded" ? "succeeded" : "unknown";
      const result = String(payload.result || payload.error || "");
      if (index === -1) {
        steps.push({ tool, label: tool.replace(/_/g, " "), status: outcome === "failed" ? "failed" : "done", outcome, result, startedAt: new Date(event.created_at).getTime(), elapsed: 0, category: (payload.category as Step["category"]) || category(tool) });
      } else {
        const realIndex = steps.length - index - 1;
        const started = steps[realIndex].startedAt;
        steps[realIndex] = { ...steps[realIndex], status: outcome === "failed" ? "failed" : "done", outcome, result, elapsed: Math.max(0, (new Date(event.created_at).getTime() - started) / 1000) };
      }
      next.steps = steps;
      break;
    }
    case "usage":
      {
        const usage = (payload.cumulative as Record<string, unknown>) || payload;
        next.inputTokens = Number(usage.input_tokens || 0);
        next.outputTokens = Number(usage.output_tokens || 0);
        next.costUsd = Number(usage.cost_usd || 0);
      }
      break;
    case "question.required":
    case "question":
      next.question = { id: String(payload.question_id || ""), run_id: event.run_id, question: String(payload.question || ""), options: (payload.options as string[]) || [], answer: null, status: "pending", created_at: event.created_at, expires_at: String(payload.expires_at || "") };
      next.connection = "paused";
      break;
    case "question.answered":
      next.question = null;
      if (next.run && (next.run.status === "awaiting_question" || next.run.status.startsWith("awaiting_"))) {
        next.run = { ...next.run, status: "queued" };
      }
      next.transcript = [...state.transcript, { role: "user", content: String(payload.answer || ""), created_at: event.created_at }];
      break;
    case "approval.required":
      next.pending = { id: String(payload.action_id || ""), run_id: event.run_id, tool: String(payload.tool || ""), risk_class: String(payload.risk_class || ""), preview: (payload.preview as Record<string, unknown>) || {}, arguments: (payload.arguments as Record<string, unknown>) || {}, expires_at: String(payload.expires_at || ""), status: "pending" };
      next.connection = "paused";
      break;
    case "approval.decided":
    case "approval.approved":
    case "approval.rejected":
      next.pending = null;
      if (next.run && (next.run.status === "awaiting_approval" || next.run.status.startsWith("awaiting_"))) {
        next.run = { ...next.run, status: "queued" };
      }
      break;
    case "final_report":
      if (!payload.scope || payload.scope === "run") {
        next.finalReport = { scope: (payload.scope as FinalReport["scope"]) || undefined, outcome: String(payload.outcome || "FAILED") as FinalReport["outcome"], done: (payload.done as string[]) || [], verification: String(payload.verification || ""), errors: String(payload.errors || ""), pending_approvals: String(payload.pending_approvals || "") };
      }
      break;
    case "task.report": {
      const taskId = String(payload.task_id || "");
      next.taskGraph = state.taskGraph?.map((task) => task.task_id === taskId ? { ...task, result: { task_id: taskId, outcome: String(payload.outcome || "FAILED") as "SUCCESS" | "PARTIAL" | "FAILED", done: (payload.done as string[]) || [], verification: String(payload.verification || ""), errors: String(payload.errors || "") } } : task) || null;
      break;
    }
    case "plan.updated":
      next.planItems = (payload.items as PlanItem[]) || [];
      break;
    case "supervisor.plan":
      next.taskGraph = (payload.task_graph as TaskGraphNode[]) || [];
      next.activeTaskId = String(payload.active_task_id || "") || null;
      break;
    case "task.started":
      next.activeTaskId = String(payload.task_id || "") || null;
      next.recoveringTaskId = null;
      next.taskGraph = state.taskGraph?.map((task) => task.task_id === payload.task_id ? { ...task, status: "in_progress" } : task) || null;
      break;
    case "task.completed":
      next.activeTaskId = null;
      next.taskGraph = (payload.task_graph as TaskGraphNode[]) || state.taskGraph?.map((task) => task.task_id === payload.task_id ? { ...task, status: "done" } : task) || null;
      break;
    case "task.recovering":
      next.recoveringTaskId = String(payload.task_id || "") || null;
      next.taskGraph = state.taskGraph?.map((task) => task.task_id === payload.task_id ? { ...task, status: "pending", retry_count: Number(payload.attempt || task.retry_count) } : task) || null;
      break;
    case "task.failed":
      next.activeTaskId = null;
      next.taskGraph = (payload.task_graph as TaskGraphNode[]) || state.taskGraph?.map((task) => task.task_id === payload.task_id ? { ...task, status: "failed" } : task) || null;
      break;
    case "task.cancelled":
      next.activeTaskId = null;
      next.taskGraph = (payload.task_graph as TaskGraphNode[]) || state.taskGraph?.map((task) => task.task_id === payload.task_id ? { ...task, status: "cancelled" } : task) || null;
      break;
    case "supervisor.complete":
      next.taskGraph = (payload.task_graph as TaskGraphNode[]) || state.taskGraph;
      next.activeTaskId = null;
      next.recoveringTaskId = null;
      break;
    case "run.completed":
      if (next.run) next.run = { ...next.run, status: String(payload.status || "succeeded") };
      next.activeRunId = null;
      next.activeTaskId = null;
      next.recoveringTaskId = null;
      next.thinkingText = "";
      next.connection = "idle";
      next.steps = next.steps.map((s) => s.status === "running" ? { ...s, ...terminalStepState(String(payload.status || "succeeded")) } : s);
      break;
    case "run.failed":
      if (next.run) {
        next.run = {
          ...next.run,
          status: "failed",
          error_category: String(payload.category || next.run.error_category || ""),
          error_message: String(payload.message || next.run.error_message || ""),
        };
      }
      next.activeRunId = null;
      next.activeTaskId = null;
      next.recoveringTaskId = null;
      next.thinkingText = "";
      next.connection = "idle";
      if (state.finalReport?.outcome === "SUCCESS") next.finalReport = null;
      next.error = `${String(payload.message || "Agent run failed")}${payload.support_id ? ` · Support ${String(payload.support_id)}` : ""}`;
      next.steps = next.steps.map((s) => s.status === "running" ? { ...s, status: "failed", outcome: "failed" } : s);
      break;
    case "run.interrupted":
      if (next.run) {
        next.run = {
          ...next.run,
          status: "interrupted",
          error_category: String(payload.category || next.run.error_category || ""),
          error_message: String(payload.message || next.run.error_message || ""),
        };
      }
      next.activeRunId = null;
      next.activeTaskId = null;
      next.recoveringTaskId = null;
      next.thinkingText = "";
      next.connection = "idle";
      if (state.finalReport?.outcome === "SUCCESS") next.finalReport = null;
      next.error = `${String(payload.message || "Agent run interrupted")}${payload.support_id ? ` · Support ${String(payload.support_id)}` : ""}`;
      next.steps = next.steps.map((s) => s.status === "running" ? { ...s, status: "failed", outcome: "failed" } : s);
      break;
    case "run.expired":
      if (next.run) {
        next.run = {
          ...next.run,
          status: "expired",
          error_category: String(payload.category || next.run.error_category || "RunExpired"),
          error_message: String(payload.message || next.run.error_message || "The run expired while waiting for input or approval."),
        };
      }
      next.question = null;
      next.pending = null;
      next.activeRunId = null;
      next.activeTaskId = null;
      next.recoveringTaskId = null;
      next.thinkingText = "";
      next.connection = "idle";
      next.error = next.run?.error_message || "The run expired while waiting for input or approval.";
      next.steps = next.steps.map((s) => s.status === "running" ? { ...s, ...terminalStepState("expired") } : s);
      break;
    case "run.cancellation_requested":
      if (next.run) next.run = { ...next.run, status: "cancelling" };
      break;
    case "run.cancelled":
      if (next.run) next.run = { ...next.run, status: "cancelled" };
      next.finalReport = null;
      next.activeRunId = null;
      next.activeTaskId = null;
      next.recoveringTaskId = null;
      next.thinkingText = "";
      next.taskGraph = (payload.task_graph as TaskGraphNode[]) || state.taskGraph;
      next.connection = "idle";
      next.error = "";
      next.steps = next.steps.map((s) => s.status === "running" ? { ...s, ...terminalStepState("cancelled") } : s);
      break;
  }
  return next;
}

export function hydrateRun(run: AgentRun, events: ToolEvent[], priorTranscript: ChatMessage[] = []): RunViewState {
  let state: RunViewState = {
    ...emptyRunState,
    run,
    selectedRunId: run.id,
    activeRunId: ACTIVE_RUN_STATUSES.has(run.status) ? run.id : null,
    transcript: [
      ...priorTranscript,
      { role: "user", content: run.prompt, created_at: run.created_at }
    ],
    question: run.question?.status === "pending" ? run.question : null,
    taskGraph: run.task_graph || null,
    activeTaskId: run.active_task_id || null,
    inputTokens: run.input_tokens || 0,
    outputTokens: run.output_tokens || 0,
    costUsd: run.cost_usd || 0,
    connection: ACTIVE_RUN_STATUSES.has(run.status) ? "connecting" : "idle",
    error: run.error_message || "",
  };
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) state = applyEvent(state, event);
  // Event replay is newer than the initial run snapshot. Keep its task statuses
  // and only use the snapshot to fill fields that events do not carry.
  const reconciledGraph = state.taskGraph || run.task_graph || null;
  state = { ...state, taskGraph: reconciledGraph, activeTaskId: state.activeTaskId || run.active_task_id || null };
  if (TERMINAL_RUN_STATUSES.has(run.status)) {
    const terminalState = terminalStepState(run.status);
    state = {
      ...state,
      activeRunId: null,
      activeTaskId: null,
      recoveringTaskId: null,
      thinkingText: "",
      connection: "idle",
      finalReport: run.status === "cancelled" || (run.status === "failed" && state.finalReport?.outcome === "SUCCESS") ? null : state.finalReport,
      error: (run.status === "failed" || run.status === "interrupted") ? (run.error_message || state.error || "") : "",
      steps: state.steps.map((s) => s.status === "running" ? { ...s, ...terminalState } : s),
    };
  }
  return state;
}

export type RunAction =
  | { type: "hydrate"; run: AgentRun; events: ToolEvent[]; priorTranscript?: ChatMessage[] }
  | { type: "continue_run"; run: AgentRun }
  | { type: "set_transcript"; transcript: ChatMessage[] }
  | { type: "event"; event: ToolEvent }
  | { type: "connection"; connection: RunViewState["connection"] }
  | { type: "run_status"; run: AgentRun }
  | { type: "error"; error: string }
  | { type: "clear" };

export function runReducer(state: RunViewState, action: RunAction): RunViewState {
  if (action.type === "hydrate") return hydrateRun(action.run, action.events, action.priorTranscript || []);
  if (action.type === "continue_run") {
    const isTerminal = TERMINAL_RUN_STATUSES.has(action.run.status);
    const active = ACTIVE_RUN_STATUSES.has(action.run.status);
    return {
      ...state,
      run: action.run,
      selectedRunId: action.run.id,
      activeRunId: active ? action.run.id : null,
      transcript: [
        ...state.transcript,
        { role: "user", content: action.run.prompt, created_at: action.run.created_at }
      ],
      cursor: 0,
      steps: [],
      thinkingText: "",
      finalReport: null,
      question: null,
      pending: null,
      taskGraph: action.run.task_graph || null,
      activeTaskId: action.run.active_task_id || null,
      recoveringTaskId: null,
      connection: isTerminal ? "idle" : "connecting",
      error: "",
    };
  }
  if (action.type === "set_transcript") return { ...state, transcript: action.transcript };
  if (action.type === "event") return applyEvent(state, action.event);
  if (action.type === "connection") return { ...state, connection: action.connection };
  if (action.type === "error") return { ...state, error: action.error };
  if (action.type === "clear") return emptyRunState;
  const isTerminal = TERMINAL_RUN_STATUSES.has(action.run.status);
  const active = ACTIVE_RUN_STATUSES.has(action.run.status);
  const terminalState = terminalStepState(action.run.status);
  const sameRunWithEvents = state.run?.id === action.run.id && state.cursor > 0;
  return {
    ...state,
    run: action.run,
    selectedRunId: action.run.id,
    activeRunId: active ? action.run.id : null,
    question: action.run.question?.status === "pending" ? action.run.question : state.question,
    taskGraph: sameRunWithEvents ? state.taskGraph : action.run.task_graph || state.taskGraph,
    activeTaskId: isTerminal ? null : (action.run.active_task_id || null),
    recoveringTaskId: isTerminal ? null : state.recoveringTaskId,
    thinkingText: isTerminal ? "" : state.thinkingText,
    steps: isTerminal ? state.steps.map((s) => s.status === "running" ? { ...s, ...terminalState } : s) : state.steps,
    finalReport: action.run.status === "cancelled" || (action.run.status === "failed" && state.finalReport?.outcome === "SUCCESS") ? null : state.finalReport,
    connection: action.run.status.startsWith("awaiting_") ? "paused" : isTerminal ? "idle" : state.connection,
    error: (action.run.status === "failed" || action.run.status === "interrupted") ? (action.run.error_message || state.error) : "",
  };
}
