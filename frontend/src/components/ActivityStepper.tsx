"use client";

import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, XCircle, Loader2, Coins } from "lucide-react";
import { Step } from "@/lib/api";

interface ActivityStepperProps {
  steps: Step[];
  usage?: string;
  isStuck?: boolean; // heartbeat timeout exceeded
}

export default function ActivityStepper({ steps = [], usage, isStuck }: ActivityStepperProps) {
  if (steps.length === 0 && !usage) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      className="max-w-2xl rounded-2xl border border-gray-200/80 bg-white/70 shadow-sm backdrop-blur-lg dark:border-white/10 dark:bg-zinc-900/60"
    >
      {isStuck && (
        <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-800 dark:border-amber-700/40 dark:bg-amber-950/40 dark:text-amber-300">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
          No activity for 30s — the agent may be stuck.
        </div>
      )}

      <div className="divide-y divide-gray-100 dark:divide-white/5">
        <AnimatePresence initial={false}>
          {steps.map((step, index) => (
            <motion.div
              key={`${step.tool}-${index}`}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.15 }}
              className="flex items-start gap-3 px-4 py-2.5 text-xs"
            >
              {/* Status icon */}
              <span className="mt-0.5 shrink-0">
                {step.status === "done" ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                ) : step.status === "failed" ? (
                  <XCircle className="h-3.5 w-3.5 text-red-500" />
                ) : (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />
                )}
              </span>

              {/* Label + result */}
              <span className="min-w-0 flex-1">
                <span className={step.status === "done" ? "text-gray-700 dark:text-gray-300" : step.status === "failed" ? "text-red-600 dark:text-red-400" : "font-medium text-blue-700 dark:text-blue-300"}>
                  {step.label}
                </span>
                {step.result && (
                  <span className="ml-2 truncate font-mono text-[10px] text-gray-400 dark:text-gray-500">
                    {step.result}
                  </span>
                )}
              </span>

              {/* Elapsed badge */}
              {step.elapsed != null && step.elapsed > 0 && (
                <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500 dark:bg-white/10 dark:text-gray-400">
                  {step.elapsed.toFixed(1)}s
                </span>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {usage && (
        <div className="flex items-center gap-1.5 border-t border-gray-100 px-4 py-2 text-[11px] text-gray-500 dark:border-white/5 dark:text-gray-400">
          <Coins className="h-3.5 w-3.5 text-amber-500" />
          <span>{usage}</span>
        </div>
      )}
    </motion.div>
  );
}
