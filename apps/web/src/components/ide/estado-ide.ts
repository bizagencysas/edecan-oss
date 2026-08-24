"use client";

/**
 * Estado y lógica compartida de Forge Studio (el IDE de agentes de Edecán).
 *
 * Por qué vive en un solo hook: `app/(app)/app/ide/page.tsx` era un componente
 * de 2.981 líneas porque TODO -- estado, efectos, llamadas al companion,
 * cálculo de las filas de la torre de control -- estaba en un único cuerpo de
 * función. Los tres estados de §3.1 de la especificación (Reposo/Trabajando/
 * Editor) son vistas del MISMO trabajo, no pantallas independientes, así que
 * comparten prácticamente todo este estado. Partirlo en un hook (`useIdeEstado`)
 * en vez de en contexto de React evita el ceremonial de un Provider para un
 * árbol que de todas formas cuelga entero de `page.tsx`.
 *
 * Quien use este hook recibe un objeto con TODO lo que necesita: estado,
 * setters y funciones ya conectadas al companion. Los componentes de
 * `components/ide/estado/*` no llaman a `fetch` ni a `api-ide.ts` directo:
 * reciben `estado: IdeEstado` y pintan.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";

import type { AgentRunSummary } from "@/components/ide/AgentActivityCenter";
import type { AgentThreadTurn } from "@/components/ide/AgentThread";
import type { IdeConversationSummary, IdeWorkspaceOption } from "@/components/ide/ProjectSidebar";
import { leerEsfuerzoPreferido } from "@/components/ide/SelectorModo";
import { putIdeModo } from "@/lib/api-modo";
import {
  activateIdeWorkspace,
  cancelIdeAgent,
  confirmIdeAgentMcp,
  createIdeAgent,
  createIdeConversation,
  createIdeProject,
  createIdeTerminal,
  createIdeWorkspace,
  cloneIdeWorkspace,
  deleteIdeConversation,
  deleteIdeProject,
  executeIdeCommand,
  getIdeAgentCost,
  getIdeAgentDiff,
  getIdeAgents,
  getIdeCommands,
  getIdeConversations,
  getIdeGitStatus,
  getIdeModels,
  getIdeProjects,
  getIdeReferences,
  getIdeStatus,
  getIdeTerminals,
  getIdeWorkspaceFile,
  getIdeWorkspaces,
  getIdeWorkspaceTree,
  pickIdeWorkspace,
  rejectIdeAgentDiffFile,
  renameIdeConversation,
  renameIdeProject,
  type IdeAgentAttachment,
  type IdeAgentCost,
  type IdeAgentDiff,
  type IdeCommandResult,
  type IdeCommandSpec,
  type IdeConversation,
  type IdeGitStatus,
  type IdeModelOption,
  type IdeProject,
  type IdeReferenceMatch,
  type IdeSession,
  type IdeSessionEvent,
  type IdeTreeNode,
  type IdeWorkspace,
  putIdeWorkspaceFile,
  readIdeAgent,
  readIdeTerminal,
  sendIdeTerminalInput,
  setIdeAgentModel,
} from "@/lib/api-ide";
import {
  conEventos,
  conFallo,
  conRespuestaDeEnvio,
  contarEnEspera,
  mensajesDeConversacion,
  normalizarRespuestaDeEnvio,
  nuevoIdLocal,
  nuevoMensajeLocal,
  sinFichasDeSesionesTerminadas,
  sinResueltosViejos,
  type IdeMensajeEnCola,
} from "@/lib/ide-cola";
import { isTauriApp, tauriInvoke } from "@/lib/tauriListen";

/** Qué se pinta: los tres estados de §3.1, elegidos por el estado del
 * trabajo (hay archivo abierto / hay conversación / ninguno) y no por una
 * pestaña que la persona elija a mano. */
export type IdeVista = "reposo" | "trabajando" | "editor";

export type AddAction = "folder" | "clone" | "image" | null;

export const FALLBACK_IDE_MODELS: IdeModelOption[] = [
  {
    id: "@cf/zai-org/glm-5.2",
    nombre: "GLM 5.2",
    descripcion: "Modelo principal para ingeniería: contexto largo, herramientas y buen balance.",
  },
  {
    id: "@cf/moonshotai/kimi-k2.7-code",
    nombre: "Kimi K2.7 Code",
    descripcion: "Especialista de código para refactors grandes y debugging.",
  },
  {
    id: "@cf/nvidia/nemotron-3-120b-a12b",
    nombre: "Nemotron 3 120B",
    descripcion: "Modelo grande para análisis pesado y arquitectura.",
  },
  {
    id: "@cf/openai/gpt-oss-120b",
    nombre: "GPT-OSS 120B",
    descripcion: "Modelo abierto grande para análisis general y producto.",
  },
  {
    id: "@cf/meta/llama-4-scout-17b-16e-instruct",
    nombre: "Llama 4 Scout",
    descripcion: "Modelo multimodal para visión y revisión de interfaces.",
  },
];

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

// 2.1 del plan de paridad: imágenes en el compositor por selector de
// archivo, pegar (portapapeles) o arrastrar -- las tres rutas terminan acá,
// para no repetir la validación (tipo aceptado, tope de tamaño) tres veces.
// La validación REAL (firma binaria, decodificación) es responsabilidad del
// companion (`ide_imagenes.validar_y_normalizar_imagen`); esto es solo un
// filtro barato en el navegador para no ni intentar subir algo que ya se ve
// mal desde el cliente.
export const ACCEPTED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"] as const;
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

function readFileAsDataUrl(file: File): Promise<string | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve(null);
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : null);
    reader.readAsDataURL(file);
  });
}

export async function fileToImageAttachment(
  file: File,
): Promise<{ attachment: IdeAgentAttachment } | { error: string }> {
  if (!ACCEPTED_IMAGE_TYPES.includes(file.type as (typeof ACCEPTED_IMAGE_TYPES)[number])) {
    return { error: "Ese formato de imagen no es compatible (usa PNG, JPEG, WebP o GIF)." };
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return { error: "La imagen supera el límite de 10 MB." };
  }
  const value = await readFileAsDataUrl(file);
  const data = value?.includes(",") ? value.slice(value.indexOf(",") + 1) : "";
  if (!data) {
    return { error: "No se pudo leer la imagen." };
  }
  return {
    attachment: {
      name: file.name,
      media_type: file.type as IdeAgentAttachment["media_type"],
      data,
    },
  };
}

export function isLive(session: IdeSession | null | undefined): boolean {
  return Boolean(
    session &&
      !session.ended_at &&
      !["completed", "failed", "closed", "cancelled", "interrupted"].includes(session.status),
  );
}

export function relativePath(parent: string, name: string): string {
  return parent && parent !== "." ? `${parent}/${name}` : name;
}

/**
 * Referencia sintética "@Web" (1.1 del plan de paridad): a diferencia de
 * archivo/carpeta/símbolo, no la resuelve `ide_referencias.ReferenceService`
 * -- el agente ya tiene la herramienta `buscar_web` y entiende la mención en
 * lenguaje natural, así que esto es solo la entrada del menú, sin ida y
 * vuelta al companion.
 */
const WEB_MENTION: IdeReferenceMatch = {
  type: "symbol",
  path: "Web",
  name: "Web",
  symbol_kind: null,
  line: null,
};

/**
 * Busca si el cursor está dentro de una mención "@" sin cerrar: recorre
 * hacia atrás desde `caret` hasta un espacio/salto de línea (mención
 * abandonada) o un "@" precedido por inicio de texto o espacio (mención
 * real). Devuelve `null` sin abrir el menú en cualquier otro caso -- p. ej.
 * un correo o un usuario de git escrito a mano no debe disparar el buscador.
 */
function findMentionTrigger(text: string, caret: number): { start: number; query: string } | null {
  let index = caret - 1;
  while (index >= 0) {
    const char = text[index];
    if (char === "@") {
      const before = index === 0 ? " " : text[index - 1];
      if (/\s/.test(before)) {
        return { start: index, query: text.slice(index + 1, caret) };
      }
      return null;
    }
    if (/\s/.test(char)) return null;
    index -= 1;
  }
  return null;
}

export function referenceKindLabel(match: IdeReferenceMatch): string {
  if (match.type === "symbol") return match.symbol_kind === "class" ? "Clase" : "Función";
  return match.type === "folder" ? "Carpeta" : "Archivo";
}

/**
 * Solo dispara cuando "/" está al INICIO del compositor y todavía no hay un
 * espacio después del nombre (`edecan_companion.ide_comandos.resolver_comando`
 * separa nombre de argumentos justo en el primer espacio) -- una vez que la
 * persona empieza a escribir los argumentos, el menú se cierra y solo queda
 * la ejecución vía Enter normal.
 */
