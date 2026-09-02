"""Generación de planes de entrenamiento para el gimnasio inteligente.

`generar_plan` arma el prompt, llama al LLM inyectado (`completar`) y
parsea/valida la respuesta JSON de forma estricta, reintentando hasta
`reintentos` veces si el modelo devuelve algo inválido. Módulo puro: sin base
de datos, HTTP ni imports de `apps/`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .memory import contexto_para_plan

_MAX_EJERCICIOS = 10


@dataclass(frozen=True)
class Ejercicio:
    """Un ejercicio del plan: músculo, series, repeticiones y descanso."""

    nombre: str
    musculo: str
    series: int
    repeticiones: str
    descanso_seg: int
    notas: str = ""

    def to_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "musculo": self.musculo,
            "series": self.series,
            "repeticiones": self.repeticiones,
            "descanso_seg": self.descanso_seg,
            "notas": self.notas,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Ejercicio:
        return cls(
            nombre=d["nombre"],
            musculo=d["musculo"],
            series=d["series"],
            repeticiones=d["repeticiones"],
            descanso_seg=d["descanso_seg"],
            notas=d.get("notas", ""),
        )


@dataclass
class WorkoutPlan:
    """Un plan de entrenamiento completo, serializable a `dict`.

    `imagen_url` es la URL pública del collage (best-effort, legado) y
    `imagen_file_id` el `file_id` para descargarlo con el Bearer del tenant
    vía `GET /v1/files/{id}/download` — el camino que usa el resto de la app
    y que no depende de una URL pública accesible desde fuera.
    """

    titulo: str
    objetivo: str
    duracion_min: int
    ejercicios: list[Ejercicio]
    imagen_url: str | None = None
    imagen_file_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "titulo": self.titulo,
            "objetivo": self.objetivo,
            "duracion_min": self.duracion_min,
            "ejercicios": [e.to_dict() for e in self.ejercicios],
            "imagen_url": self.imagen_url,
            "imagen_file_id": self.imagen_file_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkoutPlan:
        return cls(
            titulo=d["titulo"],
            objetivo=d["objetivo"],
            duracion_min=d["duracion_min"],
            ejercicios=[Ejercicio.from_dict(e) for e in d["ejercicios"]],
            imagen_url=d.get("imagen_url"),
            imagen_file_id=d.get("imagen_file_id"),
        )


def _prompt_sistema() -> str:
    return (
        "Eres un instructor de gimnasio profesional. Diseñas planes de entrenamiento de "
        "fuerza e hipertrofia, en español. No emites diagnósticos médicos ni sustituyes "
        "a un profesional de la salud; ante cualquier molestia remite al usuario a su "
        "médico. Termina cada plan con una línea de seguridad: \"calienta antes de "
        "empezar y ajusta el peso a tu nivel\". Responde ÚNICAMENTE con el JSON "
        "solicitado, sin texto adicional."
    )


def _musculos_recientes(historial: list[dict] | None, limite: int = 3) -> list[str]:
    """Grupos musculares (`musculo`) de las últimas `limite` sesiones, deduplicados.

    El historial trae `plan.ejercicios` (o `ejercicios`) con `musculo` por
    ejercicio; se preserva el orden de aparición y se ignoran valores vacíos.
    """
    musculos: list[str] = []
    vistos: set[str] = set()
    for entrada in (historial or [])[-limite:]:
        plan = entrada.get("plan")
        ejercicios = plan.get("ejercicios") if isinstance(plan, dict) else None
        if ejercicios is None:
            ejercicios = entrada.get("ejercicios")
        if not isinstance(ejercicios, list):
            continue
        for ejercicio in ejercicios:
            if not isinstance(ejercicio, dict):
                continue
            musculo = ejercicio.get("musculo")
            if not isinstance(musculo, str) or not musculo.strip():
                continue
            clave = musculo.strip().lower()
            if clave in vistos:
                continue
            vistos.add(clave)
            musculos.append(musculo.strip())
    return musculos


def _prompt_usuario(
    *,
    persona: Any,
    historial: list[dict] | None,
    objetivo: str | None,
    readiness: str | None = None,
) -> str:
    partes = ["Genera un plan de entrenamiento de fuerza/hipertrofia."]
    if objetivo:
        partes.append(f"Objetivo del usuario: {objetivo}")
    if persona:
        partes.append(f"Perfil de la persona: {persona}")
    partes.append(f"Historial reciente:\n{contexto_para_plan(historial or [])}")
    musculos = _musculos_recientes(historial)
    if musculos:
        partes.append(
            "Músculos trabajados en los últimos días:\n"
            + "\n".join(musculos)
            + "\n\nNO repitas el mismo grupo muscular principal en días consecutivos; "
            "si el grupo ya se trabajó recientemente, elige ejercicios de otros "
            "grupos o varía el estímulo."
        )
    if readiness:
        partes.append(
            "Estado de recuperación del usuario hoy: "
            f"{readiness}\n\n"
            "Si el usuario está poco recuperado, baja el volumen/intensidad o "
            "propón movilidad/recuperación; si está bien descansado, mantén el "
            "estímulo."
        )
    partes.append(
        "Responde ÚNICAMENTE con un JSON con esta forma exacta: "
        '{"titulo": str, "objetivo": str, "duracion_min": int, "ejercicios": '
        '[{"nombre": str, "musculo": str, "series": int, "repeticiones": str, '
        '"descanso_seg": int, "notas": str}]}'
    )
    return "\n".join(partes)


def _con_error(base: str, error: str) -> str:
    return (
        f"{base}\n\nTu respuesta anterior fue rechazada: {error}. Corrígela y responde "
        "ÚNICAMENTE con el JSON solicitado."
    )


def _extraer_json(texto: str) -> dict | None:
    """Parsea `texto` como objeto JSON, tolerando cercas markdown ` ```json `."""
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = limpio.strip("`").strip()
        if limpio[:4].lower() == "json":
            limpio = limpio[4:].strip()
    try:
        cargado = json.loads(limpio)
        return cargado if isinstance(cargado, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio != -1 and fin != -1 and fin > inicio:
        try:
            cargado = json.loads(texto[inicio : fin + 1])
            return cargado if isinstance(cargado, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _construir_plan(data: dict) -> WorkoutPlan:
    titulo = data.get("titulo")
    objetivo = data.get("objetivo")
    duracion_min = data.get("duracion_min")
    ejercicios = data.get("ejercicios")

    if not isinstance(titulo, str) or not titulo.strip():
        raise ValueError("'titulo' debe ser un texto no vacío")
    if not isinstance(objetivo, str) or not objetivo.strip():
        raise ValueError("'objetivo' debe ser un texto no vacío")
    if type(duracion_min) is not int or duracion_min <= 0:
        raise ValueError("'duracion_min' debe ser un entero positivo")
    if not isinstance(ejercicios, list) or not 1 <= len(ejercicios) <= _MAX_EJERCICIOS:
        raise ValueError(
            f"'ejercicios' debe ser una lista de 1 a {_MAX_EJERCICIOS} elementos"
        )

    lista: list[Ejercicio] = []
    for i, ejercicio in enumerate(ejercicios):
        if not isinstance(ejercicio, dict):
            raise ValueError(f"ejercicios[{i}] debe ser un objeto")
        nombre = ejercicio.get("nombre")
        musculo = ejercicio.get("musculo")
        series = ejercicio.get("series")
        repeticiones = ejercicio.get("repeticiones")
        descanso_seg = ejercicio.get("descanso_seg")
        notas = ejercicio.get("notas", "")

        if not isinstance(nombre, str) or not nombre.strip():
            raise ValueError(f"ejercicios[{i}].nombre debe ser un texto no vacío")
        if not isinstance(musculo, str) or not musculo.strip():
            raise ValueError(f"ejercicios[{i}].musculo debe ser un texto no vacío")
        if type(series) is not int or series < 1:
            raise ValueError(f"ejercicios[{i}].series debe ser un entero >= 1")
        if not isinstance(repeticiones, str) or not repeticiones.strip():
            raise ValueError(f"ejercicios[{i}].repeticiones debe ser un texto no vacío")
        if type(descanso_seg) is not int or descanso_seg < 0:
            raise ValueError(f"ejercicios[{i}].descanso_seg debe ser un entero >= 0")
        if notas is None:
            notas = ""
        if not isinstance(notas, str):
            raise ValueError(f"ejercicios[{i}].notas debe ser un texto")

        lista.append(
            Ejercicio(
                nombre=nombre.strip(),
                musculo=musculo.strip(),
                series=series,
                repeticiones=repeticiones.strip(),
                descanso_seg=descanso_seg,
                notas=notas,
            )
        )

    imagen_url = data.get("imagen_url")
    if imagen_url is not None and not isinstance(imagen_url, str):
        imagen_url = None

    return WorkoutPlan(
        titulo=titulo.strip(),
        objetivo=objetivo.strip(),
        duracion_min=duracion_min,
        ejercicios=lista,
        imagen_url=imagen_url,
    )


async def generar_plan(
    completar,
    *,
    persona: Any = None,
    historial: list[dict] | None = None,
    objetivo: str | None = None,
    readiness: str | None = None,
    reintentos: int = 2,
) -> WorkoutPlan:
    """Genera un plan llamando al LLM inyectado y validando estrictamente el JSON.

    `completar` es `async (system: str, user: str) -> str`. Si la respuesta no
    es un JSON válido o no pasa la validación, se reintenta hasta `reintentos`
    veces indicándole el error al modelo; si agota los reintentos, lanza
    `ValueError`.
    """
    sistema = _prompt_sistema()
    base = _prompt_usuario(
        persona=persona, historial=historial, objetivo=objetivo, readiness=readiness
    )
    usuario = base
    ultimo_error = "la respuesta no es un objeto JSON válido"
    for _ in range(reintentos + 1):
        texto = await completar(sistema, usuario)
        data = _extraer_json(texto)
        if data is None:
            ultimo_error = "la respuesta no es un objeto JSON válido"
        else:
            try:
                return _construir_plan(data)
            except ValueError as exc:
                ultimo_error = str(exc)
        usuario = _con_error(base, ultimo_error)
    raise ValueError(f"No se pudo generar un plan de entrenamiento válido: {ultimo_error}")


def prompt_collage(plan: WorkoutPlan) -> str:
    """Prompt para generar UNA imagen collage (grid limpio) con los ejercicios del plan."""
    lineas = [
        "Genera una sola imagen collage de entrenamiento en un grid limpio y ordenado, "
        f'para el plan titulado "{plan.titulo}". Una celda por ejercicio:'
    ]
    for ejercicio in plan.ejercicios:
        lineas.append(
            f"- {ejercicio.nombre}: {ejercicio.series} series de "
            f"{ejercicio.repeticiones} repeticiones"
        )
    return "\n".join(lineas)