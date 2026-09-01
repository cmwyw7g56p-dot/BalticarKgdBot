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

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "0") or 0
)

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "@Balticar_kgd",
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "",
)

TIMEZONE = os.getenv(
    "TIMEZONE",
    "Europe/Kaliningrad",
)

TZ = ZoneInfo(TIMEZONE)

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

WEBHOOK_PATH = os.getenv(
    "WEBHOOK_PATH",
    "/telegram/webhook",
)

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    "",
)

HOLD_MINUTES = int(
    os.getenv(
        "PENDING_HOLD_MINUTES",
        "60",
    )
)

PICKUP_START_HOUR = int(
    os.getenv(
        "PICKUP_START_HOUR",
        "8",
    )
)

PICKUP_END_HOUR = int(
    os.getenv(
        "PICKUP_END_HOUR",
        "20",
    )
)

TIME_STEP_MINUTES = int(
    os.getenv(
        "TIME_STEP_MINUTES",
        "30",
    )
)


# ============================================================
# CARS
# ============================================================

CARS = {
    "solaris21": {
        "name": "Hyundai Solaris 2021",
        "gear": "АКПП",
        "rates": (
            2700,
            2600,
            2500,
        ),
        "photos": [
            "photos/solaris_2021_1.png",
            "photos/solaris_2021_2.jpeg",
        ],
    },

    "solaris20": {
        "name": "Hyundai Solaris 2020",
        "gear": "АКПП",
        "rates": (
            2700,
            2600,
            2500,
        ),
        "photos": [
            "photos/solaris_2020.jpeg",
        ],
    },

    "solaris17": {
        "name": "Hyundai Solaris 2017",
        "gear": "АКПП",
        "rates": (
            2400,
            2300,
            2200,
        ),
        "photos": [
            "photos/solaris_2017_1.webp",
            "photos/solaris_2017_2.jpeg",
        ],
    },

    "i30": {
        "name": "Hyundai i30 2014",
        "gear": "МКПП",
        "rates": (
            2300,
            2200,
            2100,
        ),
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

db_pool = None


async def init_db():

    global db_pool

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL не задан. "
            "Добавьте DATABASE_URL от Neon "
            "в Environment Variables Render."
        )

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

                status TEXT NOT NULL
                    DEFAULT 'pending',

                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT NOW(),

                expires_at TIMESTAMPTZ,

                CONSTRAINT bookings_period_valid
                CHECK (end_at > start_at)
            )
            """
        )

        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_bookings_car_period
            ON bookings (
                car_id,
                start_at,
                end_at
            )
            """
        )

        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_bookings_user
            ON bookings (user_id)
            """
        )

        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_bookings_status
            ON bookings (status)
            """
        )

    print(
        "Database initialized: PostgreSQL / Neon"
    )


async def close_db():

    global db_pool

    if db_pool is not None:

        await db_pool.close()

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

        query += """
            AND id <> $4
        """

        params.append(
            exclude_booking_id
        )

    query += """
        LIMIT 1
    """

    async with db_pool.acquire() as con:

        row = await con.fetchrow(
            query,
            *params,
        )

    return row is None


# ============================================================
# HELPERS
# ============================================================

def ensure_tz(
    value: datetime,
) -> datetime:

    if value.tzinfo is None:

        return value.replace(
            tzinfo=TZ
        )

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
        f"{value:,}"
        .replace(",", " ")
        + " ₽"
    )


def rental_days(
    start_at: datetime,
    end_at: datetime,
) -> int:

    start_at = ensure_tz(
        start_at
    )

    end_at = ensure_tz(
        end_at
    )

    seconds = (
        end_at - start_at
    ).total_seconds()

    days = int(
        seconds // 86400
    )

    if seconds % 86400:
        days += 1

    return max(
        days,
        1,
    )


