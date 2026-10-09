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
    AdventDay(
        number=12,
        title="Персонализация",
        description="Профиль пользователя поверх памяти",
        endpoint="day12.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=13,
        title="Состояние задачи",
        description="planning → execution → validation → done",
        endpoint="day13.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=14,
        title="Инварианты",
        description="Правила, которые нельзя нарушать",
        endpoint="day14.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=15,
        title="Контролируемые переходы",
        description="Этап нельзя перепрыгнуть",
        endpoint="day15.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=16,
        title="Подключение MCP",
        description="Соединение и список инструментов",
        endpoint="day16.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=17,
        title="Первый инструмент MCP",
        description="Свой сервер и вызов из агента",
        endpoint="day17.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=18,
        title="Планировщик",
        description="Агент сам присылает сводку",
        endpoint="day18.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=19,
        title="Композиция MCP",
        description="Цепочка инструментов строит агент",
        endpoint="day19.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=20,
        title="Оркестрация MCP",
        description="Несколько серверов, один агент",
        endpoint="day20.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=21,
        title="Индексация документов",
        description="Чанки, эмбеддинги, индекс",
        endpoint="day21.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=22,
        title="Первый RAG-запрос",
        description="Ответ с базой и без неё",
        endpoint="day22.index",
        status="Готово",
        visible=True,
    ),
    AdventDay(
        number=23,
        title="Реранкинг и фильтрация",
        description="Второй этап после поиска",
        endpoint="day23.index",
        status="Готово",
        visible=True,
    ),
)


def get_day(number: int) -> AdventDay:
    return next(day for day in DAYS if day.number == number)
