"""
Агент — отдельная сущность, которая владеет диалогом с моделью.

Снаружи у него три вещи: `ask()` принимает текст и возвращает ответ,
`history` показывает стек сообщений, `usage` — накопленный расход.
Как собирается payload, где лежит ключ, как разбирается JSON — всё это
внутри и наружу не протекает.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any, Union

from .config import AgentConfig
from .tokens import estimate_message_tokens, estimate_tokens
from .transport import DeepSeekTransport, Transport, TransportError


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class ContextOverflowError(RuntimeError):
    """
    Стек не помещается в лимит контекста агента. Запрос не отправлялся.

    Это не сбой, а осознанный отказ: агент посчитал, что история вместе с
    новым вопросом не влезет в окно, и не стал тратить токены на запрос,
    который модель всё равно отвергнет. В дне 8 агент только показывал
    ошибку. С дня 9 у него есть политика сжатия: сначала он сворачивает
    историю в конспект и лишь если не помогло — отказывает.
    """

    def __init__(self, estimated: int, limit: int) -> None:
        self.estimated = estimated
        self.limit = limit
        super().__init__(
            f"Стек ≈{estimated} токенов не помещается в лимит контекста {limit}. "
            "Запрос не отправлен."
        )


@dataclass(frozen=True)
class TurnStats:
    """
    Во что обошёлся один ход. Три числа из задания восьмого дня.

    question_tokens — только новый вопрос. Оценка по символам, считается
                      ДО запроса: API это число отдельно не возвращает.
    context_tokens  — весь стек, ушедший в модель: системный промпт плюс
                      вся история плюс новый вопрос. Точно, из API.
    answer_tokens   — ответ модели в этом ходе. Точно, из API.

    Вопрос каждый ход весит примерно одинаково, а стек растёт: модель не
    помнит прошлые ходы, и вся история отправляется заново каждый раз.
    """

    index: int
    question_tokens: int  # ≈ оценка, до запроса
    context_tokens: int | None  # prompt_tokens из API
    answer_tokens: int | None  # completion_tokens из API
    total_tokens: int  # накоплено за весь диалог, вход + выход

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentReply:
    """Ответ модели на один запрос вместе с тем, во что он обошёлся."""

    content: str
    finish_reason: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    # Заполняется только при включённом thinking.
    reasoning: str | None = None


@dataclass
class AgentUsage:
    """Расход за всё время жизни агента — то, что куратор просил считать внутри."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(self, reply: AgentReply) -> None:
        self.requests += 1
        self.input_tokens += reply.input_tokens or 0
        self.output_tokens += reply.output_tokens or 0


# ── Сжатие истории — день 9 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Summary:
    """
    Конспект старой части стека. Хранится отдельно от сообщений и уходит в
    запрос вместо них — это и есть механизм дня 9.

    text            — конспект, как его написала модель.
    covers          — сколько первых сообщений стека он заменяет: в модель
                      уходят конспект + messages[covers:].
    stack_size      — сколько сообщений было в стеке в момент сжатия.
                      Счётчик «каждые every сообщений» отсчитывается отсюда.
    made_after_turn — после какого хода сделан. Пока новых ходов не было,
                      prompt_tokens прошлого запроса описывают старый, ещё
                      не сжатый контекст, и оценку приходится считать заново.
    """

    text: str
    covers: int
    stack_size: int
    made_after_turn: int


@dataclass(frozen=True)
class CompressionRecord:
    """
    Одно сжатие: что свернули и во что это обошлось.

    Запрос на конспект — тоже запрос к модели, и он идёт в общий расход.
    Без этой строки сравнение «до/после» врало бы в пользу сжатия.
    """

    reason: str  # every — сработал счётчик, overflow — не влезали в лимит, manual — кнопка
    compressed: int  # сколько сообщений ушло в конспект в этот раз
    covers: int  # сколько сообщений стека теперь под конспектом
    kept: int  # сколько осталось в контексте «как есть»
    input_tokens: int | None  # запрос на конспект: вход, из API
    output_tokens: int | None  # запрос на конспект: выход, из API
    summary_tokens: int  # ≈ размер конспекта, по символам
    after_turn: int
    # Модель упёрлась в summary_max_tokens и конспект оборван — факты в конце
    # потеряны молча. Это худший исход сжатия, поэтому он помечается явно.
    truncated: bool = False


@dataclass(frozen=True)
class CompressionStarted:
    """Событие потока: агент ушёл сжимать историю. Ответ уже дошёл, `done` ещё нет."""

    reason: str
    messages: int  # сколько сообщений уйдёт в конспект


