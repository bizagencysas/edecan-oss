/**
 * Cola de salida del compositor de Forge Studio: los mensajes que la persona
 * ya mandó pero que el agente todavía no recogió.
 *
 * POR QUÉ EXISTE: hasta ahora, escribirle al agente mientras trabajaba
 * devolvía un error y el texto se perdía ("Ya hay un turno en curso en esta
 * conversación"). La forma de trabajar que se pidió es la contraria: mandar la
 * idea nueva en el momento en que aparece y que llegue en la vuelta siguiente,
 * sin cortar lo que el agente está haciendo. Para que eso no termine en el
 * mismo mensaje mandado tres veces, la persona tiene que VER en qué estado
 * quedó cada uno: encolado → entregado.
 *
 * Este archivo es SOLO el estado: sin React y sin red, recibe una lista y
 * devuelve otra. Así el ciclo completo (encolar, entregar, no entregar,
 * fallar) se prueba sin navegador — ver `apps/web/ide-cola.test.mjs`.
 *
 * CONTRATO REAL CON EL MOTOR (verificado contra
 * `apps/companion/edecan_companion/ide_sessions.py` y
 * `apps/api/edecan_api/routers/ide.py`, no supuesto):
 *
 * - `POST /v1/ide/agents` responde 200 con la sesión de siempre y, SOLO si el
 *   mensaje quedó esperando, un objeto `queued` con `id`, `position`
 *   (1-based), `pending` y `max_pending`.
 * - Por el stream de la sesión llegan tres eventos, todos con el TEXTO del
 *   mensaje (no con su id): `user_queued` al recibirlo, `user_delivered`
 *   cuando el agente lo lee entre dos vueltas de su ciclo, y
 *   `user_undelivered` cuando el turno terminó sin poder leerlo ni convertirlo
 *   en el turno siguiente.
 * - Cuando el turno termina y la sesión sigue siendo continuable, lo encolado
 *   se PROMUEVE a turno siguiente: ahí no hay `user_delivered`, hay un evento
 *   `user` normal con los mensajes pendientes unidos por una línea en blanco
 *   (`_entregar_pendientes_al_cerrar`).
 *
 * Por eso la reconciliación de acá es por texto y no por id: el id del motor
 * existe, pero no viaja en ningún evento.
 */

/** El motor confirma que recibió el mensaje y quedó esperando turno. */
export const IDE_EVENTO_MENSAJE_ENCOLADO = "user_queued";

/** El agente ya lo leyó, entre dos vueltas de su ciclo de herramientas. */
export const IDE_EVENTO_MENSAJE_ENTREGADO = "user_delivered";

/** El turno terminó sin poder entregarlo: hay que volver a mandarlo. */
export const IDE_EVENTO_MENSAJE_NO_ENTREGADO = "user_undelivered";

/** Separador con el que el motor une varios pendientes al promoverlos a un turno. */
const UNION_DE_PROMOVIDOS = "\n\n";

/**
 * `MAX_EVENT_TEXT_CHARS` de `ide_sessions.py`: el motor recorta el texto de
 * cada evento a este largo. Un mensaje más largo que esto vuelve recortado y
 * la comparación exacta fallaría, dejando la ficha pegada en "en cola".
 */
const RECORTE_DEL_MOTOR = 8_000;

/**
 * `enviando` dura lo que dura el POST; `encolado` es el estado que importa
 * (el mensaje existe en el motor pero el agente sigue en el turno anterior);
 * `entregado` se borra solo a los pocos segundos porque a partir de ahí el
 * mensaje ya se ve como parte del hilo; `fallido` NO se borra solo — es el
 * único estado que necesita que la persona haga algo (volver a mandarlo).
 */
export type IdeEstadoDeMensaje = "enviando" | "encolado" | "entregado" | "fallido";

