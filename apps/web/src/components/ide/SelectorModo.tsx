"use client";

/**
 * Selector de modo + control de esfuerzo, en la fila de controles del
 * compositor (`estado/Reposo.tsx::Composer`, junto al selector de modelo).
 *
 * ACTUALIZADO tras comprobar contra `ide_runtime.py`/`ide_modos.py`/
 * `ide_opencode_permisos.py` (companion), no contra suposiciones: los cuatro
 * modos (Manual / Aceptar ediciones / Plan / Auto -- `ide_modos.ModoAgente`)
 * SÍ tienen respaldo real en el motor. `ide_workers_agent.WorkersIDEAgent._execute`
 * gatea CADA herramienta nativa con `decidir(modo, clase)` antes de tocar el
 * workspace, y bajo opencode (motor por defecto) `SessionManager.set_modo_agente`
 * además cambia el agente EN VIVO ("plan", con `edit: deny` real en opencode,
 * o "build") a través de `PuenteDePermisos`. La vieja nota de este docstring
 * ("el motor todavía no sabe aplicar esto") ya no es cierta y por eso se borra
 * -- dejarla habría sido la mentira contraria: ocultar una capacidad que sí
 * existe.
 *
 * CORREGIDO: `apps/api/edecan_api/routers/ide.py::ModoIn` YA declara `modo`
 * (`Literal["manual","aceptar_ediciones","plan","auto"] | None`) y
 * `PUT .../modo` lo reenvía tal cual a `ide_modo_set` -> `SessionManager
 * .set_modo_agente`. La nota anterior de este docstring ("ModoIn no acepta
 * el campo modo") describía un estado del repo que ya cambió -- por eso el
 * menú de abajo vuelve a tener los cuatro botones clicables, con
 * `elegirModo` llamando a `putIdeModo(sessionId, { modo })` de verdad, en
 * vez de un candado fijo con una explicación que ya era falsa.
 *
 * La LECTURA sí es honesta hoy: `GET .../modo` ya manda `modo`/`modo_motivo`
 * sin pasar por `ModoIn` (la respuesta del companion se reenvía tal cual,
 * `-> dict[str, Any]` sin schema de salida) -- por eso el botón puede mostrar
 * el modo VIGENTE de verdad, no un estado inventado ni el viejo
 * `modo_plan` (que sigue viajando en la respuesta pero
 * `ModoPlanificacionStore.verificar_accion_permitida` no lo llama ningún
 * despachador -- verificado, cero invocaciones en todo `apps/companion` --
 * así que activarlo no frena nada: no es la fuente de verdad de qué modo
 * rige la sesión).
 *
 * Sin sesión (`sessionId` nulo -- Estado 1 antes de la primera vuelta) el
 * esfuerzo SÍ se puede elegir: se guarda como preferencia local y
 * `estado-ide.ts` la aplica en cuanto nace la sesión. Deshabilitarlo aquí lo
 * volvía inútil justo cuando más se quiere usar -- antes de mandar el primer
 * mensaje. Lo que NO se hace es fingir un
 * valor por defecto que nadie guardó.
 *
 * Con sesión ya en marcha (`isLive(estado.agent)`, Estado 2 -- el agente
 * trabajando) el esfuerzo TAMBIÉN se puede elegir, sin esperar a que termine
 * la vuelta: "uno puede cambiar el effort siempre que se quiera o se pueda ...
 * no simplemente una vez al día ni una vez en la conversación" (encargo del
 * dueño). `deshabilitado` de acá abajo solo mira si hay `sessionId` -- nunca
 * si el agente está ocupado -- así que el control queda vivo durante toda la
 * ejecución. El companion ya lee el nivel vigente en cada `CompletionRequest`
 * (`ide_workers_agent.py`), pero la petición EN CURSO ya salió con el nivel
 * anterior: por eso `ocupado` (prop que manda `Composer` con
 * `isLive(estado.agent)`) dispara un aviso corto -- "aplica en la próxima
 * vuelta" -- para no dar a entender que el cambio alcanzó al turno que ya
 * está en el aire.
 */

import { useEffect, useRef, useState, type SVGProps } from "react";

