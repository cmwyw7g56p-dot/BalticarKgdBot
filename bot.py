import asyncio
import os
import secrets
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from aiohttp import web
import psycopg
from psycopg.rows import dict_row

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "0") or 0
)

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "Balticarkgdbot"
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не задан в Environment Variables Render."
    )

HOLD_MINUTES = int(
    os.getenv(
        "PENDING_HOLD_MINUTES",
        "60"
    )
)

TZ = ZoneInfo(
    os.getenv(
        "TIMEZONE",
        "Europe/Kaliningrad"
    )
)

PORT = int(
    os.getenv(
        "PORT",
        "10000"
    )
)

WEBHOOK_PATH = os.getenv(
    "WEBHOOK_PATH",
    "/telegram/webhook"
)

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    ""
).strip()


# ============================================================
# ВРЕМЯ АРЕНДЫ
# ============================================================

PICKUP_START_HOUR = int(
    os.getenv(
        "PICKUP_START_HOUR",
        "8"
    )
)

PICKUP_END_HOUR = int(
    os.getenv(
        "PICKUP_END_HOUR",
        "20"
    )
)

# Технический промежуток между арендами.
#
# Например:
#
# возврат:       06.09 17:00
# BUFFER_HOURS:  2
# следующая:     не раньше 06.09 19:00
#
# Если поставить 0:
# следующая аренда может начинаться сразу после возврата.

BUFFER_HOURS = int(
    os.getenv(
        "BUFFER_HOURS",
        "2"
    )
)


# ============================================================
# АВТОМОБИЛИ
# ============================================================

CARS = {
    "solaris21": {
        "name": "Hyundai Solaris 2021",
        "gear": "АКПП",
        "rates": (2700, 2600, 2500),
        "photos": [
            "photos/solaris_2021_1.png",
            "photos/solaris_2021_2.jpeg",
        ],
        "fuel": "Бензин",
        "seats": 5,
        "description": (
            "Надёжный и комфортный автомобиль "
            "для города и поездок по области."
        ),
    },

    "solaris20": {
        "name": "Hyundai Solaris 2020",
        "gear": "АКПП",
        "rates": (2700, 2600, 2500),
        "photos": [
            "photos/solaris_2020.jpeg"
        ],
        "fuel": "Бензин",
        "seats": 5,
        "description": (
            "Практичный автомобиль "
            "с автоматической коробкой передач."
        ),
    },

    "solaris17": {
        "name": "Hyundai Solaris 2017",
        "gear": "АКПП",
        "rates": (2400, 2300, 2200),
        "photos": [
            "photos/solaris_2017_1.webp",
            "photos/solaris_2017_2.jpeg",
        ],
        "fuel": "Бензин",
        "seats": 5,
        "description": (
            "Экономичный и удобный автомобиль "
            "для ежедневной аренды."
        ),
    },

    "i30": {
        "name": "Hyundai i30 2014",
        "gear": "МКПП",
        "rates": (2300, 2200, 2100),
        "photos": [
            "photos/i30_2014.png"
        ],
        "fuel": "Бензин",
        "seats": 5,
        "description": (
            "Компактный автомобиль "
            "с механической коробкой передач."
        ),
    },
}


# ============================================================
# FSM
# ============================================================

class Booking(StatesGroup):
    start = State()
    start_time = State()
    end = State()
    end_time = State()
    name = State()
    phone = State()
    comment = State()


# ============================================================
# DATABASE
# ============================================================

def db():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10
    )


