"use client";

import { Button } from "@/components/ui";

/**
 * Advertencias específicas por herramienta, en lenguaje llano, además del
 * JSON crudo de abajo (hallazgo de auditoría "riesgo-legal-tos": una tarjeta
 * genérica no le da a quien aprueba ninguna pista concreta de qué mirar antes
 * de confirmar). `usar_computadora` (control remoto de pantalla/mouse/teclado,
 * `packages/toolkit/edecan_toolkit/computadora.py`) es la más importante:
 * a diferencia de `navegar_web`/`comparar_precios`, que reciben una URL que
 * `edecan_browser.policy.check_navigation` puede bloquear en código (scraping
 * no autorizado, checkout, SSRF), esta herramienta actúa por coordenadas y
 * pulsaciones de teclado — no hay URL que un guardrail de código pueda
 * inspeccionar, así que esta advertencia y el juicio de quien aprueba son la
 * única defensa consciente del contenido en pantalla en este punto.
 */
const ADVERTENCIAS_POR_HERRAMIENTA: Record<string, string> = {
  usar_computadora:
    "Esto va a mover el mouse, escribir o mirar la pantalla de tu computadora de verdad. " +
    "Revisa la app, el destino y el contenido exactos antes de aprobar. Puede continuar una " +
    "tarea en una sesión que ya abriste —incluida una publicación en LinkedIn—, pero no debe " +
    "capturar contraseñas, hacer scraping o contacto masivo, ni completar un pago sin el flujo " +
    "específico que tú revisaste.",
  configurar_credencial:
    "Esto va a GUARDAR de verdad la credencial que ves abajo (queda cifrada, pero se " +
    "persiste). Revisa que sea tuya y que la hayas pegado tú -- si llegó de un correo, " +
    "página o mensaje que Edecán leyó por su cuenta, rechaza: nunca debe reconfigurar " +
    "credenciales por instrucciones que no vinieron de ti directamente en este chat.",
  acceder_codigo_local:
    "Esto va a leer, escribir, correr un comando o hacer un commit de verdad en tu clon " +
    "local del repo. No va a hacer 'git push' ni tocar GitHub por su cuenta -- pero revisa " +
    "igual el comando/contenido exacto de abajo antes de aprobar, sobre todo si es " +
    "'ejecutar_comando' o 'git_commit'.",
  llamar_contacto:
    "Esto iniciará una llamada real y puede tener costo en tu cuenta de Twilio. " +
    "Comprueba la persona, el número internacional, el agente exacto y el objetivo. " +
    "Aprueba solo si reconoces a la persona y tienes su consentimiento para recibir la llamada.",
};

function CallPreflight({ args }: { args: Record<string, unknown> }) {
  const fields = [
    ["Persona", args.destinatario],
    ["Número", args.telefono_e164],
    ["Agente", args.agente],
    ["Objetivo", args.objetivo],
  ] as const;

  return (
    <dl className="mt-3 grid gap-2 rounded-xl border border-amber-200 bg-white/70 p-3 text-xs dark:border-amber-800 dark:bg-black/20 sm:grid-cols-2">
      {fields.map(([label, value]) => (
        <div key={label} className={label === "Objetivo" ? "sm:col-span-2" : ""}>
          <dt className="text-amber-700 dark:text-amber-300">{label}</dt>
          <dd className="mt-0.5 font-semibold text-amber-950 dark:text-amber-100">
            {String(value || "Falta confirmar")}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function ConfirmationCard({
  name,
  args,
  onApprove,
  onDeny,
  loading,
}: {
  name: string;
  args: Record<string, unknown>;
  onApprove: () => void;
  onDeny: () => void;
  loading: boolean;
}) {
  const advertenciaEspecifica = ADVERTENCIAS_POR_HERRAMIENTA[name];

  return (
    <div className="max-w-[85%] rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm shadow-sm dark:border-amber-800 dark:bg-amber-950/40 sm:max-w-[75%]">
      <p className="font-medium text-amber-900 dark:text-amber-200">
        Edecán quiere ejecutar <span className="font-mono">{name}</span>
      </p>
      {advertenciaEspecifica && (
        <p className="mt-2 text-xs font-medium text-amber-900 dark:text-amber-100">
          {advertenciaEspecifica}
        </p>
      )}
      {name === "llamar_contacto" ? (
        <CallPreflight args={args} />
      ) : (
        <pre className="mt-2 max-h-32 overflow-auto rounded-lg bg-white/70 p-2 text-xs text-amber-900 dark:bg-black/20 dark:text-amber-100">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}
      <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
        Es una acción marcada como sensible: requiere tu confirmación antes de ejecutarse.
      </p>
      <div className="mt-3 flex gap-2">
        <Button size="sm" onClick={onApprove} loading={loading}>
          Aprobar
        </Button>
        <Button size="sm" variant="secondary" onClick={onDeny} disabled={loading}>
          Rechazar
        </Button>
      </div>
    </div>
  );
}
