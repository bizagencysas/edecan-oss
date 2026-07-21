"use client";

import { useEffect } from "react";

import { FullPageSpinner } from "@/components/ui";
import { hasSession } from "@/lib/tokens";

/**
 * `/` no es una pantalla en sí: redirige a `/app` (el chat) si ya hay sesión
 * (sesión persistente de desktop o sesión de navegador) o a `/login` si no.
 * `(app)/layout.tsx` valida la
 * sesión de verdad contra `GET /v1/me`; aquí solo se decide el destino
 * inicial sin parpadeo.
 */
export default function HomePage() {
  useEffect(() => {
    // El export estático de Next genera `index.txt` para navegación RSC. En
    // un servidor de archivos local, la navegación interna puede terminar
    // mostrando ese payload como texto al reiniciar la app. Una navegación
    // de documento con slash final siempre solicita el `index.html` real.
    window.location.replace(hasSession() ? "/app/" : "/login/");
  }, []);

  return <FullPageSpinner label="Abriendo Edecán…" />;
}
