"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ChatIcon } from "@/components/icons";
import { listMemory, listReminders } from "@/lib/api";
import { getMissionsInbox } from "@/lib/api-misiones";

const FALLBACK_STARTERS = [
  "Organiza mis pendientes para hoy",
  "Busca esto en la web y cita las fuentes",
  "Revisa este documento y dime lo importante",
  "Recuérdame pagar mañana",
];

export function ChatHome({
  onPickStarter,
}: {
  onPickStarter: (text: string) => void;
}) {
  const [suggestions, setSuggestions] = useState<string[]>(FALLBACK_STARTERS);
  const [resume, setResume] = useState<{ href: string; label: string }[]>([]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([listReminders(), getMissionsInbox(), listMemory(undefined, 4)]).then(
      ([reminders, inbox, memory]) => {
        if (cancelled) return;
        const next: string[] = [];
        const links: { href: string; label: string }[] = [];

        if (reminders.status === "fulfilled") {
          const pending = reminders.value.filter((item) => item.status === "pending").slice(0, 2);
          for (const item of pending) {
            next.push(`¿Qué tengo pendiente sobre: ${item.message}?`);
            links.push({ href: "/app/actividad", label: item.message });
          }
        }
        if (inbox.status === "fulfilled") {
          const attention = inbox.value.attention?.slice(0, 2) ?? [];
          for (const mission of attention) {
            next.push(`Continúa esta misión: ${mission.objetivo}`);
            links.push({ href: "/app/misiones", label: mission.objetivo });
          }
        }
        if (memory.status === "fulfilled") {
          for (const item of memory.value.slice(0, 2)) {
            next.push(`Ten en cuenta esto que recuerdas: ${item.content}`);
          }
        }
        setSuggestions(next.length > 0 ? next.slice(0, 4) : FALLBACK_STARTERS);
        setResume(links.slice(0, 3));
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div
      className="m-auto flex max-w-xl flex-col items-center px-4 py-10 text-center"
      data-testid="chat-empty-state"
    >
      <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-100 text-brand-700 dark:bg-brand-900/50 dark:text-brand-300">
        <ChatIcon className="h-5 w-5" />
      </span>
      <p className="text-xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
        Escríbele a Edecán
      </p>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
        Como un chat con alguien de confianza. También puede escribirte primero con avisos o resultados.
      </p>
      {resume.length > 0 && (
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {resume.map((item) => (
            <Link
              key={`${item.href}:${item.label}`}
              href={item.href}
              className="rounded-full bg-brand-50 px-3 py-1.5 text-[11px] text-brand-700 dark:bg-brand-950/40 dark:text-brand-300"
            >
              Seguir: {item.label}
            </Link>
          ))}
        </div>
      )}
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {suggestions.map((request) => (
          <button
            key={request}
            type="button"
            onClick={() => onPickStarter(request)}
            className="rounded-full border border-slate-200 bg-white px-3.5 py-2 text-xs text-slate-600 shadow-sm transition-all hover:-translate-y-0.5 hover:border-brand-300 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
          >
            {request}
          </button>
        ))}
      </div>
    </div>
  );
}
