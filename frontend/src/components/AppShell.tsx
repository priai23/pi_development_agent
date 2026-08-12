"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { apiFetch, AuthState, setCsrfToken, User } from "@/lib/api";
import Sidebar from "./Sidebar";

type AuthContextValue = { user: User | null; refresh: () => Promise<void> };
const AuthContext = createContext<AuthContextValue>({ user: null, refresh: async () => undefined });
export function useAuth() { return useContext(AuthContext); }

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const isPublic = pathname === "/login" || pathname === "/reset-password";

  const refresh = async () => {
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
  };

  useEffect(() => {
    // The state changes happen only after the authentication request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  if (loading) return <div className="flex h-screen items-center justify-center text-gray-500">Loading…</div>;
  if (isPublic) return <AuthContext.Provider value={{ user, refresh }}>{children}</AuthContext.Provider>;
  if (!user) return null;
  return <AuthContext.Provider value={{ user, refresh }}><div className="flex min-h-screen w-full"><Sidebar /><main className="min-w-0 flex-1">{children}</main></div></AuthContext.Provider>;
}
