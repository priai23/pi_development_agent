"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Bot, Brain, Code2, Database, FileText, Folder, GitBranch, History, Layers, Loader2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Send, Square, Trash2 } from "lucide-react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import MessageContent from "@/components/MessageContent";
import AgentStatus from "@/components/AgentStatus";
import ActivityStepper from "@/components/ActivityStepper";
import LearnedMemories from "@/components/LearnedMemories";
import CodeDiffViewer from "@/components/CodeDiffViewer";
import { apiFetch, AgentRun, AgentQuestion, Artifact, ChatMessage, deleteRun, Deployment, FinalReport, followRun, Instance, PendingAction, Project, Step, ToolEvent, WorkspaceEntry, visibleContent } from "@/lib/api";

type PendingRecord = { id: string; tool_name: string; preview: Record<string, unknown>; risk_class: string; expires_at: string };

export default function ProjectWorkspace() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const [project, setProject] = useState<Project | null>(null);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [url, setUrl] = useState("");
  const [dbName, setDbName] = useState("");
  const [detectedDatabases, setDetectedDatabases] = useState<string[]>([]);
  const [detectingDatabases, setDetectingDatabases] = useState(false);
  const [discoveryMessage, setDiscoveryMessage] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [authMethod, setAuthMethod] = useState<"json2" | "xmlrpc">("json2");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [usage, setUsage] = useState("");
  const [tokenInputs, setTokenInputs] = useState(0);
  const [currentTool, setCurrentTool] = useState<string | null>(null);
  const [isThinking, setIsThinking] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [isStuck, setIsStuck] = useState(false);
  const [agentQuestion, setAgentQuestion] = useState<AgentQuestion | null>(null);
  const [questionAnswer, setQuestionAnswer] = useState("");
  const [submittingAnswer, setSubmittingAnswer] = useState(false);
  const [finalReport, setFinalReport] = useState<FinalReport | null>(null);
  const [thinkingText, setThinkingText] = useState<string | null>(null);
  const [showLeftSidebar, setShowLeftSidebar] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(true);
  const [rightPanelWidth, setRightPanelWidth] = useState(520);
  const [isResizingRight, setIsResizingRight] = useState(false);
  const [rightPanelTab, setRightPanelTab] = useState<"code" | "diff" | "memory" | "evidence">("code");
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [diffContent, setDiffContent] = useState("");
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  // A2A Supervisor task graph state
  const [supervisorTaskGraph, setSupervisorTaskGraph] = useState<Array<{ task_id: string; title: string; status: string; risk_class: number; retry_count: number; heartbeat_at: string | null }> | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [recoveringTaskId, setRecoveringTaskId] = useState<string | null>(null);
  const [finalReportSeen, setFinalReportSeen] = useState(false);
  const [plannerModel, setPlannerModel] = useState("gpt-4o");
  const [fallbackModel, setFallbackModel] = useState("gpt-4o-mini");
  const bottom = useRef<HTMLDivElement>(null);
  const discoveryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const discoverySequence = useRef(0);
  const lastEventAt = useRef<number>(0);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Restore & save panel layout preferences in localStorage
  useEffect(() => {
    const savedSidebar = localStorage.getItem("workspace:showLeftSidebar");
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (savedSidebar !== null) setShowLeftSidebar(savedSidebar === "true");
    const savedWidth = localStorage.getItem("workspace:rightPanelWidth");
    if (savedWidth !== null) {
      const parsed = Number(savedWidth);
      if (parsed >= 320 && parsed <= window.innerWidth * 0.75) setRightPanelWidth(parsed);
    }
  }, []);

  const toggleLeftSidebar = () => {
    setShowLeftSidebar((prev) => {
      const next = !prev;
      localStorage.setItem("workspace:showLeftSidebar", String(next));
      return next;
    });
  };

  // Mouse drag handler for dynamic right panel width resizing
  useEffect(() => {
    if (!isResizingRight) return;
    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth >= 320 && newWidth <= window.innerWidth * 0.75) {
        setRightPanelWidth(newWidth);
        localStorage.setItem("workspace:rightPanelWidth", String(newWidth));
      }
    };
    const handleMouseUp = () => {
      setIsResizingRight(false);
    };
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isResizingRight]);

  const loadWorkspaceDetails = useCallback(async () => {
    try {
      const [treeData, diffData, artifactData, deploymentData] = await Promise.all([
        apiFetch<WorkspaceEntry[]>(`/projects/${projectId}/workspace/tree`).catch(() => []),
        apiFetch<{ diff: string }>(`/projects/${projectId}/workspace/diff`).catch(() => ({ diff: "" })),
        apiFetch<Artifact[]>(`/projects/${projectId}/artifacts`).catch(() => []),
        apiFetch<Deployment[]>(`/projects/${projectId}/deployments`).catch(() => []),
      ]);
      setEntries(treeData);
      setDiffContent(diffData.diff);
      setArtifacts(artifactData);
      setDeployments(deploymentData);
    } catch {
      // ignore
    }
  }, [projectId]);

  const openFile = async (path: string) => {
    try {
      const file = await apiFetch<{ content: string }>(`/projects/${projectId}/workspace/files?path=${encodeURIComponent(path)}`);
      setSelectedFile(path);
      setFileContent(file.content);
    } catch {
      // ignore
    }
  };

  const load = useCallback(async () => {
    try {
      const [projectData, instanceData, chatData, pendingData] = await Promise.all([
        apiFetch<Project>(`/projects/${projectId}`),
        apiFetch<Instance[]>(`/projects/${projectId}/instances`),
        apiFetch<ChatMessage[]>(`/projects/${projectId}/chat`),
        apiFetch<PendingRecord | null>(`/projects/${projectId}/actions/pending`),
      ]);
      setProject(projectData); setInstances(instanceData); setChat(chatData);
      setPending(pendingData ? { id: pendingData.id, tool: pendingData.tool_name, preview: pendingData.preview, risk_class: pendingData.risk_class } : null);
      // Restore step history from the last run
      const runsData = await apiFetch<AgentRun[]>(`/projects/${projectId}/runs`).catch(() => [] as AgentRun[]);
      setRuns(runsData);
      if (runsData.length > 0) {
        if (runsData[0].status === "running" || runsData[0].status === "cancelling") {
          setActiveRunId(runsData[0].id);
        }
        const events = await apiFetch<ToolEvent[]>(`/runs/${runsData[0].id}/events`).catch(() => [] as ToolEvent[]);
        const rebuilt: Step[] = [];
        for (const ev of events) {
          if (ev.event_type === "tool.started") {
            rebuilt.push({ tool: String(ev.payload.tool), label: String(ev.payload.tool).replace(/_/g, " "), status: "running", startedAt: new Date(ev.created_at).getTime() });
          } else if (ev.event_type === "tool.completed") {
            const idx = [...rebuilt].reverse().findIndex(s => s.tool === String(ev.payload.tool) && s.status === "running");
            if (idx !== -1) {
              const realIdx = rebuilt.length - 1 - idx;
              const raw = String(ev.payload.result || "").slice(0, 80).replace(/\n/g, " ");
              rebuilt[realIdx] = { ...rebuilt[realIdx], status: "done", result: raw, elapsed: (new Date(ev.created_at).getTime() - rebuilt[realIdx].startedAt) / 1000 };
            }
          } else if (ev.event_type === "usage") {
            setUsage(`${ev.payload.input_tokens || 0} input · ${ev.payload.output_tokens || 0} output tokens · $${Number(ev.payload.cost_usd || 0).toFixed(4)}`);
            setTokenInputs(Number(ev.payload.input_tokens || 0));
          }
        }
        setSteps(rebuilt);
      }
      void loadWorkspaceDetails();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load project"); }
    finally { setLoading(false); }
  }, [projectId, loadWorkspaceDetails]);

  useEffect(() => {
    // State changes occur after the API promises resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [chat, pending]);
  useEffect(() => () => {
    if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    if (heartbeatRef.current) clearInterval(heartbeatRef.current);
  }, []);

  // 60s heartbeat: detect agent hang
  useEffect(() => {
    if (!loading && !deciding) {
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsStuck(false);
      return;
    }
    lastEventAt.current = Date.now();
    setIsStuck(false);
    heartbeatRef.current = setInterval(() => {
      if (Date.now() - lastEventAt.current > 60_000) setIsStuck(true);
    }, 5_000);
    return () => { if (heartbeatRef.current) clearInterval(heartbeatRef.current); };
  }, [loading, deciding]);

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
      const result = await apiFetch<{ status: string; databases: string[]; suggested_username: string; message?: string }>("/instances/detect", {
        method: "POST",
        body: JSON.stringify({ url: normalizedUrl, erp_type: "odoo" }),
      });
      if (sequence !== discoverySequence.current) return;
      const databases = result.databases || [];
      setDetectedDatabases(databases);
      if (databases.length === 1) {
        setDbName(databases[0]);
        setDiscoveryMessage(`Database “${databases[0]}” selected automatically.`);
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
    event.preventDefault(); setLoading(true); setError("");
    try {
      await apiFetch<Instance>("/instances", { method: "POST", body: JSON.stringify({ erp_type: "odoo", url, db_name: dbName, username: authMethod === "xmlrpc" ? username : null, password: authMethod === "xmlrpc" ? password : null, api_key: authMethod === "json2" ? apiKey : null, auth_method: authMethod, project_id: projectId }) });
      setPassword(""); setApiKey(""); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Connection failed"); setLoading(false); }
  };

  const autoSelectFirstFile = useCallback(async () => {
    try {
      const treeData = await apiFetch<WorkspaceEntry[]>(`/projects/${projectId}/workspace/tree`).catch(() => []);
      const diffData = await apiFetch<{ diff: string }>(`/projects/${projectId}/workspace/diff`).catch(() => ({ diff: "" }));
      setEntries(treeData);
      setDiffContent(diffData.diff);
      const fileEntries = treeData.filter((e) => e.type === "file");
      if (fileEntries.length > 0) {
        const target = fileEntries.find((f) => f.path.includes("models/") || f.path.includes("views/") || f.path.includes("manifest")) || fileEntries[0];
        setSelectedFile(target.path);
        setRightPanelTab("code");
        void openFile(target.path);
      }
    } catch {
      // ignore
    }
  }, [projectId]);

  // Shared helper — wires SSE events to structured steps, heartbeat, tokens
  const handleRunEvent = (runEvent: ToolEvent, responseRef: { current: string }, setResponse: (r: string) => void) => {
    lastEventAt.current = Date.now();
    setIsStuck(false);
    setIsThinking(true);
    if (runEvent.event_type === "message.delta") {
      responseRef.current += String(runEvent.payload.text || "");
      setResponse(responseRef.current);
      setIsThinking(false);
    }
    if (runEvent.event_type === "tool.started") {
      const tool = String(runEvent.payload.tool);
      if (tool !== "emit_thinking" && tool !== "thinking") {
        setCurrentTool(tool);
        setIsThinking(false);
        setSteps(prev => [...prev, { tool, label: tool.replace(/_/g, " "), status: "running", startedAt: Date.now() }]);
      }
    }
    if (runEvent.event_type === "tool.completed") {
      const tool = String(runEvent.payload.tool);
      const raw = String(runEvent.payload.result || "").slice(0, 80).replace(/\n/g, " ");
      setCurrentTool(null);
      setIsThinking(true);
      setSteps(prev => {
        const idx = [...prev].reverse().findIndex(s => s.tool === tool && s.status === "running");
        if (idx === -1) return prev;
        const realIdx = prev.length - 1 - idx;
        const next = [...prev];
        const elapsedSec = Math.max(0.1, (Date.now() - next[realIdx].startedAt) / 1000);
        next[realIdx] = { ...next[realIdx], status: "done", result: raw, elapsed: elapsedSec };
        return next;
      });
    }
    if (runEvent.event_type === "usage") {
      const inp = Number(runEvent.payload.input_tokens || 0);
      setTokenInputs(inp);
      setUsage(`${inp} input · ${runEvent.payload.output_tokens || 0} output tokens · $${Number(runEvent.payload.cost_usd || 0).toFixed(4)}`);
    }
    if (runEvent.event_type === "approval.required") {
      setIsThinking(false);
      setCurrentTool(null);
      setPending({ id: String(runEvent.payload.action_id), tool: String(runEvent.payload.tool), risk_class: String(runEvent.payload.risk_class), preview: runEvent.payload.preview as Record<string, unknown> });
    }
    if (runEvent.event_type === "thinking") {
      setThinkingText(String(runEvent.payload.message || ""));
    }
    if (runEvent.event_type === "question") {
      setIsThinking(false);
      setCurrentTool(null);
      setAgentQuestion({ question: String(runEvent.payload.question || ""), options: (runEvent.payload.options as string[]) || [] });
    }
    if (runEvent.event_type === "final_report") {
      setFinalReportSeen(true);
      setFinalReport({
        outcome: String(runEvent.payload.outcome || "SUCCESS") as "SUCCESS" | "PARTIAL" | "FAILED",
        done: (runEvent.payload.done as string[]) || [],
        verification: String(runEvent.payload.verification || ""),
        errors: String(runEvent.payload.errors || ""),
        pending_approvals: String(runEvent.payload.pending_approvals || ""),
      });
      void autoSelectFirstFile();
    }
    // ── A2A Supervisor events ────────────────────────────────────────────────
    if (runEvent.event_type === "supervisor.plan") {
      const graph = runEvent.payload.task_graph as typeof supervisorTaskGraph;
      setSupervisorTaskGraph(graph);
      setActiveTaskId(String(runEvent.payload.active_task_id || ""));
    }
    if (runEvent.event_type === "task.started") {
      setActiveTaskId(String(runEvent.payload.task_id || ""));
      setRecoveringTaskId(null);
      // Update status in local graph copy
      setSupervisorTaskGraph(prev => prev ? prev.map(t =>
        t.task_id === runEvent.payload.task_id ? { ...t, status: "in_progress" } : t
      ) : prev);
    }
    if (runEvent.event_type === "task.recovering") {
      setRecoveringTaskId(String(runEvent.payload.task_id || ""));
      setIsStuck(false); // clear the generic stuck indicator
      setSupervisorTaskGraph(prev => prev ? prev.map(t =>
        t.task_id === runEvent.payload.task_id
          ? { ...t, status: "pending", retry_count: Number(runEvent.payload.attempt || 0) }
          : t
      ) : prev);
    }
    if (runEvent.event_type === "task.failed") {
      setSupervisorTaskGraph(prev => prev ? prev.map(t =>
        t.task_id === runEvent.payload.task_id ? { ...t, status: "failed" } : t
      ) : prev);
    }
    if (runEvent.event_type === "supervisor.complete") {
      const graph = runEvent.payload.task_graph as typeof supervisorTaskGraph;
      setSupervisorTaskGraph(graph);
      setActiveTaskId(null);
      setRecoveringTaskId(null);
    }
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = message.trim(); if (!text || pending) return;
    setMessage(""); setError(""); setLoading(true); setSteps([]); setUsage(""); setTokenInputs(0);
    setCurrentTool(null); setIsThinking(true);
    setSupervisorTaskGraph(null); setActiveTaskId(null); setRecoveringTaskId(null); setFinalReportSeen(false);
    setChat((current) => [...current, { role: "user", content: text }]);
    const responseRef = { current: "" };
    try {
      const run = await apiFetch<AgentRun>(`/projects/${projectId}/runs`, { 
        method: "POST", 
        body: JSON.stringify({ 
          message: text,
          planner_model: plannerModel,
          fallback_model: fallbackModel
        }) 
      });
      setActiveRunId(run.id);
      const finished = await followRun(run.id, (runEvent) => {
        handleRunEvent(runEvent, responseRef, (r) => setChat((cur) => {
          const last = cur[cur.length - 1];
          if (last?.role === "agent") return [...cur.slice(0, -1), { role: "agent", content: r }];
          return [...cur, { role: "agent", content: r }];
        }));
      });
      if (finished.status === "failed") throw new Error(`${finished.error_message || "Agent run failed"} · Support ${finished.support_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent request failed");
      setChat((current) => {
        if (current.length > 0 && current[current.length - 1].role === "agent" && !current[current.length - 1].content) {
          return current.slice(0, -1);
        }
        return current;
      });
    }
    finally { setLoading(false); setActiveRunId(null); setIsThinking(false); setCurrentTool(null); void autoSelectFirstFile(); }
  };

  const decide = async (decision: "approve" | "reject") => {
    if (!pending) return;
    const action = pending; setPending(null); setDeciding(true); setError("");
    setChat((current) => [...current, { role: "agent", content: "" }]);
    setCurrentTool(null); setIsThinking(true); setLoading(true);
    const responseRef = { current: "" };
    try {
      const run = await apiFetch<AgentRun>(`/actions/${action.id}/decision`, { method: "POST", body: JSON.stringify({ decision }) });
      const finished = await followRun(run.id, (runEvent) => {
        handleRunEvent(runEvent, responseRef, (r) => setChat((cur) => [...cur.slice(0, -1), { role: "agent", content: r }]));
        setChat((current) => [...current.slice(0, -1), { role: "agent", content: responseRef.current }]);
      });
      if (finished.status === "failed") throw new Error(`${finished.error_message || "Action failed"} · Support ${finished.support_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Decision failed");
      try {
        const freshPending = await apiFetch<PendingRecord | null>(`/projects/${projectId}/actions/pending`);
        setPending(freshPending ? { id: freshPending.id, tool: freshPending.tool_name, preview: freshPending.preview, risk_class: freshPending.risk_class } : null);
      } catch {
        // ignore
      }
    }
    finally { setDeciding(false); setLoading(false); setIsThinking(false); setCurrentTool(null); void autoSelectFirstFile(); }
  };

  const submitAnswer = async () => {
    if (!questionAnswer.trim() || submittingAnswer) return;
    const answer = questionAnswer.trim();
    setAgentQuestion(null);
    setQuestionAnswer("");
    setSubmittingAnswer(true);
    setLoading(true);
    setIsThinking(true);
    setChat((current) => [...current, { role: "agent", content: "" }]);
    const responseRef = { current: "" };
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001"}/projects/${projectId}/actions/answer`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": document.cookie.match(/csrf_token=([^;]+)/)?.[1] ?? "" },
        body: JSON.stringify({ answer }),
      });
      if (!response.ok) throw new Error("Failed to submit answer");
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        responseRef.current += decoder.decode(value, { stream: true });
        setChat((cur) => [...cur.slice(0, -1), { role: "agent", content: responseRef.current }]);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to submit answer");
    } finally {
      setSubmittingAnswer(false);
      setLoading(false);
      setIsThinking(false);
      setCurrentTool(null);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void send(e as unknown as FormEvent);
    }
  };

  const handleDeleteRun = async (runId: string) => {
    try {
      await deleteRun(runId);
      setRuns((prev) => prev.filter((r) => r.id !== runId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to delete run");
    }
  };

  const handleClearAllHistory = async () => {
    if (!window.confirm("Are you sure you want to clear all chat history and past runs for this project?")) return;
    try {
      await apiFetch(`/projects/${projectId}/chat`, { method: "DELETE" });
      setChat([]);
      setSteps([]);
      setRuns([]);
      setFinalReport(null);
      setThinkingText(null);
      setPending(null);
      setShowHistoryModal(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to clear chat history");
    }
  };

  const startNewChat = async () => {
    if (loading || deciding) return;
    try {
      await apiFetch(`/projects/${projectId}/chat`, { method: "DELETE" });
      setChat([]);
      setSteps([]);
      setUsage("");
      setTokenInputs(0);
      setPending(null);
      setError("");
      setCurrentTool(null);
      setIsThinking(false);
      setIsStuck(false);
      setActiveRunId(null);
      setAgentQuestion(null);
      setQuestionAnswer("");
      setFinalReport(null);
      setThinkingText(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to reset chat");
    }
  };

  const handleStopRun = async () => {
    if (!activeRunId) return;
    try {
      await apiFetch(`/runs/${activeRunId}/cancel`, { method: "POST" });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to stop run");
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
        <label className="block text-sm font-medium">Authentication<select value={authMethod} onChange={(event) => setAuthMethod(event.target.value as "json2" | "xmlrpc")} className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm"><option value="json2">Odoo 19 JSON-2 API key (recommended)</option><option value="xmlrpc">XML-RPC username and password</option></select></label>
        {authMethod === "json2" ? <label className="block text-sm font-medium">Scoped API key<input type="password" required value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" placeholder="Enter API key" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm" /></label> : <><label className="block text-sm font-medium">Username<input required value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="admin@example.com" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm" /></label><label className="block text-sm font-medium">Password<input type="password" required value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="Password" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm" /></label></>}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button disabled={loading || detectingDatabases} className="w-full rounded-xl bg-blue-600 py-3 font-medium text-white disabled:opacity-50 transition active:scale-95">{loading ? "Verifying…" : detectingDatabases ? "Discovering databases…" : "Verify and connect"}</button>
      </form>
    </div>
  );

  return (
    <div className={`flex h-screen overflow-hidden ${isResizingRight ? "select-none" : ""}`}>
      {/* Collapsible Left Sub-Sidebar */}
      {showLeftSidebar && (
        <aside className="w-64 shrink-0 border-r p-5 dark:border-white/10">
          <h1 className="text-xl font-bold truncate">{project.name}</h1>
          <div className="mt-4 rounded-xl border p-3.5 dark:border-white/10">
            <Database className="mb-1.5 h-4 w-4 text-blue-600" />
            <p className="truncate text-xs font-mono">{instances[0].url}</p>
            <p className="mt-1 text-[10px] uppercase font-semibold text-gray-500">{instances[0].environment} · {instances[0].status}</p>
          </div>
          <div className="mt-4 rounded-xl border p-3.5 dark:border-white/10">
            <label className="block text-[10px] font-semibold uppercase text-gray-500 mb-1">A2A Planner Model</label>
            <select value={plannerModel} onChange={(e) => setPlannerModel(e.target.value)} className="w-full rounded-lg border px-2 py-1.5 text-xs dark:bg-black dark:border-white/10 focus:ring-1 focus:ring-blue-500 transition">
              <option value="gpt-4o">gpt-4o</option>
              <option value="gpt-4o-mini">gpt-4o-mini</option>
              <option value="o1-mini">o1-mini</option>
              <option value="o1-preview">o1-preview</option>
            </select>
            <label className="block text-[10px] font-semibold uppercase text-gray-500 mt-3 mb-1">Fallback Model</label>
            <select value={fallbackModel} onChange={(e) => setFallbackModel(e.target.value)} className="w-full rounded-lg border px-2 py-1.5 text-xs dark:bg-black dark:border-white/10 focus:ring-1 focus:ring-blue-500 transition">
              <option value="gpt-4o-mini">gpt-4o-mini</option>
              <option value="gpt-4o">gpt-4o</option>
            </select>
          </div>
          <Link href={`/projects/${projectId}/workspace`} className="mt-4 block rounded-xl border p-2.5 text-xs hover:bg-black/5 dark:border-white/10">
            Workspace & lifecycle
          </Link>
          <Link href={`/projects/${projectId}/instances`} className="mt-2 block rounded-xl border p-2.5 text-xs hover:bg-black/5 dark:border-white/10">
            Odoo connections
          </Link>
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
            >
              {showLeftSidebar ? <PanelLeftClose className="h-3.5 w-3.5" /> : <PanelLeftOpen className="h-3.5 w-3.5" />}
            </button>
            <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-blue-600/20">
              <Bot className="h-3.5 w-3.5 text-blue-400" />
            </div>
            <span className="text-xs font-semibold text-white">ERP Implementation Agent</span>
            <button
              onClick={() => void startNewChat()}
              disabled={loading || deciding}
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
              History ({runs.length})
            </button>
          </div>
          <div className="flex items-center gap-4">
            {/* Context window bar — always visible after first token usage */}
            {tokenInputs > 0 && (() => {
              const pct = Math.min((tokenInputs / 128_000) * 100, 100);
              const barColor = pct > 90 ? "bg-red-500" : pct > 70 ? "bg-amber-400" : "bg-blue-500";
              return (
                <div className="flex items-center gap-2">
                  <div className="h-1 w-28 overflow-hidden rounded-full bg-white/10">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="font-mono text-[10px] text-gray-500">
                    {(tokenInputs / 1000).toFixed(0)}k
                    <span className="text-gray-600"> / 128k ctx</span>
                  </span>
                </div>
              );
            })()}
            {activeRunId && (
              <button
                onClick={() => void apiFetch(`/runs/${activeRunId}/cancel`, { method: "POST" })}
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
          {(loading || deciding) && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.18 }}
              className="border-b border-blue-100 bg-blue-50/80 px-5 py-2 backdrop-blur-sm dark:border-blue-900/30 dark:bg-blue-950/30"
            >
              <AgentStatus currentAction={currentTool} isThinking={isThinking} tokenInputs={tokenInputs} isStuck={isStuck} />
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          <AnimatePresence initial={false}>
            {chat
              .filter((m) => m.role !== "agent" || visibleContent(m.content).trim())
              .map((item, index, arr) => {
              const isCurrentStreaming = loading && index === arr.length - 1 && item.role === "agent";
              const prevRole = index > 0 ? arr[index - 1].role : null;
              const isFirstInGroup = item.role !== prevRole;
              return (
                <motion.div
                  key={`${item.id || "new"}-${index}`}
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
                          content={visibleContent(item.content)}
                          projectId={projectId}
                          isStreaming={isCurrentStreaming}
                        />
                      </div>
                    </div>
                  ) : (
                    <div className="max-w-[75%] rounded-2xl bg-blue-600 px-4 py-3 text-sm text-white shadow-xs">
                      <MessageContent
                        content={visibleContent(item.content)}
                        projectId={projectId}
                        isStreaming={false}
                      />
                    </div>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>

          {/* Pending Approval Card */}
          <AnimatePresence>
            {pending && (
              <motion.div
                initial={{ opacity: 0, scale: 0.96, y: 8 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96, y: 4 }}
                transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                className="max-w-2xl rounded-2xl border border-amber-400/80 bg-amber-50/90 p-5 text-sm shadow-sm backdrop-blur-md dark:border-amber-500/30 dark:bg-amber-950/30"
              >
                <div className="flex items-center gap-2 font-semibold text-amber-800 dark:text-amber-300">
                  <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400" />
                  <span>Class {pending.risk_class} action — approval required</span>
                </div>
                <p className="mt-2 font-mono text-xs text-amber-900/80 dark:text-amber-200/80">
                  {pending.tool}
                </p>
                <pre className="mt-3 max-h-72 overflow-auto rounded-xl bg-black/5 p-3 font-mono text-xs text-gray-800 dark:bg-black/40 dark:text-gray-200">
                  {JSON.stringify(pending.preview, null, 2)}
                </pre>
                <div className="mt-4 flex gap-2.5">
                  <button
                    disabled={deciding}
                    onClick={() => void decide("approve")}
                    className="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow-xs transition-transform active:scale-95 disabled:opacity-50"
                  >
                    {deciding ? "Approving…" : "Approve"}
                  </button>
                  <button
                    disabled={deciding}
                    onClick={() => void decide("reject")}
                    className="rounded-xl bg-gray-200 px-4 py-2 text-xs font-semibold text-gray-800 transition-transform hover:bg-gray-300 active:scale-95 disabled:opacity-50 dark:bg-white/10 dark:text-gray-200 dark:hover:bg-white/20"
                  >
                    Reject
                  </button>
                </div>
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
                    {agentQuestion.options.map((opt, i) => (
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
                  className="mt-3 rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white transition-transform active:scale-95 disabled:opacity-50"
                >
                  {submittingAnswer ? "Sending…" : "Submit Answer"}
                </button>
              </motion.div>
            )}
          </AnimatePresence>

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

          {/* Final Report Card (Protocol 4 — pinned, non-collapsible) */}
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
                {finalReport.done.map((item, i) => (
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

          {/* Activity Stepper */}
          <ActivityStepper
            steps={steps}
            usage={usage}
            isStuck={isStuck}
            supervisorTaskGraph={supervisorTaskGraph}
            activeTaskId={activeTaskId}
            recoveringTaskId={recoveringTaskId}
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

          {/* Error Banner */}
          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, scale: 0.98, y: 4 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.98, y: -4 }}
                transition={{ duration: 0.18 }}
                className="max-w-2xl rounded-2xl border border-red-200 bg-red-50/90 p-4 text-sm shadow-sm dark:border-red-900/50 dark:bg-red-950/30"
              >
                <div className="flex items-center gap-2 font-semibold text-red-700 dark:text-red-400">
                  <AlertTriangle className="h-4 w-4" />
                  <span>Agent Error</span>
                </div>
                <p className="mt-1.5 font-medium text-red-600 dark:text-red-300">{error}</p>
              </motion.div>
            )}
          </AnimatePresence>

          <div ref={bottom} />
        </div>
        <form onSubmit={send} className="border-t border-white/5 bg-zinc-900 p-3">
          <div className="relative mx-auto max-w-4xl">
            <textarea
              rows={1}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading || Boolean(pending)}
              placeholder={pending ? "Resolve the pending action first" : loading ? "Agent is working…" : "Ask the agent… (⌘↵ to send)"}
              className="w-full resize-none overflow-hidden rounded-2xl border border-white/10 bg-zinc-800 px-5 py-3 pr-12 text-sm text-white placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500/60"
              style={{ fieldSizing: "content", maxHeight: "9rem" } as React.CSSProperties}
            />
            {loading && activeRunId ? (
              <button
                type="button"
                onClick={() => void handleStopRun()}
                className="absolute bottom-2 right-2 rounded-xl bg-red-600/20 p-2 text-red-400 transition-transform hover:bg-red-600/40 hover:text-red-300 active:scale-95"
                title="Stop generation"
              >
                <Square className="h-4 w-4 fill-current" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={loading || !message.trim() || Boolean(pending)}
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
            onMouseDown={() => setIsResizingRight(true)}
            className={`group relative z-20 w-1.5 cursor-col-resize hover:bg-blue-500/60 active:bg-blue-600 transition-colors ${
              isResizingRight ? "bg-blue-600" : "bg-white/5"
            }`}
            title="Drag to resize IDE panel width"
          >
            <div className="absolute inset-y-0 -left-1 -right-1" />
          </div>

          <aside
            style={{ width: `${rightPanelWidth}px` }}
            className="flex shrink-0 flex-col border-l border-white/10 bg-zinc-900 transition-none"
          >
          {/* Tabs Navigation Header */}
          <div className="flex items-center justify-between border-b border-white/10 bg-zinc-950 px-2 py-1.5">
            <div className="flex items-center gap-1 text-xs">
              <button
                onClick={() => setRightPanelTab("code")}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "code" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <Code2 className="h-3.5 w-3.5 text-purple-400" />
                Code & Files
              </button>
              <button
                onClick={() => setRightPanelTab("diff")}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "diff" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <GitBranch className="h-3.5 w-3.5 text-blue-400" />
                Git Diff
              </button>
              <button
                onClick={() => setRightPanelTab("memory")}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "memory" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <Brain className="h-3.5 w-3.5 text-amber-400" />
                Memories
              </button>
              <button
                onClick={() => setRightPanelTab("evidence")}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 font-medium transition ${
                  rightPanelTab === "evidence" ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"
                }`}
              >
                <Layers className="h-3.5 w-3.5 text-emerald-400" />
                Artifacts
              </button>
            </div>
          </div>

          {/* Tab Contents */}
          <div className="flex-1 overflow-hidden">
            {rightPanelTab === "code" && (
              <div className="grid h-full grid-cols-[180px_1fr]">
                {/* File Tree */}
                <div className="border-r border-white/10 bg-zinc-950 p-2 overflow-y-auto">
                  <div className="mb-2 text-[10px] font-semibold uppercase text-gray-500">Workspace Files</div>
                  <ul className="space-y-0.5 text-xs font-mono">
                    {entries.map((entry) => (
                      <li key={entry.path}>
                        <button
                          disabled={entry.type !== "file"}
                          onClick={() => void openFile(entry.path)}
                          className={`flex w-full items-center gap-1.5 truncate rounded px-2 py-1 text-left transition ${
                            selectedFile === entry.path
                              ? "bg-purple-600/30 text-purple-300 font-semibold"
                              : "text-gray-400 hover:bg-white/5 hover:text-gray-200"
                          } disabled:text-gray-600`}
                        >
                          {entry.type === "directory" ? (
                            <Folder className="h-3 w-3 shrink-0 text-amber-500/80" />
                          ) : (
                            <FileText className="h-3 w-3 shrink-0 text-blue-400/80" />
                          )}
                          <span className="truncate">{entry.path}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
                {/* Code Viewer */}
                <div className="flex-1 overflow-hidden">
                  <CodeDiffViewer path={selectedFile} content={fileContent} />
                </div>
              </div>
            )}

            {rightPanelTab === "diff" && (
              <div className="h-full">
                <CodeDiffViewer path="Workspace Uncommitted / Commit Diff" content={diffContent || "No active diff changes in workspace."} isDiff={true} />
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
              </div>
            )}
          </div>
        </aside>
        </>
      )}

      {/* Chat History & Past Runs Modal Drawer */}
      <AnimatePresence>
        {showHistoryModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md p-4">
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 10 }}
              className="w-full max-w-2xl rounded-2xl border border-white/10 bg-zinc-950 p-6 shadow-2xl space-y-4 max-h-[85vh] flex flex-col"
            >
              <div className="flex items-center justify-between border-b border-white/10 pb-4">
                <div className="flex items-center gap-2.5">
                  <History className="h-5 w-5 text-purple-400" />
                  <h2 className="text-lg font-bold text-white">Chat History & Past Runs</h2>
                </div>
                <div className="flex items-center gap-2">
                  {runs.length > 0 && (
                    <button
                      onClick={() => void handleClearAllHistory()}
                      className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-950/30 px-3 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-900/50 transition active:scale-95"
                    >
                      <Trash2 className="h-3.5 w-3.5" /> Clear All History
                    </button>
                  )}
                  <button
                    onClick={() => setShowHistoryModal(false)}
                    className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-gray-400 hover:bg-white/10 hover:text-white transition"
                  >
                    Close
                  </button>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto space-y-3 pr-1">
                {runs.length === 0 ? (
                  <div className="p-8 text-center text-xs text-gray-500">No past runs recorded for this project yet.</div>
                ) : (
                  runs.map((runItem) => (
                    <div
                      key={runItem.id}
                      className="flex items-start justify-between gap-4 rounded-xl border border-white/5 bg-zinc-900/80 p-4 transition hover:border-white/20"
                    >
                      <div className="space-y-1.5 min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                            runItem.status === "succeeded" ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" :
                            runItem.status === "failed" ? "bg-red-500/20 text-red-300 border border-red-500/30" :
                            "bg-gray-500/20 text-gray-400 border border-gray-500/30"
                          }`}>
                            {runItem.status}
                          </span>
                          <span className="text-[11px] font-mono text-gray-500">
                            {new Date(runItem.created_at).toLocaleString()}
                          </span>
                          {runItem.cost_usd > 0 && (
                            <span className="text-[10px] font-mono text-purple-300 bg-purple-500/10 px-1.5 py-0.5 rounded">
                              ${Number(runItem.cost_usd).toFixed(4)}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-200 font-medium line-clamp-2 leading-relaxed">
                          {runItem.prompt || "Agent task execution"}
                        </p>
                        {runItem.error_message && (
                          <p className="text-[11px] text-red-400 font-mono line-clamp-1">{runItem.error_message}</p>
                        )}
                      </div>

                      <button
                        onClick={() => void handleDeleteRun(runItem.id)}
                        className="rounded-lg p-2 text-gray-500 hover:bg-red-500/20 hover:text-red-400 transition"
                        title="Delete this run"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
