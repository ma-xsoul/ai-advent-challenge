"""
Профиль пользователя — день 12.

Профиль — не память. Память (день 11) — то, что накопилось в разговоре:
имя, бюджет, решения. Профиль — то, как пользователь настроил агента под
себя: как к нему обращаться, каким тоном и в каком формате отвечать, чего
не делать и какими шагами решать задачу. Память накапливается, профиль
выбирают. Куратор: «профиль — не часть памяти, а часть конфига
оркестрации».

Четыре части: стиль, формат, ограничения — это «карточка вкусов»; а
пайплайн — порядок шагов — делает профиль конфигом оркестрации. Сегодня
он меняет, как агент отвечает; в дне 13 он будет решать, какие стадии
проходит задача.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tokens import estimate_tokens


MAX_TEXT = 200
MAX_ITEMS = 8
MAX_CUSTOM = 12

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class ProfileError(ValueError):
    """Профиль не собрался: пустое поле, дубликат id или битый файл."""


@dataclass(frozen=True)
class Profile:
    id: str
    title: str
    # Как обращаться: на ты / на вы, по имени или без.
    address: str
    # Каким тоном: коротко / развёрнуто / критично.
    tone: str
    # В каком виде: списком, абзацем, кодом без прозы.
    format: str
    # Чего не делать.
    limits: tuple[str, ...] = field(default_factory=tuple)
    # Какими шагами решать задачу. Это и есть «пайплайн из скиллов» —
    # уровень выше вкусов: не «как звучать», а «как работать».
    pipeline: tuple[str, ...] = field(default_factory=tuple)
    # Встроенный профиль нельзя удалить — только скопировать и поправить.
    preset: bool = False

    def __post_init__(self) -> None:
        if not _SAFE_ID.match(self.id):
            raise ProfileError("id профиля — латиница, цифры, дефис; до 40 символов")
        for name in ("title", "address", "tone", "format"):
            if not str(getattr(self, name)).strip():
                raise ProfileError(f"У профиля пустое поле: {name}")

    def render(self) -> str:
        """Блок для системного промпта. Правила — как инструкция, а не как факты."""
        lines = [
            f"Профиль пользователя «{self.title}». Соблюдай его в каждом ответе:",
            f"- Обращение: {self.address}",
            f"- Тон: {self.tone}",
            f"- Формат ответа: {self.format}",
        ]
        if self.limits:
            lines.append("- Нельзя: " + "; ".join(self.limits) + ".")
        if self.pipeline:
            steps = " ".join(f"{i}) {step};" for i, step in enumerate(self.pipeline, 1))
            lines.append("- Порядок работы над задачей: " + steps.rstrip(";") + ".")
        # Профиль можно сменить посреди диалога, и тогда в истории лежат
        # ответы в старом стиле — модель тянется к ним сильнее, чем к
        # инструкции. Говорим прямо, что важнее.
        lines.append(
            "Профиль важнее стиля предыдущих ответов в этом разговоре: "
            "если раньше ты отвечал иначе, с этого ответа переключись."
        )
        return "\n".join(lines)

    def tokens(self) -> int:
        return estimate_tokens(self.render())

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "address": self.address,
            "tone": self.tone,
            "format": self.format,
            "limits": list(self.limits),
            "pipeline": list(self.pipeline),
            "preset": self.preset,
            "tokens": self.tokens(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, preset: bool = False) -> Profile:
        def text(name: str) -> str:
            value = data.get(name, "")
            return " ".join(str(value).split())[:MAX_TEXT] if isinstance(value, (str, int, float)) else ""

        def items(name: str) -> tuple[str, ...]:
            value = data.get(name, [])
            if isinstance(value, str):
                value = [part for part in re.split(r"[\n;]", value)]
            if not isinstance(value, list):
                return ()
            cleaned = [" ".join(str(item).split())[:MAX_TEXT] for item in value]
            return tuple(item for item in cleaned if item)[:MAX_ITEMS]

        return cls(
            id=text("id").lower(),
            title=text("title"),
            address=text("address"),
            tone=text("tone"),
            format=text("format"),
            limits=items("limits"),
            pipeline=items("pipeline"),
            preset=preset,
        )


# Встроенные профили — три разных пользователя одного и того же агента.
# Разница между ними и есть демонстрация дня: один вопрос, три ответа.
PRESETS: tuple[Profile, ...] = (
    Profile(
        id="maksim",
        title="Максим · коротко",
        address="на ты, по имени — Максим",
        tone="коротко и по делу, без вступлений и без итогов в конце",
        format="одно-два предложения или список до пяти пунктов; код — блоком, без пояснений вокруг",
        limits=("предлагать платные сервисы и подписки", "выходить за Python и Flask, если не просили"),
        pipeline=("сразу ответ, без плана и без уточняющих вопросов",),
        preset=True,
    ),
    Profile(
        id="customer",
        title="Заказчик · на вы",
        address="на вы, без имени",
        tone="вежливо и развёрнуто, объясняя, зачем это нужно",
        format="абзацами, без кода и без технических аббревиатур; если термин неизбежен — расшифруй",
        limits=("показывать код", "использовать жаргон без пояснения"),
        pipeline=(
            "переформулируй задачу своими словами",
            "предложи два варианта решения с плюсами и минусами",
            "дай одну рекомендацию",
        ),
        preset=True,
    ),
    Profile(
        id="reviewer",
        title="Ревьюер · строго",
        address="на вы, обезличенно",
        tone="критично и сухо, по пунктам, без похвалы",
        format="нумерованный список «проблема → риск → что сделать»",
        limits=("соглашаться без аргумента", "хвалить", "предлагать решение, не назвав проблему"),
        pipeline=("найди слабые места", "оцени риск каждого", "предложи исправление"),
        preset=True,
    ),
)

PRESET_IDS = tuple(profile.id for profile in PRESETS)


def preset(profile_id: str) -> Profile | None:
    return next((profile for profile in PRESETS if profile.id == profile_id), None)


# ── Хранилище: свои профили пользователя и привязка к дорожкам ────────────

_SAFE_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ProfileStore:
    """
    <сессия>-profiles.json: свои профили и какой профиль у какой дорожки.

    Отдельный файл от памяти и от диалога — смена профиля не трогает ни
    стек, ни слои. Встроенные профили в файл не пишутся: они в коде.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def load(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.exists():
            return {"custom": [], "lanes": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"custom": [], "lanes": {}}
        if not isinstance(data, dict):
            return {"custom": [], "lanes": {}}
        custom = []
        for raw in data.get("custom", []) or []:
            if not isinstance(raw, dict):
                continue
            try:
                custom.append(Profile.from_dict(raw).as_dict())
            except ProfileError:
                continue
        lanes = data.get("lanes")
        return {"custom": custom, "lanes": dict(lanes) if isinstance(lanes, dict) else {}}

    def save(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "custom": [
                {key: value for key, value in item.items() if key not in ("preset", "tokens")}
                for item in data.get("custom", [])
            ],
            "lanes": data.get("lanes", {}),
        }
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError as error:
            raise ProfileError(f"Не удалось сохранить профили: {error}") from error

    def delete(self, session_id: str) -> bool:
        try:
            self._path(session_id).unlink()
            return True
        except FileNotFoundError:
            return False

    def _path(self, session_id: str) -> Path:
        if not _SAFE_SESSION.match(session_id):
            raise ProfileError("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}-profiles.json"


def all_profiles(data: dict[str, Any]) -> list[Profile]:
    """Встроенные плюс свои, в этом порядке."""
    custom = []
    for raw in data.get("custom", []):
        try:
            custom.append(Profile.from_dict(raw))
        except ProfileError:
            continue
    return list(PRESETS) + custom


def find_profile(data: dict[str, Any], profile_id: str) -> Profile | None:
    return next((profile for profile in all_profiles(data) if profile.id == profile_id), None)


# ── Проверка: что агент учёл сам ──────────────────────────────────────────

_YOU_INFORMAL = re.compile(r"\b(ты|тебе|тебя|тобой|твой|твоя|твои|твоё|твоего|твоей|твоим|твоих)\b", re.IGNORECASE)
_YOU_FORMAL = re.compile(r"\b(вы|вам|вас|вами|ваш|ваша|ваши|ваше|вашего|вашей|вашим|ваших)\b", re.IGNORECASE)
# Повелительное наклонение — тоже обращение: «используй» — ты, «используйте» —
# вы. В технических ответах его больше, чем местоимений. Без словаря
# приходится отсекать существительные с теми же окончаниями руками.
_IMPERATIVE_INFORMAL = re.compile(r"\b[а-яё]{2,}(?:ай|уй|яй)\b", re.IGNORECASE)
_IMPERATIVE_FORMAL = re.compile(r"\b[а-яё]{2,}(?:йте|ьте)\b", re.IGNORECASE)
_NOT_A_VERB = {
    "случай", "обычай", "урожай", "край", "сарай", "трамвай", "попугай", "рай",
    "чай", "май", "буй", "дубай", "негодяй", "бабай", "лишай", "каравай", "сайте",
}
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.MULTILINE)


def _imperatives(pattern: re.Pattern[str], text: str) -> int:
    return sum(
        1
        for word in pattern.findall(text)
        if word.lower() not in _NOT_A_VERB and not word.lower().endswith("байте")
    )


def expected_address(profile: Profile | None) -> str | None:
    """Что профиль просит: «на ты» → ты, «на вы» → вы, иначе не знаем."""
    if profile is None:
        return None
    text = profile.address.lower()
    if re.search(r"\bна\s+ты\b", text):
        return "ты"
    if re.search(r"\bна\s+вы\b", text):
        return "вы"
    return None


def check_answer(text: str, profile: Profile | None = None) -> dict[str, Any]:
    """
    Дешёвые признаки в ответе: обращение, список, код, имя, длина.

    Не «правильно ли», а «что видно»: два ответа на один вопрос под
    разными профилями кладутся рядом, и по этим признакам разница
    считывается без чтения текста. Второй запрос к модели не нужен.
    """
    informal = len(_YOU_INFORMAL.findall(text)) + _imperatives(_IMPERATIVE_INFORMAL, text)
    formal = len(_YOU_FORMAL.findall(text)) + _imperatives(_IMPERATIVE_FORMAL, text)
    if informal and not formal:
        address = "ты"
    elif formal and not informal:
        address = "вы"
    elif informal and formal:
        address = "смешано"
    else:
        address = "нет"
    expected = expected_address(profile)
    return {
        "address": address,
        "expected_address": expected,
        # None — сравнивать не с чем: профиль не задал или ответ без обращения.
        "address_ok": (address == expected) if expected and address in ("ты", "вы") else None,
        "list_items": len(_LIST_ITEM.findall(text)),
        "has_code": "```" in text,
        "named": bool(profile) and _mentions_name(text, profile),
        "tokens": estimate_tokens(text),
        "paragraphs": len([part for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]) if text.strip() else 0,
    }


def _mentions_name(text: str, profile: Profile) -> bool:
    # Имя берём из обращения: «по имени — Максим» → Максим.
    match = re.search(r"—\s*([А-ЯЁA-Z][\w-]+)", profile.address)
    return bool(match) and match.group(1).lower() in text.lower()
