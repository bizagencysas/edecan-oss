"use client";

/**
 * Pantalla de consentimiento — SIEMPRE lo primero que ve el usuario antes de
 * poder iniciar una sesión remota. En la app instalada, el QR ya vinculó el
 * teléfono con esta computadora; no existe un segundo emparejamiento.
 *
 * `allowControl` (WP-V4-10, control remoto fase 2) habilita el checkbox
 * adicional para pedir `kind="control"` en vez de `"view"` — el padre
 * (`/app/remoto/page.tsx`) lo calcula a partir del flag de plan
 * `companion.remote_input`; SIN ese flag este componente ni siquiera muestra
 * la opción ("deshabilita todo si el flag del plan no está").
 */

import { useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Checkbox } from "@/components/ui";
import type { RemoteSessionKind } from "@/lib/api-remoto";

export function ConsentGate({
  starting,
  onStart,
  allowControl,
}: {
  starting: boolean;
  onStart: (kind: RemoteSessionKind) => void;
  /** `true` solo si el plan trae `companion.remote_input` — ver el docstring del módulo. */
  allowControl: boolean;
}) {
  const [checked, setChecked] = useState(false);
  const [wantsControl, setWantsControl] = useState(false);

  const kind: RemoteSessionKind = allowControl && wantsControl ? "control" : "view";

  return (
    <Card>
      <CardHeader
        title={kind === "control" ? "Iniciar sesión de control remoto" : "Iniciar sesión de vista remota"}
        description={
          kind === "control"
            ? "Vas a ver y manejar esta computadora desde tu teléfono."
            : "Vas a ver la pantalla de esta computadora sin mover el mouse ni escribir."
        }
      />
      <CardBody className="space-y-4">
        <Alert variant="info">
          El teléfono que escaneó el QR ya está vinculado con esta computadora. Confirma esta
          sesión una vez y podrás terminarla en cualquier momento.
        </Alert>

        <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-600 dark:text-slate-300">
          {kind === "control" ? (
            <>
              <li>Esta sesión puede ver la pantalla Y mover el mouse / escribir en tu equipo.</li>
              <li>La autorización dura solo mientras esta sesión permanezca abierta.</li>
            </>
          ) : (
            <li>Esta sesión solo puede VER la pantalla — no existe forma de mover el mouse ni escribir.</li>
          )}
          <li>Puedes terminarla en cualquier momento con «Terminar sesión».</li>
          <li>Inicio, fin y cada comando quedan registrados en tu historial de seguridad.</li>
        </ul>

        {allowControl && (
          <Checkbox
            label="También quiero usar el mouse y el teclado"
            checked={wantsControl}
            onChange={(e) => setWantsControl(e.target.checked)}
          />
        )}

        <Checkbox
          label={
            kind === "control"
              ? "Confirmo que quiero ver y controlar esta computadora desde mi teléfono."
              : "Confirmo que quiero ver la pantalla de esta computadora desde mi teléfono."
          }
          checked={checked}
          onChange={(e) => setChecked(e.target.checked)}
        />

        <Button onClick={() => onStart(kind)} disabled={!checked} loading={starting}>
          {kind === "control" ? "Iniciar sesión de control remoto" : "Iniciar sesión de vista remota"}
        </Button>
      </CardBody>
    </Card>
  );
}