def init_db():
    """
    Создание таблицы и миграция старой структуры.

    Старые записи:
        start_date / end_date

    Новые записи:
        start_at / end_at

    Для старых записей:
        получение 10:00
        возврат 17:00
    """

    con = db()

    try:
        with con.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bookings (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    car_id TEXT NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    comment TEXT,
                    total INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL,
                    expires_at TIMESTAMPTZ
                )
                """
            )

            cur.execute(
                """
                ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS start_at TIMESTAMPTZ
                """
            )

            cur.execute(
                """
                ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS end_at TIMESTAMPTZ
                """
            )

            cur.execute(
                """
                UPDATE bookings
                SET
                    start_at =
                        (
                            start_date::timestamp
                            + TIME '10:00'
                        ) AT TIME ZONE 'Europe/Kaliningrad',

                    end_at =
                        (
                            end_date::timestamp
                            + TIME '17:00'
                        ) AT TIME ZONE 'Europe/Kaliningrad'

                WHERE start_at IS NULL
                   OR end_at IS NULL
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_bookings_car_datetime
                ON bookings (
                    car_id,
                    start_at,
                    end_at,
                    status
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_bookings_car_dates
                ON bookings (
                    car_id,
                    start_date,
                    end_date,
                    status
                )
                """
            )

        con.commit()

    finally:
        con.close()


def cleanup_pending():
    """
    Переводит истёкшие pending-заявки в expired.
    """

    con = db()

    try:

        with con.cursor() as cur:

            cur.execute(
                """
                UPDATE bookings
                SET status='expired'
                WHERE status='pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < NOW()
                """
            )

        con.commit()

    finally:

        con.close()


# ============================================================
# DATETIME HELPERS
# ============================================================

def local_dt(
    d: date,
    t: time
):
    return datetime.combine(
        d,
        t,
        tzinfo=TZ
    )


def ensure_tz(dt):
    if dt is None:
        return None

    if dt.tzinfo is None:
        return dt.replace(
            tzinfo=TZ
        )

    return dt.astimezone(TZ)


def format_dt(dt):
    if dt is None:
        return "—"

    dt = ensure_tz(dt)

    return dt.strftime(
        "%d.%m.%Y %H:%M"
    )


def format_date_time(dt):
    return format_dt(dt)


def rental_hours(
    start_at,
    end_at
):
    return (
        end_at - start_at
    ).total_seconds() / 3600


def rental_days(
    start_at,
    end_at
):
    """
    Оплачиваемые сутки считаются вверх.

    02.09 10:00 → 06.09 10:00 = 4 суток

    02.09 10:00 → 06.09 17:00 = 5 суток
    """

    hours = rental_hours(
        start_at,
        end_at
    )

    days = int(
        (hours + 23.999999) // 24
    )

    return max(
        1,
        days
    )


# ============================================================
# OVERLAP
# ============================================================

def booking_overlaps(
    car_id,
    start_at,
    end_at,
    exclude_booking_id=None
):
    """
    Проверяет пересечение интервала.

    Учитывается BUFFER_HOURS.

    ВАЖНО:

    start_at < other_end
    И
    end_at > other_start

    Это позволяет делать последовательные аренды
    без ошибочной блокировки всего дня.
    """

    cleanup_pending()

    start_at = ensure_tz(start_at)
    end_at = ensure_tz(end_at)

    buffer_delta = timedelta(
        hours=BUFFER_HOURS
    )

    check_start = (
        start_at - buffer_delta
    )

    check_end = (
        end_at + buffer_delta
    )

    con = db()

    try:

        query = """
            SELECT
                id,
                status,
                start_at,
                end_at
            FROM bookings
            WHERE car_id=%s
              AND status IN ('pending', 'confirmed')
              AND start_at < %s
              AND end_at > %s
        """

        params = [
            car_id,
            check_end,
            check_start
        ]

        if exclude_booking_id is not None:

            query += """
                AND id <> %s
            """

            params.append(
                exclude_booking_id
            )

        query += """
            ORDER BY start_at
            LIMIT 1
        """

        with con.cursor() as cur:

            cur.execute(
                query,
                params
            )

            return cur.fetchone()

    finally:

        con.close()


def available(
    car_id,
    start_at,
    end_at,
    exclude_booking_id=None
):
    if end_at <= start_at:
        return False

    return (
        booking_overlaps(
            car_id,
            start_at,
            end_at,
            exclude_booking_id
        )
        is None
    )


# ============================================================
# БЫСТРАЯ ЗАГРУЗКА ЗАНЯТОСТИ МЕСЯЦА
# ============================================================

def get_month_bookings(
    car_id,
    year,
    month
):
    """
    Один запрос к Neon.

    НИКАКИХ запросов по каждому дню.
    """

    first = date(
        year,
        month,
        1
    )

    if month == 12:

        next_first = date(
            year + 1,
            1,
            1
        )

    else:

        next_first = date(
            year,
            month + 1,
            1
        )

    month_start = local_dt(
        first,
        time(0, 0)
    )

    month_end = local_dt(
        next_first,
        time(0, 0)
    )

    con = db()

    try:

        with con.cursor() as cur:

            rows = cur.execute(
                """
                SELECT
                    id,
                    status,
                    start_at,
                    end_at
                FROM bookings
                WHERE car_id=%s
                  AND status IN ('pending','confirmed')
                  AND start_at < %s
                  AND end_at > %s
                ORDER BY start_at
                """,
                (
                    car_id,
                    month_end,
                    month_start
                )
            ).fetchall()

            return rows

    finally:

        con.close()


# ============================================================
# DAY STATUS
# ============================================================

def day_status(
    current,
    bookings,
    today
):
    """
    Статус дня используется ТОЛЬКО для визуального
    календаря.

    Важно:
    наличие брони в этот день НЕ означает,
    что весь день заблокирован.

    Точное время проверяется следующим шагом.
    """

    if current < today:
        return "past"

    day_start = local_dt(
        current,
        time(0, 0)
    )

    day_end = (
        day_start
        + timedelta(days=1)
    )

    confirmed = False
    pending = False

    for row in bookings:

        start_at = ensure_tz(
            row["start_at"]
        )

        end_at = ensure_tz(
            row["end_at"]
        )

        if (
            start_at < day_end
            and end_at > day_start
        ):

            if row["status"] == "confirmed":
                confirmed = True

            elif row["status"] == "pending":
                pending = True

    if confirmed:
        return "confirmed"

    if pending:
        return "pending"

    return "free"


# ============================================================
# RATES
# ============================================================

def rate_for_days(
    car_id,
    days
):
    rates = CARS[car_id]["rates"]

    if days <= 3:
        return rates[0]

    if days <= 6:
        return rates[1]

    return rates[2]


def money(n):
    return (
        f"{n:,}".replace(",", " ")
        + " ₽"
    )


def status_label(status):

    return {
        "pending": "🟡 Ожидает подтверждения",
        "confirmed": "🟢 Подтверждена",
        "rejected": "🔴 Отклонена",
        "expired": "⚪ Истекла",
    }.get(
        status,
        status
    )


# ============================================================
# MAIN KEYBOARDS
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 Забронировать автомобиль",
                    callback_data="catalog"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Мои заявки",
                    callback_data="mybookings"
                )
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Условия",
                    callback_data="terms"
                ),
                InlineKeyboardButton(
                    text="📞 Связаться",
                    callback_data="contact"
                )
            ],
        ]
    )


def car_keyboard():

    rows = [
        [
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=f"car:{cid}"
            )
        ]
        for cid, car in CARS.items()
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Главное меню",
                callback_data="home"
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def car_actions_keyboard(cid):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Забронировать",
                    callback_data=f"pick:{cid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ К автомобилям",
                    callback_data="catalog"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="home"
                )
            ],
        ]
    )


def back_home_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="home"
                )
            ]
        ]
    )


# ============================================================
# TIME KEYBOARD
# ============================================================

def time_keyboard(
    car_id,
    selected_date,
    mode,
    start_at=None
):
    """
    mode:

        pickup
        return

    Время показывается с PICKUP_START_HOUR
    до PICKUP_END_HOUR.

    Проверка реальной занятости выполняется
    при нажатии на конкретное время.
    """

    rows = []

    if mode == "pickup":

        title = (
            f"🕐 <b>Время получения</b>\n\n"
            f"📅 {selected_date.strftime('%d.%m.%Y')}\n\n"
            "Выберите время:"
        )

    else:

        title = (
            f"🕐 <b>Время возврата</b>\n\n"
            f"📅 {selected_date.strftime('%d.%m.%Y')}\n\n"
            "Выберите время:"
        )

    times = []

    for hour in range(
        PICKUP_START_HOUR,
        PICKUP_END_HOUR + 1
    ):

        times.append(
            time(
                hour,
                0
            )
        )

    current_row = []

    for t in times:

        if mode == "pickup":

            dt = local_dt(
                selected_date,
                t
            )

            now = datetime.now(TZ)

            if dt <= now:
                continue

            callback = (
                f"picktime:"
                f"{car_id}:"
                f"{selected_date.isoformat()}:"
                f"{t.strftime('%H:%M')}"
            )

        else:

            if start_at is None:
                continue

            end_at = local_dt(
                selected_date,
                t
            )

            if end_at <= start_at:
                continue

            callback = (
                f"endtime:"
                f"{car_id}:"
                f"{selected_date.isoformat()}:"
                f"{t.strftime('%H:%M')}"
            )

        current_row.append(
            InlineKeyboardButton(
                text=t.strftime("%H:%M"),
                callback_data=callback
            )
        )

        if len(current_row) == 3:

            rows.append(
                current_row
            )

            current_row = []

    if current_row:
        rows.append(
            current_row
        )

    if mode == "pickup":

        rows.append(
            [
                InlineKeyboardButton(
                    text="◀️ Назад к календарю",
                    callback_data=(
                        f"backstart:"
                        f"{car_id}:"
                        f"{selected_date.isoformat()}"
                    )
                )
            ]
        )

    else:

        rows.append(
            [
                InlineKeyboardButton(
                    text="◀️ Назад к датам",
                    callback_data=(
                        f"backend:"
                        f"{car_id}:"
                        f"{start_at.date().isoformat()}"
                    )
                )
            ]
        )

    return (
        title,
        InlineKeyboardMarkup(
            inline_keyboard=rows
        )
    )


# ============================================================
# CLIENT CALENDAR
# ============================================================

def calendar_keyboard(
    car_id,
    year,
    month
):

    first = date(
        year,
        month,
        1
    )

    if month == 12:

        next_first = date(
            year + 1,
            1,
            1
        )

    else:

        next_first = date(
            year,
            month + 1,
            1
        )

    if month == 1:

        prev_first = date(
            year - 1,
            12,
            1
        )

    else:

        prev_first = date(
            year,
            month - 1,
            1
        )

    days = (
        next_first - first
    ).days

    today = datetime.now(TZ).date()

    bookings = get_month_bookings(
        car_id,
        year,
        month
    )

    months = [
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
    ]

    rows = [
        [
            InlineKeyboardButton(
                text=x,
                callback_data="noop"
            )
            for x in [
                "Пн",
                "Вт",
                "Ср",
                "Чт",
                "Пт",
                "Сб",
                "Вс",
            ]
        ]
    ]

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop"
        )
        for _ in range(first.weekday())
    ]

    for n in range(
        1,
        days + 1
    ):

        current = date(
            year,
            month,
            n
        )

        status = day_status(
            current,
            bookings,
            today
        )

        if status == "past":

            text = "⚪"
            callback_data = "noop"

        elif status == "confirmed":

            text = f"🔴{n}"

            callback_data = (
                f"day:"
                f"{car_id}:"
                f"{current.isoformat()}"
            )

        elif status == "pending":

            text = f"🟡{n}"

            callback_data = (
                f"day:"
                f"{car_id}:"
                f"{current.isoformat()}"
            )

        else:

            text = f"🟢{n}"

            callback_data = (
                f"day:"
                f"{car_id}:"
                f"{current.isoformat()}"
            )

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback_data
            )
        )

        if len(week) == 7:

            rows.append(
                week
            )

            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop"
                )
            )

        rows.append(
            week
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="‹",
                callback_data=(
                    f"month:"
                    f"{car_id}:"
                    f"{prev_first.isoformat()}"
                )
            ),
            InlineKeyboardButton(
                text=f"{months[month - 1]} {year}",
                callback_data="noop"
            ),
            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"month:"
                    f"{car_id}:"
                    f"{next_first.isoformat()}"
                )
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ К автомобилю",
                callback_data=f"car:{car_id}"
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# END DATE CALENDAR
# ============================================================

def end_calendar_keyboard(
    car_id,
    start_at,
    year,
    month
):
    """
    БЫСТРЫЙ календарь возврата.

    Здесь больше НЕТ:

        available()
        booking_overlaps()

    для каждого часа каждого дня.

    Поэтому календарь больше не создаёт десятки
    соединений с Neon.

    День подсвечивается:

        🟢 — дата потенциально доступна
        🔴 — нет подходящего времени
        ⚪ — раньше/равна получению

    Точная проверка выполняется при выборе
    конкретного времени возврата.
    """

    start_at = ensure_tz(
        start_at
    )

    first = date(
        year,
        month,
        1
    )

    if month == 12:

        next_first = date(
            year + 1,
            1,
            1
        )

    else:

        next_first = date(
            year,
            month + 1,
            1
        )

    if month == 1:

        prev_first = date(
            year - 1,
            12,
            1
        )

    else:

        prev_first = date(
            year,
            month - 1,
            1
        )

    days = (
        next_first - first
    ).days

    months = [
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
    ]

    # Загружаем брони месяца ОДНИМ запросом.

    bookings = get_month_bookings(
        car_id,
        year,
        month
    )

    rows = [
        [
            InlineKeyboardButton(
                text=x,
                callback_data="noop"
            )
            for x in [
                "Пн",
                "Вт",
                "Ср",
                "Чт",
                "Пт",
                "Сб",
                "Вс",
            ]
        ]
    ]

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop"
        )
        for _ in range(first.weekday())
    ]

    for n in range(
        1,
        days + 1
    ):

        current = date(
            year,
            month,
            n
        )

        # Дата возврата должна быть ПОСЛЕ даты получения.

        if current <= start_at.date():

            text = "⚪"
            callback_data = "noop"

        else:

            # Проверяем только данные уже загруженного
            # месяца, без запросов к БД.

            day_start = local_dt(
                current,
                time(0, 0)
            )

            day_end = (
                day_start
                + timedelta(days=1)
            )

            has_booking = False

            for row in bookings:

                row_start = ensure_tz(
                    row["start_at"]
                )

                row_end = ensure_tz(
                    row["end_at"]
                )

                if (
                    row_start < day_end
                    and row_end > day_start
                ):

                    has_booking = True
                    break

            # Даже если в день есть бронь,
            # день оставляем кликабельным.
            #
            # Почему?
            #
            # Например:
            #
            # старая бронь:
            # 05.09 10:00 → 06.09 17:00
            #
            # новая аренда:
            # 06.09 19:00 → ...
            #
            # 06.09 нельзя помечать полностью занятым.
            #
            # Точное время проверяется дальше.

            if has_booking:

                text = f"🟡{n}"

            else:

                text = f"🟢{n}"

            callback_data = (
                f"end:"
                f"{car_id}:"
                f"{current.isoformat()}"
            )

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback_data
            )
        )

        if len(week) == 7:

            rows.append(
                week
            )

            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop"
                )
            )

        rows.append(
            week
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="‹",
                callback_data=(
                    f"endmonth:"
                    f"{car_id}:"
                    f"{start_at.isoformat()}:"
                    f"{prev_first.isoformat()}"
                )
            ),
            InlineKeyboardButton(
                text=f"{months[month - 1]} {year}",
                callback_data="noop"
            ),
            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"endmonth:"
                    f"{car_id}:"
                    f"{start_at.isoformat()}:"
                    f"{next_first.isoformat()}"
                )
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=(
                    f"backstarttime:"
                    f"{car_id}:"
                    f"{start_at.date().isoformat()}"
                )
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# ADMIN KEYBOARDS
# ============================================================

def admin_buttons(bid):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"confirm:{bid}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{bid}"
                ),
            ]
        ]
    )


def admin_panel_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔔 Новые заявки",
                    callback_data="admin:new"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Календарь занятости",
                    callback_data="admin:calendar"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Все бронирования",
                    callback_data="admin:bookings"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚗 Автомобили",
                    callback_data="admin:cars"
                )
            ],
        ]
    )


def admin_back_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ В админ-панель",
                    callback_data="admin:back"
                )
            ]
        ]
    )


# ============================================================
# ADMIN CALENDAR
# ============================================================

def admin_busy_calendar_keyboard(
    car_id,
    year,
    month
):

    first = date(
        year,
        month,
        1
    )

    if month == 12:

        next_first = date(
            year + 1,
            1,
            1
        )

    else:

        next_first = date(
            year,
            month + 1,
            1
        )

    if month == 1:

        previous_first = date(
            year - 1,
            12,
            1
        )

    else:

        previous_first = date(
            year,
            month - 1,
            1
        )

    days = (
        next_first - first
    ).days

    today = datetime.now(TZ).date()

    bookings = get_month_bookings(
        car_id,
        year,
        month
    )

    months = [
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
    ]

    rows = [
        [
            InlineKeyboardButton(
                text=x,
                callback_data="noop"
            )
            for x in [
                "Пн",
                "Вт",
                "Ср",
                "Чт",
                "Пт",
                "Сб",
                "Вс",
            ]
        ]
    ]

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop"
        )
        for _ in range(first.weekday())
    ]

    for n in range(
        1,
        days + 1
    ):

        current = date(
            year,
            month,
            n
        )

        status = day_status(
            current,
            bookings,
            today
        )

        if status == "confirmed":

            text = f"🔴{n}"

        elif status == "pending":

            text = f"🟡{n}"

        elif status == "past":

            text = "⚪"

        else:

            text = f"🟢{n}"

        callback_data = (
            f"adminday:"
            f"{car_id}:"
            f"{current.isoformat()}"
            if status != "past"
            else "noop"
        )

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback_data
            )
        )

        if len(week) == 7:

            rows.append(
                week
            )

            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop"
                )
            )

        rows.append(
            week
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="‹",
                callback_data=(
                    f"adminmonth:"
                    f"{car_id}:"
                    f"{previous_first.isoformat()}"
                )
            ),
            InlineKeyboardButton(
                text=f"{months[month - 1]} {year}",
                callback_data="noop"
            ),
            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"adminmonth:"
                    f"{car_id}:"
                    f"{next_first.isoformat()}"
                )
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ К автомобилям",
                callback_data="admin:calendar"
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# CAR TEXT
# ============================================================

def car_text(cid):

    car = CARS[cid]

    return (
        f"<b>{car['name']}</b>\n\n"
        f"⚙️ Коробка: <b>{car['gear']}</b>\n"
        f"⛽ Топливо: <b>{car['fuel']}</b>\n"
        f"👥 Мест: <b>{car['seats']}</b>\n\n"
        f"{car['description']}\n\n"
        f"💰 1–3 суток: "
        f"<b>{money(car['rates'][0])}/сутки</b>\n"
        f"💰 4–6 суток: "
        f"<b>{money(car['rates'][1])}/сутки</b>\n"
        f"💰 7+ суток: "
        f"<b>{money(car['rates'][2])}/сутки</b>\n\n"
        f"🕐 Выдача/возврат: "
        f"<b>{PICKUP_START_HOUR:02d}:00–"
        f"{PICKUP_END_HOUR:02d}:00</b>\n"
        f"🔧 Технический интервал между арендами: "
        f"<b>{BUFFER_HOURS} ч.</b>"
    )


async def send_car(
    bot,
    chat_id,
    cid
):

    car = CARS[cid]

    for photo in car["photos"]:

        if os.path.exists(photo):

            await bot.send_photo(
                chat_id,
                FSInputFile(photo)
            )

    await bot.send_message(
        chat_id,
        car_text(cid),
        reply_markup=car_actions_keyboard(cid)
    )


# ============================================================
# CLIENT
# ============================================================

async def start_handler(
    message: Message,
    state: FSMContext
):

    await state.clear()

    await message.answer(
        "🚗 <b>Balticar</b>\n\n"
        "Аренда автомобилей в Калининграде.\n\n"
        "Выберите нужное действие:",
        reply_markup=main_keyboard()
    )


async def home(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.clear()

    await callback.message.edit_text(
        "🚗 <b>Balticar</b>\n\n"
        "Аренда автомобилей в Калининграде.\n\n"
        "Выберите нужное действие:",
        reply_markup=main_keyboard()
    )

    await callback.answer()


async def id_handler(
    message: Message
):

    await message.answer(
        f"Ваш Telegram ID: "
        f"<code>{message.from_user.id}</code>"
    )


async def catalog(
    callback: CallbackQuery
):

    await callback.message.edit_text(
        "🚗 <b>Автомобили Balticar</b>\n\n"
        "Выберите автомобиль, чтобы посмотреть "
        "фото, характеристики и стоимость:",
        reply_markup=car_keyboard()
    )

    await callback.answer()


async def car_selected(
    callback: CallbackQuery
):

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    # Сразу подтверждаем нажатие кнопки.
    # Нельзя делать долгие операции до callback.answer().
    await callback.answer()

    await send_car(
        callback.bot,
        callback.message.chat.id,
        cid
    )


async def pick_dates(
    callback: CallbackQuery
):

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    today = datetime.now(TZ).date()

    await callback.message.answer(
        f"📅 <b>{CARS[cid]['name']}</b>\n\n"
        "Выберите дату получения.\n\n"
        "🟢 свободно\n"
        "🟡 есть заявка\n"
        "🔴 подтверждено\n"
        "⚪ прошедшая дата",
        reply_markup=calendar_keyboard(
            cid,
            today.year,
            today.month
        )
    )

    await callback.answer()


async def month(
    callback: CallbackQuery
):

    _, cid, iso = callback.data.split(":")

    d = date.fromisoformat(
        iso
    )

    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(
            cid,
            d.year,
            d.month
        )
    )

    await callback.answer()


# ============================================================
# START DATE
# ============================================================

async def start_day(
    callback: CallbackQuery,
    state: FSMContext
):

    _, cid, iso = callback.data.split(":")

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    start_d = date.fromisoformat(
        iso
    )

    today = datetime.now(TZ).date()

    if start_d < today:

        await callback.answer(
            "Нельзя выбрать прошедшую дату.",
            show_alert=True
        )

        return

    await state.update_data(
        car_id=cid,
        start_date=iso
    )

    await state.set_state(
        Booking.start_time
    )

    title, keyboard = time_keyboard(
        cid,
        start_d,
        "pickup"
    )

    await callback.message.answer(
        title,
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# BACK TO START CALENDAR
# ============================================================

async def backstart(
    callback: CallbackQuery
):

    _, cid, iso = callback.data.split(":")

    d = date.fromisoformat(
        iso
    )

    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(
            cid,
            d.year,
            d.month
        )
    )

    await callback.answer()


# ============================================================
# START TIME
# ============================================================

async def pick_time(
    callback: CallbackQuery,
    state: FSMContext
):

    _, cid, date_iso, time_text = (
        callback.data.split(":", 3)
    )

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    start_d = date.fromisoformat(
        date_iso
    )

    hour, minute = map(
        int,
        time_text.split(":")
    )

    start_at = local_dt(
        start_d,
        time(hour, minute)
    )

    now = datetime.now(TZ)

    if start_at <= now:

        await callback.answer(
            "Это время уже прошло.",
            show_alert=True
        )

        return

    # ========================================================
    # ВАЖНО
    #
    # Не создаём фиктивную бронь на 1 час.
    #
    # Проверяем только, что выбранный момент
    # не находится внутри уже существующей аренды
    # с учётом BUFFER_HOURS.
    #
    # Для этого используем небольшой тестовый интервал.
    # ========================================================

    test_end = (
        start_at
        + timedelta(
            minutes=1
        )
    )

    if not available(
        cid,
        start_at,
        test_end
    ):

        await callback.answer(
            "❌ В это время автомобиль уже занят "
            "или ещё действует технический интервал.",
            show_alert=True
        )

        return

    await state.update_data(
        car_id=cid,
        start_at=start_at.isoformat()
    )

    await state.set_state(
        Booking.end
    )

    await callback.message.answer(
        f"📅 <b>Получение автомобиля</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n"
        f"🕐 <b>{format_date_time(start_at)}</b>\n\n"
        "Теперь выберите дату возврата:",
        reply_markup=end_calendar_keyboard(
            cid,
            start_at,
            start_at.year,
            start_at.month
        )
    )

    await callback.answer()


# ============================================================
# END MONTH
# ============================================================

async def endmonth(
    callback: CallbackQuery
):

    _, cid, start_iso, iso = (
        callback.data.split(":", 3)
    )

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    start_at = datetime.fromisoformat(
        start_iso
    )

    start_at = ensure_tz(
        start_at
    )

    d = date.fromisoformat(
        iso
    )

    await callback.message.edit_reply_markup(
        reply_markup=end_calendar_keyboard(
            cid,
            start_at,
            d.year,
            d.month
        )
    )

    await callback.answer()


# ============================================================
# END DATE
# ============================================================

async def end_day(
    callback: CallbackQuery,
    state: FSMContext
):

    _, cid, end_iso = callback.data.split(":")

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    data = await state.get_data()

    if not data.get("start_at"):

        await state.clear()

        await callback.answer(
            "Сессия устарела. "
            "Начните бронирование заново.",
            show_alert=True
        )

        return

    start_at = datetime.fromisoformat(
        data["start_at"]
    )

    start_at = ensure_tz(
        start_at
    )

    end_d = date.fromisoformat(
        end_iso
    )

    if end_d <= start_at.date():

        await callback.answer(
            "Дата возврата должна быть позже даты получения.",
            show_alert=True
        )

        return

    title, keyboard = time_keyboard(
        cid,
        end_d,
        "return",
        start_at
    )

    await state.update_data(
        end_date=end_iso
    )

    await state.set_state(
        Booking.end_time
    )

    await callback.message.answer(
        title,
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# BACK TO END CALENDAR
# ============================================================

async def backend(
    callback: CallbackQuery
):

    _, cid, start_iso = callback.data.split(":")

    start_d = date.fromisoformat(
        start_iso
    )

    start_at = local_dt(
        start_d,
        time(PICKUP_START_HOUR, 0)
    )

    await callback.message.edit_reply_markup(
        reply_markup=end_calendar_keyboard(
            cid,
            start_at,
            start_d.year,
            start_d.month
        )
    )

    await callback.answer()


async def backstarttime(
    callback: CallbackQuery
):

    _, cid, start_iso = callback.data.split(":")

    start_d = date.fromisoformat(
        start_iso
    )

    title, keyboard = time_keyboard(
        cid,
        start_d,
        "pickup"
    )

    await callback.message.edit_reply_markup(
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# END TIME
# ============================================================

async def end_time_handler(
    callback: CallbackQuery,
    state: FSMContext
):
    # END_TIME_HANDLER_PRESENT

    _, cid, end_date_iso, time_text = (
        callback.data.split(":", 3)
    )

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    data = await state.get_data()

    if not data.get("start_at"):

        await state.clear()

        await callback.answer(
            "Сессия устарела.",
            show_alert=True
        )

        return

    start_at = datetime.fromisoformat(
        data["start_at"]
    )

    start_at = ensure_tz(
        start_at
    )

    end_d = date.fromisoformat(
        end_date_iso
    )

    hour, minute = map(
        int,
        time_text.split(":")
    )

    end_at = local_dt(
        end_d,
        time(hour, minute)
    )

    if end_at <= start_at:

        await callback.answer(
            "Возврат должен быть позже получения.",
            show_alert=True
        )

        return

    # ========================================================
    # ФИНАЛЬНАЯ ПРОВЕРКА
    #
    # Только здесь выполняется обращение к БД.
    #
    # Именно этот запрос определяет,
    # действительно ли выбранный интервал свободен.
    # ========================================================

    if not available(
        cid,
        start_at,
        end_at
    ):

        await callback.answer(
            "❌ В этот период автомобиль уже занят "
            "или между арендами недостаточно времени.",
            show_alert=True
        )

        return

    days = rental_days(
        start_at,
        end_at
    )

    total = (
        days
        * rate_for_days(
            cid,
            days
        )
    )

    await state.update_data(
        end_at=end_at.isoformat(),
        days=days,
        total=total
    )

    await state.set_state(
        Booking.name
    )

    await callback.message.answer(
        f"✅ <b>Период выбран</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n"
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        f"📅 Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"
        f"⏱ Продолжительность: "
        f"<b>{days} суток</b>\n"
        f"💰 Стоимость: "
        f"<b>{money(total)}</b>\n\n"
        "Введите ваше имя:"
    )

    await callback.answer()


# ============================================================
# NAME
# ============================================================

async def name_handler(
    message: Message,
    state: FSMContext
):

    text = (
        message.text or ""
    ).strip()

    if len(text) < 2:

        await message.answer(
            "Пожалуйста, введите имя."
        )

        return

    await state.update_data(
        name=text
    )

    await state.set_state(
        Booking.phone
    )

    await message.answer(
        "📞 Введите номер телефона:"
    )


# ============================================================
# PHONE
# ============================================================

async def phone_handler(
    message: Message,
    state: FSMContext
):

    phone = (
        message.text or ""
    ).strip()

    if len(phone) < 7:

        await message.answer(
            "Похоже, номер слишком короткий. "
            "Введите телефон ещё раз."
        )

        return

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        Booking.comment
    )

    await message.answer(
        "📝 Добавьте комментарий к заказу.\n"
        "Если комментарий не нужен — "
        "отправьте «-»."
    )


# ============================================================
# CREATE BOOKING
# ============================================================

async def comment_handler(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    if (
        not data.get("car_id")
        or not data.get("start_at")
        or not data.get("end_at")
    ):

        await state.clear()

        await message.answer(
            "Сессия бронирования устарела.\n\n"
            "Начните заново:",
            reply_markup=main_keyboard()
        )

        return

    comment_text = (
        message.text or ""
    ).strip()

    comment = (
        ""
        if comment_text == "-"
        else comment_text
    )

    cid = data["car_id"]

    start_at = datetime.fromisoformat(
        data["start_at"]
    )

    end_at = datetime.fromisoformat(
        data["end_at"]
    )

    start_at = ensure_tz(
        start_at
    )

    end_at = ensure_tz(
        end_at
    )

    days = rental_days(
        start_at,
        end_at
    )

    total = (
        days
        * rate_for_days(
            cid,
            days
        )
    )

    expires = (
        datetime.now(TZ)
        + timedelta(
            minutes=HOLD_MINUTES
        )
    )

    created_at = datetime.now(TZ)

    # ========================================================
    # АТОМАРНОЕ СОЗДАНИЕ БРОНИ
    # ========================================================

    con = db()

    try:

        with con.cursor() as cur:

            cur.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtext('balticar-bookings')
                )
                """
            )

            # Истёкшие pending.

            cur.execute(
                """
                UPDATE bookings
                SET status='expired'
                WHERE status='pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < NOW()
                """
            )

            buffer_delta = timedelta(
                hours=BUFFER_HOURS
            )

            check_start = (
                start_at - buffer_delta
            )

            check_end = (
                end_at + buffer_delta
            )

            overlap = cur.execute(
                """
                SELECT id, status
                FROM bookings
                WHERE car_id=%s
                  AND status IN ('pending','confirmed')
                  AND start_at < %s
                  AND end_at > %s
                LIMIT 1
                """,
                (
                    cid,
                    check_end,
                    check_start
                )
            ).fetchone()

            if overlap:

                con.rollback()

                await state.clear()

                await message.answer(
                    "❌ <b>Автомобиль уже занят</b>\n\n"
                    "Пока вы заполняли данные, "
                    "другой клиент занял выбранное "
                    "время.\n\n"
                    "Пожалуйста, выберите другие "
                    "дату или время.",
                    reply_markup=main_keyboard()
                )

                return

            start_date = start_at.date()
            end_date = end_at.date()

            row = cur.execute(
                """
                INSERT INTO bookings (
                    user_id,
                    username,
                    car_id,
                    start_date,
                    end_date,
                    start_at,
                    end_at,
                    name,
                    phone,
                    comment,
                    total,
                    status,
                    created_at,
                    expires_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    'pending', %s, %s
                )
                RETURNING id
                """,
                (
                    message.from_user.id,
                    message.from_user.username or "",
                    cid,
                    start_date,
                    end_date,
                    start_at,
                    end_at,
                    data["name"],
                    data["phone"],
                    comment,
                    total,
                    created_at,
                    expires
                )
            ).fetchone()

            bid = row["id"]

        con.commit()

    except Exception:

        con.rollback()
        raise

    finally:

        con.close()

    await state.clear()

    # ========================================================
    # CLIENT
    # ========================================================

    await message.answer(
        f"📩 <b>Заявка №{bid} отправлена!</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n\n"
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        f"📅 Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"
        f"⏱ <b>{days} суток</b>\n"
        f"💰 <b>{money(total)}</b>\n\n"
        f"⏳ Выбранное время удерживается "
        f"до {expires.strftime('%d.%m.%Y %H:%M')}.\n\n"
        "Мы свяжемся с вами после подтверждения заявки.",
        reply_markup=main_keyboard()
    )

    # ========================================================
    # ADMIN
    # ========================================================

    if ADMIN_ID:

        uname = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else "без username"
        )

        text = (
            f"🔔 <b>Новая заявка №{bid}</b>\n\n"
            f"🚗 {CARS[cid]['name']} "
            f"({CARS[cid]['gear']})\n\n"
            f"📅 Получение:\n"
            f"<b>{format_date_time(start_at)}</b>\n\n"
            f"📅 Возврат:\n"
            f"<b>{format_date_time(end_at)}</b>\n\n"
            f"⏱ {days} суток\n"
            f"💰 <b>{money(total)}</b>\n"
            f"👤 {data['name']}\n"
            f"📞 {data['phone']}\n"
            f"Telegram: {uname}\n"
            f"📝 {comment or '—'}\n\n"
            f"⏳ Ожидает подтверждения до "
            f"{expires.strftime('%d.%m.%Y %H:%M')}"
        )

        await message.bot.send_message(
            ADMIN_ID,
            text,
            reply_markup=admin_buttons(bid)
        )


