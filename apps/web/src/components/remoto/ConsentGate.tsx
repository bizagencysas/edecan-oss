"use client";

/**
 * Pantalla de consentimiento — SIEMPRE lo primero que ve el usuario antes de
 * poder iniciar una sesión remota (`ROADMAP_V2.md` §5 WP-V2-09, principio de
 * "doble consentimiento" de `docs/control-remoto.md`).
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
            ? "Vas a ver la pantalla de tu equipo Y permitir que se mueva el mouse y se escriba en él desde aquí."
            : "Solo lectura: vas a ver la pantalla de tu equipo desde aquí — nadie puede mover tu mouse ni tu teclado."
        }
      />
      <CardBody className="space-y-4">
        <Alert variant="info">
          Esto pide DOS aprobaciones distintas, en dos lugares distintos: la que das aquí abajo, y
          una segunda, local, que tu companion (la app de escritorio de Edecán) te va a mostrar en
          tu propia máquina antes de mandar la primera imagen
          {kind === "control" && " (y de nuevo, por cada comando de teclado/mouse)"}. Sin esa
          segunda aprobación no sale ni un solo frame
          {kind === "control" && " ni se mueve un solo pixel"}. El diseño completo (emparejamiento,
          cifrado, qué falta para llegar a control total) vive en{" "}
          <code>docs/control-remoto.md</code>.
        </Alert>

        <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-600 dark:text-slate-300">
          {kind === "control" ? (
            <>
              <li>Esta sesión puede ver la pantalla Y mover el mouse / escribir en tu equipo.</li>
              <li>
                Cada clic o tecla individual sigue pidiendo aprobación de tu companion (con un
                margen para recordarla unos minutos, SOLO dentro de esta misma sesión).
              </li>
            </>
          ) : (
            <li>Esta sesión solo puede VER la pantalla — no existe forma de mover el mouse ni escribir.</li>
          )}
          <li>Vas a ver un aviso permanente en pantalla mientras la sesión esté activa.</li>
          <li>Puedes terminarla en cualquier momento con «Terminar sesión», desde aquí o desde tu equipo.</li>
          <li>Inicio, fin y cada comando quedan registrados en la auditoría del tenant.</li>
        </ul>

        {allowControl && (
          <Checkbox
            label="Además, habilitar control remoto de teclado y mouse (requiere que tu companion tenga remote_input_enabled activado)."
            checked={wantsControl}
            onChange={(e) => setWantsControl(e.target.checked)}
          />
        )}

        <Checkbox
          label={
            kind === "control"
              ? "Entiendo que voy a ver Y controlar la pantalla de mi equipo, y que el companion me pedirá una aprobación local antes de cada acción."
              : "Entiendo que voy a ver la pantalla de mi equipo y que el companion me pedirá una aprobación local antes de empezar."
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