function findCommandTrigger(text: string): string | null {
  const match = /^\/(\S*)$/.exec(text);
  return match ? match[1] : null;
}

export interface IdeCommandMatch {
  spec: IdeCommandSpec;
  /** El nombre/alias concreto que casó con el prefijo -- puede ser un alias
   * (p. ej. "/rc" para "remote-control"), no siempre `spec.nombre`. */
  nombre: string;
}

/**
 * Filtra el registro completo (ya traído una sola vez con `getIdeCommands`)
 * por prefijo -- espejo, en el cliente, de `ide_comandos.autocompletar`: cada
 * alias es su propia entrada del menú, no solo el nombre canónico.
 */
function filterIdeCommands(comandos: IdeCommandSpec[], prefijo: string): IdeCommandMatch[] {
  const limpio = prefijo.toLowerCase();
  const filas: IdeCommandMatch[] = [];
  for (const spec of comandos) {
    for (const nombre of spec.nombres) {
      if (nombre.toLowerCase().startsWith(limpio)) filas.push({ spec, nombre });
    }
  }
  filas.sort((a, b) => a.nombre.localeCompare(b.nombre));
  return filas;
}

function mergeIdeEvents(events: IdeSessionEvent[]): IdeSessionEvent[] {
  const merged: IdeSessionEvent[] = [];
  for (const event of events) {
    const previous = merged.at(-1);
    if (
      previous &&
      previous.type === event.type &&
      previous.stream === event.stream &&
      ["assistant", "assistant_final", "output"].includes(event.type)
    ) {
      merged[merged.length - 1] = {
        ...previous,
        text: `${previous.text}${event.text}`,
        timestamp: event.timestamp,
      };
    } else {
      merged.push(event);
    }
  }
  return merged;
}

/**
 * Parte los eventos de UNA sesión de agente en turnos, uno por cada mensaje
 * `user` que aparece en el stream.
 *
 * Por qué hace falta: desde el fix de continuidad de `ide_sessions.py`
 * (`SessionManager.start_agent` / `_find_reusable_agent_session`), una
 * sesión ya NO es un mensaje -- es el hilo COMPLETO de una conversación, y
 * reusa el mismo `id` mientras el último turno haya cerrado limpio. El
 * `events[]` de una sesión activa puede traer varios `user` acumulados.
 */
function splitSessionIntoTurns(
  sessionId: string,
  events: IdeSessionEvent[],
  live: boolean,
): AgentThreadTurn[] {
  if (!events.length) return [];
  const userIndexes: number[] = [];
  events.forEach((event, index) => {
    if (event.type === "user") userIndexes.push(index);
  });
  // Sesiones viejas sin ningún evento `user` reconocible (contrato previo al
  // fix): todo el stream es un único turno, igual que antes.
  const boundaries = userIndexes.length ? userIndexes : [0];
  return boundaries.map((start, index) => {
    const end = index + 1 < boundaries.length ? boundaries[index + 1] : events.length;
    return {
      id: `${sessionId}:${start}`,
      events: events.slice(start, end),
      live: live && index === boundaries.length - 1,
    };
  });
}

/** Título con el que `ide_projects.ProjectRegistry.create_conversation` arranca cuando no se pide uno explícito. */
const DEFAULT_CONVERSATION_TITLE = "Nueva conversación";

/**
 * Título de respaldo simple (primera línea del prompt) para el primer
 * mensaje de una conversación nueva. Ver la nota larga que traía esta
 * función en el `page.tsx` original: evita que toda conversación nueva quede
 * pegada en "Nueva conversación" en la barra lateral.
 */
function deriveFallbackConversationTitle(text: string): string | null {
  const firstLine = text.split("\n").find((line) => line.trim()) ?? "";
  const trimmed = firstLine.trim();
  if (!trimmed) return null;
  return trimmed.length > 60 ? `${trimmed.slice(0, 57)}…` : trimmed;
}

export function cleanTerminalOutput(events: IdeSessionEvent[]): string {
  const output = events
    .filter((event) => {
      const type = event.type.toLowerCase();
      const stream = (event.stream ?? "").toLowerCase();
      return type === "output" || type.includes("stdout") || type.includes("stderr") || stream === "stdout" || stream === "stderr";
    })
    .map((event) => event.text)
    .join("");

  return output
    .replace(/\u001B\][^\u0007]*(?:\u0007|\u001B\\)/g, "")
    .replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001B[()][0-2A-Z]/g, "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "")
    .replace(/\u0000/g, "");
}

