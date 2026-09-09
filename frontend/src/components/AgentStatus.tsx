"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles,
  Search,
  Database,
  LayoutTemplate,
  FileCode,
  FolderPlus,
  FolderSearch,
  Boxes,
  Rocket,
  ShieldCheck,
  Cpu,
  Loader2,
  LucideIcon,
  Terminal,
  FileText,
  FileEdit,
  FilePlus,
  Globe,
} from "lucide-react";

import { WorkspacePhase } from "@/lib/run-state";

interface AgentStatusProps {
  currentAction?: string | null;
  isThinking?: boolean;
  phase?: WorkspacePhase;
  tokenInputs?: number;
}

const actionConfig: Record<string, { label: string; icon: LucideIcon }> = {
  // ERP Specific Tools
  inspect_odoo_schema: { label: "Inspecting Odoo schema", icon: Search },
  inspect_model: { label: "Inspecting model metadata", icon: Search },
  inspect_views: { label: "Analyzing XML views", icon: LayoutTemplate },
  inspect_instance: { label: "Querying Odoo instance", icon: Database },
  inspect_company: { label: "Fetching company info", icon: Database },
  inspect_master_data: { label: "Inspecting master data", icon: Database },
  installed_modules: { label: "Reading installed modules", icon: Boxes },
  list_directory: { label: "Exploring workspace directory", icon: FolderSearch },
  read_file: { label: "Reading workspace file", icon: FileText },
  inspect_url: { label: "Inspecting connected ERP page", icon: Globe },
  browser_snapshot: { label: "Inspecting ERP UI in browser", icon: Globe },
  write_file: { label: "Writing module code", icon: FileCode },
  create_directory: { label: "Creating directory", icon: FolderPlus },
  package_module: { label: "Packaging Odoo module", icon: Boxes },
  execute_deployment: { label: "Deploying to Odoo", icon: Rocket },
  check_deployment_status: { label: "Checking deployment", icon: ShieldCheck },

  // General Agent Tools
  grep_search: { label: "Searching codebase", icon: Search },
  run_command: { label: "Running command", icon: Terminal },
  view_file: { label: "Reading file", icon: FileText },
  replace_file_content: { label: "Editing file", icon: FileEdit },
  multi_replace_file_content: { label: "Editing file", icon: FileEdit },
  write_to_file: { label: "Creating file", icon: FilePlus },
  search_web: { label: "Searching the web", icon: Globe },
  read_url_content: { label: "Reading web page", icon: Globe },
  browser_subagent: { label: "Browsing", icon: Globe },
  ask_question: { label: "Waiting for user input", icon: Cpu },
  manage_task: { label: "Managing background tasks", icon: Cpu },
};

export default function AgentStatus({ currentAction, isThinking, phase, tokenInputs = 0 }: AgentStatusProps) {
  const [elapsed, setElapsed] = useState(0);

  const isActive = Boolean(
    currentAction ||
    isThinking ||
    (phase && ["queued", "connecting", "retrying", "recovering", "cancelling"].includes(phase))
  );

  useEffect(() => {
    if (!isActive) return;
    const start = Date.now();
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 100) / 10);
    }, 100);
    return () => {
      clearInterval(interval);
      setElapsed(0);
    };
  }, [isActive]);

  if (!isActive) return null;

  let label = "Thinking & planning";
  let Icon: LucideIcon = Sparkles;

  if (phase === "queued") {
    label = "Waiting for worker";
    Icon = Loader2;
  } else if (phase === "connecting") {
    label = "Connecting to stream";
    Icon = Loader2;
  } else if (phase === "retrying") {
    label = "Reconnecting to stream";
    Icon = Loader2;
  } else if (phase === "recovering") {
    label = "Recovering stale task";
    Icon = Loader2;
  } else if (phase === "cancelling") {
    label = "Cancelling run";
    Icon = Loader2;
  } else if (currentAction) {
    const config = actionConfig[currentAction];
    if (config) {
      label = config.label;
      Icon = config.icon;
    } else {
      label = `Running ${currentAction.replace(/_/g, " ")}`;
      Icon = Cpu;
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
    <motion.div
      initial={{ opacity: 0, y: 4, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -4, scale: 0.98 }}
      transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
      className="inline-flex items-center gap-2.5 rounded-full border border-blue-500/20 bg-blue-50/80 px-3.5 py-1.5 text-xs font-medium text-blue-700 shadow-sm backdrop-blur-md dark:border-blue-400/20 dark:bg-blue-950/40 dark:text-blue-300"
    >
      {/* Live animated Icon / Spinner */}
      <div className="relative flex h-4 w-4 items-center justify-center">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentAction || "thinking"}
            initial={{ scale: 0.7, opacity: 0, rotate: -20 }}
            animate={{ scale: 1, opacity: 1, rotate: 0 }}
            exit={{ scale: 0.7, opacity: 0, rotate: 20 }}
            transition={{ duration: 0.15 }}
            className="absolute"
          >
            {currentAction ? (
              <Icon className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
            ) : (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600 dark:text-blue-400" />
            )}
          </motion.div>
        </AnimatePresence>

        {/* Breathing glowing ring */}
        <motion.div
          animate={{ scale: [1, 1.4, 1], opacity: [0.4, 0, 0.4] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
          className="absolute inset-0 rounded-full bg-blue-500/30"
        />
      </div>

      {/* Dynamic Label */}
      <div className="flex items-center">
        <AnimatePresence mode="wait">
          <motion.span
            key={label}
            initial={{ opacity: 0, x: -4 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 4 }}
            transition={{ duration: 0.15 }}
          >
            {label}
          </motion.span>
        </AnimatePresence>

        {/* Animated pulsating dots */}
        <span className="ml-0.5 inline-flex w-3.5">
          <motion.span
            animate={{ opacity: [0, 1, 0] }}
            transition={{ duration: 1.2, repeat: Infinity, delay: 0 }}
          >
            .
          </motion.span>
          <motion.span
            animate={{ opacity: [0, 1, 0] }}
            transition={{ duration: 1.2, repeat: Infinity, delay: 0.2 }}
          >
            .
          </motion.span>
          <motion.span
            animate={{ opacity: [0, 1, 0] }}
            transition={{ duration: 1.2, repeat: Infinity, delay: 0.4 }}
          >
            .
          </motion.span>
        </span>
      </div>

      {/* Elapsed seconds badge */}
      {elapsed > 0 && (
        <span className="rounded-md bg-blue-100/80 px-1.5 py-0.5 font-mono text-[10px] text-blue-600 dark:bg-blue-900/50 dark:text-blue-300">
          {elapsed.toFixed(1)}s
        </span>
      )}
    </motion.div>

    {/* Exact usage only; context limits vary by configured model. */}
    {tokenInputs > 0 && (
      <span className="font-mono text-[10px] text-gray-500 dark:text-gray-400">{tokenInputs.toLocaleString()} input tokens</span>
    )}
    </div>
  );
}
