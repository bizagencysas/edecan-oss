"""`edecan_core.tools` — contrato de herramientas del agente (ARCHITECTURE.md §10.7)."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult
from .registry import ToolRegistry

__all__ = ["Tool", "ToolContext", "ToolResult", "ToolRegistry"]
