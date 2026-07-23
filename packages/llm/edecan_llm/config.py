"""`LLMProviderConfig` — selección de proveedor LLM elegida por el tenant.

Contrato **pinned** (`ARCHITECTURE.md` §12, WP-V3-01) que consume
`LLMRouter` (este paquete, ver `router.py`) y que produce la pantalla de
"Configuración" de la app de escritorio (fuera de este paquete): describe
QUÉ proveedor usar y con qué credenciales, elegido en runtime por el tenant
(bring-your-own-credentials) en vez de fijo por variables de entorno de
plataforma (`DIRECCION_ACTUAL.md`, "Nuevo requisito: conectar el LLM vía CLI
local"). Los nombres/tipos de los campos son el contrato — otros work
packages v3 se escriben en paralelo contra esta forma exacta.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

# Valores válidos de `kind` (documentados aquí; NO se validan en el
# constructor a propósito — `LLMProviderConfig` es un contenedor de datos
# simple y `LLMRouter._build_provider()` es quien decide qué hacer con un
# `kind` desconocido, con un `LLMError` claro. Dejarlo sin validar acá evita
# que este dataclass tenga que conocer de antemano proveedores que agregue
# un work package futuro — "Cualquier IA — extensibilidad genérica",
# DIRECCION_ACTUAL.md).
PROVIDER_KINDS: frozenset[str] = frozenset(
    {"anthropic", "openai_compat", "vertex", "claude_cli", "codex_cli", "ollama"}
)


@dataclass(frozen=True)
class LLMProviderConfig:
    """Selección de proveedor LLM + credenciales, elegida por el tenant.

    Campos de `extra` según `kind` (detalle completo en
    `docs/proveedores-llm.md`):

    - `vertex`: ``{"mode": "api_key"|"service_account", "project_id": str,
      "region": str (default "us-central1"), "service_account_json": str}``.
    - `claude_cli` / `codex_cli`: ``{"binary_path": str, "timeout_seconds":
      float}`` (ambos opcionales — si faltan, `LLMRouter` cae a
      `settings.CLAUDE_CLI_PATH`/`CODEX_CLI_PATH`/`LLM_CLI_TIMEOUT_SECONDS`,
      ver `ARCHITECTURE.md` §12.c/§12.g; si tampoco hay settings, el binario
      se busca en el `PATH` y el timeout usa el default hardcodeado del
      provider).
    """

    kind: str  # "anthropic"|"openai_compat"|"vertex"|"claude_cli"|"codex_cli"|"ollama"
    api_key: str | None = None
    base_url: str | None = None
    model_principal: str | None = None
    model_rapido: str | None = None
    model_profundo: str | None = None
    reasoning_effort_profundo: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMProviderConfig:
        """Construye desde un dict tolerante a campos extra/desconocidos.

        Pensado para deserializar lo que guarda la pantalla de Configuración
        (o un fixture de test): cualquier clave de `d` que no sea un campo de
        este dataclass se ignora en silencio en vez de reventar.
        """
        campos_validos = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in campos_validos}
        if "kind" not in kwargs:
            raise ValueError("LLMProviderConfig.from_dict requiere la clave 'kind'")
        kwargs.setdefault("extra", {})
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict plano (p. ej. para guardar en la config del tenant)."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["extra"] = dict(data["extra"])
        return data
