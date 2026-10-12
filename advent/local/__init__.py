"""Локальные модели через Ollama (неделя 7)."""

from .ollama import OllamaClient, OllamaError
from .transport import OllamaTransport

__all__ = ["OllamaClient", "OllamaError", "OllamaTransport"]
