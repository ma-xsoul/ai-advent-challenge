"""
Модель памяти агента — день 11.

До сих пор память агента была одна: стек сообщений. Стратегии дня 10
решали, сколько его отправить в модель, но куча была одна, и шкала одна —
«раньше/позже». Этого не хватает, как только у данных появляются разные
сроки жизни.

«Отвечай коротко» сказано в первой реплике — это правило пользователя, и
жить оно должно дольше всего разговора. «Бюджет 800 тысяч» — свойство
текущей задачи: должно пережить диалог и умереть вместе с задачей, иначе
чужой бюджет протечёт в следующее ТЗ. «Давай глянем вариант с подпиской»
имеет смысл только здесь и сейчас.

Отсюда три слоя, и делятся они не по важности, а по времени жизни:

    chat  — текущий диалог,   умирает по «Новому диалогу»
    task  — текущая задача,   умирает по «Новой задаче»
    user  — сам пользователь, живёт, пока не удалим руками

Слои живут снаружи агента — как дерево веток в дне 10. Агент получает их
готовым текстом в системный промпт и про хранилище ничего не знает.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .agent import AgentReply
from .tokens import estimate_tokens
from .transport import Transport, TransportError


# Порядок важен: так слои идут и в промпте, и на странице — от самого
# короткоживущего к самому долгому.
SCOPES = ("chat", "task", "user")

SCOPE_TITLES = {
    "chat": "Краткосрочная память",
    "task": "Рабочая память",
    "user": "Долговременная память",
}

SCOPE_LIVES = {
    "chat": "до конца диалога",
    "task": "до конца задачи",
    "user": "пока не удалим",
}

# Заголовки блоков в системном промпте. Агент должен не просто получить
# данные, а знать, что памяти у него три и что они разного срока годности,
# — иначе он начнёт тащить детали прошлой задачи в новую.
SCOPE_PREFIXES = {
    "chat": "Краткосрочная память (только этот разговор):",
    "task": "Рабочая память (текущая задача):",
    "user": "Долговременная память (постоянные факты и правила пользователя, действуют всегда):",
}

MEMORY_HEADER = (
    "У тебя три раздельные памяти. Ниже — их содержимое. "
    "Правила из долговременной памяти выполняй всегда, даже если разговор начат заново. "
    "Данные рабочей памяти относятся только к текущей задаче."
)

# Сколько элементов держим в слое. Память без потолка — это та же
# непомещающаяся история, только в профиль.
MAX_ITEMS = {"chat": 12, "task": 30, "user": 30}

MAX_VALUE_CHARS = 200

ROUTER_MODES = ("agent", "heuristic", "manual")

ROUTER_TEMPERATURE = 0.2

# Нарезатель возвращает только новое и изменившееся, а не весь блок
# целиком — в отличие от фактов дня 10. Разница принципиальная: факты
# живут один диалог, и переписать их заново дёшево, а долговременную
# память переписывать на каждом ходу опасно — одна неудачная генерация
# сотрёт то, что пользователь сказал неделю назад.
ROUTER_INSTRUCTION = """Ты раскладываешь информацию из реплики пользователя по трём слоям памяти агента.

Слои и их срок жизни:
- chat — важно только для текущего разговора, исчезнет при «новом диалоге»;
- task — относится к текущей задаче, переживёт разговор, исчезнет вместе с задачей;
- user — про самого пользователя: как его зовут, кто он, как он просит с ним разговаривать, его постоянные правила. Живёт всегда.

Правила:
- верни только новое или изменившееся в этой реплике, не повторяй уже записанное;
- одно и то же не дублируй в разные слои — выбери один;
- ключ: короткое слово-идентификатор латиницей, например name, budget, style;
- значение: до 12 слов;
- если в реплике нечего запоминать — верни пустые объекты;
- в forget перечисли ключи, которые реплика отменила.

