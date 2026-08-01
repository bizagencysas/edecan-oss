"use client";

/**
 * Inspector de contexto -- patrón 6 de §8 de FORGE-CONSTRUCCION-COMPLETA.md:
 * "el prompt exacto, por qué entró cada fragmento, cuántos tokens costó y
 * qué se descartó". Es, según el mismo documento (§F), "el panel más
 * importante para depurar" porque el 70 % de las causas raíz con un modelo
 * modesto están en cómo se ensambló el prompt, no en el modelo.
 *
 * Investigación real antes de escribir una sola línea de UI (mandato del
 * encargo: "si el backend no da estos datos, NO los inventes"):
 *
 *   1. `lib/api-ide.ts` HOY no tiene ningún `getIdeContext`/`getIdePrompt` ni
 *      nada que devuelva el prompt exacto que vio el modelo. El único
 *      endpoint real, adyacente, es `getIdeAgentCost` ->
 *      `GET /v1/ide/agents/{id}/cost` ("contabilidad de costo del ÚLTIMO
 *      turno", `routers/ide.py::get_agent_cost` -> `ide_costos.
 *      analizar_tarea`). Da tokens de entrada/salida/total (con su bandera
 *      `estimados`) y un desglose POR HERRAMIENTA -- real, pero es costo por
 *      herramienta invocada, no el prompt por secciones que pide §10.
 *
 *   2. `apps/companion/edecan_companion/ide_contexto.py` SÍ existe y ya
 *      calcula lo que hace falta para `/context` (uso de contexto turno a
 *      turno) y `/compact` (qué sobrevive a un resumen: pedidos, decisiones,
 *      archivos -- ver su propio docstring). Pero:
 *        - `routers/ide.py` no importa nada de ese módulo: no hay ruta HTTP
 *          que lo sirva. Mismo patrón de brecha que documenta
 *          `MemoryKnowledgePanel.tsx` para `ide_conocimiento.py`.
 *        - Incluso si se cableara, ese módulo lee `llm-calls.jsonl` (la
 *          bitácora de `edecan_core.llm_call_log`), y su propio docstring
 *          dice, con las dos manos: "hoy `log_llm_call` solo lo llama el
 *          agente de chat del SERVIDOR, no el agente local del IDE
 *          (`WorkersIDEAgent`, que no registra `usage` de ningún tipo
 *          todavía)". Es decir: ni el texto literal del prompt, ni
 *          `cached_tokens`, existen HOY para una sesión de IDE -- no es
 *          solo un cable que falta, es un dato que nadie está escribiendo
 *          todavía para este agente en particular.
 *        - Lo que `compactar()` sí calcula (`descartados_por_tipo`) es un
 *          CONTEO de eventos de narración descartados por tipo, no un
 *          `DropRecord` con ruta/tokens que habría costado cada fragmento
 *          de contexto -- el concepto más cercano que existe, y aun así no
 *          es el mismo dato.
 *
 * Conclusión, honesta: de las cuatro preguntas que exige el encargo, la (2)
 * tiene un sustituto real parcial (costo por herramienta, no por fragmento
 * de contexto); las otras tres (prompt literal por secciones, por qué entró
 * cada fragmento con su `SelectionReason`, qué se descartó, y el % que vino
 * de caché) no tienen NINGÚN dato real disponible hoy. Este archivo pinta lo
 * real y declara lo demás como pendiente de backend, con la cita exacta de
 * qué falta y dónde -- el mismo criterio que ya usa `MemoryKnowledgePanel.tsx`
 * (líneas 23-38) para "conocimiento verificado".
 *
 * El contrato tipado de abajo (`PromptSectionInspector`, `SelectionReason`,
 * `FragmentoInspector`, `DropRecordInspector`, `CacheStatsInspector`) es a
 * propósito LOCAL a este archivo y no de `lib/api-ide.ts`: el encargo de
 * este paquete de trabajo solo autoriza tocar este archivo nuevo. Cuando el
 * Context Engine (FASE F) exista de verdad, ese contrato se muda a
 * `api-ide.ts` junto con su función `getIdeContextInspector` -- queda
 * anotado también en la entrega del encargo.
 *
 * Autocontenido a propósito (mismo criterio que `MemoryKnowledgePanel.tsx` /
 * `SearchPanel.tsx`): llama a `lib/api-ide.ts` directo, sin pasar por
 * callbacks de `page.tsx` -- quien lo monte solo decide CUÁNDO mostrarlo
 * (`sessionId` + `onClose` opcional).
 *
 * Plegado por sección (cada una de las cuatro preguntas se abre/cierra por
 * separado) + virtualización de la lista de filas (`@tanstack/react-virtual`,
 * ya aprobado en §7) para el desglose por herramienta -- un turno real puede
 * traer decenas de invocaciones y, cuando el Context Engine exista, cientos
 * de fragmentos de contexto: no hay que pintarlos todos de golpe.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { ChartBarIcon, ChevronDownIcon, ChevronRightIcon, CodeIcon, TrashIcon, XIcon, ZapIcon } from "@/components/icons";
import { Alert, Badge, Spinner } from "@/components/ui";
import { ApiError, getIdeAgentCost, type IdeAgentCost } from "@/lib/api-ide";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatTokens(value: number): string {
  return value.toLocaleString("es");
}

// ---------------------------------------------------------------------------
// Contrato tipado del Context Engine (FASE F) -- ver el docstring de arriba
// sobre por qué vive acá y no en `lib/api-ide.ts` todavía.
// ---------------------------------------------------------------------------

/** Orden obligatorio de §10: "sistema → herramientas → contexto estable →
 * historial → turno actual, en ese orden y sin reordenar jamás. Vale 5× en
 * el precio de entrada" (estabilidad de prefijo para la caché). */
