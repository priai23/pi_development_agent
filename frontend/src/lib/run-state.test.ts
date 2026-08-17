import { describe, expect, it } from "vitest";
import { AgentRun, ToolEvent } from "./api";
import { emptyRunState, getWorkspacePhase, hydrateRun, runReducer, RunViewState } from "./run-state";

const run: AgentRun = { id: "run-1", project_id: 1, status: "awaiting_question", prompt: "Build it", support_id: "support", error_message: null, retryable: false, cost_usd: 0, created_at: "2026-01-01T00:00:00Z" };
const event = (sequence: number, event_type: string, payload: Record<string, unknown>): ToolEvent => ({ id: sequence, run_id: run.id, sequence, event_type, payload, created_at: `2026-01-01T00:00:0${sequence}Z` });

describe("durable run reducer", () => {
  it("hydrates transcript, failed checks, task graph, and a pending question", () => {
    const state = hydrateRun(run, [
      event(1, "supervisor.plan", { task_graph: [{ task_id: "t1", title: "Test", status: "in_progress", risk_class: 1, retry_count: 0, heartbeat_at: null }], active_task_id: "t1" }),
      event(2, "message.delta", { text: "Working" }),
      event(3, "tool.started", { tool: "run_project_check", category: "verify" }),
      event(4, "tool.completed", { tool: "run_project_check", outcome: "failed", result: '{"exit_code":1}' }),
      event(5, "question.required", { question_id: "q1", question: "Which module?", options: ["sales"] }),
    ]);
    expect(state.activeRunId).toBe("run-1");
    expect(state.transcript.map((message) => message.content)).toEqual(["Build it", "Working"]);
    expect(state.steps[0].status).toBe("failed");
    expect(state.question?.run_id).toBe("run-1");
    expect(state.taskGraph?.[0].task_id).toBe("t1");
  });

  it("keeps the run active while paused and clears it only when terminal", () => {
    const state = hydrateRun(run, []);
    expect(runReducer(state, { type: "run_status", run: { ...run, status: "queued" } }).activeRunId).toBe("run-1");
    expect(runReducer(state, { type: "run_status", run: { ...run, status: "succeeded" } }).activeRunId).toBeNull();
  });

  it("deduplicates replayed sequences", () => {
    const state = hydrateRun(run, [event(1, "message.delta", { text: "Once" })]);
    expect(runReducer(state, { type: "event", event: event(1, "message.delta", { text: "Twice" }) }).transcript[1].content).toBe("Once");
  });

  it("keeps task reports on their task and uses cumulative usage", () => {
    const withGraph = hydrateRun({ ...run, task_graph: [{ task_id: "t1", title: "Inspect", status: "in_progress", risk_class: 1, retry_count: 0, heartbeat_at: null }] }, [
      event(1, "task.report", { task_id: "t1", outcome: "SUCCESS", done: ["Inspected"], verification: "Models found", errors: "" }),
      event(2, "usage", { attempt: { input_tokens: 5 }, cumulative: { input_tokens: 25, output_tokens: 7, cost_usd: 0.2 } }),
    ]);
    expect(withGraph.taskGraph?.[0].result?.verification).toBe("Models found");
    expect(withGraph.finalReport).toBeNull();
    expect(withGraph.inputTokens).toBe(25);
  });

  it("authoritative cancellation removes stale success while retaining evidence", () => {
    const cancelled = hydrateRun({ ...run, status: "cancelled", active_task_id: null }, [
      event(1, "final_report", { outcome: "SUCCESS", done: ["Old report"], verification: "none", errors: "" }),
      event(2, "message.delta", { text: "Partial evidence" }),
    ]);
    expect(cancelled.finalReport).toBeNull();
    expect(cancelled.activeRunId).toBeNull();
    expect(cancelled.transcript.at(-1)?.content).toBe("Partial evidence");
  });

  it("cleans up running tools and thinking text on terminal run.failed and run.completed events", () => {
    const activeState: RunViewState = {
      ...emptyRunState,
      run: { ...run, status: "running" },
      activeRunId: "run-1",
      steps: [{ tool: "run_command", label: "running command", status: "running", startedAt: Date.now() }],
      thinkingText: "Planning next module install",
      connection: "connected",
    };

    const failed = runReducer(activeState, {
      type: "event",
      event: event(1, "run.failed", { category: "WorkerUnavailable", message: "Worker offline", support_id: "sup-99" }),
    });
    expect(failed.run?.status).toBe("failed");
    expect(failed.steps[0].status).toBe("failed");
    expect(failed.thinkingText).toBe("");
    expect(failed.activeRunId).toBeNull();
    expect(failed.connection).toBe("idle");
    expect(failed.error).toContain("Worker offline · Support sup-99");

    const completed = runReducer(activeState, {
      type: "event",
      event: event(1, "run.completed", { status: "succeeded" }),
    });
    expect(completed.run?.status).toBe("succeeded");
    expect(completed.steps[0].status).toBe("done");
    expect(completed.thinkingText).toBe("");
    expect(completed.activeRunId).toBeNull();
    expect(completed.connection).toBe("idle");
  });

  it("computes truthful workspace phases", () => {
    expect(getWorkspacePhase(emptyRunState, false)).toBe("idle");
    expect(getWorkspacePhase(emptyRunState, true)).toBe("submitting");

    const queuedState: RunViewState = { ...emptyRunState, run: { ...run, status: "queued" } };
    expect(getWorkspacePhase(queuedState)).toBe("queued");

    const retryingState: RunViewState = { ...emptyRunState, run: { ...run, status: "running" }, connection: "retrying" };
    expect(getWorkspacePhase(retryingState)).toBe("retrying");

    const recoveringState: RunViewState = { ...emptyRunState, run: { ...run, status: "running" }, recoveringTaskId: "t2" };
    expect(getWorkspacePhase(recoveringState)).toBe("recovering");

    const executingState: RunViewState = {
      ...emptyRunState,
      run: { ...run, status: "running" },
      steps: [{ tool: "view_file", label: "reading file", status: "running", startedAt: Date.now() }],
    };
    expect(getWorkspacePhase(executingState)).toBe("executing");

    const thinkingState: RunViewState = { ...emptyRunState, run: { ...run, status: "running" }, thinkingText: "Thinking..." };
    expect(getWorkspacePhase(thinkingState)).toBe("thinking");

    const interruptedState: RunViewState = { ...emptyRunState, run: { ...run, status: "interrupted" } };
    expect(getWorkspacePhase(interruptedState)).toBe("interrupted");
  });
});
