"use client";

import { useEffect, useMemo, useState } from "react";

import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, XIcon } from "@/components/icons";
import type { ChatModelCatalog, ChatModelInfo } from "@/lib/types";

/**
 * Hoja "Seleccionar modelo" del chat.
 *
 * Estructura calcada a la de la app de Claude porque es la que el dueño pidió
 * (portada con los modelos principales, fila "Esfuerzo" con su valor a la
 * derecha, fila "Más modelos"), pero pintada con los tokens de este proyecto
 * (slate/brand de Tailwind, claro y oscuro) — nada de la paleta de Claude.
 *
 * Todo lo que se ve viene de `GET /v1/models/chat`: ni un nombre, ni un id, ni
 * un nivel de esfuerzo está escrito aquí. Así, agregar un modelo es editar
 * `config/modelos.yml` y esta hoja se entera sola.
 */

type Vista = "modelos" | "esfuerzo" | "mas";

/** `null` = automático: el backend deja `chat_model` en NULL y decide la cadena
 * de siempre (`WORKERS_AI_CHAT_MODEL` -> `MODELO_POR_DEFECTO`). */
export type SeleccionModelo = string | null;

export function etiquetaEsfuerzo(esfuerzo: string): string {
  return esfuerzo.charAt(0).toUpperCase() + esfuerzo.slice(1);
}

/** Modelo al que el backend degrada un turno con imagen cuando el elegido es
 * ciego. Espeja `modelo_chat_con_vision_por_defecto()` de
 * `packages/llm/edecan_llm/task_router.py` (primer principal con visión) para
 * poder NOMBRARLO en el aviso; si el criterio allá cambia, cambia acá. */
export function modeloConVisionPorDefecto(catalogo: ChatModelCatalog): ChatModelInfo | null {
  const porOrden = [...catalogo.modelos].sort((a, b) => a.orden - b.orden);
  return (
    porOrden.find((modelo) => modelo.ve_imagenes && modelo.principal) ??
    porOrden.find((modelo) => modelo.ve_imagenes) ??
    null
  );
}

/** Texto de la pastilla del composer: "Oda · Alto", "Copla" (sin esfuerzo
 * porque no lo soporta) o "Automático". Vive aquí para que la pastilla y la
 * hoja no puedan discrepar nunca. */
export function etiquetaSeleccion(
  catalogo: ChatModelCatalog | null,
  model: SeleccionModelo,
  effort: string | null,
): string {
  if (!catalogo || !model) return "Automático";
  const info = catalogo.modelos.find((modelo) => modelo.id === model);
  if (!info) return "Automático";
  if (!info.soporta_esfuerzo) return info.nombre;
  return `${info.nombre} · ${etiquetaEsfuerzo(effort || catalogo.esfuerzo_default)}`;
}