Ответ строго в JSON:
{"chat": {"ключ": "значение"}, "task": {}, "user": {}, "forget": []}"""

# Эвристика — нарочно грубая: ноль запросов, ноль токенов и заметно хуже
# нарезателя. На странице эти два режима стоят рядом именно для сравнения.
_USER_MARKERS = re.compile(
    r"меня зовут|я\s+всегда|я\s+обычно|обращайся|называй меня|отвечай\s+\w+|"
    r"я\s+работаю|я\s+пишу\s+на|мой\s+стиль|не\s+люблю|предпочитаю",
    re.IGNORECASE,
)
_TASK_MARKERS = re.compile(
    r"\d|бюджет|срок|платформ|верси|валют|требовани|задач|прилож|функц|"
    r"экспорт|дизайн|тем[аы]\b|уведомлен|синхрониз",
    re.IGNORECASE,
)


class MemoryError_(RuntimeError):
    """Слой памяти не читается или не пишется."""


@dataclass(frozen=True)
class MemoryItem:
    """Одна запись слоя: что помним, откуда и с какого хода."""

    key: str
    value: str
    source: str = "agent"
    turn: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "source": self.source, "turn": self.turn}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryItem:
        key = str(data.get("key", "")).strip()
        value = str(data.get("value", "")).strip()
        if not key or not value:
            raise ValueError("У записи памяти нет ключа или значения")
        source = str(data.get("source", "agent"))
        turn = data.get("turn", 0)
        return cls(key, value[:MAX_VALUE_CHARS], source if source in ("agent", "heuristic", "manual") else "agent", int(turn) if isinstance(turn, int) else 0)


@dataclass
class MemoryLayer:
    """Один слой: упорядоченные записи «ключ — значение» и его потолок."""

    scope: str
    items: dict[str, MemoryItem] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"Неизвестный слой памяти: {self.scope}")

    @property
    def limit(self) -> int:
        return MAX_ITEMS[self.scope]

    def put(self, key: str, value: str, *, source: str = "agent", turn: int = 0) -> str:
        """Записать или обновить. Возвращает 'added' либо 'changed', либо ''."""
        key = key.strip()
        value = " ".join(str(value).split())[:MAX_VALUE_CHARS]
        if not key or not value:
            return ""
        before = self.items.get(key)
        if before is not None and before.value == value:
            return ""
        self.items[key] = MemoryItem(key, value, source, turn)
        # Потолок: вытесняем самую старую запись, а не самую длинную —
        # так предсказуемее, и на видео видно, что именно ушло.
        while len(self.items) > self.limit:
            self.items.pop(next(iter(self.items)))
        return "changed" if before is not None else "added"

    def forget(self, key: str) -> bool:
        return self.items.pop(key.strip(), None) is not None

    def clear(self) -> None:
        self.items.clear()

    def render(self) -> str:
        """Слой текстом для системного промпта. Пустой слой — пустая строка."""
        if not self.items:
            return ""
        lines = [f"- {item.key}: {item.value}" for item in self.items.values()]
        return SCOPE_PREFIXES[self.scope] + "\n" + "\n".join(lines)

    def tokens(self) -> int:
        return estimate_tokens(self.render())

    def as_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "items": [item.as_dict() for item in self.items.values()]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryLayer:
        scope = str(data.get("scope", ""))
        layer = cls(scope)
        for raw in data.get("items", []):
            if not isinstance(raw, dict):
                continue
            try:
                item = MemoryItem.from_dict(raw)
            except (ValueError, TypeError):
                continue
            layer.items[item.key] = item
        return layer


@dataclass(frozen=True)
class MemoryUpdate:
    """Что нарезатель сделал на этом ходу — и во что это обошлось."""

    after_turn: int
    mode: str
    added: dict[str, list[str]]
    changed: dict[str, list[str]]
    forgotten: list[str]
    input_tokens: int | None = None
    output_tokens: int | None = None
    memory_tokens: int = 0

    @property
    def empty(self) -> bool:
        return not any(self.added.values()) and not any(self.changed.values()) and not self.forgotten

    def as_dict(self) -> dict[str, Any]:
        return {
            "after_turn": self.after_turn,
            "mode": self.mode,
            "added": self.added,
            "changed": self.changed,
            "forgotten": self.forgotten,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "memory_tokens": self.memory_tokens,
        }


class LayeredMemory:
    """Три слоя под одной крышей: рендер в промпт и применение раскладки."""

    def __init__(self, layers: dict[str, MemoryLayer] | None = None) -> None:
        self._layers = {scope: MemoryLayer(scope) for scope in SCOPES}
        if layers:
            for scope, layer in layers.items():
                if scope in self._layers:
                    self._layers[scope] = layer

    def __getitem__(self, scope: str) -> MemoryLayer:
        if scope not in self._layers:
            raise KeyError(f"Неизвестный слой памяти: {scope}")
        return self._layers[scope]

    @property
    def layers(self) -> dict[str, MemoryLayer]:
        return dict(self._layers)

    def render(self, enabled: Iterable[str] | None = None) -> str:
        """
        Текст всех включённых слоёв для системного промпта.

        enabled — какие слои подмешивать. Выключение слоя не стирает его:
        это ручка «а что агент ответит без рабочей памяти», ради которой
        задание и просит проверить влияние на ответы.
        """
        allowed = set(SCOPES if enabled is None else enabled)
        blocks = [self._layers[scope].render() for scope in SCOPES if scope in allowed]
        blocks = [block for block in blocks if block]
        if not blocks:
            return ""
        return MEMORY_HEADER + "\n\n" + "\n\n".join(blocks)

    def tokens(self, enabled: Iterable[str] | None = None) -> int:
        return estimate_tokens(self.render(enabled))

    def apply(self, routed: dict[str, Any], *, mode: str, turn: int, reply: AgentReply | None = None) -> MemoryUpdate:
        """Разложить ответ нарезателя по слоям и вернуть отчёт об изменениях."""
        added: dict[str, list[str]] = {scope: [] for scope in SCOPES}
        changed: dict[str, list[str]] = {scope: [] for scope in SCOPES}
        forgotten: list[str] = []

        for scope in SCOPES:
            bucket = routed.get(scope)
            if not isinstance(bucket, dict):
                continue
            for key, value in bucket.items():
                result = self._layers[scope].put(str(key), str(value), source=mode, turn=turn)
                if result == "added":
                    added[scope].append(str(key).strip())
                elif result == "changed":
                    changed[scope].append(str(key).strip())

        for key in routed.get("forget", []) or []:
            for scope in SCOPES:
                if self._layers[scope].forget(str(key)):
                    forgotten.append(str(key).strip())

        return MemoryUpdate(
            after_turn=turn,
            mode=mode,
            added=added,
            changed=changed,
            forgotten=forgotten,
            input_tokens=reply.input_tokens if reply else None,
            output_tokens=reply.output_tokens if reply else None,
            memory_tokens=self.tokens(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {scope: self._layers[scope].as_dict() for scope in SCOPES}

    def state(self, enabled: Iterable[str] | None = None) -> dict[str, Any]:
        """Слепок для страницы: что лежит в каждом слое и сколько это весит."""
        allowed = set(SCOPES if enabled is None else enabled)
        return {
            "layers": [
                {
                    "scope": scope,
                    "title": SCOPE_TITLES[scope],
                    "life": SCOPE_LIVES[scope],
                    "enabled": scope in allowed,
                    "limit": MAX_ITEMS[scope],
                    "tokens": self._layers[scope].tokens(),
                    "items": [item.as_dict() for item in self._layers[scope].items.values()],
                }
                for scope in SCOPES
            ],
            "tokens": self.tokens(allowed),
        }


# ── Раскладка: нарезатель, эвристика, руки ──────────────────────────────────


def route_by_agent(
    transport: Transport,
    model: str,
    memory: LayeredMemory,
    question: str,
    answer: str,
) -> tuple[dict[str, Any], AgentReply]:
    """
    Отдельный запрос к модели: что из реплики в какой слой.

    Побочный запрос на каждом ходу — значит память стоит токенов, ровно
    как блок фактов в дне 10. Это и есть цена «агент помнит».
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ROUTER_INSTRUCTION},
            {
                "role": "user",
                "content": (
                    "Уже в памяти:\n"
                    + (_known(memory) or "(пусто)")
                    + "\n\nРеплика пользователя:\n"
                    + question
                    + "\n\nОтвет агента:\n"
                    + answer
                ),
            },
        ],
        "temperature": ROUTER_TEMPERATURE,
        "max_tokens": 400,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    raw = transport.send(payload)
    reply = _reply_from(raw)
    return _parse_routed(reply.content), reply


