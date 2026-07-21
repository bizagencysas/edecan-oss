"use client";

/**
 * Selector de proveedor LLM — el mismo componente se usa dentro de la
 * tarjeta "Inteligencia" de `/app/configuracion` y en el Paso 1 del wizard
 * de `/app/bienvenida` (DIRECCION_ACTUAL.md exige que sea "la MISMA UI").
 *
 * Orden de opciones, de menos a más fricción (principio de "pocos clicks"):
 * 1. Un clic — Claude CLI / Codex CLI / Ollama ya instalados en la máquina
 *    (`detect`, `GET /v1/setup/detect`), solo si `detect.local_mode` (no
 *    aplica a un backend hosted compartido).
 * 2. Pegar y validar — Anthropic / Compatible con OpenAI / Vertex-Gemini.
 *    Vertex por defecto pide solo una API key de Google AI; el modo cuenta
 *    de servicio de GCP queda colapsado tras "Opciones avanzadas".
 */

import { useState } from "react";

import { Alert, Button, Field, Input, Select, Spinner, Textarea } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  putLlmCredential,
  type LlmKind,
  type PutLlmCredentialInput,
  type SetupDetect,
} from "@/lib/api-configuracion";

import { CampoLlave } from "./CampoLlave";

const ENLACE_ANTHROPIC = "https://console.anthropic.com/settings/keys";
const ENLACE_OPENAI = "https://platform.openai.com/api-keys";
const ENLACE_GOOGLE_AI = "https://aistudio.google.com/apikey";

type Tab = "anthropic" | "openai_compat" | "vertex";
type Accion =
  | "claude_cli"
  | "codex_cli"
  | "ollama"
  | "anthropic"
  | "openai_compat"
  | "vertex_key"
  | "vertex_sa";

type CuerpoConexion = Omit<PutLlmCredentialInput, "kind" | "validate">;

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar.";
}

