"""Legacy provider configuration data.

Runtime inference no longer consumes this tenant-owned shape: composition now
happens behind the generic provider factory in :mod:`edecan_llm.router`.
Keeping the dataclass makes old encrypted settings readable during upgrades
without exposing them as an active model-selection mechanism.

Nota — selección de modelo POR TAREA en Workers AI (no en este dataclass):
la cuenta activa hoy usa Cloudflare Workers AI, no este `LLMProviderConfig`
(ver ``kind="workers_ai"`` arriba: es solo un valor legible, sin campos
propios). La selección por tarea real vive en
:mod:`edecan_llm.model_selection` (``WORKERS_AI_TASK_DEFAULTS``,
``resolve_task_model``) y el dueño la sobrescribe SIN recompilar editando
``platform-config.json`` (``~/.edecan/data/platform-config.json`` en local;
ver ``apps/local/edecan_local/runtime.py::_PLATFORM_CONFIG_KEYS``, fuera del
alcance de este módulo). NOTA: por la regla ``sin_nombres_de_modelo_en_el_
codigo`` de ``config/modelos.yml`` (exigida por
``tests/test_profile_routing.py::test_no_literal_model_names_in_llm_package``),
ni este archivo ni ``model_selection.py`` pueden escribir el ID completo de
un modelo de zai-org/moonshotai — los IDs concretos abajo son de OpenAI-OSS
(sí permitido) o se describen sin el prefijo del proveedor:

- ``WORKERS_AI_CHAT_MODEL`` — ya cableada hoy (``LLMRouter`` la lee y se la
  pasa a ``TaskRouter`` como modelo de chat). Cubre el alias "rapido": chat,
  voz y herramientas ligeras. Default recomendado, medido contra la cuenta
  del dueño el 27-jul-2026: ``@cf/openai/gpt-oss-20b`` (0.99s, 128k de
  contexto) — 5x más rápido que el modelo de razonamiento que usa hoy
  ``chat_rapido`` en ``config/modelos.yml``.
- ``WORKERS_AI_ENGINEERING_MODEL`` — documentada pero TODAVÍA no leída por
  ningún módulo (agregarla a ``_PLATFORM_CONFIG_KEYS`` y a ``router.py`` le
  toca a quien cablee esto, no a este cambio). Cubriría el alias
  "ingenieria_software" (el IDE). Recomendación medida: el modelo de código
  de Moonshot que ya aparece como ``modelo_alternativo`` en
  ``perfiles.ingenieria_software`` de ``config/modelos.yml`` (1.25s, 262k de
  contexto, especializado en código) — hoy ese perfil se cambia editando
  ``perfiles.ingenieria_software.modelo`` en ese mismo archivo, no vía
  ``platform-config.json``.

Los dos modelos que usan hoy ``chat_rapido`` e ``ingenieria_software`` en
``config/modelos.yml`` son de RAZONAMIENTO: queman ~150 tokens pensando antes
de responder y, con ``max_tokens`` chico, pueden devolver ``content`` vacío
porque el razonamiento se comió el presupuesto.
``model_selection.advertencia_modelo_de_razonamiento`` existe para que nadie
los ponga en una ruta de baja latencia sin saberlo.
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
    {"workers_ai", "anthropic", "openai_compat", "vertex", "claude_cli", "ollama", "azure_openai"}
)


@dataclass(frozen=True)
class LLMProviderConfig:
    """Selección de proveedor LLM + credenciales, elegida por el tenant.

    Campos de `extra` según `kind` (detalle completo en
    `docs/proveedores-llm.md`):

    - `vertex`: ``{"mode": "api_key"|"service_account", "project_id": str,
      "region": str (default "us-central1"), "service_account_json": str}``.
    Esta forma existe únicamente para leer configuraciones antiguas. El
    ``LLMRouter`` actual no permite que el tenant elija estos campos.
    """

    kind: str
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
