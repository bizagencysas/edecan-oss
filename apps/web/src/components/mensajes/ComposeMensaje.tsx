"use client";

/**
 * Compositor simple de `/app/mensajes` (WP-V4-11): destinatario + texto + botón «Enviar» con
 * estado de éxito/error — a propósito SIN un campo de "plantilla" para WhatsApp (la API sí lo
 * acepta como argumento opcional, ver `lib/api-mensajes.ts`/`docs/mensajeria.md`, pero este
 * compositor se mantiene deliberadamente mínimo). Si un envío de WhatsApp cae fuera de la
 * ventana de 24h, la API ya traduce ese error a un mensaje que explica usar una plantilla
 * (`edecan_messaging.whatsapp._mensaje_error_graph`) — se muestra tal cual en el `Alert` de
 * error, sin que este componente necesite saber nada de esa regla de negocio.
 *
 * El propio click en «Enviar» ES la confirmación humana explícita — no hay un segundo paso
 * de "aprobar" (ver el docstring de `edecan_api.routers.mensajes.enviar_mensaje`).
 */

import { useEffect, useState, type FormEvent } from "react";

import { SendIcon } from "@/components/icons";
import { Alert, Button, Field, Input, Textarea } from "@/components/ui";
import { ApiError, enviarMensaje, type CanalMensajeria } from "@/lib/api-mensajes";

const NOMBRES: Record<CanalMensajeria, string> = {
  telegram: "Telegram",
  discord: "Discord",
  slack: "Slack",
  whatsapp: "WhatsApp",
};

const DESTINATARIO_PLACEHOLDER: Record<CanalMensajeria, string> = {
  telegram: "chat_id, p. ej. 123456789",
  discord: "id del canal, p. ej. 987654321098765432",
  slack: "id o #nombre del canal, p. ej. #general",
  whatsapp: "número en E.164, p. ej. +525512345678",
};

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

export function ComposeMensaje({
  canal,
  conectado,
}: {
  canal: CanalMensajeria;
  conectado: boolean;
}) {
  const [destinatario, setDestinatario] = useState("");
  const [texto, setTexto] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<{ variant: "success" | "error"; message: string } | null>(
    null,
  );

  // Cambiar de canal limpia el borrador — evita mandar por accidente al canal equivocado.
  useEffect(() => {
    setDestinatario("");
    setTexto("");
    setStatus(null);
  }, [canal]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const destino = destinatario.trim();
    const cuerpo = texto.trim();
    if (!destino || !cuerpo || sending) return;
    setSending(true);
    setStatus(null);
    try {
      await enviarMensaje({ canal, destinatario: destino, texto: cuerpo });
      setStatus({ variant: "success", message: `Mensaje enviado por ${NOMBRES[canal]}.` });
      setTexto("");
    } catch (err) {
      setStatus({ variant: "error", message: describeError(err) });
    } finally {
      setSending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <Field label="Destinatario" htmlFor="mensaje-destinatario">
        <Input
          id="mensaje-destinatario"
          value={destinatario}
          onChange={(e) => setDestinatario(e.target.value)}
          placeholder={DESTINATARIO_PLACEHOLDER[canal]}
          disabled={!conectado || sending}
        />
      </Field>
      <Field label="Mensaje" htmlFor="mensaje-texto">
        <Textarea
          id="mensaje-texto"
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          placeholder="Escribe el mensaje…"
          disabled={!conectado || sending}
        />
      </Field>

      {status && <Alert variant={status.variant}>{status.message}</Alert>}
      {!conectado && (
        <Alert variant="info">Conecta {NOMBRES[canal]} en Conectores antes de poder enviar.</Alert>
      )}

      <Button
        type="submit"
        loading={sending}
        disabled={!conectado || !destinatario.trim() || !texto.trim()}
      >
        <SendIcon className="h-4 w-4" /> Enviar
      </Button>
    </form>
  );
}