export type PromptSectionKind = "sistema" | "herramientas" | "contexto_estable" | "historial" | "turno_actual";

export const PROMPT_SECTION_ORDER: readonly PromptSectionKind[] = [
  "sistema",
  "herramientas",
  "contexto_estable",
  "historial",
  "turno_actual",
];

export const PROMPT_SECTION_LABEL: Record<PromptSectionKind, string> = {
  sistema: "Sistema",
  herramientas: "Herramientas",
  contexto_estable: "Contexto estable",
  historial: "Historial",
  turno_actual: "Turno actual",
};

/** Un bloque del prompt, byte a byte, en el orden real en que se concatenó. */
export interface PromptSectionInspector {
  kind: PromptSectionKind;
  content: string;
  tokens: number;
}

/** Por qué entró CADA fragmento de contexto -- léxico (ripgrep), reciente
 * (historial de edición), búsqueda del propio agente (`grep`/`read`), o
 * dependencia (lo arrastró otro fragmento). Ver FASE F del documento. */
export type SelectionReason = "lexico" | "reciente" | "busqueda_agente" | "dependencia";

export const SELECTION_REASON_LABEL: Record<SelectionReason, string> = {
  lexico: "Léxico (ripgrep)",
  reciente: "Reciente",
  busqueda_agente: "Búsqueda del agente",
  dependencia: "Dependencia",
};

export interface FragmentoInspector {
  path: string;
  reason: SelectionReason;
  tokens: number;
}

/** Tan informativo como lo que entró: qué se descartó y cuánto habría costado. */
export interface DropRecordInspector {
  path: string;
  tokens_habria_costado: number;
  motivo: string;
}

/** La entrada cacheada cuesta 5× menos (§10) -- este porcentaje es la métrica
 * que dice si la estabilidad de prefijo funciona de verdad. */
export interface CacheStatsInspector {
  tokens_cacheados: number;
  tokens_totales: number;
  ratio_cache: number;
}

/** Forma completa que este panel pintaría el día que el backend la sirva. */
export interface InspectorContextoData {
  secciones: PromptSectionInspector[];
  fragmentos: FragmentoInspector[];
  descartados: DropRecordInspector[];
  cache: CacheStatsInspector | null;
}

// ---------------------------------------------------------------------------
// Pieza reusable: sección numerada, plegable, con su badge real/pendiente.
// ---------------------------------------------------------------------------

function EstadoBadge({ real }: { real: boolean }) {
  return <Badge variant={real ? "success" : "neutral"}>{real ? "Datos reales" : "Pendiente de backend"}</Badge>;
}

function Seccion({
  numero,
  titulo,
  icon: Icon,
  real,
  defaultOpen = false,
  children,
}: {
  numero: number;
  titulo: string;
  icon: (props: { className?: string }) => React.ReactElement;
  real: boolean;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-forja-borde-suave dark:border-forja-borde-oscuro-suave">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left hover:bg-forja-superficie-elevada dark:hover:bg-slate-800"
      >
        {open ? (
          <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        ) : (
          <ChevronRightIcon className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        )}
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-forja-superficie-elevada text-[10px] font-bold text-slate-500 dark:bg-forja-superficie-oscura-elevada dark:text-slate-400">
          {numero}
        </span>
        <Icon className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        <span className="flex-1 truncate text-sm font-semibold text-slate-700 dark:text-slate-200">{titulo}</span>
        <EstadoBadge real={real} />
      </button>
      {open && (
        <div className="border-t border-forja-borde-suave px-3 py-3 dark:border-forja-borde-oscuro-suave">
          {children}
        </div>
      )}
    </div>
  );
}

