"""`FakeLLMProvider` — doble determinista de `edecan_llm.base.LLMProvider` (§10.6).

Sin dependencia de `edecan_core`: solo `edecan_llm` (ya implementado) y
`edecan_evals.schema`. Se usa en modo offline de `edecan_evals.runner` para
correr el agente real (o, en tests de este paquete, un doble local del
contrato de `Agent`) sin red y de forma 100% reproducible: la "inteligencia"
del modelo se reemplaza por un `guion` — un mapa `regex -> GuionEntry` que se
prueba contra el ÚLTIMO mensaje de la petición (`req.messages[-1]`), en el
orden de definición. La primera coincidencia gana; si ninguna coincide se usa
`respuesta_por_defecto` (por defecto, un texto de cierre neutro), así el loop
de tool-use del agente siempre puede terminar aunque el guion de la suite
solo cubra el primer turno (p. ej. el mensaje de confirmación que sigue a la
ejecución de una herramienta).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from edecan_llm.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    StreamChunk,
    ToolCall,
    Usage,
)

from edecan_evals.schema import GuionEntry

RESPUESTA_POR_DEFECTO = GuionEntry(texto="Entendido.")


class FakeLLMProvider(LLMProvider):
    """Doble determinista de `LLMProvider`: sin red, guiado por regex.

    Implementa tanto `complete` como `stream` (ambos derivan de la misma
    resolución de guion) para servir sin importar qué método use el agente
    real internamente.
    """

    name = "fake"

    def __init__(
        self,
        guion: dict[str, GuionEntry] | None = None,
        *,
        respuesta_por_defecto: GuionEntry = RESPUESTA_POR_DEFECTO,
    ) -> None:
        self._guion = dict(guion or {})
        self._patrones = [
            (re.compile(patron, re.IGNORECASE), entrada) for patron, entrada in self._guion.items()
        ]
        self._por_defecto = respuesta_por_defecto
        self.llamadas: list[CompletionRequest] = []
        """Historial de peticiones recibidas — útil para depurar/afirmar en tests."""

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.llamadas.append(req)
        entrada = self._resolver(req)
        return _a_completion_response(entrada)

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        self.llamadas.append(req)
        entrada = self._resolver(req)
        respuesta = _a_completion_response(entrada)
        for llamada in respuesta.tool_calls:
            yield StreamChunk(type="tool_call", tool_call=llamada)
        if respuesta.text:
            yield StreamChunk(type="text", text=respuesta.text)
        yield StreamChunk(type="usage", usage=respuesta.usage)
        yield StreamChunk(type="stop")

    def _resolver(self, req: CompletionRequest) -> GuionEntry:
        ultimo = _texto_ultimo_mensaje(req.messages)
        for patron, entrada in self._patrones:
            if patron.search(ultimo):
                return entrada
        return self._por_defecto


def _a_completion_response(entrada: GuionEntry) -> CompletionResponse:
    if entrada.tool:
        llamada = ToolCall(id=_id_tool_call(entrada), name=entrada.tool, arguments=entrada.args)
        return CompletionResponse(
            text="",
            tool_calls=[llamada],
            usage=Usage(input_tokens=8, output_tokens=8),
            stop_reason="tool_use",
        )
    texto = entrada.texto or ""
    return CompletionResponse(
        text=texto,
        tool_calls=[],
        usage=Usage(input_tokens=8, output_tokens=max(1, len(texto.split()))),
        stop_reason="end",
    )


def _id_tool_call(entrada: GuionEntry) -> str:
    return f"toolu_fake_{entrada.tool}"


def _texto_ultimo_mensaje(messages: list[ChatMessage]) -> str:
    if not messages:
        return ""
    return _texto_de(messages[-1].content)


def _texto_de(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    partes: list[str] = []
    for bloque in content:
        tipo = bloque.get("type")
        if tipo == "text":
            partes.append(bloque.get("text", ""))
        elif tipo == "tool_result":
            valor = bloque.get("content", "")
            partes.append(valor if isinstance(valor, str) else str(valor))
    return "\n".join(parte for parte in partes if parte)
