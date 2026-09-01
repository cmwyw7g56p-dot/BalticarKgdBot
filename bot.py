import asyncio
import os
import secrets
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

import asyncpg
from dotenv import load_dotenv
from aiohttp import web

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
    Update,
)


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
except ValueError:
    ADMIN_ID = 0

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "@Balticar_kgd",
).strip()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "",
).strip()

TIMEZONE = os.getenv(
    "TIMEZONE",
    "Europe/Kaliningrad",
).strip()

try:
    TZ = ZoneInfo(TIMEZONE)
except Exception:
    print(
        f"WARNING: некорректный TIMEZONE={TIMEZONE}. "
        "Используется Europe/Kaliningrad."
    )
    TIMEZONE = "Europe/Kaliningrad"
    TZ = ZoneInfo(TIMEZONE)

try:
    PORT = int(
        os.getenv("PORT", "10000")
    )
except ValueError:
    PORT = 10000

WEBHOOK_PATH = os.getenv(
    "WEBHOOK_PATH",
    "/telegram/webhook",
).strip()

if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = "/" + WEBHOOK_PATH

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    "",
).strip()

try:
    HOLD_MINUTES = int(
        os.getenv(
            "PENDING_HOLD_MINUTES",
            "60",
        )
    )
except ValueError:
    HOLD_MINUTES = 60

try:
    PICKUP_START_HOUR = int(
        os.getenv(
            "PICKUP_START_HOUR",
            "8",
        )
    )
except ValueError:
    PICKUP_START_HOUR = 8

try:
    PICKUP_END_HOUR = int(
        os.getenv(
            "PICKUP_END_HOUR",
            "20",
        )
    )
except ValueError:
    PICKUP_END_HOUR = 20

try:
    TIME_STEP_MINUTES = int(
        os.getenv(
            "TIME_STEP_MINUTES",
            "30",
        )
    )
except ValueError:
    TIME_STEP_MINUTES = 30


# ============================================================
# VALIDATE CONFIG
# ============================================================

if PICKUP_START_HOUR < 0:
    PICKUP_START_HOUR = 0

if PICKUP_START_HOUR > 23:
    PICKUP_START_HOUR = 23

if PICKUP_END_HOUR < 0:
    PICKUP_END_HOUR = 0

if PICKUP_END_HOUR > 23:
    PICKUP_END_HOUR = 23

if PICKUP_END_HOUR < PICKUP_START_HOUR:
    PICKUP_END_HOUR = PICKUP_START_HOUR

if TIME_STEP_MINUTES <= 0:
    TIME_STEP_MINUTES = 30

if HOLD_MINUTES <= 0:
    HOLD_MINUTES = 60


# ============================================================
# CARS
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
    },

    "solaris20": {
        "name": "Hyundai Solaris 2020",
        "gear": "АКПП",
        "rates": (2700, 2600, 2500),
        "photos": [
            "photos/solaris_2020.jpeg",
        ],
    },

    "solaris17": {
        "name": "Hyundai Solaris 2017",
        "gear": "АКПП",
        "rates": (2400, 2300, 2200),
        "photos": [
            "photos/solaris_2017_1.webp",
            "photos/solaris_2017_2.jpeg",
        ],
    },

    "i30": {
        "name": "Hyundai i30 2014",
        "gear": "МКПП",
        "rates": (2300, 2200, 2100),
        "photos": [
            "photos/i30_2014.png",
        ],
    },
}


# ============================================================
# FSM
# ============================================================

class Booking(StatesGroup):
    start_date = State()
    start_time = State()
    end_date = State()
    end_time = State()
    name = State()
    phone = State()
    comment = State()


# ============================================================
# DATABASE
# ============================================================

db_pool: asyncpg.Pool | None = None


async def init_db():
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL не задан. "
            "Добавьте DATABASE_URL от Neon "
            "в Environment Variables Render."
        )

    try:
        db_pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )

        async with db_pool.acquire() as con:
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS bookings (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    car_id TEXT NOT NULL,
                    start_at TIMESTAMPTZ NOT NULL,
                    end_at TIMESTAMPTZ NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    comment TEXT,
                    total BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ
                )
                """
            )

            await con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_bookings_car_period
                ON bookings (car_id, start_at, end_at)
                """
            )

            await con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_bookings_user
                ON bookings (user_id)
                """
            )

            await con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_bookings_status
                ON bookings (status)
                """
            )

        print(
            "Neon PostgreSQL connected successfully."
        )

    except Exception:
        if db_pool is not None:
            await db_pool.close()
            db_pool = None
        raise


async def close_db():
    global db_pool

    if db_pool is not None:
        try:
            await db_pool.close()
        except Exception as e:
            print(
                f"Database close error: {e}"
            )
        finally:
            db_pool = None


async def cleanup_pending():
    if db_pool is None:
        return

    async with db_pool.acquire() as con:
        await con.execute(
            """
            UPDATE bookings
            SET status = 'expired'
            WHERE status = 'pending'
              AND expires_at IS NOT NULL
              AND expires_at < NOW()
            """
        )


async def available(
    car_id: str,
    start_at: datetime,
    end_at: datetime,
    exclude_booking_id: int | None = None,
) -> bool:

    if db_pool is None:
        return False

    start_at = ensure_tz(start_at)
    end_at = ensure_tz(end_at)

    if end_at <= start_at:
        return False

    await cleanup_pending()

    query = """
        SELECT id
        FROM bookings
        WHERE car_id = $1
          AND status IN ('pending', 'confirmed')
          AND start_at < $3
          AND end_at > $2
    """

    params = [
        car_id,
        start_at,
        end_at,
    ]

    if exclude_booking_id is not None:
        query += " AND id <> $4"
        params.append(exclude_booking_id)

    query += " LIMIT 1"

    async with db_pool.acquire() as con:
        row = await con.fetchrow(
            query,
            *params,
        )

    return row is None


