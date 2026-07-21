"use client";

/**
 * Detalle de una reunión: resumen, minutas estructuradas (decisiones,
 * acciones, temas), referencia a la transcripción y errores/avisos
 * (`ARCHITECTURE.md` §15, WP-V6-05).
 *
 * Sin descarga real: `apps/api/edecan_api/routers/files.py` no expone hoy
 * ningún endpoint de descarga de contenido (`GET /v1/files/{id}` solo
 * devuelve metadatos, igual que `/app/archivos`) — así que el link al
 * transcript apunta a `/app/archivos`, donde el archivo generado (mismo
 * `files.id` que `transcript_file_id`) ya aparece listado, en vez de
 * inventar una ruta de descarga que no existe.
 */

import { Badge, Card, CardBody, CardHeader, EmptyState } from "@/components/ui";
import type { ReunionOut } from "@/lib/api-reuniones";
import { formatDateTime } from "@/lib/format";

function formatearDuracion(segundos: number | null): string {
  if (segundos === null) return "—";
  const total = Math.round(segundos);
  const minutos = Math.floor(total / 60);
  const restantes = total % 60;
  return `${minutos}:${String(restantes).padStart(2, "0")}`;
}

export function DetalleReunion({ reunion }: { reunion: ReunionOut | null }) {
  if (reunion === null) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            title="Elige una reunión"
            description="Seleccioná una reunión de la lista para ver su resumen y minutas."
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title={reunion.titulo}
        description={`Creada ${formatDateTime(reunion.created_at)} · duración ${formatearDuracion(reunion.duracion_segundos)}`}
      />
      <CardBody className="space-y-5">
        {reunion.status === "pending" && (
          <EmptyState
            title="En cola"
            description="Esta reunión todavía no empezó a procesarse."
          />
        )}
        {reunion.status === "running" && (
          <EmptyState
            title="Procesando…"
            description="Transcribiendo y generando las minutas — esto puede tardar unos minutos según el largo de la grabación."
          />
        )}

        {reunion.error && (
          <Badge variant={reunion.status === "error" ? "danger" : "warning"}>
            {reunion.error}
          </Badge>
        )}

        {reunion.status === "done" && (
          <>
            {reunion.resumen && (
              <section>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Resumen
                </h3>
                <p className="text-sm text-slate-700 dark:text-slate-200">{reunion.resumen}</p>
              </section>
            )}

            {reunion.decisiones.length > 0 && (
              <section>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Decisiones
                </h3>
                <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-200">
                  {reunion.decisiones.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              </section>
            )}

            {reunion.acciones.length > 0 && (
              <section>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Acciones
                </h3>
                <ul className="space-y-1.5">
                  {reunion.acciones.map((a, i) => (
                    <li
                      key={i}
                      className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-1.5 text-sm dark:bg-slate-950/40"
                    >
                      <span className="text-slate-700 dark:text-slate-200">{a.tarea}</span>
                      {a.responsable && (
                        <span className="shrink-0 text-xs text-slate-400">{a.responsable}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {reunion.temas.length > 0 && (
              <section>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Temas
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {reunion.temas.map((t, i) => (
                    <Badge key={i} variant="neutral">
                      {t}
                    </Badge>
                  ))}
                </div>
              </section>
            )}

            {reunion.transcript_file_id && (
              <section>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Transcripción completa
                </h3>
                <a
                  href="/app/archivos"
                  className="text-sm font-medium text-brand-600 underline hover:text-brand-700 dark:text-brand-400"
                >
                  Ver el archivo de la transcripción en /app/archivos
                </a>
              </section>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}
