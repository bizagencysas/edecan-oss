"""Generic product context shared by Edecán agents."""

from __future__ import annotations

PRODUCT_LIVE_FACT_ES = (
    "Edecán es un asistente local-first y configurable. Sus capacidades dependen "
    "de los proveedores, herramientas y permisos configurados por cada operador."
)
PRODUCT_LIVE_FACT_EN = (
    "Edecán is a configurable, local-first assistant. Its capabilities depend on "
    "the providers, tools, and permissions configured by each operator."
)

_EMPTY_RULES: tuple[str, ...] = ()


def product_live_fact_for_language(language: str) -> str:
    return PRODUCT_LIVE_FACT_EN if language == "en" else PRODUCT_LIVE_FACT_ES


def product_companion_rules_for_language(language: str) -> tuple[str, ...]:
    return _EMPTY_RULES


def never_refuse_rules_for_language(language: str) -> tuple[str, ...]:
    return _EMPTY_RULES


def bot_companion_rules_for_language(language: str) -> tuple[str, ...]:
    return _EMPTY_RULES


def bot_operating_rules_for_language(language: str) -> tuple[str, ...]:
    return _EMPTY_RULES
