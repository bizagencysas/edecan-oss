"use client";

import { useEffect } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { FullPageSpinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, loading } = useAuth();

  useEffect(() => {
    if (!loading && !isAuthenticated) {
      window.location.replace("/login/");
    }
  }, [loading, isAuthenticated]);

  if (loading || !isAuthenticated) {
    return <FullPageSpinner label="Verificando tu sesión…" />;
  }

  return <AppShell>{children}</AppShell>;
}
