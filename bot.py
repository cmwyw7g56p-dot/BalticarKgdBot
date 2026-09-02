import asyncio
import os
import secrets
import time as monotonic_time
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from aiohttp import web
import psycopg
from psycopg.rows import dict_row

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
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
    "@Balticar_kgd"
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

# Получение и возврат:
#
# 08:00–20:00
#
# Время выбирается с шагом 1 час.

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
# ASYNC DATABASE HELPERS
# ============================================================

async def async_cleanup_pending():
    """
    Выполняет синхронный PostgreSQL-код
    в отдельном потоке.

    Это важно для aiogram:
    event loop не блокируется ожиданием Neon.
    """

    await asyncio.to_thread(
        cleanup_pending
    )


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


async def async_available(
    car_id,
    start_at,
    end_at,
    exclude_booking_id=None
):
    """
    Неблокирующая для event loop версия
    проверки занятости.

    PostgreSQL работает в отдельном потоке.
    """

    return await asyncio.to_thread(
        available,
        car_id,
        start_at,
        end_at,
        exclude_booking_id
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

    Никаких запросов по каждому дню.
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


async def async_get_month_bookings(
    car_id,
    year,
    month
):
    return await asyncio.to_thread(
        get_month_bookings,
        car_id,
        year,
        month
    )


# ============================================================
# DAY STATUS
# ============================================================

def day_status(
    current,
    bookings,
    today
):

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
        "cancelled": "⚫ Отменена",
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

    Получение:
        08:00–20:00

    Возврат:
        08:00–20:00

    Проверка занятости НЕ выполняется
    при построении клавиатуры.

    Реальная проверка выполняется только
    после нажатия конкретного времени.
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

            # День с предыдущей арендой не блокируется целиком.
            # Здесь уже известно время получения, поэтому можно
            # точно убрать часы, конфликтующие с существующей бронью
            # и BUFFER_HOURS. Например, если возврат в 09:00,
            # при буфере 2 часа заезд с 11:00 уже доступен.
            if booking_overlaps(
                car_id,
                dt,
                dt + timedelta(hours=1)
            ):
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

def calendar_keyboard_sync(
    car_id,
    year,
    month
):
    """
    Синхронная часть построения календаря.

    Вызов из Telegram выполняется через
    asyncio.to_thread().
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

            # День с бронью может быть доступен для нового заезда
            # позже в этот же день. Поэтому дату НЕ блокируем целиком.
            # Точное пересечение с учётом BUFFER_HOURS проверяется
            # после выбора времени получения.
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


async def calendar_keyboard(
    car_id,
    year,
    month
):

    return await asyncio.to_thread(
        calendar_keyboard_sync,
        car_id,
        year,
        month
    )


# ============================================================
# END DATE CALENDAR
# ============================================================

def end_calendar_keyboard_sync(
    car_id,
    start_at,
    year,
    month
):
    """
    Синхронная часть календаря возврата.

    Весь вызов выполняется через asyncio.to_thread().

    Внутри календаря нет запросов по каждому дню.
    Выполняется только один запрос месяца.
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

        if current <= start_at.date():

            text = "⚪"
            callback_data = "noop"

        else:

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
                    f"endmonth|{car_id}|{start_at.isoformat()}|{prev_first.isoformat()}"
                )
            ),
            InlineKeyboardButton(
                text=f"{months[month - 1]} {year}",
                callback_data="noop"
            ),
            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"endmonth|{car_id}|{start_at.isoformat()}|{next_first.isoformat()}"
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


async def end_calendar_keyboard(
    car_id,
    start_at,
    year,
    month
):

    started = monotonic_time.monotonic()

    print(
        f"[END_CALENDAR] START "
        f"car={car_id} "
        f"year={year} "
        f"month={month}"
    )

    result = await asyncio.to_thread(
        end_calendar_keyboard_sync,
        car_id,
        start_at,
        year,
        month
    )

    print(
        f"[END_CALENDAR] FINISHED "
        f"duration={monotonic_time.monotonic() - started:.3f}s"
    )

    return result


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

