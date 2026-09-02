"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { AgentAvatar } from "@/components/workers/AgentAvatar";
import { RELATION_OPTIONS } from "@/components/workers/options";
import { BubblesIcon, PlusIcon } from "@/components/icons";
import { Alert, Button, EmptyState, Field, Input, Select, Spinner, Textarea } from "@/components/ui";
import {
  createBot,
  listBotMessages,
  listBots,
  sendBotMessage,
  type BotMessage,
  type PersistentWorker,
} from "@/lib/api-bots";
import {
  addTeamMember,
  createTeam,
  listTeamMessages,
  listTeamsTolerant,
  sendTeamMessage,
  type Team,
  type TeamMessage,
} from "@/lib/api-teams";
import { useAuth } from "@/lib/auth-context";
import type { TeamStreamEvent } from "@/lib/api-teams";

type ChatRow =
  | { kind: "bot"; id: string; title: string; subtitle: string; bot: PersistentWorker }
  | { kind: "team"; id: string; title: string; subtitle: string; team: Team };

function botLabel(bot: PersistentWorker): string {
  return bot.display_name?.trim() || bot.name;
}

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export default function BotsPage() {
  const { me } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const allowed = Boolean(me?.flags?.["agents.missions"]);

  const [bots, setBots] = useState<PersistentWorker[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [messages, setMessages] = useState<Array<BotMessage | TeamMessage>>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newRelation, setNewRelation] = useState("profesional");
  const [newTeamName, setNewTeamName] = useState("");
  const [createMode, setCreateMode] = useState<"bot" | "team" | null>(null);
  const [search, setSearch] = useState("");
  const [selectedMembers, setSelectedMembers] = useState<string[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  const selectedBotId = searchParams.get("bot");
  const selectedTeamId = searchParams.get("team");
  const selectedBot = useMemo(
    () => bots.find((b) => b.id === selectedBotId) ?? null,
    [bots, selectedBotId],
  );
  const selectedTeam = useMemo(
    () => teams.find((t) => t.id === selectedTeamId) ?? null,
    [teams, selectedTeamId],
  );

  const rows = useMemo<ChatRow[]>(() => {
    const botRows: ChatRow[] = bots.map((bot) => ({
      kind: "bot",
      id: bot.id,
      title: botLabel(bot),
      subtitle: bot.purpose?.trim() || "Chat 1:1",
      bot,
    }));
    const teamRows: ChatRow[] = teams.map((team) => ({
      kind: "team",
      id: team.id,
      title: team.name,
      subtitle: `${team.members?.length ?? 0} bot${(team.members?.length ?? 0) === 1 ? "" : "s"}`,
      team,
    }));
    return [...botRows, ...teamRows].sort((a, b) => a.title.localeCompare(b.title, "es"));
  }, [bots, teams]);

  const filteredRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (row) => row.title.toLowerCase().includes(q) || row.subtitle.toLowerCase().includes(q),
    );
  }, [rows, search]);

  const loadChats = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextBots, nextTeams] = await Promise.all([listBots(), listTeamsTolerant()]);
      setBots(nextBots);
      setTeams(nextTeams);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No pude cargar tus chats.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (allowed) void loadChats();
    else setLoading(false);
  }, [allowed, loadChats]);

  useEffect(() => {
    if (!allowed) {
      setMessages([]);
      return;
    }
    if (selectedBotId) {
      let cancelled = false;
      setLoadingMessages(true);
      void listBotMessages(selectedBotId)
        .then((rows) => {
          if (!cancelled) setMessages(rows);
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : "No pude cargar el chat.");
          }
        })
        .finally(() => {
          if (!cancelled) setLoadingMessages(false);
        });
      return () => {
        cancelled = true;
      };
    }
    if (selectedTeamId) {
      let cancelled = false;
      setLoadingMessages(true);
      void listTeamMessages(selectedTeamId)
        .then((rows) => {
          if (!cancelled) setMessages(rows);
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : "No pude cargar el chat.");
          }
        })
        .finally(() => {
          if (!cancelled) setLoadingMessages(false);
        });
      return () => {
        cancelled = true;
      };
    }
    setMessages([]);
    return undefined;
  }, [selectedBotId, selectedTeamId, allowed]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  function openBot(id: string) {
    router.replace(`/app/bots?bot=${encodeURIComponent(id)}`);
  }

  function openTeam(id: string) {
    router.replace(`/app/bots?team=${encodeURIComponent(id)}`);
  }

  async function handleCreateBot(event: React.FormEvent) {
    event.preventDefault();
    if (!newName.trim() || !newDescription.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const bot = await createBot({
        name: newName.trim(),
        purpose: newDescription.trim(),
        display_name: newName.trim(),
        relation: newRelation,
      });
      setBots((prev) => [bot, ...prev.filter((b) => b.id !== bot.id)]);
      setNewName("");
      setNewDescription("");
      setNewRelation("profesional");
      setCreateMode(null);
      openBot(bot.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No pude crear el bot.");
    } finally {
      setCreating(false);
    }
  }

  async function handleCreateTeam(event: React.FormEvent) {
    event.preventDefault();
    if (!newTeamName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const team = await createTeam(newTeamName.trim());
      for (const agentId of selectedMembers) {
        try {
          await addTeamMember(team.id, { agent_id: agentId, role: "member" });
        } catch {
          // Un miembro fallido no invalida el grupo recién creado.
        }
      }
      setTeams((prev) => [...prev, team]);
      setNewTeamName("");
      setSelectedMembers([]);
      setCreateMode(null);
      openTeam(team.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No pude crear el grupo.");
    } finally {
      setCreating(false);
    }
  }

  async function handleSend(event?: React.FormEvent) {
    event?.preventDefault();
    const clean = text.trim();
    if (!clean || sending) return;
    if (!selectedBot && !selectedTeam) return;

    setText("");
    setSending(true);
    setError(null);

    if (selectedBot) {
      const userMsg: BotMessage = {
        id: `local-user-${Date.now()}`,
        role: "user",
        text: clean,
        sender_id: "user",
        sender_name: "Tú",
      };
      const assistantId = `local-assistant-${Date.now()}`;
      const assistantMsg: BotMessage = {
        id: assistantId,
        role: "assistant",
        text: "",
        sender_id: selectedBot.id,
        sender_name: botLabel(selectedBot),
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      try {
        await sendBotMessage(selectedBot.id, clean, (event: TeamStreamEvent) => {
          if (event.type === "text_delta") {
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, text: m.text + event.text } : m)),
            );
          }
        });
        setMessages(await listBotMessages(selectedBot.id));
      } catch (err) {
        setError(err instanceof Error ? err.message : "No pude enviar el mensaje.");
        setMessages((prev) => prev.filter((m) => m.id !== assistantId && m.id !== userMsg.id));
      } finally {
        setSending(false);
      }
      return;
    }

    const userMsg: TeamMessage = {
      id: `local-${Date.now()}`,
      team_id: selectedTeam!.id,
      role: "user",
      text: clean,
      sender_id: "user",
      sender_name: "Tú",
      created_at: new Date().toISOString(),
    };
    const assistantId = `local-a-${Date.now()}`;
    const assistantMsg: TeamMessage = {
      id: assistantId,
      team_id: selectedTeam!.id,
      role: "assistant",
      text: "",
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    try {
      await sendTeamMessage(selectedTeam!.id, clean, (event: TeamStreamEvent) => {
        if (event.type === "text_delta") {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, text: m.text + event.text } : m)),
          );
        }
      });
      setMessages(await listTeamMessages(selectedTeam!.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No pude enviar el mensaje.");
      setMessages((prev) => prev.filter((m) => m.id !== assistantId && m.id !== userMsg.id));
    } finally {
      setSending(false);
    }
  }

  if (!allowed) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <EmptyState title="Bots no disponibles" description="Tu plan no incluye bots persistentes." />
      </div>
    );
  }

  const hasSelection = Boolean(selectedBot || selectedTeam);

  return (
    <div className="flex h-[calc(100dvh-0px)] min-h-0 overflow-hidden">
      <aside className="flex w-72 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <div>
            <h1 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Bots</h1>
            <p className="text-xs text-slate-500">Chats 1:1 y grupos.</p>
          </div>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setCreateMode(createMode === "bot" ? null : "bot")}
              aria-label="Crear bot"
            >
              <PlusIcon className="h-4 w-4" />
            </Button>
          </div>
        </div>

        <div className="border-b border-slate-100 p-3 dark:border-slate-800">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar chats"
            aria-label="Buscar chats"
          />
          <div className="mt-2 flex gap-2">
            <Button size="sm" variant="secondary" className="flex-1" onClick={() => setCreateMode("bot")}>
              Nuevo bot
            </Button>
            <Button size="sm" variant="secondary" className="flex-1" onClick={() => setCreateMode("team")}>
              Nuevo grupo
            </Button>
          </div>
        </div>

        {createMode === "bot" && (
          <form onSubmit={handleCreateBot} className="space-y-2 border-b border-slate-100 p-3 dark:border-slate-800">
            <Field label="Nombre">
              <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Botsito" required />
            </Field>
            <Field label="Descripción">
              <Textarea
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                placeholder="Qué hace este bot…"
                rows={3}
                required
              />
            </Field>
            <Field label="Relación">
              <Select
                value={newRelation}
                onChange={(e) => setNewRelation(e.target.value)}
                aria-label="Relación"
              >
                {RELATION_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" size="sm" loading={creating} className="w-full">
              Crear bot
            </Button>
          </form>
        )}

        {createMode === "team" && (
          <form onSubmit={handleCreateTeam} className="space-y-2 border-b border-slate-100 p-3 dark:border-slate-800">
            <Field label="Nombre del grupo">
              <Input value={newTeamName} onChange={(e) => setNewTeamName(e.target.value)} required />
            </Field>
            {bots.length > 0 && (
              <Field label="Bots en el grupo">
                <div className="max-h-32 space-y-1 overflow-y-auto">
                  {bots.map((bot) => {
                    const checked = selectedMembers.includes(bot.id);
                    return (
                      <label key={bot.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() =>
                            setSelectedMembers((prev) =>
                              checked ? prev.filter((id) => id !== bot.id) : [...prev, bot.id],
                            )
                          }
                        />
                        {botLabel(bot)}
                      </label>
                    );
                  })}
                </div>
              </Field>
            )}
            <Button type="submit" size="sm" loading={creating} className="w-full">
              Crear grupo
            </Button>
          </form>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5" />
            </div>
          ) : filteredRows.length === 0 ? (
            <p className="px-2 py-4 text-xs text-slate-500">Crea tu primer bot o grupo con los botones de arriba.</p>
          ) : (
            <ul className="space-y-1">
              {filteredRows.map((row) => {
                const active =
                  (row.kind === "bot" && row.id === selectedBotId) ||
                  (row.kind === "team" && row.id === selectedTeamId);
                return (
                  <li key={`${row.kind}-${row.id}`}>
                    <button
                      type="button"
                      onClick={() => (row.kind === "bot" ? openBot(row.id) : openTeam(row.id))}
                      className={cx(
                        "flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors",
                        active
                          ? "bg-brand-50 text-brand-800 dark:bg-brand-900/40 dark:text-brand-200"
                          : "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800",
                      )}
                    >
                      {row.kind === "bot" ? (
                        <AgentAvatar name={row.title} avatar={row.bot.avatar} size="sm" showOnline={active} />
                      ) : (
                        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-50 text-brand-700 dark:bg-brand-900/40">
                          <BubblesIcon className="h-4 w-4" />
                        </span>
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{row.title}</span>
                        <span className="block truncate text-xs text-slate-500">{row.subtitle}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col bg-slate-50 dark:bg-slate-950">
        {error && (
          <div className="px-4 pt-3">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        {!hasSelection ? (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState
              title="Elige o crea un chat"
              description="Abre un bot 1:1 o un grupo del panel izquierdo. Cada conversación usa turnos reales del agente."
            />
          </div>
        ) : (
          <>
            <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
              {selectedBot ? (
                <>
                  <AgentAvatar name={botLabel(selectedBot)} avatar={selectedBot.avatar} size="md" />
                  <div className="min-w-0">
                    <h2 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                      {botLabel(selectedBot)}
                    </h2>
                    <p className="truncate text-xs text-slate-500">{selectedBot.purpose}</p>
                  </div>
                </>
              ) : selectedTeam ? (
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                    {selectedTeam.name}
                  </h2>
                  <p className="truncate text-xs text-slate-500">
                    {selectedTeam.members?.length ?? 0} bot
                    {(selectedTeam.members?.length ?? 0) === 1 ? "" : "s"} en el grupo
                  </p>
                </div>
              ) : null}
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
              {loadingMessages ? (
                <div className="flex justify-center py-12">
                  <Spinner className="h-5 w-5" />
                </div>
              ) : messages.length === 0 ? (
                <p className="py-12 text-center text-sm text-slate-500">
                  Todavía no hay mensajes. Escribe abajo para arrancar.
                </p>
              ) : (
                <div className="mx-auto flex max-w-2xl flex-col gap-3">
                  {messages.map((msg) => {
                    const isUser = msg.sender_id === "user" || msg.role === "user";
                    return (
                      <div key={msg.id} className={cx("flex", isUser ? "justify-end" : "justify-start")}>
                        <div
                          className={cx(
                            "max-w-[85%] rounded-[18px] px-3 py-2 text-sm leading-6",
                            isUser
                              ? "bg-brand-600 text-white"
                              : "border border-slate-200 bg-white text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100",
                          )}
                        >
                          {!isUser && msg.sender_name && (
                            <p className="mb-1 text-[11px] font-medium text-slate-500">{msg.sender_name}</p>
                          )}
                          <p className="whitespace-pre-wrap">{msg.text}</p>
                        </div>
                      </div>
                    );
                  })}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>

            <form
              onSubmit={handleSend}
              className="border-t border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
            >
              <div className="mx-auto flex max-w-2xl gap-2">
                <Textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={
                    selectedBot
                      ? `Escribe a ${botLabel(selectedBot)}…`
                      : selectedTeam
                        ? `Escribe al grupo ${selectedTeam.name}…`
                        : "Escribe…"
                  }
                  rows={2}
                  className="min-h-[44px] flex-1 resize-none"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void handleSend();
                    }
                  }}
                />
                <Button type="submit" loading={sending} disabled={!text.trim()}>
                  Enviar
                </Button>
              </div>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
