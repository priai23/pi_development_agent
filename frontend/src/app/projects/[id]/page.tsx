"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Bot, Brain, Code2, Database, FileText, Folder, GitBranch, Layers, Loader2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Send } from "lucide-react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import MessageContent from "@/components/MessageContent";
import AgentStatus from "@/components/AgentStatus";
import ActivityStepper from "@/components/ActivityStepper";
import LearnedMemories from "@/components/LearnedMemories";
import CodeDiffViewer from "@/components/CodeDiffViewer";
import { apiFetch, AgentRun, Artifact, ChatMessage, Deployment, followRun, Instance, PendingAction, Project, Step, ToolEvent, WorkspaceEntry, visibleContent } from "@/lib/api";

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
  const bottom = useRef<HTMLDivElement>(null);
  const discoveryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const discoverySequence = useRef(0);
  const lastEventAt = useRef<number>(0);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Mouse drag handler for dynamic right panel width resizing
  useEffect(() => {
    if (!isResizingRight) return;
    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth >= 320 && newWidth <= window.innerWidth * 0.75) {
        setRightPanelWidth(newWidth);
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
      const runs = await apiFetch<AgentRun[]>(`/projects/${projectId}/runs`).catch(() => [] as AgentRun[]);
      if (runs.length > 0) {
        const events = await apiFetch<ToolEvent[]>(`/runs/${runs[0].id}/events`).catch(() => [] as ToolEvent[]);
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
      setCurrentTool(tool);
      setIsThinking(false);
      setSteps(prev => [...prev, { tool, label: tool.replace(/_/g, " "), status: "running", startedAt: Date.now() }]);
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
        next[realIdx] = { ...next[realIdx], status: "done", result: raw, elapsed: (Date.now() - next[realIdx].startedAt) / 1000 };
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
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = message.trim(); if (!text || pending) return;
    setMessage(""); setError(""); setLoading(true); setSteps([]); setUsage(""); setTokenInputs(0);
    setCurrentTool(null); setIsThinking(true);
    // Don't optimistically add an empty agent bubble — backend now only saves non-empty responses
    setChat((current) => [...current, { role: "user", content: text }]);
    const responseRef = { current: "" };
    try {
      const run = await apiFetch<AgentRun>(`/projects/${projectId}/runs`, { method: "POST", body: JSON.stringify({ message: text }) });
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
    finally { setLoading(false); setActiveRunId(null); setIsThinking(false); setCurrentTool(null); }
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
    finally { setDeciding(false); setLoading(false); setIsThinking(false); setCurrentTool(null); }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void send(e as unknown as FormEvent);
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
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to reset chat");
    }
  };

  if (loading && !project) return <div className="p-8 text-gray-500">Loading…</div>;
  if (!project) return <div className="p-8 text-red-600">{error || "Project not found"}</div>;
  if (!instances.length) return (
    <div className="mx-auto max-w-xl p-8">
      <h1 className="text-3xl font-bold">{project.name}</h1><p className="mt-2 text-gray-500">Connect an approved Odoo host. Credentials are encrypted and never returned.</p>
      <form onSubmit={connect} className="mt-8 space-y-4 rounded-2xl border p-6 dark:border-white/10">
        <label className="block text-sm">Server URL <span className="text-xs text-gray-500">(database discovery runs automatically)</span><div className="relative"><input type="url" required value={url} onChange={(event) => changeUrl(event.target.value)} onBlur={finishUrlEntry} placeholder="https://odoo.internal.example" className="mt-1 w-full rounded-xl border px-4 py-3 pr-10 dark:border-white/10 dark:bg-black" />{detectingDatabases && <Loader2 className="absolute right-3 top-4 h-4 w-4 animate-spin text-blue-600" />}</div></label>
        <label className="block text-sm">Database
          {detectedDatabases.length > 1 ? (
            <select required value={dbName} onChange={(event) => setDbName(event.target.value)} className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black">
              <option value="">Select a database…</option>
              {detectedDatabases.map((database) => <option key={database} value={database}>{database}</option>)}
            </select>
          ) : (
            <input required value={dbName} onChange={(event) => setDbName(event.target.value)} readOnly={detectedDatabases.length === 1} placeholder={detectingDatabases ? "Discovering databases…" : "Database name"} className="mt-1 w-full rounded-xl border px-4 py-3 read-only:bg-gray-50 dark:border-white/10 dark:bg-black dark:read-only:bg-white/5" />
          )}
          {discoveryMessage && <span className="mt-1 block text-xs text-gray-500">{discoveryMessage}</span>}
        </label>
        <label className="block text-sm">Authentication<select value={authMethod} onChange={(event) => setAuthMethod(event.target.value as "json2" | "xmlrpc")} className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black"><option value="json2">Odoo 19 JSON-2 API key (recommended)</option><option value="xmlrpc">XML-RPC username and password</option></select></label>
        {authMethod === "json2" ? <label className="block text-sm">Scoped API key<input type="password" required value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" /></label> : <><label className="block text-sm">Username<input required value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" /></label><label className="block text-sm">Password<input type="password" required value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" className="mt-1 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" /></label></>}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button disabled={loading || detectingDatabases} className="w-full rounded-xl bg-blue-600 py-3 font-medium text-white disabled:opacity-50">{loading ? "Verifying…" : detectingDatabases ? "Discovering databases…" : "Verify and connect"}</button>
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
              onClick={() => setShowLeftSidebar(!showLeftSidebar)}
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
                  <span>Class {pending.risk_class} action awaiting approval</span>
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

          {/* Activity Stepper */}
          <ActivityStepper
            steps={steps}
            usage={usage}
            isStuck={isStuck}
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
              placeholder={pending ? "Resolve the pending action first" : "Ask the agent… (⌘↵ to send)"}
              className="w-full resize-none overflow-hidden rounded-2xl border border-white/10 bg-zinc-800 px-5 py-3 pr-12 text-sm text-white placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500/60"
              style={{ fieldSizing: "content", maxHeight: "9rem" } as React.CSSProperties}
            />
            <button
              type="submit"
              disabled={loading || !message.trim() || Boolean(pending)}
              className="absolute bottom-2 right-2 rounded-xl bg-blue-600 p-2 text-white transition-transform hover:bg-blue-500 active:scale-95 disabled:opacity-30"
            >
              <Send className="h-4 w-4" />
            </button>
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
    </div>
  );
}
