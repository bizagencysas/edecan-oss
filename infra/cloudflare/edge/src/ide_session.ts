import { DurableObject } from "cloudflare:workers";

const MAX_EVENTS_PER_SESSION = 1_000;
const EVENT_RETENTION_DAYS = 7;
const SESSION_IDLE_TTL_MS = 7 * 24 * 60 * 60 * 1_000;

export const IDE_SESSION_STATUSES = [
  "idle",
  "queued",
  "running",
  "waiting",
  "completed",
  "failed",
  "cancelled",
  "disconnected"
] as const;

export const IDE_EVENT_TYPES = [
  "session.created",
  "session.status",
  "agent.progress",
  "agent.message",
  "file.opened",
  "file.changed",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "session.completed",
  "session.failed",
  "session.cancelled",
  "desktop.heartbeat",
  "desktop.disconnected"
] as const;

type IdeSessionStatus = typeof IDE_SESSION_STATUSES[number];
type IdeEventType = typeof IDE_EVENT_TYPES[number];

export interface IdeStateInput {
  update_id: string;
  status: IdeSessionStatus;
  workspace_label?: string | null;
  active_file?: string | null;
  branch?: string | null;
  summary?: string | null;
  progress?: number | null;
  desktop_connected: boolean;
}

export interface IdeEventPayload {
  message?: string;
  progress?: number;
  status?: IdeSessionStatus;
  path?: string;
  tool?: string;
  phase?: string;
}

export interface IdeEventInput {
  event_id: string;
  type: IdeEventType;
  payload: IdeEventPayload;
}

export interface IdeSessionSnapshot {
  revision: number;
  status: IdeSessionStatus;
  workspace_label: string | null;
  active_file: string | null;
  branch: string | null;
  summary: string | null;
  progress: number | null;
  desktop_connected: boolean;
  updated_at: string;
  cursor: number;
}

export interface IdeSessionEvent {
  seq: number;
  event_id: string;
  type: IdeEventType;
  payload: IdeEventPayload;
  created_at: string;
}

interface StateRow {
  [key: string]: SqlStorageValue;
  revision: number;
  status: string;
  workspace_label: string | null;
  active_file: string | null;
  branch: string | null;
  summary: string | null;
  progress: number | null;
  desktop_connected: number;
  updated_at: string;
}

interface EventRow {
  [key: string]: SqlStorageValue;
  seq: number;
  event_id: string;
  type: string;
  payload: string;
  created_at: string;
}

interface SequenceRow {
  [key: string]: SqlStorageValue;
  cursor: number | null;
}

interface ReceiptRow {
  [key: string]: SqlStorageValue;
  revision: number;
}

function parseEventRow(row: EventRow): IdeSessionEvent {
  return {
    seq: row.seq,
    event_id: row.event_id,
    type: row.type as IdeEventType,
    payload: JSON.parse(row.payload) as IdeEventPayload,
    created_at: row.created_at
  };
}

/**
 * Durable, private projection of an IDE session.
 *
 * This object deliberately has no terminal, filesystem, Git or agent-execution
 * method. The desktop remains the only execution authority; Cloudflare stores
 * only a small allowlisted projection that mobile clients can resume by cursor.
 */
