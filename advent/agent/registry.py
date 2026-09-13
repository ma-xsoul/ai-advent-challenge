"""
Реестр агентов: один процесс приложения держит много экземпляров агента.

Flask не хранит состояние между запросами, а у агента оно есть — стек и
расход. Реестр связывает идентификатор сессии с живым экземпляром. Это тот
самый ответ на вопрос куратора «можно ли заспавнить сто агентов с разными
конфигами»: инстанс приложения один, агентов внутри — сколько нужно.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from .agent import Agent


class AgentRegistry:
    def __init__(self, factory: Callable[[], Agent]) -> None:
        self._factory = factory
        self._agents: dict[str, Agent] = {}
        # Дев-сервер Flask многопоточный: словарь трогаем под замком.
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Agent:
        """Агент сессии; если его ещё нет — создаётся фабрикой."""
        with self._lock:
            agent = self._agents.get(session_id)
            if agent is None:
                agent = self._factory()
                self._agents[session_id] = agent
            return agent

    def reset(self, session_id: str) -> Agent:
        """Новый агент вместо старого: диалог начинается заново."""
        with self._lock:
            agent = self._factory()
            self._agents[session_id] = agent
            return agent

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def __contains__(self, session_id: object) -> bool:
        with self._lock:
            return session_id in self._agents
