import { Badge, Card, CardBody, CardHeader } from "@/components/ui";
import type { ResumenInventario } from "@/lib/api-inventario";
import { formatMoney, formatNumber } from "@/lib/format";

/** Tarjeta resumen del inventario: SKUs activos + valor a costo/precio + alertas de stock
 * bajo — pantalla única (no una fila de 4 `KpiCard` como `/app/negocios`) porque acá solo
 * hay 3 números "planos" más una lista, no vale la pena una grilla separada. Duplica
 * deliberadamente el patrón de mini-tile de `components/negocios/KpiCard.tsx` en vez de
 * importarlo (mismo criterio de aislamiento entre features que `lib/api-inventario.ts`, ver
 * su docstring). */
export function ResumenInventarioCard({ resumen }: { resumen: ResumenInventario | null }) {
  return (
    <Card>
      <CardHeader
        title="Resumen del inventario"
        description="Solo productos activos — un producto desactivado no cuenta para nada de esto."
      />
      <CardBody>
        {!resumen ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">Cargando…</p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Tile label="SKUs activos" value={formatNumber(resumen.total_skus)} />
              <Tile label="Valor a costo" value={formatMoney(resumen.valor_costo)} />
              <Tile label="Valor a precio de venta" value={formatMoney(resumen.valor_precio)} />
            </div>

            <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
              {resumen.stock_bajo.length === 0 ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  Sin alertas de stock bajo.
                </p>
              ) : (
                <div>
                  <p className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                    Stock bajo ({resumen.stock_bajo.length})
                  </p>
                  <ul className="space-y-1.5">
                    {resumen.stock_bajo.map((p) => (
                      <li
                        key={p.id}
                        className="flex items-center justify-between gap-2 text-sm text-slate-600 dark:text-slate-300"
                      >
                        <span className="truncate">
                          {p.sku} — {p.nombre}
                        </span>
                        <Badge variant="danger">
                          {formatNumber(p.stock)} / {formatNumber(p.stock_minimo)}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-800 dark:text-slate-100">{value}</p>
    </div>
  );
}
