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
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
            "photos/solaris21_hero.jpg",
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
            "photos/solaris20_hero.jpg",
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
            "photos/solaris17_hero.jpg",
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
            "photos/i30_hero.jpg",
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
    confirm = State()
    review = State()


class AdminFeature(StatesGroup):
    search = State()
    car_name = State()
    car_rates = State()
    maintenance = State()


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

            cur.execute(
                """
                ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS reminder_24_sent BOOLEAN NOT NULL DEFAULT FALSE
                """
            )

            cur.execute(
                """
                ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS reminder_2_sent BOOLEAN NOT NULL DEFAULT FALSE
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS car_settings (
                    car_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    rate_1_3 INTEGER NOT NULL,
                    rate_4_6 INTEGER NOT NULL,
                    rate_7_plus INTEGER NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    id BIGSERIAL PRIMARY KEY,
                    booking_id BIGINT UNIQUE NOT NULL,
                    user_id BIGINT NOT NULL,
                    car_id TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                    review_text TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS car_maintenance (
                    id BIGSERIAL PRIMARY KEY,
                    car_id TEXT NOT NULL,
                    start_at TIMESTAMPTZ NOT NULL,
                    end_at TIMESTAMPTZ NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_maintenance_car_datetime
                ON car_maintenance(car_id, start_at, end_at)
                """
            )

            for cid, car in CARS.items():
                cur.execute(
                    """
                    INSERT INTO car_settings (car_id, name, active, rate_1_3, rate_4_6, rate_7_plus)
                    VALUES (%s,%s,TRUE,%s,%s,%s)
                    ON CONFLICT (car_id) DO NOTHING
                    """,
                    (cid, car['name'], *car['rates'])
                )
        con.commit()

    finally:

        con.close()


def load_car_settings():
    """Загружает сохранённые настройки автомобилей из PostgreSQL."""
    con = db()
    try:
        with con.cursor() as cur:
            rows = cur.execute("SELECT * FROM car_settings").fetchall()
        for row in rows:
            if row['car_id'] in CARS:
                CARS[row['car_id']]['name'] = row['name']
                CARS[row['car_id']]['rates'] = (row['rate_1_3'], row['rate_4_6'], row['rate_7_plus'])
                CARS[row['car_id']]['active'] = row['active']
    finally:
        con.close()


def active_cars():
    return {cid: car for cid, car in CARS.items() if car.get('active', True)}


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
# MAINTENANCE / CAR AVAILABILITY
# ============================================================

def maintenance_overlaps(car_id, start_at, end_at):
    start_at = ensure_tz(start_at)
    end_at = ensure_tz(end_at)
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                """
                SELECT * FROM car_maintenance
                WHERE car_id=%s AND start_at < %s AND end_at > %s
                ORDER BY start_at LIMIT 1
                """, (car_id, end_at, start_at)
            ).fetchone()
    finally:
        con.close()


def get_month_maintenance(car_id, year, month):
    first = date(year, month, 1)
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    month_start = local_dt(first, time(0,0))
    month_end = local_dt(next_first, time(0,0))
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                """
                SELECT id, 'maintenance' AS status, start_at, end_at, reason
                FROM car_maintenance
                WHERE car_id=%s AND start_at < %s AND end_at > %s
                ORDER BY start_at
                """, (car_id, month_end, month_start)
            ).fetchall()
    finally:
        con.close()


def all_calendar_blocks(car_id, year, month):
    return get_month_bookings(car_id, year, month) + get_month_maintenance(car_id, year, month)

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
    if maintenance_overlaps(car_id, start_at, end_at):
        return {"status": "maintenance"}

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

            row = cur.fetchone()
            print(
                f"[OVERLAP] car={car_id} start={start_at.isoformat()} "
                f"end={end_at.isoformat()} check_start={check_start.isoformat()} "
                f"check_end={check_end.isoformat()} result={bool(row)} row={row!r}"
            )
            return row

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

    if maintenance_overlaps(car_id, start_at, end_at):
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


def interval_overlaps_bookings(start_at, end_at, bookings, exclude_booking_id=None):
    """True when requested interval intersects a booking plus buffer.

    Boundary is open: existing end 09:00 + 2h buffer permits new start 11:00.
    """
    start_at = ensure_tz(start_at)
    end_at = ensure_tz(end_at)
    if end_at <= start_at:
        return True

    check_start = start_at - timedelta(hours=BUFFER_HOURS)
    check_end = end_at + timedelta(hours=BUFFER_HOURS)

    for row in bookings:
        if exclude_booking_id is not None and row["id"] == exclude_booking_id:
            continue
        rs = ensure_tz(row["start_at"])
        re = ensure_tz(row["end_at"])
        if rs < check_end and re > check_start:
            return True
    return False


def day_has_available_pickup(
    current,
    bookings,
    exclude_booking_id=None,
    allow_past=False
):
    """
    День зелёный, если существует хотя бы один допустимый час получения.

    Для клиента прошедшие часы отбрасываются.
    Для администратора allow_past=True позволяет исправлять бронь задним
    числом (например 02.09 -> 01.09).
    """
    now = datetime.now(TZ)

    for hour in range(PICKUP_START_HOUR, PICKUP_END_HOUR + 1):
        start_at = local_dt(current, time(hour, 0))

        if not allow_past and start_at <= now:
            continue

        # Проверяем реальный час получения с буфером.
        # Поэтому день, в котором предыдущая аренда заканчивается в 09:00,
        # остаётся зелёным, если доступно получение в 11:00 и позже.
        end_at = start_at + timedelta(hours=1)

        if not interval_overlaps_bookings(
            start_at,
            end_at,
            bookings,
            exclude_booking_id
        ):
            return True

    return False


def day_has_available_return_date(
    current,
    bookings,
    exclude_booking_id=None
):
    """
    Показывает, есть ли на КОНКРЕТНОЙ дате хотя бы одно доступное
    время возврата автомобиля.

    ВАЖНО: это только визуальная проверка даты календаря.
    Она НЕ проверяет весь будущий интервал от даты получения.

    Например, если уже существует бронь 09.10 -> 20.10, то после
    выбора получения 08.10 календарь возврата должен показывать:

        09-20 -> 🔴
        21 и далее -> 🟢

    При этом попытка реально создать бронь 08.10 -> 21.10
    всё равно будет отклонена финальной проверкой полного интервала
    в end_day()/create_booking_sync().

    Учитывается BUFFER_HOURS: дата считается доступной, если на ней
    существует хотя бы один час, не попадающий в занятый интервал
    с техническим буфером.
    """
    day_start = local_dt(current, time(PICKUP_START_HOUR, 0))
    day_end = local_dt(current, time(PICKUP_END_HOUR, 0))

    for hour in range(PICKUP_START_HOUR, PICKUP_END_HOUR + 1):
        end_at = local_dt(current, time(hour, 0))

        # Для возврата проверяем момент времени небольшим интервалом,
        # чтобы корректно учитывать буфер и границы существующей брони.
        if hour == PICKUP_END_HOUR:
            probe_end = end_at + timedelta(minutes=1)
        else:
            probe_end = end_at + timedelta(minutes=1)

        if end_at < day_start or end_at > day_end:
            continue

        if not interval_overlaps_bookings(
            end_at,
            probe_end,
            bookings,
            exclude_booking_id
        ):
            return True

    return False


