"""
Агент — отдельная сущность, которая владеет диалогом с моделью.

Снаружи у него три вещи: `ask()` принимает текст и возвращает ответ,
`history` показывает стек сообщений, `usage` — накопленный расход.
Как собирается payload, где лежит ключ, как разбирается JSON — всё это
внутри и наружу не протекает.
"""

from __future__ import annotations

from collections.abc import Generator, Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

from .config import AgentConfig
from .tokens import estimate_message_tokens
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
    который модель всё равно отвергнет. Сегодня — только показать ошибку.
    Научить агента не упираться в стену — сжимать историю — задача дня 9.
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


class Agent:
    """
    Один агент — один диалог с одной моделью и одним конфигом.

    Стек сообщений копится внутри: каждый `ask()` отправляет модели всю
    историю, а не только последнюю реплику. Именно этим агент отличается
    от функции-обёртки над HTTP из первых дней.
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

        agent = cls(config, transport)
        agent._messages = messages
        agent._usage = usage
        agent._turns = turns
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
        До первого хода считаем всё по символам.
        """
        if self._turns and self._turns[-1].context_tokens is not None:
            last = self._turns[-1]
            estimated = last.context_tokens + (last.answer_tokens or 0)
        else:
            estimated = estimate_message_tokens(self._config.system_prompt or "")
            estimated += sum(estimate_message_tokens(m.content) for m in self._messages)
        if next_question:
            estimated += estimate_message_tokens(next_question)
        return estimated

    def _check_context_limit(self, text: str) -> None:
        """
        Проверка лимита — ДО того, как вопрос попал в стек и ДО запроса.

        Порядок важен: если бы вопрос сначала ложился в стек, отказ оставлял
        бы в истории user-сообщение без ответа, и следующий ход ушёл бы
        с двумя user подряд. Переполнение — отказ отправить, стек цел.
        """
        limit = self._config.context_limit
        if limit is None:
            return
        estimated = self.estimate_context(text)
        if estimated > limit:
            raise ContextOverflowError(estimated, limit)

    def ask(self, text: str) -> AgentReply:
        """Кладёт запрос в стек, спрашивает модель, кладёт ответ в стек."""
        text = text.strip()
        if not text:
            raise ValueError("Пустой запрос")
        # Лимит проверяется до того, как вопрос попал в стек: переполнение —
        # это отказ отправлять, а не испорченная история.
        self._check_context_limit(text)

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
        return reply

    def stream(self, text: str) -> Generator[str, None, AgentReply]:
        """
        То же, что `ask()`, но куски ответа отдаются по мере генерации.
        Стек и расход обновляются только после того, как ответ дошёл целиком.
        """
        text = text.strip()
        if not text:
            raise ValueError("Пустой запрос")
        self._check_context_limit(text)

        self._messages.append(Message("user", text))
        try:
            chunks = self._transport.stream(self._build_payload(stream=True))
            reply = yield from self._consume_stream(chunks)
        except Exception:
            self._messages.pop()
            raise

        self._commit(text, reply)
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

    def reset(self) -> None:
        """Начать диалог заново: стек и расход обнуляются, конфиг остаётся."""
        self._messages.clear()
        self._usage = AgentUsage()
        self._turns.clear()

    def snapshot(self) -> dict[str, Any]:
        """Слепок для дебаг-панели: конфиг, стек и расход одним JSON."""
        return {
            "name": self._config.name,
            "config": asdict(self._config),
            "messages": [message.as_payload() for message in self._messages],
            "usage": {**asdict(self._usage), "total_tokens": self._usage.total_tokens},
            "turns": [turn.as_dict() for turn in self._turns],
            "context": {
                "estimated": self.estimate_context(),
                "limit": self._config.context_limit,
            },
        }

    def _build_payload(self, *, stream: bool = False) -> dict[str, Any]:
        """
        Собирает запрос к модели. Ключевая строка — messages.extend(...):
        в запрос уходит ВЕСЬ стек, а не последняя реплика. Модель не хранит
        состояние между вызовами, поэтому каждый ход мы пересылаем ей весь
        разговор с самого начала. Отсюда рост context_tokens в TurnStats:
        спрашиваем 25 токенов, а платим за 700.
        """
        messages: list[dict[str, str]] = []
        if self._config.system_prompt:
            messages.append({"role": "system", "content": self._config.system_prompt})
        messages.extend(message.as_payload() for message in self._messages)

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