@dataclass(frozen=True)
class CompressionFailed:
    """Запрос на конспект не удался. Ход при этом состоялся: ответ в стеке, расход учтён."""

    reason: str
    error: str


# ── Sticky Facts — день 10 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class FactsRecord:
    """
    Одно обновление блока фактов: что изменилось и во что обошлось.

    facts — весь блок после обновления. Он нужен не только панели: ветка
    от checkpoint получает факты такими, какими они были в той точке.
    """

    after_turn: int
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    input_tokens: int | None
    output_tokens: int | None
    facts_tokens: int  # ≈ размер блока по символам
    facts: dict[str, str]


@dataclass(frozen=True)
class FactsStarted:
    """Событие потока: ответ дошёл, агент ушёл обновлять факты."""

    keys: int  # сколько ключей в блоке сейчас


@dataclass(frozen=True)
class FactsFailed:
    """Обновление фактов не удалось. Блок остался прежним, ход состоялся."""

    error: str


# Что отдаёт `stream()`: куски текста ответа и события побочных запросов.
# Union вместо `|`: выражение на 3.9, а не аннотация.
StreamEvent = Union[
    str,
    CompressionStarted,
    CompressionRecord,
    CompressionFailed,
    FactsStarted,
    FactsRecord,
    FactsFailed,
]

# Инструкция модели, которая пишет конспект. Она получает старую часть
# стека и прошлый конспект — и возвращает один новый.
#
# Три живых прогона сценария «Собираем ТЗ» написали эти правила:
# первая версия просила «сжать в список» — модель пересказывала ход
# беседы вместе с вопросами ассистента, конспект вышел длиннее исходных
# сообщений и упёрся в потолок токенов; вторая просила «только факты» —
# модель выкинула просьбу «отвечай коротко» как не-факт, агент после
# сжатия стал отвечать в пять раз длиннее, и контекст вырос, а не сжался;
# третья на развёрнутых ответах тащила в конспект все предложения
# ассистента как «решения» и снова упиралась в потолок. Отсюда приоритеты:
# слова пользователя — все, из ответов ассистента — только подтверждённое.
SUMMARY_INSTRUCTION = (
    "Ты сжимаешь историю диалога для ассистента, который продолжит этот разговор.\n"
    "Составь конспект: требования, решения, числа, названия, имена, договорённости, "
    "открытые вопросы.\n"
    "Правила:\n"
    "— инструкции пользователя о том, как отвечать (стиль, длина, формат), сохраняй "
    "обязательно и ставь первыми: они действуют до конца разговора;\n"
    "— только содержание, без пересказа хода беседы: приветствия и вежливость не нужны;\n"
    "— всё, что сказал пользователь — требования, факты, решения, — сохраняй до единого;\n"
    "— из ответов ассистента бери только то, что пользователь явно подтвердил, и открытые "
    "вопросы к нему; предложения ассистента без ответа пользователя не нужны;\n"
    "— если дан прошлый конспект, объедини его с новыми репликами в один: ничего из "
    "прошлого не выбрасывай, повторы схлопни;\n"
    "— каждый пункт — одна строка не длиннее 12 слов, начинается с «—»; всего не больше 30 строк;\n"
    "— ничего не выдумывай и не оценивай.\n"
    "Верни только список строк, без заголовка и выводов."
)
# Конспекту нужна точность, а не разнообразие.
SUMMARY_TEMPERATURE = 0.3
# Так конспект подписан в системном промпте — модель видит, что это не её слова.
SUMMARY_PREFIX = (
    "Конспект предыдущей части диалога ({count} сообщений свёрнуты в него). "
    "Опирайся на него так же, как на историю:"
)


# Инструкция модели, которая ведёт блок фактов. Уроки конспекта дня 9
# зашиты сразу: инструкции о стиле — тоже факт, из ответов ассистента —
# только подтверждённое, ничего не выдумывать. Ключи устойчивые: тот же
# факт при уточнении меняет значение, а не плодит новый ключ.
FACTS_INSTRUCTION = (
    "Ты ведёшь блок фактов диалога для ассистента, который продолжит этот разговор.\n"
    "Тебе даны текущие факты, новая реплика пользователя и ответ ассистента. "
    "Верни обновлённый блок целиком как JSON-объект вида "
    '{{"facts": {{"ключ": "значение"}}}}.\n'
    "Правила:\n"
    "— ключи короткие и устойчивые: цель, платформа, валюты, бюджет, срок, стиль ответов, "
    "ограничение, решение, договорённость; уточнение известного факта меняет значение, "
    "а не добавляет ключ;\n"
    "— значение — одна короткая фраза, числа и названия как есть;\n"
    "— если пользователь изменил или отменил факт — измени или удали ключ;\n"
    "— инструкции пользователя о том, как отвечать, — тоже факт, ключ «стиль ответов»;\n"
    "— из ответа ассистента бери только то, что пользователь явно подтвердил;\n"
    "— ничего не выдумывай: чего нет в реплике и в текущих фактах, того нет в блоке;\n"
    "— не больше {max_facts} ключей."
)
FACTS_TEMPERATURE = 0.2
FACTS_PREFIX = (
    "Факты диалога (ключ — значение), собранные из реплик пользователя. "
    "Опирайся на них так же, как на историю:"
)