def day_has_available_return(
    current,
    start_at,
    bookings,
    exclude_booking_id=None
):
    """
    Полная проверка даты возврата относительно выбранного получения.

    Используется только там, где нужно определить, можно ли реально
    построить интервал start_at -> candidate_end. Для отображения
    календаря используется day_has_available_return_date().
    """
    start_at = ensure_tz(start_at)

    for hour in range(PICKUP_START_HOUR, PICKUP_END_HOUR + 1):
        end_at = local_dt(current, time(hour, 0))

        if end_at <= start_at:
            continue

        if not interval_overlaps_bookings(
            start_at,
            end_at,
            bookings,
            exclude_booking_id
        ):
            return True

    return False

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
            elif row["status"] == "maintenance":
                confirmed = True

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
                InlineKeyboardButton(text="🚗 Автомобили", callback_data="catalog"),
                InlineKeyboardButton(text="📋 Мои бронирования", callback_data="mybookings"),
            ],
            [
                InlineKeyboardButton(text="⭐ Отзывы", callback_data="reviews"),
                InlineKeyboardButton(text="✨ Почему мы", callback_data="why"),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Условия аренды", callback_data="terms"),
                InlineKeyboardButton(text="📞 Связаться", callback_data="contact"),
            ],
        ]
    )


def welcome_text():
    return (
        "🚗 <b>BALTICAR</b> <i>• аренда автомобилей</i>\n\n"
        "📍 <b>Калининград</b>\n"
        "Подберём автомобиль, покажем свободные даты и сразу рассчитаем стоимость.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🛡 Автомобили в отличном состоянии\n"
        "💰 Понятные тарифы без сюрпризов\n"
        "📅 Онлайн-бронирование в Telegram\n"
        "⚡ Быстрое подтверждение заявки\n"
        "☎️ Поддержка 24/7\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Выберите нужный раздел ниже 👇</b>"
    )


def why_text():
    return (
        "✨ <b>Почему выбирают BALTICAR?</b>\n\n"
        "🚗 <b>Автомобили в отличном состоянии</b>\n"
        "Следим за обслуживанием и чистотой автомобилей.\n\n"
        "💰 <b>Понятные тарифы</b>\n"
        "Стоимость зависит от срока аренды и показывается до отправки заявки.\n\n"
        "📅 <b>Удобное бронирование</b>\n"
        "Даты и время выбираются прямо в Telegram.\n\n"
        "⚡ <b>Быстрая обработка</b>\n"
        "После заявки менеджер получает уведомление и связывается с вами.\n\n"
        "🔔 <b>Напоминания</b>\n"
        "Бот заранее напомнит о предстоящей аренде.\n\n"
        "⭐ <b>Отзывы клиентов</b>\n"
        "После завершённой аренды можно оставить оценку и отзыв."
    )