# ============================================================
# MY BOOKINGS
# ============================================================

async def mybookings(
    callback: CallbackQuery
):

    cleanup_pending()

    con = db()

    try:

        with con.cursor() as cur:

            rows = cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE user_id=%s
                ORDER BY id DESC
                LIMIT 10
                """,
                (
                    callback.from_user.id,
                )
            ).fetchall()

    finally:

        con.close()

    if not rows:

        text = (
            "📋 <b>Мои заявки</b>\n\n"
            "У вас пока нет заявок."
        )

    else:

        out = [
            "📋 <b>Мои заявки</b>"
        ]

        for row in rows:

            start_at = row["start_at"]
            end_at = row["end_at"]

            if start_at is None:

                start_at = local_dt(
                    row["start_date"],
                    time(10, 0)
                )

            if end_at is None:

                end_at = local_dt(
                    row["end_date"],
                    time(17, 0)
                )

            start_at = ensure_tz(
                start_at
            )

            end_at = ensure_tz(
                end_at
            )

            out.append(
                f"\n<b>№{row['id']} — "
                f"{CARS[row['car_id']]['name']}</b>\n"
                f"📅 {format_date_time(start_at)}\n"
                f"↩️ {format_date_time(end_at)}\n"
                f"⏱ {rental_days(start_at, end_at)} суток\n"
                f"💰 {money(row['total'])}\n"
                f"{status_label(row['status'])}"
            )

        text = "\n".join(out)

    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚗 Забронировать автомобиль",
                        callback_data="catalog"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Главное меню",
                        callback_data="home"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# TERMS
# ============================================================

async def terms(
    callback: CallbackQuery
):

    await callback.message.answer(
        "ℹ️ <b>Условия аренды</b>\n\n"
        "• Вы выбираете дату и время получения "
        "автомобиля.\n"
        "• Затем выбираете дату и время возврата.\n"
        "• Цена рассчитывается автоматически "
        "по продолжительности аренды.\n"
        "• Выбранный период временно удерживается "
        "до подтверждения заявки.\n"
        f"• Между возвратом и следующей выдачей "
        f"предусмотрен технический интервал "
        f"<b>{BUFFER_HOURS} ч.</b>.\n"
        "• Если заявка не подтверждена в установленный "
        "срок, удержание автоматически снимается.",
        reply_markup=back_home_keyboard()
    )

    await callback.answer()


# ============================================================
# CONTACT
# ============================================================

async def contact(
    callback: CallbackQuery
):

    await callback.message.answer(
        "📞 <b>Связаться с Balticar</b>\n\n"
        "Если у вас есть вопрос по автомобилю, "
        "датам или условиям аренды, "
        "напишите менеджеру.\n\n"
        "Также можно оформить заявку прямо здесь.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚗 Забронировать автомобиль",
                        callback_data="catalog"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Главное меню",
                        callback_data="home"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_panel(
    message: Message
):

    if message.from_user.id != ADMIN_ID:

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    await message.answer(
        "👨‍💼 <b>Админ-панель Balticar</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_panel_keyboard()
    )


async def admin_back(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.message.edit_text(
        "👨‍💼 <b>Админ-панель Balticar</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_panel_keyboard()
    )

    await callback.answer()


# ============================================================
# ADMIN CALENDAR
# ============================================================

async def admin_calendar(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=f"admincar:{cid}"
            )
        ]
        for cid, car in CARS.items()
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="admin:back"
            )
        ]
    )

    await callback.message.edit_text(
        "📅 <b>Календарь занятости</b>\n\n"
        "Выберите автомобиль:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        )
    )

    await callback.answer()


async def admin_car_calendar(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    today = datetime.now(TZ).date()

    await callback.message.edit_text(
        "📅 <b>Календарь занятости</b>\n\n"
        f"🚗 <b>{CARS[cid]['name']}</b>\n\n"
        "🟢 свободно\n"
        "🟡 ожидает подтверждения\n"
        "🔴 подтверждено\n"
        "⚪ прошедшая дата\n\n"
        f"Буфер между арендами: "
        f"<b>{BUFFER_HOURS} ч.</b>",
        reply_markup=admin_busy_calendar_keyboard(
            cid,
            today.year,
            today.month
        )
    )

    await callback.answer()


async def admin_month(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    _, cid, iso = callback.data.split(":")

    d = date.fromisoformat(
        iso
    )

    await callback.message.edit_reply_markup(
        reply_markup=admin_busy_calendar_keyboard(
            cid,
            d.year,
            d.month
        )
    )

    await callback.answer()


# ============================================================
# ADMIN DAY
# ============================================================

async def admin_day(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    _, car_id, iso = callback.data.split(":")

    selected = date.fromisoformat(
        iso
    )

    day_start = local_dt(
        selected,
        time(0, 0)
    )

    day_end = (
        day_start
        + timedelta(days=1)
    )

    con = db()

    try:

        with con.cursor() as cur:

            rows = cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE car_id=%s
                  AND status IN ('pending','confirmed')
                  AND start_at < %s
                  AND end_at > %s
                ORDER BY start_at
                """,
                (
                    car_id,
                    day_end,
                    day_start
                )
            ).fetchall()

    finally:

        con.close()

    if not rows:

        await callback.answer(
            "В этот день бронирований нет.",
            show_alert=True
        )

        return

    out = [
        f"📅 <b>{selected.strftime('%d.%m.%Y')}</b>",
        f"🚗 <b>{CARS[car_id]['name']}</b>",
        ""
    ]

    for row in rows:

        start_at = ensure_tz(
            row["start_at"]
        )

        end_at = ensure_tz(
            row["end_at"]
        )

        out.append(
            f"📋 <b>Заявка №{row['id']}</b>\n"
            f"{status_label(row['status'])}\n"
            f"🕐 {format_date_time(start_at)}\n"
            f"↩️ {format_date_time(end_at)}\n"
            f"👤 {row['name']}\n"
            f"📞 {row['phone']}\n"
            f"💰 {money(row['total'])}\n"
        )

    await callback.message.answer(
        "\n".join(out)
    )

    await callback.answer()


