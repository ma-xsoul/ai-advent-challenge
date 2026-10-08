"""RAG: корпус, нарезка, эмбеддинги и локальный индекс (день 21)."""

from .chunking import STRATEGIES, STRATEGY_TITLES, Chunk, chunk
from .corpus import Document, collect
from .embeddings import EmbeddingError, HashingEmbedder, OllamaEmbedder
from .index import DEFAULT_PATH, Hit, IndexNotReady, RagIndex
from .probes import PROBES, Probe, evaluate

__all__ = [
    "Chunk",
    "DEFAULT_PATH",
    "Document",
    "EmbeddingError",
    "HashingEmbedder",
    "Hit",
    "IndexNotReady",
    "OllamaEmbedder",
    "PROBES",
    "Probe",
    "RagIndex",
    "STRATEGIES",
    "STRATEGY_TITLES",
    "chunk",
    "collect",
    "evaluate",
]
