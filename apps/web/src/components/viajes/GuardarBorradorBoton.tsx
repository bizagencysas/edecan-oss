"use client";

/**
 * Botón "Guardar como borrador en Órdenes" — reutilizado por `BuscadorVuelos`/
 * `BuscadorHoteles`. Crear un borrador de reserva SOLO puede pasar por la tool
 * `preparar_reserva` (`dangerous=True`) del agente — mismo criterio EXACTO que
 * `ads_preparar_campana`/`preparar_pago`/`preparar_orden` (`ARCHITECTURE.md` §10.7,
 * `docs/viajes.md`): no existe ningún endpoint HTTP que cree el borrador directo (ver
 * `edecan_api.routers.viajes`, que solo expone `PUT/DELETE credentials` + `GET
 * status/buscar/rastreo`, nunca un `POST` de creación). Así que este botón crea una
 * conversación nueva, le pide al agente que prepare la reserva, y muestra el gate de
 * confirmación del *tool call* ahí mismo — sin salir de esta página ni duplicar toda
 * la UI de chat.
 *
 * Reutiliza `createConversation`/`sendMessageStream`/`confirmToolCallStream` de
 * `lib/api.ts` (funciones públicas, ver el mismo criterio que `lib/api-negocios.ts`:
 * ese archivo está fuera del alcance de este paquete de trabajo para EDITAR, pero
 * importar sus exports públicos es el patrón normal del resto del repo).
 */

import { useState } from "react";

import { Alert, Button, Card, CardBody } from "@/components/ui";
import { confirmToolCallStream, createConversation, sendMessageStream } from "@/lib/api";
import type { AgentEvent } from "@/lib/types";

export interface OfertaParaReservar {
  tipo: "vuelo" | "hotel";
  descripcion: string;
  monto: number;
  moneda: string;
  ofertaId: string;
}

type Estado =
  | { fase: "inicial" }
  | { fase: "enviando" }
  | { fase: "esperando_confirmacion"; conversationId: string; toolCallId: string }
  | { fase: "confirmando" }
  | { fase: "listo"; mensaje: string }
  | { fase: "error"; mensaje: string };

function mensajeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return "No se pudo preparar la reserva.";
}

function construirPrompt(oferta: OfertaParaReservar): string {
  return (
    `Prepara un borrador de reserva de ${oferta.tipo === "vuelo" ? "vuelo" : "hotel"} usando ` +
    `la herramienta preparar_reserva con estos datos exactos: tipo="${oferta.tipo}", ` +
    `descripcion="${oferta.descripcion}", monto=${oferta.monto}, moneda="${oferta.moneda}", ` +
    `oferta_id="${oferta.ofertaId}". No reserves ni pagues nada real, solo crea el borrador.`
  );
}

export function GuardarBorradorBoton({ oferta }: { oferta: OfertaParaReservar }) {
  const [estado, setEstado] = useState<Estado>({ fase: "inicial" });

  async function iniciar() {
    setEstado({ fase: "enviando" });
    try {
      const titulo = `Reserva: ${oferta.tipo === "vuelo" ? "vuelo" : "hotel"} — ${oferta.descripcion}`;
      const conversacion = await createConversation(titulo);

      let resuelto = false;
      await sendMessageStream(conversacion.id, construirPrompt(oferta), (event: AgentEvent) => {
        if (resuelto) return;
        if (event.type === "confirmation_required") {
          resuelto = true;
          setEstado({
            fase: "esperando_confirmacion",
            conversationId: conversacion.id,
            toolCallId: event.tool_call_id,
          });
        } else if (event.type === "tool_end") {
          resuelto = true;
          setEstado({ fase: "listo", mensaje: event.result_preview });
        } else if (event.type === "error") {
          resuelto = true;
          setEstado({ fase: "error", mensaje: event.message });
        }
      });
      if (!resuelto) {
        setEstado({
          fase: "error",
          mensaje: "El asistente no preparó la reserva. Intenta de nuevo desde el chat.",
        });
      }
    } catch (err) {
      setEstado({ fase: "error", mensaje: mensajeError(err) });
    }
  }

  async function confirmar(aprobar: boolean) {
    if (estado.fase !== "esperando_confirmacion") return;
    const { conversationId, toolCallId } = estado;
    setEstado({ fase: "confirmando" });
    try {
      let resuelto = false;
      await confirmToolCallStream(conversationId, toolCallId, aprobar, (event: AgentEvent) => {
        if (resuelto) return;
        if (event.type === "tool_end") {
          resuelto = true;
          setEstado({ fase: "listo", mensaje: event.result_preview });
        } else if (event.type === "done" && !aprobar) {
          resuelto = true;
          setEstado({ fase: "listo", mensaje: "Cancelado — no se creó ningún borrador." });
        } else if (event.type === "error") {
          resuelto = true;
          setEstado({ fase: "error", mensaje: event.message });
        }
      });
      if (!resuelto) {
        setEstado({ fase: "listo", mensaje: aprobar ? "Borrador procesado." : "Cancelado." });
      }
    } catch (err) {
      setEstado({ fase: "error", mensaje: mensajeError(err) });
    }
  }

  if (estado.fase === "listo") {
    return <Alert variant="success">{estado.mensaje}</Alert>;
  }
  if (estado.fase === "error") {
    return (
      <div className="space-y-2">
        <Alert variant="error">{estado.mensaje}</Alert>
        <Button size="sm" variant="secondary" onClick={() => setEstado({ fase: "inicial" })}>
          Reintentar
        </Button>
      </div>
    );
  }
  if (estado.fase === "esperando_confirmacion") {
    return (
      <Card className="border-l-4 border-l-amber-400">
        <CardBody className="space-y-2">
          <p className="text-sm text-slate-700 dark:text-slate-200">
            El asistente quiere crear este borrador en Órdenes. Nada se reserva ni se paga
            todavía — la compra real la harías tú, directamente con la aerolínea/hotel.
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void confirmar(true)}>
              Confirmar
            </Button>
            <Button size="sm" variant="secondary" onClick={() => void confirmar(false)}>
              Cancelar
            </Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  const cargando = estado.fase === "enviando" || estado.fase === "confirmando";
  return (
    <Button size="sm" variant="secondary" onClick={() => void iniciar()} loading={cargando}>
      Guardar como borrador en Órdenes
    </Button>
  );
}
