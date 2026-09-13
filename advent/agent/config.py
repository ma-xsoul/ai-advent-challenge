"""Конфиг агента: всё, что на прошлой неделе передавалось параметрами в каждый вызов."""

from __future__ import annotations

from dataclasses import dataclass


TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0


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

    def __post_init__(self) -> None:
        if not self.model or not self.model.strip():
            raise ValueError("Укажите модель агента")
        if not TEMPERATURE_MIN <= self.temperature <= TEMPERATURE_MAX:
            raise ValueError(
                f"Температура должна быть от {TEMPERATURE_MIN:g} до {TEMPERATURE_MAX:g}"
            )
        if self.max_tokens < 1:
            raise ValueError("max_tokens должен быть положительным")
