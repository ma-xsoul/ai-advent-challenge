from dataclasses import dataclass


@dataclass(frozen=True)
class AdventDay:
    number: int
    title: str
    description: str
    endpoint: str
    status: str
    visible: bool = True


DAYS = (
    AdventDay(
        number=1,
        title="Первый запрос к LLM",
        description="LLM через API",
        endpoint="day01.index",
        status="Готово",
    ),
    AdventDay(
        number=2,
        title="Формат ответа",
        description="Контроль ответа через API",
        endpoint="day02.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=3,
        title="Способы рассуждения",
        description="Сравнение подходов к решению",
        endpoint="day03.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=4,
        title="Температура",
        description="Влияние temperature на ответ",
        endpoint="day04.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=5,
        title="Версии моделей",
        description="Слабая, средняя и сильная модель",
        endpoint="day05.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=6,
        title="Первый агент",
        description="Агент как отдельная сущность",
        endpoint="day06.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=7,
        title="Сохранение контекста",
        description="Агент помнит диалог между запусками",
        endpoint="day07.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=8,
        title="Работа с токенами",
        description="Сколько стоит каждый ход диалога",
        endpoint="day08.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=9,
        title="Сжатие истории",
        description="Summary вместо старых сообщений",
        endpoint="day09.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=10,
        title="Стратегии контекста",
        description="Sliding Window · Sticky Facts · Branching",
        endpoint="day10.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=11,
        title="Модель памяти",
        description="Краткосрочная, рабочая и долговременная",
        endpoint="day11.index",
        status="Готово",
        visible=True,
    ),
)


def get_day(number: int) -> AdventDay:
    return next(day for day in DAYS if day.number == number)