def car_keyboard():
    rows = []
    for cid, car in active_cars().items():
        rows.append([InlineKeyboardButton(
            text=f"🚗 {car['name']}",
            callback_data=f"car:{cid}"
        )])
        rows.append([InlineKeyboardButton(
            text=f"💰 от {money(car['rates'][2])}/сутки   •   {car['gear']}   •   {car['seats']} мест",
            callback_data=f"car:{cid}"
        )])
    rows.append([InlineKeyboardButton(text="⭐ Отзывы", callback_data="reviews")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def car_actions_keyboard(cid):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Забронировать", callback_data=f"pick:{cid}")],
            [InlineKeyboardButton(text="📋 Мои бронирования", callback_data="mybookings")],
            [InlineKeyboardButton(text="◀️ К автомобилям", callback_data="catalog")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
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

    bookings = all_calendar_blocks(
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

        if current < today:
            text = "⚪"
            callback_data = "noop"
        elif day_has_available_pickup(current, bookings):
            # День может содержать другую бронь, но если после неё
            # остаётся хотя бы одно доступное время получения,
            # дату оставляем доступной.
            text = f"🟢{n}"
            callback_data = f"day:{car_id}:{current.isoformat()}"
        else:
            text = f"🔴{n}"
            callback_data = "noop"

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

    bookings = all_calendar_blocks(
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

            # ВАЖНО: цвет даты показывает занятость САМОЙ даты,
            # а не возможность построить весь интервал от выбранного
            # получения до этой даты. Полный интервал проверяется
            # непосредственно при выборе даты/времени возврата.
            if day_has_available_return_date(current, bookings):
                text = f"🟢{n}"
                callback_data = f"end:{car_id}:{current.isoformat()}"
            else:
                text = f"🔴{n}"
                callback_data = "noop"

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
            [
                InlineKeyboardButton(text="🔎 Поиск / фильтр", callback_data="admin:filter"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")
            ],
            [
                InlineKeyboardButton(text="💰 Отчёт по доходам", callback_data="admin:report")
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

    bookings = all_calendar_blocks(
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
        f"🚗 <b>{car['name']}</b>\n"
        f"⚙️ {car['gear']}  •  ⛽ {car['fuel']}  •  👥 {car['seats']} мест\n\n"
        f"{car['description']}\n\n"
        f"💰 <b>Стоимость аренды</b>\n"
        f"1–3 суток — <b>{money(car['rates'][0])}</b>/сутки\n"
        f"4–6 суток — <b>{money(car['rates'][1])}</b>/сутки\n"
        f"7+ суток — <b>{money(car['rates'][2])}</b>/сутки\n\n"
        f"🕐 Выдача и возврат: <b>{PICKUP_START_HOUR:02d}:00–{PICKUP_END_HOUR:02d}:00</b>\n"
        f"🔧 Технический интервал между арендами: <b>{BUFFER_HOURS} ч.</b>\n\n"
        "📅 <i>После выбора периода итоговая сумма будет показана до отправки заявки.</i>"
    )


async def send_car(
    bot,
    chat_id,
    cid
):
    car = CARS[cid]
    existing_photos = [
        photo for photo in car["photos"]
        if os.path.exists(photo)
    ]

    if existing_photos:
        await bot.send_photo(
            chat_id,
            FSInputFile(existing_photos[0]),
            caption=car_text(cid),
            reply_markup=car_actions_keyboard(cid)
        )
        for photo in existing_photos[1:]:
            await bot.send_photo(
                chat_id,
                FSInputFile(photo)
            )
    else:
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
        welcome_text(),
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
        welcome_text(),
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
        "🚗 <b>Автомобили BALTICAR</b>\n\n"
        "Выберите модель — откроется её фотокарточка, характеристики и актуальные тарифы.\n\n"
        "💡 <b>Цена за выбранный период рассчитывается автоматически.</b>",
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

    if cid not in CARS or not CARS[cid].get("active", True):

        await callback.message.answer(
            "Автомобиль сейчас недоступен."
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
    # Финальная DB-проверка выбранного времени получения.
    # ========================================================

    if not await async_available(
        cid,
        start_at,
        start_at + timedelta(hours=1)
    ):
        await callback.message.answer(
            "❌ Это время уже занято с учётом технического интервала.\n\n"
            "Выберите другое время."
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

    # Перед показом времени возврата обязательно проверяем ВЕСЬ
    # интервал от выбранного получения до выбранной даты.
    # Это не позволяет построить период вида 08.10-21.10,
    # если внутри него уже есть бронь 09.10-20.10.
    has_free_return_time = False
    for hour in range(PICKUP_START_HOUR, PICKUP_END_HOUR + 1):
        candidate_end = local_dt(end_d, time(hour, 0))
        if candidate_end <= start_at:
            continue
        if await async_available(cid, start_at, candidate_end):
            has_free_return_time = True
            break

    if not has_free_return_time:
        await callback.message.answer(
            "❌ На выбранную дату возврат невозможен.\n\n"
            "Выбранный период пересекается с другой арендой "
            "или техническим интервалом.\n\n"
            "Выберите другую дату."
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

    if cid not in CARS or not CARS[cid].get("active", True):
        return {"ok": False, "reason": "inactive_car"}

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
        f"💰 <b>Предварительная стоимость: {money(total)}</b>\n\n"
        "Теперь заполните данные — перед отправкой заявки ещё раз покажем полный итог.\n\n"
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

    await state.set_state(Booking.comment)
    await message.answer(
        "🧳 <b>Дополнительные пожелания</b>\n\n"
        "Если нужны дополнительные услуги или особые условия, укажите их ниже. "
        "Стоимость дополнительных услуг, если она потребуется, менеджер согласует отдельно.\n\n"
        "Например: детское кресло, подача автомобиля, пожелания по месту встречи.\n\n"
        "Если ничего не нужно — отправьте «-»."
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

            maintenance = cur.execute(
                """
                SELECT id FROM car_maintenance
                WHERE car_id=%s AND start_at < %s AND end_at > %s
                LIMIT 1
                """, (cid, end_at, start_at)
            ).fetchone()

            if maintenance:
                con.rollback()
                return {"ok": False, "reason": "maintenance"}

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

            print(
                f"[CREATE_OVERLAP] car={cid} start={start_at.isoformat()} "
                f"end={end_at.isoformat()} check_start={check_start.isoformat()} "
                f"check_end={check_end.isoformat()} overlap={overlap!r}"
            )

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

async def comment_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("car_id") or not data.get("start_at") or not data.get("end_at"):
        await state.clear()
        await message.answer("Сессия бронирования устарела.\n\nНачните заново:", reply_markup=main_keyboard())
        return

    comment_text = (message.text or "").strip()
    comment = "" if comment_text == "-" else comment_text
    cid = data["car_id"]
    start_at = ensure_tz(datetime.fromisoformat(data["start_at"]))
    end_at = ensure_tz(datetime.fromisoformat(data["end_at"]))
    days = rental_days(start_at, end_at)
    total = days * rate_for_days(cid, days)

    if not await async_available(cid, start_at, end_at):
        await state.clear()
        await message.answer(
            "❌ <b>Период уже занят</b>\n\nПока вы заполняли данные, выбранное время стало недоступно. Выберите другой период.",
            reply_markup=main_keyboard()
        )
        return

    await state.update_data(comment=comment, days=days, total=total)
    await state.set_state(Booking.confirm)
    await message.answer(
        f"🧾 <b>Проверьте бронирование</b>\n\n"
        f"🚗 <b>{CARS[cid]['name']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📅 Получение: <b>{format_date_time(start_at)}</b>\n"
        f"↩️ Возврат: <b>{format_date_time(end_at)}</b>\n"
        f"⏱ Период: <b>{days} суток</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 {data['name']}\n"
        f"📞 {data['phone']}\n"
        f"📝 Пожелания: {comment or '—'}\n\n"
        f"💰 <b>ИТОГО: {money(total)}</b>\n\n"
        "Проверьте данные перед отправкой заявки.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить заявку", callback_data="booking:confirm")],
            [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="booking:edit")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="booking:abort")],
        ])
    )


async def booking_confirm(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    data = await state.get_data()
    required = ("car_id", "start_at", "end_at", "name", "phone")
    if not all(data.get(k) for k in required):
        await state.clear()
        await callback.message.answer("Сессия бронирования устарела. Начните заново.", reply_markup=main_keyboard())
        return
    cid = data["car_id"]
    start_at = ensure_tz(datetime.fromisoformat(data["start_at"]))
    end_at = ensure_tz(datetime.fromisoformat(data["end_at"]))
    result = await asyncio.to_thread(create_booking_sync, callback.from_user.id, callback.from_user.username or "", cid, start_at, end_at, data["name"], data["phone"], data.get("comment", ""))
    if not result["ok"]:
        await state.clear()
        reason = result.get("reason")
        text = "❌ <b>Не удалось создать заявку.</b>\n\n" + ("Автомобиль уже занят или недостаточно технического интервала." if reason == "overlap" else "Автомобиль недоступен из-за технического обслуживания." if reason == "maintenance" else "Попробуйте выбрать другой период.")
        await callback.message.edit_text(text, reply_markup=main_keyboard())
        return
    bid, days, total, expires = result["bid"], result["days"], result["total"], result["expires"]
    await state.clear()
    await callback.message.edit_text(
        f"📩 <b>Заявка №{bid} отправлена!</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n"
        f"📅 {format_date_time(start_at)} → {format_date_time(end_at)}\n"
        f"⏱ {days} суток\n"
        f"💰 <b>{money(total)}</b>\n\n"
        f"⏳ Заявка удерживается до <b>{expires.strftime('%d.%m.%Y %H:%M')}</b>.\n\n"
        "Мы отправим вам уведомление после решения менеджера.",
        reply_markup=main_keyboard()
    )
    if ADMIN_ID:
        uname = f"@{callback.from_user.username}" if callback.from_user.username else "без username"
        await callback.bot.send_message(ADMIN_ID,
            f"🔔 <b>Новая заявка №{bid}</b>\n\n🚗 {CARS[cid]['name']} ({CARS[cid]['gear']})\n📅 {format_date_time(start_at)} → {format_date_time(end_at)}\n⏱ {days} суток\n💰 <b>{money(total)}</b>\n👤 {data['name']}\n📞 {data['phone']}\nTelegram: {uname}\n📝 {data.get('comment') or '—'}\n\n⏳ Ожидает подтверждения до {expires.strftime('%d.%m.%Y %H:%M')}",
            reply_markup=admin_buttons(bid))


async def booking_edit(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    data = await state.get_data()
    await state.set_state(Booking.name)
    await callback.message.answer("✏️ Введите имя заново:")


async def booking_abort(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    await state.clear()
    await callback.message.edit_text("Бронирование отменено. Вы можете начать заново.", reply_markup=main_keyboard())


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


async def mybookings(callback: CallbackQuery):
    await safe_callback_answer(callback)
    rows = await asyncio.to_thread(get_user_bookings_sync, callback.from_user.id)
    if not rows:
        await callback.message.edit_text("📋 <b>Мои бронирования</b>\n\nУ вас пока нет заявок.", reply_markup=main_keyboard())
        return
    buttons=[]
    for row in rows:
        car_name=CARS.get(row['car_id'],{}).get('name',row['car_id'])
        buttons.append([InlineKeyboardButton(text=f"№{row['id']} · {car_name} · {status_label(row['status'])[:2]}", callback_data=f"mybooking:{row['id']}")])
    buttons.append([InlineKeyboardButton(text="🚗 Новое бронирование", callback_data="catalog")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    await callback.message.edit_text(
        "📋 <b>Мои бронирования</b>\n\n"
        "Здесь хранятся ваши последние заявки.\n"
        "Нажмите на заявку, чтобы открыть детали.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


def get_user_booking_sync(user_id, bid):
    cleanup_pending(); con=db()
    try:
        with con.cursor() as cur:
            return cur.execute("SELECT * FROM bookings WHERE id=%s AND user_id=%s",(bid,user_id)).fetchone()
    finally: con.close()


def user_cancel_booking_sync(user_id,bid):
    con=db()
    try:
        with con.cursor() as cur:
            row=cur.execute("SELECT * FROM bookings WHERE id=%s AND user_id=%s FOR UPDATE",(bid,user_id)).fetchone()
            if not row or row['status'] not in ('pending','confirmed'):
                con.rollback(); return None
            cur.execute("UPDATE bookings SET status='cancelled', expires_at=NULL WHERE id=%s",(bid,)); con.commit(); return row
    finally: con.close()


async def mybooking_detail(callback: CallbackQuery):
    await safe_callback_answer(callback)
    try: bid=int(callback.data.split(':',1)[1])
    except ValueError: return
    row=await asyncio.to_thread(get_user_booking_sync, callback.from_user.id,bid)
    if not row:
        await callback.message.answer("Бронирование не найдено."); return
    start_at=ensure_tz(row['start_at'] or local_dt(row['start_date'],time(10)))
    end_at=ensure_tz(row['end_at'] or local_dt(row['end_date'],time(17)))
    buttons=[]
    if row['status'] in ('pending','confirmed'):
        buttons.append([InlineKeyboardButton(text="🚫 Отменить бронирование", callback_data=f"usercancel:{bid}")])
    if end_at < datetime.now(TZ) and row['status']=='confirmed':
        buttons.append([InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data=f"review:{bid}")])
    buttons.append([InlineKeyboardButton(text="◀️ Мои бронирования", callback_data="mybookings")])
    text=(f"🧾 <b>Бронирование №{bid}</b>\n\n"
          f"🚗 <b>{CARS[row['car_id']]['name']}</b>\n"
          f"━━━━━━━━━━━━━━━━━━\n"
          f"📅 Получение: <b>{format_date_time(start_at)}</b>\n"
          f"↩️ Возврат: <b>{format_date_time(end_at)}</b>\n"
          f"⏱ Период: <b>{rental_days(start_at,end_at)} суток</b>\n"
          f"💰 <b>{money(row['total'])}</b>\n"
          f"{status_label(row['status'])}\n"
          f"━━━━━━━━━━━━━━━━━━\n"
          f"👤 {row['name']}\n"
          f"📞 {row['phone']}\n"
          f"📝 Пожелания: {row['comment'] or '—'}")
    await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def user_cancel_booking(callback: CallbackQuery):
    await safe_callback_answer(callback)
    bid=int(callback.data.split(':',1)[1])
    row=await asyncio.to_thread(user_cancel_booking_sync,callback.from_user.id,bid)
    if not row:
        await callback.message.answer("❌ Бронирование уже нельзя отменить."); return
    await callback.message.edit_text(f"🚫 <b>Бронирование №{bid} отменено.</b>\n\nАвтомобиль снова доступен для бронирования.",reply_markup=main_keyboard())
    if ADMIN_ID:
        await callback.bot.send_message(ADMIN_ID,f"🚫 <b>Клиент отменил бронирование №{bid}</b>\n\n🚗 {CARS[row['car_id']]['name']}\n👤 {row['name']}\n📞 {row['phone']}")


def create_review_sync(bid,user_id,rating,text):
    con=db()
    try:
        with con.cursor() as cur:
            row=cur.execute("SELECT * FROM bookings WHERE id=%s AND user_id=%s AND status='confirmed'",(bid,user_id)).fetchone()
            if not row or not row['end_at'] or ensure_tz(row['end_at']) >= datetime.now(TZ): return False
            cur.execute("""INSERT INTO reviews(booking_id,user_id,car_id,rating,review_text) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(booking_id) DO NOTHING""",(bid,user_id,row['car_id'],rating,text))
        con.commit(); return True
    finally: con.close()

async def review_start(callback: CallbackQuery,state:FSMContext):
    await safe_callback_answer(callback)
    bid=int(callback.data.split(':',1)[1])
    await state.update_data(review_bid=bid)
    await state.set_state(Booking.review)
    await callback.message.answer("⭐ <b>Оцените аренду</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{i} ⭐",callback_data=f"rating:{bid}:{i}") for i in range(1,6)]]))

async def rating_handler(callback: CallbackQuery,state:FSMContext):
    await safe_callback_answer(callback)
    _,bid_s,rating_s=callback.data.split(':')
    await state.update_data(review_bid=int(bid_s),rating=int(rating_s))
    await callback.message.answer("✍️ Напишите короткий отзыв или отправьте «-», чтобы оставить только оценку:")

async def review_message(message:Message,state:FSMContext):
    data=await state.get_data(); bid=data.get('review_bid'); rating=data.get('rating')
    if not bid or not rating: await state.clear(); return
    text=(message.text or '').strip(); text='' if text=='-' else text[:1000]
    ok=await asyncio.to_thread(create_review_sync,bid,message.from_user.id,rating,text)
    await state.clear()
    await message.answer("⭐ <b>Спасибо за отзыв!</b>" if ok else "Отзыв уже оставлен или бронирование недоступно для оценки.",reply_markup=main_keyboard())


def get_reviews_sync(limit=12):
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                """
                SELECT r.*, b.name
                FROM reviews r
                LEFT JOIN bookings b ON b.id=r.booking_id
                ORDER BY r.created_at DESC
                LIMIT %s
                """,
                (limit,)
            ).fetchall()
    finally:
        con.close()


async def reviews(callback: CallbackQuery):
    await safe_callback_answer(callback)
    rows = await asyncio.to_thread(get_reviews_sync, 12)

    if not rows:
        text = (
            "⭐ <b>Отзывы клиентов</b>\n\n"
            "Пока отзывов ещё нет.\n"
            "После завершённой аренды вы сможете оставить свою оценку."
        )
    else:
        parts = ["⭐ <b>Отзывы клиентов</b>", ""]
        for row in rows:
            stars = "⭐" * int(row["rating"])
            name = row["name"] or "Клиент"
            review = (row["review_text"] or "Без текста").strip()
            if len(review) > 240:
                review = review[:237] + "..."
            parts.append(f"<b>{name}</b>  {stars}")
            parts.append(review)
            parts.append("")
        text = "\n".join(parts)

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Выбрать автомобиль", callback_data="catalog")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
        ])
    )


async def why(callback: CallbackQuery):
    await safe_callback_answer(callback)
    await callback.message.edit_text(
        why_text(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Выбрать автомобиль", callback_data="catalog")],
            [InlineKeyboardButton(text="⭐ Отзывы", callback_data="reviews")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
        ])
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


def admin_edit_calendar_sync(
    bid,
    car_id,
    year,
    month,
    mode,
    start_at=None
):
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
        if mode == "start":
            available_day = day_has_available_pickup(
                current,
                bookings,
                exclude_booking_id=bid,
                allow_past=True
            )
        else:
            # Для календаря возврата нужен уже выбранный момент получения.
            # Проверяем весь интервал start_at -> candidate_end.
            if start_at is None:
                available_day = False
            else:
                # В календаре возврата показываем фактическую занятость
                # самой даты. Возможность полного интервала start_at -> end_at
                # проверяется уже при выборе времени возврата.
                available_day = day_has_available_return_date(
                    current,
                    bookings,
                    exclude_booking_id=bid
                )

        if available_day:
            text = f"🟢{n}"
            cb = f"aeditday:{bid}:{mode}:{current.isoformat()}"
        else:
            text, cb = f"🔴{n}", "noop"
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


async def admin_edit_calendar(
    bid,
    car_id,
    mode,
    d,
    start_at=None
):
    return await asyncio.to_thread(
        admin_edit_calendar_sync,
        bid,
        car_id,
        d.year,
        d.month,
        mode,
        start_at
    )


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
    start_at = None
    if mode == "end":
        data = await state.get_data()
        start_iso = data.get("edit_start_at")
        if start_iso:
            start_at = ensure_tz(datetime.fromisoformat(start_iso))
    kb = await admin_edit_calendar(
        int(bid_s),
        car_id,
        mode,
        d,
        start_at=start_at
    )
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
    kb=await admin_edit_calendar(
        bid,
        row["car_id"],
        "end",
        d0,
        start_at=start_at
    )
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

    # Используем сумму и количество суток, подтверждённые самой БД.
    days = result["days"]
    total = result["total"]

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


def update_booking_dates_sync(bid, start_at, end_at, total=None):
    """
    Атомарно изменяет период брони.

    ВАЖНО: итоговая сумма всегда пересчитывается здесь, непосредственно
    перед UPDATE, по НОВОМУ количеству оплачиваемых суток. Переданный
    total намеренно не используется как источник истины — это защищает
    от сохранения старого тарифа после сокращения/увеличения брони.
    """
    con=db()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('balticar-bookings'))")
            row=cur.execute("SELECT * FROM bookings WHERE id=%s FOR UPDATE",(bid,)).fetchone()
            if not row:
                con.rollback(); return {"ok":False,"message":"Заявка не найдена."}
            if row["status"] not in ("pending","confirmed"):
                con.rollback(); return {"ok":False,"message":"Заявку нельзя изменить в текущем статусе."}
            if end_at <= start_at:
                con.rollback(); return {"ok":False,"message":"Возврат должен быть позже получения."}

            maintenance = cur.execute(
                """
                SELECT id FROM car_maintenance
                WHERE car_id=%s AND start_at < %s AND end_at > %s
                LIMIT 1
                """, (row["car_id"], end_at, start_at)
            ).fetchone()
            if maintenance:
                con.rollback(); return {"ok":False,"message":"❌ Новый период попадает на обслуживание автомобиля."}

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

            # Пересчитываем тариф исключительно по новому периоду.
            new_days = rental_days(start_at, end_at)
            new_rate = rate_for_days(row["car_id"], new_days)
            new_total = new_days * new_rate

            cur.execute("""
                UPDATE bookings
                SET start_date=%s,end_date=%s,start_at=%s,end_at=%s,total=%s
                WHERE id=%s
            """,(start_at.date(),end_at.date(),start_at,end_at,new_total,bid))
        con.commit()
        return {"ok":True,"days":new_days,"rate":new_rate,"total":new_total}
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
    start_at = None
    if target == "end":
        data = await state.get_data()
        start_iso = data.get("edit_start_at")
        if start_iso:
            start_at = ensure_tz(datetime.fromisoformat(start_iso))
    kb=await admin_edit_calendar(
        bid,
        row["car_id"],
        target,
        d,
        start_at=start_at
    )
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
        f"📋 <b>Бронирование №{bid}</b>\n" f"{status_label(row['status'])}\n\n"
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
        f"📝 {row['comment'] or '—'}"
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
            InlineKeyboardButton(text="◀️ К бронированиям", callback_data="admin:bookings")
        ])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    else:
        buttons = []
        if row["status"] == "cancelled":
            buttons.append([
                InlineKeyboardButton(
                    text="🗑 Удалить отменённую бронь",
                    callback_data=f"deletecancel:{bid}"
                )
            ])
        buttons.append([
            InlineKeyboardButton(
                text="◀️ К бронированиям",
                callback_data="admin:bookings"
            )
        ])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(text, reply_markup=reply_markup)