export interface IdeMensajeEnCola {
  /** Id del navegador, estable desde el instante en que se presiona Enviar. */
  localId: string;
  /** Conversación a la que va dirigido (puede no ser la que está abierta). */
  conversationId: string;
  /** Sesión de agente que lo recibió; se conoce recién al responder el POST. */
  sessionId: string | null;
  texto: string;
  estado: IdeEstadoDeMensaje;
  /** Cuántos mensajes hay por delante (0 = es el próximo). */
  posicion: number | null;
  creadoEn: number;
  resueltoEn: number | null;
  /**
   * Cursor del stream de la sesión en el momento de encolar. Se usa para no
   * confundir el eco del mensaje que ya estaba corriendo con el nuestro
   * cuando ambos tienen el mismo texto (ver `conEventos`).
   */
  cursorAlEncolar: number | null;
  error?: string;
}

/** Forma mínima de `IdeSessionEvent` que necesita este archivo (evita importar `api-ide.ts`, que abre red). */
export interface IdeEventoDeSesion {
  cursor: number;
  type: string;
  text: string;
}

/** Respuesta de `POST /v1/ide/agents` ya normalizada. */
export interface IdeRespuestaDeEnvio {
  sessionId: string | null;
  encolado: boolean;
  /** Cuántos hay por delante (0 = es el próximo), ya convertido desde el `position` 1-based del motor. */
  posicion: number | null;
}

/** El objeto `queued` tal como lo arma `routers/ide.py::post_agent`. */
export interface IdeColaDelMotor {
  id?: string | null;
  /** 1-based: 1 = es el próximo que va a leer el agente. */
  position?: number | null;
  pending?: number | null;
  max_pending?: number | null;
  queued_at?: string | null;
}

/** Campos que puede traer la respuesta cruda del envío, además de la sesión. */
export interface IdeRespuestaCrudaDeEnvio {
  id?: string | null;
  queued?: IdeColaDelMotor | boolean | null;
}

const TTL_VISIBLE_MS = 5_000;

/** Id local: `crypto.randomUUID` cuando existe, contador con marca de tiempo si no (jsdom viejo, Safari antiguo). */
let contadorLocal = 0;
export function nuevoIdLocal(): string {
  const cripto = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (cripto && typeof cripto.randomUUID === "function") return cripto.randomUUID();
  contadorLocal += 1;
  return `local-${Date.now()}-${contadorLocal}`;
}

export function nuevoMensajeLocal(input: {
  localId: string;
  conversationId: string;
  texto: string;
  creadoEn: number;
  cursorAlEncolar?: number | null;
}): IdeMensajeEnCola {
  return {
    localId: input.localId,
    conversationId: input.conversationId,
    sessionId: null,
    texto: input.texto,
    estado: "enviando",
    posicion: null,
    creadoEn: input.creadoEn,
    resueltoEn: null,
    cursorAlEncolar: input.cursorAlEncolar ?? null,
  };
}

/**
 * Normaliza la respuesta del envío.
 *
 * `position` del motor es 1-based ("sos el primero de la cola" = 1) y acá se
 * guarda como "cuántos hay por delante", que es lo que la ficha dice en
 * palabras. Se tolera además un `queued: true` pelado por si el router alguna
 * vez responde el booleano sin el detalle: la posición se puede desconocer, la
 * existencia de la cola no.
 */
export function normalizarRespuestaDeEnvio(
  cruda: IdeRespuestaCrudaDeEnvio | null | undefined,
): IdeRespuestaDeEnvio {
  const sessionId = typeof cruda?.id === "string" && cruda.id ? cruda.id : null;
  const cola = cruda?.queued;
  if (cola === true) return { sessionId, encolado: true, posicion: null };
  if (!cola || typeof cola !== "object") return { sessionId, encolado: false, posicion: null };
  const position = typeof cola.position === "number" && Number.isFinite(cola.position)
    ? cola.position
    : null;
  return {
    sessionId,
    encolado: true,
    posicion: position === null ? null : Math.max(0, position - 1),
  };
}

function reemplazar(
  cola: IdeMensajeEnCola[],
  localId: string,
  cambio: (mensaje: IdeMensajeEnCola) => IdeMensajeEnCola | null,
): IdeMensajeEnCola[] {
  const siguiente: IdeMensajeEnCola[] = [];
  for (const mensaje of cola) {
    if (mensaje.localId !== localId) {
      siguiente.push(mensaje);
      continue;
    }
    const resultado = cambio(mensaje);
    if (resultado) siguiente.push(resultado);
  }
  return siguiente;
}

