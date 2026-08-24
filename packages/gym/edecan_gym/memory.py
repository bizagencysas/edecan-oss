"""Contexto de memoria/historial para la generación del siguiente plan."""

from __future__ import annotations

from typing import Any


def contexto_para_plan(historial: list[dict], limite: int = 5) -> str:
    """Texto compacto en español con la progresión de las últimas sesiones.

    Toma los últimos `limite` resúmenes (dicts tipo `WorkoutSession.resumen()`)
    y resume por sesión qué ejercicios se hicieron, con series y pesos, para
    alimentar el prompt del siguiente plan.
    """
    if not historial:
        return "Sin historial de sesiones previas."
    return "\n".join(_resumir_sesion(r) for r in historial[-limite:])


def _nombres_por_indice(resumen: dict) -> dict[int, str]:
    """Mapea `ejercicio_idx` → nombre usando `plan.ejercicios` si está presente.

    `resumen()` no trae nombres; `WorkoutSession.to_dict()` sí (vía `plan`).
    Si no hay nombres, el caller usa "ejercicio N".
    """
    plan = resumen.get("plan")
    ejercicios = plan.get("ejercicios") if isinstance(plan, dict) else None
    if ejercicios is None:
        ejercicios = resumen.get("ejercicios")
    nombres: dict[int, str] = {}
    if isinstance(ejercicios, list):
        for i, ejercicio in enumerate(ejercicios):
            nombre = ejercicio.get("nombre") if isinstance(ejercicio, dict) else None
            if nombre:
                nombres[i] = str(nombre)
    return nombres


def _resumir_sesion(resumen: dict) -> str:
    titulo = str(resumen.get("titulo") or "Sesión").strip() or "Sesión"
    nombres = _nombres_por_indice(resumen)
    por_ejercicio: dict[int, dict[str, Any]] = {}
    for serie in resumen.get("series") or []:
        if not isinstance(serie, dict):
            continue
        try:
            idx = int(serie.get("ejercicio_idx"))
        except (TypeError, ValueError):
            continue
        bloque = por_ejercicio.setdefault(idx, {"series": 0, "reps": None, "peso": None})
        bloque["series"] += 1
        reps = serie.get("repeticiones")
        if type(reps) is int:
            bloque["reps"] = reps
        peso = serie.get("peso_kg")
        if isinstance(peso, (int, float)):
            actual = bloque["peso"]
            if actual is None or peso > actual:
                bloque["peso"] = peso

    detalles = []
    for idx in sorted(por_ejercicio):
        bloque = por_ejercicio[idx]
        nombre = nombres.get(idx, f"ejercicio {idx}")
        texto = f"{nombre}: {bloque['series']} series"
        if bloque["reps"] is not None:
            texto += f" de {bloque['reps']} reps"
        if bloque["peso"] is not None:
            texto += f" @ {bloque['peso']} kg"
        detalles.append(texto)
    detalle = "; ".join(detalles) if detalles else "sin series registradas"
    return f"- {titulo}: {detalle}"