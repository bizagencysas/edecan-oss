"use client";

import { useEffect } from "react";

import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/lib/auth-context";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, loading } = useAuth();

  useEffect(() => {
    if (!loading && isAuthenticated) {
      window.location.replace("/app/");
    }
  }, [loading, isAuthenticated]);

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-brand-50 to-white px-4 py-12 dark:from-slate-950 dark:to-slate-950">
      <ThemeToggle className="absolute right-4 top-4 rounded-md p-2 text-slate-500 hover:bg-white/60 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-900/60 dark:hover:text-white" />
      <Logo className="mb-8" markClassName="h-10 w-10" wordClassName="text-xl" />
      <div className="w-full max-w-sm">{children}</div>
    </div>
  );
}