# ============================================================
# ADMIN NEW
# ============================================================

async def admin_new(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    cleanup_pending()

    con = db()

    try:

        with con.cursor() as cur:

            rows = cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE status='pending'
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()

    finally:

        con.close()

    if not rows:

        await callback.message.edit_text(
            "🔔 <b>Новые заявки</b>\n\n"
            "Новых заявок нет.",
            reply_markup=admin_back_keyboard()
        )

        await callback.answer()

        return

    keyboard = [
        [
            InlineKeyboardButton(
                text=(
                    f"№{row['id']} — "
                    f"{CARS[row['car_id']]['name']}"
                ),
                callback_data=(
                    f"adminbooking:{row['id']}"
                )
            )
        ]
        for row in rows
    ]

    keyboard.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="admin:back"
            )
        ]
    )

    await callback.message.edit_text(
        "🔔 <b>Новые заявки</b>\n\n"
        f"Найдено заявок: <b>{len(rows)}</b>\n\n"
        "Выберите заявку:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

    await callback.answer()


# ============================================================
# ADMIN BOOKING
# ============================================================

async def admin_booking(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    bid = int(
        callback.data.split(
            ":",
            1
        )[1]
    )

    con = db()

    try:

        with con.cursor() as cur:

            row = cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE id=%s
                """,
                (bid,)
            ).fetchone()

    finally:

        con.close()

    if not row:

        await callback.answer(
            "Заявка не найдена.",
            show_alert=True
        )

        return

    start_at = row["start_at"]
    end_at = row["end_at"]

    if start_at is None:

        start_at = local_dt(
            row["start_date"],
            time(10, 0)
        )

    if end_at is None:

        end_at = local_dt(
            row["end_date"],
            time(17, 0)
        )

    start_at = ensure_tz(
        start_at
    )

    end_at = ensure_tz(
        end_at
    )

    username = (
        f"@{row['username']}"
        if row["username"]
        else "нет username"
    )

    text = (
        f"📋 <b>Заявка №{bid}</b>\n\n"
        f"🚗 <b>{CARS[row['car_id']]['name']}</b>\n\n"
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        f"↩️ Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"
        f"⏱ {rental_days(start_at, end_at)} суток\n"
        f"💰 <b>{money(row['total'])}</b>\n\n"
        f"👤 {row['name']}\n"
        f"📞 {row['phone']}\n"
        f"Telegram: {username}\n"
        f"📝 {row['comment'] or '—'}\n\n"
        f"Статус: {status_label(row['status'])}"
    )

    await callback.message.edit_text(
        text,
        reply_markup=(
            admin_buttons(bid)
            if row["status"] == "pending"
            else admin_back_keyboard()
        )
    )

    await callback.answer()


# ============================================================
# ADMIN ALL BOOKINGS
# ============================================================

async def admin_bookings(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    cleanup_pending()

    con = db()

    try:

        with con.cursor() as cur:

            rows = cur.execute(
                """
                SELECT *
                FROM bookings
                ORDER BY id DESC
                LIMIT 30
                """
            ).fetchall()

    finally:

        con.close()

    if not rows:

        await callback.message.edit_text(
            "📋 <b>Все бронирования</b>\n\n"
            "Бронирований пока нет.",
            reply_markup=admin_back_keyboard()
        )

        await callback.answer()

        return

    keyboard = [
        [
            InlineKeyboardButton(
                text=(
                    f"№{row['id']} • "
                    f"{status_label(row['status'])[:2]} • "
                    f"{CARS[row['car_id']]['name'][:22]}"
                ),
                callback_data=(
                    f"adminbooking:{row['id']}"
                )
            )
        ]
        for row in rows
    ]

    keyboard.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="admin:back"
            )
        ]
    )

    await callback.message.edit_text(
        "📋 <b>Все бронирования</b>\n\n"
        f"Показаны последние {len(rows)} заявок.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

    await callback.answer()


# ============================================================
# ADMIN CARS
# ============================================================

async def admin_cars(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=f"admincarinfo:{cid}"
            )
        ]
        for cid, car in CARS.items()
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="admin:back"
            )
        ]
    )

    await callback.message.edit_text(
        "🚗 <b>Автомобили</b>\n\n"
        "Выберите автомобиль:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        )
    )

    await callback.answer()


async def admin_car_info(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    await callback.message.edit_text(
        "🚗 <b>Автомобиль</b>\n\n"
        + car_text(cid),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📅 Календарь занятости",
                        callback_data=f"admincar:{cid}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ К автомобилям",
                        callback_data="admin:cars"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# CONFIRM / REJECT
# ============================================================

async def admin_action(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    action, bid_s = callback.data.split(
        ":"
    )

    bid = int(bid_s)

    con = db()

    try:

        with con.cursor() as cur:

            cur.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtext('balticar-bookings')
                )
                """
            )

            # ==================================================
            # Истёкшие заявки
            # ==================================================

            cur.execute(
                """
                UPDATE bookings
                SET status='expired'
                WHERE status='pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < NOW()
                """
            )

            # ==================================================
            # Получаем заявку под блокировкой
            # ==================================================

            row = cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE id=%s
                FOR UPDATE
                """,
                (bid,)
            ).fetchone()

            if not row:

                con.rollback()

                await callback.answer(
                    "Заявка не найдена.",
                    show_alert=True
                )

                return

            if row["status"] != "pending":

                con.rollback()

                await callback.answer(
                    f"Заявка уже имеет статус: "
                    f"{status_label(row['status'])}",
                    show_alert=True
                )

                return

            start_at = row["start_at"]
            end_at = row["end_at"]

            if start_at is None:

                start_at = local_dt(
                    row["start_date"],
                    time(10, 0)
                )

            if end_at is None:

                end_at = local_dt(
                    row["end_date"],
                    time(17, 0)
                )

            start_at = ensure_tz(
                start_at
            )

            end_at = ensure_tz(
                end_at
            )

            # ==================================================
            # CONFIRM
            # ==================================================

            if action == "confirm":

                buffer_delta = timedelta(
                    hours=BUFFER_HOURS
                )

                other = cur.execute(
                    """
                    SELECT id
                    FROM bookings
                    WHERE car_id=%s
                      AND id<>%s
                      AND status IN ('pending','confirmed')
                      AND start_at < %s
                      AND end_at > %s
                    LIMIT 1
                    """,
                    (
                        row["car_id"],
                        bid,
                        end_at + buffer_delta,
                        start_at - buffer_delta
                    )
                ).fetchone()

                if other:

                    cur.execute(
                        """
                        UPDATE bookings
                        SET status='rejected'
                        WHERE id=%s
                        """,
                        (bid,)
                    )

                    con.commit()

                    await callback.message.edit_reply_markup(
                        reply_markup=None
                    )

                    await callback.bot.send_message(
                        row["user_id"],
                        f"❌ <b>Заявка №{bid} отклонена</b>\n\n"
                        "Выбранное время уже занято "
                        "или недостаточно времени "
                        "между арендами.",
                        reply_markup=main_keyboard()
                    )

                    await callback.answer(
                        "Время уже занято.",
                        show_alert=True
                    )

                    return

                cur.execute(
                    """
                    UPDATE bookings
                    SET status='confirmed',
                        expires_at=NULL
                    WHERE id=%s
                    """,
                    (bid,)
                )

                con.commit()

                await callback.message.edit_reply_markup(
                    reply_markup=None
                )

                await callback.bot.send_message(
                    row["user_id"],
                    f"✅ <b>Заявка №{bid} подтверждена!</b>\n\n"
                    f"🚗 {CARS[row['car_id']]['name']}\n\n"
                    f"📅 Получение:\n"
                    f"<b>{format_date_time(start_at)}</b>\n\n"
                    f"↩️ Возврат:\n"
                    f"<b>{format_date_time(end_at)}</b>\n\n"
                    f"⏱ {rental_days(start_at, end_at)} суток\n"
                    f"💰 {money(row['total'])}\n\n"
                    "Менеджер свяжется с вами "
                    "для согласования деталей.",
                    reply_markup=main_keyboard()
                )

                await callback.answer(
                    "Заявка подтверждена."
                )

            # ==================================================
            # REJECT
            # ==================================================

            else:

                cur.execute(
                    """
                    UPDATE bookings
                    SET status='rejected'
                    WHERE id=%s
                    """,
                    (bid,)
                )

                con.commit()

                await callback.message.edit_reply_markup(
                    reply_markup=None
                )

                await callback.bot.send_message(
                    row["user_id"],
                    f"❌ <b>Заявка №{bid} отклонена.</b>\n\n"
                    "Вы можете выбрать другой "
                    "автомобиль или другой период.",
                    reply_markup=main_keyboard()
                )

                await callback.answer(
                    "Заявка отклонена."
                )

    except Exception:

        con.rollback()
        raise

    finally:

        con.close()


# ============================================================
# PUBLISH
# ============================================================

async def publish(
    message: Message
):

    if message.from_user.id != ADMIN_ID:
        return

    me = await message.bot.me()

    text = (
        "🚗 <b>Balticar — аренда автомобилей "
        "в Калининграде</b>\n\n"
        "Выберите автомобиль, посмотрите стоимость "
        "и забронируйте его прямо в Telegram."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 Забронировать автомобиль",
                    url=f"https://t.me/{me.username}"
                )
            ]
        ]
    )

    await message.bot.send_message(
        CHANNEL_USERNAME,
        text,
        reply_markup=keyboard
    )

    await message.answer(
        "Готово: пост с кнопкой опубликован в канале."
    )


# ============================================================
# MAIN
# ============================================================


async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не задан в Environment Variables."
        )

    # ========================================================
    # DATABASE
    # ========================================================

    init_db()

    print(
        "Neon PostgreSQL connected successfully."
    )

    # ========================================================
    # BOT
    # ========================================================

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    dp = Dispatcher()

    # ========================================================
    # COMMANDS
    # ========================================================

    dp.message.register(
        start_handler,
        Command("start")
    )

    dp.message.register(
        id_handler,
        Command("id")
    )

    dp.message.register(
        publish,
        Command("publish")
    )

    dp.message.register(
        admin_panel,
        Command("admin")
    )

    # ========================================================
    # CLIENT
    # ========================================================

    dp.callback_query.register(
        home,
        F.data == "home"
    )

    dp.callback_query.register(
        catalog,
        F.data == "catalog"
    )

    dp.callback_query.register(
        car_selected,
        F.data.startswith("car:")
    )

    dp.callback_query.register(
        pick_dates,
        F.data.startswith("pick:")
    )

    dp.callback_query.register(
        month,
        F.data.startswith("month:")
    )

    dp.callback_query.register(
        start_day,
        F.data.startswith("day:")
    )

    dp.callback_query.register(
        backstart,
        F.data.startswith("backstart:")
    )

    dp.callback_query.register(
        pick_time,
        F.data.startswith("picktime:")
    )

    dp.callback_query.register(
        endmonth,
        F.data.startswith("endmonth:")
    )

    dp.callback_query.register(
        end_day,
        F.data.startswith("end:")
    )

    dp.callback_query.register(
        backend,
        F.data.startswith("backend:")
    )

    dp.callback_query.register(
        backstarttime,
        F.data.startswith("backstarttime:")
    )

    dp.callback_query.register(
        end_time_handler,
        F.data.startswith("endtime:")
    )

    dp.callback_query.register(
        mybookings,
        F.data == "mybookings"
    )

    dp.callback_query.register(
        terms,
        F.data == "terms"
    )

    dp.callback_query.register(
        contact,
        F.data == "contact"
    )

    # ========================================================
    # ADMIN
    # ========================================================

    dp.callback_query.register(
        admin_back,
        F.data == "admin:back"
    )

    dp.callback_query.register(
        admin_new,
        F.data == "admin:new"
    )

    dp.callback_query.register(
        admin_calendar,
        F.data == "admin:calendar"
    )

    dp.callback_query.register(
        admin_bookings,
        F.data == "admin:bookings"
    )

    dp.callback_query.register(
        admin_cars,
        F.data == "admin:cars"
    )

    dp.callback_query.register(
        admin_car_calendar,
        F.data.startswith("admincar:")
    )

    dp.callback_query.register(
        admin_car_info,
        F.data.startswith("admincarinfo:")
    )

    dp.callback_query.register(
        admin_month,
        F.data.startswith("adminmonth:")
    )

    dp.callback_query.register(
        admin_day,
        F.data.startswith("adminday:")
    )

    dp.callback_query.register(
        admin_booking,
        F.data.startswith("adminbooking:")
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("confirm:")
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("reject:")
    )

    dp.callback_query.register(
        lambda c: c.answer(),
        F.data == "noop"
    )

    # ========================================================
    # FSM
    # ========================================================

    dp.message.register(
        name_handler,
        Booking.name
    )

    dp.message.register(
        phone_handler,
        Booking.phone
    )

    dp.message.register(
        comment_handler,
        Booking.comment
    )

    # ========================================================
    # WEBHOOK / RENDER
    # ========================================================

    external_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        ""
    ).rstrip("/")

    if not external_url:

        print(
            "RENDER_EXTERNAL_URL is not set; "
            "starting polling mode."
        )

        try:

            await dp.start_polling(
                bot
            )

        finally:

            await bot.session.close()

        return

    webhook_url = (
        f"{external_url}"
        f"{WEBHOOK_PATH}"
    )

    secret = (
        WEBHOOK_SECRET
        or secrets.token_urlsafe(32)
    )

    await bot.set_webhook(
        webhook_url,
        secret_token=secret,
        allowed_updates=(
            dp.resolve_used_update_types()
        ),
        drop_pending_updates=False
    )

    async def health(
        _: web.Request
    ):

        return web.Response(
            text="Balticar bot is running"
        )

    async def telegram_webhook(
        request: web.Request
    ):

        if (
            WEBHOOK_SECRET
            and request.headers.get(
                "X-Telegram-Bot-Api-Secret-Token"
            ) != WEBHOOK_SECRET
        ):

            raise web.HTTPForbidden(
                text="Invalid webhook secret"
            )

        data = await request.json()

        from aiogram.types import Update

        update = Update.model_validate(
            data,
            context={
                "bot": bot
            }
        )

        await dp.feed_update(
            bot,
            update
        )

        return web.Response(
            text="OK"
        )

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_get(
        "/health",
        health
    )

    app.router.add_post(
        WEBHOOK_PATH,
        telegram_webhook
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(
        "Balticar bot started in webhook mode: "
        f"{webhook_url}"
    )

    try:

        await asyncio.Event().wait()

    finally:

        await bot.delete_webhook(
            drop_pending_updates=False
        )

        await runner.cleanup()

        await bot.session.close()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