export function SelectorLLM({
  detect,
  detectLoading = false,
  simplified = false,
  onConnected,
}: {
  detect: SetupDetect | null;
  detectLoading?: boolean;
  /** En el primer arranque deja los detalles de proveedores fuera del camino
   * principal. Ajustes sigue mostrando el selector completo. */
  simplified?: boolean;
  onConnected: () => void;
}) {
  const [tab, setTab] = useState<Tab>("anthropic");
  const [busyAccion, setBusyAccion] = useState<Accion | null>(null);
  const [feedback, setFeedback] = useState<{ accion: Accion; ok: boolean; mensaje: string } | null>(null);

  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState("https://api.openai.com/v1");
  const [openaiKey, setOpenaiKey] = useState("");
  const [vertexKey, setVertexKey] = useState("");
  const [vertexAvanzado, setVertexAvanzado] = useState(false);
  const [vertexJson, setVertexJson] = useState("");
  const [vertexProjectId, setVertexProjectId] = useState("");
  const [vertexRegion, setVertexRegion] = useState("us-central1");
  const [ollamaModel, setOllamaModel] = useState(detect?.ollama.models[0] ?? "");

  const ocupado = busyAccion !== null;

  async function conectar(accion: Accion, kind: LlmKind, body: CuerpoConexion = {}) {
    setBusyAccion(accion);
    setFeedback(null);
    try {
      await putLlmCredential({ kind, validate: true, ...body });
      setFeedback({ accion, ok: true, mensaje: "Conectado y validado." });
      onConnected();
    } catch (err) {
      setFeedback({ accion, ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusyAccion(null);
    }
  }

  function Feedback({ accion }: { accion: Accion }) {
    if (feedback?.accion !== accion) return null;
    return (
      <Alert variant={feedback.ok ? "success" : "error"}>
        {feedback.ok ? `✅ ${feedback.mensaje}` : `❌ ${feedback.mensaje}`}
      </Alert>
    );
  }

  const mostrarAutoDetect = detect?.local_mode === true;
  const nadaDetectado =
    mostrarAutoDetect && !detect?.claude_cli.installed && !detect?.codex_cli.installed && !detect?.ollama.running;

  return (
    <div className="space-y-5">
      {detectLoading && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner className="h-4 w-4" /> Buscando lo que ya tienes instalado…
        </div>
      )}

      {mostrarAutoDetect && !detectLoading && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Un clic — usa lo que ya tienes
          </p>

          {detect?.claude_cli.installed && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
                <span className="text-sm text-slate-700 dark:text-slate-200">
                  Usar mi Claude CLI{detect.claude_cli.version ? ` (detectado v${detect.claude_cli.version})` : " (detectado)"}
                </span>
                <Button
                  size="sm"
                  onClick={() => void conectar("claude_cli", "claude_cli")}
                  loading={busyAccion === "claude_cli"}
                  disabled={ocupado}
                >
                  Usar
                </Button>
              </div>
              <Feedback accion="claude_cli" />
            </div>
          )}

          {detect?.codex_cli.installed && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
                <span className="text-sm text-slate-700 dark:text-slate-200">
                  Usar mi Codex CLI{detect.codex_cli.version ? ` (detectado v${detect.codex_cli.version})` : " (detectado)"}
                </span>
                <Button
                  size="sm"
                  onClick={() => void conectar("codex_cli", "codex_cli")}
                  loading={busyAccion === "codex_cli"}
                  disabled={ocupado}
                >
                  Usar
                </Button>
              </div>
              <Feedback accion="codex_cli" />
            </div>
          )}

          {detect?.ollama.running && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
                <span className="text-sm text-slate-700 dark:text-slate-200">
                  Usar Ollama ({detect.ollama.models.length} modelo{detect.ollama.models.length === 1 ? "" : "s"})
                </span>
                {detect.ollama.models.length > 0 ? (
                  <div className="flex flex-1 flex-wrap items-center gap-2">
                    <div className="min-w-[9rem] flex-1">
                      <Select
                        aria-label="Modelo de Ollama"
                        value={ollamaModel}
                        onChange={(e) => setOllamaModel(e.target.value)}
                      >
                        {detect.ollama.models.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </Select>
                    </div>
                    <Button
                      size="sm"
                      onClick={() =>
                        void conectar("ollama", "ollama", {
                          model_principal: ollamaModel,
                          model_rapido: ollamaModel,
                          base_url: detect.ollama.base_url ?? undefined,
                        })
                      }
                      loading={busyAccion === "ollama"}
                      disabled={ocupado || !ollamaModel}
                    >
                      Usar
                    </Button>
                  </div>
                ) : (
                  <span className="text-xs text-slate-400">Sin modelos descargados todavía.</span>
                )}
              </div>
              <Feedback accion="ollama" />
            </div>
          )}

          {nadaDetectado && (
            <p className="text-xs text-slate-400">
              No detectamos Claude CLI, Codex CLI ni Ollama instalados en este equipo — usa una API key abajo.
            </p>
          )}
        </div>
      )}

      {simplified ? (
        <details className={mostrarAutoDetect ? "border-t border-slate-100 pt-4 dark:border-slate-800" : ""}>
          <summary className="cursor-pointer text-sm font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400">
            Conectar otro servicio de IA
          </summary>
          <div className="mt-4">
            <ManualProviderSelector />
          </div>
        </details>
      ) : (
        <ManualProviderSelector />
      )}
    </div>
  );

  function ManualProviderSelector() {
    return (
      <div className={mostrarAutoDetect && !simplified ? "border-t border-slate-100 pt-4 dark:border-slate-800" : ""}>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Con una API key</p>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {(
            [
              ["anthropic", "Anthropic"],
              ["openai_compat", "Compatible con OpenAI"],
              ["vertex", "Vertex AI / Gemini"],
            ] as [Tab, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setTab(value)}
              className={
                "rounded-full px-3 py-1 text-xs font-medium transition-colors " +
                (tab === value
                  ? "bg-brand-600 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700")
              }
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "anthropic" && (
          <div className="space-y-3">
            <CampoLlave
              id="llm_anthropic_key"
              label="API key de Anthropic"
              value={anthropicKey}
              onChange={setAnthropicKey}
              placeholder="sk-ant-…"
              linkHref={ENLACE_ANTHROPIC}
              disabled={ocupado}
            />
            <Feedback accion="anthropic" />
            <Button
              size="sm"
              onClick={() => void conectar("anthropic", "anthropic", { api_key: anthropicKey })}
              loading={busyAccion === "anthropic"}
              disabled={ocupado || !anthropicKey.trim()}
            >
              Conectar
            </Button>
          </div>
        )}

        {tab === "openai_compat" && (
          <div className="space-y-3">
            <Field
              label="Base URL"
              htmlFor="llm_openai_base_url"
              hint="Cualquier endpoint compatible con /chat/completions (OpenAI, Groq, Together.ai, un LLM local…)."
            >
              <Input
                id="llm_openai_base_url"
                value={openaiBaseUrl}
                onChange={(e) => setOpenaiBaseUrl(e.target.value)}
                placeholder="https://api.openai.com/v1"
                autoComplete="off"
                disabled={ocupado}
              />
            </Field>
            <CampoLlave
              id="llm_openai_key"
              label="API key"
              value={openaiKey}
              onChange={setOpenaiKey}
              placeholder="sk-…"
              linkHref={ENLACE_OPENAI}
              disabled={ocupado}
            />
            <Feedback accion="openai_compat" />
            <Button
              size="sm"
              onClick={() =>
                void conectar("openai_compat", "openai_compat", { api_key: openaiKey, base_url: openaiBaseUrl })
              }
              loading={busyAccion === "openai_compat"}
              disabled={ocupado || !openaiKey.trim() || !openaiBaseUrl.trim()}
            >
              Conectar
            </Button>
          </div>
        )}

        {tab === "vertex" && (
          <div className="space-y-3">
            <CampoLlave
              id="llm_vertex_key"
              label="API key de Google AI"
              value={vertexKey}
              onChange={setVertexKey}
              placeholder="AIza…"
              linkHref={ENLACE_GOOGLE_AI}
              hint="El camino más simple: una sola key de Google AI Studio."
              disabled={ocupado}
            />
            <Feedback accion="vertex_key" />
            <Button
              size="sm"
              onClick={() => void conectar("vertex_key", "vertex", { api_key: vertexKey, extra: { mode: "api_key" } })}
              loading={busyAccion === "vertex_key"}
              disabled={ocupado || !vertexKey.trim()}
            >
              Conectar
            </Button>

            <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
              <button
                type="button"
                onClick={() => setVertexAvanzado((v) => !v)}
                className="text-xs font-medium text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
              >
                {vertexAvanzado ? "Ocultar opciones avanzadas" : "Opciones avanzadas (cuenta de servicio de GCP)"}
              </button>
              {vertexAvanzado && (
                <div className="mt-3 space-y-3">
                  <Field label="JSON de la cuenta de servicio" htmlFor="llm_vertex_json">
                    <Textarea
                      id="llm_vertex_json"
                      value={vertexJson}
                      onChange={(e) => setVertexJson(e.target.value)}
                      placeholder='{"type": "service_account", …}'
                      className="font-mono text-xs"
                      disabled={ocupado}
                    />
                  </Field>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Field label="Project ID" htmlFor="llm_vertex_project">
                      <Input
                        id="llm_vertex_project"
                        value={vertexProjectId}
                        onChange={(e) => setVertexProjectId(e.target.value)}
                        placeholder="mi-proyecto-gcp"
                        autoComplete="off"
                        disabled={ocupado}
                      />
                    </Field>
                    <Field label="Región" htmlFor="llm_vertex_region">
                      <Input
                        id="llm_vertex_region"
                        value={vertexRegion}
                        onChange={(e) => setVertexRegion(e.target.value)}
                        placeholder="us-central1"
                        autoComplete="off"
                        disabled={ocupado}
                      />
                    </Field>
                  </div>
                  <Feedback accion="vertex_sa" />
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      void conectar("vertex_sa", "vertex", {
                        extra: {
                          mode: "service_account",
                          service_account_json: vertexJson,
                          project_id: vertexProjectId,
                          region: vertexRegion,
                        },
                      })
                    }
                    loading={busyAccion === "vertex_sa"}
                    disabled={ocupado || !vertexJson.trim() || !vertexProjectId.trim()}
                  >
                    Conectar con cuenta de servicio
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  }
}