/**
 * Aplica la respuesta del envío. Si el turno arrancó de una (no había nada
 * corriendo), el mensaje SALE de la cola sin dejar ficha: ya se ve en el hilo
 * como un turno normal y una ficha "entregado" ahí sería ruido. La ficha es
 * para lo que el hilo todavía no muestra.
 */
export function conRespuestaDeEnvio(
  cola: IdeMensajeEnCola[],
  localId: string,
  respuesta: IdeRespuestaDeEnvio,
): IdeMensajeEnCola[] {
  return reemplazar(cola, localId, (mensaje) => {
    if (!respuesta.encolado) return null;
    return {
      ...mensaje,
      estado: "encolado",
      sessionId: respuesta.sessionId ?? mensaje.sessionId,
      posicion: respuesta.posicion,
    };
  });
}

export function conFallo(
  cola: IdeMensajeEnCola[],
  localId: string,
  error: string,
): IdeMensajeEnCola[] {
  return reemplazar(cola, localId, (mensaje) => ({ ...mensaje, estado: "fallido", error }));
}

export function sinMensaje(cola: IdeMensajeEnCola[], localId: string): IdeMensajeEnCola[] {
  return cola.filter((mensaje) => mensaje.localId !== localId);
}

/**
 * ¿Este evento habla de este mensaje? El motor manda el texto, no un id, así
 * que la comparación es por texto — con dos concesiones a cómo lo emite:
 * recorta a `MAX_EVENT_TEXT_CHARS`, y al promover pendientes los une con una
 * línea en blanco en un solo evento `user`.
 */
function eventoHablaDe(textoDelEvento: string, propio: string): boolean {
  const evento = textoDelEvento.trim();
  if (!evento || !propio) return false;
  if (evento === propio) return true;
  if (evento.length >= RECORTE_DEL_MOTOR && propio.startsWith(evento)) return true;
  return evento
    .split(UNION_DE_PROMOVIDOS)
    .map((parte) => parte.trim())
    .includes(propio);
}

/**
 * Cruza la cola con los eventos nuevos de UNA sesión y resuelve las fichas con
 * lo que el motor ya dijo de cada mensaje.
 *
 * Tres desenlaces, los tres del contrato de `ide_sessions.py`:
 *  - `user_delivered` → entregado (el agente lo leyó).
 *  - `user_undelivered` → fallido, con el texto a mano para reenviarlo: el
 *    turno terminó sin leerlo y nadie más lo va a hacer. Perderlo en silencio
 *    sería peor que haberlo rechazado de entrada.
 *  - un evento `user` posterior al cursor de encolado → entregado también: es
 *    lo encolado promovido a turno siguiente cuando el turno cerró limpio.
 *
 * El cursor de encolado es lo que evita que el eco del mensaje que YA estaba
 * corriendo (mismo texto, mandado antes) dé por entregado al nuevo.
 */
export function conEventos(
  cola: IdeMensajeEnCola[],
  sessionId: string,
  eventos: ReadonlyArray<IdeEventoDeSesion>,
  ahoraMs: number,
): IdeMensajeEnCola[] {
  if (!cola.length || !eventos.length) return cola;

  const entregados: Array<{ cursor: number; texto: string }> = [];
  const noEntregados: Array<{ cursor: number; texto: string }> = [];

  for (const evento of eventos) {
    const texto = (evento.text ?? "").trim();
    if (!texto) continue;
    if (evento.type === IDE_EVENTO_MENSAJE_ENTREGADO || evento.type === "user") {
      entregados.push({ cursor: evento.cursor, texto });
    } else if (evento.type === IDE_EVENTO_MENSAJE_NO_ENTREGADO) {
      noEntregados.push({ cursor: evento.cursor, texto });
    }
  }
  if (!entregados.length && !noEntregados.length) return cola;

  let cambio = false;
  const siguiente = cola.map((mensaje) => {
    if (mensaje.estado !== "encolado" || mensaje.sessionId !== sessionId) return mensaje;
    const propio = mensaje.texto.trim();
    const esNuestro = (evento: { cursor: number; texto: string }) =>
      eventoHablaDe(evento.texto, propio) &&
      (mensaje.cursorAlEncolar === null || evento.cursor > mensaje.cursorAlEncolar);
    if (entregados.some(esNuestro)) {
      cambio = true;
      return { ...mensaje, estado: "entregado" as const, resueltoEn: ahoraMs, posicion: null };
    }
    if (noEntregados.some(esNuestro)) {
      cambio = true;
      return {
        ...mensaje,
        estado: "fallido" as const,
        posicion: null,
        error: "El turno terminó antes de leerlo. Vuelve a mandarlo.",
      };
    }
    return mensaje;
  });
  return cambio ? siguiente : cola;
}

