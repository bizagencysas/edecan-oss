"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { PantallaPreparacion } from "@/components/preparacion/PantallaPreparacion";
import { FullPageSpinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, loading, isLocalDesktop } = useAuth();
  // Antes de abrir la app completamente en la PC del dueño (encargo de la
  // pantalla de preparación de Windows): solo la app de escritorio puede
  // tener requisitos del sistema sin cumplir -- `GET /v1/preparacion` ya
  // devuelve lista vacía fuera de Windows, así que en macOS/Linux esta
  // pantalla se resuelve sola de inmediato y nunca llega a pintar nada.
  const [preparacionLista, setPreparacionLista] = useState(false);

  useEffect(() => {
    if (!loading && !isAuthenticated) {
      window.location.replace("/login/");
    }
  }, [loading, isAuthenticated]);

  if (loading || !isAuthenticated) {
    return <FullPageSpinner label="Verificando tu sesión…" />;
  }

  if (isLocalDesktop && !preparacionLista) {
    return <PantallaPreparacion onListo={() => setPreparacionLista(true)} />;
  }

  return <AppShell>{children}</AppShell>;
}
