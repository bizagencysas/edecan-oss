"use client";

/**
 * Panel de terminal del IDE embebido: input de comando + historial
 * append-only (`$ cmd`, stdout/stderr, exit code) vía `POST /v1/ide/run`.
 * El companion solo corre ejecutables que el DUEÑO del equipo listó en
 * `allowed_commands` (`~/.edecan/companion.yaml`) — cualquier otro comando
 * vuelve como un error 422 legible, no como un fallo silencioso.
 */

import { useRef, useState } from "react";

import { Button } from "@/components/ui";
import { postIdeRun } from "@/lib/api-ide";

interface HistoryEntry {
  id: number;
  command: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  truncated: boolean;
  error: string | null;
  running: boolean;
}

export function Terminal({ disabled }: { disabled?: boolean }) {
  const [command, setCommand] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [running, setRunning] = useState(false);
  const nextIdRef = useRef(1);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const cmd = command.trim();
    if (!cmd || running) return;

    const id = nextIdRef.current++;
    setCommand("");
    setHistory((prev) => [
      ...prev,
      {
        id,
        command: cmd,
        stdout: "",
        stderr: "",
        exitCode: null,
        truncated: false,
        error: null,
        running: true,
      },
    ]);
    setRunning(true);
    try {
      const result = await postIdeRun(cmd);
      setHistory((prev) =>
        prev.map((entry) =>
          entry.id === id
            ? {
                ...entry,
                stdout: result.stdout,
                stderr: result.stderr,
                exitCode: result.exit_code,
                truncated: result.truncated,
                running: false,
              }
            : entry,
        ),
      );
    } catch (err) {
      setHistory((prev) =>
        prev.map((entry) =>
          entry.id === id
            ? {
                ...entry,
                error: err instanceof Error ? err.message : "No se pudo ejecutar el comando.",
                running: false,
              }
            : entry,
        ),
      );
    } finally {
      setRunning(false);
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: "end" }));
    }
  }

  return (
    <div className="flex h-48 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-slate-950 dark:border-slate-800">
      <div className="flex-1 space-y-2 overflow-y-auto px-3 py-2 font-mono text-xs">
        {history.length === 0 && (
          <p className="text-slate-500">
            {disabled
              ? "Conecta el companion (Ajustes) para poder ejecutar comandos."
              : "Escribe un comando y presiona Enter."}
          </p>
        )}
        {history.map((entry) => (
          <div key={entry.id}>
            <p className="text-emerald-400">$ {entry.command}</p>
            {entry.running && <p className="text-slate-400">ejecutando…</p>}
            {entry.stdout && <pre className="whitespace-pre-wrap break-words text-slate-100">{entry.stdout}</pre>}
            {entry.stderr && (
              <pre className="whitespace-pre-wrap break-words text-rose-400">{entry.stderr}</pre>
            )}
            {entry.error && <p className="text-rose-400">{entry.error}</p>}
            {entry.truncated && <p className="text-amber-400">(salida recortada)</p>}
            {entry.exitCode !== null && (
              <p className={entry.exitCode === 0 ? "text-slate-500" : "text-amber-400"}>
                (código de salida: {entry.exitCode})
              </p>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <form onSubmit={handleSubmit} className="flex items-center gap-2 border-t border-slate-800 px-3 py-2">
        <span className="font-mono text-xs text-emerald-400">$</span>
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          disabled={disabled || running}
          placeholder="comando…"
          aria-label="Comando de terminal"
          className="flex-1 bg-transparent font-mono text-xs text-slate-100 outline-none placeholder:text-slate-600 disabled:opacity-50"
        />
        <Button
          type="submit"
          size="sm"
          variant="secondary"
          disabled={disabled || running || !command.trim()}
          loading={running}
        >
          Ejecutar
        </Button>
      </form>
    </div>
  );
}
