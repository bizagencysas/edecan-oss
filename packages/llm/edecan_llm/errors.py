"""Errores del paquete `edecan_llm` (`ARCHITECTURE.md` §10.6)."""

from __future__ import annotations


class LLMError(Exception):
    """Error base para cualquier fallo al hablar con un proveedor LLM."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code

    def __str__(self) -> str:
        if self.provider:
            return f"[{self.provider}] {self.message}"
        return self.message


class RateLimitedError(LLMError):
    """Se agotaron los reintentos ante 429 (rate limit) o 5xx del proveedor."""


class ProviderDownError(LLMError):
    """El proveedor es inalcanzable (timeout/error de conexión) tras agotar reintentos."""


class CLINotInstalledError(LLMError):
    """El binario del CLI (`claude`/`codex`) no está instalado o no se encuentra.

    Usada por `ClaudeCLIProvider`/`CodexCLIProvider` (WP-V3-03) cuando ni el
    `binary_path` explícito ni `shutil.which(...)` resuelven un ejecutable.
    """


class CLINotAuthenticatedError(LLMError):
    """El binario del CLI está instalado pero no autenticado en esta máquina.

    Se lanza cuando el proceso termina con código de error y su stderr/stdout
    sugiere que falta iniciar sesión (`claude login` / `codex login`).
    """