import { ChevronDownIcon } from "@/components/icons";
import {
  ESFUERZO_ETIQUETA,
  ESFUERZO_NIVELES,
  getIdeModo,
  MODOS_AGENTE,
  nivelesValidos,
  putIdeModo,
  type IdeEsfuerzo,
  type IdeModo,
  type IdeModoAgente,
} from "@/lib/api-modo";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/** No hay `InfoIcon` en `components/icons.tsx` -- se define acá mismo, mismo
 * lenguaje visual que el resto (trazo 1.75, `currentColor`), igual que
 * `MemoryKnowledgePanel.tsx::GlobeIcon`. */
function InfoIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.5M12 8v.01" />
    </svg>
  );
}

interface ModoOpcion {
  id: IdeModoAgente;
  numero: string;
  nombre: string;
  descripcion: string;
}

/**
 * Los cuatro, con la descripción que de verdad aplica cada uno --
 * espejo literal de `_TABLA_DECISION` en `ide_modos.py`/
 * `ide_opencode_permisos.py` (LECTURA siempre permite en los cuatro; lo que
 * cambia es EDICION/COMANDO/PELIGROSA). Ninguno lleva ya `disponible`: los
 * cuatro son reales en el motor por igual -- lo que falta es el mismo hueco
 * para los cuatro (`ModoIn` sin campo `modo`, ver el docstring del módulo),
 * así que la selección se deshabilita pareja más abajo, no opción por
 * opción.
 */
const MODOS: ModoOpcion[] = [
  { id: "manual", numero: "1", nombre: "Manual", descripcion: "nada se ejecuta sin que lo apruebes" },
  {
    id: "aceptar_ediciones",
    numero: "2",
    nombre: "Aceptar ediciones",
    descripcion: "las ediciones de archivo van solas; los comandos siguen pidiendo tu aprobación",
  },
  { id: "plan", numero: "3", nombre: "Plan", descripcion: "no toca archivos ni corre comandos: solo lee y propone" },
  { id: "auto", numero: "4", nombre: "Auto", descripcion: "trabaja solo; solo pide permiso en lo peligroso o irreversible" },
];

const NOMBRE_DE_MODO: Record<IdeModoAgente, string> = {
  manual: "Manual",
  aceptar_ediciones: "Aceptar ediciones",
  plan: "Plan",
  auto: "Auto",
};

export interface SelectorModoProps {
  /** Sesión de agente activa, o `null`/`undefined` si todavía no arrancó ninguna. */
  sessionId: string | null | undefined;
  /** `isLive(estado.agent)` de quien llama: el agente sigue trabajando la
   * vuelta actual. NO deshabilita el control (el encargo es justo lo
   * contrario) -- solo decide si un cambio avisa que aplica en la próxima
   * vuelta en vez de en la que ya está en curso. */
  ocupado?: boolean;
  className?: string;
}

/** Clave de la preferencia de esfuerzo mientras todavía no hay sesión.
 *
 * El esfuerzo se elige ANTES de mandar el primer mensaje —es cuando de verdad
 * se quiere decidir si esta tarea va rápida o pensada— pero en ese momento no
 * existe `session_id` contra el que guardarlo. Se recuerda aquí y
 * `estado-ide.ts` lo aplica en cuanto la sesión nace. */
export const CLAVE_ESFUERZO_PREFERIDO = "forge.esfuerzo";

/** Lee la preferencia guardada. `null` si no hay o si no es un nivel válido. */
export function leerEsfuerzoPreferido(): IdeEsfuerzo | null {
  if (typeof window === "undefined") return null;
  const valor = window.localStorage.getItem(CLAVE_ESFUERZO_PREFERIDO);
  return valor === "bajo" || valor === "medio" || valor === "alto" ? valor : null;
}

function guardarEsfuerzoPreferido(nivel: IdeEsfuerzo): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(CLAVE_ESFUERZO_PREFERIDO, nivel);
}

