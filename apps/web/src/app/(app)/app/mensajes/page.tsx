"use client";

/**
 * `/app/mensajes` — bandeja de mensajería unificada (WP-V4-11, `ARCHITECTURE.md` §13). La
 * entrada de nav ("Mensajes") ya la agrega WP-V4-01 (`components/layout/nav-items.ts`).
 *
 * Compone los tres bloques que pide el paquete de trabajo:
 * - `CanalSelector` — chips con badge de estado (conectado/sin conectar/solo envío) + link a
 *   `/app/conectores`.
 * - `MensajesList` — mensajes recientes del canal seleccionado, refrescable a mano.
 * - `ComposeMensaje` — destinatario + texto + botón «Enviar» con estado de éxito/error.
 *
 * Mismo criterio que `/app/misiones` (`useAuth().me?.flags?.[...]`): sin el flag de plan
 * `connectors.messaging`, se muestra un `EmptyState` en vez de intentar cargar nada — evita
 * el "parpadeo" de un error 403 la primera vez que se pinta la página.
 */

import { useCallback, useEffect, useState } from "react";

import { CanalSelector } from "@/components/mensajes/CanalSelector";
import { ComposeMensaje } from "@/components/mensajes/ComposeMensaje";
import { MensajesList } from "@/components/mensajes/MensajesList";
import { Alert, Card, CardBody, CardHeader, EmptyState, PageHeader } from "@/components/ui";
import {
  ApiError,
  CANALES_MENSAJERIA,
  FLAG_CONNECTORS_MESSAGING,
  listCanales,
  type CanalEstado,
  type CanalMensajeria,
} from "@/lib/api-mensajes";
import { useAuth } from "@/lib/auth-context";

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

/** Placeholder "todo sin conectar" mientras `GET /v1/mensajes/canales` responde por primera
 * vez, o si falló — así `CanalSelector` siempre tiene los 4 canales para pintar. */
function canalesVacios(): CanalEstado[] {
  return CANALES_MENSAJERIA.map((canal) => ({
    canal,
    conectado: false,
    puede_leer: canal !== "whatsapp",
  }));
}

export default function MensajesPage() {
  const { me } = useAuth();
  const habilitado = Boolean(me?.flags?.[FLAG_CONNECTORS_MESSAGING]);

  const [canales, setCanales] = useState<CanalEstado[]>(canalesVacios());
  const [loadingCanales, setLoadingCanales] = useState(true);
  const [canalesError, setCanalesError] = useState<string | null>(null);
  const [seleccionado, setSeleccionado] = useState<CanalMensajeria>("telegram");

  const cargarCanales = useCallback(async () => {
    setLoadingCanales(true);
    try {
      const estado = await listCanales();
      setCanales(estado);
      setCanalesError(null);
      setSeleccionado((actual) => {
        if (estado.some((c) => c.canal === actual && c.conectado)) return actual;
        return estado.find((c) => c.conectado)?.canal ?? actual;
      });
    } catch (err) {
      setCanalesError(describeError(err));
    } finally {
      setLoadingCanales(false);
    }
  }, []);

  useEffect(() => {
    if (habilitado) {
      void cargarCanales();
    } else {
      setLoadingCanales(false);
    }
  }, [habilitado, cargarCanales]);

  const canalActual = canales.find((c) => c.canal === seleccionado);

  return (
    <div>
      <PageHeader
        title="Mensajes"
        description="Bandeja unificada: lee y envía mensajes por Telegram, Discord, Slack y WhatsApp con las cuentas que ya conectaste."
      />

      {!habilitado ? (
        <EmptyState
          title="La mensajería no está disponible en tu plan"
          description="Actualiza tu plan para leer y enviar mensajes por Telegram, Discord, Slack y WhatsApp desde Edecán."
        />
      ) : (
        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Canales"
              description="Estado de tus cuentas conectadas — WhatsApp solo admite envío."
            />
            <CardBody>
              {canalesError && (
                <div className="mb-3">
                  <Alert variant="error">{canalesError}</Alert>
                </div>
              )}
              <CanalSelector
                canales={canales}
                loading={loadingCanales}
                selected={seleccionado}
                onSelect={setSeleccionado}
                onRefresh={cargarCanales}
              />
            </CardBody>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader title="Mensajes recientes" />
              <CardBody>
                <MensajesList
                  canal={seleccionado}
                  conectado={Boolean(canalActual?.conectado)}
                  puedeLeer={Boolean(canalActual?.puede_leer ?? seleccionado !== "whatsapp")}
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Enviar mensaje" />
              <CardBody>
                <ComposeMensaje canal={seleccionado} conectado={Boolean(canalActual?.conectado)} />
              </CardBody>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
