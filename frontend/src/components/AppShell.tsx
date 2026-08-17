"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { apiFetch, AuthState, setCsrfToken, User } from "@/lib/api";
import Sidebar from "./Sidebar";
import { Menu } from "lucide-react";

type AuthContextValue = { user: User | null; refresh: () => Promise<void> };
const AuthContext = createContext<AuthContextValue>({ user: null, refresh: async () => undefined });
export function useAuth() { return useContext(AuthContext); }

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const isPublic = pathname === "/login" || pathname === "/reset-password" || pathname === "/setup";

  const refresh = useCallback(async () => {
    try {
      const state = await apiFetch<AuthState>("/auth/me");
      setCsrfToken(state.csrf_token);
      setUser(state.user);
      if (state.user.must_change_password && pathname !== "/account") router.replace("/account");
      else if (isPublic && pathname === "/login") router.replace("/projects");
    } catch {
      setUser(null);
      if (!isPublic) router.replace("/login");
    } finally { setLoading(false); }
  }, [isPublic, pathname, router]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  useEffect(() => {
    const unauthorized = () => {
      setCsrfToken("");
      setUser(null);
      if (!isPublic) router.replace("/login");
    };
    window.addEventListener("auth:unauthorized", unauthorized);
    return () => window.removeEventListener("auth:unauthorized", unauthorized);
  }, [isPublic, router]);

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading…</div>;
  if (isPublic) return <AuthContext.Provider value={{ user, refresh }}>{children}</AuthContext.Provider>;
  if (!user) return null;
  return <AuthContext.Provider value={{ user, refresh }}><div className="flex min-h-screen w-full">
    {navigationOpen && <button className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setNavigationOpen(false)} aria-label="Close navigation overlay" />}
    <Sidebar open={navigationOpen} onClose={() => setNavigationOpen(false)} />
    <main className="min-w-0 flex-1">
      <button type="button" onClick={() => setNavigationOpen(true)} className="sticky left-3 top-3 z-30 m-3 rounded-lg border bg-black/70 p-2 text-white lg:hidden" aria-label="Open navigation"><Menu className="h-5 w-5" /></button>
      {children}
    </main>
  </div></AuthContext.Provider>;
}
