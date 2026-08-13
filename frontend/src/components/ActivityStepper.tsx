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
  ExternalLink
} from "lucide-react";
import { Step } from "@/lib/api";

interface ActivityStepperProps {
  steps: Step[];
  usage?: string;
  isStuck?: boolean;
  onOpenDiff?: () => void;
  onOpenFile?: (path: string) => void;
}

export default function ActivityStepper({
  steps = [],
  usage,
  isStuck,
  onOpenDiff,
  onOpenFile,
}: ActivityStepperProps) {
  const [expandedItems, setExpandedItems] = useState<Record<string, boolean>>({});

  if (steps.length === 0 && !usage) return null;

  const toggleExpand = (id: string) => {
    setExpandedItems((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const totalTime = steps.reduce((acc, s) => acc + (s.elapsed || 0), 0);

  // Group steps for Antigravity-style rendering
  const viewSteps = steps.filter((s) => s.tool.includes("view") || s.tool.includes("read") || s.tool.includes("inspect") || s.tool.includes("list"));
  const editSteps = steps.filter((s) => s.tool.includes("write") || s.tool.includes("patch") || s.tool.includes("replace"));
  const runSteps = steps.filter((s) => !viewSteps.includes(s) && !editSteps.includes(s));

  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      className="max-w-2xl space-y-2 font-sans"
    >
      {/* Stuck Alert */}
      {isStuck && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-800 dark:border-amber-700/40 dark:bg-amber-950/40 dark:text-amber-300">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500 animate-ping" />
          No activity for 60s — the agent may be processing a long tool task.
        </div>
      )}

      {/* 1. Worked for XXs Accordion Header */}
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
            <span>Worked for {totalTime.toFixed(1)}s</span>
            <span className="text-gray-500 font-mono text-[10px]">({steps.length} actions)</span>
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
                      {step.elapsed && <span className="text-[10px] text-gray-600">{step.elapsed.toFixed(1)}s</span>}
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {/* Individual File Edits (Edited page.tsx +1 -1 style) */}
        {editSteps.map((step, idx) => {
          const match = step.label.match(/(?:file|path)?\s*:?\s*([A-Za-z0-9_./-]+)/);
          const filepath = match ? match[1] : step.label;
          return (
            <div
              key={`edit-${idx}`}
              className="flex items-center justify-between rounded-lg border border-white/5 bg-zinc-950/80 px-3 py-1.5 text-xs text-gray-300"
            >
              <div className="flex items-center gap-2 truncate">
                <FileCode className="h-3.5 w-3.5 text-blue-400 shrink-0" />
                <span className="text-gray-400">Edited</span>
                <button
                  onClick={() => onOpenFile?.(filepath)}
                  className="font-mono text-purple-300 underline hover:text-purple-200 truncate"
                >
                  {filepath}
                </button>
              </div>
              <div className="flex items-center gap-2 shrink-0 font-mono text-[10px]">
                <span className="text-emerald-400">+1</span>
                <span className="text-red-400">-1</span>
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
