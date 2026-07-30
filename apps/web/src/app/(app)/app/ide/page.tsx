"use client";

import Link from "next/link";
import type { ChangeEvent, KeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentActivityCenter, type AgentRunSummary } from "@/components/ide/AgentActivityCenter";
import AgentThread, { type AgentThreadTurn } from "@/components/ide/AgentThread";
import DiffReview, { type DiffReviewFile } from "@/components/ide/DiffReview";
import { MemoryKnowledgePanel } from "@/components/ide/MemoryKnowledgePanel";
import { MessageQueue } from "@/components/ide/MessageQueue";
import {
  ProjectSidebar,
  type IdeConversationSummary,
  type IdeWorkspaceOption,
} from "@/components/ide/ProjectSidebar";
import { SearchPanel } from "@/components/ide/SearchPanel";
import {
  BrainIcon,
  ChartBarIcon,
  ChevronDownIcon,
  CodeIcon,
  FileIcon,
  GridIcon,
  PencilIcon,
  PlusIcon,
  SendIcon,
  SettingsIcon,
  SparklesIcon,
  SquareIcon,
  XIcon,
} from "@/components/icons";
import { Alert, Badge, Spinner } from "@/components/ui";
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

type Panel = "agent" | "files" | "terminal";
type AddAction = "folder" | "clone" | "image" | null;

const FALLBACK_IDE_MODELS: IdeModelOption[] = [
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

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

// 2.1 del plan de paridad: imágenes en el compositor por selector de
// archivo, pegar (portapapeles) o arrastrar -- las tres rutas terminan acá,
// para no repetir la validación (tipo aceptado, tope de tamaño) tres veces.
// La validación REAL (firma binaria, decodificación) es responsabilidad del
// companion (`ide_imagenes.validar_y_normalizar_imagen`); esto es solo un
// filtro barato en el navegador para no ni intentar subir algo que ya se ve
// mal desde el cliente.
const ACCEPTED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"] as const;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

function readFileAsDataUrl(file: File): Promise<string | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve(null);
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : null);
    reader.readAsDataURL(file);
  });
}