def route_by_heuristic(question: str, turn: int) -> dict[str, Any]:
    """
    Раскладка без модели: по словам-маркерам в реплике.

    Нарочно грубая. Она бесплатна и мгновенна, но кладёт целую реплику
    одним куском и легко ошибается слоем — это видно на странице рядом с
    нарезателем и объясняет, за что мы платим токенами.
    """
    text = question.strip()
    if not text:
        return {"chat": {}, "task": {}, "user": {}, "forget": []}
    if _USER_MARKERS.search(text):
        scope = "user"
    elif _TASK_MARKERS.search(text):
        scope = "task"
    else:
        scope = "chat"
    routed = {"chat": {}, "task": {}, "user": {}, "forget": []}
    routed[scope] = {f"msg{turn}": text}
    return routed


def _known(memory: LayeredMemory) -> str:
    lines = []
    for scope in SCOPES:
        for item in memory[scope].items.values():
            lines.append(f"{scope}.{item.key}: {item.value}")
    return "\n".join(lines)


def _reply_from(response: dict[str, Any]) -> AgentReply:
    try:
        choice = response["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise TransportError(f"Нарезатель вернул неожиданный ответ: {error}") from error
    usage = response.get("usage") or {}
    return AgentReply(
        content=content or "",
        finish_reason=choice.get("finish_reason"),
        model=response.get("model"),
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )


def _parse_routed(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise TransportError(f"Нарезатель вернул не JSON: {error}") from error
    if not isinstance(data, dict):
        raise TransportError("Нарезатель вернул не объект")

    routed: dict[str, Any] = {}
    for scope in SCOPES:
        bucket = data.get(scope)
        routed[scope] = {
            str(key).strip(): str(value).strip()
            for key, value in bucket.items()
            if isinstance(bucket, dict) and str(key).strip() and str(value).strip()
        } if isinstance(bucket, dict) else {}
    forget = data.get("forget")
    routed["forget"] = [str(key).strip() for key in forget if str(key).strip()] if isinstance(forget, list) else []
    return routed


# ── Хранилище: три слоя — три файла ─────────────────────────────────────────

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class MemoryStore:
    """
    Слой на файл: <сессия>-mem-chat.json, -task.json, -user.json.

    Три файла, а не три ключа в одном, — потому что у слоёв разные сроки
    жизни: «Новый диалог» удаляет один файл, «Новая задача» другой, а
    третий переживает оба. На диске это видно так же, как на странице.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def load(self, session_id: str) -> LayeredMemory:
        layers = {}
        for scope in SCOPES:
            path = self._path(session_id, scope)
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # Битый файл слоя не должен ронять страницу: слой просто
                # окажется пустым, остальные два уцелеют.
                continue
            if isinstance(data, dict):
                layers[scope] = MemoryLayer.from_dict({"scope": scope, "items": data.get("items", [])})
        return LayeredMemory(layers)

    def save(self, session_id: str, memory: LayeredMemory) -> None:
        for scope in SCOPES:
            self._write(session_id, scope, memory[scope])

    def save_scope(self, session_id: str, memory: LayeredMemory, scope: str) -> None:
        self._write(session_id, scope, memory[scope])

    def clear(self, session_id: str, scope: str) -> bool:
        path = self._path(session_id, scope)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            raise MemoryError_(f"Не удалось очистить слой {scope}: {error}") from error

    def files(self, session_id: str) -> list[dict[str, Any]]:
        """Что лежит на диске по слоям — для дебаг-панели."""
        out = []
        for scope in SCOPES:
            path = self._path(session_id, scope)
            exists = path.exists()
            out.append(
                {
                    "scope": scope,
                    "file": path.name,
                    "exists": exists,
                    "bytes": path.stat().st_size if exists else 0,
                }
            )
        return out

    def load_settings(self, session_id: str) -> dict[str, Any]:
        """Режим раскладки, включённые слои и история раскладок — рядом со слоями."""
        path = self._settings_path(session_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_settings(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._settings_path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError as error:
            raise MemoryError_(f"Не удалось сохранить настройки памяти: {error}") from error

    def _settings_path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise MemoryError_("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}-mem-settings.json"

    def _write(self, session_id: str, scope: str, layer: MemoryLayer) -> None:
        path = self._path(session_id, scope)
        self._directory.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": session_id,
            "scope": scope,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "items": [item.as_dict() for item in layer.items.values()],
        }
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError as error:
            raise MemoryError_(f"Не удалось сохранить слой {scope}: {error}") from error

    def _path(self, session_id: str, scope: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise MemoryError_("Недопустимый идентификатор сессии")
        if scope not in SCOPES:
            raise MemoryError_(f"Неизвестный слой памяти: {scope}")
        return self._directory / f"{session_id}-mem-{scope}.json"