export function ModelSelector({
  catalogo,
  model,
  effort,
  onSelect,
  onClose,
}: {
  catalogo: ChatModelCatalog;
  model: SeleccionModelo;
  effort: string | null;
  /** Se llama con la selección COMPLETA (modelo + esfuerzo), no con un delta:
   * el `PUT` escribe las dos claves siempre. Elegir modelo cierra la hoja;
   * elegir esfuerzo vuelve a la portada sin cerrarla (así se ve el valor nuevo
   * en su fila), y de eso se encarga esta hoja, no el padre. */
  onSelect: (model: SeleccionModelo, effort: string | null) => void;
  onClose: () => void;
}) {
  const [vista, setVista] = useState<Vista>("modelos");

  const principales = useMemo(
    () =>
      catalogo.modelos
        .filter((modelo) => modelo.principal)
        .sort((a, b) => a.orden - b.orden),
    [catalogo.modelos],
  );
  const secundarios = useMemo(
    () =>
      catalogo.modelos
        .filter((modelo) => !modelo.principal)
        .sort((a, b) => a.orden - b.orden),
    [catalogo.modelos],
  );

  const activo = model ? (catalogo.modelos.find((modelo) => modelo.id === model) ?? null) : null;
  const esfuerzoVisible = activo?.soporta_esfuerzo ?? false;
  const esfuerzoActual = effort || catalogo.esfuerzo_default;
  const nombrePorDefecto =
    catalogo.modelos.find((modelo) => modelo.id === catalogo.default)?.nombre ?? "el más liviano";
  const nombreConVision = modeloConVisionPorDefecto(catalogo)?.nombre ?? nombrePorDefecto;

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const titulo =
    vista === "esfuerzo" ? "Esfuerzo" : vista === "mas" ? "Más modelos" : "Seleccionar modelo";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-end justify-center bg-slate-900/50 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      role="presentation"
      onMouseDown={onClose}
    >
      <section
        className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl border border-slate-200 bg-white p-4 shadow-xl thin-scrollbar sm:max-w-md sm:rounded-2xl dark:border-slate-800 dark:bg-slate-900"
        role="dialog"
        aria-modal="true"
        aria-labelledby="selector-modelo-titulo"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="mb-3 flex items-center gap-2">
          {vista !== "modelos" && (
            <button
              type="button"
              onClick={() => setVista("modelos")}
              className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 dark:text-slate-400 dark:hover:bg-slate-800"
              aria-label="Volver a los modelos"
            >
              <ChevronLeftIcon className="h-4 w-4" />
            </button>
          )}
          <h2
            id="selector-modelo-titulo"
            className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-900 dark:text-slate-100"
          >
            {titulo}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 dark:text-slate-400 dark:hover:bg-slate-800"
            aria-label="Cerrar"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </header>

        {vista === "modelos" && (
          <>
            <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
              {principales.map((modelo) => (
                <FilaModelo
                  key={modelo.id}
                  modelo={modelo}
                  activo={modelo.id === model}
                  onClick={() => {
                    onSelect(modelo.id, effort);
                    onClose();
                  }}
                />
              ))}
              {/* Sin esta fila, elegir un modelo sería un viaje sin regreso: el
                  estado inicial de toda conversación es automático. */}
              <FilaBase
                titulo="Automático"
                detalle={`Deja que Edecán elija (hoy: ${nombrePorDefecto})`}
                activo={model === null}
                onClick={() => {
                  onSelect(null, effort);
                  onClose();
                }}
              />
            </div>

            <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
              {/* La fila solo existe si el modelo activo de verdad usa el nivel
                  (los que no razonan corren con el presupuesto fijo, así que un
                  control ahí no cambiaría nada). */}
              {esfuerzoVisible && (
                <FilaBase
                  titulo="Esfuerzo"
                  valor={etiquetaEsfuerzo(esfuerzoActual)}
                  chevron
                  onClick={() => setVista("esfuerzo")}
                />
              )}
              {/* Si el activo vive detrás de esta fila, su nombre va aquí: sin
                  eso la portada no muestra ningún check y parece que no hay
                  nada elegido. */}
              <FilaBase
                titulo="Más modelos"
                valor={activo && !activo.principal ? activo.nombre : undefined}
                chevron
                onClick={() => setVista("mas")}
              />
            </div>
          </>
        )}

        {vista === "esfuerzo" && (
          <>
            <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
              {catalogo.esfuerzos.map((nivel) => (
                <FilaBase
                  key={nivel}
                  titulo={etiquetaEsfuerzo(nivel)}
                  activo={nivel === esfuerzoActual}
                  onClick={() => {
                    onSelect(model, nivel);
                    setVista("modelos");
                  }}
                />
              ))}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
              Cuánto puede pensar y escribir el modelo en cada paso de un turno. Más esfuerzo =
              respuestas más trabajadas y más lentas.
            </p>
          </>
        )}

        {vista === "mas" && (
          <>
            <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
              {secundarios.map((modelo) => (
                <FilaModelo
                  key={modelo.id}
                  modelo={modelo}
                  activo={modelo.id === model}
                  onClick={() => {
                    onSelect(modelo.id, effort);
                    onClose();
                  }}
                />
              ))}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
              Los marcados como ciegos no leen capturas ni fotos: si tu mensaje lleva una imagen,
              ese turno lo atiende {nombreConVision} y tu selección no se pierde.
            </p>
          </>
        )}
      </section>
    </div>
  );
}

/** La ceguera se anuncia con la etiqueta, que sale del flag `ve_imagenes` y no
 * de la prosa del YAML. Si la descripción ya la repite al final, se recorta:
 * decirlo dos veces en la misma fila se lee como un error. */
function detalleSinRepetirCeguera(descripcion: string): string {
  return descripcion.replace(/\s*·?\s*no ve im[áa]genes\.?\s*$/i, "");
}

/** Fila de un modelo del catálogo: nombre, su línea de descripción y la
 * etiqueta de ceguera cuando aplica (el dueño manda capturas todo el día, así
 * que esto tiene que verse antes de elegir, no después). */
function FilaModelo({
  modelo,
  activo,
  onClick,
}: {
  modelo: ChatModelInfo;
  activo: boolean;
  onClick: () => void;
}) {
  return (
    <FilaBase
      titulo={modelo.nombre}
      detalle={
        modelo.ve_imagenes ? modelo.descripcion : detalleSinRepetirCeguera(modelo.descripcion)
      }
      etiqueta={modelo.ve_imagenes ? null : "No ve imágenes"}
      activo={activo}
      onClick={onClick}
    />
  );
}

function FilaBase({
  titulo,
  detalle,
  etiqueta,
  valor,
  activo = false,
  chevron = false,
  onClick,
}: {
  titulo: string;
  detalle?: string;
  etiqueta?: string | null;
  valor?: string;
  activo?: boolean;
  chevron?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={activo ? "true" : undefined}
      className="flex w-full items-center gap-3 border-b border-slate-100 px-3 py-2.5 text-left transition-colors last:border-b-0 hover:bg-slate-50 focus-visible:outline focus-visible:-outline-offset-2 focus-visible:outline-2 focus-visible:outline-brand-500 dark:border-slate-800 dark:hover:bg-slate-800/60"
    >
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{titulo}</span>
          {etiqueta && (
            <span className="rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
              {etiqueta}
            </span>
          )}
        </span>
        {detalle && (
          <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">{detalle}</span>
        )}
      </span>
      {valor && <span className="shrink-0 text-xs text-slate-500 dark:text-slate-400">{valor}</span>}
      {activo && <CheckIcon className="h-4 w-4 shrink-0 text-brand-600 dark:text-brand-400" />}
      {chevron && <ChevronRightIcon className="h-4 w-4 shrink-0 text-slate-400" />}
    </button>
  );
}
