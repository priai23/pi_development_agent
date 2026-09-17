"use client";

import Link from "next/link";
import { CheckSquare, FolderGit2, LogOut, Settings, ShieldCheck, UserRound, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, setCsrfToken } from "@/lib/api";
import { useAuth } from "./AppShell";

export default function Sidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const { user } = useAuth();
  const router = useRouter();
  const [width, setWidth] = useState(256);
  const [resizing, setResizing] = useState(false);
  const compact = width < 180;
  useEffect(() => { // eslint-disable-next-line react-hooks/set-state-in-effect
    const saved = Number(localStorage.getItem("app:sidebarWidth")); if (saved) setWidth(Math.min(360, Math.max(64, saved)));
  }, []);
  useEffect(() => {
    if (!resizing) return;
    const move = (event: PointerEvent) => { const next = Math.min(360, Math.max(64, event.clientX)); setWidth(next); localStorage.setItem("app:sidebarWidth", String(next)); };
    const stop = () => setResizing(false);
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
  }, [resizing]);
  const logout = async () => {
    try {
      await apiFetch<void>("/auth/logout", { method: "POST" });
    } catch {
      // Ignore network / auth errors during logout
    } finally {
      setCsrfToken("");
      router.replace("/login");
    }
  };
  return (
    <aside style={{ width: `${width}px` }} className={`glass-panel fixed inset-y-0 left-0 z-50 flex h-screen shrink-0 flex-col border-r border-black/5 p-4 transition-transform dark:border-white/10 lg:sticky lg:translate-x-0 lg:transition-[width,transform] ${open ? "translate-x-0" : "-translate-x-full"}`} aria-label="Main navigation">
      <div className={`flex items-start justify-between p-2 ${compact ? "justify-center" : ""}`}><div>{!compact && <><h2 className="text-xl font-bold">Primacy AI</h2><p className="mt-1 max-w-44 truncate text-xs text-gray-500">{user?.email}</p></>}</div><button type="button" onClick={onClose} className="rounded p-1 lg:hidden" aria-label="Close navigation"><X className="h-5 w-5" /></button></div>
      <nav className="mt-6 flex-1 space-y-2">
        <Link title="Projects" onClick={onClose} href="/projects" className={`flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10 ${compact ? "justify-center" : ""}`}><FolderGit2 className="h-5 w-5 shrink-0" /> {!compact && "Projects"}</Link>
        <Link title="Approvals" onClick={onClose} href="/approvals" className={`flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10 ${compact ? "justify-center" : ""}`}><CheckSquare className="h-5 w-5 shrink-0" /> {!compact && "Approvals"}</Link>
        {user?.role === "admin" && <><Link title="Administration" href="/admin" className={`flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10 ${compact ? "justify-center" : ""}`}><ShieldCheck className="h-5 w-5 shrink-0" /> {!compact && "Administration"}</Link><Link title="Model settings" href="/settings" className={`flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10 ${compact ? "justify-center" : ""}`}><Settings className="h-5 w-5 shrink-0" /> {!compact && "Model settings"}</Link></>}
        <Link title="Account & sessions" href="/account" className={`flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10 ${compact ? "justify-center" : ""}`}><UserRound className="h-5 w-5 shrink-0" /> {!compact && "Account & sessions"}</Link>
      </nav>
      <button title="Sign out" onClick={() => void logout()} className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-left text-gray-600 hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/10 ${compact ? "justify-center" : ""}`}><LogOut className="h-5 w-5 shrink-0" /> {!compact && "Sign out"}</button>
      <div onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); setResizing(true); }} role="separator" aria-label="Resize navigation sidebar" aria-orientation="vertical" aria-valuemin={64} aria-valuemax={360} aria-valuenow={Math.round(width)} tabIndex={0} onKeyDown={(event) => { if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return; event.preventDefault(); setWidth((current) => { const next = Math.min(360, Math.max(64, current + (event.key === "ArrowRight" ? 16 : -16))); localStorage.setItem("app:sidebarWidth", String(next)); return next; }); }} className={`absolute inset-y-0 -right-2 z-50 hidden w-4 cursor-col-resize lg:block ${resizing ? "bg-blue-500/70" : "hover:bg-blue-500/40"}`} />
    </aside>
  );
}
