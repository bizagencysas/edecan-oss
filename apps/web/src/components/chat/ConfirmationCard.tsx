"use client";

import { useState } from "react";

import { Button } from "@/components/ui";

/**
 * Advertencias específicas por herramienta, en lenguaje llano, además del
 * JSON crudo plegado (hallazgo de auditoría "riesgo-legal-tos").
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
  instalar_skill:
    "Una skill son INSTRUCCIONES que Edecán va a seguir literalmente cuando se active, no un " +
    "archivo de datos. Instalarla es dejar que quien la escribió influya en cómo se comporta " +
    "tu asistente. Mira abajo DE DÓNDE viene: si es una URL o una carpeta que no reconoces, " +
    "o si llegó de algo que Edecán leyó por su cuenta y no de ti, rechaza. Las que no vienen " +
    "de un índice revisado quedan marcadas «sin revisar» a propósito.",
};

const TITULOS_CORTOS: Record<string, string> = {
  usar_computadora: "¿Uso tu computadora?",
  llamar_contacto: "¿Hago esta llamada?",
  instalar_skill: "¿Instalo esta skill?",
  configurar_credencial: "¿Guardo esta credencial?",
};

function tituloParaHerramienta(name: string): string {
  if (TITULOS_CORTOS[name]) return TITULOS_CORTOS[name];
  const legible = name.replace(/[_-]+/g, " ").trim();
  return legible ? `¿Apruebo «${legible}»?` : "¿Apruebo esta acción?";
}

function CallPreflight({ args }: { args: Record<string, unknown> }) {
  const fields = [
    ["Persona", args.destinatario],
    ["Número", args.telefono_e164],
    ["Agente", args.agente],
    ["Objetivo", args.objetivo],
  ] as const;

  return (
    <dl className="mt-2 grid gap-2 rounded-xl border border-amber-200/80 bg-white/70 p-3 text-xs dark:border-amber-800 dark:bg-black/20 sm:grid-cols-2">
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
  onViewComputer,
  loading,
}: {
  name: string;
  args: Record<string, unknown>;
  onApprove: () => void;
  onDeny: () => void;
  onViewComputer?: () => void;
  loading: boolean;
}) {
  const [mostrarDetalle, setMostrarDetalle] = useState(false);
  const advertenciaEspecifica = ADVERTENCIAS_POR_HERRAMIENTA[name];
  const tieneArgs = Object.keys(args).length > 0;

  return (
    <div className="max-w-[340px] rounded-2xl border border-amber-300/90 bg-amber-50 px-3.5 py-3 text-sm shadow-sm dark:border-amber-800 dark:bg-amber-950/40">
      <p className="font-semibold text-amber-950 dark:text-amber-100">{tituloParaHerramienta(name)}</p>
      {advertenciaEspecifica && (
        <p
          className={`mt-1.5 text-xs text-amber-900 dark:text-amber-100 ${mostrarDetalle ? "" : "line-clamp-3"}`}
        >
          {advertenciaEspecifica}
        </p>
      )}
      {name === "llamar_contacto" ? (
        <CallPreflight args={args} />
      ) : (
        tieneArgs && (
          <>
            <button
              type="button"
              onClick={() => setMostrarDetalle((value) => !value)}
              className="mt-2 text-xs font-semibold text-amber-800 underline-offset-2 hover:underline dark:text-amber-200"
            >
              {mostrarDetalle ? "Ocultar detalles" : "Ver detalles"}
            </button>
            {mostrarDetalle && (
              <pre className="mt-1.5 max-h-28 overflow-auto rounded-lg bg-white/70 p-2 text-[11px] text-amber-900 dark:bg-black/20 dark:text-amber-100">
                {JSON.stringify(args, null, 2)}
              </pre>
            )}
          </>
        )
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" onClick={onDeny} disabled={loading}>
          Rechazar
        </Button>
        {name === "usar_computadora" && onViewComputer && (
          <Button size="sm" variant="secondary" onClick={onViewComputer} disabled={loading}>
            Ver computadora
          </Button>
        )}
        <Button size="sm" onClick={onApprove} loading={loading} className="ml-auto">
          Aprobar
        </Button>
      </div>
    </div>
  );
}
