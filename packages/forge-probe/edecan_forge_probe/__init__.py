"""Fase 0 de Forge: sondas que MIDEN de qué es capaz un modelo, no lo suponen.

El contrato de la fase vive en `edecan_forge_probe.modelcard`. Este paquete no
reexporta nada a propósito: cada sonda se importa por su ruta explícita
(`edecan_forge_probe.probes.perf`) para que quede escrito en el import qué se
está midiendo.
"""

from __future__ import annotations
