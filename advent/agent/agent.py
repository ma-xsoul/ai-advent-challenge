"""
Агент — отдельная сущность, которая владеет диалогом с моделью.

Снаружи у него три вещи: `ask()` принимает текст и возвращает ответ,
`history` показывает стек сообщений, `usage` — накопленный расход.
Как собирается payload, где лежит ключ, как разбирается JSON — всё это
внутри и наружу не протекает.
"""

from __future__ import annotations

from collections.abc import Generator, Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .config import AgentConfig
from .transport import DeepSeekTransport, Transport, TransportError


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


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

        agent = cls(config, transport)
        agent._messages = messages
        agent._usage = usage
        return agent

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

    def ask(self, text: str) -> AgentReply:
        """Кладёт запрос в стек, спрашивает модель, кладёт ответ в стек."""
        text = text.strip()
        if not text:
            raise ValueError("Пустой запрос")

        self._messages.append(Message("user", text))
        try:
            response = self._transport.send(self._build_payload())
            reply = self._parse(response)
        except Exception:
            # Ответа не было — запрос без пары в стеке оставлять нельзя,
            # иначе следующий вызов уйдёт с двумя user-сообщениями подряд.
            self._messages.pop()
            raise

        self._messages.append(Message("assistant", reply.content))
        self._usage.record(reply)
        return reply

    def stream(self, text: str) -> Generator[str, None, AgentReply]:
        """
        То же, что `ask()`, но куски ответа отдаются по мере генерации.
        Стек и расход обновляются только после того, как ответ дошёл целиком.
        """
        text = text.strip()
        if not text:
            raise ValueError("Пустой запрос")

        self._messages.append(Message("user", text))
        try:
            chunks = self._transport.stream(self._build_payload(stream=True))
            reply = yield from self._consume_stream(chunks)
        except Exception:
            self._messages.pop()
            raise

        self._messages.append(Message("assistant", reply.content))
        self._usage.record(reply)
        return reply

    def reset(self) -> None:
        """Начать диалог заново: стек и расход обнуляются, конфиг остаётся."""
        self._messages.clear()
        self._usage = AgentUsage()

    def snapshot(self) -> dict[str, Any]:
        """Слепок для дебаг-панели: конфиг, стек и расход одним JSON."""
        return {
            "name": self._config.name,
            "config": asdict(self._config),
            "messages": [message.as_payload() for message in self._messages],
            "usage": {**asdict(self._usage), "total_tokens": self._usage.total_tokens},
        }

    def _build_payload(self, *, stream: bool = False) -> dict[str, Any]:
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
