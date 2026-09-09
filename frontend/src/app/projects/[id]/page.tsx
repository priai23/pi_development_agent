"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Bot, Brain, ChevronDown, ChevronUp, Code2, Database, FileText, GitBranch, HelpCircle, History, Image as ImageIcon, Layers, Lightbulb, Loader2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Rocket, RotateCcw, Search, Send, Server, Shield, SlidersHorizontal, Sparkles, Square, Terminal } from "lucide-react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import MessageContent from "@/components/MessageContent";
import AgentStatus from "@/components/AgentStatus";
import ActivityStepper from "@/components/ActivityStepper";
import LearnedMemories from "@/components/LearnedMemories";
import CodeDiffViewer from "@/components/CodeDiffViewer";
import ApprovalCard from "@/components/ApprovalCard";
import WorkspaceFileTree from "@/components/WorkspaceFileTree";
import RunHistoryDialog from "@/components/RunHistoryDialog";
import { apiFetch, AgentRun, AgentSchedule, AgentSubtask, Artifact, ChatMessage, deleteRun, deleteThread, Deployment, fetchThreadTranscript, followRun, Instance, Project, TerminalSession, ToolEvent, WorkspaceEntry, WorkspaceSearchResult, WorkspaceStatus } from "@/lib/api";
import { ACTIVE_RUN_STATUSES, getWorkspacePhase, WorkspacePhase } from "@/lib/run-state";
import { useRunController } from "@/hooks/useRunController";

