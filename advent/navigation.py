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
        status="Оформлено",
        visible=True,
    ),
)


def get_day(number: int) -> AdventDay:
    return next(day for day in DAYS if day.number == number)