def admin_busy_calendar_keyboard_sync(
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


async def admin_busy_calendar_keyboard(
    car_id,
    year,
    month
):

    return await asyncio.to_thread(
        admin_busy_calendar_keyboard_sync,
        car_id,
        year,
        month
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

    # Отвечаем Telegram сразу.
    await safe_callback_answer(callback)

    await state.clear()

    await callback.message.edit_text(
        "🚗 <b>Balticar</b>\n\n"
        "Аренда автомобилей в Калининграде.\n\n"
        "Выберите нужное действие:",
        reply_markup=main_keyboard()
    )


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

    await safe_callback_answer(callback)

    await callback.message.edit_text(
        "🚗 <b>Автомобили Balticar</b>\n\n"
        "Выберите автомобиль, чтобы посмотреть "
        "фото, характеристики и стоимость:",
        reply_markup=car_keyboard()
    )


async def car_selected(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.message.answer(
            "Автомобиль не найден."
        )

        return

    await send_car(
        callback.bot,
        callback.message.chat.id,
        cid
    )


async def pick_dates(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.message.answer(
            "Автомобиль не найден."
        )

        return

    today = datetime.now(TZ).date()

    keyboard = await calendar_keyboard(
        cid,
        today.year,
        today.month
    )

    await callback.message.answer(
        f"📅 <b>{CARS[cid]['name']}</b>\n\n"
        "Выберите дату получения.\n\n"
        "🟢 свободно\n"
        "🟡 есть заявка\n"
        "🔴 подтверждено\n"
        "⚪ прошедшая дата",
        reply_markup=keyboard
    )


async def month(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    _, cid, iso = callback.data.split(":")

    d = date.fromisoformat(
        iso
    )

    keyboard = await calendar_keyboard(
        cid,
        d.year,
        d.month
    )

    await callback.message.edit_reply_markup(
        reply_markup=keyboard
    )


# ============================================================
# START DATE
# ============================================================

async def start_day(
    callback: CallbackQuery,
    state: FSMContext
):

    await safe_callback_answer(callback)

    _, cid, iso = callback.data.split(":")

    if cid not in CARS:

        await callback.message.answer(
            "Автомобиль не найден."
        )

        return

    start_d = date.fromisoformat(
        iso
    )

    today = datetime.now(TZ).date()

    if start_d < today:

        await callback.message.answer(
            "Нельзя выбрать прошедшую дату."
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


# ============================================================
# BACK TO START CALENDAR
# ============================================================

async def backstart(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    _, cid, iso = callback.data.split(":")

    d = date.fromisoformat(
        iso
    )

    keyboard = await calendar_keyboard(
        cid,
        d.year,
        d.month
    )

    await callback.message.edit_reply_markup(
        reply_markup=keyboard
    )


# ============================================================
# START TIME
# ============================================================

async def pick_time(
    callback: CallbackQuery,
    state: FSMContext
):
    """
    Обработка выбора времени получения.

    callback_data имеет формат:

        picktime:CAR_ID:YYYY-MM-DD:HH:MM

    Поэтому обычный split(":") даёт 5 частей.
    Время собираем из последних двух частей.
    """

    # ========================================================
    # Telegram подтверждаем сразу.
    # ========================================================

    await safe_callback_answer(callback)

    try:

        parts = callback.data.split(":")

        if len(parts) != 5:

            await callback.message.answer(
                "❌ Некорректные данные выбора времени."
            )

            return

        _, cid, date_iso, hour_s, minute_s = parts

        time_text = (
            f"{hour_s}:{minute_s}"
        )

    except (ValueError, AttributeError):

        await callback.message.answer(
            "❌ Некорректные данные выбора времени."
        )

        return

    # ========================================================
    # Проверяем автомобиль.
    # ========================================================

    if cid not in CARS:

        await callback.message.answer(
            "Автомобиль не найден."
        )

        return

    # ========================================================
    # Проверяем дату и время.
    # ========================================================

    try:

        start_d = date.fromisoformat(
            date_iso
        )

        hour = int(hour_s)
        minute = int(minute_s)

    except ValueError:

        await callback.message.answer(
            "❌ Некорректная дата или время."
        )

        return

    # ========================================================
    # Проверяем диапазон времени.
    # ========================================================

    if hour < 0 or hour > 23:

        await callback.message.answer(
            "❌ Некорректный час."
        )

        return

    if minute < 0 or minute > 59:

        await callback.message.answer(
            "❌ Некорректные минуты."
        )

        return

    if not (
        PICKUP_START_HOUR
        <= hour
        <= PICKUP_END_HOUR
    ):

        await callback.message.answer(
            f"Время получения доступно "
            f"с {PICKUP_START_HOUR:02d}:00 "
            f"до {PICKUP_END_HOUR:02d}:00."
        )

        return

    # ========================================================
    # Создаём timezone-aware datetime.
    # ========================================================

    start_at = local_dt(
        start_d,
        time(hour, minute)
    )

    now = datetime.now(TZ)

    if start_at <= now:

        await callback.message.answer(
            "Это время уже прошло."
        )

        return

    # ========================================================
    # Сохраняем выбранное время.
    # ========================================================

    await state.update_data(
        car_id=cid,
        start_at=start_at.isoformat()
    )

    await state.set_state(
        Booking.end
    )

    # ========================================================
    # Показываем календарь возврата.
    # ========================================================

    keyboard = await end_calendar_keyboard(
        cid,
        start_at,
        start_at.year,
        start_at.month
    )

    await callback.message.answer(
        f"📅 <b>Получение автомобиля</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n"
        f"🕐 <b>{format_date_time(start_at)}</b>\n\n"
        "Теперь выберите дату возврата:",
        reply_markup=keyboard
    )


# ============================================================
# SAFE CALLBACK ANSWER
# ============================================================

async def safe_callback_answer(
    callback: CallbackQuery,
    **kwargs
):
    """
    Безопасное подтверждение callback-запроса.

    Telegram может прислать повторный callback или callback,
    который к моменту обработки уже стал недействительным.
    В таком случае ошибка TelegramBadRequest не должна падать
    и останавливать обработку update.
    """

    try:
        await callback.answer(**kwargs)
        return True
    except TelegramBadRequest as exc:
        text = str(exc).lower()

        if (
            "query is too old" in text
            or "response timeout expired" in text
            or "query id is invalid" in text
            or "query is invalid" in text
        ):
            print(
                f"[CALLBACK] answer skipped: "
                f"id={callback.id} error={exc}"
            )
            return False

        raise


# ============================================================
# END MONTH
# ============================================================

async def endmonth(
    callback: CallbackQuery
):
    """
    Переключение месяца календаря возврата.

    Обработчик специально сделан максимально самостоятельным:
    callback подтверждается сразу, входные данные проверяются
    поэтапно, а ошибка редактирования клавиатуры явно
    логируется и не теряется.
    """

    started = monotonic_time.monotonic()
    data = callback.data or ""

    print(
        f"[ENDMONTH] START "
        f"id={callback.id} "
        f"data={data!r} "
        f"t={started:.3f}"
    )

    # Подтверждаем callback как можно раньше.
    try:
        answered = await safe_callback_answer(callback)
        print(
            f"[ENDMONTH] ANSWERED "
            f"id={callback.id} "
            f"result={answered} "
            f"after={monotonic_time.monotonic() - started:.3f}s"
        )
    except Exception as exc:
        print(
            f"[ENDMONTH] callback.answer ERROR "
            f"id={callback.id} "
            f"type={type(exc).__name__} "
            f"error={exc!r}"
        )
        raise

    # --------------------------------------------------------
    # Проверяем формат callback_data.
    # Формат специально использует |, потому что ISO datetime
    # содержит двоеточия.
    # Ожидается:
    # endmonth|<car_id>|<start_datetime>|<YYYY-MM-DD>
    # --------------------------------------------------------
    parts = data.split("|")

    print(
        f"[ENDMONTH] PARSE "
        f"parts_count={len(parts)} "
        f"parts={parts!r}"
    )

    if len(parts) != 4 or parts[0] != "endmonth":
        print(
            f"[ENDMONTH] INVALID CALLBACK FORMAT "
            f"data={data!r}"
        )
        return

    _, cid, start_iso, month_iso = parts

    print(
        f"[ENDMONTH] VALUES "
        f"car_id={cid!r} "
        f"start_iso={start_iso!r} "
        f"month_iso={month_iso!r}"
    )

    # --------------------------------------------------------
    # Проверяем автомобиль.
    # --------------------------------------------------------
    if cid not in CARS:
        print(
            f"[ENDMONTH] INVALID CAR "
            f"car_id={cid!r}"
        )

        await callback.message.answer(
            "Автомобиль не найден."
        )
        return

    # --------------------------------------------------------
    # Проверяем дату/время начала аренды.
    # --------------------------------------------------------
    try:
        start_at = datetime.fromisoformat(
            start_iso
        )
        start_at = ensure_tz(
            start_at
        )
    except (TypeError, ValueError, AttributeError) as exc:
        print(
            f"[ENDMONTH] INVALID START DATE "
            f"start_iso={start_iso!r} "
            f"type={type(exc).__name__} "
            f"error={exc!r}"
        )

        await callback.message.answer(
            "❌ Некорректная дата начала аренды. "
            "Начните бронирование заново."
        )
        return

    # --------------------------------------------------------
    # Проверяем дату выбранного месяца.
    # --------------------------------------------------------
    try:
        selected_month = date.fromisoformat(
            month_iso
        )
    except (TypeError, ValueError, AttributeError) as exc:
        print(
            f"[ENDMONTH] INVALID MONTH DATE "
            f"month_iso={month_iso!r} "
            f"type={type(exc).__name__} "
            f"error={exc!r}"
        )

        await callback.message.answer(
            "❌ Некорректный месяц календаря. "
            "Откройте календарь заново."
        )
        return

    year = selected_month.year
    month_number = selected_month.month

    print(
        f"[ENDMONTH] TARGET MONTH "
        f"car_id={cid} "
        f"start={start_at.isoformat()} "
        f"year={year} "
        f"month={month_number}"
    )

    # --------------------------------------------------------
    # Формируем календарь. Логику выбора даты возврата
    # здесь не меняем.
    # --------------------------------------------------------
    try:
        keyboard = await end_calendar_keyboard(
            cid,
            start_at,
            year,
            month_number
        )
    except Exception as exc:
        print(
            f"[ENDMONTH] CALENDAR BUILD ERROR "
            f"car_id={cid} "
            f"year={year} "
            f"month={month_number} "
            f"type={type(exc).__name__} "
            f"error={exc!r}"
        )
        raise

    print(
        f"[ENDMONTH] CALENDAR READY "
        f"car_id={cid} "
        f"year={year} "
        f"month={month_number} "
        f"after={monotonic_time.monotonic() - started:.3f}s"
    )

    # --------------------------------------------------------
    # Отдельно контролируем Telegram-редактирование клавиатуры.
    # --------------------------------------------------------
    try:
        await callback.message.edit_reply_markup(
            reply_markup=keyboard
        )
    except TelegramBadRequest as exc:
        print(
            f"[ENDMONTH] edit_reply_markup TelegramBadRequest "
            f"id={callback.id} "
            f"car_id={cid} "
            f"year={year} "
            f"month={month_number} "
            f"error={exc!r}"
        )
        return
    except Exception as exc:
        print(
            f"[ENDMONTH] edit_reply_markup ERROR "
            f"id={callback.id} "
            f"car_id={cid} "
            f"year={year} "
            f"month={month_number} "
            f"type={type(exc).__name__} "
            f"error={exc!r}"
        )
        raise

    print(
        f"[ENDMONTH] DONE "
        f"id={callback.id} "
        f"car_id={cid} "
        f"year={year} "
        f"month={month_number} "
        f"total={monotonic_time.monotonic() - started:.3f}s"
    )


# ============================================================
# END DATE
# ============================================================

async def end_day(
    callback: CallbackQuery,
    state: FSMContext
):

    await safe_callback_answer(callback)

    _, cid, end_iso = callback.data.split(":")

    if cid not in CARS:

        await callback.message.answer(
            "Автомобиль не найден."
        )

        return

    data = await state.get_data()

    if not data.get("start_at"):

        await state.clear()

        await callback.message.answer(
            "Сессия устарела. "
            "Начните бронирование заново.",
            reply_markup=main_keyboard()
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

        await callback.message.answer(
            "Дата возврата должна быть позже даты получения."
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


# ============================================================
# BACK TO END CALENDAR
# ============================================================

async def backend(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    _, cid, start_iso = callback.data.split(":")

    start_d = date.fromisoformat(
        start_iso
    )

    start_at = local_dt(
        start_d,
        time(PICKUP_START_HOUR, 0)
    )

    keyboard = await end_calendar_keyboard(
        cid,
        start_at,
        start_d.year,
        start_d.month
    )

    await callback.message.edit_reply_markup(
        reply_markup=keyboard
    )


async def backstarttime(
    callback: CallbackQuery
):

    started = monotonic_time.monotonic()

    print(
        f"[BACKSTARTTIME] START "
        f"id={callback.id} "
        f"data={callback.data} "
        f"t={started:.3f}"
    )

    await safe_callback_answer(callback)

    print(
        f"[BACKSTARTTIME] ANSWERED "
        f"id={callback.id} "
        f"after={monotonic_time.monotonic() - started:.3f}s"
    )

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
        reply_markup=

keyboard
    )

# ============================================================
# END TIME
# ============================================================

async def end_time_handler(
    callback: CallbackQuery,
    state: FSMContext
):
    """
    Обработка выбора времени возврата.

    callback_data имеет формат:
        endtime:CAR_ID:YYYY-MM-DD:HH:MM

    После выбора времени повторно проверяем доступность
    автомобиля и только затем переходим к вводу данных клиента.
    """

    await safe_callback_answer(callback)

    try:
        parts = callback.data.split(":")

        if len(parts) != 5:
            await callback.message.answer(
                "❌ Некорректные данные выбора времени."
            )
            return

        _, cid, end_date_iso, hour_s, minute_s = parts

        hour = int(hour_s)
        minute = int(minute_s)
        end_d = date.fromisoformat(end_date_iso)

    except (ValueError, AttributeError):
        await callback.message.answer(
            "❌ Некорректная дата или время возврата."
        )
        return

    if cid not in CARS:
        await callback.message.answer(
            "Автомобиль не найден."
        )
        return

    if not (
        PICKUP_START_HOUR
        <= hour
        <= PICKUP_END_HOUR
    ) or minute not in (0,):
        await callback.message.answer(
            f"Время возврата доступно с "
            f"{PICKUP_START_HOUR:02d}:00 "
            f"до {PICKUP_END_HOUR:02d}:00."
        )
        return

    data = await state.get_data()

    if not data.get("start_at"):
        await state.clear()
        await callback.message.answer(
            "Сессия бронирования устарела.\n\n"
            "Начните бронирование заново.",
            reply_markup=main_keyboard()
        )
        return

    try:
        start_at = datetime.fromisoformat(
            data["start_at"]
        )
        start_at = ensure_tz(start_at)
    except (ValueError, TypeError):
        await state.clear()
        await callback.message.answer(
            "Сессия бронирования повреждена.\n\n"
            "Начните бронирование заново.",
            reply_markup=main_keyboard()
        )
        return

    end_at = local_dt(
        end_d,
        time(hour, minute)
    )

    if end_at <= start_at:
        await callback.message.answer(
            "Возврат должен быть позже времени получения."
        )
        return

    # Повторная проверка непосредственно перед переходом
    # к заполнению данных клиента.
    if not await async_available(
        cid,
        start_at,
        end_at
    ):
        await callback.message.answer(
            "❌ <b>Автомобиль уже занят</b>\n\n"
            "Выбранный период пересекается с другой заявкой.\n"
            "Пожалуйста, выберите другую дату или время."
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
        car_id=cid,
        end_date=end_date_iso,
        end_at=end_at.isoformat(),
        days=days,
        total=total
    )

    await state.set_state(
        Booking.name
    )

    await callback.message.answer(
        f"✅ <b>Период выбран</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n\n"
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        f"📅 Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"
        f"⏱ Продолжительность: <b>{days} суток</b>\n"
        f"💰 Стоимость: <b>{money(total)}</b>\n\n"
        "Введите ваше имя:"
    )


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
# CREATE BOOKING — DATABASE PART
# ============================================================

def create_booking_sync(
    message_user_id,
    username,
    cid,
    start_at,
    end_at,
    name,
    phone,
    comment
):
    """
    Полностью синхронная транзакция PostgreSQL.

    Вызывается только через asyncio.to_thread().

    Это исключает блокировку event loop.
    """

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

    con = db()

    try:

        with con.cursor() as cur:

            # ==================================================
            # АТОМАРНАЯ БЛОКИРОВКА
            # ==================================================

            cur.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtext('balticar-bookings')
                )
                """
            )

            # ==================================================
            # Истёкшие pending
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

                return {
                    "ok": False,
                    "reason": "overlap"
                }

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
                    message_user_id,
                    username,
                    cid,
                    start_date,
                    end_date,
                    start_at,
                    end_at,
                    name,
                    phone,
                    comment,
                    total,
                    created_at,
                    expires
                )
            ).fetchone()

            bid = row["id"]

        con.commit()

        return {
            "ok": True,
            "bid": bid,
            "days": days,
            "total": total,
            "expires": expires
        }

    except Exception:

        con.rollback()
        raise

    finally:

        con.close()


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

    # ========================================================
    # Вся тяжёлая работа с PostgreSQL —
    # в отдельном потоке.
    # ========================================================

    result = await asyncio.to_thread(
        create_booking_sync,
        message.from_user.id,
        message.from_user.username or "",
        cid,
        start_at,
        end_at,
        data["name"],
        data["phone"],
        comment
    )

    if not result["ok"]:

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

    bid = result["bid"]
    days = result["days"]
    total = result["total"]
    expires = result["expires"]

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

def get_user_bookings_sync(
    user_id
):

    cleanup_pending()

    con = db()

    try:

        with con.cursor() as cur:

            return cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE user_id=%s
                ORDER BY id DESC
                LIMIT 10
                """,
                (
                    user_id,
                )
            ).fetchall()

    finally:

        con.close()


async def mybookings(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    rows = await asyncio.to_thread(
        get_user_bookings_sync,
        callback.from_user.id
    )

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


# ============================================================
# TERMS
# ============================================================

async def terms(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

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


# ============================================================
# CONTACT
# ============================================================

async def contact(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

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

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    await callback.message.edit_text(
        "👨‍💼 <b>Админ-панель Balticar</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_panel_keyboard()
    )


# ============================================================
# ADMIN CALENDAR
# ============================================================

async def admin_calendar(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
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


async def admin_car_calendar(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.message.answer(
            "Автомобиль не найден."
        )

        return

    today = datetime.now(TZ).date()

    keyboard = await admin_busy_calendar_keyboard(
        cid,
        today.year,
        today.month
    )

    await callback.message.edit_text(
        "📅 <b>Календарь занятости</b>\n\n"
        f"🚗 <b>{CARS[cid]['name']}</b>\n\n"
        "🟢 свободно\n"
        "🟡 ожидает подтверждения\n"
        "🔴 подтверждено\n"
        "⚪ прошедшая дата\n\n"
        f"Буфер между арендами: "
        f"<b>{BUFFER_HOURS} ч.</b>",
        reply_markup=keyboard
    )


async def admin_month(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    _, cid, iso = callback.data.split(":")

    d = date.fromisoformat(
        iso
    )

    keyboard = await admin_busy_calendar_keyboard(
        cid,
        d.year,
        d.month
    )

    await callback.message.edit_reply_markup(
        reply_markup=keyboard
    )


# ============================================================
# ADMIN DAY
# ============================================================

def get_admin_day_bookings_sync(
    car_id,
    selected
):

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

            return cur.execute(
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


async def admin_day(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    _, car_id, iso = callback.data.split(":")

    selected = date.fromisoformat(
        iso
    )

    rows = await asyncio.to_thread(
        get_admin_day_bookings_sync,
        car_id,
        selected
    )

    if not rows:

        await callback.message.answer(
            "В этот день бронирований нет."
        )

        return

    out = [
        f"📅 <b>{selected.strftime('%d.%m.%Y')}</b>",
        f"🚗 <b>{CARS[car_id]['name']}</b>",
        ""
    ]

    keyboard = []

    for row in rows:

        start_at = ensure_tz(row["start_at"])
        end_at = ensure_tz(row["end_at"])

        out.append(
            f"📋 <b>Заявка №{row['id']}</b>\n"
            f"{status_label(row['status'])}\n"
            f"🕐 {format_date_time(start_at)}\n"
            f"↩️ {format_date_time(end_at)}\n"
            f"👤 {row['name']}\n"
            f"📞 {row['phone']}\n"
            f"💰 {money(row['total'])}\n"
        )
        if row["status"] in ("pending", "confirmed"):
            keyboard.append([InlineKeyboardButton(text=f"✏️ Изменить даты №{row['id']}", callback_data=f"adminedit:{row['id']}")])
            keyboard.append([InlineKeyboardButton(text=f"🚫 Отменить бронь №{row['id']}", callback_data=f"cancel:{row['id']}")])
        keyboard.append([InlineKeyboardButton(text=f"📋 Открыть заявку №{row['id']}", callback_data=f"adminbooking:{row['id']}")])

    keyboard.append([InlineKeyboardButton(text="◀️ К календарю", callback_data=f"admincar:{car_id}")])
    await callback.message.answer("\n".join(out), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))



# ============================================================
# ADMIN EDIT BOOKING
# ============================================================

class AdminEdit(StatesGroup):
    start = State()
    start_time = State()
    end = State()
    end_time = State()


def admin_edit_calendar_sync(bid, car_id, year, month, mode):
    first = date(year, month, 1)
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    prev_first = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    days = (next_first - first).days
    today = datetime.now(TZ).date()
    bookings = get_month_bookings(car_id, year, month)
    months = ["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]

    rows = [[InlineKeyboardButton(text=x, callback_data="noop") for x in ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]]]
    week = [InlineKeyboardButton(text=" ", callback_data="noop") for _ in range(first.weekday())]

    for n in range(1, days + 1):
        current = date(year, month, n)
        # В админ-редактировании можно перенести активную бронь даже на прошедшую дату.
        # Это нужно, например, для исправления брони 02.09–07.09 -> 01.09–05.09.
        blocked = False
        if not blocked:
            day_start = local_dt(current, time(0, 0))
            day_end = day_start + timedelta(days=1)
            for row in bookings:
                if row["id"] == bid:
                    continue
                rs = ensure_tz(row["start_at"])
                re = ensure_tz(row["end_at"])
                if rs < day_end + timedelta(hours=BUFFER_HOURS) and re > day_start - timedelta(hours=BUFFER_HOURS):
                    blocked = True
                    break

        if blocked:
            text, cb = "🔴", "noop"
        else:
            text = f"🟢{n}"
            cb = f"aeditday:{bid}:{mode}:{current.isoformat()}"
        week.append(InlineKeyboardButton(text=text, callback_data=cb))
        if len(week) == 7:
            rows.append(week); week=[]
    if week:
        while len(week)<7:
            week.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        rows.append(week)

    rows.append([
        InlineKeyboardButton(text="‹", callback_data=f"aeditmonth:{bid}:{car_id}:{mode}:{prev_first.isoformat()}"),
        InlineKeyboardButton(text=f"{months[month-1]} {year}", callback_data="noop"),
        InlineKeyboardButton(text="›", callback_data=f"aeditmonth:{bid}:{car_id}:{mode}:{next_first.isoformat()}"),
    ])
    rows.append([InlineKeyboardButton(text="◀️ Назад к заявке", callback_data=f"adminbooking:{bid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def admin_edit_calendar(bid, car_id, mode, d):
    return await asyncio.to_thread(admin_edit_calendar_sync, bid, car_id, d.year, d.month, mode)


def admin_edit_time_keyboard_sync(bid, car_id, selected_date, mode, start_at=None):
    now = datetime.now(TZ)
    rows = []
    current_row = []
    for hour in range(PICKUP_START_HOUR, PICKUP_END_HOUR + 1):
        dt = local_dt(selected_date, time(hour, 0))
        if mode == "start":
            # Для админа разрешено редактировать бронь на прошедшую дату.
            # Existing booking is excluded; all other bookings and the buffer are respected.
            if booking_overlaps(car_id, dt, dt + timedelta(hours=1), exclude_booking_id=bid):
                continue
            cb = f"aeditstarttime:{bid}:{selected_date.isoformat()}:{hour:02d}:00"
        else:
            if start_at is None or dt <= start_at:
                continue
            if booking_overlaps(car_id, start_at, dt, exclude_booking_id=bid):
                continue
            cb = f"aeditendtime:{bid}:{selected_date.isoformat()}:{hour:02d}:00"
        current_row.append(InlineKeyboardButton(text=f"{hour:02d}:00", callback_data=cb))
        if len(current_row)==3:
            rows.append(current_row); current_row=[]
    if current_row: rows.append(current_row)
    rows.append([InlineKeyboardButton(text="◀️ Назад к датам", callback_data=f"aeditbackdate:{bid}:{mode}:{selected_date.isoformat()}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def admin_edit_start(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    bid = int(callback.data.split(":",1)[1])
    row = await asyncio.to_thread(get_booking_sync, bid)
    if not row:
        await callback.message.answer("Заявка не найдена."); return
    if row["status"] not in ("pending", "confirmed"):
        await callback.message.answer("Эту заявку нельзя редактировать в текущем статусе."); return
    await state.clear()
    d = ensure_tz(row["start_at"]).date()
    await state.update_data(admin_edit_bid=bid, admin_edit_car_id=row["car_id"])
    await state.set_state(AdminEdit.start)
    kb = await admin_edit_calendar(bid, row["car_id"], "start", d)
    await callback.message.edit_text(
        f"✏️ <b>Изменение заявки №{bid}</b>\n\n🚗 {CARS[row['car_id']]['name']}\n\nВыберите новую <b>дату получения</b>.\n\n🔴 занято / недоступно\n🟢 можно выбрать",
        reply_markup=kb
    )


async def admin_edit_month(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    _, bid_s, car_id, mode, iso = callback.data.split(":")
    d = date.fromisoformat(iso)
    kb = await admin_edit_calendar(int(bid_s), car_id, mode, d)
    await callback.message.edit_reply_markup(reply_markup=kb)


async def admin_edit_day(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    _, bid_s, mode, iso = callback.data.split(":")
    bid = int(bid_s); d = date.fromisoformat(iso)
    row = await asyncio.to_thread(get_booking_sync, bid)
    if not row:
        await callback.message.answer("Заявка не найдена."); return
    if mode == "start":
        await state.update_data(admin_edit_bid=bid, admin_edit_car_id=row["car_id"], edit_start_date=iso)
        await state.set_state(AdminEdit.start_time)
        kb = await asyncio.to_thread(admin_edit_time_keyboard_sync, bid, row["car_id"], d, "start")
        await callback.message.edit_text(f"✏️ <b>Заявка №{bid}</b>\n\n📅 Новая дата получения: <b>{d.strftime('%d.%m.%Y')}</b>\n\nВыберите время:", reply_markup=kb)
    else:
        data = await state.get_data()
        start_iso = data.get("edit_start_at")
        if not start_iso:
            await callback.message.answer("Сначала выберите дату и время получения."); return
        start_at = ensure_tz(datetime.fromisoformat(start_iso))
        await state.update_data(edit_end_date=iso)
        await state.set_state(AdminEdit.end_time)
        kb = await asyncio.to_thread(admin_edit_time_keyboard_sync, bid, row["car_id"], d, "end", start_at)
        await callback.message.edit_text(f"✏️ <b>Заявка №{bid}</b>\n\n📅 Новая дата возврата: <b>{d.strftime('%d.%m.%Y')}</b>\n\nВыберите время:", reply_markup=kb)


async def admin_edit_start_time(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    parts = callback.data.split(":")
    bid_s, iso, hh_s, mm_s = parts[1], parts[2], parts[3], parts[4]
    bid=int(bid_s); d=date.fromisoformat(iso); hh=int(hh_s); mm=int(mm_s)
    start_at=local_dt(d,time(hh,mm))
    row=await asyncio.to_thread(get_booking_sync,bid)
    if not row: await callback.message.answer("Заявка не найдена."); return
    if not await async_available(row["car_id"], start_at, start_at+timedelta(hours=1), exclude_booking_id=bid):
        await callback.message.answer("❌ Это время недоступно."); return
    await state.update_data(edit_start_at=start_at.isoformat(), admin_edit_car_id=row["car_id"])
    await state.set_state(AdminEdit.end)
    d0=start_at.date()
    kb=await admin_edit_calendar(bid,row["car_id"],"end",d0)
    await callback.message.edit_text(f"✏️ <b>Заявка №{bid}</b>\n\n📅 Получение: <b>{format_date_time(start_at)}</b>\n\nВыберите новую <b>дату возврата</b>.",reply_markup=kb)


async def admin_edit_end_time(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    parts = callback.data.split(":")
    bid_s, iso, hh_s, mm_s = parts[1], parts[2], parts[3], parts[4]
    bid=int(bid_s); d=date.fromisoformat(iso); hh=int(hh_s); mm=int(mm_s)
    data=await state.get_data(); start_iso=data.get("edit_start_at")
    if not start_iso: await callback.message.answer("Сессия редактирования устарела."); return
    start_at=ensure_tz(datetime.fromisoformat(start_iso)); end_at=local_dt(d,time(hh,mm))
    if end_at <= start_at:
        await callback.message.answer("Возврат должен быть позже получения."); return
    row=await asyncio.to_thread(get_booking_sync,bid)
    if not row: await callback.message.answer("Заявка не найдена."); return
    if not await async_available(row["car_id"],start_at,end_at,exclude_booking_id=bid):
        await callback.message.answer("❌ Выбранный период пересекается с другой арендой или техническим интервалом."); return
    days=rental_days(start_at,end_at); total=days*rate_for_days(row["car_id"],days)
    result=await asyncio.to_thread(update_booking_dates_sync,bid,start_at,end_at,total)
    if not result["ok"]:
        await callback.message.answer(result.get("message","Не удалось изменить бронь.")); return
    await state.clear()

    # После изменения сразу показываем свежий календарь из БД.
    # Это гарантирует, что соседние/следующие брони не пропадают из интерфейса.
    refreshed_calendar = await admin_busy_calendar_keyboard(
        row["car_id"],
        start_at.year,
        start_at.month
    )

    await callback.message.edit_text(
        f"✅ <b>Заявка №{bid} изменена</b>\n\n"
        f"🚗 {CARS[row['car_id']]['name']}\n\n"
        f"📅 Получение: <b>{format_date_time(start_at)}</b>\n"
        f"↩️ Возврат: <b>{format_date_time(end_at)}</b>\n"
        f"⏱ {days} суток\n"
        f"💰 <b>{money(total)}</b>\n\n"
        f"Статус: {status_label(row['status'])}\n\n"
        "📅 Календарь обновлён. Другие активные бронирования сохранены.",
        reply_markup=refreshed_calendar
    )
    try:
        await callback.bot.send_message(row["user_id"], f"ℹ️ <b>Изменение заявки №{bid}</b>\n\nВаше бронирование автомобиля {CARS[row['car_id']]['name']} было изменено.\n\n📅 Получение: <b>{format_date_time(start_at)}</b>\n↩️ Возврат: <b>{format_date_time(end_at)}</b>\n💰 <b>{money(total)}</b>", reply_markup=main_keyboard())
    except Exception as e:
        print(f"[ADMIN_EDIT] notify error: {e}")


def update_booking_dates_sync(bid, start_at, end_at, total):
    con=db()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('balticar-bookings'))")
            row=cur.execute("SELECT * FROM bookings WHERE id=%s FOR UPDATE",(bid,)).fetchone()
            if not row:
                con.rollback(); return {"ok":False,"message":"Заявка не найдена."}
            if row["status"] not in ("pending","confirmed"):
                con.rollback(); return {"ok":False,"message":"Заявку нельзя изменить в текущем статусе."}
            buffer_delta=timedelta(hours=BUFFER_HOURS)
            other=cur.execute("""
                SELECT id FROM bookings
                WHERE car_id=%s AND id<>%s
                  AND status IN ('pending','confirmed')
                  AND start_at < %s AND end_at > %s
                LIMIT 1
            """,(row["car_id"],bid,end_at+buffer_delta,start_at-buffer_delta)).fetchone()
            if other:
                con.rollback(); return {"ok":False,"message":"❌ Новый период пересекается с другой арендой или техническим интервалом."}
            cur.execute("""
                UPDATE bookings
                SET start_date=%s,end_date=%s,start_at=%s,end_at=%s,total=%s
                WHERE id=%s
            """,(start_at.date(),end_at.date(),start_at,end_at,total,bid))
        con.commit(); return {"ok":True}
    except Exception:
        con.rollback(); raise
    finally: con.close()


async def admin_edit_backdate(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    _, bid_s, mode, iso = callback.data.split(":")
    bid=int(bid_s); d=date.fromisoformat(iso)
    row=await asyncio.to_thread(get_booking_sync,bid)
    if not row: return
    target="start" if mode=="start" else "end"
    await state.set_state(AdminEdit.start if target=="start" else AdminEdit.end)
    kb=await admin_edit_calendar(bid,row["car_id"],target,d)
    await callback.message.edit_text(f"✏️ <b>Заявка №{bid}</b>\n\nВыберите дату:",reply_markup=kb)


# ============================================================
# ADMIN NEW
# ============================================================

def get_admin_new_sync():

    cleanup_pending()

    con = db()

    try:

        with con.cursor() as cur:

            return cur.execute(
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


async def admin_new(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    rows = await asyncio.to_thread(
        get_admin_new_sync
    )

    if not rows:

        await callback.message.edit_text(
            "🔔 <b>Новые заявки</b>\n\n"
            "Новых заявок нет.",
            reply_markup=admin_back_keyboard()
        )

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


# ============================================================
# ADMIN BOOKING
# ============================================================

def get_booking_sync(
    bid
):

    con = db()

    try:

        with con.cursor() as cur:

            return cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE id=%s
                """,
                (bid,)
            ).fetchone()

    finally:

        con.close()


async def admin_booking(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    bid = int(
        callback.data.split(
            ":",
            1
        )[1]
    )

    row = await asyncio.to_thread(
        get_booking_sync,
        bid
    )

    if not row:

        await callback.message.answer(
            "Заявка не найдена."
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

    if row["status"] in ("pending", "confirmed"):
        buttons = [
            [InlineKeyboardButton(text="✏️ Изменить даты", callback_data=f"adminedit:{bid}")],
        ]
        if row["status"] == "pending":
            buttons.append([
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{bid}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{bid}"),
            ])
        buttons.append([
            InlineKeyboardButton(text="🚫 Отменить бронь", callback_data=f"cancel:{bid}")
        ])
        buttons.append([
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"admincar:{row['car_id']}")
        ])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    else:
        reply_markup = admin_back_keyboard()

    await callback.message.edit_text(text, reply_markup=reply_markup)


# ============================================================
# ADMIN ALL BOOKINGS
# ============================================================

def get_all_bookings_sync():

    cleanup_pending()

    con = db()

    try:

        with con.cursor() as cur:

            return cur.execute(
                """
                SELECT *
                FROM bookings
                ORDER BY id DESC
                LIMIT 30
                """
            ).fetchall()

    finally:

        con.close()


async def admin_bookings(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    rows = await asyncio.to_thread(
        get_all_bookings_sync
    )

    if not rows:

        await callback.message.edit_text(
            "📋 <b>Все бронирования</b>\n\n"
            "Бронирований пока нет.",
            reply_markup=admin_back_keyboard()
        )

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


# ============================================================
# ADMIN CARS
# ============================================================

async def admin_cars(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
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


async def admin_car_info(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    cid = callback.data.split(
        ":",
        1
    )[1]

    if cid not in CARS:

        await callback.message.answer(
            "Автомобиль не найден."
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


# ============================================================
# CONFIRM / REJECT
# ============================================================

def admin_action_sync(
    action,
    bid
):
    """
    Полностью атомарная операция PostgreSQL.

    Возвращает данные заявки и результат.
    """

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

            cur.execute(
                """
                UPDATE bookings
                SET status='expired'
                WHERE status='pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < NOW()
                """
            )

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

                return {
                    "ok": False,
                    "reason": "not_found"
                }

            if action == "cancel":
                if row["status"] not in ("pending", "confirmed"):
                    con.rollback()
                    return {
                        "ok": False,
                        "reason": "already_processed",
                        "status": row["status"]
                    }
            elif row["status"] != "pending":
                con.rollback()
                return {
                    "ok": False,
                    "reason": "already_processed",
                    "status": row["status"]
                }

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

            if action == "cancel":

                cur.execute(
                    """
                    UPDATE bookings
                    SET status='cancelled',
                        expires_at=NULL
                    WHERE id=%s
                    """,
                    (bid,)
                )

                con.commit()

                return {
                    "ok": True,
                    "action": "cancel",
                    "row": row,
                    "start_at": start_at,
                    "end_at": end_at
                }

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

                    return {
                        "ok": False,
                        "reason": "overlap",
                        "row": row,
                        "start_at": start_at,
                        "end_at": end_at
                    }

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

                return {
                    "ok": True,
                    "action": "confirm",
                    "row": row,
                    "start_at": start_at,
                    "end_at": end_at
                }

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

                return {
                    "ok": True,
                    "action": "reject",
                    "row": row,
                    "start_at": start_at,
                    "end_at": end_at
                }

    except Exception:

        con.rollback()
        raise

    finally:

        con.close()


async def admin_action(
    callback: CallbackQuery
):

    # Отвечаем Telegram сразу.
    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )

        return

    action, bid_s = callback.data.split(
        ":"
    )

    bid = int(bid_s)

    # ========================================================
    # PostgreSQL выполняется в отдельном потоке.
    # ========================================================

    result = await asyncio.to_thread(
        admin_action_sync,
        action,
        bid
    )

    if not result["ok"]:

        if result["reason"] == "not_found":

            await callback.message.answer(
                "Заявка не найдена."
            )

            return

        if result["reason"] == "already_processed":

            await callback.message.answer(
                f"Заявка уже имеет статус: "
                f"{status_label(result['status'])}"
            )

            return

        if result["reason"] == "overlap":

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

            row = result["row"]

            await callback.bot.send_message(
                row["user_id"],
                f"❌ <b>Заявка №{bid} отклонена</b>\n\n"
                "Выбранное время уже занято "
                "или недостаточно времени "
                "между арендами.",
                reply_markup=main_keyboard()
            )

            await callback.message.answer(
                "Время уже занято. "
                "Заявка автоматически отклонена."
            )

            return

    row = result["row"]

    start_at = result["start_at"]
    end_at = result["end_at"]

    if result["action"] == "cancel":

        await callback.message.edit_reply_markup(reply_markup=None)

        await callback.bot.send_message(
            row["user_id"],
            f"🚫 <b>Бронирование №{bid} отменено</b>.\n\n"
            f"🚗 {CARS[row['car_id']]['name']}\n"
            f"📅 {format_date_time(start_at)} → {format_date_time(end_at)}\n\n"
            "Вы можете выбрать другой свободный период.",
            reply_markup=main_keyboard()
        )

        await callback.message.answer(
            f"🚫 Бронь №{bid} отменена. Даты снова свободны для бронирования.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📅 К календарю", callback_data=f"admincar:{row['car_id']}")]
            ])
        )

    elif result["action"] == "confirm":

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

    else:

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
    #
    # Инициализация происходит один раз при старте.
    # ========================================================

    await asyncio.to_thread(
        init_db
    )

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
        F.data.startswith(("endmonth:", "endmonth|"))
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
        admin_edit_start,
        F.data.startswith("adminedit:")
    )

    dp.callback_query.register(
        admin_edit_month,
        F.data.startswith("aeditmonth:")
    )

    dp.callback_query.register(
        admin_edit_day,
        F.data.startswith("aeditday:")
    )

    dp.callback_query.register(
        admin_edit_start_time,
        F.data.startswith("aeditstarttime:")
    )

    dp.callback_query.register(
        admin_edit_end_time,
        F.data.startswith("aeditendtime:")
    )

    dp.callback_query.register(
        admin_edit_backdate,
        F.data.startswith("aeditbackdate:")
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
        admin_action,
        F.data.startswith("cancel:")
    )

    async def noop_handler(
        callback: CallbackQuery
    ):
        """Безопасно подтверждает декоративные callback-кнопки."""

        print(
            f"[NOOP] callback id={callback.id} "
            f"data={callback.data!r}"
        )

        await safe_callback_answer(callback)

    dp.callback_query.register(
        noop_handler,
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

        print(
            f"[WEBHOOK] update_id={update.update_id} "
            f"type={update.event_type} "
            f"received={monotonic_time.monotonic():.3f}"
        )

        # Telegram должен получить HTTP 200 как можно быстрее.
        # Долгая обработка через await dp.feed_update() здесь
        # заставляет Telegram ждать и может привести к повторной
        # доставке одного и того же callback query.
        async def process_update():
            started = monotonic_time.monotonic()

            try:
                await dp.feed_update(
                    bot,
                    update
                )
            except Exception as exc:
                print(
                    f"[WEBHOOK] update_id={update.update_id} "
                    f"ERROR={type(exc).__name__}: {exc}"
                )
            finally:
                print(
                    f"[WEBHOOK] update_id={update.update_id} "
                    f"finished={monotonic_time.monotonic():.3f} "
                    f"duration={monotonic_time.monotonic() - started:.3f}s"
                )

        asyncio.create_task(
            process_update()
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
