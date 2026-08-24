"""Web content security boundary: defensa contra prompt injection en contenido externo (§78).

Todo contenido que llega de herramientas externas (web pages, emails,
documents, repos, tool results) se considera UNTRUSTED DATA.

Este módulo proporciona funciones para:
1. Envolver contenido externo en delimiters que el modelo puede distinguir
2. Escanear contenido en busca de patrones de prompt injection
3. Sanitizar contenido antes de incluirlo en el prompt
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|all|prior)\s+instructions", re.I),
    re.compile(r"you\s+are\s+(now|actually)\s+", re.I),
    re.compile(r"disregard\s+(the\s+above|all|previous)", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"system\s+prompt\s*:", re.I),
    re.compile(r"<\s*system\s*>", re.I),
    re.compile(r"reveal\s+(your|the)\s+(system\s+)?prompt", re.I),
    re.compile(r"what\s+are\s+your\s+instructions", re.I),
    re.compile(r"act\s+as\s+(if\s+you\s+are\s+)?a\s+(different|new)", re.I),
    re.compile(r"forget\s+(everything|all\s+rules|your\s+instructions)", re.I),
    re.compile(r"do\s+not\s+follow\s+(your|the|any)\s+rules", re.I),
    re.compile(r"override\s+(your|the|all)\s+(rules|instructions|safety)", re.I),
]


@dataclass
class InjectionScanResult:
    is_suspicious: bool
    patterns_found: list[str]
    content_preview: str


def scan_for_injection(content: str) -> InjectionScanResult:
    """Escanea contenido externo en busca de patrones de prompt injection.

    Non-blocking: solo registra y marca, no bloquea el contenido.
    El agente decide si usarlo o no.
    """
    if not content:
        return InjectionScanResult(is_suspicious=False, patterns_found=[], content_preview="")
    found: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(content):
            found.append(pattern.pattern)
    return InjectionScanResult(
        is_suspicious=bool(found),
        patterns_found=found,
        content_preview=content[:200],
    )


def wrap_untrusted(content: str, source: str = "web") -> str:
    """Envuelve contenido externo en delimiters que lo marcan como data, no instrucción.

    El modelo debe tratar el contenido dentro de los delimiters como
    información, no como órdenes.
    """
    if not content:
        return ""
    return f"<contenido_externo fuente=\"{source}\">\n{content}\n</contenido_externo>"


def sanitize_web_content(content: str, max_length: int = 14000) -> str:
    """Sanitiza contenido web antes de incluirlo en el prompt.

    1. Trunca a max_length
    2. Remueve scripts y style tags
    3. Escapa delimiters de sistema
    """
    if not content:
        return ""
    # Remove script/style blocks
    content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.I)
    content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.I)
    # Remove HTML comments
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    # Truncate
    if len(content) > max_length:
        content = content[:max_length] + "\n[... contenido truncado ...]"
    return content.strip()