function GapNotice({ children }: { children: React.ReactNode }) {
  return (
    <Alert variant="info">
      <div className="text-xs leading-5 opacity-90">{children}</div>
    </Alert>
  );
}

// ---------------------------------------------------------------------------
// Sección 3 (única con dato real hoy): costo medido del último turno, por
// herramienta -- lo más cercano que existe a "cuánto costó cada fragmento".
// ---------------------------------------------------------------------------

const FILA_ALTO_PX = 44;

function ListaHerramientas({ filas }: { filas: IdeAgentCost["por_herramienta"] }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: filas.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => FILA_ALTO_PX,
    overscan: 6,
  });

  if (filas.length === 0) {
    return <p className="py-4 text-center text-xs text-slate-400">Este turno no invocó ninguna herramienta.</p>;
  }

  return (
    <div ref={parentRef} className="max-h-64 overflow-y-auto thin-scrollbar">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((item) => {
          const fila = filas[item.index];
          const tokens = fila.tokens_reales ?? fila.tokens_estimados;
          return (
            <div
              key={item.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: item.size,
                transform: `translateY(${item.start}px)`,
              }}
              className="flex items-center gap-3 border-b border-forja-borde-suave px-1 text-xs last:border-b-0 dark:border-forja-borde-oscuro-suave"
            >
              <span className="min-w-0 flex-1 truncate font-mono text-slate-600 dark:text-slate-300" title={fila.nombre}>
                {fila.nombre}
              </span>
              <span className="w-20 shrink-0 truncate text-slate-400">
                {fila.acciones} {fila.acciones === 1 ? "acción" : "acciones"}
              </span>
              <span className="w-20 shrink-0 text-right font-mono text-slate-700 dark:text-slate-200">
                {formatTokens(tokens)} tk
              </span>
              <span className="w-10 shrink-0 text-right text-slate-400">{fila.porcentaje_tokens.toFixed(0)}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CostoMedidoTurno({ sessionId }: { sessionId: string }) {
  const [cost, setCost] = useState<IdeAgentCost | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getIdeAgentCost(sessionId)
      .then((data) => {
        if (!cancelled) setCost(data);
      })
      .catch((err) => {
        if (cancelled) return;
        const notFound = err instanceof ApiError && err.status === 404;
        setError(
          notFound
            ? "Todavía no hay un turno completo de este agente para contabilizar."
            : errorMessage(err, "No se pudo cargar el costo de este turno."),
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  if (error) {
    return <Alert variant="error">{error}</Alert>;
  }

  if (!cost) return null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="brand">
          {formatTokens(cost.tokens.total)} tokens totales · {formatTokens(cost.tokens.entrada)} entrada / {formatTokens(cost.tokens.salida)} salida
        </Badge>
        <Badge variant={cost.tokens.estimados ? "warning" : "success"}>
          {cost.tokens.estimados ? "Estimado (heurística de caracteres)" : "Medido por el proveedor"}
        </Badge>
        {cost.costo_usd != null && <Badge variant="neutral">${cost.costo_usd.toFixed(4)} USD</Badge>}
      </div>
      <p className="text-[11px] text-slate-400">
        Desglose por herramienta invocada en este turno -- el sustituto real más cercano que existe hoy a
        &quot;cuánto costó cada fragmento&quot;, aunque agrupa por herramienta y no por fragmento de contexto
        individual (ver más abajo por qué).
      </p>
      <ListaHerramientas filas={cost.por_herramienta} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel público
// ---------------------------------------------------------------------------

export interface InspectorContextoProps {
  /** Sesión de agente cuyo último turno se inspecciona. `null` = sin selección. */
  sessionId: string | null;
  sessionTitle?: string | null;
  onClose?: () => void;
  className?: string;
}

export function InspectorContexto({ sessionId, sessionTitle, onClose, className }: InspectorContextoProps) {
  const seccionesVacias = useMemo(
    () => PROMPT_SECTION_ORDER.map((kind) => ({ kind, label: PROMPT_SECTION_LABEL[kind] })),
    [],
  );

  return (
    <section
      className={`flex min-h-0 flex-col gap-3 bg-forja-superficie p-4 dark:bg-forja-superficie-oscura ${className ?? ""}`}
    >
      <div className="flex shrink-0 items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-slate-800 dark:text-slate-100">Inspector de contexto</h2>
          <p className="truncate text-[11px] text-slate-400">
            {sessionTitle || (sessionId ? `Sesión ${sessionId}` : "Ningún agente seleccionado")}
          </p>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar inspector de contexto"
            className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-forja-superficie-elevada dark:hover:bg-slate-800"
          >
            <XIcon className="h-4 w-4" />
          </button>
        )}
      </div>

      {!sessionId ? (
        <p className="py-8 text-center text-sm text-slate-400">
          Abre o selecciona un turno de agente para inspeccionar el prompt que recibió el modelo.
        </p>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto thin-scrollbar">
          <Seccion numero={1} titulo="Qué recibió el modelo, literal" icon={CodeIcon} real={false}>
            <GapNotice>
              <p className="font-semibold text-slate-700 dark:text-slate-200">
                Ningún endpoint de <code className="font-mono">lib/api-ide.ts</code> devuelve hoy el prompt exacto.
              </p>
              <p className="mt-1">
                El orden que exige §10 (sistema → herramientas → contexto estable → historial → turno actual, sin
                reordenar jamás, porque vale 5× en el precio de entrada) ya está definido como contrato en este mismo
                archivo (<code className="font-mono">PROMPT_SECTION_ORDER</code>), listo para pintarse en cuanto el
                backend lo sirva. <code className="font-mono">apps/companion/edecan_companion/ide_contexto.py</code>{" "}
                existe y calcula uso de contexto para <code className="font-mono">/context</code>, pero
                {" "}<code className="font-mono">routers/ide.py</code> no expone ninguna ruta de ese módulo, y aun
                cableada no incluye el texto literal por secciones -- solo conteos de tokens por llamada.
              </p>
            </GapNotice>
            <ul className="mt-2 space-y-1">
              {seccionesVacias.map((s) => (
                <li
                  key={s.kind}
                  className="flex items-center justify-between rounded-md border border-dashed border-forja-borde px-2.5 py-1.5 text-xs text-slate-400 dark:border-forja-borde-oscuro"
                >
                  <span>{s.label}</span>
                  <span>— tk</span>
                </li>
              ))}
            </ul>
          </Seccion>

          <Seccion numero={2} titulo="Por qué entró cada fragmento" icon={ChartBarIcon} real defaultOpen>
            <GapNotice>
              <p>
                <code className="font-mono">SelectionReason</code> (léxico / reciente / búsqueda del agente /
                dependencia) no existe en ningún endpoint hoy -- es parte del Context Engine (FASE F, sin construir).
                Lo real disponible es el costo <strong>por herramienta</strong> del último turno
                (<code className="font-mono">GET /v1/ide/agents/{"{id}"}/cost</code>), que no es lo mismo que un
                fragmento de contexto pero sí la mejor aproximación real a &quot;en qué se gastaron los tokens&quot;
                que existe hoy.
              </p>
            </GapNotice>
            <div className="mt-2">
              <CostoMedidoTurno sessionId={sessionId} />
            </div>
          </Seccion>

          <Seccion numero={3} titulo="Qué se descartó y cuánto habría costado" icon={TrashIcon} real={false}>
            <GapNotice>
              <p>
                <code className="font-mono">DropRecord</code> (ruta + tokens que habría costado) no tiene ningún
                equivalente expuesto. Lo más cercano que existe es{" "}
                <code className="font-mono">ide_contexto.compactar()</code>, que sí cuenta cuántos eventos de
                narración se descartarían en un <code className="font-mono">/compact</code> agrupados por tipo
                (<code className="font-mono">descartados_por_tipo</code>) -- pero es un CONTEO de eventos, no tokens
                por fragmento de contexto, y esa ruta tampoco está cableada en{" "}
                <code className="font-mono">routers/ide.py</code>.
              </p>
            </GapNotice>
          </Seccion>

          <Seccion numero={4} titulo="Cuánto de la entrada vino de caché" icon={ZapIcon} real={false}>
            <GapNotice>
              <p>
                La entrada cacheada cuesta 5× menos (§10) y ese porcentaje es la métrica que confirma si la
                estabilidad de prefijo funciona -- pero <code className="font-mono">IdeAgentCost</code> (el único
                endpoint de costo que ve el IDE) solo trae <code className="font-mono">tokens.entrada/salida/total</code>
                , sin ningún campo de caché. El dato <code className="font-mono">cached_tokens</code> sí existe en
                el proveedor y en el esquema de <code className="font-mono">llm-calls.jsonl</code>{" "}
                (<code className="font-mono">edecan_core.llm_call_log</code>), pero ese archivo solo lo escribe hoy
                el agente de chat del servidor -- el propio docstring de{" "}
                <code className="font-mono">ide_contexto.py</code> confirma que{" "}
                <code className="font-mono">WorkersIDEAgent</code> (el agente del IDE) &quot;no registra{" "}
                <code className="font-mono">usage</code> de ningún tipo todavía&quot;. No hay ratio de caché que
                mostrar, para nada, hoy.
              </p>
            </GapNotice>
          </Seccion>
        </div>
      )}
    </section>
  );
}

export default InspectorContexto;
