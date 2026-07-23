"use client";

/**
 * Pestaña "Escucha siempre" de `/app/voz` -- entrenamiento de la wake word
 * NATIVA (Tauri, `apps/desktop/src-tauri/`: cpal + rustpotter corriendo en
 * segundo plano, funciona con la ventana minimizada/cerrada -- distinto del
 * modo del navegador en `components/chat/AlwaysListenMode.tsx`, que solo
 * escucha con la ventana abierta y requiere click manual). Mismo patrón
 * visual que `VocesTab`/`PodcastsTab` de esta carpeta.
 *
 * Todo lo de acá depende de comandos Tauri (`@/lib/tauriListen`), que no
 * existen fuera de la app de escritorio empaquetada -- en el navegador
 * normal (dev, o el modo hosted multi-tenant) se degrada a un aviso, ver
 * `isTauriApp()`.
 */

import { useEffect, useState } from "react";

import { Alert, Button, Card, CardBody, CardHeader, Field, Input, Select, Switch } from "@/components/ui";
import { isTauriApp, tauriInvoke } from "@/lib/tauriListen";
import { WAKE_WORD_PRESETS } from "@/lib/wakeWords";

const TOTAL_MUESTRAS = 3;

interface AlwaysListenState {
  enabled: boolean;
  trained: boolean;
  wake_label: string;
  listening: boolean;
  samples_recorded: number;
}

export function EscuchaSiempreTab() {
  if (!isTauriApp()) {
    return (
      <Alert variant="info">
        «Escucha siempre» en segundo plano -- que te escuche sin tener la app abierta ni hacer clic
        en nada -- solo está disponible en la app de escritorio de Edecán, no en el navegador ni en
        el modo alojado. Descarga la app de escritorio para entrenar tu palabra clave y activarla.
      </Alert>
    );
  }

  return <EscuchaSiempreTabNativa />;
}