export function SelectorModo({ sessionId, ocupado = false, className }: SelectorModoProps) {
  const [modo, setModo] = useState<IdeModo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Aparte de `saving` (esfuerzo): fijar el modo es una llamada distinta al
  // mismo endpoint, con su propio candado para no mandar dos PUT pisándose
  // si la persona hace doble clic en dos modos seguidos.
  const [savingModo, setSavingModo] = useState(false);
  const [menuAbierto, setMenuAbierto] = useState(false);
  const [ayudaAbierta, setAyudaAbierta] = useState(false);
  // Se enciende cuando un cambio de esfuerzo se manda MIENTRAS el agente
  // trabaja: la petición en curso ya salió con el nivel anterior, así que el
  // cambio aplica en la próxima vuelta, no en esta. Se apaga solo cuando la
  // vuelta actual termina (`ocupado` pasa a `false`) -- para entonces ya no
  // queda ninguna llamada al modelo con el nivel viejo en el aire.
  const [avisoProximaVuelta, setAvisoProximaVuelta] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ocupado) setAvisoProximaVuelta(false);
  }, [ocupado]);

  useEffect(() => {
    setAvisoProximaVuelta(false);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      // Sin sesión se muestra la preferencia local: el control sigue vivo y la
      // elección se aplica en cuanto arranque el primer turno.
      setModo({
        modo_plan: false,
        // "alto" replica el default real del backend (NIVEL_POR_DEFECTO en
        // ide_modos.py): antes del EsfuerzoStore, toda sesión corría fija en
        // reasoning_effort="high". Si esto no coincide con el backend, la UI
        // muestra un nivel que no es el que de verdad va a usar el primer turno.
        esfuerzo: leerEsfuerzoPreferido() ?? "alto",
        reasoning_effort: "",
        niveles: ["bajo", "medio", "alto"],
        // "manual" replica `ide_modos.MODO_POR_DEFECTO`. (Bajo opencode,
        // `_turno_opencode` sube esto a "auto" SOLO en el primerísimo turno
        // si nadie lo tocó antes -- salvaguarda para no colgarse pidiendo un
        // permiso que todavía no tiene dónde mostrarse; no es una preferencia
        // que la persona haya guardado, así que no se simula acá.)
        modo: "manual",
        modo_motivo: null,
        modos_disponibles: [...MODOS_AGENTE],
      });
      setError(null);
      return;
    }
    let cancelled = false;
    setError(null);
    getIdeModo(sessionId)
      .then((data) => {
        if (!cancelled) setModo(data);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err, "No se pudo leer el modo de este agente."));
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!menuAbierto) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setMenuAbierto(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuAbierto(false);
      }
      // Sin atajo de número todavía: los cuatro modos ya se pueden FIJAR con
      // clic (ver `elegirModo`), pero un atajo de teclado es una superficie
      // aparte (conflictos con atajos del editor/composer) que no pidió el
      // encargo que arregló el candado -- se deja fuera a propósito, no por
      // el mismo hueco de antes.
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuAbierto]);

  async function elegirEsfuerzo(nivel: IdeEsfuerzo) {
    if (!modo || saving || nivel === modo.esfuerzo) return;
    if (!sessionId) {
      // Todavía no hay dónde persistirlo en el companion: se recuerda local y
      // `estado-ide.ts` lo manda al crear la sesión.
      guardarEsfuerzoPreferido(nivel);
      setModo((prev) => (prev ? { ...prev, esfuerzo: nivel } : prev));
      return;
    }
    const previo = modo.esfuerzo;
    const enVueloAlPedir = ocupado; // el valor cambia mientras la petición viaja; se congela acá
    setSaving(true);
    setError(null);
    setAvisoProximaVuelta(false);
    setModo((prev) => (prev ? { ...prev, esfuerzo: nivel } : prev));
    try {
      const actualizado = await putIdeModo(sessionId, { esfuerzo: nivel });
      setModo(actualizado);
      guardarEsfuerzoPreferido(nivel); // la próxima sesión arranca donde la dejaste
      // El backend ya guardó el nivel nuevo, pero la vuelta que está en curso
      // salió con el anterior -- avisar, no fingir que ya cambió esta.
      if (enVueloAlPedir) setAvisoProximaVuelta(true);
    } catch (err) {
      setModo((prev) => (prev ? { ...prev, esfuerzo: previo } : prev));
      setError(errorMessage(err, "No se pudo cambiar el esfuerzo."));
    } finally {
      setSaving(false);
    }
  }

  /**
   * Fija el modo real de la sesión (`ModoIn.modo`, ver el docstring del
   * módulo). A diferencia del esfuerzo, esto SÍ exige `sessionId`: no hay
   * preferencia local que aplicar al arrancar -- `ide_modo_set` necesita una
   * sesión de agente real contra la que operar (`_required_text(params,
   * "session_id")` en `ide_runtime.py`), así que el menú se queda
   * deshabilitado hasta que exista una.
   */
  async function elegirModo(nuevo: IdeModoAgente) {
    if (!sessionId || !modo || savingModo || nuevo === modo.modo) {
      setMenuAbierto(false);
      return;
    }
    const previo = modo.modo;
    setSavingModo(true);
    setError(null);
    setModo((prev) => (prev ? { ...prev, modo: nuevo } : prev));
    setMenuAbierto(false);
    try {
      const actualizado = await putIdeModo(sessionId, { modo: nuevo });
      setModo(actualizado);
    } catch (err) {
      setModo((prev) => (prev ? { ...prev, modo: previo } : prev));
      setError(errorMessage(err, "No se pudo cambiar el modo."));
    } finally {
      setSavingModo(false);
    }
  }

  const deshabilitado = !sessionId;
  const niveles = nivelesValidos(modo?.niveles);
  // El modo VIGENTE de verdad -- viene de `GET .../modo` (campo `modo`, real,
  // no de `modo_plan`, que sigue viajando pero no lo lee ningún despachador).
  const modoVigente = modo?.modo ?? "manual";

  return (
    <div ref={containerRef} className={cx("flex items-center gap-1.5", className)}>
      <div className="relative">
        <button
          type="button"
          disabled={deshabilitado}
          onClick={() => setMenuAbierto((value) => !value)}
          className={cx(
            "flex h-8 items-center gap-1 rounded-md px-2 text-[11px] font-semibold disabled:cursor-not-allowed disabled:opacity-40",
            modoVigente === "manual"
              ? "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
              : "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300",
          )}
          aria-haspopup="menu"
          aria-expanded={menuAbierto}
          aria-label="Modo de trabajo del agente"
          title={deshabilitado ? "Disponible en cuanto arranque una sesión con el agente." : undefined}
        >
          {sessionId ? NOMBRE_DE_MODO[modoVigente] : "Modo"}
          <ChevronDownIcon className="h-3 w-3" />
        </button>
        {menuAbierto && (
          <div
            role="menu"
            className="absolute bottom-full left-0 z-40 mb-2 w-72 rounded-lg border border-forja-borde bg-forja-superficie p-1.5 shadow-lg dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada"
          >
            <p className="px-2 pb-1.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
              Modo vigente: {NOMBRE_DE_MODO[modoVigente]}
            </p>
            {MODOS.map((opcion) => {
              const activo = opcion.id === modoVigente;
              return (
                <button
                  key={opcion.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={activo}
                  // Los cuatro son reales en el motor Y ya se pueden FIJAR
                  // desde acá: `ModoIn.modo` existe en `routers/ide.py` y
                  // `elegirModo` lo manda vía `putIdeModo`. `savingModo` frena
                  // mientras un cambio está en vuelo (evita mandar dos PUT
                  // pisándose); sin `sessionId` no hay sesión real contra la
                  // que fijar el modo (ver el docstring de `elegirModo`).
                  disabled={deshabilitado || savingModo}
                  onClick={() => void elegirModo(opcion.id)}
                  title={deshabilitado ? "Disponible en cuanto arranque una sesión con el agente." : undefined}
                  className={cx(
                    "flex w-full items-start justify-between gap-2 rounded-md px-2 py-1.5 text-left disabled:cursor-not-allowed disabled:opacity-60",
                    activo ? "bg-indigo-50 dark:bg-indigo-950/30" : "",
                  )}
                >
                  <span className="min-w-0">
                    <span
                      className={cx(
                        "block text-xs font-semibold",
                        activo ? "text-indigo-700 dark:text-indigo-300" : "text-slate-700 dark:text-slate-200",
                      )}
                    >
                      {opcion.nombre}
                      {activo ? " · vigente" : ""}
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-4 text-slate-400 dark:text-slate-500">
                      {opcion.descripcion}
                    </span>
                  </span>
                  <span className="shrink-0 rounded border border-forja-borde px-1.5 py-0.5 font-mono text-[10px] text-slate-400 dark:border-forja-borde-oscuro dark:text-slate-500">
                    {opcion.numero}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <span className="h-4 w-px bg-forja-borde dark:bg-forja-borde-oscuro" aria-hidden="true" />

      {/* Sin `hidden ... sm:flex`: ese breakpoint escondía el grupo ENTERO por
          debajo de 640px de ventana, justo lo contrario de "que el nivel
          activo se lea de un vistazo ... y sobreviva a hacer la ventana
          pequeña". La fila exterior (`Reposo.tsx::Composer`) ya trae
          `flex-wrap` + `min-w-0`: si no cabe, este grupo cae a su propia
          línea -- no desaparece. */}
      <div className="flex flex-wrap items-center gap-1">
        {/* Entre `sm` y `md` el control se vuelve compacto (encargo: "puede
            pasar a ... un control compacto"): se quedan los cuatro botones
            y el icono de ayuda, pero los rótulos descriptivos "más
            rápido"/"más inteligente" ceden primero porque son los que menos
            aportan cuando ya no hay espacio. */}
        <span className="hidden text-[10px] text-slate-400 dark:text-slate-500 md:inline">más rápido</span>
        <div className="flex overflow-hidden rounded-md border border-forja-borde dark:border-forja-borde-oscuro">
          {ESFUERZO_NIVELES.map((nivel) => (
            <button
              key={nivel}
              type="button"
              // SIN `deshabilitado`: el esfuerzo se elige SIEMPRE, con sesión o
              // sin ella. Este candado es el bug que el dueño reportó dos veces
              // ("Bajo Medio y alto no sirve", "los 3 están deshabilitados"):
              // `elegirEsfuerzo` ya resuelve el caso sin sesión guardando la
              // preferencia local, así que atarlo a `sessionId` deshabilitaba el
              // control justo en reposo -- el momento en que uno decide si la
              // tarea va rápida o pensada. `saving` sí frena: evita mandar dos
              // cambios pisándose.
              disabled={saving || !niveles.includes(nivel)}
              onClick={() => void elegirEsfuerzo(nivel)}
              aria-pressed={modo?.esfuerzo === nivel}
              title={deshabilitado ? "Se aplicará en cuanto arranque la sesión." : undefined}
              className={cx(
                "px-2 py-1 text-[11px] font-semibold disabled:cursor-not-allowed disabled:opacity-30",
                modo?.esfuerzo === nivel
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "bg-forja-superficie text-slate-500 hover:bg-forja-superficie-elevada dark:bg-forja-superficie-oscura-elevada dark:text-slate-400 dark:hover:bg-slate-800",
              )}
            >
              {ESFUERZO_ETIQUETA[nivel]}
            </button>
          ))}
        </div>
        <span className="hidden text-[10px] text-slate-400 dark:text-slate-500 md:inline">más inteligente</span>

        <div className="relative">
          <button
            type="button"
            onClick={() => setAyudaAbierta((value) => !value)}
            aria-label="Por qué importa el esfuerzo"
            aria-expanded={ayudaAbierta}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:text-slate-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
          >
            <InfoIcon className="h-3.5 w-3.5" />
          </button>
          {ayudaAbierta && (
            <div className="absolute bottom-full right-0 z-40 mb-2 w-64 rounded-lg border border-forja-borde bg-forja-superficie p-2.5 text-[11px] leading-5 text-slate-500 shadow-lg dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada dark:text-slate-400">
              Subir el nivel hace que el modelo que elegiste (selector de modelo, aparte) piense más -- no lo cambia
              por otro. Pensar más tarda y cuesta más, con el mismo modelo.
            </div>
          )}
        </div>

        {/* Verdad del backend, no cosmética: la vuelta en curso ya salió con
            el nivel anterior -- este cambio entra recién en la próxima. Sin
            esta línea el dueño cree que no le hizo caso (encargo literal). */}
        {avisoProximaVuelta && (
          <span
            role="status"
            className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-950/30 dark:text-amber-400"
          >
            Aplica en la próxima vuelta
          </span>
        )}
      </div>

      {error && (
        <span className="hidden max-w-[16rem] truncate text-[10px] text-red-600 dark:text-red-400 lg:inline" title={error}>
          {error}
        </span>
      )}
    </div>
  );
}

export default SelectorModo;