async def create_booking_atomic(
    user_id: int,
    username: str,
    car_id: str,
    start_at: datetime,
    end_at: datetime,
    name: str,
    phone: str,
    comment: str,
    total: int,
    expires_at: datetime,
) -> tuple[int | None, bool]:

    """
    Атомарное создание заявки.

    Возвращает:

        (booking_id, True)
            если заявка создана.

        (None, False)
            если период уже занят.

    Для защиты от ситуации, когда два клиента одновременно
    пытаются забронировать один автомобиль, используется
    PostgreSQL advisory transaction lock по car_id.
    """

    if db_pool is None:
        raise RuntimeError(
            "Database pool is not initialized."
        )

    start_at = ensure_tz(start_at)
    end_at = ensure_tz(end_at)
    expires_at = ensure_tz(expires_at)

    async with db_pool.acquire() as con:

        async with con.transaction():

            # ------------------------------------------------
            # Блокируем операции бронирования этого автомобиля
            # внутри текущей PostgreSQL-транзакции.
            # ------------------------------------------------

            await con.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtext($1)
                )
                """,
                car_id,
            )

            # ------------------------------------------------
            # Удаляем просроченные pending.
            # ------------------------------------------------

            await con.execute(
                """
                UPDATE bookings
                SET status = 'expired'
                WHERE status = 'pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < NOW()
                """
            )

            # ------------------------------------------------
            # Проверяем пересечение.
            # ------------------------------------------------

            conflict = await con.fetchrow(
                """
                SELECT id
                FROM bookings
                WHERE car_id = $1
                  AND status IN ('pending', 'confirmed')
                  AND start_at < $3
                  AND end_at > $2
                LIMIT 1
                """,
                car_id,
                start_at,
                end_at,
            )

            if conflict:
                return None, False

            # ------------------------------------------------
            # Создаём заявку.
            # ------------------------------------------------

            row = await con.fetchrow(
                """
                INSERT INTO bookings
                (
                    user_id,
                    username,
                    car_id,
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
                VALUES
                (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    'pending',
                    NOW(),
                    $10
                )
                RETURNING id
                """,
                user_id,
                username,
                car_id,
                start_at,
                end_at,
                name,
                phone,
                comment,
                total,
                expires_at,
            )

            return int(row["id"]), True


# ============================================================
# HELPERS
# ============================================================

def ensure_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)

    return value.astimezone(TZ)


def local_dt(
    d: date,
    t: time,
) -> datetime:

    return datetime(
        d.year,
        d.month,
        d.day,
        t.hour,
        t.minute,
        tzinfo=TZ,
    )


def format_date_time(
    value: datetime,
) -> str:

    value = ensure_tz(value)

    return value.strftime(
        "%d.%m.%Y %H:%M"
    )


def money(
    value: int,
) -> str:

    return (
        f"{value:,}".replace(",", " ")
        + " ₽"
    )


def rental_days(
    start_at: datetime,
    end_at: datetime,
) -> int:

    start_at = ensure_tz(start_at)
    end_at = ensure_tz(end_at)

    seconds = (
        end_at - start_at
    ).total_seconds()

    if seconds <= 0:
        return 0

    days = int(
        seconds // 86400
    )

    if seconds % 86400:
        days += 1

    return max(days, 1)


def rate_for_days(
    car_id: str,
    days: int,
) -> int:

    if car_id not in CARS:
        raise ValueError(
            "Unknown car_id"
        )

    rates = CARS[car_id]["rates"]

    if days <= 3:
        return rates[0]

    if days <= 6:
        return rates[1]

    return rates[2]


def generate_time_values():

    values = []

    current = PICKUP_START_HOUR * 60
    end = PICKUP_END_HOUR * 60

    while current <= end:

        hour = current // 60
        minute = current % 60

        values.append(
            time(
                hour,
                minute,
            )
        )

        current += TIME_STEP_MINUTES

    return values


def is_valid_time(
    t: time,
) -> bool:

    minutes = (
        t.hour * 60
        + t.minute
    )

    return (
        PICKUP_START_HOUR * 60
        <= minutes
        <= PICKUP_END_HOUR * 60
    )


def parse_callback_time(
    callback_data: str,
    prefix: str,
):
    """
    Разбирает callback:

        picktime:car:YYYY-MM-DD:HH:MM

    и

        endtime:car:YYYY-MM-DD:HH:MM

    """

    parts = callback_data.split(":")

    if len(parts) != 5:
        return None

    if parts[0] != prefix:
        return None

    cid = parts[1]
    date_iso = parts[2]
    hour_s = parts[3]
    minute_s = parts[4]

    if cid not in CARS:
        return None

    try:
        selected_date = date.fromisoformat(
            date_iso
        )

        hour = int(hour_s)
        minute = int(minute_s)

        selected_time = time(
            hour,
            minute,
        )

    except (ValueError, TypeError):
        return None

    return (
        cid,
        date_iso,
        selected_date,
        selected_time,
    )


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 Забронировать автомобиль",
                    callback_data="catalog",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Мои заявки",
                    callback_data="mybookings",
                )
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Условия",
                    callback_data="terms",
                )
            ],
        ]
    )


# ============================================================
# CAR KEYBOARD
# ============================================================

def car_keyboard():

    rows = []

    for cid, car in CARS.items():

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🚗 {car['name']}",
                    callback_data=f"car:{cid}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Главное меню",
                callback_data="home",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# CAR TEXT
# ============================================================

def car_text(
    cid: str,
):

    car = CARS[cid]

    return (
        f"<b>{car['name']}</b>\n"
        f"⚙️ {car['gear']}\n\n"
        f"💰 1–3 суток: "
        f"<b>{money(car['rates'][0])}/сутки</b>\n"
        f"💰 4–6 суток: "
        f"<b>{money(car['rates'][1])}/сутки</b>\n"
        f"💰 7+ суток: "
        f"<b>{money(car['rates'][2])}/сутки</b>"
    )


# ============================================================
# SEND CAR
# ============================================================

async def send_car(
    bot: Bot,
    chat_id: int,
    cid: str,
):

    if cid not in CARS:
        await bot.send_message(
            chat_id,
            "❌ Автомобиль не найден.",
        )
        return

    car = CARS[cid]

    photos_sent = False

    for photo in car["photos"]:

        if os.path.exists(photo):

            try:
                await bot.send_photo(
                    chat_id,
                    FSInputFile(photo),
                )

                photos_sent = True

            except Exception as e:

                print(
                    f"Ошибка отправки фото "
                    f"{photo}: {e}"
                )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Выбрать даты",
                    callback_data=f"pick:{cid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ К автомобилям",
                    callback_data="catalog",
                )
            ],
        ]
    )

    await bot.send_message(
        chat_id,
        car_text(cid),
        reply_markup=keyboard,
    )

    if not photos_sent:
        print(
            f"Фото для {cid} не найдены "
            f"или не отправились."
        )


# ============================================================
# START
# ============================================================

async def start_handler(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        "🚗 <b>Balticar</b>\n\n"
        "Аренда автомобилей в Калининграде.\n"
        "Выберите действие:",
        reply_markup=main_keyboard(),
    )


# ============================================================
# CANCEL
# ============================================================

async def cancel_handler(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        "❌ Текущее оформление отменено.\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard(),
    )


# ============================================================
# ID
# ============================================================

async def id_handler(
    message: Message,
):

    await message.answer(
        "Ваш Telegram ID: "
        f"<code>{message.from_user.id}</code>"
    )


# ============================================================
# HOME
# ============================================================

async def home_handler(
    callback: CallbackQuery,
    state: FSMContext,
):

    await state.clear()

    await callback.answer()

    await callback.message.answer(
        "🚗 <b>Balticar</b>\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard(),
    )


# ============================================================
# CATALOG
# ============================================================

async def catalog(
    callback: CallbackQuery,
):

    await callback.answer()

    await callback.message.answer(
        "🚗 <b>Выберите автомобиль:</b>",
        reply_markup=car_keyboard(),
    )


# ============================================================
# CAR SELECTED
# ============================================================

async def car_selected(
    callback: CallbackQuery,
):

    parts = callback.data.split(":")

    if len(parts) != 2:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    cid = parts[1]

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    await callback.answer()

    await send_car(
        callback.bot,
        callback.message.chat.id,
        cid,
    )


# ============================================================
# START DATE CALENDAR
# ============================================================

def calendar_keyboard(
    car_id: str,
    year: int,
    month: int,
):

    first = date(
        year,
        month,
        1,
    )

    if month == 12:

        next_month = date(
            year + 1,
            1,
            1,
        )

    else:

        next_month = date(
            year,
            month + 1,
            1,
        )

    days = (
        next_month - first
    ).days

    previous_day = (
        first - timedelta(days=1)
    )

    prev_first = date(
        previous_day.year,
        previous_day.month,
        1,
    )

    rows = [
        [
            InlineKeyboardButton(
                text=x,
                callback_data="noop",
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
            callback_data="noop",
        )
        for _ in range(
            first.weekday()
        )
    ]

    today = (
        datetime.now(TZ).date()
    )

    for number in range(
        1,
        days + 1,
    ):

        current = date(
            year,
            month,
            number,
        )

        disabled = (
            current < today
        )

        if disabled:

            button = InlineKeyboardButton(
                text="·",
                callback_data="noop",
            )

        else:

            button = InlineKeyboardButton(
                text=str(number),
                callback_data=(
                    f"day:{car_id}:"
                    f"{current.isoformat()}"
                ),
            )

        week.append(button)

        if len(week) == 7:

            rows.append(week)
            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop",
                )
            )

        rows.append(week)

    rows.append(
        [
            InlineKeyboardButton(
                text="‹",
                callback_data=(
                    f"month:{car_id}:"
                    f"{prev_first.isoformat()}"
                ),
            ),

            InlineKeyboardButton(
                text=first.strftime("%m.%Y"),
                callback_data="noop",
            ),

            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"month:{car_id}:"
                    f"{next_month.isoformat()}"
                ),
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="home",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# PICK DATES
# ============================================================

async def pick_dates(
    callback: CallbackQuery,
    state: FSMContext,
):

    parts = callback.data.split(":")

    if len(parts) != 2:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    cid = parts[1]

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    await state.clear()

    today = (
        datetime.now(TZ).date()
    )

    await callback.answer()

    await callback.message.answer(
        f"📅 <b>{CARS[cid]['name']}</b>\n\n"
        "Выберите дату получения:",
        reply_markup=calendar_keyboard(
            cid,
            today.year,
            today.month,
        ),
    )


# ============================================================
# MONTH
# ============================================================

async def month(
    callback: CallbackQuery,
):

    parts = callback.data.split(":")

    if len(parts) != 3:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    _, cid, iso = parts

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    try:

        d = date.fromisoformat(
            iso
        )

    except ValueError:

        await callback.answer(
            "Некорректная дата.",
            show_alert=True,
        )
        return

    today = (
        datetime.now(TZ).date()
    )

    # Не позволяем календарю уходить слишком далеко
    # назад относительно текущего месяца.

    if (
        d.year < today.year
        or (
            d.year == today.year
            and d.month < today.month
        )
    ):

        d = date(
            today.year,
            today.month,
            1,
        )

    try:

        await callback.message.edit_reply_markup(
            reply_markup=calendar_keyboard(
                cid,
                d.year,
                d.month,
            )
        )

    except Exception as e:

        print(
            f"Calendar edit error: {e}"
        )

    await callback.answer()


# ============================================================
# START DAY
# ============================================================

async def start_day(
    callback: CallbackQuery,
    state: FSMContext,
):

    parts = callback.data.split(":")

    if len(parts) != 3:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    _, cid, iso = parts

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    try:

        start_d = date.fromisoformat(
            iso
        )

    except ValueError:

        await callback.answer(
            "Некорректная дата.",
            show_alert=True,
        )
        return

    today = (
        datetime.now(TZ).date()
    )

    if start_d < today:

        await callback.answer(
            "Эта дата уже прошла.",
            show_alert=True,
        )
        return

    await state.update_data(
        car_id=cid,
        start_date=iso,
    )

    await state.set_state(
        Booking.start_time
    )

    await callback.answer()

    await callback.message.answer(
        f"📅 Дата получения: "
        f"<b>{start_d.strftime('%d.%m.%Y')}</b>\n\n"
        "⏰ Выберите время получения:",
        reply_markup=time_keyboard(
            prefix="picktime",
            cid=cid,
            date_iso=iso,
        ),
    )


# ============================================================
# TIME KEYBOARD
# ============================================================

def time_keyboard(
    prefix: str,
    cid: str,
    date_iso: str,
):

    rows = []
    row = []

    for t in generate_time_values():

        callback_data = (
            f"{prefix}:"
            f"{cid}:"
            f"{date_iso}:"
            f"{t.hour:02d}:"
            f"{t.minute:02d}"
        )

        row.append(
            InlineKeyboardButton(
                text=t.strftime("%H:%M"),
                callback_data=callback_data,
            )
        )

        if len(row) == 4:

            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="home",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# PICKUP TIME
# ============================================================

async def pick_time(
    callback: CallbackQuery,
    state: FSMContext,
):

    parsed = parse_callback_time(
        callback.data,
        "picktime",
    )

    if parsed is None:

        await callback.answer(
            "Некорректные данные выбора времени.",
            show_alert=True,
        )
        return

    (
        cid,
        date_iso,
        start_d,
        selected_time,
    ) = parsed

    if not is_valid_time(
        selected_time
    ):

        await callback.answer(
            "Это время недоступно.",
            show_alert=True,
        )
        return

    start_at = local_dt(
        start_d,
        selected_time,
    )

    now = datetime.now(TZ)

    if start_at <= now:

        await callback.answer(
            "Это время уже прошло.",
            show_alert=True,
        )
        return

    await state.update_data(
        car_id=cid,
        start_date=date_iso,
        start_at=start_at.isoformat(),
    )

    await state.set_state(
        Booking.end_date
    )

    await callback.answer()

    await callback.message.answer(
        f"✅ Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        "📅 Теперь выберите дату возврата:",
        reply_markup=end_calendar_keyboard(
            cid,
            start_d,
            start_d.year,
            start_d.month,
        ),
    )


# ============================================================
# END DATE CALENDAR
# ============================================================

def end_calendar_keyboard(
    car_id: str,
    start_d: date,
    year: int,
    month: int,
):

    first = date(
        year,
        month,
        1,
    )

    if month == 12:

        next_month = date(
            year + 1,
            1,
            1,
        )

    else:

        next_month = date(
            year,
            month + 1,
            1,
        )

    days = (
        next_month - first
    ).days

    previous_day = (
        first - timedelta(days=1)
    )

    prev_first = date(
        previous_day.year,
        previous_day.month,
        1,
    )

    # Не разрешаем календарю возврата уходить
    # в месяц раньше месяца получения.

    if (
        prev_first.year < start_d.year
        or (
            prev_first.year == start_d.year
            and prev_first.month < start_d.month
        )
    ):

        prev_first = date(
            start_d.year,
            start_d.month,
            1,
        )

    rows = [
        [
            InlineKeyboardButton(
                text=x,
                callback_data="noop",
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
            callback_data="noop",
        )
        for _ in range(
            first.weekday()
        )
    ]

    for number in range(
        1,
        days + 1,
    ):

        current = date(
            year,
            month,
            number,
        )

        disabled = (
            current <= start_d
        )

        if disabled:

            button = InlineKeyboardButton(
                text="·",
                callback_data="noop",
            )

        else:

            button = InlineKeyboardButton(
                text=str(number),
                callback_data=(
                    f"endday:{car_id}:"
                    f"{current.isoformat()}"
                ),
            )

        week.append(button)

        if len(week) == 7:

            rows.append(week)
            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop",
                )
            )

        rows.append(week)

    rows.append(
        [
            InlineKeyboardButton(
                text="‹",
                callback_data=(
                    f"endmonth:{car_id}:"
                    f"{start_d.isoformat()}:"
                    f"{prev_first.isoformat()}"
                ),
            ),

            InlineKeyboardButton(
                text=first.strftime("%m.%Y"),
                callback_data="noop",
            ),

            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"endmonth:{car_id}:"
                    f"{start_d.isoformat()}:"
                    f"{next_month.isoformat()}"
                ),
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="home",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# END MONTH
# ============================================================

async def endmonth(
    callback: CallbackQuery,
):

    parts = callback.data.split(":")

    if len(parts) != 4:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    _, cid, start_iso, iso = parts

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    try:

        start_d = date.fromisoformat(
            start_iso
        )

        d = date.fromisoformat(
            iso
        )

    except ValueError:

        await callback.answer(
            "Некорректная дата.",
            show_alert=True,
        )
        return

    if (
        d.year < start_d.year
        or (
            d.year == start_d.year
            and d.month < start_d.month
        )
    ):

        d = date(
            start_d.year,
            start_d.month,
            1,
        )

    try:

        await callback.message.edit_reply_markup(
            reply_markup=end_calendar_keyboard(
                cid,
                start_d,
                d.year,
                d.month,
            )
        )

    except Exception as e:

        print(
            f"End calendar edit error: {e}"
        )

    await callback.answer()


# ============================================================
# END DAY
# ============================================================

async def end_day(
    callback: CallbackQuery,
    state: FSMContext,
):

    parts = callback.data.split(":")

    if len(parts) != 3:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    _, cid, end_iso = parts

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    data = await state.get_data()

    if not data.get("start_at"):

        await state.clear()

        await callback.answer(
            "Сессия устарела.",
            show_alert=True,
        )

        await callback.message.answer(
            "Начните бронирование заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    try:

        start_at = datetime.fromisoformat(
            data["start_at"]
        )

        start_at = ensure_tz(
            start_at
        )

        end_d = date.fromisoformat(
            end_iso
        )

    except ValueError:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    if end_d <= start_at.date():

        await callback.answer(
            "Дата возврата должна быть позже "
            "даты получения.",
            show_alert=True,
        )
        return

    await state.update_data(
        end_date=end_iso
    )

    await state.set_state(
        Booking.end_time
    )

    await callback.answer()

    await callback.message.answer(
        f"📅 Возврат: "
        f"<b>{end_d.strftime('%d.%m.%Y')}</b>\n\n"
        "⏰ Выберите время возврата:",
        reply_markup=time_keyboard(
            prefix="endtime",
            cid=cid,
            date_iso=end_iso,
        ),
    )


# ============================================================
# END TIME
# ============================================================

async def end_time_handler(
    callback: CallbackQuery,
    state: FSMContext,
):

    """
    Финальный выбор времени возврата.

    callback_data:

        endtime:CAR_ID:YYYY-MM-DD:HH:MM

    Время содержит двоеточие, поэтому нельзя разбирать
    callback четырьмя переменными.

    Используется полный разбор пяти частей.
    """

    parsed = parse_callback_time(
        callback.data,
        "endtime",
    )

    if parsed is None:

        await callback.answer(
            "Некорректные данные выбора времени.",
            show_alert=True,
        )
        return

    (
        cid,
        end_date_iso,
        end_d,
        selected_time,
    ) = parsed

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    data = await state.get_data()

    if not data.get("start_at"):

        await state.clear()

        await callback.answer(
            "Сессия устарела.",
            show_alert=True,
        )

        await callback.message.answer(
            "❌ Сессия устарела.\n\n"
            "Начните бронирование заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    try:

        start_at = datetime.fromisoformat(
            data["start_at"]
        )

        start_at = ensure_tz(
            start_at
        )

    except ValueError:

        await state.clear()

        await callback.answer(
            "Ошибка данных.",
            show_alert=True,
        )

        await callback.message.answer(
            "❌ Ошибка данных бронирования.\n\n"
            "Начните заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    if not is_valid_time(
        selected_time
    ):

        await callback.answer(
            "Это время недоступно.",
            show_alert=True,
        )
        return

    end_at = local_dt(
        end_d,
        selected_time,
    )

    if end_at <= start_at:

        await callback.answer(
            "Возврат должен быть позже получения.",
            show_alert=True,
        )
        return

    # --------------------------------------------------------
    # ФИНАЛЬНАЯ ПРОВЕРКА ЗАНЯТОСТИ
    # --------------------------------------------------------

    is_available = await available(
        cid,
        start_at,
        end_at,
    )

    if not is_available:

        await callback.answer(
            "Автомобиль уже занят.",
            show_alert=True,
        )

        await callback.message.answer(
            "❌ В этот период автомобиль уже занят.\n\n"
            "Выберите другие даты или время."
        )

        return

    # --------------------------------------------------------
    # РАСЧЁТ
    # --------------------------------------------------------

    days = rental_days(
        start_at,
        end_at,
    )

    if days <= 0:

        await callback.answer(
            "Некорректная продолжительность.",
            show_alert=True,
        )
        return

    rate = rate_for_days(
        cid,
        days,
    )

    total = (
        days * rate
    )

    await state.update_data(
        end_date=end_date_iso,
        end_at=end_at.isoformat(),
        days=days,
        total=total,
    )

    await state.set_state(
        Booking.name
    )

    await callback.answer()

    await callback.message.answer(
        "✅ <b>Период выбран</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n\n"
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        f"📅 Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"
        f"⏱ Продолжительность: "
        f"<b>{days} суток</b>\n\n"
        f"💰 Стоимость: "
        f"<b>{money(total)}</b>\n\n"
        "Введите ваше имя:"
    )


# ============================================================
# NAME
# ============================================================

async def name_handler(
    message: Message,
    state: FSMContext,
):

    text = (
        message.text or ""
    ).strip()

    if len(text) < 2:

        await message.answer(
            "Пожалуйста, введите имя."
        )
        return

    if len(text) > 100:

        await message.answer(
            "Имя слишком длинное. "
            "Введите имя ещё раз."
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
    state: FSMContext,
):

    phone = (
        message.text or ""
    ).strip()

    if len(phone) < 7:

        await message.answer(
            "Похоже, номер слишком короткий.\n"
            "Введите телефон ещё раз."
        )
        return

    if len(phone) > 50:

        await message.answer(
            "Номер телефона слишком длинный.\n"
            "Введите его ещё раз."
        )
        return

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        Booking.comment
    )

    await message.answer(
        "📝 Комментарий к заказу "
        "(или отправьте «-»):"
    )


# ============================================================
# COMMENT / CREATE BOOKING
# ============================================================

async def comment_handler(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    required = [
        "car_id",
        "start_at",
        "end_at",
        "name",
        "phone",
        "total",
        "days",
    ]

    if not all(
        key in data
        for key in required
    ):

        await state.clear()

        await message.answer(
            "❌ Данные бронирования устарели.\n\n"
            "Начните заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    cid = data["car_id"]

    if cid not in CARS:

        await state.clear()

        await message.answer(
            "❌ Автомобиль не найден.\n\n"
            "Начните заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    try:

        start_at = ensure_tz(
            datetime.fromisoformat(
                data["start_at"]
            )
        )

        end_at = ensure_tz(
            datetime.fromisoformat(
                data["end_at"]
            )
        )

        total = int(
            data["total"]
        )

        days = int(
            data["days"]
        )

    except (
        ValueError,
        TypeError,
    ):

        await state.clear()

        await message.answer(
            "❌ Ошибка данных бронирования.\n\n"
            "Начните заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    if end_at <= start_at:

        await state.clear()

        await message.answer(
            "❌ Некорректный период бронирования.\n\n"
            "Начните заново: /start",
            reply_markup=main_keyboard(),
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

    if len(comment) > 2000:

        await message.answer(
            "Комментарий слишком длинный. "
            "Максимум 2000 символов."
        )
        return

    # --------------------------------------------------------
    # Ещё одна проверка перед созданием.
    # --------------------------------------------------------

    if not await available(
        cid,
        start_at,
        end_at,
    ):

        await state.clear()

        await message.answer(
            "❌ К сожалению, автомобиль только что "
            "забронировали на выбранный период.\n\n"
            "Пожалуйста, начните бронирование заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    # --------------------------------------------------------
    # Временное удержание заявки.
    # --------------------------------------------------------

    expires_at = (
        datetime.now(TZ)
        + timedelta(
            minutes=HOLD_MINUTES
        )
    )

    username = (
        message.from_user.username
        or ""
    )

    # --------------------------------------------------------
    # АТОМАРНОЕ СОЗДАНИЕ В POSTGRESQL.
    # --------------------------------------------------------

    try:

        bid, created = await create_booking_atomic(
            user_id=message.from_user.id,
            username=username,
            car_id=cid,
            start_at=start_at,
            end_at=end_at,
            name=data["name"],
            phone=data["phone"],
            comment=comment,
            total=total,
            expires_at=expires_at,
        )

    except Exception as e:

        print(
            f"Booking creation error: {e}"
        )

        await state.clear()

        await message.answer(
            "❌ Не удалось создать заявку "
            "из-за ошибки базы данных.\n\n"
            "Попробуйте ещё раз через несколько секунд.",
            reply_markup=main_keyboard(),
        )
        return

    if not created or bid is None:

        await state.clear()

        await message.answer(
            "❌ К сожалению, автомобиль только что "
            "забронировали на выбранный период.\n\n"
            "Пожалуйста, начните бронирование заново: /start",
            reply_markup=main_keyboard(),
        )
        return

    await state.clear()

    # --------------------------------------------------------
    # КЛИЕНТУ
    # --------------------------------------------------------

    await message.answer(
        f"📩 <b>Заявка №{bid} отправлена!</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n\n"
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        f"📅 Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"
        f"⏱ {days} суток\n"
        f"💰 <b>{money(total)}</b>\n\n"
        "Мы свяжемся с вами после подтверждения заявки."
    )

    # --------------------------------------------------------
    # АДМИНУ
    # --------------------------------------------------------

    if ADMIN_ID:

        username_display = (
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
            f"💰 <b>{money(total)}</b>\n\n"
            f"👤 {data['name']}\n"
            f"📞 {data['phone']}\n"
            f"Telegram: {username_display}\n"
            f"📝 {comment or '—'}\n\n"
            f"⏳ Ожидает подтверждения до "
            f"{expires_at.astimezone(TZ).strftime('%d.%m.%Y %H:%M')}"
        )

        try:

            await message.bot.send_message(
                ADMIN_ID,
                text,
                reply_markup=admin_buttons(
                    bid
                ),
            )

        except Exception as e:

            print(
                f"Admin notification error: {e}"
            )


# ============================================================
# ADMIN BUTTONS
# ============================================================

def admin_buttons(
    bid: int,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"confirm:{bid}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{bid}",
                ),
            ]
        ]
    )


# ============================================================
# MY BOOKINGS
# ============================================================

async def mybookings(
    callback: CallbackQuery,
):

    await callback.answer()

    if db_pool is None:

        await callback.message.answer(
            "❌ База данных временно недоступна."
        )
        return

    try:

        await cleanup_pending()

        async with db_pool.acquire() as con:

            rows = await con.fetch(
                """
                SELECT
                    id,
                    car_id,
                    start_at,
                    end_at,
                    total,
                    status
                FROM bookings
                WHERE user_id = $1
                ORDER BY id DESC
                LIMIT 10
                """,
                callback.from_user.id,
            )

    except Exception as e:

        print(
            f"My bookings error: {e}"
        )

        await callback.message.answer(
            "❌ Не удалось загрузить заявки."
        )
        return

    if not rows:

        await callback.message.answer(
            "У вас пока нет заявок."
        )
        return

    labels = {
        "pending": "⏳ ожидает подтверждения",
        "confirmed": "✅ подтверждена",
        "rejected": "❌ отклонена",
        "expired": "⌛ истекла",
    }

    out = [
        "📋 <b>Ваши заявки:</b>"
    ]

    for row in rows:

        status = labels.get(
            row["status"],
            row["status"],
        )

        car_name = CARS.get(
            row["car_id"],
            {},
        ).get(
            "name",
            row["car_id"],
        )

        start_at = ensure_tz(
            row["start_at"]
        )

        end_at = ensure_tz(
            row["end_at"]
        )

        out.append(
            f"\n№{row['id']} — "
            f"{car_name}\n"
            f"📅 "
            f"{format_date_time(start_at)} — "
            f"{format_date_time(end_at)}\n"
            f"💰 {money(row['total'])}\n"
            f"Статус: {status}"
        )

    await callback.message.answer(
        "\n".join(out)
    )


# ============================================================
# TERMS
# ============================================================

async def terms(
    callback: CallbackQuery,
):

    await callback.answer()

    await callback.message.answer(
        "ℹ️ <b>Условия</b>\n\n"
        "• Бронирование оформляется после "
        "подтверждения заявки.\n"
        "• Цена рассчитывается автоматически "
        "по продолжительности аренды.\n"
        "• Заявка временно удерживает выбранный "
        "период до подтверждения.\n"
        "• Детали получения и возврата согласовываются "
        "с менеджером."
    )


# ============================================================
# ADMIN ACTION
# ============================================================

async def admin_action(
    callback: CallbackQuery,
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    if db_pool is None:

        await callback.answer(
            "База данных недоступна.",
            show_alert=True,
        )
        return

    parts = callback.data.split(":")

    if len(parts) != 2:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )
        return

    action = parts[0]

    try:

        bid = int(parts[1])

    except ValueError:

        await callback.answer(
            "Некорректный номер заявки.",
            show_alert=True,
        )
        return

    if action not in {
        "confirm",
        "reject",
    }:

        await callback.answer(
            "Неизвестное действие.",
            show_alert=True,
        )
        return

    # --------------------------------------------------------
    # CONFIRM
    # --------------------------------------------------------

    if action == "confirm":

        try:

            async with db_pool.acquire() as con:

                async with con.transaction():

                    # ----------------------------------------
                    # Получаем заявку.
                    # ----------------------------------------

                    row = await con.fetchrow(
                        """
                        SELECT *
                        FROM bookings
                        WHERE id = $1
                        FOR UPDATE
                        """,
                        bid,
                    )

                    if not row:

                        await callback.answer(
                            "Заявка не найдена.",
                            show_alert=True,
                        )
                        return

                    if row["status"] != "pending":

                        await callback.answer(
                            "Заявка уже обработана.",
                            show_alert=True,
                        )
                        return

                    start_at = ensure_tz(
                        row["start_at"]
                    )

                    end_at = ensure_tz(
                        row["end_at"]
                    )

                    car_id = row["car_id"]

                    # ----------------------------------------
                    # Блокируем операции по этому автомобилю.
                    # ----------------------------------------

                    await con.execute(
                        """
                        SELECT pg_advisory_xact_lock(
                            hashtext($1)
                        )
                        """,
                        car_id,
                    )

                    # ----------------------------------------
                    # Удаляем истёкшие заявки.
                    # ----------------------------------------

                    await con.execute(
                        """
                        UPDATE bookings
                        SET status = 'expired'
                        WHERE status = 'pending'
                          AND expires_at IS NOT NULL
                          AND expires_at < NOW()
                        """
                    )

                    # ----------------------------------------
                    # Повторная проверка конфликта.
                    # ----------------------------------------

                    conflict = await con.fetchrow(
                        """
                        SELECT id
                        FROM bookings
                        WHERE car_id = $1
                          AND id <> $2
                          AND status IN ('pending', 'confirmed')
                          AND start_at < $4
                          AND end_at > $3
                        LIMIT 1
                        """,
                        car_id,
                        bid,
                        start_at,
                        end_at,
                    )

                    if conflict:

                        await con.execute(
                            """
                            UPDATE bookings
                            SET status = 'rejected'
                            WHERE id = $1
                            """,
                            bid,
                        )

                        conflict_found = True
                        updated = False

                    else:

                        updated_row = await con.fetchrow(
                            """
                            UPDATE bookings
                            SET status = 'confirmed',
                                expires_at = NULL
                            WHERE id = $1
                              AND status = 'pending'
                            RETURNING id
                            """,
                            bid,
                        )

                        conflict_found = False
                        updated = (
                            updated_row is not None
                        )

        except Exception as e:

            print(
                f"Confirm booking error: {e}"
            )

            await callback.answer(
                "Ошибка базы данных.",
                show_alert=True,
            )
            return

        if conflict_found:

            try:

                await callback.message.edit_reply_markup(
                    reply_markup=None
                )

            except Exception:
                pass

            try:

                await callback.bot.send_message(
                    row["user_id"],
                    f"❌ Заявка №{bid} отклонена.\n\n"
                    "Выбранный период уже занят.",
                )

            except Exception as e:

                print(
                    f"Client conflict notification error: {e}"
                )

            await callback.answer(
                "Даты уже заняты.",
                show_alert=True,
            )

            return

        if not updated:

            await callback.answer(
                "Заявка уже обработана.",
                show_alert=True,
            )
            return

        try:

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        try:

            await callback.bot.send_message(
                row["user_id"],
                f"✅ <b>Заявка №{bid} подтверждена!</b>\n\n"
                f"🚗 {CARS[row['car_id']]['name']}\n\n"
                f"📅 Получение:\n"
                f"<b>{format_date_time(start_at)}</b>\n\n"
                f"📅 Возврат:\n"
                f"<b>{format_date_time(end_at)}</b>\n\n"
                f"💰 {money(row['total'])}",
            )

        except Exception as e:

            print(
                f"Client confirmation notification error: {e}"
            )

        await callback.answer(
            "Заявка подтверждена."
        )

        return

    # --------------------------------------------------------
    # REJECT
    # --------------------------------------------------------

    if action == "reject":

        try:

            async with db_pool.acquire() as con:

                async with con.transaction():

                    row = await con.fetchrow(
                        """
                        SELECT *
                        FROM bookings
                        WHERE id = $1
                        FOR UPDATE
                        """,
                        bid,
                    )

                    if not row:

                        await callback.answer(
                            "Заявка не найдена.",
                            show_alert=True,
                        )
                        return

                    if row["status"] != "pending":

                        await callback.answer(
                            "Заявка уже обработана.",
                            show_alert=True,
                        )
                        return

                    updated_row = await con.fetchrow(
                        """
                        UPDATE bookings
                        SET status = 'rejected'
                        WHERE id = $1
                          AND status = 'pending'
                        RETURNING id
                        """,
                        bid,
                    )

                    updated = (
                        updated_row is not None
                    )

        except Exception as e:

            print(
                f"Reject booking error: {e}"
            )

            await callback.answer(
                "Ошибка базы данных.",
                show_alert=True,
            )
            return

        if not updated:

            await callback.answer(
                "Заявка уже обработана.",
                show_alert=True,
            )
            return

        try:

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        try:

            await callback.bot.send_message(
                row["user_id"],
                f"❌ <b>Заявка №{bid} отклонена.</b>",
            )

        except Exception as e:

            print(
                f"Client rejection notification error: {e}"
            )

        await callback.answer(
            "Заявка отклонена."
        )


# ============================================================
# PUBLISH
# ============================================================

async def publish(
    message: Message,
):

    if message.from_user.id != ADMIN_ID:
        return

    if not CHANNEL_USERNAME:

        await message.answer(
            "CHANNEL_USERNAME не задан."
        )
        return

    try:

        bot_me = await message.bot.get_me()

        if not bot_me.username:

            await message.answer(
                "У бота нет username."
            )
            return

        text = (
            "🚗 <b>Balticar — аренда автомобилей</b>\n\n"
            "Выберите автомобиль, посмотрите стоимость "
            "и забронируйте его прямо в Telegram."
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚗 Забронировать автомобиль",
                        url=(
                            f"https://t.me/"
                            f"{bot_me.username}"
                        ),
                    )
                ]
            ]
        )

        await message.bot.send_message(
            CHANNEL_USERNAME,
            text,
            reply_markup=keyboard,
        )

        await message.answer(
            "Готово: пост с кнопкой опубликован "
            "в канале."
        )

    except Exception as e:

        print(
            f"Publish error: {e}"
        )

        await message.answer(
            "❌ Не удалось опубликовать пост.\n\n"
            f"Ошибка: {e}"
        )


# ============================================================
# HEALTH
# ============================================================

async def health(
    _: web.Request,
):

    return web.Response(
        text="Balticar bot is running",
        status=200,
    )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

async def telegram_webhook(
    request: web.Request,
):

    bot = request.app["bot"]
    dp = request.app["dp"]

    if WEBHOOK_SECRET:

        incoming_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if incoming_secret != WEBHOOK_SECRET:

            raise web.HTTPForbidden(
                text="Invalid webhook secret"
            )

    try:

        data = await request.json()

        update = Update.model_validate(
            data,
            context={
                "bot": bot
            },
        )

        await dp.feed_update(
            bot,
            update,
        )

        return web.Response(
            text="OK",
            status=200,
        )

    except Exception as e:

        print(
            f"Webhook error: {e}"
        )

        return web.Response(
            status=500,
            text="Webhook error",
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    exception,
):

    print(
        f"Dispatcher error: {exception}"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не задан."
        )

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL не задан."
        )

    await init_db()

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher()

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    dp.errors.register(
        error_handler
    )

    # ========================================================
    # COMMANDS
    # ========================================================

    dp.message.register(
        start_handler,
        Command("start"),
    )

    dp.message.register(
        id_handler,
        Command("id"),
    )

    dp.message.register(
        cancel_handler,
        Command("cancel"),
    )

    dp.message.register(
        publish,
        Command("publish"),
    )

    # ========================================================
    # CATALOG
    # ========================================================

    dp.callback_query.register(
        home_handler,
        F.data == "home",
    )

    dp.callback_query.register(
        catalog,
        F.data == "catalog",
    )

    dp.callback_query.register(
        car_selected,
        F.data.startswith("car:"),
    )

    dp.callback_query.register(
        pick_dates,
        F.data.startswith("pick:"),
    )

    # ========================================================
    # START DATE
    # ========================================================

    dp.callback_query.register(
        month,
        F.data.startswith("month:"),
    )

    dp.callback_query.register(
        start_day,
        F.data.startswith("day:"),
    )

    # ========================================================
    # PICKUP TIME
    # ========================================================

    dp.callback_query.register(
        pick_time,
        F.data.startswith("picktime:"),
    )

    # ========================================================
    # END DATE
    # ========================================================

    dp.callback_query.register(
        endmonth,
        F.data.startswith("endmonth:"),
    )

    dp.callback_query.register(
        end_day,
        F.data.startswith("endday:"),
    )

    # ========================================================
    # RETURN TIME
    # ========================================================

    # ВАЖНО:
    # Здесь теперь действительно существует функция
    # end_time_handler.
    #
    # Именно её не хватало в предыдущей версии, из-за чего
    # Render выдавал:
    #
    # NameError:
    # name 'end_time_handler' is not defined
    #
    # ========================================================

    dp.callback_query.register(
        end_time_handler,
        F.data.startswith("endtime:"),
    )

    # ========================================================
    # OTHER
    # ========================================================

    dp.callback_query.register(
        mybookings,
        F.data == "mybookings",
    )

    dp.callback_query.register(
        terms,
        F.data == "terms",
    )

    # ========================================================
    # ADMIN
    # ========================================================

    dp.callback_query.register(
        admin_action,
        F.data.startswith("confirm:"),
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("reject:"),
    )

    # ========================================================
    # NOOP
    # ========================================================

    async def noop_handler(
        callback: CallbackQuery,
    ):

        await callback.answer()

    dp.callback_query.register(
        noop_handler,
        F.data == "noop",
    )

    # ========================================================
    # FSM
    # ========================================================

    dp.message.register(
        name_handler,
        Booking.name,
    )

    dp.message.register(
        phone_handler,
        Booking.phone,
    )

    dp.message.register(
        comment_handler,
        Booking.comment,
    )

    # ========================================================
    # RENDER
    # ========================================================

    external_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        "",
    ).strip().rstrip("/")

    # --------------------------------------------------------
    # Если Render URL отсутствует — polling.
    # --------------------------------------------------------

    if not external_url:

        print(
            "========================================"
        )

        print(
            "Balticar bot started in POLLING mode"
        )

        print(
            f"Timezone: {TIMEZONE}"
        )

        print(
            "Database: PostgreSQL / Neon"
        )

        print(
            "========================================"
        )

        try:

            await dp.start_polling(
                bot
            )

        finally:

            await close_db()

            try:
                await bot.session.close()
            except Exception:
                pass

        return

    # ========================================================
    # WEBHOOK
    # ========================================================

    webhook_url = (
        f"{external_url}"
        f"{WEBHOOK_PATH}"
    )

    # --------------------------------------------------------
    # Если секрет задан в Environment Variables — используем
    # его.
    #
    # Если не задан — генерируем секрет автоматически.
    # Важно: сгенерированный секрет используется только
    # Telegram API. Проверка заголовка ниже в этом случае
    # специально не включается, потому что после рестарта
    # Render значение может измениться.
    # --------------------------------------------------------

    generated_secret = False

    if WEBHOOK_SECRET:

        secret = WEBHOOK_SECRET

    else:

        secret = secrets.token_urlsafe(32)
        generated_secret = True

    try:

        await bot.set_webhook(
            webhook_url,
            secret_token=secret,
            allowed_updates=(
                dp.resolve_used_update_types()
            ),
            drop_pending_updates=False,
        )

        print(
            "========================================"
        )

        print(
            "Balticar bot started"
        )

        print(
            f"Webhook: {webhook_url}"
        )

        print(
            f"Timezone: {TIMEZONE}"
        )

        print(
            f"Pickup time: "
            f"{PICKUP_START_HOUR:02d}:00 - "
            f"{PICKUP_END_HOUR:02d}:00"
        )

        print(
            f"Hold time: {HOLD_MINUTES} minutes"
        )

        print(
            "Database: PostgreSQL / Neon"
        )

        if generated_secret:

            print(
                "Webhook secret: generated automatically"
            )

        else:

            print(
                "Webhook secret: configured "
                "in Environment Variables"
            )

        print(
            "========================================"
        )

    except Exception as e:

        print(
            f"Webhook setup error: {e}"
        )

        await close_db()

        try:
            await bot.session.close()
        except Exception:
            pass

        raise

    # ========================================================
    # AIOHTTP
    # ========================================================

    app = web.Application()

    app["bot"] = bot
    app["dp"] = dp

    app.router.add_get(
        "/",
        health,
    )

    app.router.add_get(
        "/health",
        health,
    )

    app.router.add_post(
        WEBHOOK_PATH,
        telegram_webhook,
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    print(
        f"Server started on port {PORT}"
    )

    try:

        await asyncio.Event().wait()

    except asyncio.CancelledError:

        print(
            "Main task cancelled."
        )

        raise

    finally:

        print(
            "Stopping Balticar bot..."
        )

        try:

            await bot.delete_webhook(
                drop_pending_updates=False
            )

        except Exception as e:

            print(
                f"Webhook delete error: {e}"
            )

        try:

            await runner.cleanup()

        except Exception as e:

            print(
                f"Runner cleanup error: {e}"
            )

        await close_db()

        try:

            await bot.session.close()

        except Exception as e:

            print(
                f"Bot session close error: {e}"
            )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Balticar bot stopped."
        )

    except Exception as e:

        print(
            "========================================"
        )

        print(
            f"FATAL ERROR: {e}"
        )

        print(
            "========================================"
        )

        raise
