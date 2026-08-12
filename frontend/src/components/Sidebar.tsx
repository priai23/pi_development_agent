"use client";

import Link from "next/link";
import { CheckSquare, FolderGit2, LogOut, Settings, ShieldCheck, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { apiFetch, setCsrfToken } from "@/lib/api";
import { useAuth } from "./AppShell";

export default function Sidebar() {
  const { user } = useAuth();
  const router = useRouter();
  const logout = async () => {
    await apiFetch<void>("/auth/logout", { method: "POST" });
    setCsrfToken("");
    router.replace("/login");
  };
  return (
    <aside className="glass-panel sticky top-0 flex h-screen w-64 flex-col border-r border-black/5 p-4 dark:border-white/10">
      <div className="p-2"><h2 className="text-xl font-bold">Primacy AI</h2><p className="mt-1 truncate text-xs text-gray-500">{user?.email}</p></div>
      <nav className="mt-6 flex-1 space-y-2">
        <Link href="/projects" className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10"><FolderGit2 className="h-5 w-5" /> Projects</Link>
        <Link href="/approvals" className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10"><CheckSquare className="h-5 w-5" /> Approvals</Link>
        {user?.role === "admin" && <><Link href="/admin" className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10"><ShieldCheck className="h-5 w-5" /> Administration</Link><Link href="/settings" className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10"><Settings className="h-5 w-5" /> Model settings</Link></>}
        <Link href="/account" className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-black/5 dark:hover:bg-white/10"><UserRound className="h-5 w-5" /> Account & sessions</Link>
      </nav>
      <button onClick={() => void logout()} className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-left text-gray-600 hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/10"><LogOut className="h-5 w-5" /> Sign out</button>
    </aside>
  );
}
