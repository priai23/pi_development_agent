"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, ChevronLeft, ChevronRight, Database, Globe, Loader2, Play, Sparkles, X } from "lucide-react";
import { apiFetch, Instance, Organization, Project } from "@/lib/api";

interface ProjectCreationWizardProps {
  open: boolean;
  onClose: () => void;
  organizations: Organization[];
  onProjectCreated?: (project: Project) => void;
}

export type ERPType = "odoo" | "pri_erp";

export default function ProjectCreationWizard({
  open,
  onClose,
  organizations,
  onProjectCreated,
}: ProjectCreationWizardProps) {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2>(1);

  // Step 1: ERP Type & Project Info
  const [erpType, setErpType] = useState<ERPType>("odoo");
  const [projectName, setProjectName] = useState("");
  const [organizationId, setOrganizationId] = useState<number>(0);

  // Step 2: Server & Credentials
  const [url, setUrl] = useState("");
  const [dbName, setDbName] = useState("");
  const [detectedDatabases, setDetectedDatabases] = useState<string[]>([]);
  const [detectingDatabases, setDetectingDatabases] = useState(false);
  const [discoveryMessage, setDiscoveryMessage] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [verifySuccess, setVerifySuccess] = useState(false);
  const [verifyError, setVerifyError] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const discoveryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const effectiveOrgId = organizationId || organizations[0]?.id || 0;

  // Auto-discover databases when URL changes
  useEffect(() => {
    if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    const trimmed = url.trim();
    if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
      return;
    }

    discoveryTimer.current = setTimeout(async () => {
      setDetectingDatabases(true);
      setDiscoveryMessage("Discovering available databases...");
      setVerifySuccess(false);
      try {
        const res = await apiFetch<{
          status: string;
          databases: string[];
          suggested_username?: string;
          message?: string;
        }>("/instances/detect", {
          method: "POST",
          body: JSON.stringify({ url: trimmed, erp_type: erpType }),
        });

        if (res.databases && res.databases.length > 0) {
          setDetectedDatabases(res.databases);
          setDbName((current) => current || res.databases[0]);
          setDiscoveryMessage(`Found ${res.databases.length} database(s)`);
        } else {
          setDetectedDatabases([]);
          setDiscoveryMessage(res.message || "Enter database name manually");
        }
      } catch {
        setDetectedDatabases([]);
        setDiscoveryMessage("Could not auto-list databases; enter manually");
      } finally {
        setDetectingDatabases(false);
      }
    }, 600);

    return () => {
      if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    };
  }, [url, erpType]);

  // Test credentials & connection
  const handleTestConnection = async () => {
    setVerifying(true);
    setVerifyError("");
    setVerifySuccess(false);
    try {
      const res = await apiFetch<{ status: string; databases: string[] }>("/instances/detect", {
        method: "POST",
        body: JSON.stringify({ url: url.trim(), erp_type: erpType }),
      });
      if (res) {
        setVerifySuccess(true);
      }
    } catch (caught) {
      setVerifyError(caught instanceof Error ? caught.message : "Connection failed");
    } finally {
      setVerifying(false);
    }
  };

  const handleFinish = async (e: FormEvent) => {
    e.preventDefault();
    if (!projectName.trim()) {
      setError("Please enter a project name");
      setStep(1);
      return;
    }
    if (!effectiveOrgId) {
      setError("Please select or create an organization");
      return;
    }
    if (!url.trim() || !dbName.trim()) {
      setError("ERP Server URL and database name are required");
      return;
    }

    setSubmitting(true);
    setError("");

    try {
      // 1. Create Project
      const project = await apiFetch<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: projectName.trim(),
          organization_id: effectiveOrgId,
        }),
      });

      // 2. Connect ERP Instance
      await apiFetch<Instance>("/instances", {
        method: "POST",
        body: JSON.stringify({
          erp_type: erpType,
          url: url.trim(),
          db_name: dbName.trim(),
          username: username.trim(),
          password: password,
          auth_method: "xmlrpc",
          project_id: project.id,
        }),
      });

      onProjectCreated?.(project);
      onClose();
      router.push(`/projects/${project.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to create project");
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative flex flex-col w-full max-w-2xl overflow-hidden rounded-2xl border border-white/10 bg-[#0f172a] shadow-2xl text-slate-100 max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-4 bg-slate-900/60">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-blue-400" />
              New Implementation Project
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Step {step} of 2 &bull;{" "}
              {step === 1 && "Choose ERP Platform & Project Details"}
              {step === 2 && "Configure Server & Connect Instance"}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-white/10 hover:text-white transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Stepper Progress Bar */}
        <div className="flex w-full bg-slate-800/40 h-1">
          <div
            className="bg-blue-500 transition-all duration-300 h-full"
            style={{ width: `${(step / 2) * 100}%` }}
          />
        </div>

        {/* Content Body */}
        <div className="overflow-y-auto p-6 space-y-6">
          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400">
              {error}
            </div>
          )}

          {/* STEP 1: Select ERP & Project Details */}
          {step === 1 && (
            <div className="space-y-5">
              <div>
                <label className="text-sm font-semibold text-slate-200">
                  Which ERP system do you need to make changes to?
                </label>
                <p className="text-xs text-slate-400 mt-1">
                  Select your target ERP platform for this implementation workspace.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Odoo Option */}
                <button
                  type="button"
                  onClick={() => setErpType("odoo")}
                  className={`flex flex-col items-start p-5 rounded-xl border text-left transition-all ${
                    erpType === "odoo"
                      ? "border-blue-500 bg-blue-500/10 ring-2 ring-blue-500/30 shadow-lg shadow-blue-500/10"
                      : "border-white/10 bg-slate-800/40 hover:border-white/20 hover:bg-slate-800/70"
                  }`}
                >
                  <div className="flex items-center justify-between w-full mb-3">
                    <span className="flex items-center justify-center w-10 h-10 rounded-lg bg-amber-500/20 text-amber-400 font-bold text-lg border border-amber-500/30">
                      O
                    </span>
                    {erpType === "odoo" && (
                      <CheckCircle2 className="h-5 w-5 text-blue-400" />
                    )}
                  </div>
                  <h3 className="font-bold text-base text-white">Odoo</h3>
                  <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                    Odoo 17, 18 & 19 (Community, Enterprise, or Odoo.sh) via XML-RPC.
                  </p>
                </button>

                {/* PRI ERP Option */}
                <button
                  type="button"
                  onClick={() => setErpType("pri_erp")}
                  className={`flex flex-col items-start p-5 rounded-xl border text-left transition-all ${
                    erpType === "pri_erp"
                      ? "border-blue-500 bg-blue-500/10 ring-2 ring-blue-500/30 shadow-lg shadow-blue-500/10"
                      : "border-white/10 bg-slate-800/40 hover:border-white/20 hover:bg-slate-800/70"
                  }`}
                >
                  <div className="flex items-center justify-between w-full mb-3">
                    <span className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-500/20 text-blue-400 font-bold text-lg border border-blue-500/30">
                      P
                    </span>
                    {erpType === "pri_erp" && (
                      <CheckCircle2 className="h-5 w-5 text-blue-400" />
                    )}
                  </div>
                  <h3 className="font-bold text-base text-white">PRI ERP (Pi ERP)</h3>
                  <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                    Primacy Enterprise Resource Planning suite with bridge integration and custom schemas.
                  </p>
                </button>
              </div>

              {/* Project Name & Organization */}
              <div className="pt-2 grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-300">Project Name</label>
                  <input
                    type="text"
                    required
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                    placeholder="e.g. Leave Approval Workflow"
                    className="w-full rounded-lg border border-white/10 bg-slate-800/80 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-300">Organization</label>
                  <select
                    value={effectiveOrgId}
                    onChange={(e) => setOrganizationId(Number(e.target.value))}
                    className="w-full rounded-lg border border-white/10 bg-slate-800/80 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
                  >
                    {organizations.map((org) => (
                      <option key={org.id} value={org.id}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          )}

          {/* STEP 2: Server & Credentials */}
          {step === 2 && (
            <form id="wizard-finish-form" onSubmit={handleFinish} className="space-y-4">
              <div>
                <h3 className="text-sm font-semibold text-slate-200">
                  {erpType === "pri_erp" ? "PRI ERP Server & Credentials" : "Odoo Server & Credentials"}
                </h3>
                <p className="text-xs text-slate-400 mt-1">
                  Connect your live or staging ERP instance for schema analysis and module deployments.
                </p>
              </div>

              {/* URL */}
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Globe className="h-3.5 w-3.5 text-blue-400" />
                  Server URL
                </label>
                <input
                  type="url"
                  required
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="http://your-server-ip:8069"
                  className="w-full rounded-lg border border-white/10 bg-slate-800/80 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-blue-500 focus:outline-none"
                />
              </div>

              {/* Database */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                    <Database className="h-3.5 w-3.5 text-blue-400" />
                    Database Name
                  </label>
                  {detectingDatabases ? (
                    <span className="text-xs text-blue-400 flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" /> Detecting...
                    </span>
                  ) : (
                    discoveryMessage && (
                      <span className="text-xs text-slate-400">{discoveryMessage}</span>
                    )
                  )}
                </div>

                {detectedDatabases.length > 0 ? (
                  <select
                    value={dbName}
                    onChange={(e) => setDbName(e.target.value)}
                    className="w-full rounded-lg border border-white/10 bg-slate-800/80 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
                  >
                    {detectedDatabases.map((db) => (
                      <option key={db} value={db}>
                        {db}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    required
                    value={dbName}
                    onChange={(e) => setDbName(e.target.value)}
                    placeholder="e.g. production or test_db"
                    className="w-full rounded-lg border border-white/10 bg-slate-800/80 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-blue-500 focus:outline-none"
                  />
                )}
              </div>

              {/* Credentials Fields */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-300">Username / Email</label>
                  <input
                    type="text"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="admin@example.com"
                    className="w-full rounded-lg border border-white/10 bg-slate-800/80 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-300">Password</label>
                  <input
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-lg border border-white/10 bg-slate-800/80 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-blue-500 focus:outline-none"
                  />
                </div>
              </div>

              {/* Verification Button & Status */}
              <div className="pt-2 flex items-center justify-between">
                <button
                  type="button"
                  onClick={handleTestConnection}
                  disabled={verifying || !url.trim()}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-slate-800 text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                >
                  {verifying ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Globe className="h-3.5 w-3.5 text-blue-400" />
                  )}
                  Test Endpoint
                </button>
                {verifySuccess && (
                  <span className="text-xs text-emerald-400 font-medium flex items-center gap-1">
                    <CheckCircle2 className="h-3.5 w-3.5" /> Endpoint Reachable
                  </span>
                )}
                {verifyError && (
                  <span className="text-xs text-red-400 font-medium">{verifyError}</span>
                )}
              </div>
            </form>
          )}
        </div>

        {/* Footer Navigation */}
        <div className="flex items-center justify-between border-t border-white/10 px-6 py-4 bg-slate-900/60">
          {step === 2 ? (
            <button
              type="button"
              onClick={() => setStep(1)}
              className="flex items-center gap-1 text-xs font-medium px-3.5 py-2 rounded-lg border border-white/10 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white transition-colors"
            >
              <ChevronLeft className="h-4 w-4" />
              Back
            </button>
          ) : (
            <div />
          )}

          {step === 1 ? (
            <button
              type="button"
              onClick={() => {
                if (!projectName.trim()) {
                  setError("Please enter a project name");
                  return;
                }
                setError("");
                setStep(2);
              }}
              className="flex items-center gap-1 text-xs font-semibold px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors"
            >
              Continue to Server Setup
              <ChevronRight className="h-4 w-4" />
            </button>
          ) : (
            <button
              type="submit"
              form="wizard-finish-form"
              disabled={submitting || !url.trim() || !dbName.trim()}
              className="flex items-center gap-2 text-xs font-semibold px-5 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 text-white hover:from-blue-500 hover:to-indigo-500 disabled:opacity-50 transition-all shadow-lg shadow-blue-500/20"
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4 fill-white" />
              )}
              Create & Go to Workspace
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