# ============================================================
# DELETE CANCELLED BOOKING
# ============================================================

def delete_cancelled_booking_sync(bid):

    con = db()

    try:

        with con.cursor() as cur:

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
                return {"ok": False, "reason": "not_found"}

            if row["status"] != "cancelled":
                con.rollback()
                return {
                    "ok": False,
                    "reason": "not_cancelled",
                    "status": row["status"]
                }

            cur.execute(
                """
                DELETE FROM bookings
                WHERE id=%s
                  AND status='cancelled'
                """,
                (bid,)
            )

            con.commit()

            return {"ok": True, "row": row}

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


async def delete_cancelled_booking(callback: CallbackQuery):

    await safe_callback_answer(callback)

    if callback.from_user.id != ADMIN_ID:

        await callback.message.answer(
            "Нет доступа."
        )
        return

    try:
        bid = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer(
            "Некорректный номер брони."
        )
        return

    result = await asyncio.to_thread(
        delete_cancelled_booking_sync,
        bid
    )

    if not result["ok"]:

        if result["reason"] == "not_found":
            await callback.message.answer(
                "Бронь не найдена или уже удалена."
            )
            return

        await callback.message.answer(
            "Удалять можно только отменённые брони."
        )
        return

    # После удаления сразу возвращаемся в обновлённый список.
    rows = await asyncio.to_thread(get_all_bookings_sync)

    if not rows:
        await callback.message.edit_text(
            "📋 <b>Все бронирования</b>\n\n"
            "Бронирований пока нет.",
            reply_markup=admin_back_keyboard()
        )
        return

    keyboard = []
    for row in rows:
        keyboard.append([
            InlineKeyboardButton(
                text=(
                    f"№{row['id']} • "
                    f"{status_label(row['status'])[:2]} • "
                    f"{CARS[row['car_id']]['name'][:22]}"
                ),
                callback_data=f"adminbooking:{row['id']}"
            )
        ])
        if row["status"] == "cancelled":
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑 Удалить бронь №{row['id']}",
                    callback_data=f"deletecancel:{row['id']}"
                )
            ])

    keyboard.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="admin:back"
        )
    ])

    await callback.message.edit_text(
        "📋 <b>Все бронирования</b>\n\n"
        f"Показаны последние {len(rows)} заявок.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )


# ============================================================
# ADMIN ALL BOOKINGS / SEARCH / FILTER
# ============================================================

def booking_short_text(row):
    car = CARS.get(row["car_id"], {"name": row["car_id"]})
    start_at = ensure_tz(row["start_at"])
    end_at = ensure_tz(row["end_at"])
    return (
        f"№{row['id']} • {car['name']}\n"
        f"📅 {start_at.strftime('%d.%m %H:%M')} → {end_at.strftime('%d.%m %H:%M')}\n"
        f"👤 {row['name']} • {money(row['total'])}\n"
        f"{status_label(row['status'])}"
    )


def get_all_bookings_sync(status=None, car_id=None, query=None, limit=50):
    cleanup_pending()
    con = db()
    try:
        with con.cursor() as cur:
            sql = "SELECT * FROM bookings WHERE 1=1"
            params=[]
            if status:
                sql += " AND status=%s"; params.append(status)
            if car_id:
                sql += " AND car_id=%s"; params.append(car_id)
            if query:
                sql += " AND (name ILIKE %s OR phone ILIKE %s OR COALESCE(username,'') ILIKE %s)"
                q=f"%{query}%"; params += [q,q,q]
            sql += " ORDER BY start_at DESC, id DESC LIMIT %s"; params.append(limit)
            return cur.execute(sql, params).fetchall()
    finally:
        con.close()


def admin_bookings_markup(rows, show_delete=True):
    keyboard=[]
    for row in rows:
        keyboard.append([InlineKeyboardButton(text=f"№{row['id']} • {CARS[row['car_id']]['name'][:18]} • {status_label(row['status'])[:2]}", callback_data=f"adminbooking:{row['id']}")])
        if show_delete and row['status']=='cancelled':
            keyboard.append([InlineKeyboardButton(text=f"🗑 Удалить №{row['id']}", callback_data=f"deletecancel:{row['id']}")])
    return keyboard


async def admin_bookings(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.message.answer("Нет доступа."); return
    rows=await asyncio.to_thread(get_all_bookings_sync)
    if not rows:
        await callback.message.edit_text("📋 <b>Все бронирования</b>\n\nБронирований пока нет.", reply_markup=admin_back_keyboard()); return
    keyboard=admin_bookings_markup(rows)
    keyboard.append([InlineKeyboardButton(text="🔎 Фильтр / поиск", callback_data="admin:filter")])
    keyboard.append([InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin:back")])
    await callback.message.edit_text("📋 <b>Все бронирования</b>\n\nПоказываю последние 50 заявок. Нажмите на бронь для подробностей.", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


async def admin_filter(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    rows=[[InlineKeyboardButton(text="📋 Все", callback_data="af:all")],
          [InlineKeyboardButton(text="🟡 Новые", callback_data="af:pending"), InlineKeyboardButton(text="🟢 Подтверждённые", callback_data="af:confirmed")],
          [InlineKeyboardButton(text="⚫ Отменённые", callback_data="af:cancelled"), InlineKeyboardButton(text="🔴 Отклонённые", callback_data="af:rejected")]]
    for cid,car in CARS.items():
        rows.append([InlineKeyboardButton(text=f"🚗 {car['name']}", callback_data=f"afcar:{cid}")])
    rows.append([InlineKeyboardButton(text="🔍 Найти по имени/телефону", callback_data="af:search")])
    rows.append([InlineKeyboardButton(text="◀️ К бронированиям", callback_data="admin:bookings")])
    await callback.message.edit_text("🔎 <b>Поиск и фильтр броней</b>\n\nВыберите нужный фильтр:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def admin_filter_result(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    parts=callback.data.split(":",1)
    value=parts[1] if len(parts)>1 else "all"
    status=value if value in {"pending","confirmed","cancelled","rejected"} else None
    car_id=value if value in CARS else None
    rows=await asyncio.to_thread(get_all_bookings_sync,status,car_id,None,50)
    title="📋 Результат фильтра"
    keyboard=admin_bookings_markup(rows)
    keyboard.append([InlineKeyboardButton(text="🔎 Другой фильтр", callback_data="admin:filter")])
    keyboard.append([InlineKeyboardButton(text="◀️ К бронированиям", callback_data="admin:bookings")])
    await callback.message.edit_text(f"{title}\n\nНайдено: <b>{len(rows)}</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


async def admin_search_start(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminFeature.search)
    await callback.message.edit_text("🔍 <b>Поиск брони</b>\n\nВведите имя клиента, телефон или username:")


async def admin_search_message(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    q=(message.text or "").strip()
    if not q: return
    await state.clear()
    rows=await asyncio.to_thread(get_all_bookings_sync,None,None,q,50)
    keyboard=admin_bookings_markup(rows)
    keyboard.append([InlineKeyboardButton(text="🔎 Другой поиск", callback_data="admin:filter")])
    keyboard.append([InlineKeyboardButton(text="◀️ К бронированиям", callback_data="admin:bookings")])
    await message.answer(f"🔍 <b>Поиск: {q}</b>\n\nНайдено: <b>{len(rows)}</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

# ============================================================
# ADMIN CARS / SETTINGS / MAINTENANCE
# ============================================================

async def admin_cars(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    rows=[]
    for cid,car in CARS.items():
        state="🟢 включён" if car.get('active',True) else "⚪ выключен"
        rows.append([InlineKeyboardButton(text=f"🚗 {car['name']} — {state}", callback_data=f"admincarinfo:{cid}")])
    rows.append([InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin:back")])
    await callback.message.edit_text("🚗 <b>Управление автомобилями</b>\n\nВыберите автомобиль:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def car_settings_sync(cid, **kwargs):
    con=db()
    try:
        with con.cursor() as cur:
            sets=[]; vals=[]
            for k,v in kwargs.items(): sets.append(f"{k}=%s"); vals.append(v)
            vals.append(cid)
            cur.execute(f"UPDATE car_settings SET {', '.join(sets)}, updated_at=NOW() WHERE car_id=%s",vals)
        con.commit()
    finally: con.close()
    load_car_settings()


def add_maintenance_sync(cid,start_at,end_at,reason):
    con=db()
    try:
        with con.cursor() as cur:
            row=cur.execute("INSERT INTO car_maintenance(car_id,start_at,end_at,reason) VALUES(%s,%s,%s,%s) RETURNING *",(cid,start_at,end_at,reason)).fetchone()
        con.commit(); return row
    finally: con.close()


def get_maintenance_sync(cid=None):
    con=db()
    try:
        with con.cursor() as cur:
            if cid:
                return cur.execute("SELECT * FROM car_maintenance WHERE car_id=%s ORDER BY start_at",(cid,)).fetchall()
            return cur.execute("SELECT * FROM car_maintenance ORDER BY start_at").fetchall()
    finally: con.close()


def delete_maintenance_sync(mid):
    con=db()
    try:
        with con.cursor() as cur: cur.execute("DELETE FROM car_maintenance WHERE id=%s",(mid,))
        con.commit()
    finally: con.close()


async def admin_car_info(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID: return
    cid=callback.data.split(":",1)[1]
    if cid not in CARS: return
    car=CARS[cid]; active=car.get('active',True)
    maint=await asyncio.to_thread(get_maintenance_sync,cid)
    mt="\n".join(f"• {format_date_time(x['start_at'])} → {format_date_time(x['end_at'])} — {x['reason'] or 'обслуживание'}" for x in maint[:10]) or "нет"
    text=(f"🚗 <b>{car['name']}</b>\n\n{car_text(cid)}\n\n"
          f"Статус: {'🟢 доступен' if active else '⚪ отключён'}\n\n"
          f"🔧 <b>Обслуживание</b>\n{mt}")
    buttons=[
        [InlineKeyboardButton(text="⛔ Отключить" if active else "🟢 Включить",callback_data=f"cartoggle:{cid}")],
        [InlineKeyboardButton(text="💰 Изменить тарифы",callback_data=f"carrates:{cid}")],
        [InlineKeyboardButton(text="✏️ Изменить название",callback_data=f"carname:{cid}")],
        [InlineKeyboardButton(text="🔧 Добавить обслуживание",callback_data=f"maintadd:{cid}")],
    ]
    for x in maint[:5]: buttons.append([InlineKeyboardButton(text=f"🗑 Удалить ТО №{x['id']}",callback_data=f"maintdel:{x['id']}:{cid}")])
    buttons += [[InlineKeyboardButton(text="◀️ К автомобилям",callback_data="admin:cars")]]
    await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def car_toggle(callback: CallbackQuery):
    await safe_callback_answer(callback)
    cid=callback.data.split(":",1)[1]
    if callback.from_user.id != ADMIN_ID or cid not in CARS:return
    new=not CARS[cid].get('active',True)
    await asyncio.to_thread(car_settings_sync,cid,active=new)
    await admin_car_info(callback)


async def car_rates_start(callback: CallbackQuery,state:FSMContext):
    await safe_callback_answer(callback)
    cid=callback.data.split(":",1)[1]
    if callback.from_user.id != ADMIN_ID:return
    await state.update_data(car_id=cid); await state.set_state(AdminFeature.car_rates)
    await callback.message.edit_text(f"💰 <b>Тарифы: {CARS[cid]['name']}</b>\n\nВведите три числа через пробел:\n<b>1–3 / 4–6 / 7+ суток</b>\nНапример: 2700 2600 2500")


async def car_rates_message(message:Message,state:FSMContext):
    if message.from_user.id != ADMIN_ID:return
    data=await state.get_data(); cid=data['car_id']
    try: vals=tuple(int(x) for x in (message.text or '').split())
    except ValueError: vals=()
    if len(vals)!=3 or any(x<=0 for x in vals):
        await message.answer("Нужно ровно три положительных числа, например: 2700 2600 2500"); return
    await asyncio.to_thread(car_settings_sync,cid,rate_1_3=vals[0],rate_4_6=vals[1],rate_7_plus=vals[2])
    await state.clear(); await message.answer("✅ Тарифы сохранены.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚗 К автомобилю",callback_data=f"admincarinfo:{cid}")]]))


async def car_name_start(callback:CallbackQuery,state:FSMContext):
    await safe_callback_answer(callback); cid=callback.data.split(":",1)[1]
    if callback.from_user.id != ADMIN_ID:return
    await state.update_data(car_id=cid); await state.set_state(AdminFeature.car_name)
    await callback.message.edit_text("✏️ Введите новое название автомобиля:")


async def car_name_message(message:Message,state:FSMContext):
    if message.from_user.id != ADMIN_ID:return
    data=await state.get_data(); cid=data['car_id']; name=(message.text or '').strip()
    if not name: await message.answer("Название не должно быть пустым."); return
    await asyncio.to_thread(car_settings_sync,cid,name=name); await state.clear()
    await message.answer("✅ Название сохранено.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚗 К автомобилю",callback_data=f"admincarinfo:{cid}")]]))


async def maintenance_start(callback:CallbackQuery,state:FSMContext):
    await safe_callback_answer(callback); cid=callback.data.split(":",1)[1]
    if callback.from_user.id != ADMIN_ID:return
    await state.update_data(car_id=cid); await state.set_state(AdminFeature.maintenance)
    await callback.message.edit_text("🔧 <b>Обслуживание</b>\n\nВведите период в формате:\n<b>ДД.ММ.ГГГГ ЧЧ:ММ — ДД.ММ.ГГГГ ЧЧ:ММ | причина</b>\n\nНапример:\n03.09.2026 09:00 — 04.09.2026 18:00 | ТО и замена масла")


async def maintenance_message(message:Message,state:FSMContext):
    if message.from_user.id != ADMIN_ID:return
    data=await state.get_data(); cid=data['car_id']; raw=(message.text or '').strip()
    try:
        period,reason=(raw.split('|',1)+[''])[:2]
        a,b=period.split('—')
        fmt='%d.%m.%Y %H:%M'
        start_at=datetime.strptime(a.strip(),fmt).replace(tzinfo=TZ); end_at=datetime.strptime(b.strip(),fmt).replace(tzinfo=TZ)
        if end_at<=start_at: raise ValueError
    except Exception:
        await message.answer("Не удалось распознать период. Пример: 03.09.2026 09:00 — 04.09.2026 18:00 | ТО"); return
    await asyncio.to_thread(add_maintenance_sync,cid,start_at,end_at,reason.strip() or 'обслуживание')
    await state.clear(); await message.answer("✅ Автомобиль заблокирован на обслуживание.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚗 К автомобилю",callback_data=f"admincarinfo:{cid}")]]))


async def maintenance_delete(callback:CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:return
    _,mid,cid=callback.data.split(":")
    await asyncio.to_thread(delete_maintenance_sync,int(mid)); await admin_car_info(callback)

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

                maintenance = cur.execute(
                    """
                    SELECT id FROM car_maintenance
                    WHERE car_id=%s AND start_at < %s AND end_at > %s
                    LIMIT 1
                    """, (row["car_id"], end_at, start_at)
                ).fetchone()
                if maintenance:
                    cur.execute("UPDATE bookings SET status='rejected' WHERE id=%s", (bid,))
                    con.commit()
                    return {"ok":False,"reason":"maintenance","row":row,"start_at":start_at,"end_at":end_at}

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

        if result["reason"] == "maintenance":
            await callback.message.edit_reply_markup(reply_markup=None)
            row=result["row"]
            await callback.bot.send_message(row["user_id"], f"❌ <b>Заявка №{bid} отклонена</b>\n\nАвтомобиль назначен на техническое обслуживание в выбранный период.", reply_markup=main_keyboard())
            await callback.message.answer("Бронь попала на период обслуживания автомобиля и отклонена.")
            return

        if result["reason"] == "inactive_car":
            await callback.message.answer("Автомобиль сейчас отключён в настройках.")
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
# STATISTICS / INCOME REPORTS
# ============================================================

def stats_sync():
    con=db()
    try:
        with con.cursor() as cur:
            rows=cur.execute("""
                SELECT car_id,
                       COUNT(*) FILTER (WHERE status='confirmed') AS confirmed_count,
                       COALESCE(SUM(total) FILTER (WHERE status='confirmed'),0) AS revenue,
                       COALESCE(SUM(GREATEST(0, EXTRACT(EPOCH FROM (end_at-start_at))/86400.0)) FILTER (WHERE status='confirmed'),0) AS rental_days_raw
                FROM bookings GROUP BY car_id ORDER BY car_id
            """).fetchall()
            return rows
    finally: con.close()


def income_sync(period):
    con=db()
    try:
        with con.cursor() as cur:
            if period=='month':
                where="status='confirmed' AND date_trunc('month', start_at)=date_trunc('month', NOW())"
            elif period=='prev':
                where="status='confirmed' AND date_trunc('month', start_at)=date_trunc('month', NOW() - interval '1 month')"
            else:
                where="status='confirmed'"
            return cur.execute(f"""
                SELECT COUNT(*) AS cnt, COALESCE(SUM(total),0) AS revenue,
                       COUNT(DISTINCT car_id) AS cars
                FROM bookings WHERE {where}
            """).fetchone()
    finally: con.close()


async def admin_stats(callback:CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:return
    rows=await asyncio.to_thread(stats_sync)
    lines=["📊 <b>Статистика по автомобилям</b>",""]
    total_rev=0; total_cnt=0
    for r in rows:
        car=CARS.get(r['car_id'],{'name':r['car_id']}); rev=int(r['revenue'] or 0); cnt=int(r['confirmed_count'] or 0)
        total_rev+=rev; total_cnt+=cnt
        lines.append(f"🚗 <b>{car['name']}</b>\n   Броней: {cnt} • Доход: {money(rev)}")
    lines += ["",f"📌 Всего подтверждено: <b>{total_cnt}</b>",f"💰 Общий доход: <b>{money(total_rev)}</b>"]
    await callback.message.edit_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В админ-панель",callback_data="admin:back")]]))


async def admin_report(callback:CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:return
    rows=[[InlineKeyboardButton(text="📅 Текущий месяц",callback_data="report:month")],[InlineKeyboardButton(text="◀️ Предыдущий месяц",callback_data="report:prev")],[InlineKeyboardButton(text="📚 За всё время",callback_data="report:all")],[InlineKeyboardButton(text="◀️ В админ-панель",callback_data="admin:back")]]
    await callback.message.edit_text("💰 <b>Отчёт по доходам</b>\n\nВыберите период:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def report_result(callback:CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:return
    period=callback.data.split(":",1)[1]; row=await asyncio.to_thread(income_sync,period)
    label={'month':'текущий месяц','prev':'предыдущий месяц','all':'всё время'}[period]
    await callback.message.edit_text(f"💰 <b>Доход — {label}</b>\n\n🚗 Автомобилей в аренде: <b>{row['cars']}</b>\n📋 Подтверждённых броней: <b>{row['cnt']}</b>\n💵 Выручка: <b>{money(int(row['revenue'] or 0))}</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К отчётам",callback_data="admin:report")]]))


# ============================================================
# REMINDERS
# ============================================================

def reminder_candidates_sync():
    cleanup_pending()
    con=db()
    try:
        with con.cursor() as cur:
            rows=cur.execute("""
                SELECT * FROM bookings
                WHERE status='confirmed'
                  AND ((start_at > NOW() AND start_at <= NOW()+interval '2 hours' AND reminder_2_sent=FALSE)
                    OR (start_at > NOW()+interval '2 hours' AND start_at <= NOW()+interval '24 hours' AND reminder_24_sent=FALSE))
                ORDER BY start_at
            """).fetchall()
            return rows
    finally: con.close()


def mark_reminder_sync(bid,kind):
    con=db()
    try:
        with con.cursor() as cur:
            cur.execute(f"UPDATE bookings SET reminder_{kind}_sent=TRUE WHERE id=%s",(bid,))
        con.commit()
    finally: con.close()


async def reminder_loop(bot):
    while True:
        try:
            rows=await asyncio.to_thread(reminder_candidates_sync)
            now=datetime.now(TZ)
            for row in rows:
                start_at=ensure_tz(row['start_at'])
                delta=start_at-now
                if delta <= timedelta(hours=2):
                    kind='2'; text=(f"⏰ <b>Напоминание о бронировании №{row['id']}</b>\n\n🚗 {CARS[row['car_id']]['name']}\n📅 Получение: <b>{format_date_time(start_at)}</b>\n↩️ Возврат: <b>{format_date_time(row['end_at'])}</b>\n\nДо начала аренды осталось около 2 часов.")
                elif delta <= timedelta(hours=24):
                    kind='24'; text=(f"🔔 <b>Напоминание о бронировании №{row['id']}</b>\n\n🚗 {CARS[row['car_id']]['name']}\n📅 Получение: <b>{format_date_time(start_at)}</b>\n↩️ Возврат: <b>{format_date_time(row['end_at'])}</b>\n💰 {money(row['total'])}\n\nДо начала аренды менее 24 часов.")
                else: continue
                try:
                    await bot.send_message(row['user_id'],text,reply_markup=main_keyboard())
                    await asyncio.to_thread(mark_reminder_sync,row['id'],kind)
                except Exception as exc:
                    print(f"[REMINDER] booking={row['id']} ERROR={type(exc).__name__}: {exc}")
        except Exception as exc:
            print(f"[REMINDER LOOP] ERROR={type(exc).__name__}: {exc}")
        await asyncio.sleep(300)

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
    await asyncio.to_thread(load_car_settings)

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

    asyncio.create_task(reminder_loop(bot))

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
        reviews,
        F.data == "reviews"
    )

    dp.callback_query.register(
        why,
        F.data == "why"
    )

    dp.callback_query.register(
        terms,
        F.data == "terms"
    )

    dp.callback_query.register(
        contact,
        F.data == "contact"
    )

    dp.callback_query.register(booking_confirm, F.data == "booking:confirm")
    dp.callback_query.register(booking_edit, F.data == "booking:edit")
    dp.callback_query.register(booking_abort, F.data == "booking:abort")
    dp.callback_query.register(mybooking_detail, F.data.startswith("mybooking:"))
    dp.callback_query.register(user_cancel_booking, F.data.startswith("usercancel:"))
    dp.callback_query.register(review_start, F.data.startswith("review:"))
    dp.callback_query.register(rating_handler, F.data.startswith("rating:"))

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
        delete_cancelled_booking,
        F.data.startswith("deletecancel:")
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("cancel:")
    )
    dp.callback_query.register(admin_filter, F.data == "admin:filter")
    dp.callback_query.register(admin_stats, F.data == "admin:stats")
    dp.callback_query.register(admin_report, F.data == "admin:report")
    dp.callback_query.register(admin_search_start, F.data == "af:search")
    dp.callback_query.register(admin_filter_result, F.data.startswith("af:"))
    dp.callback_query.register(admin_filter_result, F.data.startswith("afcar:"))
    dp.callback_query.register(report_result, F.data.startswith("report:"))
    dp.callback_query.register(car_toggle, F.data.startswith("cartoggle:"))
    dp.callback_query.register(car_rates_start, F.data.startswith("carrates:"))
    dp.callback_query.register(car_name_start, F.data.startswith("carname:"))
    dp.callback_query.register(maintenance_start, F.data.startswith("maintadd:"))
    dp.callback_query.register(maintenance_delete, F.data.startswith("maintdel:"))

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

    dp.message.register(admin_search_message, AdminFeature.search)
    dp.message.register(car_rates_message, AdminFeature.car_rates)
    dp.message.register(car_name_message, AdminFeature.car_name)
    dp.message.register(maintenance_message, AdminFeature.maintenance)

    dp.message.register(review_message, Booking.review)

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