class Agent:
    """
    Один агент — один диалог с одной моделью и одним конфигом.

    Стек сообщений копится внутри: каждый `ask()` отправляет модели всю
    историю, а не только последнюю реплику. Именно этим агент отличается
    от функции-обёртки над HTTP из первых дней.

    С политикой сжатия (день 9) в модель уходит не весь стек, а конспект
    старой части плюс последние сообщения. Стек при этом не редеет:
    память агента и контекст запроса — разные вещи. День 10 добавляет
    ещё две стратегии: окно (только последние N) и факты (блок
    «ключ — значение» плюс последние N). Какая включена — решает конфиг.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._config = config or AgentConfig()
        self._transport = transport or DeepSeekTransport()
        self._messages: list[Message] = []
        self._usage = AgentUsage()
        self._turns: list[TurnStats] = []
        self._summary: Summary | None = None
        self._compressions: list[CompressionRecord] = []
        self._facts: dict[str, str] = {}
        self._fact_updates: list[FactsRecord] = []
        # День 11: слои памяти живут снаружи агента — как дерево веток в
        # дне 10. Сюда их кладут готовым текстом перед ходом, и агент
        # подмешивает его в системный промпт. В слепок не попадает:
        # у слоёв своё хранилище и свои сроки жизни.
        self._memory_text: str | None = None

    @classmethod
    def restore(
        cls,
        data: dict[str, Any],
        transport: Transport | None = None,
    ) -> Agent:
        """
        Собирает агента из слепка `snapshot()`: конфиг, стек и расход
        возвращаются ровно такими, какими были. Обратная операция к snapshot().
        """
        config_data = data.get("config")
        if not isinstance(config_data, dict):
            raise ValueError("В слепке нет конфига агента")
        config = AgentConfig(**config_data)

        messages: list[Message] = []
        for item in data.get("messages", []):
            role = item.get("role") if isinstance(item, dict) else None
            content = item.get("content") if isinstance(item, dict) else None
            if role not in ("user", "assistant") or not isinstance(content, str):
                raise ValueError("В слепке повреждён стек сообщений")
            messages.append(Message(role, content))

        usage_data = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = AgentUsage(
            requests=int(usage_data.get("requests", 0) or 0),
            input_tokens=int(usage_data.get("input_tokens", 0) or 0),
            output_tokens=int(usage_data.get("output_tokens", 0) or 0),
        )

        turns: list[TurnStats] = []
        for item in data.get("turns", []) or []:
            if not isinstance(item, dict):
                continue
            turns.append(
                TurnStats(
                    index=int(item.get("index", len(turns) + 1)),
                    question_tokens=int(item.get("question_tokens", 0) or 0),
                    context_tokens=_int_or_none(item.get("context_tokens")),
                    answer_tokens=_int_or_none(item.get("answer_tokens")),
                    total_tokens=int(item.get("total_tokens", 0) or 0),
                )
            )

        summary: Summary | None = None
        summary_data = data.get("summary")
        if isinstance(summary_data, dict) and isinstance(summary_data.get("text"), str):
            summary = Summary(
                text=summary_data["text"],
                covers=min(int(summary_data.get("covers", 0) or 0), len(messages)),
                stack_size=int(summary_data.get("stack_size", 0) or 0),
                made_after_turn=int(summary_data.get("made_after_turn", 0) or 0),
            )

        compressions: list[CompressionRecord] = []
        for item in data.get("compressions", []) or []:
            if not isinstance(item, dict):
                continue
            compressions.append(
                CompressionRecord(
                    reason=str(item.get("reason") or "every"),
                    compressed=int(item.get("compressed", 0) or 0),
                    covers=int(item.get("covers", 0) or 0),
                    kept=int(item.get("kept", 0) or 0),
                    input_tokens=_int_or_none(item.get("input_tokens")),
                    output_tokens=_int_or_none(item.get("output_tokens")),
                    summary_tokens=int(item.get("summary_tokens", 0) or 0),
                    after_turn=int(item.get("after_turn", 0) or 0),
                    truncated=bool(item.get("truncated", False)),
                )
            )

        facts_data = data.get("facts")
        facts = _clean_facts(facts_data) if isinstance(facts_data, dict) else {}

        fact_updates: list[FactsRecord] = []
        for item in data.get("fact_updates", []) or []:
            if not isinstance(item, dict):
                continue
            fact_updates.append(
                FactsRecord(
                    after_turn=int(item.get("after_turn", 0) or 0),
                    added=tuple(str(k) for k in item.get("added", []) or []),
                    changed=tuple(str(k) for k in item.get("changed", []) or []),
                    removed=tuple(str(k) for k in item.get("removed", []) or []),
                    input_tokens=_int_or_none(item.get("input_tokens")),
                    output_tokens=_int_or_none(item.get("output_tokens")),
                    facts_tokens=int(item.get("facts_tokens", 0) or 0),
                    facts=_clean_facts(item.get("facts")) if isinstance(item.get("facts"), dict) else {},
                )
            )

        agent = cls(config, transport)
        agent._messages = messages
        agent._usage = usage
        agent._turns = turns
        agent._summary = summary
        agent._compressions = compressions
        agent._facts = facts
        agent._fact_updates = fact_updates
        return agent

    def with_config(self, **changes: Any) -> Agent:
        """
        Тот же диалог с изменённым конфигом: конфиг новый — стек старый.

        Конфиг заморожен намеренно, поэтому смена лимита или температуры —
        это новый экземпляр агента, которому передали стек, расход и
        статистику ходов. Так на странице снимаешь лимит после переполнения
        и продолжаешь разговор с того же места.
        """
        data = self.snapshot()
        data["config"] = asdict(replace(self._config, **changes))
        return type(self).restore(data, transport=self._transport)

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def history(self) -> tuple[Message, ...]:
        """Стек сообщений без системного промпта — он часть конфига, а не диалога."""
        return tuple(self._messages)

    @property
    def context_messages(self) -> tuple[Message, ...]:
        """
        Та часть стека, что уходит в модель. Память ≠ контекст.

        full    — весь стек;
        summary — сообщения после конспекта;
        window  — последние window сообщений, остальное отброшено;
        facts   — последние keep_last, остальное живёт только в фактах.
        """
        if self._summary is not None:
            start = self._summary.covers
        elif self._config.window is not None:
            start = self._tail_start(self._config.window)
        elif self._config.facts is not None:
            start = self._tail_start(self._config.facts.keep_last)
        else:
            start = 0
        return tuple(self._messages[start:])

    @property
    def memory_text(self) -> str | None:
        """Слои памяти дня 11 текстом. Пусто, пока их не подложили."""
        return self._memory_text

    @memory_text.setter
    def memory_text(self, value: str | None) -> None:
        self._memory_text = value or None

    @property
    def facts(self) -> dict[str, str]:
        """Блок «ключ — значение» стратегии Sticky Facts. Пуст, пока стратегия не включена."""
        return dict(self._facts)

    @property
    def fact_updates(self) -> tuple[FactsRecord, ...]:
        return tuple(self._fact_updates)

    def _tail_start(self, keep: int) -> int:
        """
        Индекс, с которого стек уходит в модель «как есть».

        keep считается по истории до нового вопроса — сам вопрос уходит
        всегда. Хвост начинается с реплики пользователя: если keep режет
        пару «вопрос — ответ», пара остаётся целиком.
        """
        end = len(self._messages)
        if end and self._messages[-1].role == "user":
            end -= 1
        cut = max(0, end - keep)
        if 0 < cut < end and self._messages[cut].role != "user":
            cut -= 1
        return cut

    @property
    def summary(self) -> Summary | None:
        return self._summary

    @property
    def compressions(self) -> tuple[CompressionRecord, ...]:
        """Все сжатия за диалог — по одной записи на запрос конспекта."""
        return tuple(self._compressions)

    @property
    def usage(self) -> AgentUsage:
        return self._usage

    @property
    def turns(self) -> tuple[TurnStats, ...]:
        """Статистика по ходам — по одной записи на каждый успешный ответ."""
        return tuple(self._turns)

    def estimate_context(self, next_question: str | None = None) -> int:
        """
        Сколько токенов уйдёт в следующий запрос, по оценке.

        После первого хода оценка почти точная: берём prompt_tokens прошлого
        запроса (точно, из API), прибавляем прошлый ответ (точно, из API)
        и новый вопрос (единственное, что оцениваем по символам).
        До первого хода — и сразу после сжатия, пока новых ходов не было, —
        считаем всё по символам: цифры API описывают старый контекст.
        """
        last = self._turns[-1] if self._turns else None
        api_numbers_hold = (
            last is not None
            and last.context_tokens is not None
            and not self._compressed_after(last)
            # Окно и факты меняют состав запроса каждый ход — цифры прошлого
            # запроса ничего не говорят о следующем.
            and self._config.strategy in ("full", "summary")
        )
        if api_numbers_hold:
            estimated = last.context_tokens + (last.answer_tokens or 0)
        else:
            estimated = estimate_message_tokens(self._system_text() or "")
            estimated += sum(estimate_message_tokens(m.content) for m in self.context_messages)
        if next_question:
            estimated += estimate_message_tokens(next_question)
        return estimated

    def _compressed_after(self, turn: TurnStats) -> bool:
        return self._summary is not None and self._summary.made_after_turn == turn.index

    def _make_room(self, text: str) -> Generator[StreamEvent, None, None]:
        """
        Проверка лимита — ДО того, как вопрос попал в стек и ДО запроса.

        Порядок важен: если бы вопрос сначала ложился в стек, отказ оставлял
        бы в истории user-сообщение без ответа, и следующий ход ушёл бы
        с двумя user подряд. Переполнение — отказ отправить, стек цел.

        День 9: с политикой сжатия агент не упирается в стену сразу. Если
        есть что свернуть, он сначала делает конспект и проверяет снова;
        ошибка — только когда история не влезает и после сжатия.
        """
        limit = self._config.context_limit
        if limit is None:
            return
        estimated = self.estimate_context(text)
        if estimated <= limit:
            return
        if self._config.compression is not None and self._compressible() > 0:
            yield from self._compress_events("overflow")
            estimated = self.estimate_context(text)
            if estimated <= limit:
                return
        raise ContextOverflowError(estimated, limit)

    def ask(self, text: str) -> AgentReply:
        """Кладёт запрос в стек, спрашивает модель, кладёт ответ в стек."""
        text = text.strip()
        if not text:
            raise ValueError("Пустой запрос")
        # Лимит проверяется до того, как вопрос попал в стек: переполнение —
        # это отказ отправлять, а не испорченная история.
        self._drain(self._make_room(text))

        self._messages.append(Message("user", text))
        try:
            response = self._transport.send(self._build_payload())
            reply = self._parse(response)
        except Exception:
            # Ответа не было — запрос без пары в стеке оставлять нельзя,
            # иначе следующий вызов уйдёт с двумя user-сообщениями подряд.
            self._messages.pop()
            raise

        self._commit(text, reply)
        self._drain(self._compress_if_due())
        self._drain(self._facts_if_due(text, reply.content))
        return reply

    def stream(self, text: str) -> Generator[StreamEvent, None, AgentReply]:
        """
        То же, что `ask()`, но куски ответа отдаются по мере генерации.
        Стек и расход обновляются только после того, как ответ дошёл целиком.

        Кроме текста генератор отдаёт события сжатия: до ответа — если
        пришлось освобождать место под лимит, после — если сработал счётчик.
        """
        text = text.strip()
        if not text:
            raise ValueError("Пустой запрос")
        yield from self._make_room(text)

        self._messages.append(Message("user", text))
        try:
            chunks = self._transport.stream(self._build_payload(stream=True))
            reply = yield from self._consume_stream(chunks)
        except Exception:
            self._messages.pop()
            raise

        self._commit(text, reply)
        yield from self._compress_if_due()
        yield from self._facts_if_due(text, reply.content)
        return reply

    def _commit(self, question: str, reply: AgentReply) -> None:
        """Ответ дошёл: положить в стек, прибавить расход, записать ход."""
        self._messages.append(Message("assistant", reply.content))
        self._usage.record(reply)
        self._turns.append(
            TurnStats(
                index=len(self._turns) + 1,
                question_tokens=estimate_message_tokens(question),
                context_tokens=reply.input_tokens,
                answer_tokens=reply.output_tokens,
                total_tokens=self._usage.total_tokens,
            )
        )

    # ── Сжатие ──────────────────────────────────────────────────────────────

    def compress(self, reason: str = "manual") -> CompressionRecord | None:
        """
        Свернуть старую часть стека в конспект отдельным запросом к модели.

        Всё, кроме последних keep_last сообщений, уходит модели с просьбой
        оставить одни факты; прошлый конспект, если был, объединяется с
        новыми репликами — конспект всегда один. Стек при этом не трогается:
        сжимается контекст, а не память. Возвращает запись о сжатии или
        None, если сжимать нечего.
        """
        policy = self._config.compression
        if policy is None:
            raise ValueError("У агента нет политики сжатия")
        covers = self._summary.covers if self._summary else 0
        cut = self._cut_point()
        if cut <= covers:
            return None

        old = self._messages[covers:cut]
        reply = self._parse(self._transport.send(self._build_summary_payload(old)))
        # Конспект стоит токенов, и они идут в тот же счётчик, что и ходы.
        self._usage.record(reply)

        text = reply.content.strip()
        self._summary = Summary(
            text=text,
            covers=cut,
            stack_size=len(self._messages),
            made_after_turn=len(self._turns),
        )
        record = CompressionRecord(
            reason=reason,
            compressed=len(old),
            covers=cut,
            kept=len(self._messages) - cut,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            summary_tokens=estimate_tokens(text),
            after_turn=len(self._turns),
            truncated=reply.finish_reason == "length",
        )
        self._compressions.append(record)
        return record

    def _cut_point(self) -> int:
        """С какого сообщения стек остаётся «как есть» при сжатии: последние keep_last."""
        policy = self._config.compression
        return self._tail_start(policy.keep_last if policy else 0)

    def _compressible(self) -> int:
        """Сколько сообщений ушло бы в конспект прямо сейчас."""
        covers = self._summary.covers if self._summary else 0
        return max(0, self._cut_point() - covers)

    def _compress_if_due(self) -> Generator[StreamEvent, None, None]:
        """
        Триггер из задания: каждые every сообщений диалога всё, кроме
        последних keep_last, сворачивается в конспект. Счёт идёт от
        прошлого сжатия, поэтому неудавшийся конспект повторится на
        следующем ходу, а сжатие по кнопке сдвигает счётчик.
        """
        policy = self._config.compression
        if policy is None or self._messages_since_compression() < policy.every:
            return
        yield from self._compress_events("every", keep_going=True)

    def _messages_since_compression(self) -> int:
        return len(self._messages) - (self._summary.stack_size if self._summary else 0)

    def _compress_events(self, reason: str, *, keep_going: bool = False) -> Generator[StreamEvent, None, None]:
        """
        Сжатие как события потока: «начали» — «готово» (или «не вышло»).

        keep_going — не ронять ход, если конспект не удался: ответ уже в
        стеке, и следующий ход попробует сжать снова. Без keep_going ошибка
        транспорта уходит наверх — так ведёт себя сжатие перед запросом.
        """
        yield CompressionStarted(reason, self._compressible())
        try:
            record = self.compress(reason)
        except TransportError as error:
            if not keep_going:
                raise
            yield CompressionFailed(reason, str(error))
            return
        if record is not None:
            yield record

    # ── Факты ───────────────────────────────────────────────────────────────

    def update_facts(self, question: str, answer: str) -> FactsRecord:
        """
        Переписать блок фактов по свежей реплике пользователя.

        Модель получает текущие факты, реплику и ответ — и возвращает блок
        целиком: так факт может уточниться, смениться или исчезнуть, а не
        только добавиться. Старые сообщения при этом никуда не уходят из
        памяти, но в запрос попадает лишь то, что осталось в фактах.
        """
        policy = self._config.facts
        if policy is None:
            raise ValueError("У агента нет политики фактов")

        reply = self._parse(self._transport.send(self._build_facts_payload(question, answer)))
        # Блок фактов стоит токенов — и они в том же счётчике, что и ходы.
        self._usage.record(reply)
        try:
            facts = _parse_facts(reply.content, policy.max_facts)
        except ValueError as error:
            raise TransportError(f"Блок фактов не разобрался: {error}") from error

        before = self._facts
        record = FactsRecord(
            after_turn=len(self._turns),
            added=tuple(key for key in facts if key not in before),
            changed=tuple(key for key in facts if key in before and before[key] != facts[key]),
            removed=tuple(key for key in before if key not in facts),
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            facts_tokens=estimate_tokens(self._facts_text(facts)),
            facts=dict(facts),
        )
        self._facts = facts
        self._fact_updates.append(record)
        return record

    def _facts_if_due(self, question: str, answer: str) -> Generator[StreamEvent, None, None]:
        """Триггер из задания: после каждой реплики пользователя блок фактов обновляется."""
        if self._config.facts is None:
            return
        yield FactsStarted(len(self._facts))
        try:
            record = self.update_facts(question, answer)
        except TransportError as error:
            # Ход состоялся, блок остался прежним — следующая реплика попробует снова.
            yield FactsFailed(str(error))
            return
        yield record

    def _build_facts_payload(self, question: str, answer: str) -> dict[str, Any]:
        """Запрос на факты: JSON-режим, низкая температура, без потока. Та же модель и тот же счётчик."""
        policy = self._config.facts
        assert policy is not None
        current = json.dumps(self._facts, ensure_ascii=False, indent=1) if self._facts else "{}"
        content = (
            f"Текущие факты:\n{current}\n\n"
            f"Новая реплика пользователя:\n{question}\n\n"
            f"Ответ ассистента:\n{answer}"
        )
        return {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": FACTS_INSTRUCTION.format(max_facts=policy.max_facts)},
                {"role": "user", "content": content},
            ],
            "temperature": FACTS_TEMPERATURE,
            "max_tokens": policy.max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        }

    @staticmethod
    def _facts_text(facts: dict[str, str]) -> str:
        return "\n".join(f"- {key}: {value}" for key, value in facts.items())

    @staticmethod
    def _drain(events: Iterable[StreamEvent]) -> None:
        """`ask()` без потока: события сжатия некому показывать, просто прокручиваем их."""
        for _ in events:
            pass

    def _build_summary_payload(self, old: list[Message]) -> dict[str, Any]:
        """
        Запрос на конспект: своя инструкция, низкая температура, без потока.
        Та же модель и тот же транспорт, что у ходов, — и тот же счётчик.
        """
        policy = self._config.compression
        assert policy is not None
        lines = [
            f"{'Пользователь' if message.role == 'user' else 'Ассистент'}: {message.content}"
            for message in old
        ]
        previous = self._summary.text if self._summary else "— пока нет —"
        content = f"Прошлый конспект:\n{previous}\n\nНовые реплики:\n" + "\n".join(lines)
        return {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": SUMMARY_INSTRUCTION},
                {"role": "user", "content": content},
            ],
            "temperature": SUMMARY_TEMPERATURE,
            "max_tokens": policy.summary_max_tokens,
            "thinking": {"type": "disabled"},
            "stream": False,
        }

    # ── Состояние ───────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Начать диалог заново: стек, расход и конспект обнуляются, конфиг остаётся."""
        self._messages.clear()
        self._usage = AgentUsage()
        self._turns.clear()
        self._summary = None
        self._compressions.clear()
        self._facts = {}
        self._fact_updates.clear()

    def snapshot(self) -> dict[str, Any]:
        """Слепок для дебаг-панели: конфиг, стек, расход, конспект и факты одним JSON."""
        covers = self._summary.covers if self._summary else 0
        return {
            "name": self._config.name,
            "strategy": self._config.strategy,
            "config": asdict(self._config),
            "messages": [message.as_payload() for message in self._messages],
            "usage": {**asdict(self._usage), "total_tokens": self._usage.total_tokens},
            "turns": [turn.as_dict() for turn in self._turns],
            "summary": self._summary_snapshot(),
            "compressions": [asdict(record) for record in self._compressions],
            "facts": dict(self._facts),
            "fact_updates": [asdict(record) for record in self._fact_updates],
            "context": {
                "estimated": self.estimate_context(),
                "limit": self._config.context_limit,
                # Сколько сообщений стека уходит в модель, сколько накрыто
                # конспектом и сколько просто не попадает в запрос.
                "messages": len(self.context_messages),
                "summarized": covers,
                "dropped": len(self._messages) - len(self.context_messages) - covers,
            },
        }

    def fork(self, message_count: int) -> dict[str, Any]:
        """
        Слепок диалога, обрезанный до первых message_count сообщений, —
        точка ветвления (день 10, Branching).

        Ветка — новый агент из этого слепка: тот же конфиг, та же история
        до checkpoint, дальше — свой стек. Расход ветки — только за общую
        часть; конспект и факты — какими они были в той точке.
        """
        if message_count < 0 or message_count > len(self._messages) or message_count % 2:
            raise ValueError("Checkpoint ставится после ответа агента: чётное число сообщений в пределах стека")
        turns = message_count // 2
        kept_turns = self._turns[:turns]
        compressions = [record for record in self._compressions if record.after_turn <= turns]
        updates = [record for record in self._fact_updates if record.after_turn <= turns]

        input_tokens = sum(turn.context_tokens or 0 for turn in kept_turns)
        output_tokens = sum(turn.answer_tokens or 0 for turn in kept_turns)
        for record in (*compressions, *updates):
            input_tokens += record.input_tokens or 0
            output_tokens += record.output_tokens or 0

        summary = self._summary
        if summary is not None and (summary.made_after_turn > turns or summary.covers > message_count):
            summary = None

        data = {
            "name": self._config.name,
            "config": asdict(self._config),
            "messages": [message.as_payload() for message in self._messages[:message_count]],
            "usage": {
                "requests": len(kept_turns) + len(compressions) + len(updates),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "turns": [turn.as_dict() for turn in kept_turns],
            "summary": asdict(summary) if summary else None,
            "compressions": [asdict(record) for record in compressions],
            "facts": dict(updates[-1].facts) if updates else {},
            "fact_updates": [asdict(record) for record in updates],
        }
        return type(self).restore(data, transport=self._transport).snapshot()

    def _summary_snapshot(self) -> dict[str, Any] | None:
        if self._summary is None:
            return None
        return {
            **asdict(self._summary),
            "tokens": estimate_tokens(self._summary.text),
            "compressions": len(self._compressions),
            "input_tokens": sum(record.input_tokens or 0 for record in self._compressions),
            "output_tokens": sum(record.output_tokens or 0 for record in self._compressions),
        }

    def _system_text(self) -> str | None:
        """
        Системный промпт вместе с конспектом. Конспект — часть инструкции,
        а не диалога: так модель не принимает его за свои прошлые слова.
        """
        parts = []
        if self._config.system_prompt:
            parts.append(self._config.system_prompt)
        if self._summary is not None:
            parts.append(SUMMARY_PREFIX.format(count=self._summary.covers) + "\n" + self._summary.text)
        if self._config.facts is not None and self._facts:
            parts.append(FACTS_PREFIX + "\n" + self._facts_text(self._facts))
        if self._memory_text:
            parts.append(self._memory_text)
        return "\n\n".join(parts) or None

    def _build_payload(self, *, stream: bool = False) -> dict[str, Any]:
        """
        Собирает запрос к модели. Ключевая строка — messages.extend(...):
        в запрос уходит стек, а не последняя реплика. Модель не хранит
        состояние между вызовами, поэтому каждый ход мы пересылаем ей весь
        разговор с самого начала. Отсюда рост context_tokens в TurnStats:
        спрашиваем 25 токенов, а платим за 700.

        День 9: с конспектом уходит не весь стек, а context_messages —
        сообщения после конспекта; сам конспект вшит в системный промпт.
        День 10: окно режет context_messages до последних N, факты
        добавляют в системный промпт блок «ключ — значение».
        """
        messages: list[dict[str, str]] = []
        system = self._system_text()
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(message.as_payload() for message in self.context_messages)

        return {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "thinking": {"type": "enabled" if self._config.thinking else "disabled"},
            "stream": stream,
        }

    def _consume_stream(
        self, chunks: Iterable[dict[str, Any]]
    ) -> Generator[str, None, AgentReply]:
        parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: str | None = None
        model: str | None = None
        usage: dict[str, Any] = {}

        for chunk in chunks:
            if isinstance(chunk.get("model"), str):
                model = chunk["model"]
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]

            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue
            choice = choices[0]
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]

            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
            content = delta.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
                yield content

        content = "".join(parts)
        if not content.strip():
            raise TransportError("DeepSeek не вернул текст ответа")

        return AgentReply(
            content=content,
            finish_reason=finish_reason or "не указан",
            model=model or self._config.model,
            input_tokens=_int_or_none(usage.get("prompt_tokens")),
            output_tokens=_int_or_none(usage.get("completion_tokens")),
            reasoning="".join(reasoning_parts) or None,
        )

    def _parse(self, response: dict[str, Any]) -> AgentReply:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise TransportError("DeepSeek не вернул вариант ответа")

        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise TransportError("DeepSeek не вернул сообщение ответа")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise TransportError("DeepSeek не вернул текст ответа")

        usage = response.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        reasoning = message.get("reasoning_content")

        return AgentReply(
            content=content,
            finish_reason=choice.get("finish_reason") or "не указан",
            model=response.get("model") or self._config.model,
            input_tokens=_int_or_none(usage.get("prompt_tokens")),
            output_tokens=_int_or_none(usage.get("completion_tokens")),
            reasoning=reasoning if isinstance(reasoning, str) and reasoning else None,
        )


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_facts(content: str, limit: int) -> dict[str, str]:
    """
    Разобрать ответ модели в блок фактов. Принимает и {"facts": {...}},
    и голый объект: JSON-режим гарантирует объект, но не его форму.
    """
    data = json.loads(content)
    if isinstance(data, dict) and isinstance(data.get("facts"), dict):
        data = data["facts"]
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект с фактами")
    facts = _clean_facts(data)
    return dict(list(facts.items())[:limit])


def _clean_facts(data: dict[Any, Any]) -> dict[str, str]:
    """Ключи и значения — строки; списки склеиваются, пустое выбрасывается."""
    facts: dict[str, str] = {}
    for key, value in data.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        text = str(value).strip()
        if text:
            facts[name] = text
    return facts
