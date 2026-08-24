"""Recupera una tool call que el modelo escribió como texto en vez de `tool_calls`.

Llama 3.3 y otros instruct a veces conocen la herramienta correcta y aún así
la emiten como JSON en `content` (a menudo dentro de un fence markdown). Scout
escribe ``[usar_computadora accion="screenshot" parametros={}]`` mezclado con
prosa y tags de voz (`[excited]`). El agente ya tiene el contrato estructurado;
este módulo traduce esa fuga de canal solo si el `name` está entre las
herramientas que se ofrecieron en este turno.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

_FENCE = re.compile(
    r"^```(?:json|javascript|js)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL | re.IGNORECASE,
)
_NOMBRE_TOOL = re.compile(r"[a-z][a-z0-9_]{2,40}")


@dataclass(frozen=True, slots=True)
class EmittedToolCall:
    """Misma forma que el `.tool_call` de un chunk de stream (duck-typed)."""

    id: str
    name: str
    arguments: dict[str, Any]


def parse_emitted_tool_call(text: str, allowed_names: set[str]) -> EmittedToolCall | None:
    """Si `text` es únicamente una invocación a una tool ofrecida, la devuelve.

    Acepta las formas que ya vimos en producción:

    * ``{"name": "calculadora", "parameters": {...}}``
    * ``{"name": "calculadora", "arguments": {...}}``
    * ``{"tool_call": {"name": "...", "arguments": {...}}}``
    * ``{"function": {"name": "...", "arguments": {...}}}``
    * cualquiera de las anteriores dentro de `````json ... ``` ``

    Rechaza prosa alrededor, JSON que no nombra una tool ofrecida, y texto
    vacío. El caller es quien decide no mostrar el JSON al usuario.
    """
    if not text or not allowed_names:
        return None
    cuerpo = text.strip()
    cercado = _FENCE.match(cuerpo)
    if cercado:
        cuerpo = cercado.group(1).strip()
    if not cuerpo.startswith("{"):
        return None
    try:
        objeto, fin = json.JSONDecoder().raw_decode(cuerpo)
    except json.JSONDecodeError:
        return None
    if cuerpo[fin:].strip():
        return None
    return _como_llamada(objeto, allowed_names)


def parse_emitted_tool_calls(text: str, allowed_names: set[str]) -> list[EmittedToolCall]:
    """Una o varias invocaciones fugadas: JSON entero o corchetes de Scout."""
    unica = parse_emitted_tool_call(text, allowed_names)
    if unica is not None:
        return [unica]
    return _llamadas_en_corchetes(text, allowed_names)


def parece_json_de_tool(text: str) -> bool:
    """¿El prefijo (aún incompleto) parece el JSON de una tool call, no prosa?"""
    inicio = text.lstrip()
    return inicio.startswith("{") or inicio.startswith("```")


def parece_llamada_en_corchetes(text: str, allowed_names: set[str]) -> bool:
    """¿Hay (o está empezando) un ``[usar_computadora …]`` de una tool ofrecida?

    No confunde tags de voz (``[excited]``, ``[pause]``) con herramientas.
    """
    if not text or not allowed_names:
        return False
    lower = text.lower()
    for name in allowed_names:
        n = name.lower()
        if f"[{n}" in lower or f"[ {n}" in lower:
            return True
    cola = re.search(r"\[\s*([a-z][a-z0-9_]{0,40})$", lower)
    if cola is None:
        return False
    frag = cola.group(1)
    return any(name.lower().startswith(frag) for name in allowed_names)


def _como_llamada(objeto: object, allowed_names: set[str]) -> EmittedToolCall | None:
    if not isinstance(objeto, dict):
        return None
    for clave in ("tool_call", "function"):
        interno = objeto.get(clave)
        if isinstance(interno, dict) and "name" in interno:
            return _como_llamada(interno, allowed_names)
    nombre = objeto.get("name")
    if not isinstance(nombre, str) or nombre not in allowed_names:
        return None
    argumentos = objeto.get("arguments")
    if not isinstance(argumentos, dict):
        argumentos = objeto.get("parameters")
    if not isinstance(argumentos, dict):
        argumentos = {}
    return EmittedToolCall(id=str(uuid4()), name=nombre, arguments=argumentos)


def _llamadas_en_corchetes(text: str, allowed_names: set[str]) -> list[EmittedToolCall]:
    llamadas: list[EmittedToolCall] = []
    i = 0
    while i < len(text):
        abre = text.find("[", i)
        if abre < 0:
            break
        resto = text[abre + 1 :]
        nombre_m = _NOMBRE_TOOL.match(resto.lstrip())
        if nombre_m is None:
            i = abre + 1
            continue
        hueco = len(resto) - len(resto.lstrip())
        nombre = nombre_m.group(0)
        if nombre not in allowed_names:
            i = abre + 1
            continue
        cuerpo_ini = abre + 1 + hueco + len(nombre)
        cierre = _cerrar_corchete(text, cuerpo_ini)
        if cierre is None:
            break
        argumentos = _atributos_de_corchete(text[cuerpo_ini:cierre])
        llamadas.append(EmittedToolCall(id=str(uuid4()), name=nombre, arguments=argumentos))
        i = cierre + 1
    return llamadas


def _cerrar_corchete(text: str, desde: int) -> int | None:
    """Índice del ``]`` que cierra, respetando strings y llaves de parametros."""
    i = desde
    en_comillas: str | None = None
    llaves = 0
    while i < len(text):
        ch = text[i]
        if en_comillas is not None:
            if ch == "\\" and i + 1 < len(text):
                i += 2
                continue
            if ch == en_comillas:
                en_comillas = None
            i += 1
            continue
        if ch in {'"', "'"}:
            en_comillas = ch
            i += 1
            continue
        if ch == "{":
            llaves += 1
        elif ch == "}":
            llaves = max(0, llaves - 1)
        elif ch == "]" and llaves == 0:
            return i
        i += 1
    return None


def _atributos_de_corchete(cuerpo: str) -> dict[str, Any]:
    argumentos: dict[str, Any] = {}
    i = 0
    n = len(cuerpo)
    while i < n:
        while i < n and cuerpo[i].isspace():
            i += 1
        clave_m = re.match(r"[A-Za-z_]\w*", cuerpo[i:])
        if clave_m is None:
            i += 1
            continue
        clave = clave_m.group(0)
        i += len(clave)
        while i < n and cuerpo[i].isspace():
            i += 1
        if i >= n or cuerpo[i] != "=":
            continue
        i += 1
        while i < n and cuerpo[i].isspace():
            i += 1
        valor, i = _leer_valor_atributo(cuerpo, i)
        if clave == "parametros" and isinstance(valor, str):
            valor = _objeto_suelto(valor)
        argumentos[clave] = valor
    return argumentos


def _leer_valor_atributo(cuerpo: str, i: int) -> tuple[Any, int]:
    if i >= len(cuerpo):
        return "", i
    ch = cuerpo[i]
    if ch in {'"', "'"}:
        fin = i + 1
        while fin < len(cuerpo):
            if cuerpo[fin] == "\\" and fin + 1 < len(cuerpo):
                fin += 2
                continue
            if cuerpo[fin] == ch:
                crudo = cuerpo[i + 1 : fin]
                return crudo.encode("utf-8").decode("unicode_escape") if "\\" in crudo else crudo, fin + 1
            fin += 1
        return cuerpo[i + 1 :], len(cuerpo)
    if ch == "{":
        profundidad = 0
        fin = i
        en_comillas: str | None = None
        while fin < len(cuerpo):
            actual = cuerpo[fin]
            if en_comillas is not None:
                if actual == "\\" and fin + 1 < len(cuerpo):
                    fin += 2
                    continue
                if actual == en_comillas:
                    en_comillas = None
                fin += 1
                continue
            if actual in {'"', "'"}:
                en_comillas = actual
            elif actual == "{":
                profundidad += 1
            elif actual == "}":
                profundidad -= 1
                if profundidad == 0:
                    return cuerpo[i : fin + 1], fin + 1
            fin += 1
        return cuerpo[i:], len(cuerpo)
    fin = i
    while fin < len(cuerpo) and not cuerpo[fin].isspace() and cuerpo[fin] not in {"]"}:
        fin += 1
    return cuerpo[i:fin], fin


def _objeto_suelto(texto: str) -> dict[str, Any]:
    s = texto.strip()
    if not s:
        return {}
    if not s.startswith("{"):
        s = "{" + s + "}"
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    inner = s[1:-1].strip() if s.startswith("{") and s.endswith("}") else s
    if not inner:
        return {}
    out: dict[str, Any] = {}
    for m in re.finditer(
        r'([A-Za-z_]\w*)\s*:\s*(?:"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\'|([^,}]+))',
        inner,
    ):
        if m.group(2) is not None:
            valor: Any = m.group(2)
        elif m.group(3) is not None:
            valor = m.group(3)
        else:
            crudo = m.group(4).strip()
            if crudo.lower() == "true":
                valor = True
            elif crudo.lower() == "false":
                valor = False
            elif re.fullmatch(r"-?\d+", crudo):
                valor = int(crudo)
            else:
                valor = crudo
        out[m.group(1)] = valor
    return out