def rate_for_days(
    car_id: str,
    days: int,
) -> int:

    rates = CARS[car_id]["rates"]

    if days <= 3:

        return rates[0]

    if days <= 6:

        return rates[1]

    return rates[2]


def generate_time_values():

    values = []

    current = (
        PICKUP_START_HOUR * 60
    )

    end = (
        PICKUP_END_HOUR * 60
    )

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

    car = CARS[cid]

    for photo in car["photos"]:

        if not os.path.exists(photo):
            continue

        try:

            await bot.send_photo(
                chat_id,
                FSInputFile(photo),
            )

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


async def id_handler(
    message: Message,
):

    await message.answer(
        "Ваш Telegram ID: "
        f"<code>{message.from_user.id}</code>"
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

        if current < today:

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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# PICK DATES
# ============================================================

async def pick_dates(
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

    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(
            cid,
            d.year,
            d.month,
        )
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

    parts = callback.data.split(":")

    if len(parts) != 5:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )

        return

    _, cid, date_iso, hour_s, minute_s = parts

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )

        return

    try:

        start_d = date.fromisoformat(
            date_iso
        )

        hour = int(hour_s)

        minute = int(minute_s)

        selected_time = time(
            hour,
            minute,
        )

    except ValueError:

        await callback.answer(
            "Некорректная дата или время.",
            show_alert=True,
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

        if current <= start_d:

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

    await callback.message.edit_reply_markup(
        reply_markup=end_calendar_keyboard(
            cid,
            start_d,
            d.year,
            d.month,
        )
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

    parts = callback.data.split(":")

    if len(parts) != 5:

        await callback.answer(
            "Некорректные данные.",
            show_alert=True,
        )

        return

    _, cid, end_date_iso, hour_s, minute_s = parts

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
            end_date_iso
        )

        hour = int(hour_s)

        minute = int(minute_s)

        selected_time = time(
            hour,
            minute,
        )

    except ValueError:

        await callback.answer(
            "Некорректная дата или время.",
            show_alert=True,
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
    # ПРОВЕРКА ЗАНЯТОСТИ
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

    rate = rate_for_days(
        cid,
        days,
    )

    total = days * rate

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

    except ValueError:

        await state.clear()

        await message.answer(
            "❌ Ошибка данных бронирования.\n\n"
            "Начните заново: /start",
            reply_markup=main_keyboard(),
        )

        return

    comment_text = (
        message.text or ""
    ).strip()

    if comment_text == "-":

        comment = ""

    else:

        comment = comment_text[:1000]

    # --------------------------------------------------------
    # ЗАЩИЩЁННОЕ СОЗДАНИЕ БРОНИ
    #
    # Здесь используется PostgreSQL advisory transaction lock.
    #
    # Если два клиента одновременно нажмут последний шаг
    # бронирования одного автомобиля:
    #
    # Клиент 1 получает lock -> проверка -> INSERT -> COMMIT
    # Клиент 2 ждёт lock -> проверка -> видит бронь -> отказ
    #
    # Поэтому двойное бронирование невозможно.
    # --------------------------------------------------------

    if db_pool is None:

        await state.clear()

        await message.answer(
            "❌ База данных временно недоступна.\n"
            "Попробуйте немного позже.",
            reply_markup=main_keyboard(),
        )

        return

    expires_at = (
        datetime.now(TZ)
        + timedelta(
            minutes=HOLD_MINUTES
        )
    )

    bid = None

    try:

        async with db_pool.acquire() as con:

            async with con.transaction():

                # ------------------------------------------------
                # LOCK ТОЛЬКО ДЛЯ ЭТОГО АВТОМОБИЛЯ
                # ------------------------------------------------

                await con.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended($1, 0)
                    )
                    """,
                    cid,
                )

                # ------------------------------------------------
                # УДАЛЯЕМ ПРОСРОЧЕННЫЕ PENDING
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
                # ФИНАЛЬНАЯ ПРОВЕРКА
                # ------------------------------------------------

                conflict = await con.fetchrow(
                    """
                    SELECT id
                    FROM bookings
                    WHERE car_id = $1
                      AND status IN (
                          'pending',
                          'confirmed'
                      )
                      AND start_at < $3
                      AND end_at > $2
                    LIMIT 1
                    """,
                    cid,
                    start_at,
                    end_at,
                )

                if conflict:

                    await state.clear()

                    await message.answer(
                        "❌ К сожалению, автомобиль "
                        "только что забронировали "
                        "на выбранный период.\n\n"
                        "Выберите другие даты.",
                        reply_markup=main_keyboard(),
                    )

                    return

                # ------------------------------------------------
                # СОЗДАЁМ БРОНЬ
                # ------------------------------------------------

                row = await con.fetchrow(
                    """
                    INSERT INTO bookings (
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
                    VALUES (
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
                    message.from_user.id,
                    message.from_user.username or "",
                    cid,
                    start_at,
                    end_at,
                    data["name"],
                    data["phone"],
                    comment,
                    int(data["total"]),
                    expires_at,
                )

                bid = row["id"]

    except Exception as e:

        print(
            f"Ошибка создания бронирования: {e}"
        )

        await state.clear()

        await message.answer(
            "❌ Не удалось создать заявку.\n\n"
            "Попробуйте ещё раз через несколько секунд.",
            reply_markup=main_keyboard(),
        )

        return

    await state.clear()

    # --------------------------------------------------------
    # КЛИЕНТ
    # --------------------------------------------------------

    await message.answer(
        f"📩 <b>Заявка №{bid} отправлена!</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n\n"
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        f"📅 Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"
        f"⏱ {data['days']} суток\n"
        f"💰 <b>{money(int(data['total']))}</b>\n\n"
        "Мы свяжемся с вами после "
        "подтверждения заявки."
    )

    # --------------------------------------------------------
    # АДМИН
    # --------------------------------------------------------

    if ADMIN_ID:

        username = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else "без username"
        )

        admin_text = (
            f"🔔 <b>Новая заявка №{bid}</b>\n\n"
            f"🚗 {CARS[cid]['name']} "
            f"({CARS[cid]['gear']})\n\n"
            f"📅 Получение:\n"
            f"<b>{format_date_time(start_at)}</b>\n\n"
            f"📅 Возврат:\n"
            f"<b>{format_date_time(end_at)}</b>\n\n"
            f"⏱ {data['days']} суток\n"
            f"💰 <b>{money(int(data['total']))}</b>\n\n"
            f"👤 {data['name']}\n"
            f"📞 {data['phone']}\n"
            f"Telegram: {username}\n"
            f"📝 {comment or '—'}\n\n"
            f"⏳ Ожидает подтверждения до "
            f"{expires_at.astimezone(TZ).strftime('%d.%m.%Y %H:%M')}"
        )

        try:

            await message.bot.send_message(
                ADMIN_ID,
                admin_text,
                reply_markup=admin_buttons(
                    bid
                ),
            )

        except Exception as e:

            print(
                f"Ошибка отправки заявки админу: {e}"
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

    if db_pool is None:

        await callback.answer(
            "База данных недоступна.",
            show_alert=True,
        )

        return

    await cleanup_pending()

    async with db_pool.acquire() as con:

        rows = await con.fetch(
            """
            SELECT *
            FROM bookings
            WHERE user_id = $1
            ORDER BY id DESC
            LIMIT 10
            """,
            callback.from_user.id,
        )

    if not rows:

        await callback.message.answer(
            "У вас пока нет заявок."
        )

    else:

        labels = {
            "pending":
                "⏳ ожидает подтверждения",

            "confirmed":
                "✅ подтверждена",

            "rejected":
                "❌ отклонена",

            "expired":
                "⌛ истекла",
        }

        out = [
            "📋 <b>Ваши заявки:</b>"
        ]

        for row in rows:

            status = labels.get(
                row["status"],
                row["status"],
            )

            start_at = ensure_tz(
                row["start_at"]
            )

            end_at = ensure_tz(
                row["end_at"]
            )

            car = CARS.get(
                row["car_id"]
            )

            car_name = (
                car["name"]
                if car
                else row["car_id"]
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

    await callback.answer()


# ============================================================
# TERMS
# ============================================================

async def terms(
    callback: CallbackQuery,
):

    await callback.message.answer(
        "ℹ️ <b>Условия</b>\n\n"
        "• Бронирование оформляется после "
        "подтверждения заявки.\n"
        "• Цена рассчитывается автоматически "
        "по продолжительности аренды.\n"
        "• Заявка временно удерживает выбранный "
        "период до подтверждения.\n"
        f"• Срок удержания заявки — "
        f"{HOLD_MINUTES} минут.\n"
        "• Детали получения и возврата "
        "согласовываются с менеджером."
    )

    await callback.answer()


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

    if db_pool is None:

        await callback.answer(
            "База данных недоступна.",
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

                    # Получаем заявку с блокировкой строки
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

                    # ------------------------------------------------
                    # LOCK АВТОМОБИЛЯ
                    # ------------------------------------------------

                    await con.execute(
                        """
                        SELECT pg_advisory_xact_lock(
                            hashtextextended($1, 0)
                        )
                        """,
                        row["car_id"],
                    )

                    # ------------------------------------------------
                    # ПРОСРОЧЕННЫЕ PENDING
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
                    # ПРОВЕРКА СРОКА
                    # ------------------------------------------------

                    if (
                        row["expires_at"] is not None
                        and row["expires_at"] < datetime.now(
                            TZ
                        )
                    ):

                        await con.execute(
                            """
                            UPDATE bookings
                            SET status = 'expired'
                            WHERE id = $1
                              AND status = 'pending'
                            """,
                            bid,
                        )

                        await callback.answer(
                            "Срок заявки истёк.",
                            show_alert=True,
                        )

                        await callback.message.edit_reply_markup(
                            reply_markup=None
                        )

                        try:

                            await callback.bot.send_message(
                                row["user_id"],
                                f"⌛ Заявка №{bid} "
                                "истекла.\n\n"
                                "Пожалуйста, оформите "
                                "новую заявку.",
                            )

                        except Exception as e:

                            print(
                                f"Ошибка уведомления "
                                f"пользователя: {e}"
                            )

                        return

                    # ------------------------------------------------
                    # ПРОВЕРКА ПЕРЕСЕЧЕНИЯ
                    # ------------------------------------------------

                    conflict = await con.fetchrow(
                        """
                        SELECT id
                        FROM bookings
                        WHERE car_id = $1
                          AND id <> $2
                          AND status IN (
                              'pending',
                              'confirmed'
                          )
                          AND start_at < $4
                          AND end_at > $3
                        LIMIT 1
                        """,
                        row["car_id"],
                        bid,
                        row["start_at"],
                        row["end_at"],
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

                        await callback.message.edit_reply_markup(
                            reply_markup=None
                        )

                        try:

                            await callback.bot.send_message(
                                row["user_id"],
                                f"❌ Заявка №{bid} "
                                "отклонена.\n\n"
                                "Выбранный период "
                                "уже занят.",
                            )

                        except Exception as e:

                            print(
                                f"Ошибка уведомления: {e}"
                            )

                        await callback.answer(
                            "Даты уже заняты.",
                            show_alert=True,
                        )

                        return

                    # ------------------------------------------------
                    # ПОДТВЕРЖДАЕМ
                    # ------------------------------------------------

                    updated = await con.fetchrow(
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

                    if not updated:

                        await callback.answer(
                            "Заявка уже обработана.",
                            show_alert=True,
                        )

                        return

            # --------------------------------------------------------
            # УВЕДОМЛЕНИЕ КЛИЕНТА
            # --------------------------------------------------------

            start_at = ensure_tz(
                row["start_at"]
            )

            end_at = ensure_tz(
                row["end_at"]
            )

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

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
                    f"Ошибка уведомления клиента: {e}"
                )

            await callback.answer(
                "Заявка подтверждена."
            )

            return

        except Exception as e:

            print(
                f"Ошибка подтверждения заявки "
                f"{bid}: {e}"
            )

            await callback.answer(
                "Ошибка при подтверждении заявки.",
                show_alert=True,
            )

            return

    # --------------------------------------------------------
    # REJECT
    # --------------------------------------------------------

    if action == "reject":

        try:

            async with db_pool.acquire() as con:

                row = await con.fetchrow(
                    """
                    SELECT *
                    FROM bookings
                    WHERE id = $1
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

                result = await con.execute(
                    """
                    UPDATE bookings
                    SET status = 'rejected'
                    WHERE id = $1
                      AND status = 'pending'
                    """,
                    bid,
                )

            if result.endswith("0"):

                await callback.answer(
                    "Заявка уже обработана.",
                    show_alert=True,
                )

                return

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

            try:

                await callback.bot.send_message(
                    row["user_id"],
                    f"❌ <b>Заявка №{bid} "
                    "отклонена.</b>",
                )

            except Exception as e:

                print(
                    f"Ошибка уведомления клиента: {e}"
                )

            await callback.answer(
                "Заявка отклонена."
            )

            return

        except Exception as e:

            print(
                f"Ошибка отклонения заявки "
                f"{bid}: {e}"
            )

            await callback.answer(
                "Ошибка при отклонении заявки.",
                show_alert=True,
            )

            return

    await callback.answer(
        "Неизвестное действие.",
        show_alert=True,
    )


# ============================================================
# PUBLISH
# ============================================================

async def publish(
    message: Message,
):

    if message.from_user.id != ADMIN_ID:
        return

    try:

        bot_me = await message.bot.get_me()

        if not bot_me.username:

            await message.answer(
                "Не удалось получить username бота."
            )

            return

        text = (
            "🚗 <b>Balticar — аренда автомобилей</b>\n\n"
            "Выберите автомобиль, посмотрите "
            "стоимость и забронируйте его "
            "прямо в Telegram."
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
            f"Ошибка публикации: {e}"
        )

        await message.answer(
            "❌ Не удалось опубликовать пост.\n\n"
            "Проверьте, что бот является "
            "администратором канала."
        )


# ============================================================
# HEALTH
# ============================================================

async def health(
    _: web.Request,
):

    return web.Response(
        text="Balticar bot is running"
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
            text="OK"
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
        publish,
        Command("publish"),
    )

    # ========================================================
    # CATALOG
    # ========================================================

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

    dp.callback_query.register(
        lambda callback: callback.answer(),
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
    # RENDER URL
    # ========================================================

    external_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        "",
    ).rstrip("/")

    # ========================================================
    # POLLING
    # ========================================================

    if not external_url:

        print(
            "RENDER_EXTERNAL_URL не задан."
        )

        print(
            "Запуск polling."
        )

        try:

            await dp.start_polling(
                bot
            )

        finally:

            await close_db()

            await bot.session.close()

        return

    # ========================================================
    # WEBHOOK
    # ========================================================

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
        f"Hold time: "
        f"{HOLD_MINUTES} minutes"
    )

    print(
        "Database: PostgreSQL / Neon"
    )

    print(
        "Booking protection: "
        "PostgreSQL advisory transaction lock"
    )

    print(
        "========================================"
    )

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

    # ========================================================
    # KEEP PROCESS ALIVE
    # ========================================================

    try:

        await asyncio.Event().wait()

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

        await runner.cleanup()

        await close_db()

        await bot.session.close()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())
