"use client";

/**
 * `/app/viajes`: vuelos y hoteles mediante la capa nativa de viajes, más
 * rastreo de paquetes vía AfterShip (`ARCHITECTURE.md` §14, WP-V5-09; ver
 * `docs/viajes.md`). Amadeus se conserva solo para instalaciones heredadas.
 *
 * Nota de navegación: `components/layout/nav-items.ts` está fuera de las rutas que
 * este paquete de trabajo puede tocar — el enlace del menú lateral lo agrega el
 * linchpin de v5 (WP-V5-01), mismo criterio que dejaron WP-V2-01/WP-V4-01 para sus
 * propias páginas nuevas ("un enlace puede dar 404 hasta entonces, es esperado").
 * Esta página funciona igual navegando directo a `/app/viajes`.
 */

import { TravelConnectionsPanel } from "@/components/configuracion/TravelConnectionsPanel";
import { PageHeader } from "@/components/ui";
import { BuscadorHoteles } from "@/components/viajes/BuscadorHoteles";
import { BuscadorVuelos } from "@/components/viajes/BuscadorVuelos";
import { CajaRastreo } from "@/components/viajes/CajaRastreo";

export default function ViajesPage() {
  return (
    <div>
      <PageHeader
        title="Viajes"
        description="Busca vuelos y hoteles con la capa de viajes de Edecán, sin configurar Amadeus. También puede rastrear paquetes con AfterShip. Edecán nunca reserva ni paga por su cuenta; cualquier borrador queda pendiente de tu confirmación."
      />

      <div className="mb-6"><TravelConnectionsPanel /></div>

      <div className="space-y-6">
        <BuscadorVuelos />
        <BuscadorHoteles />
        <CajaRastreo />
      </div>
    </div>
  );
}
