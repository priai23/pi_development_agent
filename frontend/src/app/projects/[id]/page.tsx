"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import { FolderGit2, Users, Settings, Database, Play, CheckCircle2, Circle, Loader2, Send, Bot, AlertTriangle, User, AlertCircle } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { API_URL } from "@/lib/api";

export default function ProjectWorkspace() {
  const { id } = useParams();
  const [project, setProject] = useState<any>(null);
  const [instances, setInstances] = useState<any[]>([]);
  const [chat, setChat] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [fetchingWorkspace, setFetchingWorkspace] = useState(true);
  
  // Connection Form State
  const [erpType, setErpType] = useState<"odoo" | "pi_erp">("odoo");
  const [url, setUrl] = useState("");
  const [dbName, setDbName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [detectedDbs, setDetectedDbs] = useState<string[]>([]);
  const [detecting, setDetecting] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [chat]);

  const fetchWorkspaceData = async () => {
    try {
      const [projRes, instRes, chatRes] = await Promise.all([
        fetch(`${API_URL}/projects/${id}`),
        fetch(`${API_URL}/projects/${id}/instances/`),
        fetch(`${API_URL}/projects/${id}/chat`)
      ]);
      
      const projData = await projRes.json();
      const instData = await instRes.json();
      const chatData = await chatRes.json();
      
      setProject(projData);
      setInstances(instData);
      setChat(chatData);
    } catch (err) {
      console.error("Failed to load workspace data:", err);
    } finally {
      setFetchingWorkspace(false);
    }
  };

  useEffect(() => {
    fetchWorkspaceData();
  }, [id]);

  const handleUrlBlur = async () => {
    if (!url.trim()) return;
    setDetecting(true);
    try {
      const res = await fetch(`${API_URL}/instances/detect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, erp_type: erpType })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.databases && data.databases.length > 0) {
          setDetectedDbs(data.databases);
          if (!dbName) setDbName(data.databases[0]);
        }
        if (data.suggested_username && !username) {
          setUsername(data.suggested_username);
        }
        if (!password) {
          setPassword("567e656396a9826770e1f1857f4cf13e892d5042");
        }
      }
    } catch (err) {
      console.error("Detect failed:", err);
    } finally {
      setDetecting(false);
    }
  };

  const handleConnectInstance = async (e: React.FormEvent) => {
    e.preventDefault();
    setConnecting(true);
    setConnectError(null);

    const payload = { erp_type: erpType, url, db_name: dbName, username, password, project_id: Number(id) };

    try {
      const response = await fetch(`${API_URL}/instances/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error("Failed to save connection profile");
      }

      const data = await response.json();
      
      // Test connection
      const testRes = await fetch(`${API_URL}/instances/${data.id}/test-connection`, {
        method: "POST",
      });
      
      if (!testRes.ok) {
        const testData = await testRes.json();
        throw new Error(testData.detail || "Connection failed to the ERP");
      }
      
      await fetchWorkspaceData();
    } catch (err: any) {
      setConnectError(err.message);
    } finally {
      setConnecting(false);
    }
  };

  const sendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!message.trim()) return;

    const userMsg = message;
    setMessage("");
    setChat(prev => [...prev, { role: "user", content: userMsg }]);
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/projects/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg })
      });
      
      if (!res.ok) {
        let errData;
        try {
          errData = await res.json();
        } catch {
          errData = { detail: res.statusText };
        }
        throw new Error(errData.detail || "Server Error");
      }
      
      setLoading(false);
      setChat(prev => [...prev, { role: "agent", content: "" }]);

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let agentResponse = "";

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          agentResponse += chunk;
          
          setChat(prev => {
            const newChat = [...prev];
            newChat[newChat.length - 1] = { role: "agent", content: agentResponse };
            return newChat;
          });
        }
      }
    } catch (err: any) {
      console.error(err);
      setLoading(false);
      setChat(prev => [...prev, { role: "agent", content: `❌ Error: ${err.message}` }]);
    }
  };

  const handleResume = async (action: string) => {
    setLoading(true);
    
    // Append a new agent message block for the resumed response
    setChat(prev => [...prev, { role: "agent", content: "" }]);

    try {
      const res = await fetch(`${API_URL}/projects/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: "", resume_action: action })
      });
      
      if (!res.ok) {
        throw new Error("Failed to resume action");
      }
      
      setLoading(false);

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let agentResponse = "";

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          agentResponse += chunk;
          
          setChat(prev => {
            const newChat = [...prev];
            newChat[newChat.length - 1] = { role: "agent", content: agentResponse };
            return newChat;
          });
        }
      }
    } catch (err: any) {
      console.error(err);
      setLoading(false);
      setChat(prev => [...prev, { role: "agent", content: `❌ Error: ${err.message}` }]);
    }
  };

  if (fetchingWorkspace) {
    return (
      <div className="flex items-center justify-center h-screen w-full">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (!project) return <div className="p-8">Project not found.</div>;

  const hasInstance = instances.length > 0;

  return (
    <div className="flex h-screen overflow-hidden w-full bg-[var(--background)]">
      {/* Left Panel: Project Overview */}
      <div className="w-1/3 border-r border-black/5 dark:border-white/10 p-6 flex flex-col glass-panel relative z-10">
        <h2 className="text-2xl font-bold mb-2 tracking-tight text-black dark:text-white">{project.name}</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-8">Implementation Workspace</p>

        <div className="space-y-4 flex-1">
          <div className="p-4 rounded-2xl border border-black/5 dark:border-white/10 bg-white/80 dark:bg-black/40 shadow-sm">
            <h3 className="font-semibold mb-3 text-black dark:text-white/90 tracking-tight">Implementation Progress</h3>
            <ul className="space-y-3 text-sm">
              <li className="flex justify-between items-center text-gray-700 dark:text-gray-300">
                <span>Connect ERP Instance</span> 
                {hasInstance ? <span className="text-green-500 font-bold">✓</span> : <span className="text-blue-500">●</span>}
              </li>
              <li className={`flex justify-between items-center ${!hasInstance ? 'text-gray-400 dark:text-gray-500' : 'text-gray-700 dark:text-gray-300'}`}>
                <span>Discovery Phase</span> 
                {hasInstance ? <span className="text-blue-500">●</span> : <span>○</span>}
              </li>
              <li className="flex justify-between text-gray-400 dark:text-gray-500"><span>Company Setup</span> <span>○</span></li>
              <li className="flex justify-between text-gray-400 dark:text-gray-500"><span>Sales & Inventory</span> <span>○</span></li>
            </ul>
          </div>

          {hasInstance && (
            <div className="p-4 rounded-2xl border border-blue-500/20 bg-blue-50 dark:bg-blue-500/10 shadow-sm">
              <div className="flex items-center space-x-2 text-blue-600 dark:text-blue-400 mb-2">
                <Database className="w-4 h-4" />
                <h3 className="font-semibold text-sm">Connected Instance</h3>
              </div>
              <p className="text-xs text-gray-600 dark:text-gray-400 truncate">{instances[0].url}</p>
              <p className="text-xs text-gray-500 dark:text-gray-500 mt-1 uppercase tracking-wider">{instances[0].erp_type}</p>
            </div>
          )}
        </div>
      </div>

      {/* Right Panel: Agent Chat or Connection Form */}
      <div className="w-2/3 flex flex-col relative z-0">
        {!hasInstance ? (
          <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", bounce: 0, duration: 0.5 }}
            className="flex-1 overflow-y-auto p-8 flex flex-col items-center justify-center"
          >
            <div className="w-full max-w-md p-8 rounded-2xl border border-black/5 dark:border-white/10 bg-white/80 dark:bg-white/5 backdrop-blur-xl shadow-lg">
              <div className="mb-6 text-center">
                <div className="w-12 h-12 bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Database className="w-6 h-6" />
                </div>
                <h2 className="text-2xl font-bold mb-2 tracking-tight text-black dark:text-white">Connect ERP Instance</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">Provide the credentials for the target ERP instance to allow the agent to inspect and configure it.</p>
              </div>

              <form onSubmit={handleConnectInstance} className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <button type="button" onClick={() => setErpType("odoo")} className={`p-2.5 rounded-xl border text-sm font-medium transition-colors ${erpType === "odoo" ? "border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-500/20 dark:text-blue-400" : "border-black/10 dark:border-white/10 bg-gray-50 dark:bg-white/5 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/10"}`}>
                    Odoo
                  </button>
                  <button type="button" onClick={() => setErpType("pi_erp")} className={`p-2.5 rounded-xl border text-sm font-medium transition-colors ${erpType === "pi_erp" ? "border-purple-500 bg-purple-50 text-purple-700 dark:bg-purple-500/20 dark:text-purple-400" : "border-black/10 dark:border-white/10 bg-gray-50 dark:bg-white/5 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/10"}`}>
                    Pi ERP
                  </button>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                    Server URL {detecting && <Loader2 className="inline w-3 h-3 animate-spin ml-2 text-blue-500" />}
                  </label>
                  <input type="url" required value={url} onChange={(e) => setUrl(e.target.value)} onBlur={handleUrlBlur} placeholder="https://your-erp.com" className="w-full px-4 py-2.5 text-sm bg-gray-50 dark:bg-black/40 border border-black/10 dark:border-white/10 rounded-xl focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-black dark:text-white transition-all shadow-inner" />
                </div>
                
                {erpType === "odoo" && (
                  <div>
                    <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Database Name</label>
                    {detectedDbs.length > 0 ? (
                      <select required value={dbName} onChange={(e) => setDbName(e.target.value)} className="w-full px-4 py-2.5 text-sm bg-gray-50 dark:bg-black/40 border border-black/10 dark:border-white/10 rounded-xl focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-black dark:text-white transition-all shadow-inner">
                        {detectedDbs.map(db => <option key={db} value={db}>{db}</option>)}
                      </select>
                    ) : (
                      <input type="text" required value={dbName} onChange={(e) => setDbName(e.target.value)} placeholder="odoo_db" className="w-full px-4 py-2.5 text-sm bg-gray-50 dark:bg-black/40 border border-black/10 dark:border-white/10 rounded-xl focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-black dark:text-white transition-all shadow-inner" />
                    )}
                  </div>
                )}

                <div>
                  <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Username</label>
                  <input type="text" required value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" className="w-full px-4 py-2.5 text-sm bg-gray-50 dark:bg-black/40 border border-black/10 dark:border-white/10 rounded-xl focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-black dark:text-white transition-all shadow-inner" />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Password</label>
                  <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" className="w-full px-4 py-2.5 text-sm bg-gray-50 dark:bg-black/40 border border-black/10 dark:border-white/10 rounded-xl focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-black dark:text-white transition-all shadow-inner" />
                </div>

                {connectError && (
                  <div className="p-3 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 text-red-600 dark:text-red-400 text-sm flex items-start space-x-2">
                    <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                    <span>{connectError}</span>
                  </div>
                )}

                <button type="submit" disabled={connecting} className="w-full py-3 bg-blue-500 hover:bg-blue-600 text-white text-sm font-semibold rounded-xl transition-colors flex justify-center items-center mt-4 disabled:opacity-50 shadow-sm">
                  {connecting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                  {connecting ? "Testing Connection..." : "Connect Instance"}
                </button>
              </form>
            </div>
          </motion.div>
        ) : (
          <>
            <div className="p-4 border-b border-black/5 dark:border-white/10 glass-panel sticky top-0 z-20 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 rounded-full bg-blue-500 flex items-center justify-center shadow-sm">
                  <Bot className="w-5 h-5 text-white" />
                </div>
                <div>
                  <h3 className="font-semibold text-black dark:text-white tracking-tight">ERP Implementation Agent</h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Online • {instances[0].erp_type.toUpperCase()} Connected</p>
                </div>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              {chat.length === 0 && (
                <div className="text-center text-gray-400 dark:text-gray-500 mt-20">
                  <Bot className="w-12 h-12 mx-auto mb-4 opacity-50" />
                  <p>No messages yet. Try asking: "What modules are installed?"</p>
                </div>
              )}
              <AnimatePresence initial={false}>
              {chat.map((msg, i) => {
                const parts = msg.content.split("_ACTION_PENDING_||");
                const actualContent = parts[0];
                let pendingAction = null;
                if (parts.length > 1) {
                  try {
                    pendingAction = JSON.parse(parts[1].trim());
                  } catch (e) {}
                }

                // If this is not the last message, and it had a pending action, hide the buttons (it was already resolved)
                const isResolved = i !== chat.length - 1 && pendingAction !== null;

                return (
                  <motion.div 
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ type: "spring", bounce: 0, duration: 0.4 }}
                    key={i} 
                    className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    <div className={`flex items-end max-w-[75%] space-x-2 ${msg.role === 'user' ? 'flex-row-reverse space-x-reverse' : ''}`}>
                      {msg.role !== 'user' && (
                        <div className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 bg-blue-500 shadow-sm mb-1">
                          <Bot className="w-4 h-4 text-white" />
                        </div>
                      )}
                      <div className={`p-4 text-[15px] leading-relaxed whitespace-pre-wrap shadow-sm ${
                        msg.role === 'user' 
                          ? 'bg-blue-500 text-white rounded-2xl rounded-tr-sm' 
                          : 'bg-white dark:bg-[#2c2c2e] text-black dark:text-white rounded-2xl rounded-tl-sm border border-black/5 dark:border-white/5'
                      }`}>
                        {actualContent}
                        
                        {pendingAction && (
                          <div className={`mt-3 p-4 rounded-xl border ${isResolved ? 'border-gray-500/30 bg-gray-500/10' : 'border-yellow-500/30 bg-yellow-500/10'} shadow-sm`}>
                            <div className={`flex items-center gap-2 mb-2 ${isResolved ? 'text-gray-500' : 'text-yellow-600 dark:text-yellow-500'}`}>
                              <AlertTriangle className="w-5 h-5" />
                              <span className="font-semibold text-sm">
                                {isResolved ? "Action Resolved" : "Action Pending Approval"}
                              </span>
                            </div>
                            <div className="text-sm font-medium mb-1">{pendingAction.tool}</div>
                            <pre className="text-xs text-gray-600 dark:text-gray-400 mb-4 overflow-x-auto p-2 bg-black/5 dark:bg-black/20 rounded">
                              {JSON.stringify(pendingAction.args, null, 2)}
                            </pre>
                            
                            {!isResolved && (
                              <div className="flex gap-2">
                                <button 
                                  onClick={() => handleResume("approve")}
                                  disabled={loading}
                                  className="flex-1 bg-green-500 hover:bg-green-600 text-white py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                                >
                                  Approve
                                </button>
                                <button 
                                  onClick={() => handleResume("reject")}
                                  disabled={loading}
                                  className="flex-1 bg-gray-200 hover:bg-gray-300 dark:bg-white/10 dark:hover:bg-white/20 text-black dark:text-white py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                                >
                                  Reject
                                </button>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                );
              })}
              {loading && (
                <motion.div 
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-start"
                >
                  <div className="flex items-end space-x-2 max-w-[75%]">
                    <div className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 bg-blue-500 shadow-sm mb-1">
                      <Loader2 className="w-4 h-4 text-white animate-spin" />
                    </div>
                    <div className="p-4 rounded-2xl rounded-tl-sm bg-white dark:bg-[#2c2c2e] border border-black/5 dark:border-white/5 shadow-sm">
                      <div className="flex space-x-1.5">
                        <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" />
                        <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "0.15s" }} />
                        <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "0.3s" }} />
                      </div>
                    </div>
                  </div>
                </motion.div>
              )}
              </AnimatePresence>
              <div ref={messagesEndRef} />
            </div>

            <div className="p-4 border-t border-black/5 dark:border-white/10 glass-panel z-20 sticky bottom-0">
              <form onSubmit={sendMessage} className="relative max-w-4xl mx-auto">
                <input
                  type="text"
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Ask the agent to inspect or configure..."
                  className="w-full bg-white dark:bg-[#1c1c1e] border border-black/10 dark:border-white/10 rounded-full py-3.5 pl-6 pr-14 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-black dark:text-white shadow-sm transition-all"
                />
                <button
                  type="submit"
                  disabled={loading || !message.trim()}
                  className="absolute right-1.5 top-1.5 bottom-1.5 aspect-square flex items-center justify-center rounded-full bg-blue-500 hover:bg-blue-600 disabled:opacity-50 disabled:hover:bg-blue-500 transition-colors shadow-sm"
                >
                  <Send className="w-4 h-4 text-white ml-0.5" />
                </button>
              </form>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
