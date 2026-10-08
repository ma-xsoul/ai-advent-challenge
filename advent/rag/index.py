"""
Локальный индекс — день 21.

Один файл SQLite (data/day21/index.sqlite3):

- chunks  — куски обеих стратегий с метаданными: strategy, chunk_id,
            source, title, section, kind, строки начала и конца, текст;
- vectors — вектор каждого куска (float32, нормирован);
- meta    — чем и когда собран индекс: модель, размерность, время
            сборки каждой стратегии, корпус;
- cache   — векторы по хэшу текста. Переживает пересборку: неизменённые
            куски второй раз не считаются — пересобрать индекс после
            правки пары файлов можно за секунды.

Поиск — честный перебор: вектор вопроса умножается на матрицу всех
векторов стратегии, сверху — top-K по близости. На шестистах кусках это
миллисекунды; FAISS понадобился бы на сотнях тысяч.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..mcp.client import PROJECT_ROOT
from .chunking import STRATEGIES, Chunk, chunk, stats
from .corpus import CHARS_PER_PAGE, Document
from .embeddings import Embedder


DEFAULT_PATH = PROJECT_ROOT / "data" / "day21" / "index.sqlite3"

Progress = Callable[[dict[str, Any]], None]


class IndexNotReady(RuntimeError):
    """Индекса нет или он собран другой моделью."""


@dataclass(frozen=True)
class Hit:
    rank: int
    score: float
    chunk_id: str
    source: str
    title: str
    section: str
    kind: str
    start_line: int
    end_line: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RagIndex:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)

    # ── сборка ───────────────────────────────────────────────────────────

    def build(
        self,
        documents: list[Document],
        embedder: Embedder,
        *,
        strategies: tuple[str, ...] = STRATEGIES,
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        emit = progress or (lambda event: None)
        started = time.perf_counter()
        corpus = _corpus_stats(documents)
        emit({"type": "corpus", **corpus})

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            _schema(db)
            built: dict[str, Any] = {}
            dims = None
            for strategy in strategies:
                strategy_started = time.perf_counter()
                pieces = chunk(documents, strategy)
                emit({"type": "chunked", "strategy": strategy, **stats(pieces)})
                vectors, cached = self._vectors(db, pieces, embedder, strategy, emit)
                dims = int(vectors.shape[1]) if vectors.size else dims
                db.execute("DELETE FROM vectors WHERE chunk IN (SELECT id FROM chunks WHERE strategy = ?)", (strategy,))
                db.execute("DELETE FROM chunks WHERE strategy = ?", (strategy,))
                for piece, vector in zip(pieces, vectors):
                    cursor = db.execute(
                        "INSERT INTO chunks (strategy, chunk_id, source, title, section, kind, start_line, end_line, text, chars) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (strategy, piece.chunk_id, piece.source, piece.title, piece.section, piece.kind,
                         piece.start_line, piece.end_line, piece.text, piece.chars),
                    )
                    db.execute("INSERT INTO vectors (chunk, vector) VALUES (?, ?)", (cursor.lastrowid, vector.tobytes()))
                built[strategy] = {
                    **stats(pieces),
                    "embedded": len(pieces) - cached,
                    "cached": cached,
                    "ms": round((time.perf_counter() - strategy_started) * 1000),
                }
                emit({"type": "built", "strategy": strategy, **built[strategy]})

            meta = {
                "model": embedder.name,
                "dims": dims,
                "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "ms": round((time.perf_counter() - started) * 1000),
                "corpus": corpus,
                "strategies": built,
            }
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('index', ?)", (json.dumps(meta, ensure_ascii=False),))
        info = self.info()
        emit({"type": "done", "info": info})
        return info

    def _vectors(
        self, db: sqlite3.Connection, pieces: list[Chunk], embedder: Embedder, strategy: str, emit: Progress
    ) -> tuple[np.ndarray, int]:
        """Векторы кусков: из кэша, если текст уже встречался, иначе — у модели."""
        keys = [_hash(embedder.name, piece.text) for piece in pieces]
        known: dict[str, np.ndarray] = {}
        for start in range(0, len(keys), 500):
            part = keys[start : start + 500]
            marks = ",".join("?" * len(part))
            for key, blob in db.execute(f"SELECT hash, vector FROM cache WHERE hash IN ({marks})", part):
                known[key] = np.frombuffer(blob, dtype=np.float32)

        missing = [index for index, key in enumerate(keys) if key not in known]
        emit({"type": "embedding", "strategy": strategy, "done": 0, "total": len(missing), "cached": len(pieces) - len(missing)})
        step = 64
        for start in range(0, len(missing), step):
            batch = missing[start : start + step]
            fresh = embedder.embed([pieces[index].text for index in batch])
            for index, vector in zip(batch, fresh):
                known[keys[index]] = vector
                db.execute("INSERT OR REPLACE INTO cache (hash, vector) VALUES (?, ?)", (keys[index], vector.tobytes()))
            emit({"type": "embedding", "strategy": strategy, "done": min(start + step, len(missing)), "total": len(missing)})

        if not pieces:
            return np.zeros((0, 0), dtype=np.float32), 0
        return np.vstack([known[key] for key in keys]), len(pieces) - len(missing)

    # ── поиск ────────────────────────────────────────────────────────────

    def search(self, query: str, embedder: Embedder, strategy: str, k: int = 5) -> list[Hit]:
        info = self.info()
        if not info["built"]:
            raise IndexNotReady("Индекс ещё не собран")
        if info["model"] != embedder.name:
            raise IndexNotReady(f"Индекс собран моделью {info['model']}, а вопрос — {embedder.name}: векторы несравнимы")
        rows, matrix = self._matrix(strategy)
        if not rows:
            return []
        vector = embedder.embed([query])[0]
        scores = matrix @ vector
        order = np.argsort(-scores)[:k]
        return [
            Hit(rank=rank, score=round(float(scores[index]), 4), **rows[index])
            for rank, index in enumerate(order, start=1)
        ]

    def _matrix(self, strategy: str) -> tuple[list[dict[str, Any]], np.ndarray]:
        with self._connect() as db:
            records = db.execute(
                "SELECT c.chunk_id, c.source, c.title, c.section, c.kind, c.start_line, c.end_line, c.text, v.vector "
                "FROM chunks c JOIN vectors v ON v.chunk = c.id WHERE c.strategy = ? ORDER BY c.id",
                (strategy,),
            ).fetchall()
        rows = [
            {key: record[key] for key in ("chunk_id", "source", "title", "section", "kind", "start_line", "end_line", "text")}
            for record in records
        ]
        matrix = np.vstack([np.frombuffer(record["vector"], dtype=np.float32) for record in records]) if records else np.zeros((0, 0))
        return rows, matrix

    # ── что внутри ───────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"built": False, "path": str(self.path)}
        with self._connect() as db:
            _schema(db)
            row = db.execute("SELECT value FROM meta WHERE key = 'index'").fetchone()
            cached = db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        if row is None:
            return {"built": False, "path": str(self.path)}
        return {"built": True, "path": str(self.path), "bytes": self.path.stat().st_size, "cache": cached, **json.loads(row["value"])}

    def sample(self, strategy: str, source: str, limit: int = 50) -> list[dict[str, Any]]:
        """Куски одного файла в одной стратегии — посмотреть, как он нарезан."""
        with self._connect() as db:
            records = db.execute(
                "SELECT chunk_id, section, start_line, end_line, chars, text FROM chunks "
                "WHERE strategy = ? AND source = ? ORDER BY id LIMIT ?",
                (strategy, source, limit),
            ).fetchall()
        return [dict(record) for record in records]

    def sources(self) -> list[str]:
        if not self.path.exists():
            return []
        with self._connect() as db:
            _schema(db)
            return [row[0] for row in db.execute("SELECT DISTINCT source FROM chunks ORDER BY source")]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db


def _schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            chunk_id TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            section TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            text TEXT NOT NULL,
            chars INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunks_strategy ON chunks (strategy);
        CREATE TABLE IF NOT EXISTS vectors (chunk INTEGER PRIMARY KEY, vector BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS cache (hash TEXT PRIMARY KEY, vector BLOB NOT NULL);
        """
    )


def _hash(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}\n{text}".encode("utf-8")).hexdigest()


def _corpus_stats(documents: list[Document]) -> dict[str, Any]:
    chars = sum(len(document.text) for document in documents)
    kinds: dict[str, int] = {}
    for document in documents:
        kinds[document.kind] = kinds.get(document.kind, 0) + 1
    return {"documents": len(documents), "chars": chars, "pages": round(chars / CHARS_PER_PAGE), "kinds": kinds}
