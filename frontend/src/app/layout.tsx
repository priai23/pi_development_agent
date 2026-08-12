import type { Metadata } from "next";
import "./globals.css";
import AppShell from "@/components/AppShell";

export const metadata: Metadata = {
  title: "ERP Implementation Agent",
  description: "AI-assisted ERP implementation by Primacy Infotech",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className="min-h-screen bg-[var(--background)] text-[var(--foreground)] antialiased"><AppShell>{children}</AppShell></body></html>;
}
