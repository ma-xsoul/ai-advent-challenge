"""Конфиг агента: всё, что на прошлой неделе передавалось параметрами в каждый вызов."""

from __future__ import annotations

from dataclasses import dataclass


TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0


@dataclass(frozen=True)
class CompressionPolicy:
    """
    Политика сжатия истории — день 9.

    keep_last — сколько последних сообщений стека уходят в модель «как есть».
    every     — каждые столько сообщений диалога всё, кроме последних
                keep_last, сворачивается в конспект (summary) отдельным
                запросом к модели. Конспект хранится отдельно от стека и
                подставляется в запрос вместо старых сообщений.
    summary_max_tokens — потолок длины конспекта. Это предохранитель, а не
                способ сжатия: оборванный конспект молча теряет факты, и
                агент помечает такой случай в записи о сжатии.

    Сжатие — это не удаление: стек в памяти агента остаётся целым, режется
    только то, что уходит в запрос. Удаление — Sliding Window, день 10.
    """

    keep_last: int = 4
    every: int = 10
    summary_max_tokens: int = 800

    def __post_init__(self) -> None:
        if self.keep_last < 0:
            raise ValueError("keep_last не может быть отрицательным")
        if self.every < 2:
            raise ValueError("every должен быть не меньше 2")
        if self.keep_last >= self.every:
            raise ValueError("keep_last должен быть меньше every — иначе сжимать нечего")
        if self.summary_max_tokens < 1:
            raise ValueError("summary_max_tokens должен быть положительным")


@dataclass(frozen=True)
class AgentConfig:
    """
    Неизменяемый набор настроек одного агента.

    Заморожен намеренно: конфиг задаётся при создании агента, а не меняется
    по ходу диалога — иначе история и параметры запросов разойдутся.
    Нужны другие настройки — создайте другого агента.
    """

    model: str = "deepseek-v4-flash"
    system_prompt: str | None = None
    temperature: float = 1.0
    max_tokens: int = 1024
    # При включённом thinking DeepSeek молча игнорирует temperature и top_p —
    # см. day 4. По умолчанию выключен, как и во всём проекте.
    thinking: bool = False
    # Имя нужно, когда агентов больше одного: дебаг-панель и будущие суб-агенты.
    name: str = "agent"
    # Лимит контекста агента в токенах — отдельно от окна модели.
    # У deepseek-v4-flash окно 128 000 токенов: чтобы упереться в него,
    # нужны сотни ходов. Агент может держать окно меньше — например 700, —
    # и тогда переполнение видно за десять реплик. None — без лимита.
    context_limit: int | None = None
    # Политика сжатия истории. None — в модель уходит весь стек, как в днях 6–8.
    compression: CompressionPolicy | None = None

    def __post_init__(self) -> None:
        if not self.model or not self.model.strip():
            raise ValueError("Укажите модель агента")
        if not TEMPERATURE_MIN <= self.temperature <= TEMPERATURE_MAX:
            raise ValueError(
                f"Температура должна быть от {TEMPERATURE_MIN:g} до {TEMPERATURE_MAX:g}"
            )
        if self.max_tokens < 1:
            raise ValueError("max_tokens должен быть положительным")
        if self.context_limit is not None and self.context_limit < 1:
            raise ValueError("context_limit должен быть положительным или None")
        # Из слепка на диске политика приходит словарём — собираем её обратно.
        # Заморозка обходится через object.__setattr__: это единственное
        # место, где конфиг дописывает сам себя, и только при создании.
        if isinstance(self.compression, dict):
            object.__setattr__(self, "compression", CompressionPolicy(**self.compression))
        if self.compression is not None and not isinstance(self.compression, CompressionPolicy):
            raise ValueError("compression должен быть CompressionPolicy или None")
