"use client";

/**
 * Controles de teclado del modo "Control" (WP-V4-10, control remoto fase 2):
 * un campo de texto + botón "Enviar", y una fila de teclas especiales — cada
 * una dispara un `POST .../input {tipo: "key", ...}` vía el callback
 * `onSendKey` que le pasa `/app/remoto/page.tsx` (dueña de las llamadas HTTP
 * y del manejo de errores). El clic/doble clic/clic derecho SOBRE el frame
 * (mouse) vive en `RemoteViewer`, no aquí — este componente es solo teclado.
 */

import { useState } from "react";

import { Button, Field, Input } from "@/components/ui";
import type { KeyInputPayload, SpecialKey } from "@/lib/api-remoto";

const SPECIAL_KEYS: Array<{ value: SpecialKey; label: string; title: string }> = [
  { value: "enter", label: "Enter", title: "Enter" },
  { value: "tab", label: "Tab", title: "Tab" },
  { value: "escape", label: "Esc", title: "Escape" },
  { value: "backspace", label: "⌫", title: "Backspace" },
  { value: "arrow_up", label: "↑", title: "Flecha arriba" },
  { value: "arrow_down", label: "↓", title: "Flecha abajo" },
  { value: "arrow_left", label: "←", title: "Flecha izquierda" },
  { value: "arrow_right", label: "→", title: "Flecha derecha" },
];

export function RemoteControlPanel({
  onSendKey,
  sending,
  disabled = false,
}: {
  onSendKey: (payload: KeyInputPayload) => void;
  sending: boolean;
  disabled?: boolean;
}) {
  const [texto, setTexto] = useState("");

  function handleSendTexto() {
    if (!texto) return;
    onSendKey({ tipo: "key", texto });
    setTexto("");
  }

  const inputsDisabled = disabled || sending;

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <Field
        label="Escribir texto en el equipo remoto"
        hint="Se envía carácter por carácter al companion, como si lo tipearas ahí."
      >
        <div className="flex gap-2">
          <Input
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleSendTexto();
              }
            }}
            placeholder="Escribe aquí y pulsa Enviar…"
            disabled={inputsDisabled}
          />
          <Button
            type="button"
            variant="secondary"
            onClick={handleSendTexto}
            disabled={inputsDisabled || !texto}
            loading={sending}
          >
            Enviar
          </Button>
        </div>
      </Field>

      <div>
        <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-200">
          Teclas especiales
        </p>
        <div className="flex flex-wrap gap-2">
          {SPECIAL_KEYS.map((key) => (
            <Button
              key={key.value}
              type="button"
              size="sm"
              variant="secondary"
              title={key.title}
              disabled={inputsDisabled}
              onClick={() => onSendKey({ tipo: "key", tecla: key.value })}
            >
              {key.label}
            </Button>
          ))}
        </div>
      </div>
    </div>
  );
}
