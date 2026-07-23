"use client";

import { useEffect } from "react";

import { FullPageSpinner } from "@/components/ui";
import { getSetupStatus } from "@/lib/api-configuracion";
import { useAuth } from "@/lib/auth-context";

/**
 * `/` no es una pantalla en sí: redirige a `/app` (el chat) si ya hay sesión
 * (sesión persistente de desktop o sesión de navegador) o a `/login` si no.
 * `(app)/layout.tsx` valida la
 * sesión de verdad contra `GET /v1/me`; aquí solo se decide el destino
 * inicial sin parpadeo.
 */
export default function HomePage() {
  const { isAuthenticated, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    // El export estático de Next genera `index.txt` para navegación RSC. En
    // un servidor de archivos local, la navegación interna puede terminar
    // mostrando ese payload como texto al reiniciar la app. Una navegación
    // de documento con slash final siempre solicita el `index.html` real.
    if (!isAuthenticated) {
      window.location.replace("/login/");
      return;
    }
    void getSetupStatus()
      .then((status) => {
        if (!cancelled) {
          window.location.replace(
            status.onboarding_completed ? "/app/" : "/app/bienvenida/",
          );
        }
      })
      .catch(() => {
        // La sesión ya es válida. Si el estado de bienvenida falla de forma
        // transitoria, el chat sigue siendo una salida útil y puede reintentar.
        if (!cancelled) window.location.replace("/app/");
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, loading]);

  return <FullPageSpinner label="Abriendo Edecán…" />;
}