function EscuchaSiempreTabNativa() {
  const [state, setState] = useState<AlwaysListenState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [wakeWord, setWakeWord] = useState<string>(WAKE_WORD_PRESETS[0]);
  const [personalizado, setPersonalizado] = useState(false);

  const [grabandoIndex, setGrabandoIndex] = useState<number | null>(null);
  const [entrenando, setEntrenando] = useState(false);
  const [cambiandoActivacion, setCambiandoActivacion] = useState(false);
  const [reiniciando, setReiniciando] = useState(false);

  useEffect(() => {
    void cargarEstado();
  }, []);

  async function cargarEstado() {
    setLoading(true);
    setError(null);
    try {
      const s = await tauriInvoke<AlwaysListenState>("always_listen_get_state");
      setState(s);
      if (s.wake_label) {
        setWakeWord(s.wake_label);
        setPersonalizado(!WAKE_WORD_PRESETS.includes(s.wake_label as (typeof WAKE_WORD_PRESETS)[number]));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo leer el estado de escucha siempre.");
    } finally {
      setLoading(false);
    }
  }

  async function handleGrabar(index: number) {
    setError(null);
    setGrabandoIndex(index);
    try {
      await tauriInvoke("always_listen_record_sample", { index });
      await cargarEstado();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo grabar la muestra.");
    } finally {
      setGrabandoIndex(null);
    }
  }

  async function handleEntrenar() {
    setError(null);
    setEntrenando(true);
    try {
      // Tauri transforma los argumentos Rust snake_case a camelCase en JS.
      await tauriInvoke("always_listen_train", { wakeLabel: wakeWord });
      await cargarEstado();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo entrenar la palabra clave.");
    } finally {
      setEntrenando(false);
    }
  }

  async function handleToggle(next: boolean) {
    setError(null);
    setCambiandoActivacion(true);
    try {
      await tauriInvoke("always_listen_set_enabled", { enabled: next });
      await cargarEstado();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el estado de escucha siempre.");
    } finally {
      setCambiandoActivacion(false);
    }
  }

  async function handleReiniciar() {
    setError(null);
    setReiniciando(true);
    try {
      await tauriInvoke("always_listen_reset_training");
      setPersonalizado(false);
      setWakeWord(WAKE_WORD_PRESETS[0]);
      await cargarEstado();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo reiniciar el entrenamiento.");
    } finally {
      setReiniciando(false);
    }
  }

  const samplesRecorded = state?.samples_recorded ?? 0;
  const trained = state?.trained ?? false;
  const enabled = state?.enabled ?? false;
  // Grabar muestras / (re)entrenar sigue disponible incluso ya entrenado --
  // así se puede mejorar la calidad de una muestra y volver a entrenar sin
  // pasar por "Reiniciar". Cambiar la PALABRA CLAVE sí exige reiniciar
  // primero: las muestras grabadas son audio de esa frase específica.
  const busy = grabandoIndex !== null || entrenando || cambiandoActivacion || reiniciando;

  return (
    <div>
      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      <div className="mb-6">
        <Card>
          <CardHeader
            title="Entrena tu palabra clave"
            description="Graba 3 muestras cortas diciendo la frase de activación con tu propia voz; así Edecán aprende a reconocerte a ti, no a cualquier persona."
          />
          <CardBody>
            {loading ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">Cargando estado…</p>
            ) : (
              <>
                <Field label="Palabra clave" htmlFor="escucha-wakeword">
                  <Select
                    id="escucha-wakeword"
                    value={personalizado ? "__custom__" : wakeWord}
                    disabled={busy || trained}
                    onChange={(e) => {
                      if (e.target.value === "__custom__") {
                        setPersonalizado(true);
                        return;
                      }
                      setPersonalizado(false);
                      setWakeWord(e.target.value);
                    }}
                  >
                    {WAKE_WORD_PRESETS.map((preset) => (
                      <option key={preset} value={preset}>
                        {preset}
                      </option>
                    ))}
                    <option value="__custom__">Personalizada…</option>
                  </Select>
                  {personalizado && (
                    <Input
                      className="mt-2"
                      placeholder="Escribe tu frase de activación"
                      defaultValue={
                        WAKE_WORD_PRESETS.includes(wakeWord as (typeof WAKE_WORD_PRESETS)[number]) ? "" : wakeWord
                      }
                      disabled={busy || trained}
                      onBlur={(e) => {
                        if (e.target.value.trim()) setWakeWord(e.target.value.trim());
                      }}
                    />
                  )}
                  {trained && (
                    <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                      Ya entrenaste con «{state?.wake_label}». Para cambiar de palabra clave, reinicia
                      el entrenamiento primero.
                    </p>
                  )}
                </Field>

                <div className="mt-4 grid gap-2 sm:grid-cols-3">
                  {Array.from({ length: TOTAL_MUESTRAS }, (_, index) => (
                    <Button
                      key={index}
                      type="button"
                      variant={index < samplesRecorded ? "secondary" : "primary"}
                      disabled={busy}
                      loading={grabandoIndex === index}
                      onClick={() => void handleGrabar(index)}
                    >
                      {grabandoIndex === index
                        ? "Grabando… di la palabra clave"
                        : index < samplesRecorded
                          ? `Muestra ${index + 1} lista`
                          : `Grabar muestra ${index + 1}`}
                    </Button>
                  ))}
                </div>
                <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                  {samplesRecorded}/{TOTAL_MUESTRAS} muestras grabadas.
                </p>

                <div className="mt-4 flex justify-end">
                  <Button
                    onClick={() => void handleEntrenar()}
                    disabled={samplesRecorded < TOTAL_MUESTRAS || busy}
                    loading={entrenando}
                  >
                    Entrenar mi voz
                  </Button>
                </div>
              </>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Escucha en segundo plano"
          description="Con esto activado, Edecán te escucha en segundo plano -- incluso con la ventana minimizada o cerrada -- y atiende apenas dices tu palabra clave. La dices una sola vez y la conversación continúa hasta que digas «descansa» o cierres el modo."
        />
        <CardBody>
          <Switch
            id="escucha-siempre-toggle"
            checked={enabled}
            onChange={(next) => void handleToggle(next)}
            label="Activar escucha siempre en segundo plano"
            disabled={!trained || busy}
          />

          <div className="mt-4">
            <Alert variant="info">
              Al activarlo: cerrar la ventana la minimiza a la bandeja del sistema en vez de cerrar el
              programa; usa «Salir» desde el ícono de la bandeja para cerrar por completo. El sistema
              operativo te pedirá permiso de micrófono una sola vez.
            </Alert>
          </div>

          <div className="mt-4 flex justify-end">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void handleReiniciar()}
              loading={reiniciando}
              disabled={busy}
            >
              Reiniciar entrenamiento
            </Button>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
