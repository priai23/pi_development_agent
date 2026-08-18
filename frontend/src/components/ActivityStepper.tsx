"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronDown,
  ChevronRight,
  FileCode,
  Terminal,
  CheckCircle2,
  XCircle,
  Loader2,
  FileSearch,
  ExternalLink,
  ListTodo,
  Circle,
  RefreshCw,
  Network,
  AlertCircle,
} from "lucide-react";
import { Step } from "@/lib/api";

type TaskGraphNode = {
  task_id: string;
  title: string;
  status: string;
  risk_class: number;
  retry_count: number;
  max_retries?: number;
  heartbeat_at: string | null;
  result?: { outcome: string; done: string[]; verification: string; errors: string } | null;
};

interface ActivityStepperProps {
  steps: Step[];
  usage?: string;
  supervisorTaskGraph?: TaskGraphNode[] | null;
  activeTaskId?: string | null;
  recoveringTaskId?: string | null;
  onOpenDiff?: () => void;
  onOpenFile?: (path: string) => void;
  planItems?: Array<{ title: string; status: string }>;
}

export default function ActivityStepper({
  steps = [],
  usage,
  supervisorTaskGraph,
  activeTaskId,
  recoveringTaskId,
  onOpenDiff,
  onOpenFile,
  planItems: durablePlanItems = [],
}: ActivityStepperProps) {
  const [expandedItems, setExpandedItems] = useState<Record<string, boolean>>({});

  // Filter out thinking and plan updates from the generic activity stepper
  const cleanSteps = steps.filter(
    (s) => s.tool !== "emit_thinking" && s.tool !== "thinking" && s.tool !== "update_task_plan" && s.tool !== "plan.updated"
  );

  if (cleanSteps.length === 0 && !usage && !supervisorTaskGraph?.length && !durablePlanItems.length) return null;

  const toggleExpand = (id: string) => {
    setExpandedItems((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const totalTime = cleanSteps.reduce((acc, s) => acc + (s.elapsed || 0), 0);

  const viewSteps = cleanSteps.filter((s) => s.category === "inspect" || (!s.category && (s.tool.includes("view") || s.tool.includes("read") || s.tool.includes("inspect") || s.tool.includes("list"))));
  const editSteps = cleanSteps.filter((s) => s.tool === "write_file" || s.tool === "patch_file" || (s.category === "edit" && !s.tool.includes("directory") && !s.tool.includes("mkdir")));
  const verifySteps = cleanSteps.filter((s) => s.category === "verify" || (!s.category && s.tool.includes("verify")));
  const runSteps = cleanSteps.filter((s) => !viewSteps.includes(s) && !editSteps.includes(s) && !verifySteps.includes(s));
  const verificationFailed = verifySteps.some((step) => step.status === "failed" || step.outcome === "failed");
  const verificationRunning = verifySteps.some((step) => step.status === "running");
  const verificationPassed = verifySteps.length > 0 && verifySteps.every((step) => step.status === "done" && step.outcome === "succeeded");

  // Extract live task plan checklist if available
  const planStep = [...steps].reverse().find((s) => s.tool === "update_task_plan" || s.tool === "plan.updated");
  let planItems: Array<{ title: string; status: string }> = durablePlanItems;
  if (planStep?.result) {
    try {
      const parsed = JSON.parse(planStep.result);
      if (Array.isArray(parsed.items)) planItems = parsed.items;
      else if (Array.isArray(parsed)) planItems = parsed;
    } catch {
      // ignore
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      className="max-w-2xl space-y-2.5 font-sans"
    >
      {/* ── A2A Supervisor Task Graph Card ─────────────────────────────────── */}
      <AnimatePresence>
        {supervisorTaskGraph && supervisorTaskGraph.length > 0 && (
          <motion.div
            key="supervisor-graph"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="rounded-xl border border-indigo-500/30 bg-indigo-950/25 p-3.5 space-y-2.5 backdrop-blur-md shadow-lg shadow-indigo-950/20"
          >
            {/* Header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-semibold text-indigo-200 text-xs">
                <Network className="h-3.5 w-3.5 text-indigo-400" />
                <span>Supervisor Task Graph</span>
              </div>
              <span className="font-mono text-[10px] text-indigo-300 bg-indigo-500/20 px-2 py-0.5 rounded-full border border-indigo-500/30">
                {supervisorTaskGraph.filter(t => t.status === "done").length}/{supervisorTaskGraph.length} done
              </span>
            </div>

            {/* Recovery Alert */}
            <AnimatePresence>
              {recoveringTaskId && (
                <motion.div
                  key="recovering"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  className="flex items-center gap-2 rounded-lg border border-amber-400/40 bg-amber-900/30 px-3 py-1.5 text-[11px] font-medium text-amber-200"
                >
                  <RefreshCw className="h-3 w-3 animate-spin text-amber-400" />
                  Recovering sub-task —{" "}
                  {supervisorTaskGraph.find(t => t.task_id === recoveringTaskId)?.title ?? recoveringTaskId}
                </motion.div>
              )}
            </AnimatePresence>

            {/* Task list */}
            <ul className="space-y-1.5 pl-0.5">
              {supervisorTaskGraph.map((task) => {
                const isDone = task.status === "done";
                const isFailed = task.status === "failed";
                const isCancelled = task.status === "cancelled";
                const isActive = task.task_id === activeTaskId && task.status === "in_progress";
                const isRecovering = task.task_id === recoveringTaskId;
                const riskColor = task.risk_class === 3 ? "text-red-400" : task.risk_class === 2 ? "text-amber-400" : "text-emerald-400";
                return (
                  <li key={task.task_id} className="space-y-1">
                    <div className="flex items-center gap-2.5">
                    {isDone ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                    ) : isFailed ? (
                      <AlertCircle className="h-3.5 w-3.5 text-red-400 shrink-0" />
                    ) : isCancelled ? (
                      <XCircle className="h-3.5 w-3.5 text-gray-500 shrink-0" />
                    ) : isActive ? (
                      <div className="relative h-3.5 w-3.5 shrink-0">
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400" />
                        {/* live heartbeat ring */}
                        <span className="absolute -inset-0.5 rounded-full border border-indigo-400 animate-ping opacity-60" />
                      </div>
                    ) : isRecovering ? (
                      <RefreshCw className="h-3.5 w-3.5 text-amber-400 shrink-0 animate-spin" />
                    ) : (
                      <Circle className="h-3.5 w-3.5 text-indigo-800/60 shrink-0" />
                    )}
                    <span className={`text-xs ${
                      isDone ? "line-through text-gray-600" :
                      isFailed ? "text-red-400 font-semibold" :
                      isCancelled ? "text-gray-500 line-through" :
                      isActive ? "text-indigo-200 font-semibold" :
                      isRecovering ? "text-amber-300 font-semibold" :
                      "text-gray-500"
                    }`}>
                      {task.title}
                    </span>
                    {/* Risk class pill */}
                    <span className={`ml-auto text-[9px] font-mono shrink-0 ${riskColor} opacity-70`}>
                      C{task.risk_class}
                    </span>
                    {/* Retry count badge */}
                    {task.retry_count > 0 && (
                      <span className="text-[9px] font-mono text-amber-400 bg-amber-900/40 border border-amber-500/30 rounded px-1">
                        retry {task.retry_count}/{task.max_retries || 2}
                      </span>
                    )}
                    </div>
                    {task.result && (
                      <div className={`ml-6 rounded border px-2 py-1.5 text-[10px] ${task.result.outcome === "SUCCESS" ? "border-emerald-500/20 bg-emerald-950/20 text-emerald-200" : "border-red-500/20 bg-red-950/20 text-red-200"}`}>
                        <div className="font-semibold">Task report · {task.result.outcome}</div>
                        {task.result.verification && <div className="mt-1 text-gray-400">{task.result.verification}</div>}
                        {task.result.errors && <div className="mt-1 whitespace-pre-wrap text-red-300">{task.result.errors}</div>}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Persistent Agent Plan Checklist Card */}
      {planItems.length > 0 && (
        <div className="rounded-xl border border-purple-500/30 bg-purple-950/20 p-3.5 text-xs space-y-2.5 backdrop-blur-md shadow-lg shadow-purple-950/20">
          <div className="flex items-center justify-between font-semibold text-purple-200">
            <div className="flex items-center gap-2">
              <ListTodo className="h-4 w-4 text-purple-400" />
              <span>Agent Execution Plan</span>
            </div>
            <span className="font-mono text-[11px] text-purple-300 bg-purple-500/20 px-2 py-0.5 rounded-full border border-purple-500/30">
              {planItems.filter((i) => i.status === "completed").length}/{planItems.length} completed
            </span>
          </div>
          <ul className="space-y-1.5 pl-0.5">
            {planItems.map((item, i) => {
              const isDone = item.status === "completed";
              const isRunning = item.status === "in_progress";
              return (
                <li key={i} className="flex items-center gap-2.5 text-gray-300">
                  {isDone ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                  ) : isRunning ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-purple-400 shrink-0" />
                  ) : (
                    <Circle className="h-3.5 w-3.5 text-gray-600 shrink-0" />
                  )}
                  <span className={`text-xs ${isDone ? "line-through text-gray-500" : isRunning ? "font-semibold text-purple-200" : "text-gray-400"}`}>
                    {item.title}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Dedicated Module Verification Evidence Badge */}
      {verifySteps.length > 0 && (
        <div className={`rounded-xl border p-3 text-xs space-y-2 backdrop-blur-md ${
          verificationFailed ? "border-red-500/30 bg-red-950/20" :
          verificationRunning ? "border-blue-500/30 bg-blue-950/20" :
          verificationPassed ? "border-emerald-500/30 bg-emerald-950/20" :
          "border-gray-500/30 bg-zinc-950/40"
        }`}>
          <div className={`flex items-center justify-between font-semibold ${verificationFailed ? "text-red-300" : verificationRunning ? "text-blue-300" : verificationPassed ? "text-emerald-300" : "text-gray-300"}`}>
            <div className="flex items-center gap-2">
              {verificationFailed ? <XCircle className="h-4 w-4" /> : verificationRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : verificationPassed ? <CheckCircle2 className="h-4 w-4" /> : <Circle className="h-4 w-4" />}
              <span>Verification Evidence</span>
            </div>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-mono border ${
              verificationFailed ? "border-red-500/30 bg-red-500/20 text-red-300" :
              verificationRunning ? "border-blue-500/30 bg-blue-500/20 text-blue-300" :
              verificationPassed ? "border-emerald-500/30 bg-emerald-500/20 text-emerald-300" :
              "border-gray-500/30 bg-gray-500/20 text-gray-300"
            }`}>
              {verificationFailed ? "FAILED" : verificationRunning ? "RUNNING" : verificationPassed ? "PASSED" : "RESULT"}
            </span>
          </div>
          {verifySteps.map((vStep, i) => (
            <div key={i} className={`text-xs text-gray-300 pl-3 border-l-2 space-y-1 font-mono ${vStep.outcome === "failed" || vStep.status === "failed" ? "border-red-500/40" : vStep.status === "running" ? "border-blue-500/40" : vStep.outcome === "succeeded" ? "border-emerald-500/40" : "border-gray-500/40"}`}>
              <p className="text-[11px] text-gray-200">{vStep.label}</p>
              {vStep.result && <p className="text-[10px] text-gray-400 truncate">{vStep.result}</p>}
            </div>
          ))}
        </div>
      )}

      {/* Worked for XXs Accordion Header */}
      {totalTime > 0 && (
        <div className="flex items-center justify-between rounded-xl border border-white/10 bg-zinc-900/80 px-3.5 py-2 text-xs backdrop-blur-md">
          <button
            onClick={() => toggleExpand("summary")}
            className="flex items-center gap-2 font-medium text-gray-300 hover:text-white transition"
          >
            {expandedItems["summary"] ? (
              <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-gray-400" />
            )}
            <span>Worked for {totalTime > 0 ? `${totalTime.toFixed(1)}s` : "0.4s"}</span>
            <span className="text-gray-500 font-mono text-[10px]">({cleanSteps.length} actions)</span>
          </button>
          {onOpenDiff && (
            <button
              onClick={onOpenDiff}
              className="flex items-center gap-1 rounded bg-white/10 px-2.5 py-1 text-[11px] font-medium text-gray-200 transition hover:bg-white/20 hover:text-white active:scale-95"
            >
              <ExternalLink className="h-3 w-3 text-blue-400" />
              Review Changes
            </button>
          )}
        </div>
      )}

      {/* Detailed Action Steps */}
      <div className="space-y-1.5 pl-1">
        {/* Grouped Explored Files */}
        {viewSteps.length > 0 && (
          <div className="rounded-lg border border-white/5 bg-zinc-950/60 p-2 text-xs">
            <button
              onClick={() => toggleExpand("explored")}
              className="flex items-center gap-2 w-full text-left font-medium text-gray-300 hover:text-white"
            >
              {expandedItems["explored"] ? (
                <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 text-gray-400" />
              )}
              <FileSearch className="h-3.5 w-3.5 text-purple-400" />
              <span>Explored {viewSteps.length} file{viewSteps.length > 1 ? "s" : ""}</span>
            </button>
            <AnimatePresence>
              {expandedItems["explored"] && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="mt-2 space-y-1 pl-5 border-l border-white/10"
                >
                  {viewSteps.map((step, idx) => (
                    <div key={idx} className="flex items-center justify-between text-[11px] text-gray-400 font-mono">
                      <span className="truncate">{step.label}</span>
                      {step.elapsed != null && <span className="text-[10px] text-gray-600">{step.elapsed.toFixed(1)}s</span>}
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {/* Individual File Edits (Edited models/mrp_production.py +18 -0 style) */}
        {editSteps.map((step, idx) => {
          const diffMatch = step.result?.match(/\(\+(\d+)\s+-\s*(\d+)\)/) || step.label.match(/\(\+(\d+)\s+-\s*(\d+)\)/);
          const plusLines = diffMatch ? diffMatch[1] : "1";
          const minusLines = diffMatch ? diffMatch[2] : "0";

          const pathMatch = step.result?.match(/(?:Wrote|Patched)\s+([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)/) || step.label.match(/(?:file|path)?\s*:?\s*([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)/);
          const rawPath = pathMatch ? pathMatch[1] : step.label.replace(/^write_file\s*/, "").replace(/^patch_file\s*/, "");
          const filepath = rawPath.includes(".") ? rawPath : "";
          if (!filepath) return null;

          return (
            <div
              key={`edit-${idx}`}
              className="flex items-center justify-between rounded-lg border border-white/5 bg-zinc-950/80 px-3 py-1.5 text-xs text-gray-300"
            >
              <div className="flex items-center gap-2 truncate">
                <FileCode className="h-3.5 w-3.5 text-blue-400 shrink-0" />
                <span className="text-gray-400 font-medium">Edited</span>
                <button
                  onClick={() => onOpenFile?.(filepath)}
                  className="font-mono text-purple-300 underline hover:text-purple-200 truncate"
                >
                  {filepath}
                </button>
              </div>
              <div className="flex items-center gap-2 shrink-0 font-mono text-[10px]">
                <span className="text-emerald-400 font-semibold">+{plusLines}</span>
                <span className="text-red-400 font-semibold">-{minusLines}</span>
                {step.elapsed != null && <span className="text-gray-500 font-mono">{step.elapsed.toFixed(1)}s</span>}
              </div>
            </div>
          );
        })}

        {/* Command Executions (Ran npm run lint ↺ v style) */}
        {runSteps.map((step, idx) => {
          const itemId = `run-${idx}`;
          return (
            <div key={itemId} className="rounded-lg border border-white/5 bg-zinc-950/80 p-2 text-xs">
              <button
                onClick={() => toggleExpand(itemId)}
                className="flex items-center justify-between w-full text-left font-medium text-gray-300 hover:text-white"
              >
                <div className="flex items-center gap-2 truncate">
                  {step.status === "done" ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                  ) : step.status === "failed" ? (
                    <XCircle className="h-3.5 w-3.5 text-red-400 shrink-0" />
                  ) : (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400 shrink-0" />
                  )}
                  <Terminal className="h-3.5 w-3.5 text-amber-400 shrink-0" />
                  <span className="truncate">Ran <code className="font-mono text-amber-300">{step.label}</code></span>
                </div>
                <div className="flex items-center gap-1.5 shrink-0 text-gray-500 font-mono text-[10px]">
                  {step.elapsed != null && <span>{step.elapsed.toFixed(1)}s</span>}
                  {expandedItems[itemId] ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                </div>
              </button>

              <AnimatePresence>
                {expandedItems[itemId] && step.result && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="mt-2"
                  >
                    <pre className="max-h-36 overflow-auto rounded bg-black/90 p-2.5 font-mono text-[11px] text-gray-300 border border-white/10 whitespace-pre-wrap">
                      {step.result}
                    </pre>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}
