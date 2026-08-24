"""Sondas individuales de la fase 0.

Cada módulo de este paquete mide UNA familia de propiedades y devuelve
`ProbeResult` del contrato. Ninguna sonda rellena la `ModelCard` por su cuenta:
eso lo hace el runner, que es quien decide qué medición va a qué campo.

`SONDAS` es el único punto de registro que el runner conoce: `descubrir_sondas`
lo busca por convención. Se reexporta desde `registro`, que es donde vive el
adaptador entre la firma propia de cada sonda y la forma que el runner orquesta.
"""

from __future__ import annotations

from .registro import SONDAS, sondas_de_la_fase_0

__all__ = ["SONDAS", "sondas_de_la_fase_0"]