async function fileToImageAttachment(
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

function isLive(session: IdeSession | null | undefined): boolean {
  return Boolean(
    session &&
      !session.ended_at &&
      !["completed", "failed", "closed", "cancelled", "interrupted"].includes(session.status),
  );
}

function relativePath(parent: string, name: string): string {
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

function referenceKindLabel(match: IdeReferenceMatch): string {
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

interface IdeCommandMatch {
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
 * `events[]` de una sesión activa puede traer varios `user` acumulados. La
 * versión vieja de esta pantalla (antes de este cableado) asumía "una sesión
 * = un turno" y solo mostraba la ÚLTIMA respuesta, enterrando las anteriores
 * dentro del detalle técnico plegado -- por eso se corrige acá, no
 * adaptándolo por fuera: `AgentThread` (`components/ide/AgentThread.tsx`)
 * espera turnos ya partidos, no sesiones completas.
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
 * mensaje de una conversación nueva.
 *
 * Por qué existe: antes de este cableado, `POST /v1/ide/agents` generaba un
 * título semántico con LLM (`_semantic_conversation_title`,
 * `routers/ide.py`) cada vez que `conversation_id` llegaba vacío. Ahora
 * SIEMPRE mandamos el `conversation_id` del registro de proyectos (es el
 * contrato con `ide_sessions._find_reusable_agent_session` -- sin eso no hay
 * continuidad), así que esa rama del router queda permanentemente muerta
 * para Forge Studio. Esto es un respaldo intencionalmente simple, no una
 * reimplementación de la generación semántica: evita que toda conversación
 * nueva quede pegada en "Nueva conversación" en la barra lateral.
 */
function deriveFallbackConversationTitle(text: string): string | null {
  const firstLine = text.split("\n").find((line) => line.trim()) ?? "";
  const trimmed = firstLine.trim();
  if (!trimmed) return null;
  return trimmed.length > 60 ? `${trimmed.slice(0, 57)}…` : trimmed;
}

function cleanTerminalOutput(events: IdeSessionEvent[]): string {
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

export default function IdePage() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [workspaces, setWorkspaces] = useState<IdeWorkspace[]>([]);
  const [workspace, setWorkspace] = useState<IdeWorkspace | null>(null);
  const [agents, setAgents] = useState<IdeSession[]>([]);
  const [agent, setAgent] = useState<IdeSession | null>(null);
  const [agentEvents, setAgentEvents] = useState<IdeSessionEvent[]>([]);
  const [agentCursor, setAgentCursor] = useState(0);
  const [prompt, setPrompt] = useState("");
  const [ideModels, setIdeModels] = useState<IdeModelOption[]>([]);
  const [ideModel, setIdeModel] = useState<string>("");
  const [agentAttachments, setAgentAttachments] = useState<IdeAgentAttachment[]>([]);
  const [imageNotice, setImageNotice] = useState<string | null>(null);

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

  const [panel, setPanel] = useState<Panel>("agent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addAction, setAddAction] = useState<AddAction>(null);
  const [addMenu, setAddMenu] = useState(false);

  const [tree, setTree] = useState<IdeTreeNode[]>([]);
  const [treePath, setTreePath] = useState("");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [savedContent, setSavedContent] = useState("");

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
      setIdeModel((current) =>
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
  //
  // Pasar de "todavía no hay conversación" a la recién creada NO es cambiar de
  // hilo: es el mismo hilo tomando nombre, y ahí borrar el borrador se comería
  // lo que la persona ya empezó a escribir para el mensaje siguiente mientras
  // el primero se registraba.
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

  useEffect(() => {
    if (!activeAgentId || panel !== "agent") return;
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
  }, [activeAgentId, panel]);

  useEffect(() => {
    if (!activeTerminalId || panel !== "terminal") return;
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
  }, [activeTerminalId, panel]);

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

  async function selectWorkspace(next: IdeWorkspace) {
    if (next.id === workspace?.id) return;
    setBusy(true);
    setError(null);
    try {
      const active = await activateIdeWorkspace(next.id);
      setWorkspaces((rows) => rows.map((row) => ({ ...row, active: row.id === active.id })));
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
   * Manda un mensaje al agente. YA NO se bloquea porque haya un turno en
   * curso: ese era justo el problema -- el mensaje se rechazaba y el texto se
   * perdía. Ahora el compositor se vacía de inmediato (se puede seguir
   * pensando en voz alta) y el destino del mensaje se sigue por su ficha:
   * encolado → entregado.
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
    setPanel("agent");
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
      // `conversation_id` va SIEMPRE (nunca `undefined`): es el contrato con
      // `ide_projects.ProjectRegistry` -- el id de la conversación en el
      // registro tiene que ser el mismo que usa `SessionManager` para reusar
      // sesión (`_find_reusable_agent_session`, `ide_sessions.py`). Por eso,
      // a diferencia de antes, la generación semántica de título del router
      // (`_semantic_conversation_title`, gateada en `conversation_id is None`)
      // ya nunca corre para Forge Studio: se compensa abajo con un título de
      // respaldo simple sobre el registro, no sobre la sesión.
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
   * explícitamente (`confirmCommand` más abajo).
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
    setPanel("agent");
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
    } catch (deleteError) {
      setError(errorMessage(deleteError, "No se pudo borrar la conversación."));
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
    setPanel("agent");
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
      setPanel("files");
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
      setPanel("terminal");
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

  return (
    <div className="-m-6 h-[calc(100vh-3.5rem)] min-h-[44rem] overflow-hidden bg-white text-slate-950">
      <div className="flex h-full">
        <div className="hidden lg:flex lg:h-full lg:flex-col">
          <div className="min-h-0 flex-1">
            <ProjectSidebar
              projects={projects}
              conversations={conversationSummaries}
              activeConversationId={activeConversationId}
              workspaces={workspaceOptions}
              busy={busy}
              onSelectConversation={(id) => void selectConversation(id)}
              onCreateConversation={(projectId) => void createConversation(projectId)}
              onRenameConversation={(id, title) => void renameConversation(id, title)}
              onDeleteConversation={(id) => void deleteConversation(id)}
              onCreateProject={(input) => void createProject(input)}
              onRenameProject={(id, name) => void renameProject(id, name)}
              onDeleteProject={(id, mode) => void deleteProject(id, mode)}
            />
          </div>
          <div className="shrink-0 border-t border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
            <Link
              href="/app/ajustes"
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-500 hover:bg-[#f7f7f6] dark:text-slate-400 dark:hover:bg-slate-800"
            >
              <SettingsIcon className="h-4 w-4" /> Ajustes del estudio
            </Link>
          </div>
        </div>

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white">
          <header className="flex min-h-[58px] items-center justify-between gap-3 border-b border-[#dededc] px-4 sm:px-5">
            <div className="flex min-w-0 items-center gap-3">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-900 text-white">
                <CodeIcon className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="truncate text-base font-bold">Forge Studio</h1>
                  <Badge variant={connected ? "success" : "danger"}>
                    {connected ? "Mac conectado" : "Sin Mac"}
                  </Badge>
                </div>
                <p className="truncate text-xs text-slate-400">
                  {workspace?.name ?? "Selecciona un proyecto"}
                  {git?.branch ? ` · ${git.branch}` : ""}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setActivityOpen((value) => !value)}
                aria-pressed={activityOpen}
                title="Ver todo lo que está corriendo"
                className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${
                  activityOpen
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-[#e2e2e0] bg-white text-slate-600 hover:bg-[#f7f7f6]"
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    liveRunCount > 0 ? "animate-pulse bg-amber-400" : "bg-slate-300"
                  }`}
                  aria-hidden="true"
                />
                {liveRunCount > 0 ? `${liveRunCount} corriendo` : "Ejecuciones"}
              </button>
              <button
                type="button"
                disabled={!workspace}
                onClick={() => setMemoryPanelOpen(true)}
                className="flex items-center gap-1.5 rounded-lg border border-[#e2e2e0] bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-[#f7f7f6] disabled:opacity-40"
              >
                <BrainIcon className="h-3.5 w-3.5" />
                <span className="hidden md:inline">Memoria y conocimiento</span>
              </button>
              <div className="flex rounded-lg border border-[#e2e2e0] bg-white p-0.5">
                {([
                  ["agent", "Agente", SparklesIcon],
                  ["files", "Código", GridIcon],
                  ["terminal", "Terminal", CodeIcon],
                ] as const).map(([value, label, Icon]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setPanel(value)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold ${
                      panel === value
                        ? "bg-slate-950 text-white"
                        : "text-slate-500"
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">{label}</span>
                  </button>
                ))}
              </div>
            </div>
          </header>

          {error && (
            <div className="px-4 pt-3">
              <Alert variant="error">
                <span className="flex items-start justify-between gap-3">
                  <span>{error}</span>
                  <button type="button" onClick={() => setError(null)} aria-label="Cerrar">
                    <XIcon className="h-4 w-4" />
                  </button>
                </span>
              </Alert>
            </div>
          )}

          {!workspace ? (
            <EmptyWorkspace
              connected={Boolean(connected)}
              onAdd={() => setAddAction("folder")}
            />
          ) : panel === "agent" ? (
            <section className="relative flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-10">
                {!agent ? (
                  <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center text-center">
                    <span className="grid h-12 w-12 place-items-center rounded-xl bg-slate-900 text-white">
                      <SparklesIcon className="h-7 w-7" />
                    </span>
                    <h2 className="mt-5 text-2xl font-bold">¿Qué vamos a construir?</h2>
                    <p className="mt-2 max-w-lg text-sm leading-6 text-slate-500">
                      Edecán trabajará sobre {workspace.name}. Verás cada paso, comando y resultado
                      aquí y en tu teléfono.
                    </p>
                  </div>
                ) : (
                  <div className="mx-auto max-w-4xl space-y-5">
                    <div className="mb-2 flex items-center justify-between gap-3 border-b border-[#dededc] pb-4">
                      <div>
                        <h2 className="font-bold">{agent.title || "Sesión de ingeniería"}</h2>
                        <p className="text-xs text-slate-400">
                          {agent.workspace_name} · {agent.status} · cursor {agentCursor}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void openCostSummary()}
                          className="flex items-center gap-1.5 rounded-lg border border-[#d9d9d7] bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-[#f7f7f6]"
                        >
                          <ChartBarIcon className="h-3.5 w-3.5" /> Costo
                        </button>
                        <button
                          type="button"
                          onClick={() => void openDiffReview()}
                          className="flex items-center gap-1.5 rounded-lg border border-[#d9d9d7] bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-[#f7f7f6]"
                        >
                          <PencilIcon className="h-3.5 w-3.5" /> Ver cambios
                        </button>
                        {isLive(agent) && (
                          <button
                            type="button"
                            onClick={() => void stopAgent()}
                            className="flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-xs font-semibold text-red-600 hover:bg-red-50"
                          >
                            <SquareIcon className="h-3.5 w-3.5" /> Detener
                          </button>
                        )}
                      </div>
                    </div>
                    {agentTurns.length > 0 ? (
                      <AgentThread
                        turns={agentTurns}
                        resolvedMcpCalls={resolvedMcpCalls}
                        onResolveMcp={(callId, approved) => void resolveMcpCall(callId, approved)}
                        scrollAnchorRef={timelineEndRef}
                      />
                    ) : (
                      <>
                        <div className="border-y border-[#dededc] py-5 text-sm text-slate-500">
                          {isLive(agent) ? "Iniciando el agente…" : "Esta sesión no tiene eventos visibles."}
                        </div>
                        <div ref={timelineEndRef} />
                      </>
                    )}
                  </div>
                )}
              </div>

              <div className="border-t border-[#e6e6e4] bg-white p-3 sm:p-4">
                <div
                  className="relative mx-auto max-w-4xl rounded-xl border border-[#d9d9d7] bg-white p-3"
                  onDragOver={(event) => {
                    if (Array.from(event.dataTransfer.types).includes("Files")) {
                      event.preventDefault();
                    }
                  }}
                  onDrop={(event) => {
                    const file = Array.from(event.dataTransfer.files).find((row) =>
                      row.type.startsWith("image/"),
                    );
                    if (!file) return;
                    event.preventDefault();
                    void handleImageFile(file);
                  }}
                >
                  <textarea
                    ref={promptFieldRef}
                    value={prompt}
                    onChange={handlePromptChange}
                    onKeyDown={handlePromptKeyDown}
                    onPaste={(event) => {
                      const item = Array.from(event.clipboardData.items).find((row) =>
                        row.type.startsWith("image/"),
                      );
                      const file = item?.getAsFile();
                      if (!file) return;
                      event.preventDefault();
                      void handleImageFile(file);
                    }}
                    onBlur={() => {
                      window.setTimeout(() => setMentionOpen(false), 120);
                      window.setTimeout(() => setCommandMenuOpen(false), 120);
                    }}
                    placeholder={
                      agent && isLive(agent)
                        ? "Sigue dirigiendo: lo que escribas entra en la próxima vuelta, sin cortarle el trabajo…"
                        : "Describe el resultado. Escribe @ para referenciar, / para un comando, o pega/arrastra una imagen…"
                    }
                    className="min-h-24 w-full resize-none bg-transparent px-2 py-1 text-sm leading-6 outline-none placeholder:text-slate-400"
                  />
                  {commandMenuOpen && (
                    <CommandMenu
                      matches={commandMatches}
                      activeIndex={commandIndex}
                      query={commandQuery}
                      onHover={setCommandIndex}
                      onSelect={chooseCommandFromMenu}
                    />
                  )}
                  {mentionOpen && mentionMatches.length > 0 && (
                    <MentionMenu
                      matches={mentionMatches}
                      activeIndex={mentionIndex}
                      onHover={setMentionIndex}
                      onSelect={selectMention}
                    />
                  )}
                  {commandResult && (
                    <CommandResultBanner
                      result={commandResult}
                      helpText={ideHelpText}
                      onDismiss={() => setCommandResult(null)}
                      onUseSuggestion={(sugerencia) => {
                        setCommandResult(null);
                        setPrompt(`${sugerencia} `);
                        requestAnimationFrame(() => promptFieldRef.current?.focus());
                      }}
                      onOpenHelp={() => setHelpOpen(true)}
                    />
                  )}
                  {promptReferences.length > 0 && (
                    <div className="mb-2 flex flex-wrap gap-2 border-t border-slate-200/70 px-1 pt-2">
                      {promptReferences.map((reference) => (
                        <span
                          key={reference.path}
                          className="flex max-w-52 items-center gap-1.5 rounded-md bg-indigo-50 px-2.5 py-1.5 text-xs text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300"
                        >
                          <span className="truncate font-mono">@{reference.name}</span>
                          <button
                            type="button"
                            onClick={() =>
                              setPromptReferences((rows) => rows.filter((row) => row.path !== reference.path))
                            }
                            aria-label={`Quitar referencia ${reference.name}`}
                          >
                            <XIcon className="h-3.5 w-3.5" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                  {agentAttachments.length > 0 && (
                    <div className="mb-2 flex flex-wrap gap-2 border-t border-slate-200/70 px-1 pt-2">
                      {agentAttachments.map((attachment, index) => (
                        <span
                          key={`${attachment.name}-${index}`}
                          className="flex max-w-52 items-center gap-2 rounded-md bg-slate-100 px-2.5 py-1.5 text-xs text-slate-700"
                        >
                          <span className="truncate">{attachment.name}</span>
                          <button
                            type="button"
                            onClick={() => {
                              setAgentAttachments((rows) => rows.filter((_, rowIndex) => rowIndex !== index));
                              setImageNotice(null);
                            }}
                            aria-label={`Quitar ${attachment.name}`}
                          >
                            <XIcon className="h-3.5 w-3.5" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                  {imageNotice && (
                    <p className="mb-2 border-t border-slate-200/70 px-1 pt-2 text-[11px] text-amber-600 dark:text-amber-400">
                      {imageNotice}
                    </p>
                  )}
                  <MessageQueue
                    mensajes={composerQueue}
                    onRecuperarTexto={(texto) => {
                      setPrompt((actual) => (actual.trim() ? `${actual}\n${texto}` : texto));
                      requestAnimationFrame(() => promptFieldRef.current?.focus());
                    }}
                  />
                  <div className="flex items-center justify-between border-t border-slate-200/70 pt-2">
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => setAddMenu((value) => !value)}
                        className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"
                        aria-label="Añadir contexto"
                      >
                        <PlusIcon className="h-5 w-5" />
                      </button>
                      {addMenu && (
                        <AddMenu
                          onClose={() => setAddMenu(false)}
                          onAction={(action) => {
                            setAddMenu(false);
                            if (action === "terminal") void startTerminal();
                            else setAddAction(action);
                          }}
                        />
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <label className="relative hidden min-w-[13rem] sm:block">
                        <select
                          value={ideModel}
                          onChange={(event) => setIdeModel(event.target.value)}
                          disabled={!ideModels.length}
                          className="w-full appearance-none rounded-lg border border-[#d9d9d7] bg-white py-2 pl-3 pr-8 text-xs font-semibold text-slate-700 outline-none focus:border-slate-500"
                          aria-label="Modelo de Forge Studio"
                        >
                          {ideModels.length === 0 && (
                            <option value="">Modelo de ingeniería</option>
                          )}
                          {ideModels.map((option) => (
                            <option key={option.id} value={option.id}>
                              {option.nombre}
                            </option>
                          ))}
                        </select>
                        <ChevronDownIcon className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                      </label>
                      <span className="hidden text-[11px] text-slate-400 lg:inline">
                        Cambias el modelo, no el contexto.
                      </span>
                      {/* Detener y Enviar conviven: antes, con el agente
                          trabajando, el botón de enviar se cambiaba por el de
                          detener y no había forma de decirle nada más. Cortar
                          el trabajo y dirigirlo son dos cosas distintas. */}
                      {agent && isLive(agent) && (
                        <button
                          type="button"
                          onClick={() => void stopAgent()}
                          className="flex h-10 items-center gap-2 rounded-lg border border-red-200 bg-white px-3 text-xs font-bold text-red-600 hover:bg-red-50"
                          aria-label="Detener trabajo"
                        >
                          <SquareIcon className="h-4 w-4" />
                          <span className="hidden sm:inline">Detener</span>
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={!prompt.trim() || commandBusy}
                        onClick={() => void submitPrompt()}
                        className="grid h-10 w-10 place-items-center rounded-lg bg-slate-900 text-white hover:bg-slate-700 disabled:opacity-40"
                        aria-label={
                          agent && isLive(agent) ? "Mandar a la cola del agente" : "Iniciar trabajo"
                        }
                      >
                        <SendIcon className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </section>
          ) : panel === "files" ? (
            <FilesPanel
              workspaceId={workspace.id}
              entries={tree}
              currentPath={treePath}
              selectedFile={selectedFile}
              content={fileContent}
              dirty={fileContent !== savedContent}
              busy={busy}
              onNavigate={(path) => void openTreePath(path)}
              onOpen={(path) => void openFile(path)}
              onContent={setFileContent}
              onSave={() => void saveFile()}
            />
          ) : (
            <TerminalPanel
              terminals={terminals}
              terminal={terminal}
              output={terminalText}
              input={terminalInput}
              cursor={terminalCursor}
              onSelect={setTerminal}
              onCreate={() => void startTerminal()}
              onInput={setTerminalInput}
              onSend={() => void submitTerminal()}
            />
          )}
        </main>

        {/* En pantallas anchas la torre de control es una columna más: se ve
            al mismo tiempo que el hilo abierto, que es justo lo que se pidió
            ("sin perder de vista a los otros"). En pantallas chicas no cabe
            otra columna, así que se comporta como cajón sobre el contenido. */}
        {activityOpen && (
          <>
            <button
              type="button"
              aria-label="Cerrar el panel de ejecuciones"
              onClick={() => setActivityOpen(false)}
              className="fixed inset-0 z-[85] bg-slate-950/25 lg:hidden"
            />
            <div className="fixed inset-y-0 right-0 z-[86] w-full max-w-sm shadow-xl lg:static lg:z-auto lg:h-full lg:w-[21rem] lg:max-w-none lg:shrink-0 lg:shadow-none">
              <AgentActivityCenter
                runs={activityRuns}
                loading={activityLoading}
                onGo={(run) => void goToRun(run)}
                onStop={(run) => void stopRun(run)}
                onDirect={(run, texto) => void directRun(run, texto)}
                onClose={() => setActivityOpen(false)}
              />
            </div>
          </>
        )}
      </div>

      {addAction && (
        <WorkspaceModal
          kind={addAction}
          busy={busy}
          onClose={() => setAddAction(null)}
          onAuthorize={(path, name) => void authorizePath(path, name)}
          onPick={(name) => void pickWorkspace(name)}
          onClone={(input) => void cloneWorkspace(input)}
          onImage={addImageAttachment}
        />
      )}

      {diffOpen && (
        <DiffReviewModal
          loading={diffLoading}
          data={diffData}
          resolutions={diffResolutions}
          pendingPaths={diffPending}
          onAccept={acceptDiffFile}
          onReject={(path) => void rejectDiffFile(path)}
          onClose={() => setDiffOpen(false)}
        />
      )}

      {costOpen && (
        <CostSummaryModal
          loading={costLoading}
          data={costData}
          onClose={() => setCostOpen(false)}
        />
      )}

      {memoryPanelOpen && workspace && (
        <MemoryKnowledgePanel
          workspaceId={workspace.id}
          workspaceName={workspace.name}
          onClose={() => setMemoryPanelOpen(false)}
        />
      )}

      {pendingCommand && (
        <CommandConfirmModal
          comando={pendingCommand.comando}
          mensaje={pendingCommand.mensaje}
          busy={commandBusy}
          onCancel={() => setPendingCommand(null)}
          onConfirm={confirmPendingCommand}
        />
      )}

      {helpOpen && <CommandHelpModal texto={ideHelpText} onClose={() => setHelpOpen(false)} />}
    </div>
  );
}

function CommandMenu({
  matches,
  activeIndex,
  query,
  onHover,
  onSelect,
}: {
  matches: IdeCommandMatch[];
  activeIndex: number;
  query: string;
  onHover: (index: number) => void;
  onSelect: (match: IdeCommandMatch) => void;
}) {
  return (
    <div className="absolute bottom-full left-0 z-40 mb-2 max-h-80 w-96 overflow-y-auto rounded-lg border border-[#d9d9d7] bg-white p-1.5 shadow-lg dark:border-slate-700 dark:bg-slate-900">
      {matches.length === 0 ? (
        <p className="px-2.5 py-2 text-xs text-slate-400">
          {query ? `Ningún comando empieza con "/${query}".` : "No hay comandos cargados todavía."}
        </p>
      ) : (
        matches.map((match, index) => (
          <button
            key={`${match.spec.nombre}-${match.nombre}`}
            type="button"
            // onMouseDown (no onClick) para que dispare ANTES de que el
            // `onBlur` del textarea cierre el menú.
            onMouseDown={(event) => {
              event.preventDefault();
              onSelect(match);
            }}
            onMouseEnter={() => onHover(index)}
            className={`flex w-full items-start gap-2 rounded-md px-2.5 py-1.5 text-left text-xs ${
              index === activeIndex ? "bg-slate-100 dark:bg-slate-800" : "hover:bg-[#f7f7f6] dark:hover:bg-slate-800/60"
            }`}
          >
            <span className="shrink-0 font-mono font-semibold text-slate-700 dark:text-slate-200">
              /{match.nombre}
            </span>
            <span className="truncate text-slate-500 dark:text-slate-400">{match.spec.descripcion}</span>
            {match.spec.destructivo && (
              <span className="ml-auto shrink-0 rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-600 dark:bg-red-950/40 dark:text-red-300">
                Confirma
              </span>
            )}
          </button>
        ))
      )}
    </div>
  );
}

function CommandResultBanner({
  result,
  helpText,
  onDismiss,
  onUseSuggestion,
  onOpenHelp,
}: {
  result: IdeCommandResult;
  helpText: string;
  onDismiss: () => void;
  onUseSuggestion: (sugerencia: string) => void;
  onOpenHelp: () => void;
}) {
  // "/help" ya trae el texto completo del registro -- se muestra en el
  // modal dedicado en vez de aplastarlo dentro de este banner angosto.
  if (result.ok && result.comando === "help") {
    return (
      <div className="mb-2 flex items-center justify-between gap-2 border-t border-slate-200/70 px-1 pt-2">
        <Alert variant="info">Lista de comandos disponible.</Alert>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={onOpenHelp}
            className="rounded-md border border-[#d9d9d7] px-2 py-1 text-xs font-semibold hover:bg-[#f7f7f6]"
          >
            Ver /help
          </button>
          <button type="button" onClick={onDismiss} aria-label="Cerrar">
            <XIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    );
  }

  const variant = result.ok ? (result.limitado ? "info" : "success") : "error";
  const mensaje = result.mensaje || result.error || "El comando no devolvió ningún mensaje.";

  return (
    <div className="mb-2 flex items-start justify-between gap-2 border-t border-slate-200/70 px-1 pt-2">
      <div className="min-w-0 flex-1">
        <Alert variant={variant}>{mensaje}</Alert>
        {!result.ok && result.sugerencia && (
          <button
            type="button"
            onClick={() => onUseSuggestion(result.sugerencia as string)}
            className="mt-1 text-xs font-semibold text-indigo-600 underline hover:text-indigo-800 dark:text-indigo-300"
          >
            Usar {result.sugerencia}
          </button>
        )}
        {result.copy_text && (
          <p className="mt-1 text-[11px] text-slate-400">Copiado al portapapeles.</p>
        )}
      </div>
      <button type="button" onClick={onDismiss} className="shrink-0" aria-label="Cerrar">
        <XIcon className="h-3.5 w-3.5" />
      </button>
      {/* `helpText` no se usa directamente acá (vive en el modal dedicado);
          se recibe solo para que este componente no dependa de un import
          adicional del lado del padre cuando ambos evolucionen juntos. */}
      <span className="hidden">{helpText.length}</span>
    </div>
  );
}

function CommandConfirmModal({
  comando,
  mensaje,
  busy,
  onCancel,
  onConfirm,
}: {
  comando: string;
  mensaje: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[110] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="w-full max-w-sm rounded-xl border border-[#d9d9d7] bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <h2 className="text-lg font-bold">Confirmar /{comando}</h2>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{mensaje}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg border border-[#d9d9d7] px-3 py-2 text-xs font-semibold hover:bg-[#f7f7f6] disabled:opacity-50"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            {busy ? "Ejecutando…" : "Confirmar"}
          </button>
        </div>
      </section>
    </div>
  );
}

function CommandHelpModal({ texto, onClose }: { texto: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[110] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl border border-[#d9d9d7] bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-[#dededc] px-5 py-4 dark:border-slate-800">
          <h2 className="text-lg font-bold">Comandos disponibles</h2>
          <button type="button" onClick={onClose} className="rounded-md p-2 hover:bg-[#efefed] dark:hover:bg-slate-800">
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        <pre className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap p-4 font-mono text-xs text-slate-600 dark:text-slate-300">
          {texto || "Todavía no se cargó el registro de comandos."}
        </pre>
      </section>
    </div>
  );
}

function MentionMenu({
  matches,
  activeIndex,
  onHover,
  onSelect,
}: {
  matches: IdeReferenceMatch[];
  activeIndex: number;
  onHover: (index: number) => void;
  onSelect: (match: IdeReferenceMatch) => void;
}) {
  return (
    <div className="absolute bottom-full left-0 z-40 mb-2 max-h-72 w-80 overflow-y-auto rounded-lg border border-[#d9d9d7] bg-white p-1.5 shadow-lg dark:border-slate-700 dark:bg-slate-900">
      {matches.map((match, index) => (
        <button
          key={`${match.type}-${match.path}-${match.line ?? ""}`}
          type="button"
          // onMouseDown (no onClick) para que dispare ANTES de que el
          // `onBlur` del textarea cierre el menú.
          onMouseDown={(event) => {
            event.preventDefault();
            onSelect(match);
          }}
          onMouseEnter={() => onHover(index)}
          className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs ${
            index === activeIndex ? "bg-slate-100 dark:bg-slate-800" : "hover:bg-[#f7f7f6] dark:hover:bg-slate-800/60"
          }`}
        >
          <span className="truncate font-mono text-slate-700 dark:text-slate-200">{match.path}</span>
          <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide text-slate-400">
            {referenceKindLabel(match)}
          </span>
        </button>
      ))}
    </div>
  );
}

function DiffReviewModal({
  loading,
  data,
  resolutions,
  pendingPaths,
  onAccept,
  onReject,
  onClose,
}: {
  loading: boolean;
  data: IdeAgentDiff | null;
  resolutions: Record<string, "accepted" | "rejected">;
  pendingPaths: Record<string, boolean>;
  onAccept: (path: string) => void;
  onReject: (path: string) => void;
  onClose: () => void;
}) {
  const files: DiffReviewFile[] = (data?.files ?? []).map((file) => ({
    path: file.path,
    kind: file.kind,
    beforeContent: file.before_content,
    afterContent: file.after_content,
    unavailableReason: file.unavailable_reason ?? undefined,
  }));
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-[#d9d9d7] bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-[#dededc] px-5 py-4 dark:border-slate-800">
          <div>
            <h2 className="text-lg font-bold">Cambios de este turno</h2>
            <p className="text-xs text-slate-400">
              {data?.sealed === false
                ? "El turno sigue en curso: la lista puede crecer."
                : "Acepta o rechaza cada archivo por separado."}
            </p>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-2 hover:bg-[#efefed] dark:hover:bg-slate-800">
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="flex items-center justify-center py-10">
              <Spinner className="h-5 w-5" />
            </div>
          ) : (
            <DiffReview
              files={files}
              resolutions={resolutions}
              pendingPaths={pendingPaths}
              onAccept={onAccept}
              onReject={onReject}
            />
          )}
        </div>
      </section>
    </div>
  );
}

function CostSummaryModal({
  loading,
  data,
  onClose,
}: {
  loading: boolean;
  data: IdeAgentCost | null;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="w-full max-w-md rounded-xl border border-[#d9d9d7] bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold">Costo de este turno</h2>
          <button type="button" onClick={onClose} className="rounded-md p-2 hover:bg-[#efefed] dark:hover:bg-slate-800">
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Spinner className="h-5 w-5" />
          </div>
        ) : !data ? (
          <p className="mt-4 text-sm text-slate-500">No se pudo cargar el costo.</p>
        ) : (
          <div className="mt-4 space-y-3 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-[#f7f7f6] p-3 dark:bg-slate-800">
                <p className="text-[11px] text-slate-400">Duración</p>
                <p className="font-bold">{data.duracion_humana}</p>
              </div>
              <div className="rounded-lg bg-[#f7f7f6] p-3 dark:bg-slate-800">
                <p className="text-[11px] text-slate-400">Acciones</p>
                <p className="font-bold">{data.total_acciones}</p>
              </div>
              <div className="rounded-lg bg-[#f7f7f6] p-3 dark:bg-slate-800">
                <p className="text-[11px] text-slate-400">
                  Tokens{data.tokens.estimados ? " (estimados)" : ""}
                </p>
                <p className="font-bold">{data.tokens.total.toLocaleString("es")}</p>
              </div>
              <div className="rounded-lg bg-[#f7f7f6] p-3 dark:bg-slate-800">
                <p className="text-[11px] text-slate-400">Costo</p>
                <p className="font-bold">
                  {data.costo_usd !== null ? `$${data.costo_usd.toFixed(4)}` : "—"}
                </p>
              </div>
            </div>
            {data.comparacion?.fuera_de_lo_normal && (
              <Alert variant="error">{data.comparacion.motivo}</Alert>
            )}
            {data.bucles.length > 0 && (
              <Alert variant="error">
                Se detectó un patrón repetido sin avanzar: {data.bucles[0].patron} (
                {data.bucles[0].repeticiones_del_ciclo} veces).
              </Alert>
            )}
            {data.por_herramienta.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  Por herramienta
                </p>
                {data.por_herramienta.slice(0, 6).map((tool) => (
                  <div key={tool.nombre} className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-300">
                    <span className="truncate">{tool.nombre}</span>
                    <span className="shrink-0 tabular-nums text-slate-400">
                      {tool.acciones}× · {tool.porcentaje_tokens.toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            )}
            {data.advertencias.length > 0 && (
              <p className="text-[11px] italic text-slate-400">{data.advertencias[0]}</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function AddMenu({
  onClose,
  onAction,
}: {
  onClose: () => void;
  onAction: (action: "folder" | "clone" | "image" | "terminal") => void;
}) {
  return (
    <div className="absolute bottom-full left-0 z-40 mb-2 w-64 rounded-lg border border-[#d9d9d7] bg-white p-2 shadow-lg">
      {[
        ["folder", "Abrir carpeta del Mac", "Autoriza un workspace del Finder"],
        ["clone", "Clonar repositorio", "HTTPS o SSH a una carpeta local"],
        ["image", "Añadir imagen o archivo", "Contexto visual para el agente"],
        ["terminal", "Nueva terminal", "Shell real dentro del proyecto"],
      ].map(([action, title, subtitle]) => (
        <button
          key={action}
          type="button"
          onClick={() => onAction(action as "folder" | "clone" | "image" | "terminal")}
          className="w-full rounded-md px-3 py-2 text-left hover:bg-[#efefed]"
        >
          <span className="block text-sm font-semibold">{title}</span>
          <span className="block text-[11px] text-slate-400">{subtitle}</span>
        </button>
      ))}
      <button type="button" onClick={onClose} className="mt-1 w-full rounded-md px-3 py-2 text-xs text-slate-400 hover:bg-[#efefed]">
        Cerrar
      </button>
    </div>
  );
}

function FilesPanel({
  workspaceId,
  entries,
  currentPath,
  selectedFile,
  content,
  dirty,
  busy,
  onNavigate,
  onOpen,
  onContent,
  onSave,
}: {
  workspaceId: string;
  entries: IdeTreeNode[];
  currentPath: string;
  selectedFile: string | null;
  content: string;
  dirty: boolean;
  busy: boolean;
  onNavigate: (path: string) => void;
  onOpen: (path: string) => void;
  onContent: (content: string) => void;
  onSave: () => void;
}) {
  // 2.2 del plan de paridad: el árbol de archivos y el panel de búsqueda
  // (texto/significado) comparten la misma columna lateral -- una persona
  // sabe el nombre de lo que busca (árbol) o no (búsqueda), rara vez las dos
  // cosas a la vez.
  const [asideView, setAsideView] = useState<"tree" | "search">("tree");
  return (
    <section className="grid min-h-0 flex-1 grid-cols-[17rem_minmax(0,1fr)] bg-white">
      <aside className="min-h-0 overflow-y-auto border-r border-[#e6e6e4] bg-white p-3">
        <div className="mb-3 flex gap-1 text-xs">
          {([
            ["tree", "Archivos"],
            ["search", "Buscar"],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setAsideView(value)}
              className={`rounded-md px-2.5 py-1 font-semibold ${
                asideView === value
                  ? "bg-slate-900 text-white"
                  : "text-slate-500 hover:bg-[#f7f7f6]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {asideView === "search" ? (
          <SearchPanel workspaceId={workspaceId} onOpenFile={onOpen} />
        ) : (
          <>
            <div className="mb-3 flex items-center gap-2">
              <button
                type="button"
                disabled={!currentPath}
                onClick={() => onNavigate(currentPath.split("/").slice(0, -1).join("/"))}
                className="rounded-lg px-2 py-1 text-sm disabled:opacity-30"
              >
                ←
              </button>
              <span className="min-w-0 flex-1 truncate text-xs text-slate-400">/{currentPath}</span>
            </div>
            <div className="space-y-1">
              {entries.map((entry) => {
                const path = relativePath(currentPath, entry.name);
                return (
                  <button
                    key={path}
                    type="button"
                    onClick={() => (entry.is_dir ? onNavigate(path) : onOpen(path))}
                    className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm ${
                      path === selectedFile
                        ? "bg-[#f1f1ef] text-slate-950"
                        : "text-slate-600 hover:bg-[#f7f7f6]"
                    }`}
                  >
                    <FileIcon className="h-4 w-4 shrink-0" />
                    <span className="truncate">{entry.name}</span>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </aside>
      <div className="flex min-w-0 flex-col">
        <div className="flex h-12 items-center justify-between border-b border-[#dededc] bg-white px-4">
          <span className="truncate text-xs font-semibold">{selectedFile || "Abre un archivo"}</span>
          <button
            type="button"
            disabled={!dirty || busy}
            onClick={onSave}
            className="rounded-md bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-35"
          >
            Guardar
          </button>
        </div>
        {selectedFile ? (
          <textarea
            spellCheck={false}
            value={content}
            onChange={(event) => onContent(event.target.value)}
            className="min-h-0 flex-1 resize-none bg-white p-5 font-mono text-[13px] leading-6 text-slate-800 outline-none selection:bg-slate-200"
          />
        ) : (
          <div className="grid flex-1 place-items-center text-sm text-slate-400">
            Selecciona un archivo para verlo y editarlo.
          </div>
        )}
      </div>
    </section>
  );
}

function TerminalPanel({
  terminals,
  terminal,
  output,
  input,
  cursor,
  onSelect,
  onCreate,
  onInput,
  onSend,
}: {
  terminals: IdeSession[];
  terminal: IdeSession | null;
  output: string;
  input: string;
  cursor: number;
  onSelect: (session: IdeSession) => void;
  onCreate: () => void;
  onInput: (value: string) => void;
  onSend: () => void;
}) {
  const selectableTerminals = terminals.filter(
    (row) => isLive(row) || row.id === terminal?.id,
  );

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-white text-slate-800">
      <div className="flex h-12 items-center gap-2 border-b border-[#dededc] bg-white px-3">
        <select
          value={terminal?.id ?? ""}
          onChange={(event) => {
            const selected = terminals.find((row) => row.id === event.target.value);
            if (selected) onSelect(selected);
          }}
          className="rounded-md border border-[#d9d9d7] bg-white px-3 py-1.5 text-xs text-slate-700 outline-none"
        >
          <option value="">Sin terminal</option>
          {selectableTerminals.map((row) => (
            <option key={row.id} value={row.id}>
              {row.title || "Terminal"} · {row.status}
            </option>
          ))}
        </select>
        <button type="button" onClick={onCreate} className="rounded-md bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white">
          + Terminal
        </button>
        <span className="ml-auto text-[10px] text-slate-400">
          {terminal && isLive(terminal) ? "Conectada" : "Sin terminal activa"} · cursor {cursor}
        </span>
      </div>
      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-white p-5 font-mono text-[12px] leading-6 text-slate-700 selection:bg-slate-200">
        {output || "La terminal real del Mac aparecerá aquí.\n"}
      </pre>
      <div className="flex items-center border-t border-[#dededc] bg-white px-4 py-3">
        <span className="mr-3 font-mono font-semibold text-slate-700">›</span>
        <input
          value={input}
          disabled={!terminal || !isLive(terminal)}
          onChange={(event) => onInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onSend();
            }
          }}
          placeholder={terminal ? "Escribe un comando…" : "Abre una terminal"}
          className="min-w-0 flex-1 bg-transparent font-mono text-sm text-slate-800 outline-none placeholder:text-slate-400"
        />
        <button type="button" disabled={!input.trim()} onClick={onSend} className="rounded-md p-2 text-slate-700 disabled:opacity-25">
          <SendIcon className="h-4 w-4" />
        </button>
      </div>
    </section>
  );
}

function WorkspaceModal({
  kind,
  busy,
  onClose,
  onAuthorize,
  onPick,
  onClone,
  onImage,
}: {
  kind: Exclude<AddAction, null>;
  busy: boolean;
  onClose: () => void;
  onAuthorize: (path: string, name?: string) => void;
  onPick: (name?: string) => void;
  onClone: (input: { url: string; name?: string; branch?: string }) => void;
  onImage: (attachment: IdeAgentAttachment) => void;
}) {
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [manualPath, setManualPath] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const title =
    kind === "clone"
      ? "Clonar repositorio"
      : kind === "image"
        ? "Añadir contexto visual"
        : "Abrir carpeta del Mac";
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="w-full max-w-lg rounded-xl border border-[#d9d9d7] bg-white p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold">{title}</h2>
          <button type="button" onClick={onClose} className="rounded-md p-2 hover:bg-[#efefed]">
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        {kind === "image" ? (
          <label className="mt-5 block cursor-pointer rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center transition hover:bg-[#f7f7f6]">
            <span className="font-semibold">Elegir imagen</span>
            <span className="mt-2 block text-sm text-slate-500">
              PNG, JPEG, WebP o GIF. Se envía directamente al modelo con visión, no como texto.
            </span>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              className="sr-only"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                void (async () => {
                  const result = await fileToImageAttachment(file);
                  if ("error" in result) {
                    setFileError(result.error);
                    return;
                  }
                  onImage(result.attachment);
                })();
              }}
            />
            {fileError && <span className="mt-3 block text-sm text-red-600">{fileError}</span>}
          </label>
        ) : kind === "clone" ? (
          <>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              Edecán clonará el repositorio dentro del proyecto actual, sin guardar tokens incrustados en la URL.
            </p>
            <label className="mt-5 block text-sm font-semibold">URL del repositorio</label>
            <input
              autoFocus
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="https://github.com/organizacion/proyecto.git"
              className="mt-2 w-full rounded-lg border border-[#d9d9d7] bg-white px-3 py-3 text-sm outline-none focus:border-slate-500"
            />
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="block text-sm font-semibold">
                Nombre opcional
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="proyecto"
                  className="mt-2 w-full rounded-lg border border-[#d9d9d7] bg-white px-3 py-3 text-sm font-normal outline-none focus:border-slate-500"
                />
              </label>
              <label className="block text-sm font-semibold">
                Rama opcional
                <input
                  value={branch}
                  onChange={(event) => setBranch(event.target.value)}
                  placeholder="main"
                  className="mt-2 w-full rounded-lg border border-[#d9d9d7] bg-white px-3 py-3 text-sm font-normal outline-none focus:border-slate-500"
                />
              </label>
            </div>
          </>
        ) : (
          <>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              El selector nativo abre Finder. La carpeta se autoriza únicamente en tu Mac y nunca se convierte en acceso público.
            </p>
            <label className="mt-4 block text-sm font-semibold">Nombre opcional</label>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Mi proyecto"
              className="mt-2 w-full rounded-lg border border-[#d9d9d7] bg-white px-3 py-3 text-sm outline-none focus:border-slate-500"
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => onPick(name.trim() || undefined)}
              className="mt-5 w-full rounded-lg bg-slate-950 px-4 py-3 text-sm font-semibold text-white disabled:opacity-40"
            >
              Abrir Finder
            </button>
            <button
              type="button"
              onClick={() => setManualPath((value) => !value)}
              className="mt-3 text-xs font-semibold text-slate-400"
            >
              {manualPath ? "Ocultar ruta manual" : "Escribir una ruta manualmente"}
            </button>
            {manualPath && (
              <input
                autoFocus
                value={path}
                onChange={(event) => setPath(event.target.value)}
                placeholder="/Users/tu-usuario/Projects/mi-app"
                className="mt-2 w-full rounded-lg border border-[#d9d9d7] bg-white px-3 py-3 text-sm outline-none focus:border-slate-500"
              />
            )}
          </>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-md px-4 py-2 text-sm font-semibold text-slate-500 hover:bg-[#efefed]">
            Cancelar
          </button>
          {kind === "clone" && (
            <button
              type="button"
              disabled={!path.trim() || busy}
              onClick={() =>
                onClone({
                  url: path.trim(),
                  name: name.trim() || undefined,
                  branch: branch.trim() || undefined,
                })
              }
              className="rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            >
              Clonar
            </button>
          )}
          {kind === "folder" && manualPath && (
            <button
              type="button"
              disabled={!path.trim() || busy}
              onClick={() => onAuthorize(path.trim(), name.trim() || undefined)}
              className="rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            >
              Autorizar ruta
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

function EmptyWorkspace({
  connected,
  onAdd,
}: {
  connected: boolean;
  onAdd: () => void;
}) {
  return (
    <div className="grid flex-1 place-items-center p-6 text-center">
      <div className="max-w-lg">
        <span className="mx-auto grid h-16 w-16 place-items-center rounded-xl bg-slate-950 text-white">
          <CodeIcon className="h-7 w-7" />
        </span>
        <h2 className="mt-5 text-2xl font-bold">{connected ? "Abre tu primer proyecto" : "Conecta tu Mac"}</h2>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          {connected
            ? "Autoriza una carpeta local para usar archivos, terminal, Git y sesiones de ingeniería."
            : "El código y la terminal viven en tu computadora; el teléfono es un control remoto seguro."}
        </p>
        {connected && (
          <button type="button" onClick={onAdd} className="mt-5 rounded-lg bg-slate-950 px-5 py-3 text-sm font-semibold text-white">
            Abrir carpeta
          </button>
        )}
      </div>
    </div>
  );
}
