"""
Эмбеддинги — день 21.

Текст → вектор, «отпечаток смысла»: тексты о похожем дают близкие
векторы, даже если слова в них разные. Считаем локально через Ollama
моделью bge-m3 — многоязычной, она понимает и русские комментарии, и код.
Облако не нужно: в дне 28 весь RAG станет локальным без переделок.

Векторы нормируются на длину 1 — тогда близость двух текстов это просто
скалярное произведение (косинус угла между векторами).

В тестах вместо Ollama — HashingEmbedder: слова раскладываются по
корзинам хэшем, общие слова дают близкие векторы. Смысла он не понимает,
но для проверки индекса и поиска этого достаточно.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Protocol

import numpy as np
import requests


DEFAULT_MODEL = "bge-m3"
DEFAULT_URL = "http://127.0.0.1:11434"
BATCH = 32
TIMEOUT_SECONDS = 120


class EmbeddingError(RuntimeError):
    """Модель эмбеддингов недоступна или ответила не тем."""


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """Нормированные векторы, по строке на текст: shape (len(texts), dims), float32."""


class OllamaEmbedder:
    def __init__(self, model: str = DEFAULT_MODEL, url: str | None = None, *, timeout: int = TIMEOUT_SECONDS) -> None:
        self.model = model
        self.url = (url or os.getenv("OLLAMA_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.name = f"ollama:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = texts[start : start + BATCH]
            try:
                response = requests.post(
                    f"{self.url}/api/embed", json={"model": self.model, "input": batch}, timeout=self.timeout
                )
            except requests.RequestException as error:
                raise EmbeddingError(f"Ollama недоступен ({self.url}): {error}") from error
            if response.status_code == 404:
                raise EmbeddingError(f"В Ollama нет модели {self.model} — ollama pull {self.model}")
            if not response.ok:
                raise EmbeddingError(f"Ollama ответил {response.status_code}: {response.text[:200]}")
            data = response.json().get("embeddings")
            if not isinstance(data, list) or len(data) != len(batch):
                raise EmbeddingError("Ollama вернул не столько векторов, сколько текстов")
            vectors.extend(data)
        return normalize(np.asarray(vectors, dtype=np.float32))


class HashingEmbedder:
    """Для тестов: мешок слов в 256 корзинах. Детерминирован и не ходит в сеть."""

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims
        self.name = f"hashing:{dims}"

    def embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"\w{3,}", text.lower()):
                bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims
                matrix[row, bucket] += 1.0
        return normalize(matrix)


def normalize(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix.reshape(0, matrix.shape[1] if matrix.ndim == 2 else 0)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)
