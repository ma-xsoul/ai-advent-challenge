"""
Оценка токенов без токенизатора.

Точные числа даёт API в ответе — но только после запроса. Оценка нужна
до него: чтобы показать вес нового вопроса и проверить лимит контекста
перед отправкой. Куратор подтвердил, что приблизительная оценка
допустима, поэтому здесь эвристика по символам, а не токенизатор.
"""

from __future__ import annotations

import math


# Калибровка по реальным ответам DeepSeek на русских диалогах:
# около 2.5 символа кириллицы на токен, около 4 — латиницы и прочего.
# Проверка на живом прогоне: оценка вопроса ≈27 при фактических ~25.
# Для решения «влезет или нет» такой точности достаточно.
CYRILLIC_CHARS_PER_TOKEN = 2.5
OTHER_CHARS_PER_TOKEN = 4.0

# Служебная обвязка одного сообщения в chat-формате: роль, разделители.
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str) -> int:
    """Приблизительное число токенов в тексте. Всегда ≥ 1 для непустого текста."""
    if not text:
        return 0
    cyrillic = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    other = len(text) - cyrillic
    return max(1, math.ceil(cyrillic / CYRILLIC_CHARS_PER_TOKEN + other / OTHER_CHARS_PER_TOKEN))


def estimate_message_tokens(text: str) -> int:
    """Токены сообщения вместе с его обвязкой в запросе."""
    return estimate_tokens(text) + MESSAGE_OVERHEAD_TOKENS