export default function ProjectWorkspace() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { state: runState, hydrate: hydrateRun, continueRun, receive: receiveRunEvent, setConnection, setRun, setError: setRunError, clear: clearRun } = useRunController();
  const [project, setProject] = useState<Project | null>(null);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [message, setMessage] = useState("");
  const [attachments, setAttachments] = useState<{ name: string; type: string; content: string; text?: string }[]>([]);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const attachmentInput = useRef<HTMLInputElement>(null);
  const [bootLoading, setBootLoading] = useState(true);
  const [connectingInstance, setConnectingInstance] = useState(false);
  const [submittingRun, setSubmittingRun] = useState(false);
  const [pageError, setPageError] = useState("");
  const [workspaceError, setWorkspaceError] = useState("");
  const [streamAttempt, setStreamAttempt] = useState(0);
  const [url, setUrl] = useState("");
  const [dbName, setDbName] = useState("");
  const [detectedDatabases, setDetectedDatabases] = useState<string[]>([]);
  const [detectingDatabases, setDetectingDatabases] = useState(false);
  const [discoveryMessage, setDiscoveryMessage] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [authMethod] = useState<"json2" | "xmlrpc">("xmlrpc");
  const [deciding, setDeciding] = useState(false);
  const [diagnosticExpanded, setDiagnosticExpanded] = useState(false);
  const [questionAnswer, setQuestionAnswer] = useState("");
  const [submittingAnswer, setSubmittingAnswer] = useState(false);
  const [showLeftSidebar, setShowLeftSidebar] = useState(false);
  const [showRightPanel, setShowRightPanel] = useState(false);
  const [rightPanelWidth, setRightPanelWidth] = useState(520);
  const [leftPanelWidth, setLeftPanelWidth] = useState(256);
  const [isResizingLeft, setIsResizingLeft] = useState(false);
  const [isResizingRight, setIsResizingRight] = useState(false);
  const [rightPanelTab, setRightPanelTab] = useState<"code" | "diff" | "terminal" | "memory" | "evidence">("code");
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [workspaceSearchQuery, setWorkspaceSearchQuery] = useState("");
  const [workspaceSearchResults, setWorkspaceSearchResults] = useState<WorkspaceSearchResult[]>([]);
  const [selectedFile, setSelectedFile] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [diffContent, setDiffContent] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState<WorkspaceStatus | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [terminalSessions, setTerminalSessions] = useState<TerminalSession[]>([]);
  const [terminalCommand, setTerminalCommand] = useState("");
  const [terminalCwd, setTerminalCwd] = useState(".");
  const [terminalSubmitting, setTerminalSubmitting] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<"local" | "isolated">("local");
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [subtasks, setSubtasks] = useState<AgentSubtask[]>([]);
  const [schedules, setSchedules] = useState<AgentSchedule[]>([]);
  const [schedulePrompt, setSchedulePrompt] = useState("");
  const [scheduleInterval, setScheduleInterval] = useState("3600");
  const [scheduleSubmitting, setScheduleSubmitting] = useState(false);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [deployMsg, setDeployMsg] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const discoveryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const discoverySequence = useRef(0);
  const streamAbortRef = useRef<AbortController | null>(null);
  const streamCursorRef = useRef(0);
  const selectedRunIdRef = useRef<string | null>(null);

  const loadSubtasks = useCallback(async (runId: string) => {
    try { setSubtasks(await apiFetch<AgentSubtask[]>(`/runs/${runId}/subtasks`)); } catch { /* child-agent telemetry is best effort */ }
  }, []);

  const loadSchedules = useCallback(async () => {
    try { setSchedules(await apiFetch<AgentSchedule[]>(`/projects/${projectId}/schedules`)); } catch { /* scheduling is optional for older deployments */ }
  }, [projectId]);

  const phase: WorkspacePhase = getWorkspacePhase(runState, submittingRun);
  const chat = runState.transcript;
  const pending = runState.pending?.status === "pending" ? runState.pending : null;
  const activeRunId = runState.activeRunId;
  const steps = runState.steps;
  const tokenInputs = runState.inputTokens;
  const usage = runState.inputTokens || runState.outputTokens || runState.costUsd
    ? `${runState.inputTokens} input · ${runState.outputTokens} output tokens · $${runState.costUsd.toFixed(4)}` : "";
  const currentTool = [...steps].reverse().find((step) => step.status === "running")?.tool || null;
  const runWorking = ["queued", "connecting", "retrying", "thinking", "executing", "recovering", "cancelling"].includes(phase);
  const isThinking = (phase === "thinking" || phase === "executing") && !currentTool;
  const canStop = ["queued", "connecting", "retrying", "thinking", "executing", "recovering"].includes(phase) && Boolean(runState.run?.id);
  const isInteractive = ["idle", "succeeded", "failed", "cancelled", "interrupted"].includes(phase) && !pending;
  const loading = bootLoading || connectingInstance || submittingRun || runWorking;
  const error = pageError || runState.error;
  const agentQuestion = runState.question?.status === "pending" ? runState.question : null;
  const finalReport = runState.finalReport;
  const thinkingText = runState.thinkingText;
  const supervisorTaskGraph = runState.taskGraph;
  const activeTaskId = runState.activeTaskId;
  const recoveringTaskId = runState.recoveringTaskId;
  const runStatus = runState.run?.status;
  const compactIdePanel = rightPanelWidth < 430;

  useEffect(() => { streamCursorRef.current = runState.cursor; }, [runState.cursor]);
  useEffect(() => { selectedRunIdRef.current = runState.selectedRunId; }, [runState.selectedRunId]);

  // Restore & save panel layout preferences in localStorage
  useEffect(() => {
    const savedSidebar = localStorage.getItem("workspace:showLeftSidebar");
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (savedSidebar !== null) setShowLeftSidebar(savedSidebar === "true");
    else setShowLeftSidebar(window.innerWidth >= 1280);
    setShowRightPanel(window.innerWidth >= 1440);
    const savedWidth = localStorage.getItem("workspace:rightPanelWidth");
    if (savedWidth !== null) {
      const parsed = Number(savedWidth);
      if (parsed >= 320 && parsed <= window.innerWidth * 0.75) setRightPanelWidth(parsed);
    }
    const savedLeftWidth = localStorage.getItem("workspace:leftPanelWidth");
    if (savedLeftWidth !== null) setLeftPanelWidth(Math.min(360, Math.max(64, Number(savedLeftWidth))));
  }, []);

  const toggleLeftSidebar = () => {
    setShowLeftSidebar((prev) => {
      const next = !prev;
      localStorage.setItem("workspace:showLeftSidebar", String(next));
      return next;
    });
  };

  useEffect(() => {
    if (!isResizingLeft) return;
    const move = (event: PointerEvent) => {
      const width = Math.min(360, Math.max(64, event.clientX));
      setLeftPanelWidth(width); localStorage.setItem("workspace:leftPanelWidth", String(width));
    };
    const stop = () => setIsResizingLeft(false);
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
  }, [isResizingLeft]);

  // Mouse drag handler for dynamic right panel width resizing
  useEffect(() => {
    if (!isResizingRight) return;
    const handlePointerMove = (e: PointerEvent) => {
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth >= 320 && newWidth <= window.innerWidth * 0.75) {
        setRightPanelWidth(newWidth);
        localStorage.setItem("workspace:rightPanelWidth", String(newWidth));
      }
    };
    const handlePointerUp = () => {
      setIsResizingRight(false);
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, [isResizingRight]);

  const loadWorkspaceDetails = useCallback(async () => {
    setWorkspaceError("");
    const runQuery = selectedRunIdRef.current ? `?run_id=${encodeURIComponent(selectedRunIdRef.current)}` : "";
    const results = await Promise.allSettled([
      apiFetch<WorkspaceEntry[]>(`/projects/${projectId}/workspace/tree${runQuery}`),
      apiFetch<{ diff: string }>(`/projects/${projectId}/workspace/diff${runQuery}`),
      apiFetch<WorkspaceStatus>(`/projects/${projectId}/workspace/status${runQuery}`),
      apiFetch<Artifact[]>(`/projects/${projectId}/artifacts`),
      apiFetch<Deployment[]>(`/projects/${projectId}/deployments`),
    ]);
    const [treeResult, diffResult, statusResult, artifactResult, deploymentResult] = results;
    if (treeResult.status === "fulfilled") setEntries(treeResult.value);
    if (diffResult.status === "fulfilled") setDiffContent(diffResult.value.diff);
    if (statusResult.status === "fulfilled") setWorkspaceStatus(statusResult.value);
    if (artifactResult.status === "fulfilled") setArtifacts(artifactResult.value);
    if (deploymentResult.status === "fulfilled") setDeployments(deploymentResult.value);
    const labels = ["Files", "Diff", "Git status", "Artifacts", "Deployments"];
    const failures = results.flatMap((result, index) => result.status === "rejected" ? [`${labels[index]}: ${result.reason instanceof Error ? result.reason.message : "request failed"}`] : []);
    setWorkspaceError(failures.join(" · "));
    try { setTerminalSessions(await apiFetch<TerminalSession[]>(`/projects/${projectId}/terminal`)); } catch { /* terminal is optional */ }
  }, [projectId]);

  const runTerminalCommand = useCallback(async (event: FormEvent) => {
    event.preventDefault();
    if (!terminalCommand.trim() || terminalSubmitting) return;
    setTerminalSubmitting(true); setWorkspaceError("");
    try {
      const result = await apiFetch<{ session: TerminalSession }>(`/projects/${projectId}/terminal`, {
        method: "POST", body: JSON.stringify({ command: terminalCommand.trim(), cwd: terminalCwd || ".", run_id: selectedRunIdRef.current }),
      });
      setTerminalSessions((current) => [result.session, ...current.filter((item) => item.id !== result.session.id)]);
      setTerminalCommand("");
    } catch (caught) { setWorkspaceError(caught instanceof Error ? caught.message : "Could not start terminal command"); }
    finally { setTerminalSubmitting(false); }
  }, [projectId, terminalCommand, terminalCwd, terminalSubmitting]);

  useEffect(() => {
    if (!showRightPanel || rightPanelTab !== "terminal") return;
    const refresh = () => void apiFetch<TerminalSession[]>(`/projects/${projectId}/terminal`).then(setTerminalSessions).catch(() => undefined);
    const timer = window.setInterval(refresh, 1500);
    return () => window.clearInterval(timer);
  }, [projectId, rightPanelTab, showRightPanel]);

  const searchWorkspace = useCallback(async (event: FormEvent) => {
    event.preventDefault();
    const query = workspaceSearchQuery.trim();
    if (!query) { setWorkspaceSearchResults([]); return; }
    try {
      const runQuery = selectedRunIdRef.current ? `&run_id=${encodeURIComponent(selectedRunIdRef.current)}` : "";
      setWorkspaceSearchResults(await apiFetch<WorkspaceSearchResult[]>(`/projects/${projectId}/workspace/search?query=${encodeURIComponent(query)}${runQuery}`));
    } catch (caught) {
      setWorkspaceError(caught instanceof Error ? caught.message : "Could not search workspace");
    }
  }, [projectId, workspaceSearchQuery]);

  const createSchedule = useCallback(async (event: FormEvent) => {
    event.preventDefault();
    if (!schedulePrompt.trim() || scheduleSubmitting) return;
    setScheduleSubmitting(true);
    try {
      await apiFetch<AgentSchedule>(`/projects/${projectId}/schedules`, {
        method: "POST",
        body: JSON.stringify({ prompt: schedulePrompt.trim(), interval_seconds: Number(scheduleInterval), enabled: true }),
      });
      setSchedulePrompt("");
      await loadSchedules();
    } catch (caught) { setWorkspaceError(caught instanceof Error ? caught.message : "Could not create schedule"); }
    finally { setScheduleSubmitting(false); }
  }, [projectId, scheduleInterval, schedulePrompt, scheduleSubmitting, loadSchedules]);

  const toggleSchedule = useCallback(async (schedule: AgentSchedule) => {
    try {
      await apiFetch<AgentSchedule>(`/schedules/${schedule.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !schedule.enabled }) });
      await loadSchedules();
    } catch (caught) { setWorkspaceError(caught instanceof Error ? caught.message : "Could not update schedule"); }
  }, [loadSchedules]);

  const removeSchedule = useCallback(async (schedule: AgentSchedule) => {
    try {
      await apiFetch<void>(`/schedules/${schedule.id}`, { method: "DELETE" });
      await loadSchedules();
    } catch (caught) { setWorkspaceError(caught instanceof Error ? caught.message : "Could not delete schedule"); }
  }, [loadSchedules]);

  const openFile = useCallback(async (path: string) => {
    try {
      const runQuery = selectedRunIdRef.current ? `&run_id=${encodeURIComponent(selectedRunIdRef.current)}` : "";
      const file = await apiFetch<{ content: string }>(`/projects/${projectId}/workspace/files?path=${encodeURIComponent(path)}${runQuery}`);
      setSelectedFile(path);
      setFileContent(file.content);
    } catch (caught) { setWorkspaceError(caught instanceof Error ? caught.message : "Could not read file"); }
  }, [projectId]);

  const load = useCallback(async () => {
    try {
      const [projectData, instanceData, runsData] = await Promise.all([
        apiFetch<Project>(`/projects/${projectId}`),
        apiFetch<Instance[]>(`/projects/${projectId}/instances`),
        apiFetch<AgentRun[]>(`/projects/${projectId}/runs`),
      ]);
      setProject(projectData); setInstances(instanceData);
      setRuns(runsData);
      void loadSchedules();
      if (runsData.length > 0) {
        const selected = runsData.find((run) => ACTIVE_RUN_STATUSES.has(run.status)) || runsData[0];
        setActiveThreadId(selected.thread_id || null);
        selectedRunIdRef.current = selected.id;
        void loadSubtasks(selected.id);
        const events = await apiFetch<ToolEvent[]>(`/runs/${selected.id}/events`);
        if (selected.thread_id) {
          try {
            const threadTranscript = await fetchThreadTranscript(projectId, selected.thread_id);
            const priorTranscript = threadTranscript.filter((m) => !String(m.id).startsWith(`${selected.id}-`));
            hydrateRun(selected, events, priorTranscript);
          } catch {
            hydrateRun(selected, events);
          }
        } else {
          hydrateRun(selected, events);
        }
      }
      void loadWorkspaceDetails();
    } catch (caught) { setPageError(caught instanceof Error ? caught.message : "Could not load project"); }
    finally { setBootLoading(false); }
  }, [projectId, loadWorkspaceDetails, hydrateRun, loadSubtasks, loadSchedules]);

  useEffect(() => {
    // State changes occur after the API promises resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [chat, pending, agentQuestion, error, finalReport, steps]);
  useEffect(() => () => {
    if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    streamAbortRef.current?.abort();
  }, []);

  const discoverDatabases = async (candidateUrl: string) => {
    const normalizedUrl = candidateUrl.trim();
    if (!normalizedUrl) return;
    try {
      new URL(normalizedUrl);
    } catch {
      return;
    }
    const sequence = ++discoverySequence.current;
    setDetectingDatabases(true); setDiscoveryMessage(""); setDetectedDatabases([]); setDbName("");
    try {
      const result = await apiFetch<{ status: string; databases: string[]; suggested_db?: string; suggested_username: string; message?: string }>("/instances/detect", {
        method: "POST",
        body: JSON.stringify({ url: normalizedUrl, erp_type: "odoo" }),
      });
      if (sequence !== discoverySequence.current) return;
      const databases = result.databases || [];
      setDetectedDatabases(databases);
      if (databases.length === 1) {
        setDbName(databases[0]);
        setDiscoveryMessage(`Database “${databases[0]}” selected automatically.`);
      } else if (result.suggested_db) {
        setDbName(result.suggested_db);
        setDiscoveryMessage(result.message || `Database “${result.suggested_db}” suggested from URL.`);
      } else if (databases.length > 1) {
        setDiscoveryMessage(`${databases.length} databases found. Select one to continue.`);
      } else {
        setDiscoveryMessage(result.message || "No database list was exposed by this server. Enter the database name manually.");
      }
      if (!username && result.suggested_username) setUsername(result.suggested_username);
    } catch (caught) {
      if (sequence !== discoverySequence.current) return;
      setDiscoveryMessage(caught instanceof Error ? `${caught.message}. You can enter the database name manually.` : "Database discovery failed. Enter the name manually.");
    } finally {
      if (sequence === discoverySequence.current) setDetectingDatabases(false);
    }
  };

  const changeUrl = (value: string) => {
    setUrl(value); setDetectedDatabases([]); setDbName(""); setDiscoveryMessage("");
    if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    discoveryTimer.current = setTimeout(() => void discoverDatabases(value), 700);
  };

  const finishUrlEntry = () => {
    if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    void discoverDatabases(url);
  };

  const connect = async (event: FormEvent) => {
    event.preventDefault(); setConnectingInstance(true); setPageError("");
    try {
      await apiFetch<Instance>("/instances", { method: "POST", body: JSON.stringify({ erp_type: "odoo", url, db_name: dbName, username: authMethod === "xmlrpc" ? username : null, password: authMethod === "xmlrpc" ? password : null, api_key: authMethod === "json2" ? apiKey : null, auth_method: authMethod, project_id: projectId }) });
      setPassword(""); setApiKey(""); await load();
    } catch (caught) { setPageError(caught instanceof Error ? caught.message : "Connection failed"); }
    finally { setConnectingInstance(false); }
  };

  const autoSelectFirstFile = useCallback(async () => {
    try {
      const runQuery = selectedRunIdRef.current ? `?run_id=${encodeURIComponent(selectedRunIdRef.current)}` : "";
      const treeData = await apiFetch<WorkspaceEntry[]>(`/projects/${projectId}/workspace/tree${runQuery}`);
      const diffData = await apiFetch<{ diff: string }>(`/projects/${projectId}/workspace/diff${runQuery}`);
      setEntries(treeData);
      setDiffContent(diffData.diff);
      const fileEntries = treeData.filter((e) => e.type === "file");
      if (fileEntries.length > 0) {
        const target = fileEntries.find((f) => f.path.includes("models/") || f.path.includes("views/") || f.path.includes("manifest")) || fileEntries[0];
        setSelectedFile(target.path);
        setRightPanelTab("code");
        void openFile(target.path);
      }
    } catch (caught) { setWorkspaceError(caught instanceof Error ? caught.message : "Could not refresh workspace"); }
  }, [projectId, openFile]);

  useEffect(() => {
    if (!activeRunId || !runStatus || runStatus.startsWith("awaiting_")) return;
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    let isActive = true;

    void followRun(activeRunId, (runEvent) => {
      if (runEvent.sequence > streamCursorRef.current) {
        streamCursorRef.current = runEvent.sequence;
      }
      receiveRunEvent(runEvent);
      if (runEvent.event_type === "final_report") void autoSelectFirstFile();
    }, { after: streamCursorRef.current, signal: controller.signal, onConnectionState: setConnection })
      .then((run) => {
        if (!isActive) return;
        setRun(run);
        setRuns((current) => current.map((item) => item.id === run.id ? run : item));
        if (ACTIVE_RUN_STATUSES.has(run.status) && !run.status.startsWith("awaiting_")) {
          setStreamAttempt((v) => v + 1);
        }
      })
      .catch((caught) => {
        if (isActive && !controller.signal.aborted) {
          setRunError(caught instanceof Error ? caught.message : "Agent stream failed");
        }
      });
    return () => {
      isActive = false;
      controller.abort();
    };
  }, [activeRunId, autoSelectFirstFile, receiveRunEvent, runStatus, setConnection, setRun, setRunError, streamAttempt]);

  // Background status reconciliation loop for active runs
  useEffect(() => {
    if (!activeRunId || !runStatus || !ACTIVE_RUN_STATUSES.has(runStatus)) return;
    const interval = setInterval(async () => {
      try {
        const current = await apiFetch<AgentRun>(`/runs/${activeRunId}`);
        if (current.status !== runStatus) {
          setRun(current);
          setRuns((prev) => prev.map((item) => item.id === current.id ? current : item));
        }
      } catch {
        // silent background poll
      }
    }, 4000);
    return () => clearInterval(interval);
  }, [activeRunId, runStatus, setRun]);

  useEffect(() => {
    const selected = runState.selectedRunId;
    if (!selected) return;
    const initial = window.setTimeout(() => void loadSubtasks(selected), 0);
    if (!activeRunId) return () => window.clearTimeout(initial);
    const interval = window.setInterval(() => void loadSubtasks(selected), 1500);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [activeRunId, loadSubtasks, runState.selectedRunId]);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = message.trim(); if (!text || pending || activeRunId) return;
    setMessage(""); setPageError(""); setSubmittingRun(true);
    const targetThreadId = activeThreadId || runState.run?.thread_id || undefined;
    try {
      const run = await apiFetch<AgentRun>(`/projects/${projectId}/runs`, {
        method: "POST",
        body: JSON.stringify({
          message: text,
          workspace_mode: workspaceMode,
          thread_id: targetThreadId,
          attachments,
        })
      });
      setActiveThreadId(run.thread_id || null);
      selectedRunIdRef.current = run.id;
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      setSubtasks([]);
      if (targetThreadId && run.thread_id === targetThreadId && runState.transcript.length > 0) {
        continueRun(run);
      } else {
        hydrateRun(run, []);
      }
      setAttachments([]);
    } catch (caught) {
      setPageError(caught instanceof Error ? caught.message : "Agent request failed");
    } finally { setSubmittingRun(false); }
  };

  const addFiles = async (files: FileList | File[]) => {
    const selectedFiles = Array.from(files);
    const incoming = selectedFiles.filter((file) => file.size <= 25 * 1024 * 1024).slice(0, 10 - attachments.length);
    if (incoming.length !== selectedFiles.length) setPageError("Attachments must be 25 MB or smaller, with a maximum of 10 files.");
    const loaded = await Promise.all(incoming.map(async (file) => ({
      name: file.name, type: file.type || "application/octet-stream", content: await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result || "")); reader.onerror = () => reject(reader.error); reader.readAsDataURL(file); }),
      text: file.type.startsWith("text/") ? await file.text() : undefined,
    })));
    setAttachments((current) => [...current, ...loaded]);
  };

  const decide = async (decision: "approve" | "reject", autoApproveTask = false, remember = false) => {
    if (!pending) return;
    const action = pending; setDeciding(true); setPageError("");
    try {
      const run = await apiFetch<AgentRun>(`/actions/${action.id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, auto_approve_task: autoApproveTask, remember }),
      });
      setRun(run);
      setRuns((current) => current.map((item) => item.id === run.id ? run : item));
      setStreamAttempt((v) => v + 1);
    } catch (caught) {
      setPageError(caught instanceof Error ? caught.message : "Decision failed");
    } finally { setDeciding(false); }
  };

  const submitAnswer = async () => {
    if (!questionAnswer.trim() || submittingAnswer) return;
    const answer = questionAnswer.trim();
    setSubmittingAnswer(true); setPageError("");
    try {
      if (!agentQuestion?.run_id) throw new Error("No active run is waiting for an answer");
      const run = await apiFetch<AgentRun>(`/runs/${agentQuestion.run_id}/question`, {
        method: "POST",
        body: JSON.stringify({ answer }),
      });
      setQuestionAnswer("");
      setRun(run);
      setRuns((current) => current.map((item) => item.id === run.id ? run : item));
      setStreamAttempt((v) => v + 1);
    } catch (caught) {
      setPageError(caught instanceof Error ? caught.message : "Failed to submit answer");
    } finally { setSubmittingAnswer(false); }
  };

  const handleQuickDeploy = async () => {
    setDeploying(true);
    setDeployMsg(null);
    try {
      const res = await apiFetch<{ message: string; deployed: boolean }>(`/projects/${projectId}/quick-deploy`, {
        method: "POST",
      });
      setDeployMsg(res.message);
      void loadWorkspaceDetails();
    } catch (err) {
      setDeployMsg(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setDeploying(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void send(e as unknown as FormEvent);
    }
  };

  const handleDeleteRun = async (runId: string) => {
    if (runId === activeRunId) return;
    const targetRun = runs.find((r) => r.id === runId);
    try {
      if (targetRun?.thread_id) {
        await deleteThread(projectId, targetRun.thread_id).catch(() => deleteRun(runId));
        setRuns((prev) => prev.filter((r) => r.thread_id ? r.thread_id !== targetRun.thread_id : r.id !== runId));
        if (runState.run?.thread_id === targetRun.thread_id || runState.selectedRunId === runId) {
          setActiveThreadId(null);
          clearRun();
        }
      } else {
        await deleteRun(runId);
        setRuns((prev) => prev.filter((r) => r.id !== runId));
        if (runState.selectedRunId === runId) clearRun();
      }
    } catch (caught) {
      setPageError(caught instanceof Error ? caught.message : "Failed to delete run");
    }
  };

  const handleRetryRun = async (runId?: string) => {
    const targetId = runId || runState.run?.id;
    if (!targetId) return;
    setPageError("");
    try {
      const newRun = await apiFetch<AgentRun>(`/runs/${targetId}/retry`, { method: "POST" });
      selectedRunIdRef.current = newRun.id;
      setRun(newRun);
      setRuns((prev) => [newRun, ...prev.filter((r) => r.id !== newRun.id)]);
      setStreamAttempt((v) => v + 1);
      void loadWorkspaceDetails();
    } catch (err) {
      setPageError(err instanceof Error ? err.message : "Failed to retry run");
    }
  };

  const openRun = async (run: AgentRun) => {
    streamAbortRef.current?.abort();
    try {
      setActiveThreadId(run.thread_id || null);
      selectedRunIdRef.current = run.id;
      void loadSubtasks(run.id);
      void loadWorkspaceDetails();
      setShowHistoryModal(false);

      const events = await apiFetch<ToolEvent[]>(`/runs/${run.id}/events`);
      if (run.thread_id) {
        try {
          const threadTranscript = await fetchThreadTranscript(projectId, run.thread_id);
          const priorTranscript = threadTranscript.filter((m) => !String(m.id).startsWith(`${run.id}-`));
          hydrateRun(run, events, priorTranscript);
          return;
        } catch {
          // fallback to single run events if thread fetch fails
        }
      }
      hydrateRun(run, events);
    } catch (caught) { setPageError(caught instanceof Error ? caught.message : "Failed to open run"); }
  };

  const startNewChat = () => {
    // Read-only runs are safe to leave running in the background while a new
    // conversation is opened. Write runs remain project-serialized.
    if ((activeRunId && runState.run?.intent !== "read_only") || deciding) return;
    streamAbortRef.current?.abort();
    selectedRunIdRef.current = null;
    setActiveThreadId(null);
    clearRun();
    setSubtasks([]);
    setMessage(""); setQuestionAnswer(""); setPageError("");
    void loadWorkspaceDetails();
  };

  const handleStopRun = async () => {
    if (!activeRunId) return;
    try {
      await apiFetch(`/runs/${activeRunId}/cancel`, { method: "POST" });
    } catch (caught) {
      setPageError(caught instanceof Error ? caught.message : "Failed to stop run");
    }
  };

  if (loading && !project) return <div className="p-8 text-gray-500">Loading…</div>;
  if (!project) return <div className="p-8 text-red-600">{error || "Project not found"}</div>;
  if (!instances.length) return (
    <div className="mx-auto max-w-xl p-8">
      <h1 className="text-3xl font-bold">{project.name}</h1>
      <p className="mt-2 text-sm text-gray-400">
        Connect to hosted Odoo (Odoo.sh, Odoo Online, Cloud) or local ERP instance. Credentials are encrypted securely.
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" onClick={() => changeUrl("https://my-company.odoo.com")} className="rounded-lg border border-purple-500/30 bg-purple-500/10 px-2.5 py-1 text-xs text-purple-300 hover:bg-purple-500/20 transition">
          ☁️ Odoo Online (*.odoo.com)
        </button>
        <button type="button" onClick={() => changeUrl("https://my-company.odoo.sh")} className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 text-xs text-blue-300 hover:bg-blue-500/20 transition">
          🚀 Odoo.sh (*.odoo.sh)
        </button>
        <button type="button" onClick={() => changeUrl("http://localhost:8069")} className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-300 hover:bg-emerald-500/20 transition">
          💻 Local Odoo (localhost:8069)
        </button>
      </div>

      <form onSubmit={connect} className="mt-6 space-y-4 rounded-2xl border p-6 dark:border-white/10">
        <label className="block text-sm font-medium">
          Server URL <span className="text-xs text-gray-500 font-normal">(Hosted Odoo or Local ERP)</span>
          <div className="relative mt-1">
            <input
              type="url"
              required
              value={url}
              onChange={(event) => changeUrl(event.target.value)}
              onBlur={finishUrlEntry}
              placeholder="https://your-company.odoo.com or http://localhost:8069"
              className="w-full rounded-xl border px-4 py-3 pr-10 dark:border-white/10 dark:bg-black text-sm"
            />
            {detectingDatabases && <Loader2 className="absolute right-3 top-3.5 h-4 w-4 animate-spin text-blue-600" />}
          </div>
        </label>
        <label className="block text-sm font-medium">Database
          {detectedDatabases.length > 1 ? (
            <select required value={dbName} onChange={(event) => setDbName(event.target.value)} className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm">
              <option value="">Select a database…</option>
              {detectedDatabases.map((database) => <option key={database} value={database}>{database}</option>)}
            </select>
          ) : (
            <input required value={dbName} onChange={(event) => setDbName(event.target.value)} readOnly={detectedDatabases.length === 1} placeholder={detectingDatabases ? "Discovering databases…" : "Database name (e.g. production)"} className="mt-1 w-full rounded-xl border px-4 py-3 read-only:bg-gray-50 dark:border-white/10 dark:bg-black dark:read-only:bg-white/5 text-sm" />
          )}
          {discoveryMessage && <span className="mt-1 block text-xs text-gray-500">{discoveryMessage}</span>}
        </label>
        <label className="block text-sm font-medium">Username<input required value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="admin@example.com" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm" /></label>
        <label className="block text-sm font-medium">Password<input type="password" required value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="Password" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm" /></label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button disabled={loading || detectingDatabases} className="w-full rounded-xl bg-blue-600 py-3 font-medium text-white disabled:opacity-50 transition active:scale-95">{loading ? "Verifying…" : detectingDatabases ? "Discovering databases…" : "Verify and connect"}</button>
      </form>
    </div>
  );

  return (
    <div className={`flex h-[100dvh] overflow-hidden ${isResizingRight || isResizingLeft ? "select-none" : ""}`}>
      {/* Collapsible Left Sub-Sidebar */}
      {showLeftSidebar && <button className="fixed inset-0 z-20 bg-black/60 xl:hidden" onClick={toggleLeftSidebar} aria-label="Close project navigation overlay" />}
      {showLeftSidebar && (
        <aside style={{ width: `${leftPanelWidth}px` }} className="project-navigation-panel relative inset-y-0 left-0 z-30 flex shrink-0 flex-col border-r bg-black p-3 dark:border-white/10 xl:w-auto">
          <div className={leftPanelWidth < 180 ? "flex flex-col items-center gap-3 pt-2" : ""}>
          {leftPanelWidth >= 180 && <h1 className="px-2 text-xl font-bold truncate">{project.name}</h1>}
          <div className={leftPanelWidth < 180 ? "flex flex-col items-center gap-3" : "mt-4 rounded-xl border p-3.5 dark:border-white/10"} title={instances[0].url}>
            <Database className="mb-1.5 h-4 w-4 text-blue-600" />
            {leftPanelWidth >= 180 && <><p className="truncate text-xs font-mono">{instances[0].url}</p><p className="mt-1 text-[10px] uppercase font-semibold text-gray-500">{instances[0].environment} · {instances[0].status}</p></>}
          </div>
          <nav className={leftPanelWidth < 180 ? "mt-4 flex flex-col items-center gap-4" : "mt-4 space-y-2"}>
            <Link title="Workspace & lifecycle" href={`/projects/${projectId}/workspace`} className="block rounded-xl border p-2.5 text-xs hover:bg-white/10 dark:border-white/10">{leftPanelWidth < 180 ? <Server className="h-4 w-4" /> : "Workspace & lifecycle"}</Link>
            <Link title="Project settings & permissions" href={`/projects/${projectId}/settings`} className="block rounded-xl border p-2.5 text-xs hover:bg-white/10 dark:border-white/10">{leftPanelWidth < 180 ? <SlidersHorizontal className="h-4 w-4" /> : "Project settings & permissions"}</Link>
            <Link title="Odoo connections" href={`/projects/${projectId}/instances`} className="block rounded-xl border p-2.5 text-xs hover:bg-white/10 dark:border-white/10">{leftPanelWidth < 180 ? <Database className="h-4 w-4" /> : "Odoo connections"}</Link>
          </nav>
          </div>
          <div
            onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); setIsResizingLeft(true); }}
            onPointerMove={(event) => {
              if (!isResizingLeft) return;
              const panelLeft = event.currentTarget.parentElement?.getBoundingClientRect().left ?? 0;
              const next = Math.min(360, Math.max(64, event.clientX - panelLeft));
              setLeftPanelWidth(next);
              localStorage.setItem("workspace:leftPanelWidth", String(next));
            }}
            onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); setIsResizingLeft(false); }}
            onPointerCancel={() => setIsResizingLeft(false)}
            onKeyDown={(event) => { if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return; event.preventDefault(); setLeftPanelWidth((width) => { const next = Math.min(360, Math.max(64, width + (event.key === "ArrowRight" ? 16 : -16))); localStorage.setItem("workspace:leftPanelWidth", String(next)); return next; }); }}
            role="separator" aria-label="Resize project navigation" aria-orientation="vertical" aria-valuemin={64} aria-valuemax={360} aria-valuenow={Math.round(leftPanelWidth)} tabIndex={0}
            className={`absolute inset-y-0 -right-2 z-30 hidden w-4 cursor-col-resize touch-none xl:block ${isResizingLeft ? "bg-blue-600/70" : "bg-transparent hover:bg-blue-500/60"}`}
          />
        </aside>
      )}

      {/* Center Fluid Chat Pane */}
      <section className="flex min-w-0 flex-1 flex-col">
        {/* Header */}
        <header className="flex items-center justify-between border-b border-white/5 bg-zinc-900 px-4 py-2.5">
          <div className="flex items-center gap-2.5">
            <button
              onClick={toggleLeftSidebar}
              className="rounded-md border border-white/10 bg-white/5 p-1.5 text-gray-400 hover:bg-white/10 hover:text-white"
              title="Toggle Project Sub-Sidebar"
              aria-label="Toggle project navigation"
            >
              {showLeftSidebar ? <PanelLeftClose className="h-3.5 w-3.5" /> : <PanelLeftOpen className="h-3.5 w-3.5" />}
            </button>
            <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-blue-600/20">
              <Bot className="h-3.5 w-3.5 text-blue-400" />
            </div>
            <span className="text-xs font-semibold text-white">ERP Implementation Agent</span>
            <button
              onClick={() => void startNewChat()}
              disabled={Boolean(activeRunId && runState.run?.intent !== "read_only") || loading && runState.run?.intent !== "read_only" || deciding}
              className="flex items-center gap-1.5 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium text-gray-400 transition hover:bg-white/10 hover:text-white active:scale-95 disabled:opacity-40"
            >
              <Plus className="h-3 w-3" />
              New Chat
            </button>
            <button
              onClick={() => setShowHistoryModal(true)}
              className="flex items-center gap-1.5 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium text-gray-400 transition hover:bg-white/10 hover:text-white active:scale-95"
            >
              <History className="h-3 w-3 text-purple-400" />
              History ({new Set(runs.map((r) => r.thread_id || r.id)).size})
            </button>
            <label className="hidden items-center gap-1.5 text-[10px] text-gray-500 sm:flex" title="Run implementation work in an isolated Git worktree">
              <input type="checkbox" checked={workspaceMode === "isolated"} onChange={(event) => setWorkspaceMode(event.target.checked ? "isolated" : "local")} disabled={loading || Boolean(activeRunId)} />
              Isolated worktree
            </label>
          </div>
          <div className="flex items-center gap-4">
            {tokenInputs > 0 && <span className="font-mono text-[10px] text-gray-500">{tokenInputs.toLocaleString()} input tokens</span>}
            {activeRunId && (
              <button
                onClick={() => void handleStopRun()}
                className="text-xs text-red-400 hover:text-red-300"
              >
                Cancel
              </button>
            )}
            <button
              onClick={() => setShowRightPanel(!showRightPanel)}
              className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition ${
                showRightPanel
                  ? "border-blue-500/30 bg-blue-600/20 text-blue-300"
                  : "border-white/10 bg-white/5 text-gray-400 hover:bg-white/10 hover:text-white"
              }`}
              title="Toggle Right Workspace Panel"
            >
              {showRightPanel ? <PanelRightClose className="h-3.5 w-3.5" /> : <PanelRightOpen className="h-3.5 w-3.5" />}
              <span>IDE Panel</span>
            </button>
          </div>
        </header>

        {/* Persistent sticky agent status bar — always visible when agent is working */}
        <AnimatePresence>
          {(runWorking || deciding) && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.18 }}
              className="border-b border-blue-100 bg-blue-50/80 px-5 py-2 backdrop-blur-sm dark:border-blue-900/30 dark:bg-blue-950/30"
            >
              <AgentStatus currentAction={currentTool} isThinking={isThinking} phase={phase} tokenInputs={tokenInputs} />
            </motion.div>
          )}
        </AnimatePresence>

        {activeRunId && runState.connection !== "connected" && (
          <div className="border-b border-white/10 bg-zinc-950 px-5 py-2 text-xs text-gray-300" aria-live="polite">
            {runState.connection === "retrying" && "Connection lost — retrying…"}
            {runState.connection === "connecting" && "Connecting to agent…"}
            {runState.connection === "paused" && (agentQuestion ? "Waiting for your answer." : pending ? "Waiting for approval." : "Run paused.")}
            {runState.connection === "disconnected" && <span>Connection could not be restored. <button className="ml-2 font-semibold text-blue-400 underline" onClick={() => setStreamAttempt((value) => value + 1)}>Retry</button></span>}
          </div>
        )}

        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          {/* Welcome & Context Awareness Card (Empty State) */}
          {chat.length === 0 && !loading && !activeRunId && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              className="mx-auto max-w-2xl py-6 space-y-5"
            >
              {/* Header */}
              <div className="text-center space-y-2">
                <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-600 shadow-lg shadow-blue-500/20 mb-1">
                  <Sparkles className="h-6 w-6 text-white" />
                </div>
                <h2 className="text-xl font-bold text-white tracking-tight">
                  Welcome to {project?.name || "ERP Implementation Agent"}
                </h2>
                <p className="text-xs text-slate-400 max-w-md mx-auto leading-relaxed">
                  Your AI engineering partner for {instances[0]?.erp_type === "pri_erp" ? "PRI ERP" : "Odoo 19"}.
                  I inspect real schemas, formulate plans, and ask for confirmation before modifying code.
                </p>
              </div>

              {/* Connected ERP Awareness Card */}
              {instances[0] && (
                <div className="rounded-2xl border border-white/10 bg-slate-900/80 p-4 shadow-sm backdrop-blur-sm space-y-3">
                  <div className="flex items-center justify-between border-b border-white/5 pb-2.5">
                    <span className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
                      <Server className="h-3.5 w-3.5 text-blue-400" />
                      Connected ERP Environment
                    </span>
                    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-emerald-400 bg-emerald-500/10 px-2.5 py-0.5 rounded-full border border-emerald-500/20">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      Active & Connected
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div className="space-y-0.5">
                      <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Server URL</span>
                      <p className="font-mono text-slate-200 truncate">{instances[0].url}</p>
                    </div>
                    <div className="space-y-0.5">
                      <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Active Database</span>
                      <p className="font-mono font-semibold text-blue-400">{instances[0].db_name || "Default"}</p>
                    </div>
                    <div className="space-y-0.5">
                      <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Engine / Version</span>
                      <p className="text-slate-300">
                        {instances[0].version_info?.server_version ? `Odoo ${String(instances[0].version_info.server_version)}` : "Odoo 19.0"}
                        {instances[0].version_info?.server_edition ? ` (${String(instances[0].version_info.server_edition)})` : ""}
                      </p>
                    </div>
                    <div className="space-y-0.5">
                      <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">Environment & Auth</span>
                      <p className="text-slate-300 capitalize">{instances[0].environment} · {instances[0].auth_method?.toUpperCase()}</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Safety & Grounding Callout */}
              <div className="flex items-start gap-3 rounded-xl border border-blue-500/20 bg-blue-950/20 p-3.5 text-xs text-blue-300">
                <Shield className="h-4 w-4 shrink-0 text-blue-400 mt-0.5" />
                <p className="leading-relaxed">
                  <strong className="text-blue-200">Safety & Grounding Guarantee:</strong> I will never modify files or write code randomly. For general chats, I will explain and clarify. When building, I inspect your schema first, propose a structured plan, and require your confirmation before any changes.
                </p>
              </div>

              {/* Starter Prompts */}
              <div className="space-y-2">
                <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Suggested Starters</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {[
                    {
                      icon: Search,
                      title: "Inspect Database & Apps",
                      desc: "List installed modules, models, and current settings",
                      prompt: "Inspect the connected database and show installed modules and system status.",
                    },
                    {
                      icon: Database,
                      title: "Review Existing Models",
                      desc: "Inspect models and fields before planning customizations",
                      prompt: "Inspect existing Odoo models and fields to understand current capabilities.",
                    },
                    {
                      icon: Lightbulb,
                      title: "Plan a Custom Feature",
                      desc: "Design a new workflow (I will propose a plan first)",
                      prompt: "Help me plan a custom module for my business workflow. Inspect what we have first.",
                    },
                    {
                      icon: HelpCircle,
                      title: "Ask a Question",
                      desc: "Learn about Odoo 19 architecture, OWL, or deployment",
                      prompt: "How does the Odoo 19 module build and deployment workflow work in this workspace?",
                    },
                  ].map((starter, i) => {
                    const Icon = starter.icon;
                    return (
                      <button
                        key={i}
                        type="button"
                        onClick={() => {
                          setMessage(starter.prompt);
                          inputRef.current?.focus();
                        }}
                        className="group flex items-start gap-3 rounded-xl border border-white/5 bg-slate-900/60 p-3 text-left transition hover:border-blue-500/40 hover:bg-slate-800/80 active:scale-[0.98]"
                      >
                        <div className="rounded-lg bg-blue-500/10 p-2 text-blue-400 group-hover:bg-blue-500/20 group-hover:text-blue-300 transition">
                          <Icon className="h-4 w-4" />
                        </div>
                        <div className="space-y-0.5">
                          <p className="text-xs font-semibold text-slate-200 group-hover:text-white transition">{starter.title}</p>
                          <p className="text-[11px] text-slate-400 leading-snug">{starter.desc}</p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </motion.div>
          )}

          {/* Messages & Turn Structure (Antigravity flow: Prompt -> Tool Execution Steps -> Generated Summary Below) */}
          {(() => {
            const validMessages = chat.filter((item: ChatMessage) => item.role !== "agent" || item.content.trim());
            const lastUserIndex = validMessages.map((m) => m.role).lastIndexOf("user");

            const renderSingleMessage = (item: ChatMessage, index: number, isCurrentStreaming: boolean, isFirstInGroup: boolean) => (
              <motion.div
                key={`${item.id || "msg"}-${index}`}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                className={`flex ${item.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {item.role === "agent" ? (
                  <div className="flex min-w-0 max-w-[82%] items-start gap-3">
                    {/* Bot avatar — only on first message in a sequence */}
                    <div className={`mt-0.5 shrink-0 transition-opacity ${isFirstInGroup ? "opacity-100" : "opacity-0"}`}>
                      <div className="flex h-6 w-6 items-center justify-center rounded-md bg-blue-600/15">
                        <Bot className="h-3.5 w-3.5 text-blue-400" />
                      </div>
                    </div>
                    {/* Message content — flat, no bubble */}
                    <div className="min-w-0 flex-1 pb-1">
                      <MessageContent
                        content={item.content}
                        projectId={projectId}
                        isStreaming={isCurrentStreaming}
                      />
                    </div>
                  </div>
                ) : (
                  <div className="max-w-[75%] rounded-2xl bg-blue-600 px-4 py-3 text-sm text-white shadow-xs">
                    <MessageContent
                      content={item.content}
                      projectId={projectId}
                      isStreaming={false}
                    />
                  </div>
                )}
              </motion.div>
            );

            const executionTrace = (
              <div key="execution-trace" className="space-y-3">
                {/* Activity Stepper */}
                <ActivityStepper
                  steps={steps}
                  usage={usage}
                  supervisorTaskGraph={supervisorTaskGraph}
                  activeTaskId={activeTaskId}
                  recoveringTaskId={recoveringTaskId}
                  planItems={runState.planItems}
                  inspectionOnly={runState.run?.intent === "read_only"}
                  onOpenDiff={() => {
                    setShowRightPanel(true);
                    setRightPanelTab("diff");
                  }}
                  onOpenFile={(filepath) => {
                    setShowRightPanel(true);
                    setRightPanelTab("code");
                    void openFile(filepath);
                  }}
                />

                {subtasks.length > 0 && (
                  <section className="max-w-2xl rounded-xl border border-cyan-500/20 bg-cyan-950/10 p-3.5" aria-label="Child agent executions">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-cyan-200">Child agents</span>
                      <span className="font-mono text-[10px] text-cyan-300">
                        {subtasks.filter((item) => item.status === "succeeded").length}/{subtasks.length} complete
                      </span>
                    </div>
                    <ul className="mt-2 space-y-1.5">
                      {subtasks.map((item) => (
                        <li key={item.id} className="flex items-center justify-between gap-3 text-[11px]">
                          <span className={item.status === "failed" ? "text-red-300" : item.status === "succeeded" ? "text-gray-500 line-through" : "text-cyan-100"}>
                            {item.title}
                          </span>
                          <span className="shrink-0 font-mono text-[10px] text-gray-500">{item.role.replace(/_/g, " ")} · {item.status}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {/* Thinking Text (Protocol 2 — muted italic inline) */}
                <AnimatePresence>
                  {thinkingText && loading && (
                    <motion.p
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="max-w-2xl pl-9 text-xs italic text-gray-400 dark:text-gray-500"
                    >
                      {thinkingText}
                    </motion.p>
                  )}
                </AnimatePresence>

                {/* Pending Approval Card */}
                <AnimatePresence>
                  {pending && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.96, y: 8 }}
                      animate={{ opacity: 1, scale: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.96, y: 4 }}
                      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                      className="max-w-2xl"
                    >
                      <ApprovalCard action={pending} busy={deciding} onDecision={(decision, autoApprove, remember) => void decide(decision, autoApprove, remember)} />
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Question Card (Protocol 3) */}
                <AnimatePresence>
                  {agentQuestion && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.96, y: 8 }}
                      animate={{ opacity: 1, scale: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.96, y: 4 }}
                      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                      className="max-w-2xl rounded-2xl border border-blue-400/60 bg-blue-50/90 p-5 text-sm shadow-sm backdrop-blur-md dark:border-blue-500/30 dark:bg-blue-950/30"
                    >
                      <div className="flex items-center gap-2 font-semibold text-blue-800 dark:text-blue-300">
                        <Brain className="h-5 w-5 text-blue-500 dark:text-blue-400" />
                        <span>Agent needs clarification</span>
                      </div>
                      <p className="mt-2.5 text-sm text-blue-900/90 dark:text-blue-100/90">{agentQuestion.question}</p>
                      {agentQuestion.options.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {agentQuestion.options.map((opt: string, i: number) => (
                            <button
                              key={i}
                              onClick={() => setQuestionAnswer(opt)}
                              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${questionAnswer === opt ? "border-blue-500 bg-blue-600 text-white" : "border-blue-200 bg-white/80 text-blue-800 hover:bg-blue-50 dark:border-blue-500/30 dark:bg-blue-900/30 dark:text-blue-200"}`}
                            >
                              {opt}
                            </button>
                          ))}
                        </div>
                      )}
                      <textarea
                        value={questionAnswer}
                        onChange={(e) => setQuestionAnswer(e.target.value)}
                        placeholder="Type your answer here…"
                        rows={2}
                        className="mt-3 w-full rounded-xl border border-blue-200 bg-white/90 px-3 py-2 text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-400 dark:border-blue-500/30 dark:bg-blue-900/20 dark:text-gray-100"
                      />
                      <button
                        disabled={!questionAnswer.trim() || submittingAnswer}
                        onClick={() => void submitAnswer()}
                        className="mt-3 inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-blue-500 active:scale-95 disabled:opacity-40"
                      >
                        {submittingAnswer && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                        Submit Answer
                      </button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );

            if (lastUserIndex === -1) {
              return (
                <>
                  {executionTrace}
                  <AnimatePresence initial={false}>
                    {validMessages.map((item, index) => {
                      const isCurrentStreaming = loading && index === validMessages.length - 1 && item.role === "agent";
                      return renderSingleMessage(item, index, isCurrentStreaming, index === 0);
                    })}
                  </AnimatePresence>
                </>
              );
            }

            const priorMessages = validMessages.slice(0, lastUserIndex);
            const currentTurnUser = validMessages[lastUserIndex];
            const currentTurnAgentMessages = validMessages.slice(lastUserIndex + 1);

            return (
              <>
                {/* 1. Prior chat history (if multi-turn) */}
                <AnimatePresence initial={false}>
                  {priorMessages.map((item, index) => {
                    const prevRole = index > 0 ? priorMessages[index - 1].role : null;
                    const isFirstInGroup = item.role !== prevRole;
                    return renderSingleMessage(item, index, false, isFirstInGroup);
                  })}
                </AnimatePresence>

                {/* 2. User Prompt for Current Turn */}
                <AnimatePresence initial={false}>
                  {renderSingleMessage(currentTurnUser, lastUserIndex, false, true)}
                </AnimatePresence>

                {/* 3. Antigravity Execution / Tool Steps / Stepper (ALWAYS ABOVE summary) */}
                {executionTrace}

                {/* 4. Generated Summary / Response (ALWAYS BELOW the tool execution trace) */}
                <AnimatePresence initial={false}>
                  {currentTurnAgentMessages.map((item, idx) => {
                    const globalIndex = lastUserIndex + 1 + idx;
                    const isCurrentStreaming = loading && idx === currentTurnAgentMessages.length - 1;
                    return renderSingleMessage(item, globalIndex, isCurrentStreaming, idx === 0);
                  })}
                </AnimatePresence>
              </>
            );
          })()}

          {/* Final Report Card (Protocol 4 — pinned, non-collapsible) */}
          {runStatus === "cancelled" && (
            <div className="max-w-2xl rounded-2xl border border-gray-600/40 bg-gray-900/40 p-4 text-sm text-gray-300" role="status">
              <div className="font-semibold">Run cancelled</div>
              <p className="mt-1 text-xs text-gray-500">Completed task evidence and the partial transcript are preserved.</p>
            </div>
          )}
          {finalReport && (
            <div className={`max-w-2xl rounded-2xl border p-5 text-sm backdrop-blur-md ${
              finalReport.outcome === "SUCCESS" ? "border-emerald-500/40 bg-emerald-950/20" :
              finalReport.outcome === "PARTIAL" ? "border-amber-500/40 bg-amber-950/20" :
              "border-red-500/40 bg-red-950/20"
            }`}>
              <div className="flex items-center gap-2.5">
                <span className={`rounded-md px-2.5 py-0.5 text-xs font-bold tracking-wide ${
                  finalReport.outcome === "SUCCESS" ? "bg-emerald-500/20 text-emerald-300" :
                  finalReport.outcome === "PARTIAL" ? "bg-amber-500/20 text-amber-300" :
                  "bg-red-500/20 text-red-300"
                }`}>
                  {finalReport.outcome}
                </span>
                <span className="font-semibold text-gray-200">Final Report</span>
              </div>
              <ul className="mt-3 space-y-1">
                {finalReport.done.map((item: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-gray-300">
                    <span className="mt-0.5 text-emerald-400">✓</span>
                    {item}
                  </li>
                ))}
              </ul>
              {finalReport.verification && (
                <p className="mt-3 text-xs text-gray-400"><span className="font-semibold text-gray-300">Verification:</span> {finalReport.verification}</p>
              )}
              {finalReport.errors && (
                <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-black/40 p-2.5 font-mono text-[11px] text-red-300 border border-red-500/20">
                  {finalReport.errors}
                </pre>
              )}
            </div>
          )}

          {/* Diagnostic Error / Failure Card */}
          <AnimatePresence>
            {(error || phase === "failed" || phase === "interrupted") && (
              <motion.div
                initial={{ opacity: 0, scale: 0.98, y: 4 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.98, y: -4 }}
                transition={{ duration: 0.18 }}
                className="max-w-2xl rounded-2xl border border-red-200 bg-red-50/90 p-4 text-sm shadow-sm dark:border-red-900/50 dark:bg-red-950/30"
                role="alert"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-semibold text-red-700 dark:text-red-400">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>{phase === "interrupted" ? "Agent Interrupted" : "Agent Error"}</span>
                    {runState.run?.error_category && (
                      <span className="rounded-md border border-red-300 bg-red-100 px-2 py-0.5 font-mono text-[10px] text-red-800 dark:border-red-800/60 dark:bg-red-900/50 dark:text-red-300">
                        {runState.run.error_category}
                      </span>
                    )}
                  </div>
                  {runState.run?.id && (
                    <button
                      type="button"
                      onClick={() => void handleRetryRun()}
                      className="flex items-center gap-1.5 rounded-lg border border-red-300 bg-white/80 px-2.5 py-1 text-xs font-semibold text-red-700 shadow-sm transition hover:bg-white active:scale-95 dark:border-red-700/60 dark:bg-red-900/40 dark:text-red-200 dark:hover:bg-red-900/70"
                    >
                      <RotateCcw className="h-3 w-3" />
                      Retry Run
                    </button>
                  )}
                </div>
                <p className="mt-1.5 font-medium text-red-600 dark:text-red-300">{error || runState.run?.error_message || "Agent run encountered a failure."}</p>
                {(runState.run?.support_id || runState.run?.planner_model || activeTaskId) && (
                  <div className="mt-2.5 border-t border-red-200/60 pt-2 text-[11px] text-red-700/80 dark:border-red-800/40 dark:text-red-400">
                    <button
                      type="button"
                      onClick={() => setDiagnosticExpanded((v) => !v)}
                      className="flex items-center gap-1 font-mono hover:underline"
                    >
                      {diagnosticExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                      Diagnostics details
                    </button>
                    {diagnosticExpanded && (
                      <div className="mt-1.5 space-y-1 font-mono text-[10px]">
                        {runState.run?.support_id && <div>Support ID: {runState.run.support_id}</div>}
                        {activeTaskId && <div>Task ID: {activeTaskId}</div>}
                        {runState.run?.planner_model && <div>Model: {runState.run.planner_model}</div>}
                        {runState.run?.workspace_base_revision && <div>Base Revision: {runState.run.workspace_base_revision}</div>}
                      </div>
                    )}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          <div ref={bottom} />
        </div>
        <form onSubmit={send} onDragOver={(event) => { event.preventDefault(); setDraggingFiles(true); }} onDragLeave={() => setDraggingFiles(false)} onDrop={(event) => { event.preventDefault(); setDraggingFiles(false); void addFiles(event.dataTransfer.files); }} className={`border-t border-white/5 bg-zinc-900 p-3 ${draggingFiles ? "bg-blue-950/40" : ""}`}>
          <div className="relative mx-auto max-w-4xl rounded-2xl border border-white/10 bg-zinc-800 px-3 pt-2 shadow-2xl shadow-black/20 focus-within:border-blue-500/50">
            <input ref={attachmentInput} type="file" multiple accept="image/*,.pdf,.txt,.md,.json,.csv,.xml,.py,.js,.ts" className="hidden" onChange={(event) => { if (event.target.files) void addFiles(event.target.files); event.target.value = ""; }} />
            {attachments.length > 0 && <div className="mb-2 flex flex-wrap gap-1.5">{attachments.map((file, index) => <span key={`${file.name}-${index}`} className="flex max-w-60 items-center gap-1.5 rounded-lg border border-white/10 bg-zinc-950 px-2 py-1 text-[10px] text-gray-300">{file.type.startsWith("image/") ? <img src={file.content} alt="" className="h-6 w-6 rounded object-cover" /> : file.type === "application/pdf" ? <FileText className="h-4 w-4 shrink-0 text-red-300" /> : <ImageIcon className="h-4 w-4 shrink-0 text-gray-500" />}<span className="truncate">{file.name}</span><button type="button" onClick={() => setAttachments((current) => current.filter((_, item) => item !== index))} className="text-gray-500 hover:text-white" aria-label={`Remove ${file.name}`}>×</button></span>)}</div>}
            <textarea
              ref={inputRef}
              rows={1}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={!isInteractive || loading || Boolean(pending)}
              placeholder={pending ? "Resolve the pending action first" : runWorking ? "Agent is working…" : "Ask the agent… (⌘↵ to send)"}
              className="w-full resize-none overflow-hidden bg-transparent px-2 py-2 pr-12 text-sm text-white placeholder:text-gray-500 focus:outline-none"
              style={{ fieldSizing: "content", maxHeight: "9rem" } as React.CSSProperties}
            />
            <div className={`flex items-center gap-1 border-t border-white/5 py-2 ${draggingFiles ? "text-blue-300" : "text-gray-600"}`}><button type="button" onClick={() => attachmentInput.current?.click()} className="rounded-lg px-2 py-1 text-lg text-gray-400 hover:bg-white/10 hover:text-white" aria-label="Attach files" title="Attach files">＋</button><span className="text-[10px]">{draggingFiles ? "Release to attach files" : "Drop files, PDFs, or images here"}</span></div>
            {canStop ? (
              <button
                type="button"
                onClick={() => void handleStopRun()}
                className="absolute bottom-2 right-2 rounded-xl bg-red-600/20 p-2 text-red-400 transition-transform hover:bg-red-600/40 hover:text-red-300 active:scale-95"
                title="Stop generation"
                aria-label="Stop generation"
              >
                <Square className="h-4 w-4 fill-current" />
              </button>
            ) : (
              <button
                type="submit"
                aria-label="Send"
                disabled={!isInteractive || loading || !message.trim() || Boolean(pending)}
                className="absolute bottom-2 right-2 rounded-xl bg-blue-600 p-2 text-white transition-transform hover:bg-blue-500 active:scale-95 disabled:opacity-30"
              >
                <Send className="h-4 w-4" />
              </button>
            )}
          </div>
          {usage && <p className="mt-1.5 text-center font-mono text-[10px] text-gray-600">{usage}</p>}
        </form>
      </section>

      {/* Resizable & Adaptive Right IDE Split-Pane Panel */}
      {showRightPanel && (
        <>
          {/* Draggable Resizer Handle Bar */}
          <div
            onPointerDown={() => setIsResizingRight(true)}
            onKeyDown={(event) => {
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
              event.preventDefault();
              setRightPanelWidth((width) => Math.min(window.innerWidth * 0.75, Math.max(320, width + (event.key === "ArrowLeft" ? 20 : -20))));
            }}
            role="separator"
            aria-label="Resize workspace panel"
            aria-orientation="vertical"
            aria-valuemin={320}
            aria-valuemax={Math.round(typeof window === "undefined" ? 1200 : window.innerWidth * 0.75)}
            aria-valuenow={Math.round(rightPanelWidth)}
            tabIndex={0}
            className={`group relative z-20 hidden w-1.5 cursor-col-resize hover:bg-blue-500/60 active:bg-blue-600 xl:block ${
              isResizingRight ? "bg-blue-600" : "bg-white/5"
            }`}
            title="Drag to resize IDE panel width"
          >
            <div className="absolute inset-y-0 -left-1 -right-1" />
          </div>

          <aside
            style={{ "--ide-width": `${rightPanelWidth}px` } as React.CSSProperties}
            className="fixed inset-0 z-40 flex w-full shrink-0 flex-col border-l border-white/10 bg-zinc-900 xl:static xl:z-auto xl:w-[var(--ide-width)]"
          >
          {/* Tabs Navigation Header */}
          <div className="flex items-center justify-between border-b border-white/10 bg-zinc-950 px-2 py-1.5">
            <div className="flex items-center gap-1 text-xs">
              <button
                onClick={() => setRightPanelTab("code")}
                title="Code & Files"
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "code" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <Code2 className="h-3.5 w-3.5 text-purple-400" />
                {!compactIdePanel && "Code & Files"}
              </button>
              <button
                onClick={() => setRightPanelTab("diff")}
                title="Git Diff"
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "diff" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <GitBranch className="h-3.5 w-3.5 text-blue-400" />
                {!compactIdePanel && "Git Diff"}
              </button>
              <button
                onClick={() => setRightPanelTab("terminal")}
                title="Terminal"
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${rightPanelTab === "terminal" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"}`}
              >
                <Terminal className="h-3.5 w-3.5 text-amber-400" />
                {!compactIdePanel && "Terminal"}
              </button>
              <button
                onClick={() => setRightPanelTab("memory")}
                title="Memories"
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "memory" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <Brain className="h-3.5 w-3.5 text-amber-400" />
                {!compactIdePanel && "Memories"}
              </button>
              <button
                onClick={() => setRightPanelTab("evidence")}
                title="Artifacts"
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "evidence" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <Layers className="h-3.5 w-3.5 text-emerald-400" />
                {!compactIdePanel && "Artifacts"}
              </button>
            </div>
            <div className="flex items-center gap-2">
              <button
                disabled={deploying}
                onClick={() => void handleQuickDeploy()}
                title="Package module and run disposable Odoo installation tests"
                className="flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-950/40 px-2.5 py-1 text-xs font-semibold text-emerald-300 transition hover:bg-emerald-900/60 active:scale-95 disabled:opacity-40"
              >
                {deploying ? <Loader2 className="h-3 w-3 animate-spin text-emerald-400" /> : <Rocket className="h-3 w-3 text-emerald-400" />}
                <span>{deploying ? "Validating…" : "Validate module"}</span>
              </button>
              <button type="button" onClick={() => setShowRightPanel(false)} className="rounded p-1 text-gray-400 hover:bg-white/10 hover:text-white xl:hidden" aria-label="Close workspace panel"><PanelRightClose className="h-4 w-4" /></button>
            </div>
          </div>

          {deployMsg && (
            <div className="flex items-center justify-between border-b border-emerald-500/20 bg-emerald-950/30 px-3 py-1.5 text-xs text-emerald-300">
              <span>{deployMsg}</span>
              <button onClick={() => setDeployMsg(null)} className="ml-2 text-emerald-400 hover:text-emerald-200">✕</button>
            </div>
          )}

          {workspaceError && <div className="flex items-center justify-between border-b border-red-500/20 bg-red-950/30 px-3 py-2 text-xs text-red-300" role="alert"><span>{workspaceError}</span><button onClick={() => void loadWorkspaceDetails()} className="font-semibold underline">Retry</button></div>}

          {/* Tab Contents */}
          <div className="flex-1 overflow-hidden">
            {rightPanelTab === "code" && (
              <div className="grid h-full grid-cols-[180px_1fr]">
                {/* File Tree */}
                <div className="border-r border-white/10 bg-zinc-950 p-2 overflow-y-auto">
                  <div className="mb-2 text-[10px] font-semibold uppercase text-gray-500">Workspace Files</div>
                  <form onSubmit={searchWorkspace} className="mb-2 flex gap-1">
                    <input
                      value={workspaceSearchQuery}
                      onChange={(event) => setWorkspaceSearchQuery(event.target.value)}
                      placeholder="Search files"
                      aria-label="Search workspace files"
                      className="min-w-0 flex-1 rounded border border-white/10 bg-zinc-900 px-2 py-1.5 text-[10px] text-white outline-none focus:border-blue-500"
                    />
                    <button type="submit" aria-label="Search workspace" className="rounded border border-white/10 px-2 text-xs text-cyan-300 hover:bg-white/10">⌕</button>
                  </form>
                  {workspaceSearchResults.length > 0 && (
                    <ul className="mb-2 space-y-1 rounded border border-cyan-500/20 bg-cyan-950/10 p-1.5">
                      {workspaceSearchResults.map((result, index) => (
                        <li key={`${result.path}:${result.line}:${index}`}>
                          <button
                            type="button"
                            className="block w-full truncate text-left font-mono text-[9px] text-cyan-300 hover:text-white"
                            onClick={() => { setSelectedFile(result.path); void openFile(result.path); }}
                          >
                            {result.path}:{result.line}
                          </button>
                          <div className="truncate text-[9px] text-gray-500">{result.text}</div>
                        </li>
                      ))}
                    </ul>
                  )}
                  <WorkspaceFileTree key={runState.selectedRunId || "base-workspace"} projectId={projectId} runId={runState.selectedRunId} entries={entries} selected={selectedFile} onOpen={(path) => void openFile(path)} onError={setWorkspaceError} />
                </div>
                {/* Code Viewer */}
                <div className="flex-1 overflow-hidden">
                  <CodeDiffViewer path={selectedFile} content={fileContent} />
                </div>
              </div>
            )}

            {rightPanelTab === "diff" && (
              <div className="flex h-full flex-col">
                {workspaceStatus && <div className="border-b border-white/10 bg-zinc-950 px-3 py-2 text-[10px] text-gray-400"><div className="flex items-center justify-between"><span>Branch: <code className="text-blue-300">{workspaceStatus.branch}</code></span><span className={workspaceStatus.clean ? "text-emerald-400" : "text-amber-400"}>{workspaceStatus.clean ? "clean" : `${workspaceStatus.changes.length} change${workspaceStatus.changes.length === 1 ? "" : "s"}`}</span></div>{workspaceStatus.changes.length > 0 && <div className="mt-1 truncate text-gray-500">{workspaceStatus.changes.map((change) => change.path).join(" · ")}</div>}</div>}
                <div className="min-h-0 flex-1">
                <CodeDiffViewer path="Workspace Uncommitted / Commit Diff" content={diffContent || "No active diff changes in workspace."} isDiff={true} />
                </div>
              </div>
            )}

            {rightPanelTab === "terminal" && (
              <div className="flex h-full flex-col bg-zinc-950 p-3">
                <form onSubmit={runTerminalCommand} className="space-y-2">
                  <input value={terminalCwd} onChange={(event) => setTerminalCwd(event.target.value)} aria-label="Terminal working directory" placeholder="Working directory (.)" className="w-full rounded border border-white/10 bg-zinc-900 px-2.5 py-2 font-mono text-xs text-white outline-none focus:border-blue-500" />
                  <div className="flex gap-2">
                    <input value={terminalCommand} onChange={(event) => setTerminalCommand(event.target.value)} aria-label="Terminal command" placeholder="Run a command (approval required)" className="min-w-0 flex-1 rounded border border-white/10 bg-zinc-900 px-2.5 py-2 font-mono text-xs text-white outline-none focus:border-blue-500" />
                    <button disabled={terminalSubmitting || !terminalCommand.trim()} className="rounded bg-blue-600 px-3 text-xs text-white disabled:opacity-40">{terminalSubmitting ? "…" : "Run"}</button>
                  </div>
                </form>
                <div className="mt-3 flex-1 space-y-2 overflow-y-auto">
                  {terminalSessions.length === 0 ? <p className="text-xs text-gray-500">Commands are paused for approval and their output remains attached to the session.</p> : terminalSessions.map((session) => (
                    <article key={session.id} className="rounded border border-white/10 bg-zinc-900 p-2.5 text-xs">
                      <div className="flex items-center justify-between gap-2"><code className="truncate text-amber-300">$ {session.command}</code><span className={session.status === "succeeded" ? "text-emerald-400" : session.status === "failed" ? "text-red-400" : "text-gray-400"}>{session.status}</span></div>
                      {session.output && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[10px] text-gray-400">{session.output}</pre>}
                    </article>
                  ))}
                </div>
              </div>
            )}

            {rightPanelTab === "memory" && (
              <div className="h-full">
                <LearnedMemories projectId={projectId} />
              </div>
            )}

            {rightPanelTab === "evidence" && (
              <div className="h-full overflow-y-auto p-4 space-y-4 text-xs">
                <div>
                  <h3 className="font-semibold text-white mb-2">Build Artifacts ({artifacts.length})</h3>
                  {artifacts.length === 0 ? (
                    <p className="text-gray-500">No packaged artifacts yet.</p>
                  ) : (
                    <ul className="space-y-2">
                      {artifacts.map((a) => (
                        <li key={a.id} className="rounded-lg border border-white/10 bg-zinc-800/60 p-2.5">
                          <div className="flex justify-between font-semibold text-purple-300">
                            <span>{a.name} v{a.version}</span>
                            <span className="text-[10px] uppercase text-emerald-400">{a.status}</span>
                          </div>
                          <p className="mt-1 font-mono text-[10px] text-gray-500 truncate">SHA256: {a.digest}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div>
                  <h3 className="font-semibold text-white mb-2">Deployments ({deployments.length})</h3>
                  {deployments.length === 0 ? (
                    <p className="text-gray-500">No deployment jobs requested yet.</p>
                  ) : (
                    <ul className="space-y-2">
                      {deployments.map((d) => (
                        <li key={d.id} className="rounded-lg border border-white/10 bg-zinc-800/60 p-2.5">
                          <div className="flex justify-between font-semibold text-blue-300">
                            <span>Env: {d.environment}</span>
                            <span className="text-[10px] uppercase text-amber-400">{d.status}</span>
                          </div>
                          {d.logs && <pre className="mt-1.5 max-h-24 overflow-auto rounded bg-zinc-950 p-2 text-[10px] text-gray-400">{d.logs}</pre>}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="border-t border-white/10 pt-4">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="font-semibold text-white">Recurring agent runs</h3>
                    <span className="text-[10px] text-gray-500">{schedules.length} configured</span>
                  </div>
                  <form onSubmit={createSchedule} className="space-y-2 rounded-lg border border-white/10 bg-zinc-800/60 p-2.5">
                    <input
                      value={schedulePrompt}
                      onChange={(event) => setSchedulePrompt(event.target.value)}
                      placeholder="Prompt to run periodically"
                      aria-label="Recurring agent prompt"
                      className="w-full rounded border border-white/10 bg-zinc-950 px-2 py-1.5 text-xs text-white outline-none focus:border-blue-500"
                    />
                    <div className="flex gap-2">
                      <select value={scheduleInterval} onChange={(event) => setScheduleInterval(event.target.value)} aria-label="Recurring run interval" className="min-w-0 flex-1 rounded border border-white/10 bg-zinc-950 px-2 py-1.5 text-xs text-gray-300">
                        <option value="3600">Every hour</option>
                        <option value="21600">Every 6 hours</option>
                        <option value="86400">Every day</option>
                        <option value="604800">Every week</option>
                      </select>
                      <button disabled={scheduleSubmitting || !schedulePrompt.trim()} className="rounded bg-cyan-700 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">{scheduleSubmitting ? "…" : "Add"}</button>
                    </div>
                  </form>
                  {schedules.length > 0 && <ul className="mt-2 space-y-1.5">
                    {schedules.map((schedule) => (
                      <li key={schedule.id} className="rounded border border-white/10 bg-zinc-800/40 p-2">
                        <div className="flex items-start justify-between gap-2">
                          <span className={schedule.enabled ? "text-gray-300" : "text-gray-600 line-through"}>{schedule.prompt}</span>
                          <div className="flex shrink-0 gap-2 text-[10px]">
                            <button type="button" onClick={() => void toggleSchedule(schedule)} className="text-cyan-300 hover:text-white">{schedule.enabled ? "Pause" : "Resume"}</button>
                            <button type="button" onClick={() => void removeSchedule(schedule)} className="text-red-300 hover:text-red-200">Delete</button>
                          </div>
                        </div>
                        {(() => { const latest = schedule.last_run_id ? runs.find((run) => run.id === schedule.last_run_id) : null; return <div className="mt-2 space-y-1 text-[10px]">
                          <div className="text-gray-500">Next run: {new Date(schedule.next_run_at).toLocaleString()}</div>
                          <div className={schedule.last_error ? "rounded bg-amber-950/50 px-2 py-1 text-amber-200" : "text-gray-500"}>
                            {schedule.last_error ? `Action needed: ${schedule.last_error}` : latest ? `Latest run: ${latest.status} · ${new Date(latest.created_at).toLocaleString()}` : "Latest run: not dispatched yet"}
                          </div>
                        </div>; })()}
                      </li>
                    ))}
                  </ul>}
                </div>
              </div>
            )}
          </div>
        </aside>
        </>
      )}

      <RunHistoryDialog open={showHistoryModal} runs={runs} activeRunId={activeRunId} selectedRunId={runState.selectedRunId} onOpenRun={(run) => void openRun(run)} onDeleteRun={(id) => void handleDeleteRun(id)} onClose={() => setShowHistoryModal(false)} />
    </div>
  );
}
