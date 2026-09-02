/**
 * "Mensajes entre agentes" (`GET/POST /v1/agents/messages`): una lista de
 * mensajes directos entre compañeros y un compositor (agente → agente). Si el
 * backend todavía no expone la ruta (404), se muestra "Próximamente" — nunca
 * un envío fingido como exitoso.
 */

"use client";

import { useEffect, useState } from "react";

import { SendIcon } from "@/components/icons";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  Field,
  Select,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  listAgentMessages,
  sendAgentMessage,
  isNotFound,
  type AgentMessage,
} from "@/lib/api-agent-messages";
import type { PersistentWorker } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

function workerLabel(worker: PersistentWorker): string {
  return worker.display_name?.trim() || worker.name;
}

export function AgentMessagesSection({ workers }: { workers: PersistentWorker[] }) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [upcoming, setUpcoming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listAgentMessages()
      .then((next) => {
        if (!cancelled) setMessages(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (isNotFound(err)) {
          setUpcoming(true);
        } else {
          setError(err instanceof Error ? err.message : "No se pudieron cargar los mensajes.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Selecciones por defecto cuando ya se conoce el roster.
  useEffect(() => {
    if (workers.length === 0) return;
    const ids = new Set(workers.map((w) => w.id));
    if (!fromId || !ids.has(fromId)) setFromId(workers[0]?.id ?? "");
    if (!toId || !ids.has(toId)) setToId(workers[1]?.id ?? workers[0]?.id ?? "");
  }, [workers, fromId, toId]);

  const workerById = new Map(workers.map((w) => [w.id, w]));

  function agentLabel(id: string | null): string {
    if (!id) return "—";
    const worker = workerById.get(id);
    return worker ? workerLabel(worker) : id.slice(0, 8);
  }

  async function handleSend(event: React.FormEvent) {
    event.preventDefault();
    if (!fromId || !toId || !content.trim()) return;
    if (fromId === toId) {
      setError("Elige dos compañeros distintos.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      const created = await sendAgentMessage({
        from_agent_id: fromId,
        to_agent_id: toId,
        content: content.trim(),
      });
      setMessages((prev) => [created, ...prev]);
      setContent("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo enviar el mensaje.");
    } finally {
      setSending(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Mensajes entre agentes"
        description="Mensajes directos entre compañeros, para coordinar traspasos sin pasar por el chat."
      />
      <CardBody>
        {error && <Alert variant="error">{error}</Alert>}

        {loading ? (
          <div className="flex justify-center py-4">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : upcoming ? (
          <p className="text-sm text-slate-400 dark:text-slate-500">Próximamente</p>
        ) : (
          <>
            {workers.length >= 2 ? (
              <form onSubmit={handleSend} className="mb-4 space-y-3 rounded-lg border border-slate-100 p-3 dark:border-slate-800">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="De" htmlFor="agm-from">
                    <Select id="agm-from" value={fromId} onChange={(e) => setFromId(e.target.value)}>
                      {workers.map((w) => (
                        <option key={w.id} value={w.id}>
                          {workerLabel(w)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Para" htmlFor="agm-to">
                    <Select id="agm-to" value={toId} onChange={(e) => setToId(e.target.value)}>
                      {workers.map((w) => (
                        <option key={w.id} value={w.id}>
                          {workerLabel(w)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <Field label="Mensaje" htmlFor="agm-content">
                  <Textarea
                    id="agm-content"
                    rows={2}
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    placeholder="Pásale esto al analista…"
                  />
                </Field>
                <div className="flex justify-end">
                  <Button
                    type="submit"
                    size="sm"
                    loading={sending}
                    disabled={!fromId || !toId || !content.trim()}
                  >
                    <SendIcon className="h-3.5 w-3.5" />
                    Enviar
                  </Button>
                </div>
              </form>
            ) : (
              <p className="mb-4 text-xs text-slate-400 dark:text-slate-500">
                Necesitas al menos dos compañeros para mandar mensajes entre ellos.
              </p>
            )}

            {messages.length === 0 ? (
              <p className="text-sm text-slate-400 dark:text-slate-500">
                Sin mensajes entre agentes todavía.
              </p>
            ) : (
              <ul className="space-y-2">
                {messages.map((message) => (
                  <li
                    key={message.id}
                    className="rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
                  >
                    <p className="text-sm text-slate-700 dark:text-slate-200">{message.content}</p>
                    <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                      {agentLabel(message.from_agent_id)} → {agentLabel(message.to_agent_id)}
                      {message.created_at ? ` · ${formatDateTime(message.created_at)}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}
