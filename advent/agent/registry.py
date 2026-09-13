"""
Реестр агентов: один процесс приложения держит много экземпляров агента.

Flask не хранит состояние между запросами, а у агента оно есть — стек и
расход. Реестр связывает идентификатор сессии с живым экземпляром. Это тот
самый ответ на вопрос куратора «можно ли заспавнить сто агентов с разными
конфигами»: инстанс приложения один, агентов внутри — сколько нужно.

С хранилищем реестр переживает перезапуск: агента, которого нет в памяти,
он сначала ищет на диске и только потом создаёт заново.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Optional

from .agent import Agent
from .store import JsonStore, StoredSession


# Фабрика получает сохранённый слепок или None и возвращает агента.
# Так реестр не знает ни про транспорт, ни про конфиг по умолчанию.
# Optional вместо `| None`: это выражение, а не аннотация, и на 3.9 союз типов не работает.
AgentFactory = Callable[[Optional[dict[str, Any]]], Agent]


class AgentRegistry:
    def __init__(self, factory: AgentFactory, store: JsonStore | None = None) -> None:
        self._factory = factory
        self._store = store
        self._agents: dict[str, Agent] = {}
        # Дев-сервер Flask многопоточный: словарь трогаем под замком.
        self._lock = threading.Lock()

    @property
    def persistent(self) -> bool:
        return self._store is not None

    def get(self, session_id: str) -> Agent:
        """Агент сессии: из памяти, иначе с диска, иначе новый."""
        with self._lock:
            agent = self._agents.get(session_id)
            if agent is None:
                saved = self._store.load(session_id) if self._store else None
                agent = self._factory(saved)
                self._agents[session_id] = agent
            return agent

    def peek(self, session_id: str) -> Agent:
        """
        Посмотреть, не создавая. Из памяти или с диска — возвращается и
        регистрируется, это настоящая сессия. Незнакомый id получает пустого
        агента без регистрации: просмотр страницы не должен плодить записи.
        """
        with self._lock:
            agent = self._agents.get(session_id)
            if agent is not None:
                return agent
            saved = self._store.load(session_id) if self._store else None
            if saved is not None:
                agent = self._factory(saved)
                self._agents[session_id] = agent
                return agent
            return self._factory(None)

    def reset(self, session_id: str) -> Agent:
        """Новый агент вместо старого: диалог начинается заново, файл удаляется."""
        with self._lock:
            if self._store:
                self._store.delete(session_id)
            agent = self._factory(None)
            self._agents[session_id] = agent
            return agent

    def reconfigure(self, session_id: str, **changes: Any) -> Agent:
        """
        Сменить конфиг агента, не теряя диалог: старый экземпляр заменяется
        новым с тем же стеком. Так лимит контекста меняется по ходу демо.
        """
        with self._lock:
            agent = self._agents.get(session_id)
            if agent is None:
                saved = self._store.load(session_id) if self._store else None
                agent = self._factory(saved)
            agent = agent.with_config(**changes)
            self._agents[session_id] = agent
            return agent

    def save(self, session_id: str) -> None:
        """Сбросить стек агента на диск. Без хранилища — ничего не делает."""
        if not self._store:
            return
        with self._lock:
            agent = self._agents.get(session_id)
            if agent is not None:
                self._store.save(session_id, agent)

    def evict(self, session_id: str) -> bool:
        """
        Выкинуть агента из памяти, файл оставить. Следующий get() пойдёт
        на диск — так перезапуск процесса проверяется без перезапуска.
        """
        with self._lock:
            return self._agents.pop(session_id, None) is not None

    def stored(self, session_id: str) -> StoredSession | None:
        if not self._store:
            return None
        return self._store.describe(session_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def __contains__(self, session_id: object) -> bool:
        with self._lock:
            return session_id in self._agents