/**
 * Suelta las fichas cuya sesión ya terminó sin que llegara ningún evento sobre
 * ellas.
 *
 * Por qué hace falta: el hilo abierto se lee cada segundo y ahí `conEventos`
 * resuelve todo, pero a un agente de OTRA conversación se le puede dirigir
 * desde la torre de control, y de esa sesión solo se conoce el estado. Sin
 * esto, esa ficha diría "en cola" para siempre y el contador de la torre
 * mentiría hasta recargar la página.
 *
 * No se pierde nada al soltarla: el motor garantiza que ese mensaje quedó
 * escrito en el hilo de esa conversación (`user_delivered` o
 * `user_undelivered`, ver `_entregar_pendientes_al_cerrar`), y el hilo lo
 * muestra al abrirlo. Solo se sueltan las sesiones que se SABEN terminadas —
 * una que no aparece en la lista todavía se deja quieta.
 */
export function sinFichasDeSesionesTerminadas(
  cola: IdeMensajeEnCola[],
  sesionesVivas: ReadonlySet<string>,
  sesionesConocidas: ReadonlySet<string>,
  /**
   * Conversación abierta: sus fichas NO se sueltan por acá. De esa sí se leen
   * los eventos, y esta regla (más lenta) le ganaría la carrera al
   * `user_undelivered` que tiene que pintar la ficha en rojo con el texto para
   * recuperarlo.
   */
  conversacionAbierta: string | null = null,
): IdeMensajeEnCola[] {
  const siguiente = cola.filter((mensaje) => {
    if (mensaje.estado !== "encolado" || !mensaje.sessionId) return true;
    if (conversacionAbierta && mensaje.conversationId === conversacionAbierta) return true;
    if (!sesionesConocidas.has(mensaje.sessionId)) return true;
    return sesionesVivas.has(mensaje.sessionId);
  });
  return siguiente.length === cola.length ? cola : siguiente;
}

/** Saca las fichas ya entregadas después de unos segundos: cumplieron su función de aviso. */
export function sinResueltosViejos(
  cola: IdeMensajeEnCola[],
  ahoraMs: number,
  ttlMs: number = TTL_VISIBLE_MS,
): IdeMensajeEnCola[] {
  const siguiente = cola.filter((mensaje) => {
    if (mensaje.estado !== "entregado") return true;
    if (mensaje.resueltoEn === null) return true;
    return ahoraMs - mensaje.resueltoEn < ttlMs;
  });
  return siguiente.length === cola.length ? cola : siguiente;
}

export function mensajesDeConversacion(
  cola: IdeMensajeEnCola[],
  conversationId: string | null,
): IdeMensajeEnCola[] {
  if (!conversationId) return [];
  return cola.filter((mensaje) => mensaje.conversationId === conversationId);
}

/** Cuántos mensajes siguen esperando turno (los resueltos no cuentan). */
export function contarEnEspera(
  cola: IdeMensajeEnCola[],
  conversationId?: string | null,
): number {
  return cola.filter(
    (mensaje) =>
      (mensaje.estado === "enviando" || mensaje.estado === "encolado") &&
      (!conversationId || mensaje.conversationId === conversationId),
  ).length;
}
