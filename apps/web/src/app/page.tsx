"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { FullPageSpinner } from "@/components/ui";
import { hasSession } from "@/lib/tokens";

/**
 * `/` no es una pantalla en sí: redirige a `/app` (el chat) si ya hay sesión
 * (tokens en localStorage) o a `/login` si no. `(app)/layout.tsx` valida la
 * sesión de verdad contra `GET /v1/me`; aquí solo se decide el destino
 * inicial sin parpadeo.
 */
export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(hasSession() ? "/app" : "/login");
  }, [router]);

  return <FullPageSpinner label="Abriendo Edecán…" />;
}