export class IdeSessionContinuity extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.ctx.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS ide_session_state (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        revision INTEGER NOT NULL,
        status TEXT NOT NULL,
        workspace_label TEXT,
        active_file TEXT,
        branch TEXT,
        summary TEXT,
        progress REAL,
        desktop_connected INTEGER NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS ide_session_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS ide_session_events_created_at
        ON ide_session_events(created_at);
      CREATE TABLE IF NOT EXISTS ide_state_receipts (
        update_id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS ide_state_receipts_created_at
        ON ide_state_receipts(created_at);
    `);
  }

  snapshot(): IdeSessionSnapshot {
    const rows = this.ctx.storage.sql.exec<StateRow>(`
      SELECT revision, status, workspace_label, active_file, branch, summary,
             progress, desktop_connected, updated_at
      FROM ide_session_state
      WHERE singleton = 1
    `).toArray();
    const cursor = this.cursor();
    const state = rows[0];
    if (!state) {
      return {
        revision: 0,
        status: "idle",
        workspace_label: null,
        active_file: null,
        branch: null,
        summary: null,
        progress: null,
        desktop_connected: false,
        updated_at: new Date(0).toISOString(),
        cursor
      };
    }
    return {
      revision: state.revision,
      status: state.status as IdeSessionStatus,
      workspace_label: state.workspace_label,
      active_file: state.active_file,
      branch: state.branch,
      summary: state.summary,
      progress: state.progress,
      desktop_connected: state.desktop_connected === 1,
      updated_at: state.updated_at,
      cursor
    };
  }

  async replaceState(input: IdeStateInput): Promise<IdeSessionSnapshot> {
    const now = new Date().toISOString();
    const existingReceipt = this.ctx.storage.sql.exec<ReceiptRow>(
      "SELECT revision FROM ide_state_receipts WHERE update_id = ?",
      input.update_id
    ).toArray()[0];
    if (existingReceipt) {
      return this.snapshot();
    }

    this.ctx.storage.transactionSync(() => {
      const revision = (
        this.ctx.storage.sql.exec<StateRow>(
          "SELECT revision FROM ide_session_state WHERE singleton = 1"
        ).toArray()[0]?.revision ?? 0
      ) + 1;
      this.ctx.storage.sql.exec(
        `INSERT INTO ide_session_state (
           singleton, revision, status, workspace_label, active_file, branch,
           summary, progress, desktop_connected, updated_at
         ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(singleton) DO UPDATE SET
           revision = excluded.revision,
           status = excluded.status,
           workspace_label = excluded.workspace_label,
           active_file = excluded.active_file,
           branch = excluded.branch,
           summary = excluded.summary,
           progress = excluded.progress,
           desktop_connected = excluded.desktop_connected,
           updated_at = excluded.updated_at`,
        revision,
        input.status,
        input.workspace_label ?? null,
        input.active_file ?? null,
        input.branch ?? null,
        input.summary ?? null,
        input.progress ?? null,
        input.desktop_connected ? 1 : 0,
        now
      );
      this.ctx.storage.sql.exec(
        `INSERT INTO ide_state_receipts (update_id, revision, created_at)
         VALUES (?, ?, ?)`,
        input.update_id,
        revision,
        now
      );
      this.prune(now);
    });
    await this.ctx.storage.setAlarm(Date.now() + SESSION_IDLE_TTL_MS);
    return this.snapshot();
  }

  async appendEvent(input: IdeEventInput): Promise<{
    event: IdeSessionEvent;
    duplicate: boolean;
  }> {
    const duplicate = this.ctx.storage.sql.exec<EventRow>(
      `SELECT seq, event_id, type, payload, created_at
       FROM ide_session_events
       WHERE event_id = ?`,
      input.event_id
    ).toArray()[0];
    if (duplicate) {
      return { event: parseEventRow(duplicate), duplicate: true };
    }

    const now = new Date().toISOString();
    let event: IdeSessionEvent | undefined;
    this.ctx.storage.transactionSync(() => {
      this.ctx.storage.sql.exec(
        `INSERT INTO ide_session_events (event_id, type, payload, created_at)
         VALUES (?, ?, ?, ?)`,
        input.event_id,
        input.type,
        JSON.stringify(input.payload),
        now
      );
      const inserted = this.ctx.storage.sql.exec<EventRow>(
        `SELECT seq, event_id, type, payload, created_at
         FROM ide_session_events
         WHERE event_id = ?`,
        input.event_id
      ).one();
      event = parseEventRow(inserted);
      this.prune(now);
    });
    if (!event) {
      throw new Error("event_not_persisted");
    }
    await this.ctx.storage.setAlarm(Date.now() + SESSION_IDLE_TTL_MS);
    return { event, duplicate: false };
  }

  alarm(): void {
    this.ctx.storage.transactionSync(() => {
      this.ctx.storage.sql.exec("DELETE FROM ide_session_events");
      this.ctx.storage.sql.exec("DELETE FROM ide_session_state");
      this.ctx.storage.sql.exec("DELETE FROM ide_state_receipts");
    });
  }

  events(after: number, limit: number): IdeSessionEvent[] {
    return this.ctx.storage.sql.exec<EventRow>(
      `SELECT seq, event_id, type, payload, created_at
       FROM ide_session_events
       WHERE seq > ?
       ORDER BY seq ASC
       LIMIT ?`,
      after,
      limit
    ).toArray().map(parseEventRow);
  }

  stream(after: number, limit: number): Response {
    const snapshot = this.snapshot();
    const events = this.events(after, limit);
    const encoder = new TextEncoder();
    const chunks = [
      "retry: 1500\n\n",
      `event: ide.snapshot\ndata: ${JSON.stringify(snapshot)}\n\n`,
      ...events.map((event) => (
        `id: ${event.seq}\nevent: ide.event\ndata: ${JSON.stringify(event)}\n\n`
      )),
      `event: ide.sync\ndata: ${JSON.stringify({
        cursor: events.at(-1)?.seq ?? after,
        has_more: events.length === limit
      })}\n\n`
    ];
    return new Response(
      new ReadableStream({
        start(controller) {
          for (const chunk of chunks) {
            controller.enqueue(encoder.encode(chunk));
          }
          controller.close();
        }
      }),
      {
        headers: {
          "cache-control": "no-store, max-age=0",
          "content-type": "text/event-stream; charset=utf-8",
          "x-accel-buffering": "no"
        }
      }
    );
  }

  private cursor(): number {
    return this.ctx.storage.sql.exec<SequenceRow>(
      "SELECT MAX(seq) AS cursor FROM ide_session_events"
    ).one().cursor ?? 0;
  }

  private prune(now: string): void {
    const cutoff = new Date(
      Date.parse(now) - EVENT_RETENTION_DAYS * 24 * 60 * 60 * 1_000
    ).toISOString();
    this.ctx.storage.sql.exec(
      "DELETE FROM ide_session_events WHERE created_at < ?",
      cutoff
    );
    this.ctx.storage.sql.exec(
      `DELETE FROM ide_session_events
       WHERE seq <= COALESCE(
         (SELECT MAX(seq) - ? FROM ide_session_events),
         -1
       )`,
      MAX_EVENTS_PER_SESSION
    );
    this.ctx.storage.sql.exec(
      "DELETE FROM ide_state_receipts WHERE created_at < ?",
      cutoff
    );
  }
}
