from datetime import date, timedelta
from typing import Callable


WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def month_dates(year: int, month: int):
    """Возвращает первый день месяца и количество дней в месяце."""
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    return first, (next_first - first).days


def next_month(d: date) -> date:
    """Первый день следующего месяца."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)

    return date(d.year, d.month + 1, 1)


def previous_month(d: date) -> date:
    """Первый день предыдущего месяца."""
    if d.month == 1:
        return date(d.year - 1, 12, 1)

    return date(d.year, d.month - 1, 1)


def month_title(year: int, month: int) -> str:
    """Название месяца на русском."""
    return f"{MONTHS[month - 1]} {year}"


def month_matrix(year: int, month: int):
    """
    Возвращает календарь месяца по неделям.

    Каждый элемент недели содержит 7 значений:
    date или None для пустых ячеек.
    """
    first, days = month_dates(year, month)

    weeks = []
    week = [None] * first.weekday()

    for day_number in range(1, days + 1):
        week.append(date(year, month, day_number))

        if len(week) == 7:
            weeks.append(week)
            week = []

    if week:
        week.extend([None] * (7 - len(week)))
        weeks.append(week)

    return weeks


def day_status(
    car_id: str,
    current: date,
    today: date,
    available: Callable[[str, date, date], bool],
) -> str:
    """
    Определяет состояние конкретного дня.

    Возможные значения:
    - past  — прошедшая дата
    - busy  — автомобиль занят
    - free  — автомобиль свободен
    """
    if current < today:
        return "past"

    if not available(
        car_id,
        current,
        current + timedelta(days=1),
    ):
        return "busy"

    return "free"


def range_available(
    car_id: str,
    start: date,
    end: date,
    available: Callable[[str, date, date], bool],
) -> bool:
    """Проверяет, свободен ли автомобиль на весь выбранный период."""
    if end <= start:
        return False

    return available(car_id, start, end)


def calendar_days(
    car_id: str,
    year: int,
    month: int,
    today: date,
    available: Callable[[str, date, date], bool],
):
    """
    Возвращает список дней месяца со статусами.

    Пример:
    [
        (date(2026, 8, 1), "free"),
        (date(2026, 8, 2), "busy"),
        ...
    ]
    """
    first, days = month_dates(year, month)

    result = []

    for day_number in range(1, days + 1):
        current = date(year, month, day_number)

        status = day_status(
            car_id,
            current,
            today,
            available,
        )

        result.append((current, status))

    return result


def is_period_available(
    car_id: str,
    start: date,
    end: date,
    available: Callable[[str, date, date], bool],
) -> bool:
    """
    Финальная проверка периода перед созданием заявки.

    Это дополнительная защита от ситуации,
    когда автомобиль забронировали между выбором
    дат и отправкой заявки.
    """
    if start < date.today():
        return False

    if end <= start:
        return False

    return available(car_id, start, end)


def status_icon(status: str) -> str:
    """Иконка состояния даты."""
    if status == "busy":
        return "🔴"

    if status == "past":
        return "⚪"

    return "🟢"


def calendar_legend() -> str:
    """Легенда календаря."""
    return (
        "🟢 свободно\n"
        "🔴 занято\n"
        "⚪ прошедшая дата"
    )
