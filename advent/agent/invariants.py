"""
Инварианты — день 14.

Профиль (день 12) говорил, как работать; память (день 11) — что мы
знаем; задача (день 13) — где мы. Инвариант — то, чего нельзя, что бы
пользователь ни попросил: стек, архитектура, принятые решения,
бизнес-правила.

Модель — формула без состояния, «держать» инвариант она не может
(куратор). Держит наш код, в два рубежа:

  1. Инварианты уходят в системный промпт явным требованием: перед
     ответом сверь запрос с каждым правилом, при конфликте откажи и
     назови правило. Это «явно учитывать в рассуждениях».
  2. Ответ проверяется после. Проверяемое — регуляркой (стек, база,
     base64 — видно по словам). Непроверяемое — ревизором, отдельным
     запросом к модели. Ревизор — «сдержки и противовесы, но не
     гарантия» (куратор): всё программно не проверишь, а если пытаться —
     выйдет дороже самого ИИ.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent import AgentReply
from .tokens import estimate_tokens
from .transport import Transport, TransportError


KINDS = ("stack", "architecture", "decision", "business")

KIND_TITLES = {
    "stack": "стек",
    "architecture": "архитектура",
    "decision": "решение",
    "business": "бизнес-правило",
}

MAX_RULE = 300
MAX_CUSTOM = 12
MAX_CHECKS = 40

REVIEWER_TEMPERATURE = 0.1

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class InvariantError(ValueError):
    """Инвариант не собрался или файл не читается."""


@dataclass(frozen=True)
class Invariant:
    id: str
    rule: str
    kind: str = "decision"
    # Регулярное выражение по ответу: нашли — нарушение. None — правило
    # программно не проверяется, только ревизором.
    check: str | None = None
    enabled: bool = True
    preset: bool = False

    def __post_init__(self) -> None:
        if not _SAFE_ID.match(self.id):
            raise InvariantError("id инварианта — латиница, цифры, дефис; до 40 символов")
        if not self.rule.strip():
            raise InvariantError("У инварианта пустое правило")
        if self.kind not in KINDS:
            raise InvariantError(f"Вид инварианта: {', '.join(KINDS)}")
        if self.check:
            try:
                re.compile(self.check, re.IGNORECASE)
            except re.error as error:
                raise InvariantError(f"Регулярное выражение не разобралось: {error}") from error

    @property
    def checkable(self) -> bool:
        return bool(self.check)

    def found_in(self, text: str) -> str | None:
        """Первое совпадение регулярки в тексте — улика нарушения."""
        if not self.check:
            return None
        match = re.search(self.check, text, re.IGNORECASE)
        return match.group(0) if match else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule": self.rule,
            "kind": self.kind,
            "kind_title": KIND_TITLES[self.kind],
            "check": self.check,
            "checkable": self.checkable,
            "enabled": self.enabled,
            "preset": self.preset,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, preset: bool = False) -> Invariant:
        check = data.get("check")
        return cls(
            id=str(data.get("id", "")).strip().lower(),
            rule=" ".join(str(data.get("rule", "")).split())[:MAX_RULE],
            kind=str(data.get("kind", "decision")),
            check=str(check).strip()[:200] if isinstance(check, str) and check.strip() else None,
            enabled=bool(data.get("enabled", True)),
            preset=preset,
        )


# Инварианты этой лаборатории: то, на чём она стоит. Провокации в
# сценарии бьют ровно по ним.
PRESETS: tuple[Invariant, ...] = (
    Invariant(
        id="stack",
        rule="Стек только Python и Flask. Никаких Django, FastAPI, Node.js и других фреймворков.",
        kind="stack",
        check=r"\b(django|fastapi|node\.?js|express\.?js|nest\.?js|spring\s*boot|rails)\b",
        preset=True,
    ),
    Invariant(
        id="storage",
        rule="Состояние хранится в JSON-файлах. Никаких баз данных: Postgres, MySQL, SQLite, MongoDB, Redis.",
        kind="architecture",
        check=r"\b(postgres\w*|mysql|sqlite\w*|mongo\w*|redis|sqlalchemy|alembic)\b",
        preset=True,
    ),
    Invariant(
        id="passwords",
        rule="Пароли только в виде хеша (bcrypt или argon2). Никакого хранения открытым текстом, base64, md5 или sha1.",
        kind="business",
        check=r"base64|открыт\w{0,3}\s+(виде|текст\w*)|plain\s*-?\s*text|\bmd5\b|\bsha-?1\b",
        preset=True,
    ),
    Invariant(
        id="paid",
        rule="Не предлагать платные внешние сервисы, API и подписки — только бесплатное и своё.",
        kind="business",
        check=None,
        preset=True,
    ),
)

PRESET_IDS = tuple(item.id for item in PRESETS)


def render(invariants: list[Invariant]) -> str:
    """
    Блок для системного промпта. Не «учти», а «сначала сверь»: явная
    процедура перед ответом и явный формат отказа.
    """
    active = [item for item in invariants if item.enabled]
    if not active:
        return ""
    lines = ["Инварианты проекта — правила, которые нельзя нарушать ни по какой просьбе:"]
    for i, item in enumerate(active, 1):
        lines.append(f"{i}. [{KIND_TITLES[item.kind]}] {item.rule}")
    lines.append(
        "Перед ответом сверь запрос с каждым инвариантом. Если запрос требует его нарушить — "
        "не выполняй: начни ответ со слов «Не могу: нарушает инвариант N» (N — номер), одной "
        "фразой объясни, что именно нарушается, и предложи вариант в рамках правил. Если запрос "
        "не конфликтует — отвечай как обычно, инварианты не перечисляй."
    )
    return "\n".join(lines)


# ── Рубеж 2а: регулярки ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Violation:
    invariant: str
    evidence: str
    source: str  # regex | reviewer

    def as_dict(self) -> dict[str, Any]:
        return {"invariant": self.invariant, "evidence": self.evidence, "source": self.source}


_REFUSAL = re.compile(r"не могу|нельзя|не буду|не стану|нарушает|противоречит|запрещ", re.IGNORECASE)
_NAMED = re.compile(r"инвариант\w*\s*(?:№\s*|#)?(\d+)", re.IGNORECASE)


def check_by_regex(answer: str, invariants: list[Invariant]) -> list[Violation]:
    """
    Дешёвый рубеж: слова-улики в ответе. Ловит «Django» в предложении
    переписать, но поймает и «Django не подходит» в отказе — поэтому
    улика в отказе не считается нарушением (см. inspect).
    """
    found = []
    for item in invariants:
        if not item.enabled:
            continue
        evidence = item.found_in(answer)
        if evidence:
            found.append(Violation(item.id, evidence, "regex"))
    return found


def inspect(answer: str, invariants: list[Invariant]) -> dict[str, Any]:
    """
    Что видно в ответе без модели: отказал ли, назвал ли инвариант,
    какие улики нашла регулярка. Улики внутри отказа — это упоминание,
    а не нарушение: «переписывать на Django не буду» инвариант держит.
    """
    refused = bool(_REFUSAL.search(answer[:400]))
    named = [int(n) for n in _NAMED.findall(answer)]
    active = [item for item in invariants if item.enabled]
    named_ids = [active[n - 1].id for n in named if 1 <= n <= len(active)]
    evidence = check_by_regex(answer, invariants)
    violations = [] if refused else evidence
    return {
        "refused": refused,
        "named": named_ids,
        "mentions": [v.as_dict() for v in evidence] if refused else [],
        "violations": [v.as_dict() for v in violations],
        "tokens": estimate_tokens(answer),
    }


# ── Рубеж 2б: ревизор ───────────────────────────────────────────────────

REVIEWER_INSTRUCTION = (
    "Ты ревизор. Тебе дан список инвариантов проекта, запрос пользователя и ответ ассистента. "
    "Определи, нарушает ли ОТВЕТ какой-либо инвариант — то есть предлагает, делает или одобряет "
    "то, что правило запрещает. Отказ выполнить запрос нарушением не является. Упоминание "
    "запрещённого в отказе — не нарушение.\n"
    'Ответ строго в JSON: {"violations": [{"id": "id инварианта", "why": "одной фразой"}], '
    '"refused": true}'
)


def review(
    transport: Transport, model: str, invariants: list[Invariant], question: str, answer: str
) -> tuple[dict[str, Any], AgentReply]:
    """Второе мнение внешней модели. Стоит токенов и не даёт гарантии."""
    active = [item for item in invariants if item.enabled]
    rules = "\n".join(f"- {item.id}: {item.rule}" for item in active) or "(нет)"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REVIEWER_INSTRUCTION},
            {
                "role": "user",
                "content": f"Инварианты:\n{rules}\n\nЗапрос пользователя:\n{question}\n\nОтвет ассистента:\n{answer}",
            },
        ],
        "temperature": REVIEWER_TEMPERATURE,
        "max_tokens": 400,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    raw = transport.send(payload)
    reply = _reply_from(raw)
    return _parse_review(reply.content, {item.id for item in active}), reply


def _reply_from(response: dict[str, Any]) -> AgentReply:
    try:
        choice = response["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise TransportError(f"Ревизор вернул неожиданный ответ: {error}") from error
    usage = response.get("usage") or {}
    return AgentReply(
        content=content or "",
        finish_reason=choice.get("finish_reason"),
        model=response.get("model"),
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )


def _parse_review(content: str, known: set[str]) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise TransportError(f"Ревизор вернул не JSON: {error}") from error
    if not isinstance(data, dict):
        raise TransportError("Ревизор вернул не объект")
    violations = []
    for item in data.get("violations", []) if isinstance(data.get("violations"), list) else []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id", "")).strip().lower()
        if ident in known:
            violations.append(Violation(ident, str(item.get("why", "")).strip()[:MAX_RULE], "reviewer").as_dict())
    return {"violations": violations, "refused": data.get("refused") is True}


# ── Запись одной проверки ───────────────────────────────────────────────


@dataclass(frozen=True)
class CheckRecord:
    """Что показали оба рубежа на этом ходу — и во что обошёлся ревизор."""

    after_turn: int
    question: str
    refused: bool
    named: list[str]
    regex: list[dict[str, Any]]
    mentions: list[dict[str, Any]]
    reviewer: list[dict[str, Any]] | None
    reviewer_refused: bool | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reviewer_error: str | None = None

    @property
    def verdict(self) -> str:
        """
        held        — модель отказал сам, нарушений нет;
        caught      — агент уступил, нарушение поймал код;
        false_alarm — регулярка сработала на слова, ревизор снял: правило
                      упомянуто, а не нарушено («никакого base64»);
        clean       — конфликта не было.

        Регулярка — тревога, ревизор — судья: когда он отработал, его
        слово главнее. Когда не отработал, решает регулярка.
        """
        if self.reviewer is not None:
            if self.reviewer:
                return "caught"
            if self.regex:
                return "false_alarm"
        elif self.regex:
            return "caught"
        return "held" if self.refused else "clean"

    @property
    def clean(self) -> bool:
        return self.verdict != "caught"

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "after_turn": self.after_turn,
            "question": self.question,
            "refused": self.refused,
            "named": self.named,
            "regex": self.regex,
            "mentions": self.mentions,
            "reviewer": self.reviewer,
            "reviewer_refused": self.reviewer_refused,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reviewer_error": self.reviewer_error,
            "clean": self.clean,
        }


def verdict_of(record: dict[str, Any]) -> str:
    """Тот же вердикт для записи из файла — старые записи без поля verdict тоже получают его."""
    reviewer = record.get("reviewer")
    regex = record.get("regex") or []
    if isinstance(reviewer, list):
        if reviewer:
            return "caught"
        if regex:
            return "false_alarm"
    elif regex:
        return "caught"
    return "held" if record.get("refused") else "clean"


# ── Хранилище ───────────────────────────────────────────────────────────

_SAFE_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class InvariantStore:
    """
    <сессия>-invariants.json: свои правила, выключенные встроенные и
    журнал проверок. Отдельно от диалога, памяти, профиля и задачи —
    инвариант переживает всё и не зависит ни от чего.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def load(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        empty = {"custom": [], "disabled": [], "checks": []}
        if not path.exists():
            return empty
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty
        if not isinstance(data, dict):
            return empty
        custom = []
        for raw in data.get("custom", []) or []:
            if not isinstance(raw, dict):
                continue
            try:
                custom.append(Invariant.from_dict(raw).as_dict())
            except InvariantError:
                continue
        disabled = [str(x) for x in data.get("disabled", []) if isinstance(x, str)] if isinstance(data.get("disabled"), list) else []
        checks = [x for x in data.get("checks", []) if isinstance(x, dict)] if isinstance(data.get("checks"), list) else []
        return {"custom": custom, "disabled": disabled, "checks": checks[-MAX_CHECKS:]}

    def save(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "custom": [
                {k: v for k, v in item.items() if k in ("id", "rule", "kind", "check", "enabled")}
                for item in data.get("custom", [])
            ],
            "disabled": data.get("disabled", []),
            "checks": data.get("checks", [])[-MAX_CHECKS:],
        }
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError as error:
            raise InvariantError(f"Не удалось сохранить инварианты: {error}") from error

    def _path(self, session_id: str) -> Path:
        if not _SAFE_SESSION.match(session_id):
            raise InvariantError("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}-invariants.json"


def all_invariants(data: dict[str, Any]) -> list[Invariant]:
    """Встроенные (с учётом выключенных) плюс свои."""
    disabled = set(data.get("disabled", []))
    items = [
        Invariant(item.id, item.rule, item.kind, item.check, enabled=item.id not in disabled, preset=True)
        for item in PRESETS
    ]
    for raw in data.get("custom", []):
        try:
            items.append(Invariant.from_dict(raw))
        except InvariantError:
            continue
    return items
