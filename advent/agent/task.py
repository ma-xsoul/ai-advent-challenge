"""
Состояние задачи как конечный автомат — день 13.

До сих пор «задача» жила в голове пользователя и в стеке сообщений.
Модель — функция без состояния: она не знает, на каком мы этапе, пока
ей не скажут. Поэтому состояние держит наш код: объект задачи с этапом,
текущим шагом и ожидаемым действием. Он рождается с первой репликой,
переживает сброс диалога и уходит в системный промпт блоком — так агент
продолжает с того же места, не переспрашивая, о чём была задача.

Кто двигает: модель предлагает (план готов, шаг сделан, проверка
прошла), переход делает код. Наблюдатель — отдельный JSON-запрос после
каждого хода, как нарезатель в дне 11: он не решает, куда идти, он
докладывает, что произошло.

    planning ──approve──▶ execution ──last step──▶ validation ──ok──▶ done
                              ▲                         │
                              └────────── fail ─────────┘
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


STAGES = ("planning", "execution", "validation", "done")

STAGE_TITLES = {
    "planning": "Планирование",
    "execution": "Выполнение",
    "validation": "Проверка",
    "done": "Готово",
}

# Ожидаемое действие по умолчанию — чего ждём, чтобы двинуться дальше.
STAGE_EXPECTS = {
    "planning": "план шагов от агента, потом утверждение плана",
    "execution": "сделать текущий шаг и сообщить результат",
    "validation": "проверить результат против плана: принять или вернуть на шаг",
    "done": "ничего — задача закрыта",
}

MAX_STEPS = 12
MAX_TEXT = 300
MAX_LOG = 60

OBSERVER_TEMPERATURE = 0.1

# Наблюдатель докладывает, а не командует. Для каждого этапа — свой вопрос
# и своя форма ответа; куда двигать задачу, решает код по этому докладу.
OBSERVER_INSTRUCTIONS = {
    "planning": (
        "Ты следишь за задачей на этапе планирования. По реплике пользователя и ответу агента "
        "выпиши план: короткие шаги, каждый — одно действие с проверяемым результатом. "
        "Если агент ещё уточняет требования и плана нет — верни пустой список.\n"
        'Ответ строго в JSON: {"plan": ["шаг 1", "шаг 2"], "plan_ready": true}'
    ),
    "execution": (
        "Ты следишь за задачей на этапе выполнения. Тебе дан текущий шаг плана и последний обмен "
        "репликами. Реши, сделан ли текущий шаг в ответе агента: сделан — если результат шага "
        "получен (код, текст, решение), а не обещан. В note — одна строка, что именно сделано.\n"
        'Ответ строго в JSON: {"step_done": true, "note": "что сделано"}'
    ),
    "validation": (
        "Ты следишь за задачей на этапе проверки. Тебе дан план, цель и последний обмен репликами. "
        "Реши по ответу агента: проверка пройдена (verdict ok) или найдены проблемы (verdict fail). "
        "Если fail — укажи номер шага плана, на который вернуться (с 1), и проблемы списком.\n"
        'Ответ строго в JSON: {"verdict": "ok", "step": null, "issues": []}'
    ),
}


class TaskError(ValueError):
    """Недопустимый переход или битое состояние."""


@dataclass
class Step:
    text: str
    done: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "done": self.done, "note": self.note}


@dataclass(frozen=True)
class Transition:
    """Одна запись журнала переходов: откуда, куда, почему и на каком ходу."""

    from_stage: str
    to_stage: str
    reason: str
    turn: int
    at: str
    step: int | None = None
    # День 15: кто просил переход и было ли отказано. Отказ тоже пишется
    # в журнал — попытка перепрыгнуть должна быть видна.
    actor: str = "user"
    refused: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_stage,
            "to": self.to_stage,
            "reason": self.reason,
            "turn": self.turn,
            "at": self.at,
            "step": self.step,
            "actor": self.actor,
            "refused": self.refused,
        }


@dataclass(frozen=True)
class ObserverRecord:
    """Что наблюдатель увидел на этом ходу — и во что это обошлось."""

    after_turn: int
    stage: str
    report: dict[str, Any]
    applied: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "after_turn": self.after_turn,
            "stage": self.stage,
            "report": self.report,
            "applied": self.applied,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Таблица переходов — день 15 ─────────────────────────────────────────
#
# Всё, чего здесь нет, — недопустимо. Guard — функция над состоянием и
# тем, кто просит: вернула None — можно, вернула строку — отказ с этой
# причиной. Кнопка, наблюдатель и модель только просят; разрешает таблица.

ACTORS = ("user", "observer", "model")


def _guard_approve(task: TaskState, actor: str) -> str | None:
    if not task.steps:
        return "плана ещё нет — нечего утверждать"
    if actor != "user":
        return "план утверждает только пользователь"
    return None


def _guard_all_done(task: TaskState, actor: str) -> str | None:
    if not task.steps:
        return "плана нет"
    open_steps = [i + 1 for i, s in enumerate(task.steps) if not s.done]
    if open_steps:
        return f"не закрыты шаги {', '.join(map(str, open_steps))}"
    return None


def _guard_accept(task: TaskState, actor: str) -> str | None:
    if actor != "user":
        return "финал подтверждает только пользователь"
    return None


def _guard_return(task: TaskState, actor: str) -> str | None:
    if not task.steps:
        return "плана нет — возвращаться некуда"
    return None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    title: str
    guard: Any

    def as_dict(self) -> dict[str, Any]:
        return {"from": self.source, "to": self.target, "title": self.title}


TRANSITIONS: tuple[Edge, ...] = (
    Edge("planning", "execution", "план утверждён", _guard_approve),
    Edge("execution", "validation", "все шаги закрыты", _guard_all_done),
    Edge("validation", "done", "проверка принята", _guard_accept),
    Edge("validation", "execution", "возврат на шаг", _guard_return),
)


def find_edge(source: str, target: str) -> Edge | None:
    return next((e for e in TRANSITIONS if e.source == source and e.target == target), None)


def allowed_targets(task: TaskState) -> list[str]:
    return [e.target for e in TRANSITIONS if e.source == task.stage]


# Что модель пишет в конце ответа, если считает, что пора менять этап.
# Сама она этап не меняет: строку разбирает код и прогоняет через таблицу.
TRANSITION_MARK = re.compile(r"\[переход:\s*(planning|execution|validation|done)\s*\]", re.IGNORECASE)


@dataclass
class TaskState:
    """
    Задача: этап, текущий шаг, ожидаемое действие.

    Переходы — только методами: каждый пишет запись в журнал. Прямой
    записи в stage нет. День 15: у каждого перехода есть строка в таблице
    и guard; методы — сахар над request(), и всё, чего в таблице нет,
    получает отказ, а не молчаливый прыжок.
    """

    id: str
    title: str
    goal: str
    stage: str = "planning"
    steps: list[Step] = field(default_factory=list)
    current: int = 0
    expected: str = STAGE_EXPECTS["planning"]
    paused: bool = False
    log: list[Transition] = field(default_factory=list)
    observer: list[ObserverRecord] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # День 15: строгий режим — правила переходов уходят в промпт, модель
    # просит переходы строкой [переход: …], попытки прыгнуть пишутся в
    # журнал. День 13 работает без этого.
    guarded: bool = False

    # ── Переходы через таблицу ──────────────────────────────────────────

    def check(self, target: str, actor: str = "user") -> str | None:
        """Можно ли перейти в target. None — можно, иначе причина отказа."""
        if target not in STAGES:
            return f"неизвестный этап: {target}"
        if self.paused:
            return "задача на паузе — сначала продолжите"
        if target == self.stage:
            return f"уже на этапе {target}"
        edge = find_edge(self.stage, target)
        if edge is None:
            path = " → ".join([self.stage] + allowed_targets(self)) if allowed_targets(self) else self.stage
            return f"переход {self.stage} → {target} не разрешён; отсюда можно только в {', '.join(allowed_targets(self)) or 'никуда'}"
        return edge.guard(self, actor)

    def request(self, target: str, *, actor: str, turn: int, note: str = "") -> Transition:
        """
        Единственные ворота: кнопка, наблюдатель и модель приходят сюда.
        Разрешено — переход делается; нет — в журнал ложится отказ с
        причиной, состояние не меняется.
        """
        reason = self.check(target, actor)
        if reason is not None:
            record = Transition(self.stage, target, reason, turn, _now(), self._step_number(), actor, refused=True)
            self.log = (self.log + [record])[-MAX_LOG:]
            self._touch()
            return record
        edge = find_edge(self.stage, target)
        why = edge.title + (f": {note}" if note else "")
        if target == "execution" and self.stage == "planning":
            self._move("execution", why, turn, step=1, actor=actor)
            self.current = 0
            self.expected = self._step_expectation()
        elif target == "validation":
            self._move("validation", why, turn, actor=actor)
            self.expected = STAGE_EXPECTS["validation"]
        elif target == "done":
            self._move("done", why, turn, actor=actor)
            self.expected = STAGE_EXPECTS["done"]
        elif target == "execution" and self.stage == "validation":
            index = len(self.steps) - 1
            for item in self.steps[index:]:
                item.done = False
            self.current = index
            self._move("execution", why, turn, step=index + 1, actor=actor)
            self.expected = self._step_expectation()
        return self.log[-1]

    @property
    def refusals(self) -> list[Transition]:
        return [item for item in self.log if item.refused]

    # ── Переходы ────────────────────────────────────────────────────────

    def set_plan(self, steps: list[str]) -> bool:
        """План на этапе planning. Возвращает True, если план изменился."""
        if self.stage != "planning":
            raise TaskError("План меняется только на этапе планирования")
        cleaned = [" ".join(s.split())[:MAX_TEXT] for s in steps if str(s).strip()][:MAX_STEPS]
        if [s.text for s in self.steps] == cleaned:
            return False
        self.steps = [Step(text) for text in cleaned]
        self.current = 0
        self.expected = "утвердить план" if cleaned else STAGE_EXPECTS["planning"]
        self._touch()
        return True

    def approve_plan(self, *, turn: int, reason: str = "план утверждён") -> None:
        if self.stage != "planning":
            raise TaskError("Утвердить план можно только на этапе планирования")
        if not self.steps:
            raise TaskError("Плана ещё нет — нечего утверждать")
        self._move("execution", reason, turn, step=1)
        self.current = 0
        self.expected = self._step_expectation()

    def complete_step(self, *, turn: int, note: str = "", reason: str = "шаг сделан") -> None:
        if self.stage != "execution":
            raise TaskError("Шаги закрываются только на этапе выполнения")
        step = self.steps[self.current]
        step.done = True
        step.note = " ".join(note.split())[:MAX_TEXT]
        if self.current + 1 < len(self.steps):
            self.current += 1
            self.expected = self._step_expectation()
            self._touch()
            return
        # Последний шаг закрыт — задача сама уходит на проверку.
        self._move("validation", f"{reason}: все {len(self.steps)} шагов закрыты", turn, actor="observer" if "сделан" in reason else "user")
        self.expected = STAGE_EXPECTS["validation"]

    def validate(self, ok: bool, *, turn: int, step: int | None = None, issues: list[str] | None = None) -> None:
        if self.stage != "validation":
            raise TaskError("Вердикт ставится только на этапе проверки")
        if ok:
            self._move("done", "проверка пройдена", turn)
            self.expected = STAGE_EXPECTS["done"]
            return
        # Возврат на шаг: этот шаг и все после него открываются заново.
        index = (step - 1) if isinstance(step, int) and 1 <= step <= len(self.steps) else len(self.steps) - 1
        for item in self.steps[index:]:
            item.done = False
        self.current = index
        why = "; ".join(issues or [])[:MAX_TEXT] or "найдены проблемы"
        self._move("execution", f"возврат на шаг {index + 1}: {why}", turn, step=index + 1)
        self.expected = self._step_expectation()

    def pause(self, *, turn: int) -> None:
        if self.stage == "done":
            raise TaskError("Готовую задачу ставить на паузу незачем")
        if self.paused:
            return
        self.paused = True
        self.log.append(Transition(self.stage, self.stage, "пауза", turn, _now(), self._step_number()))
        self._touch()

    def resume(self, *, turn: int) -> None:
        if not self.paused:
            return
        self.paused = False
        self.log.append(Transition(self.stage, self.stage, "продолжение", turn, _now(), self._step_number()))
        self._touch()

    # ── Наблюдатель ───────────────────────────────────────────────────────

    def apply_report(self, report: dict[str, Any], *, turn: int, reply: AgentReply | None = None) -> ObserverRecord:
        """Применить доклад наблюдателя — переходы делает код, не модель."""
        applied = "без изменений"
        if self.stage == "planning":
            plan = report.get("plan")
            # Пустой доклад — «в этом ответе плана нет», а не «плана нет»:
            # отказ на просьбу перепрыгнуть не должен стирать снятый план.
            if isinstance(plan, list) and plan and self.set_plan([str(s) for s in plan]):
                applied = f"план: {len(self.steps)} шагов"
        elif self.stage == "execution":
            if report.get("step_done") is True:
                number = self.current + 1
                self.complete_step(turn=turn, note=str(report.get("note", "")), reason=f"шаг {number} сделан")
                applied = f"шаг {number} закрыт" + ("" if self.stage == "execution" else " → проверка")
        elif self.stage == "validation":
            verdict = report.get("verdict")
            if verdict == "ok":
                self.validate(True, turn=turn)
                applied = "принято → done"
            elif verdict == "fail":
                step = report.get("step")
                issues = [str(i) for i in report.get("issues", []) if str(i).strip()] if isinstance(report.get("issues"), list) else []
                self.validate(False, turn=turn, step=step if isinstance(step, int) else None, issues=issues)
                applied = f"возврат на шаг {self.current + 1}"
        record = ObserverRecord(
            after_turn=turn,
            stage=self.stage,
            report=report,
            applied=applied,
            input_tokens=reply.input_tokens if reply else None,
            output_tokens=reply.output_tokens if reply else None,
        )
        self.observer = (self.observer + [record])[-MAX_LOG:]
        return record

    # ── В промпт ────────────────────────────────────────────────────────

    def render(self) -> str:
        """
        Блок для системного промпта. Здесь и живёт «продолжение без
        повторных объяснений»: агент получает этап, шаг и ожидание — и ему
        незачем переспрашивать, о чём задача.
        """
        lines = [
            f"Текущая задача: «{self.title}».",
            f"Цель: {self.goal}",
            f"Этап: {self.stage} ({STAGE_TITLES[self.stage].lower()})" + (" — НА ПАУЗЕ" if self.paused else "") + ".",
        ]
        if self.steps:
            plan = []
            for i, step in enumerate(self.steps, 1):
                mark = "[x]" if step.done else ("[>]" if self.stage == "execution" and i - 1 == self.current else "[ ]")
                plan.append(f"  {mark} {i}. {step.text}")
            lines.append("План:\n" + "\n".join(plan))
        if self.stage == "execution" and self.steps:
            lines.append(f"Текущий шаг: {self.current + 1} из {len(self.steps)} — {self.steps[self.current].text}")
        lines.append(f"Ожидаемое действие: {self.expected}.")
        rules = {
            "planning": "Твоя работа сейчас — план: уточни, если непонятно, и предложи шаги списком. Реализацию не начинай.",
            "execution": "Работай только над текущим шагом. Не пересказывай задачу и план — пользователь их видит. Сделал шаг — скажи это прямо.",
            "validation": "Проверь результат против плана и цели: перечисли, что сходится и что нет. Новый код не пиши.",
            "done": "Задача закрыта. Отвечай на вопросы по ней коротко, новую работу не начинай.",
        }
        lines.append(rules[self.stage])
        if self.guarded:
            nxt = allowed_targets(self)
            edges = "; ".join(f"{e.source} → {e.target} ({e.title})" for e in TRANSITIONS)
            lines.append(
                "Жизненный цикл задачи строгий. Разрешённые переходы: " + edges + ". "
                + (f"Отсюда можно только в: {', '.join(nxt)}. " if nxt else "Отсюда переходов нет. ")
                + "Этапы ты не переключаешь сам и не перепрыгиваешь: план утверждает пользователь, "
                "финал подтверждает пользователь после проверки. Если пользователь просит перепрыгнуть "
                "(код без утверждённого плана, закрыть задачу без проверки) — откажи: назови текущий этап "
                "и что нужно для следующего, и продолжай работу текущего этапа. Переходы, которые требуют "
                "решения пользователя (утвердить план, принять результат), не проси — жди его. Если все "
                "шаги закрыты и пора на проверку, добавь последней строкой ответа [переход: validation] — решит код."
            )
        if self.paused:
            lines.append("Задача на паузе: не продолжай её, пока пользователь не скажет продолжать.")
        return "\n".join(lines)

    def tokens(self) -> int:
        return estimate_tokens(self.render())

    # ── Сериализация ────────────────────────────────────────────────────

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "stage": self.stage,
            "stage_title": STAGE_TITLES[self.stage],
            "steps": [step.as_dict() for step in self.steps],
            "current": self.current,
            "expected": self.expected,
            "paused": self.paused,
            "log": [item.as_dict() for item in self.log],
            "observer": [item.as_dict() for item in self.observer],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tokens": self.tokens(),
            "guarded": self.guarded,
            "allowed": allowed_targets(self) if not self.paused else [],
            "refusals": sum(1 for item in self.log if item.refused),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskState:
        stage = data.get("stage")
        if stage not in STAGES:
            raise TaskError(f"Неизвестный этап: {stage}")
        steps = [
            Step(str(item.get("text", "")), bool(item.get("done")), str(item.get("note", "")))
            for item in data.get("steps", [])
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ]
        current = data.get("current", 0)
        task = cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", ""))[:MAX_TEXT],
            goal=str(data.get("goal", ""))[:2000],
            stage=stage,
            steps=steps,
            current=current if isinstance(current, int) and 0 <= current < max(len(steps), 1) else 0,
            expected=str(data.get("expected", STAGE_EXPECTS[stage]))[:MAX_TEXT],
            paused=bool(data.get("paused")),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
            guarded=bool(data.get("guarded")),
        )
        for item in data.get("log", []):
            if isinstance(item, dict) and item.get("from") in STAGES and item.get("to") in STAGES:
                task.log.append(
                    Transition(
                        item["from"], item["to"], str(item.get("reason", "")), int(item.get("turn", 0) or 0),
                        str(item.get("at", "")), item.get("step"),
                        item.get("actor") if item.get("actor") in ACTORS else "user", bool(item.get("refused")),
                    )
                )
        for item in data.get("observer", []):
            if isinstance(item, dict) and item.get("stage") in STAGES:
                task.observer.append(
                    ObserverRecord(
                        int(item.get("after_turn", 0) or 0),
                        item["stage"],
                        item.get("report") if isinstance(item.get("report"), dict) else {},
                        str(item.get("applied", "")),
                        item.get("input_tokens"),
                        item.get("output_tokens"),
                    )
                )
        if not task.id or not task.goal:
            raise TaskError("У задачи нет id или цели")
        return task

    # ── Внутреннее ──────────────────────────────────────────────────────

    def _move(self, to: str, reason: str, turn: int, step: int | None = None, actor: str = "user") -> None:
        # Даже внутренние методы дня 13 идут через таблицу: перехода нет — TaskError.
        if find_edge(self.stage, to) is None:
            raise TaskError(f"Нет перехода {self.stage} → {to}")
        self.log = (self.log + [Transition(self.stage, to, reason, turn, _now(), step, actor)])[-MAX_LOG:]
        self.stage = to
        self._touch()

    def _step_expectation(self) -> str:
        return f"сделать шаг {self.current + 1}: {self.steps[self.current].text}"

    def _step_number(self) -> int | None:
        return self.current + 1 if self.stage == "execution" and self.steps else None

    def _touch(self) -> None:
        self.updated_at = _now()


def new_task(goal: str, task_id: str, *, guarded: bool = False) -> TaskState:
    """Задача рождается с первой репликой: цель — реплика, заголовок — её начало."""
    goal = " ".join(goal.split())
    words = goal.split(" ")
    title = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
    return TaskState(id=task_id, title=title.rstrip(".,:;") or "Задача", goal=goal[:2000], guarded=guarded)


def requested_transition(answer: str) -> str | None:
    """Этап, который модель попросила строкой [переход: …], или None."""
    match = TRANSITION_MARK.search(answer or "")
    return match.group(1).lower() if match else None


# ── Наблюдатель ───────────────────────────────────────────────────────────


def observe(transport: Transport, model: str, task: TaskState, question: str, answer: str) -> tuple[dict[str, Any], AgentReply]:
    """Доклад наблюдателя по последнему обмену. На этапе done не вызывается."""
    instruction = OBSERVER_INSTRUCTIONS.get(task.stage)
    if instruction is None:
        raise TaskError("На этапе done наблюдатель не нужен")
    context = [f"Цель задачи: {task.goal}"]
    if task.steps:
        context.append("План:\n" + "\n".join(f"{i}. {'[x] ' if s.done else ''}{s.text}" for i, s in enumerate(task.steps, 1)))
    if task.stage == "execution":
        context.append(f"Текущий шаг: {task.current + 1} — {task.steps[task.current].text}")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": "\n\n".join(context) + f"\n\nРеплика пользователя:\n{question}\n\nОтвет агента:\n{answer}",
            },
        ],
        "temperature": OBSERVER_TEMPERATURE,
        "max_tokens": 500,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    raw = transport.send(payload)
    reply = _reply_from(raw)
    return _parse_report(reply.content, task.stage), reply


def _reply_from(response: dict[str, Any]) -> AgentReply:
    try:
        choice = response["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise TransportError(f"Наблюдатель вернул неожиданный ответ: {error}") from error
    usage = response.get("usage") or {}
    return AgentReply(
        content=content or "",
        finish_reason=choice.get("finish_reason"),
        model=response.get("model"),
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )


def _parse_report(content: str, stage: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise TransportError(f"Наблюдатель вернул не JSON: {error}") from error
    if not isinstance(data, dict):
        raise TransportError("Наблюдатель вернул не объект")

    if stage == "planning":
        plan = data.get("plan")
        return {
            "plan": [str(s).strip() for s in plan if str(s).strip()] if isinstance(plan, list) else [],
            "plan_ready": bool(data.get("plan_ready")),
        }
    if stage == "execution":
        return {"step_done": data.get("step_done") is True, "note": str(data.get("note", "")).strip()[:MAX_TEXT]}
    verdict = data.get("verdict")
    step = data.get("step")
    issues = data.get("issues")
    return {
        "verdict": verdict if verdict in ("ok", "fail") else "fail",
        "step": step if isinstance(step, int) and not isinstance(step, bool) else None,
        "issues": [str(i).strip() for i in issues if str(i).strip()] if isinstance(issues, list) else [],
    }


# ── Пересказ: повторил ли агент объяснения ─────────────────────────────

_STOP = {"и", "в", "на", "с", "по", "для", "не", "что", "как", "это", "из", "к", "о", "а", "но", "у", "за", "же", "бы", "то", "ли"}
_RETELL_PHRASES = re.compile(
    r"напомн\w*|как мы договор\w*|как обсужд\w*|задача (?:была|состоит|заключа)|давай(?:те)? (?:ещё раз|снова|сначала)|итак, (?:наша )?задача|повтор\w* задач",
    re.IGNORECASE,
)


def retelling(answer: str, task: TaskState) -> dict[str, Any]:
    """
    Пересказал ли агент задачу заново. Дёшево и без модели: фразы-маркеры
    плюс доля слов цели, повторённых в ответе. После паузы и сброса это
    главный признак: продолжил с места — или объяснил всё сначала.
    """
    words = {w for w in re.findall(r"[а-яёa-z0-9]+", task.goal.lower()) if len(w) > 3 and w not in _STOP}
    found = {w for w in re.findall(r"[а-яёa-z0-9]+", answer.lower()) if w in words}
    share = round(len(found) / len(words), 2) if words else 0.0
    phrases = _RETELL_PHRASES.findall(answer)
    return {
        "share": share,
        "phrases": phrases[:3],
        "retold": bool(phrases) or share >= 0.6,
    }


# ── Хранилище: задача — файл рядом с сессией ───────────────────────────

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class TaskStore:
    """
    <сессия>-task.json. Это и есть task scope из дня 11 — только теперь
    вместо словаря фактов там объект с этапом и журналом.
    """

    def __init__(self, directory: str | Path, name: str = "task") -> None:
        self._directory = Path(directory)
        # Имя файла: день 13 и день 15 держат задачи порознь.
        self._name = name

    def load(self, session_id: str) -> TaskState | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TaskState.from_dict(data["task"] if isinstance(data, dict) and "task" in data else data)
        except (OSError, json.JSONDecodeError, TaskError, KeyError, TypeError, ValueError):
            return None

    def save(self, session_id: str, task: TaskState) -> None:
        path = self._path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {"session_id": session_id, "saved_at": _now(), "task": task.as_dict()}
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError as error:
            raise TaskError(f"Не удалось сохранить задачу: {error}") from error

    def delete(self, session_id: str) -> bool:
        try:
            self._path(session_id).unlink()
            return True
        except FileNotFoundError:
            return False

    def describe(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        exists = path.exists()
        return {"file": path.name, "exists": exists, "bytes": path.stat().st_size if exists else 0}

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise TaskError("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}-{self._name}.json"