export function useIdeEstado() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [workspaces, setWorkspaces] = useState<IdeWorkspace[]>([]);
  const [workspace, setWorkspace] = useState<IdeWorkspace | null>(null);
  const [agents, setAgents] = useState<IdeSession[]>([]);
  const [agent, setAgent] = useState<IdeSession | null>(null);
  const [agentEvents, setAgentEvents] = useState<IdeSessionEvent[]>([]);
  const [agentCursor, setAgentCursor] = useState(0);
  const [prompt, setPrompt] = useState("");
  const [ideModels, setIdeModels] = useState<IdeModelOption[]>([]);
  const [ideModel, setIdeModelInterno] = useState<string>("");
  const [agentAttachments, setAgentAttachments] = useState<IdeAgentAttachment[]>([]);
  const [imageNotice, setImageNotice] = useState<string | null>(null);

  /**
   * Encargo "Hecho 2": `SessionManager.set_modelo_agente` ya aplicaba el
   * cambio de modelo EN VIVO del lado del companion, pero sin ruta REST el
   * `<select>` de `Reposo.tsx::Composer` solo podía cambiar `ideModel` -- una
   * preferencia local que `createIdeAgent` recién manda en el PRÓXIMO
   * mensaje. Este wrapper es el cable: además de guardar la preferencia
   * (para cuando todavía no hay sesión, o el próximo turno la retoma), si
   * `agent` sigue vivo (`isLive`) empuja el cambio de inmediato vía
   * `setIdeAgentModel`. Mejor esfuerzo, mismo patrón que el esfuerzo en
   * `sendMessage` (abajo): si la llamada falla, la preferencia local ya
   * quedó puesta y el próximo turno la retoma -- no rompe el compositor por
   * un cambio de modelo que no alcanzó a aplicarse en vivo.
   *
   * El esfuerzo vigente de la sesión NO se toca acá -- `set_modelo_agente`
   * del companion lo lee y lo reafirma junto con el modelo nuevo, nunca lo
   * reinicia (encargo punto 4: "que no se pise con el esfuerzo").
   */
  const setIdeModel = useCallback(
    (model: string) => {
      setIdeModelInterno(model);
      if (model && agent && isLive(agent)) {
        void setIdeAgentModel(agent.id, model).catch(() => undefined);
      }
    },
    [agent],
  );

  // Menú de "@" (1.1 del plan de paridad): referencias resueltas por
  // `ide_referencias.ReferenceService` mientras se escribe la mención, y las
  // ya elegidas para este mensaje (fichas visibles, quitables, antes de
  // enviar -- no tocan el texto que ya se insertó en el prompt).
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionStart, setMentionStart] = useState<number | null>(null);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionMatches, setMentionMatches] = useState<IdeReferenceMatch[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [promptReferences, setPromptReferences] = useState<IdeReferenceMatch[]>([]);
  const promptFieldRef = useRef<HTMLTextAreaElement | null>(null);

  // Revisión de diffs del último turno (1.2 del plan de paridad) y su
  // contabilidad de costo (4): se piden bajo demanda, no en cada poll del
  // agente -- son consultas explícitas de la persona, no parte del stream.
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffData, setDiffData] = useState<IdeAgentDiff | null>(null);
  const [diffResolutions, setDiffResolutions] = useState<Record<string, "accepted" | "rejected">>({});
  const [diffPending, setDiffPending] = useState<Record<string, boolean>>({});
  const [costOpen, setCostOpen] = useState(false);
  const [costLoading, setCostLoading] = useState(false);
  const [costData, setCostData] = useState<IdeAgentCost | null>(null);
  const [memoryPanelOpen, setMemoryPanelOpen] = useState(false);

  // Menú de "/" (registro de `ide_comandos.py` + ejecución cableada en
  // `ide_runtime._despachar_comando`): el registro completo se trae UNA vez
  // (es chico y no cambia durante la sesión) y el filtrado por prefijo
  // mientras se escribe corre en el cliente, sin ida y vuelta al companion
  // en cada tecla -- a diferencia del menú "@", que sí depende de una
  // búsqueda en el workspace.
  const [ideCommandSpecs, setIdeCommandSpecs] = useState<IdeCommandSpec[]>([]);
  const [ideHelpText, setIdeHelpText] = useState("");
  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [commandMatches, setCommandMatches] = useState<IdeCommandMatch[]>([]);
  const [commandIndex, setCommandIndex] = useState(0);
  const [commandBusy, setCommandBusy] = useState(false);
  // Comando destructivo resuelto que espera un "sí" explícito antes de
  // correr de verdad -- ver el bullet de confirmación en el encargo.
  const [pendingCommand, setPendingCommand] = useState<{
    text: string;
    comando: string;
    mensaje: string;
  } | null>(null);
  // Última respuesta de un comando "/" (éxito, error con sugerencia, o nota
  // informativa) -- se muestra pegada al compositor, no dentro del hilo del
  // agente: un comando "/" no es un turno del agente.
  const [commandResult, setCommandResult] = useState<IdeCommandResult | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addAction, setAddAction] = useState<AddAction>(null);
  const [addMenu, setAddMenu] = useState(false);

  const [tree, setTree] = useState<IdeTreeNode[]>([]);
  const [treePath, setTreePath] = useState("");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  // Estado 3 de §3.1 (Editor): se entra desde un diff o desde el árbol, y se
  // sale con un gesto explícito -- nunca es la pantalla de inicio. No es lo
  // mismo que "hay un archivo abierto": abrir una terminal también entra al
  // Editor aunque no haya `selectedFile` (explorador visible, sin archivo).
  const [editorAbierto, setEditorAbierto] = useState(false);

  const [terminals, setTerminals] = useState<IdeSession[]>([]);
  const [terminal, setTerminal] = useState<IdeSession | null>(null);
  const [terminalEvents, setTerminalEvents] = useState<IdeSessionEvent[]>([]);
  const [terminalCursor, setTerminalCursor] = useState(0);
  const [terminalInput, setTerminalInput] = useState("");

  const [git, setGit] = useState<IdeGitStatus | null>(null);
  const [resolvedMcpCalls, setResolvedMcpCalls] = useState<Record<string, boolean>>({});

  // Proyectos + conversaciones (`ide_projects.ProjectRegistry`, ver
  // `lib/api-ide.ts`): la jerarquía que pinta `ProjectSidebar`. Se cargan
  // aparte de `workspace`/`agents` porque cruzan TODOS los workspaces del
  // dueño, no solo el activo -- una conversación puede vivir en un proyecto
  // atado a una carpeta distinta a la que se está viendo ahora mismo.
  const [projects, setProjects] = useState<IdeProject[]>([]);
  const [conversations, setConversations] = useState<IdeConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  // Cola de salida del compositor (`lib/ide-cola.ts`): escribirle al agente
  // mientras trabaja ya no es un error, así que hace falta un lugar donde
  // vivan los mensajes que todavía no recogió. Sin esto, la persona manda lo
  // mismo tres veces porque nada le dice qué pasó con lo anterior.
  const [outbox, setOutbox] = useState<IdeMensajeEnCola[]>([]);

  // Todas las sesiones de agente de TODOS los workspaces (`GET /v1/ide/agents`
  // sin filtro), no solo las de la carpeta abierta: es lo que alimenta la
  // torre de control. `agents` sigue siendo la lista del workspace activo
  // porque de ahí salen el hilo y los puntitos de la barra lateral.
  const [allAgents, setAllAgents] = useState<IdeSession[]>([]);
  const [activityOpen, setActivityOpen] = useState(true);
  const [activityLoading, setActivityLoading] = useState(true);

  const agentCursorRef = useRef(0);
  // Espejo síncrono de `activeConversationId`. Dos envíos seguidos en una
  // conversación recién creada corren antes de que React repinte, y sin este
  // espejo el segundo volvería a crear otra conversación.
  const conversationIdRef = useRef<string | null>(null);
  const creatingConversationRef = useRef<Promise<IdeConversation> | null>(null);
  const previousConversationRef = useRef<string | null>(null);
  const terminalCursorRef = useRef(0);
  const timelineEndRef = useRef<HTMLDivElement | null>(null);
  const activeAgentId = agent?.id ?? null;
  const activeTerminalId = terminal?.id ?? null;

  const loadWorkspaceData = useCallback(async (current: IdeWorkspace) => {
    const [agentRows, terminalRows, treeResult, gitResult] = await Promise.allSettled([
      getIdeAgents(current.id),
      getIdeTerminals(current.id),
      getIdeWorkspaceTree(current.id, undefined, 2, 500),
      getIdeGitStatus(current.id),
    ]);
    const nextAgents = agentRows.status === "fulfilled" ? agentRows.value : [];
    const nextTerminals = terminalRows.status === "fulfilled" ? terminalRows.value : [];
    setAgents(nextAgents);
    setTerminals(nextTerminals);
    setTree(treeResult.status === "fulfilled" ? treeResult.value.entries : []);
    setTreePath(treeResult.status === "fulfilled" ? treeResult.value.path : "");
    setGit(gitResult.status === "fulfilled" ? gitResult.value : null);
    // `agent` ya no se elige acá: lo resuelve el efecto que cruza `agents`
    // con `activeConversationId` (más abajo), porque ahora la navegación
    // primaria es "qué conversación estoy viendo", no "qué workspace".
    setTerminal((old) => {
      const current = nextTerminals.find((row) => row.id === old?.id);
      if (isLive(current)) return current ?? null;
      return nextTerminals.find((row) => isLive(row)) ?? null;
    });
  }, []);

  const bootstrap = useCallback(async () => {
    setError(null);
    try {
      const [status, rows, modelRowsRaw, projectRows, conversationRows] = await Promise.all([
        getIdeStatus(),
        getIdeWorkspaces(),
        getIdeModels().catch(() => FALLBACK_IDE_MODELS),
        // Mejor esfuerzo: sin companion emparejado esto también fallaría,
        // pero no debe tumbar el resto del arranque (workspaces/modelos sí
        // son indispensables para el resto de la pantalla).
        getIdeProjects().catch(() => []),
        getIdeConversations().catch(() => []),
      ]);
      const modelRows = modelRowsRaw.length ? modelRowsRaw : FALLBACK_IDE_MODELS;
      setConnected(status.connected);
      setWorkspaces(rows);
      setProjects(projectRows);
      setConversations(conversationRows);
      setIdeModels(modelRows);
      // Selección de arranque, no un cambio de la persona: usa el setter
      // interno (nunca hay sesión viva todavía en este punto del arranque)
      // para no disparar `setIdeAgentModel` de balde.
      setIdeModelInterno((current) =>
        current && modelRows.some((row) => row.id === current)
          ? current
          : modelRows[0]?.id ?? "",
      );
      const selected = rows.find((row) => row.active) ?? rows[0] ?? null;
      setWorkspace(selected);
      if (selected) await loadWorkspaceData(selected);
    } catch (bootstrapError) {
      setConnected(false);
      setError(errorMessage(bootstrapError, "No se pudo abrir el estudio."));
    }
  }, [loadWorkspaceData]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  // Registro de comandos "/" (`ide_comandos.listar_comandos`): se trae UNA
  // sola vez -- es chico, no cambia durante la sesión, y el filtrado por
  // prefijo mientras se escribe corre después en el cliente (ver
  // `filterIdeCommands`), sin pedirlo de nuevo en cada tecla.
  useEffect(() => {
    let cancelled = false;
    getIdeCommands()
      .then((result) => {
        if (cancelled) return;
        setIdeCommandSpecs(result.comandos);
        setIdeHelpText(result.ayuda);
      })
      .catch(() => {
        // Sin companion conectado todavía: el menú "/" queda vacío hasta que
        // `bootstrap` logre conectar: no es un error que deba interrumpir la
        // carga del resto de la página.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    agentCursorRef.current = 0;
    setAgentCursor(0);
    setAgentEvents([]);
  }, [activeAgentId]);

  // Resuelve qué `IdeSession` representa la conversación activa. `agents`
  // ya viene más-reciente-primero (`SessionManager.list`, ver
  // `ide_sessions.py`), y como una conversación reusa la MISMA sesión
  // mientras el companion no se reinicie a mitad de turno, basta con tomar
  // la primera que calce por `conversation_id` -- no hace falta comparar
  // fechas de nuevo acá.
  useEffect(() => {
    if (!activeConversationId) {
      setAgent(null);
      return;
    }
    const latest = agents.find((row) => row.conversation_id === activeConversationId) ?? null;
    setAgent((old) => (old && latest && old.id === latest.id ? old : latest));
  }, [agents, activeConversationId]);

  // El espejo síncrono y el candado de creación viven juntos porque cuentan la
  // misma historia: cuál es la conversación a la que hay que mandar AHORA.
  useEffect(() => {
    conversationIdRef.current = activeConversationId;
    creatingConversationRef.current = null;
  }, [activeConversationId]);

  // Un borrador sin enviar no debe "seguir" al usuario de una conversación a
  // otra: confundiría con qué hilo va a hablar.
  useEffect(() => {
    const previous = previousConversationRef.current;
    previousConversationRef.current = activeConversationId;
    if (previous === null) return;
    setPrompt("");
    setAgentAttachments([]);
    setPromptReferences([]);
    setMentionOpen(false);
    setCommandMenuOpen(false);
    setCommandResult(null);
    setPendingCommand(null);
  }, [activeConversationId]);

  // El diff/costo abierto es del turno de OTRA conversación en cuanto cambia
  // la sesión activa: cerrarlo evita mostrar "cambios" de un hilo distinto
  // al que la persona está viendo ahora.
  useEffect(() => {
    setDiffOpen(false);
    setDiffData(null);
    setDiffResolutions({});
    setDiffPending({});
    setCostOpen(false);
    setCostData(null);
  }, [activeAgentId]);

  useEffect(() => {
    terminalCursorRef.current = 0;
    setTerminalCursor(0);
    setTerminalEvents([]);
  }, [activeTerminalId]);

  // El latido del hilo del agente ya NO depende de qué panel se esté viendo:
  // el mission control de Trabajando y el agente lateral del Editor (§3.1,
  // Estado 3: "agente a la derecha") necesitan el mismo stream en vivo.
  useEffect(() => {
    if (!activeAgentId) return;
    const agentId = activeAgentId;
    let stopped = false;
    async function poll() {
      if (stopped) return;
      try {
        const result = await readIdeAgent(agentId, agentCursorRef.current);
        if (stopped) return;
        if (result.events.length) {
          setAgentEvents((old) => [...old, ...result.events]);
          agentCursorRef.current = result.next_cursor;
          setAgentCursor(result.next_cursor);
          // El mismo sorbo de eventos que pinta el hilo es el que resuelve las
          // fichas "en cola": el evento de entrega del motor viaja por acá.
          setOutbox((cola) => conEventos(cola, agentId, result.events, Date.now()));
        }
        setOutbox((cola) => sinResueltosViejos(cola, Date.now()));
        setAgent(result.session);
        setAgents((old) => old.map((row) => (row.id === result.session.id ? result.session : row)));
      } catch (pollError) {
        if (!stopped) setError(errorMessage(pollError, "Se interrumpió la lectura del agente."));
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 1_200);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeAgentId]);

  useEffect(() => {
    if (!activeTerminalId) return;
    const terminalId = activeTerminalId;
    let stopped = false;
    async function poll() {
      if (stopped) return;
      try {
        const result = await readIdeTerminal(terminalId, terminalCursorRef.current);
        if (stopped) return;
        if (result.events.length) {
          setTerminalEvents((old) => [...old, ...result.events]);
          terminalCursorRef.current = result.next_cursor;
          setTerminalCursor(result.next_cursor);
        }
        setTerminal(result.session);
      } catch (pollError) {
        if (!stopped) setError(errorMessage(pollError, "Se interrumpió la terminal."));
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 1_200);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeTerminalId]);

  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [agentEvents.length]);

  // Latido de la torre de control: la lista completa de sesiones, de todas las
  // carpetas. Va más lento que el hilo (2.5s contra 1.2s) porque acá solo
  // importan el estado y el reloj, no el texto que va saliendo.
  useEffect(() => {
    if (!connected) return;
    let stopped = false;
    async function poll() {
      try {
        const rows = await getIdeAgents();
        if (stopped) return;
        setAllAgents(rows);
        // Red de seguridad de las fichas: a un agente de otra conversación se
        // le puede dirigir desde la torre, pero de esa sesión no se leen
        // eventos, así que su ficha nunca vería el `user_delivered` que la
        // resuelve y diría "en cola" para siempre. Cuando esa sesión ya no
        // está viva, la ficha sobra: el mensaje quedó escrito en el hilo de esa
        // conversación pase lo que pase (`_entregar_pendientes_al_cerrar`).
        const conocidas = new Set(rows.map((row) => row.id));
        const vivas = new Set(rows.filter((row) => isLive(row)).map((row) => row.id));
        setOutbox((cola) =>
          sinFichasDeSesionesTerminadas(cola, vivas, conocidas, conversationIdRef.current),
        );
      } catch {
        // Mejor esfuerzo: si el companion se cae, el panel se queda con lo
        // último que supo en vez de tumbar la pantalla entera.
      } finally {
        if (!stopped) setActivityLoading(false);
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 2_500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [connected]);

  // Las fichas ya resueltas se van solas. Este intervalo existe para las que
  // no cuelgan de la conversación abierta (mandadas desde la torre de
  // control): ahí no hay poll de eventos que las limpie de paso.
  const hayFichasResueltas = outbox.some((mensaje) => mensaje.estado === "entregado");
  useEffect(() => {
    if (!hayFichasResueltas) return;
    const timer = window.setInterval(
      () => setOutbox((cola) => sinResueltosViejos(cola, Date.now())),
      1_000,
    );
    return () => window.clearInterval(timer);
  }, [hayFichasResueltas]);

  // Autocompletado del menú "@": debounced para no pegarle al companion en
  // cada tecla (el propio `ReferenceService` ya cachea por workspace, pero
  // no hace falta ni esa ida y vuelta mientras la persona sigue tecleando).
  useEffect(() => {
    if (!mentionOpen || !workspace) {
      setMentionMatches([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const result = await getIdeReferences(workspace.id, mentionQuery, { limit: 15 });
        if (cancelled) return;
        const web =
          !mentionQuery || "web".startsWith(mentionQuery.toLowerCase())
            ? [WEB_MENTION]
            : [];
        setMentionMatches([...web, ...result.matches]);
        setMentionIndex(0);
      } catch {
        if (!cancelled) setMentionMatches(mentionQuery ? [] : [WEB_MENTION]);
      }
    }, 150);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [mentionOpen, mentionQuery, workspace]);

  // Filtrado del menú "/": instantáneo, sin red -- `ideCommandSpecs` ya vive
  // en memoria completo (ver el `useEffect` de `getIdeCommands` más arriba).
  useEffect(() => {
    if (!commandMenuOpen) {
      setCommandMatches([]);
      return;
    }
    setCommandMatches(filterIdeCommands(ideCommandSpecs, commandQuery));
    setCommandIndex(0);
  }, [commandMenuOpen, commandQuery, ideCommandSpecs]);

  /**
   * Cambia de workspace activo. Cubre dos casos porque desde el encargo "la
   * pastilla de proyecto encima del compositor" esta función ya no solo la
   * llama la barra lateral con una carpeta YA conocida: la pastilla nueva
   * embebe `ProjectPicker`, que también puede autorizar una carpeta que
   * `workspaces` todavía no trae (recién elegida en Finder). Si ya la
   * conocíamos, se activa en el companion (`activateIdeWorkspace`); si es
   * nueva, ya viene activa de fábrica -- mismo contrato que `authorizePath`/
   * `pickWorkspace` -- y solo falta que este estado se entere.
   */
  async function selectWorkspace(next: IdeWorkspace) {
    if (next.id === workspace?.id) return;
    setBusy(true);
    setError(null);
    try {
      const yaConocido = workspaces.some((row) => row.id === next.id);
      const active = yaConocido ? await activateIdeWorkspace(next.id) : next;
      setWorkspaces((rows) => {
        const base = yaConocido ? rows : [active, ...rows.filter((row) => row.id !== active.id)];
        return base.map((row) => ({ ...row, active: row.id === active.id }));
      });
      setWorkspace(active);
      setSelectedFile(null);
      await loadWorkspaceData(active);
    } catch (selectError) {
      setError(errorMessage(selectError, "No se pudo abrir el proyecto."));
    } finally {
      setBusy(false);
    }
  }

  /**
   * `ProjectPicker` puede vivir embebido en más de un sitio a la vez (la
   * pastilla de workspace y el formulario de crear proyecto de
   * `ProjectSidebar`) y cada instancia puede autorizar carpetas o refrescar
   * su lista por su cuenta. Esto le da a cualquiera de ellas una forma de
   * mantener `workspaces` -- la única fuente de verdad -- al día, vía su
   * prop `onWorkspacesChange`, sin duplicar el fetch aquí.
   */
  const syncWorkspaces = useCallback((rows: IdeWorkspace[]) => setWorkspaces(rows), []);

  /**
   * Resuelve a qué conversación va el mensaje, creándola si es el primero.
   *
   * El espejo `conversationIdRef` se lee ANTES que el estado porque dos Enter
   * seguidos ocurren dentro del mismo repintado: con el estado nada más, el
   * segundo mensaje abriría una conversación nueva y quedaría hablando solo.
   */
  async function ensureConversation(): Promise<{ id: string; conversation: IdeConversation | null }> {
    const known = conversationIdRef.current;
    if (known) {
      return { id: known, conversation: conversations.find((row) => row.id === known) ?? null };
    }
    if (!creatingConversationRef.current) {
      const creation = (async () => {
        const createdConversation = await createIdeConversation({});
        conversationIdRef.current = createdConversation.id;
        setConversations((rows) => [createdConversation, ...rows]);
        setActiveConversationId(createdConversation.id);
        return createdConversation;
      })();
      creatingConversationRef.current = creation;
      creation.catch(() => {
        creatingConversationRef.current = null;
      });
    }
    const created = await creatingConversationRef.current;
    return { id: created.id, conversation: created };
  }

  /**
   * Manda un mensaje al agente. No se bloquea porque haya un turno en curso:
   * el compositor se vacía de inmediato (se puede seguir pensando en voz
   * alta) y el destino del mensaje se sigue por su ficha: encolado → entregado.
   */
  async function submitPrompt(overrideText?: string) {
    const text = (overrideText ?? prompt).trim();
    if (!text || !workspace) return;
    // Un mensaje que empieza con "/" es un comando del menú (ver
    // `ide_comandos.py`), no un turno normal del agente -- se resuelve y
    // ejecuta aparte, nunca llega a `createIdeAgent`.
    if (text.startsWith("/")) {
      await runCommand(text);
      return;
    }
    const localId = nuevoIdLocal();
    const attachments = agentAttachments;
    // La ficha solo puede existir cuando ya se sabe a qué conversación cuelga,
    // y eso lo resuelve `ensureConversation` con una ida a la red. Si falla
    // ANTES de eso no hay ficha que marcar en rojo, así que el texto vuelve al
    // compositor: es el único lugar donde no se pierde.
    let hayFicha = false;
    // Optimista a propósito: el campo se vacía antes de que responda la red,
    // como cuando uno le escribe a alguien que está ocupado. Si el envío
    // falla, la ficha ofrece recuperar el texto.
    if (overrideText === undefined) setPrompt("");
    setAgentAttachments([]);
    setImageNotice(null);
    // Mandar un mensaje siempre vuelve a la conversación (Trabajando), aunque
    // se haya estado editando un archivo -- ver la nota de `editorAbierto`.
    setEditorAbierto(false);
    setError(null);
    try {
      const { id: conversationId, conversation } = await ensureConversation();
      setOutbox((cola) => [
        ...cola,
        nuevoMensajeLocal({
          localId,
          conversationId,
          texto: text,
          creadoEn: Date.now(),
          cursorAlEncolar: agentCursorRef.current,
        }),
      ]);
      hayFicha = true;
      const isFirstTurn = !agent;
      const created = await createIdeAgent({
        workspace_id: workspace.id,
        prompt: text,
        model: ideModel || undefined,
        conversation_id: conversationId,
        title: conversation?.title,
        attachments,
      });
      setAgents((rows) => [created, ...rows.filter((row) => row.id !== created.id)]);
      setAgent(created);
      // El esfuerzo se elige ANTES de mandar el primer mensaje, cuando todavía
      // no hay `session_id` contra el que guardarlo (ver `SelectorModo`). Aquí
      // ya existe la sesión, así que se aplica. Mejor esfuerzo: si falla, el
      // turno sigue con el nivel por defecto en vez de romperse.
      {
        const preferido = leerEsfuerzoPreferido();
        if (preferido && preferido !== "medio") {
          void putIdeModo(created.id, { esfuerzo: preferido }).catch(() => undefined);
        }
      }
      setOutbox((cola) => conRespuestaDeEnvio(cola, localId, normalizarRespuestaDeEnvio(created)));
      if (isFirstTurn && conversation && conversation.title === DEFAULT_CONVERSATION_TITLE) {
        const fallbackTitle = deriveFallbackConversationTitle(text);
        if (fallbackTitle) {
          try {
            const renamed = await renameIdeConversation(conversationId, fallbackTitle);
            setConversations((rows) => rows.map((row) => (row.id === renamed.id ? renamed : row)));
          } catch {
            // Mejor esfuerzo: si falla el renombrado automático, la conversación
            // queda con su título por defecto y la persona la puede renombrar a mano.
          }
        }
      }
    } catch (submitError) {
      const motivo = errorMessage(submitError, "No se pudo entregar el mensaje.");
      // Las imágenes vuelven al compositor pase lo que pase: la ficha solo
      // sabe guardar texto, y el motor rechaza justamente los adjuntos cuando
      // el agente está ocupado ("manda la imagen cuando termine el turno").
      // Si mientras tanto se adjuntaron otras, mandan las nuevas.
      if (attachments.length) {
        setAgentAttachments((actuales) => (actuales.length ? actuales : attachments));
      }
      if (hayFicha) {
        setOutbox((cola) => conFallo(cola, localId, motivo));
        return;
      }
      setError(motivo);
      setPrompt((actual) => (actual.trim() ? `${actual}\n${text}` : text));
    }
  }

  /**
   * Le habla a un agente de OTRA conversación desde la torre de control, sin
   * cambiar de carpeta ni de hilo: es el punto entero de la vista de varios
   * agentes a la vez. Conserva el modelo con el que esa sesión venía
   * trabajando en vez de imponerle el del selector, que pertenece al hilo
   * abierto.
   */
  async function directRun(run: AgentRunSummary, texto: string) {
    const limpio = texto.trim();
    const conversationId = run.conversationId;
    if (!limpio || !conversationId) return;
    const session =
      allAgents.find((row) => row.id === run.sessionId) ??
      agents.find((row) => row.id === run.sessionId) ??
      null;
    if (!session) return;
    const localId = nuevoIdLocal();
    setError(null);
    setOutbox((cola) => [
      ...cola,
      nuevoMensajeLocal({
        localId,
        conversationId,
        texto: limpio,
        creadoEn: Date.now(),
        // Solo el hilo abierto tiene cursor conocido; para los demás, la ficha
        // se resuelve con el evento de entrega o al entrar a esa conversación.
        cursorAlEncolar: run.sessionId === activeAgentId ? agentCursorRef.current : null,
      }),
    ]);
    try {
      const created = await createIdeAgent({
        workspace_id: session.workspace_id,
        prompt: limpio,
        model: session.model || undefined,
        conversation_id: conversationId,
      });
      setOutbox((cola) => conRespuestaDeEnvio(cola, localId, normalizarRespuestaDeEnvio(created)));
      setAllAgents((rows) => rows.map((row) => (row.id === created.id ? created : row)));
      if (created.id === activeAgentId) setAgent(created);
    } catch (directError) {
      const motivo = errorMessage(directError, "No se pudo entregar el mensaje.");
      setOutbox((cola) => conFallo(cola, localId, motivo));
      // La ficha en rojo queda colgada de ESA conversación y solo se ve al
      // abrirla, así que sin este aviso un "dirigir" rechazado (plan esperando
      // aprobación, cola llena, Mac desconectado) no dejaría ninguna señal en
      // la pantalla desde la que se mandó. El texto sigue en la ficha.
      setError(`«${run.titulo}» no recibió el mensaje: ${motivo}`);
    }
  }

  function handlePromptChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const value = event.target.value;
    setPrompt(value);
    // El menú "/" solo aplica al INICIO del compositor (ver
    // `findCommandTrigger`); en cuanto aparece, se apaga el de "@" -- los dos
    // conviven en el compositor, pero nunca a la vez sobre la misma tecla.
    const commandPrefix = findCommandTrigger(value);
    if (commandPrefix !== null) {
      setCommandMenuOpen(true);
      setCommandQuery(commandPrefix);
      setMentionOpen(false);
      return;
    }
    setCommandMenuOpen(false);
    const caret = event.target.selectionStart ?? value.length;
    const trigger = findMentionTrigger(value, caret);
    if (trigger) {
      setMentionOpen(true);
      setMentionStart(trigger.start);
      setMentionQuery(trigger.query);
    } else {
      setMentionOpen(false);
    }
  }

  /** Inserta la referencia elegida como texto (`@ruta `) donde estaba la mención y la agrega como ficha visible. */
  function selectMention(match: IdeReferenceMatch) {
    if (mentionStart === null) return;
    const caret = promptFieldRef.current?.selectionStart ?? prompt.length;
    const before = prompt.slice(0, mentionStart);
    const after = prompt.slice(caret);
    const insertText = `@${match.path} `;
    const nextPrompt = `${before}${insertText}${after}`;
    setPrompt(nextPrompt);
    setMentionOpen(false);
    setMentionQuery("");
    setMentionStart(null);
    if (match !== WEB_MENTION) {
      setPromptReferences((rows) => [...rows.filter((row) => row.path !== match.path), match].slice(-8));
    }
    const nextCaret = before.length + insertText.length;
    requestAnimationFrame(() => {
      const field = promptFieldRef.current;
      if (!field) return;
      field.focus();
      field.setSelectionRange(nextCaret, nextCaret);
    });
  }

  /** Completa el nombre en el compositor (Tab) sin ejecutar todavía -- para
   * cuando el comando necesita argumentos que la persona va a escribir. */
  function fillCommandFromMenu(match: IdeCommandMatch) {
    setPrompt(`/${match.nombre} `);
    setCommandMenuOpen(false);
    requestAnimationFrame(() => {
      const field = promptFieldRef.current;
      if (!field) return;
      field.focus();
      field.setSelectionRange(field.value.length, field.value.length);
    });
  }

  /** Enter sobre el menú "/": ejecuta el comando resaltado directo, sin
   * argumentos -- si de verdad los necesita, el propio companion lo dice
   * (`ide_comandos.IDEComandoError`) y queda a la vista en `commandResult`. */
  function chooseCommandFromMenu(match: IdeCommandMatch) {
    setCommandMenuOpen(false);
    void runCommand(`/${match.nombre}`);
  }

  /**
   * Resuelve y ejecuta un comando "/" completo contra el companion
   * (`ide_runtime._despachar_comando`). Un comando destructivo sin
   * `confirmed` vuelve con `requiere_confirmacion: true` -- se guarda en
   * `pendingCommand` y NO se ejecuta nada hasta que la persona confirme
   * explícitamente (`confirmPendingCommand`).
   */
  async function runCommand(text: string, options?: { confirmed?: boolean }) {
    const trimmed = text.trim();
    if (!trimmed || commandBusy) return;
    setCommandMenuOpen(false);
    setCommandBusy(true);
    setError(null);
    try {
      const currentConversation = activeConversationId
        ? conversations.find((row) => row.id === activeConversationId) ?? null
        : null;
      const result = await executeIdeCommand(trimmed, {
        workspaceId: workspace?.id,
        conversationId: activeConversationId ?? undefined,
        projectId: currentConversation?.project_id ?? undefined,
        sessionId: activeAgentId ?? undefined,
        confirmed: options?.confirmed ?? false,
      });

      if (!result.ok && result.requiere_confirmacion) {
        setPendingCommand({
          text: trimmed,
          comando: result.comando ?? "",
          mensaje: result.mensaje ?? "Este comando es destructivo. Confirma para continuar.",
        });
        return;
      }

      setPendingCommand(null);
      setCommandResult(result);
      if (result.ok) setPrompt("");

      if (result.set_model) setIdeModel(result.set_model);

      if (result.copy_text) {
        try {
          await navigator.clipboard.writeText(result.copy_text);
        } catch {
          // El portapapeles puede fallar por permisos del navegador; el
          // texto ya queda visible en el resultado para copiarlo a mano.
        }
      }

      if (result.download) {
        const blob = new Blob([result.download.content], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = result.download.filename;
        link.click();
        URL.revokeObjectURL(url);
      }

      if (result.nueva_conversacion_id) {
        try {
          setConversations(await getIdeConversations());
        } catch {
          // Mejor esfuerzo: la conversación ya quedó creada en el companion
          // aunque no se pueda refrescar la lista ahora mismo.
        }
        setActiveConversationId(result.nueva_conversacion_id);
      }

      if (result.reanudar_session_id) {
        try {
          const refreshedAgents = await getIdeAgents();
          setAgents(refreshedAgents);
          const found = refreshedAgents.find((row) => row.id === result.reanudar_session_id);
          if (found) setActiveConversationId(found.conversation_id ?? null);
        } catch {
          // Mejor esfuerzo.
        }
      }

      if (result.prefill_prompt) {
        if (result.auto_send) {
          await submitPrompt(result.prefill_prompt);
        } else {
          setPrompt(result.prefill_prompt);
        }
      }
    } catch (commandError) {
      setError(errorMessage(commandError, "No se pudo ejecutar el comando."));
    } finally {
      setCommandBusy(false);
    }
  }

  function confirmPendingCommand() {
    if (!pendingCommand) return;
    void runCommand(pendingCommand.text, { confirmed: true });
  }

  function handlePromptKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (commandMenuOpen && commandMatches.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setCommandIndex((index) => (index + 1) % commandMatches.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setCommandIndex((index) => (index - 1 + commandMatches.length) % commandMatches.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        fillCommandFromMenu(commandMatches[commandIndex]);
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        chooseCommandFromMenu(commandMatches[commandIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setCommandMenuOpen(false);
        return;
      }
    }
    if (mentionOpen && mentionMatches.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMentionIndex((index) => (index + 1) % mentionMatches.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMentionIndex((index) => (index - 1 + mentionMatches.length) % mentionMatches.length);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        selectMention(mentionMatches[mentionIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMentionOpen(false);
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitPrompt();
    }
  }

  async function openDiffReview() {
    if (!agent) return;
    setDiffOpen(true);
    setDiffLoading(true);
    setError(null);
    try {
      const result = await getIdeAgentDiff(agent.id);
      setDiffData(result);
      setDiffResolutions({});
    } catch (diffError) {
      setError(errorMessage(diffError, "No se pudieron cargar los cambios de este turno."));
      setDiffOpen(false);
    } finally {
      setDiffLoading(false);
    }
  }

  function acceptDiffFile(path: string) {
    setDiffResolutions((rows) => ({ ...rows, [path]: "accepted" }));
  }

  async function rejectDiffFile(path: string) {
    if (!agent) return;
    setDiffPending((rows) => ({ ...rows, [path]: true }));
    setError(null);
    try {
      await rejectIdeAgentDiffFile(agent.id, path);
      setDiffResolutions((rows) => ({ ...rows, [path]: "rejected" }));
    } catch (rejectError) {
      setError(errorMessage(rejectError, "No se pudo deshacer ese archivo."));
    } finally {
      setDiffPending((rows) => {
        const next = { ...rows };
        delete next[path];
        return next;
      });
    }
  }

  async function openCostSummary() {
    if (!agent) return;
    setCostOpen(true);
    setCostLoading(true);
    setError(null);
    try {
      setCostData(await getIdeAgentCost(agent.id));
    } catch (costError) {
      setError(errorMessage(costError, "No se pudo calcular el costo de este turno."));
      setCostOpen(false);
    } finally {
      setCostLoading(false);
    }
  }

  /** Cambia de conversación activa y, si pertenece a un proyecto con otra carpeta, activa esa carpeta primero. */
  async function selectConversation(conversationId: string) {
    const conversation = conversations.find((row) => row.id === conversationId);
    if (!conversation) return;
    setError(null);
    setEditorAbierto(false);
    const project = conversation.project_id
      ? projects.find((row) => row.id === conversation.project_id)
      : null;
    if (project && project.workspace_available && project.workspace_id !== workspace?.id) {
      const target = workspaces.find((row) => row.id === project.workspace_id);
      if (target) await selectWorkspace(target);
    }
    setActiveConversationId(conversationId);
  }

  async function createConversation(projectId: string | null) {
    setBusy(true);
    setError(null);
    try {
      const created = await createIdeConversation({ project_id: projectId ?? undefined });
      setConversations((rows) => [created, ...rows]);
      if (projectId) {
        setProjects((rows) =>
          rows.map((row) =>
            row.id === projectId ? { ...row, conversation_count: row.conversation_count + 1 } : row,
          ),
        );
        const project = projects.find((row) => row.id === projectId);
        if (project && project.workspace_available && project.workspace_id !== workspace?.id) {
          const target = workspaces.find((row) => row.id === project.workspace_id);
          if (target) await selectWorkspace(target);
        }
      }
      setActiveConversationId(created.id);
    } catch (createError) {
      setError(errorMessage(createError, "No se pudo crear la conversación."));
    } finally {
      setBusy(false);
    }
  }

  async function renameConversation(conversationId: string, title: string) {
    setError(null);
    try {
      const updated = await renameIdeConversation(conversationId, title);
      setConversations((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
    } catch (renameError) {
      setError(errorMessage(renameError, "No se pudo renombrar la conversación."));
    }
  }

  async function deleteConversation(conversationId: string) {
    setError(null);
    try {
      await deleteIdeConversation(conversationId);
    } catch (deleteError) {
      console.warn("Error borrando conversación en backend (limpiando estado local):", deleteError);
    } finally {
      const removed = conversations.find((row) => row.id === conversationId);
      setConversations((rows) => rows.filter((row) => row.id !== conversationId));
      if (removed?.project_id) {
        const projectId = removed.project_id;
        setProjects((rows) =>
          rows.map((row) =>
            row.id === projectId
              ? { ...row, conversation_count: Math.max(0, row.conversation_count - 1) }
              : row,
          ),
        );
      }
      if (activeConversationId === conversationId) setActiveConversationId(null);
    }
  }

  async function createProject(input: { name: string; workspace_id: string }) {
    setBusy(true);
    setError(null);
    try {
      const created = await createIdeProject(input);
      setProjects((rows) => [created, ...rows]);
    } catch (createError) {
      setError(errorMessage(createError, "No se pudo crear el proyecto."));
    } finally {
      setBusy(false);
    }
  }

  async function renameProject(projectId: string, name: string) {
    setError(null);
    try {
      const updated = await renameIdeProject(projectId, name);
      setProjects((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
    } catch (renameError) {
      setError(errorMessage(renameError, "No se pudo renombrar el proyecto."));
    }
  }

  async function deleteProject(projectId: string, mode: "keep" | "delete") {
    setBusy(true);
    setError(null);
    try {
      const result = await deleteIdeProject(projectId, mode);
      setProjects((rows) => rows.filter((row) => row.id !== projectId));
      if (mode === "delete") {
        const affected = new Set(result.affected_conversation_ids);
        setConversations((rows) => rows.filter((row) => !affected.has(row.id)));
        if (activeConversationId && affected.has(activeConversationId)) setActiveConversationId(null);
      } else {
        setConversations((rows) =>
          rows.map((row) => (row.project_id === projectId ? { ...row, project_id: null } : row)),
        );
      }
    } catch (deleteError) {
      setError(errorMessage(deleteError, "No se pudo borrar el proyecto."));
    } finally {
      setBusy(false);
    }
  }

  async function stopAgent() {
    if (!agent) return;
    try {
      await cancelIdeAgent(agent.id);
      const result = await readIdeAgent(agent.id, agentCursorRef.current);
      setAgent(result.session);
    } catch (stopError) {
      setError(errorMessage(stopError, "No se pudo detener la sesión."));
    }
  }

  /** Detiene cualquier ejecución desde la torre de control, sea o no la abierta. */
  async function stopRun(run: AgentRunSummary) {
    setError(null);
    try {
      await cancelIdeAgent(run.sessionId);
      // Marca local para que la tarjeta reaccione al instante; el latido de
      // 2.5s trae el estado real poco después.
      setAllAgents((rows) =>
        rows.map((row) => (row.id === run.sessionId ? { ...row, status: "cancelled" } : row)),
      );
      if (run.sessionId === activeAgentId) {
        const result = await readIdeAgent(run.sessionId, agentCursorRef.current);
        setAgent(result.session);
      }
    } catch (stopError) {
      setError(errorMessage(stopError, "No se pudo detener esa ejecución."));
    }
  }

  /**
   * Salta a una ejecución. Cambia de carpeta usando el `workspace_id` de la
   * SESIÓN, no el del proyecto: una conversación sin proyecto también puede
   * vivir en otra carpeta, y por el proyecto ese caso se caía en un hilo vacío.
   */
  async function goToRun(run: AgentRunSummary) {
    setError(null);
    setEditorAbierto(false);
    const session = allAgents.find((row) => row.id === run.sessionId) ?? null;
    if (session && session.workspace_id !== workspace?.id) {
      const target = workspaces.find((row) => row.id === session.workspace_id);
      if (target) await selectWorkspace(target);
    }
    if (run.conversationId) setActiveConversationId(run.conversationId);
  }

  async function resolveMcpCall(callId: string, approved: boolean) {
    if (!agent || resolvedMcpCalls[callId] !== undefined) return;
    setResolvedMcpCalls((old) => ({ ...old, [callId]: approved }));
    setError(null);
    try {
      await confirmIdeAgentMcp(agent.id, callId, approved);
    } catch (confirmationError) {
      setResolvedMcpCalls((old) => {
        const next = { ...old };
        delete next[callId];
        return next;
      });
      setError(errorMessage(confirmationError, "No se pudo responder la confirmación MCP."));
    }
  }

  async function openTreePath(path: string) {
    if (!workspace) return;
    try {
      const result = await getIdeWorkspaceTree(workspace.id, path || undefined, 2, 700);
      setTree(result.entries);
      setTreePath(result.path === "." ? "" : result.path);
    } catch (treeError) {
      setError(errorMessage(treeError, "No se pudo abrir la carpeta."));
    }
  }

  async function openFile(path: string) {
    if (!workspace) return;
    setError(null);
    try {
      const file = await getIdeWorkspaceFile(workspace.id, path);
      if (file.encoding !== "utf-8") throw new Error("Este archivo es binario y no se edita como texto.");
      setSelectedFile(path);
      setFileContent(file.content);
      setSavedContent(file.content);
      setEditorAbierto(true);
    } catch (fileError) {
      setError(errorMessage(fileError, "No se pudo abrir el archivo."));
    }
  }

  async function saveFile() {
    if (!workspace || !selectedFile || fileContent === savedContent) return;
    setBusy(true);
    try {
      await putIdeWorkspaceFile(workspace.id, selectedFile, fileContent);
      setSavedContent(fileContent);
      setGit(await getIdeGitStatus(workspace.id));
    } catch (saveError) {
      setError(errorMessage(saveError, "No se pudo guardar el archivo."));
    } finally {
      setBusy(false);
    }
  }

  async function startTerminal() {
    if (!workspace) return;
    setBusy(true);
    try {
      const created = await createIdeTerminal({
        workspace_id: workspace.id,
        title: `Terminal · ${workspace.name}`,
      });
      setTerminals((rows) => [created, ...rows.filter((row) => row.id !== created.id)]);
      setTerminal(created);
      setEditorAbierto(true);
    } catch (terminalError) {
      setError(errorMessage(terminalError, "No se pudo abrir la terminal."));
    } finally {
      setBusy(false);
    }
  }

  async function submitTerminal() {
    const data = terminalInput;
    if (!terminal || !data.trim()) return;
    setTerminalInput("");
    try {
      await sendIdeTerminalInput(terminal.id, `${data}\n`);
    } catch (terminalError) {
      setTerminalInput(data);
      setError(errorMessage(terminalError, "No se pudo enviar el comando."));
    }
  }

  /** Entra al Editor (explorador + editor + terminal, §3.1 Estado 3) sin
   * pasar por un archivo o una terminal concretos -- p. ej. desde el "+" del
   * compositor ("Explorador de archivos"), que es la otra puerta de entrada
   * que describe la especificación ("desde un diff o desde el árbol"). */
  function openExplorer() {
    setEditorAbierto(true);
  }

  /** Vuelve de Editor a Trabajando/Reposo sin perder el archivo abierto --
   * un gesto explícito, porque ya no hay pestaña de la que simplemente salir. */
  function closeEditor() {
    setEditorAbierto(false);
  }

  async function authorizePath(path: string, name?: string) {
    setBusy(true);
    setError(null);
    try {
      const created = await createIdeWorkspace(path, name);
      setWorkspaces((rows) => [created, ...rows.filter((row) => row.id !== created.id)]);
      setWorkspace(created);
      await loadWorkspaceData(created);
      setAddAction(null);
    } catch (workspaceError) {
      setError(errorMessage(workspaceError, "No se pudo autorizar la carpeta."));
    } finally {
      setBusy(false);
    }
  }

  async function pickWorkspace(name?: string) {
    setBusy(true);
    setError(null);
    try {
      if (isTauriApp()) {
        const path = await tauriInvoke<string | null>("pick_workspace_folder");
        if (!path) return;
        await authorizePath(path, name);
        return;
      }
      const result = await pickIdeWorkspace(name);
      if (result.cancelled || !result.workspace) return;
      const created = result.workspace;
      setWorkspaces((rows) => [created, ...rows.filter((row) => row.id !== created.id)]);
      setWorkspace(created);
      setSelectedFile(null);
      await loadWorkspaceData(created);
      setAddAction(null);
    } catch (workspaceError) {
      setError(errorMessage(workspaceError, "No se pudo abrir el selector de carpetas."));
    } finally {
      setBusy(false);
    }
  }

  async function cloneWorkspace(input: { url: string; name?: string; branch?: string }) {
    if (!workspace) {
      setError("Primero abre la carpeta donde quieres guardar el repositorio.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await cloneIdeWorkspace({
        parent_workspace_id: workspace.id,
        url: input.url,
        name: input.name,
        branch: input.branch,
        depth: 1,
      });
      const created = result.workspace;
      setWorkspaces((rows) => [created, ...rows.filter((row) => row.id !== created.id)]);
      setWorkspace(created);
      setSelectedFile(null);
      await loadWorkspaceData(created);
      setAddAction(null);
    } catch (cloneError) {
      setError(errorMessage(cloneError, "No se pudo clonar el repositorio."));
    } finally {
      setBusy(false);
    }
  }

  function addImageAttachment(attachment: IdeAgentAttachment) {
    setAgentAttachments((rows) => [...rows, attachment].slice(-5));
    setAddAction(null);
    // 2.1 del plan de paridad: aviso claro (no silencioso) cuando el modelo
    // activo no ve. Si hay uno con visión disponible, se cambia sola --
    // mismo criterio que ya aplica el companion en `_model_for_turn` cuando
    // no hay modelo pedido explícitamente ("no le pidamos a la persona que
    // entienda o elija modelos"); acá SÍ hay uno elegido, así que se avisa
    // del cambio en vez de hacerlo mudo.
    const current = ideModels.find((option) => option.id === ideModel);
    if (current && current.capacidades && !current.capacidades.includes("vision")) {
      const withVision = ideModels.find((option) => option.capacidades?.includes("vision"));
      if (withVision) {
        setIdeModel(withVision.id);
        setImageNotice(
          `«${current.nombre}» no tiene visión: cambié al modelo «${withVision.nombre}» para que se pueda ver esta imagen.`,
        );
      } else {
        setImageNotice(
          "Ningún modelo disponible tiene capacidad de visión: esta imagen no podrá analizarse.",
        );
      }
    } else {
      setImageNotice(null);
    }
  }

  async function handleImageFile(file: File) {
    const result = await fileToImageAttachment(file);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    addImageAttachment(result.attachment);
  }

  const terminalText = useMemo(() => cleanTerminalOutput(terminalEvents), [terminalEvents]);
  const mergedAgentEvents = useMemo(() => mergeIdeEvents(agentEvents), [agentEvents]);
  const agentTurns = useMemo(
    () => (agent ? splitSessionIntoTurns(agent.id, mergedAgentEvents, isLive(agent)) : []),
    [agent, mergedAgentEvents],
  );

  // `last_session_status` (el puntito de "corriendo"/"falló" en la barra
  // lateral) antes solo se sabía para las conversaciones del workspace activo,
  // porque `agents` es la lista de ESE workspace nada más y las demás
  // conversaciones quedaban sin marca. Con el latido de `allAgents` ya se
  // conocen las de todas las carpetas: `agents` queda solo como respaldo para
  // los primeros segundos, antes de que llegue la primera lista completa.
  const conversationSummaries = useMemo<IdeConversationSummary[]>(() => {
    const statusByConversation = new Map<string, IdeSession["status"]>();
    for (const session of [...allAgents, ...agents]) {
      if (!session.conversation_id || statusByConversation.has(session.conversation_id)) continue;
      statusByConversation.set(session.conversation_id, session.status);
    }
    return conversations.map((conversation) => ({
      ...conversation,
      last_session_status: statusByConversation.get(conversation.id),
    }));
  }, [conversations, agents, allAgents]);

  const workspaceOptions = useMemo<IdeWorkspaceOption[]>(
    () => workspaces.map((row) => ({ id: row.id, name: row.name, available: row.available })),
    [workspaces],
  );

  /**
   * Las filas de la torre de control: una por CONVERSACIÓN, no una por sesión.
   * `allAgents` ya viene más-reciente-primero (`SessionManager.list`), y una
   * conversación larga deja detrás sesiones viejas de turnos anteriores;
   * mostrarlas todas convertiría el panel en un historial y taparía justo lo
   * que se quiere ver, que es qué está pasando ahora.
   */
  const activityRuns = useMemo<AgentRunSummary[]>(() => {
    const conversationById = new Map(conversations.map((row) => [row.id, row]));
    const projectById = new Map(projects.map((row) => [row.id, row]));
    const vistas = new Set<string>();
    const filas: AgentRunSummary[] = [];
    for (const session of allAgents) {
      const clave = session.conversation_id ?? session.id;
      if (vistas.has(clave)) continue;
      vistas.add(clave);
      const conversation = session.conversation_id
        ? conversationById.get(session.conversation_id) ?? null
        : null;
      const project = conversation?.project_id ? projectById.get(conversation.project_id) ?? null : null;
      filas.push({
        sessionId: session.id,
        conversationId: session.conversation_id ?? null,
        titulo: conversation?.title || session.title || "Sesión de ingeniería",
        estado: session.status,
        viva: isLive(session),
        proyecto: project?.name ?? null,
        carpeta: session.workspace_name || "",
        modelo: session.model ?? null,
        startedAt: session.started_at,
        endedAt: session.ended_at,
        enCola: session.conversation_id ? contarEnEspera(outbox, session.conversation_id) : 0,
        activa: Boolean(session.conversation_id && session.conversation_id === activeConversationId),
      });
      if (filas.length >= 40) break;
    }
    return filas;
  }, [allAgents, conversations, projects, outbox, activeConversationId]);

  const liveRunCount = useMemo(
    () => activityRuns.filter((run) => run.viva).length,
    [activityRuns],
  );

  const composerQueue = useMemo(
    () => mensajesDeConversacion(outbox, activeConversationId),
    [outbox, activeConversationId],
  );

  // El estado que se pinta, derivado de los datos y no de una pestaña
  // elegida a mano (regla dura del encargo): archivo o terminal abiertos
  // manda primero, luego si hay conversación activa o en marcha, y si no, Reposo.
  const vista: IdeVista = editorAbierto ? "editor" : (agent || activeConversationId) ? "trabajando" : "reposo";

  return {
    // Estado del mundo
    connected,
    workspaces,
    workspace,
    agent,
    agentCursor,
    git,
    busy,
    error,
    setError,
    vista,

    // Composer / prompt
    prompt,
    setPrompt,
    ideModels,
    ideModel,
    setIdeModel,
    agentAttachments,
    setAgentAttachments,
    imageNotice,
    setImageNotice,
    promptFieldRef,
    promptReferences,
    setPromptReferences,
    mentionOpen,
    setMentionOpen,
    mentionMatches,
    mentionIndex,
    setMentionIndex,
    selectMention,
    commandMenuOpen,
    setCommandMenuOpen,
    commandQuery,
    commandMatches,
    commandIndex,
    setCommandIndex,
    commandBusy,
    fillCommandFromMenu,
    chooseCommandFromMenu,
    commandResult,
    setCommandResult,
    ideHelpText,
    pendingCommand,
    setPendingCommand,
    confirmPendingCommand,
    runCommand,
    helpOpen,
    setHelpOpen,
    handlePromptChange,
    handlePromptKeyDown,
    submitPrompt,
    directRun,
    addAction,
    setAddAction,
    addMenu,
    setAddMenu,
    authorizePath,
    pickWorkspace,
    cloneWorkspace,
    addImageAttachment,
    handleImageFile,
    composerQueue,
    startTerminal,
    openExplorer,

    // Trabajando (mission control)
    agentTurns,
    resolvedMcpCalls,
    resolveMcpCall,
    stopAgent,
    openDiffReview,
    diffOpen,
    setDiffOpen,
    diffLoading,
    diffData,
    diffResolutions,
    diffPending,
    acceptDiffFile,
    rejectDiffFile,
    openCostSummary,
    costOpen,
    setCostOpen,
    costLoading,
    costData,
    timelineEndRef,

    // Editor (explorador + archivo + terminal)
    editorAbierto,
    closeEditor,
    tree,
    treePath,
    selectedFile,
    fileContent,
    setFileContent,
    savedContent,
    openTreePath,
    openFile,
    saveFile,
    terminals,
    terminal,
    setTerminal,
    terminalText,
    terminalInput,
    setTerminalInput,
    terminalCursor,
    submitTerminal,

    // Proyectos / conversaciones (barra lateral)
    projects,
    conversationSummaries,
    activeConversationId,
    workspaceOptions,
    selectConversation,
    createConversation,
    renameConversation,
    deleteConversation,
    createProject,
    renameProject,
    deleteProject,
    selectWorkspace,
    syncWorkspaces,

    // Torre de control ("Ejecuciones", acceso discreto en la barra lateral)
    activityRuns,
    activityLoading,
    activityOpen,
    setActivityOpen,
    liveRunCount,
    stopRun,
    goToRun,

    // Memoria y conocimiento (acceso discreto en la barra lateral)
    memoryPanelOpen,
    setMemoryPanelOpen,
  };
}

export type IdeEstado = ReturnType<typeof useIdeEstado>;
