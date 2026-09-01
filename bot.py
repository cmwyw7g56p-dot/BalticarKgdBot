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

if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = "/" + WEBHOOK_PATH

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

TIME_STEP_MINUTES = int(
    os.getenv(
        "TIME_STEP_MINUTES",
        "30"
    )
)

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
            "photos/solaris_2020.jpeg",
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
            "photos/i30_2014.png",
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

                    start_date DATE,
                    end_date DATE,

                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    comment TEXT,

                    total INTEGER NOT NULL,

                    status TEXT NOT NULL DEFAULT 'pending',

                    created_at TIMESTAMPTZ NOT NULL
                        DEFAULT NOW(),

                    expires_at TIMESTAMPTZ,

                    start_at TIMESTAMPTZ,

                    end_at TIMESTAMPTZ
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
                ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS start_date DATE
                """
            )

            cur.execute(
                """
                ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS end_date DATE
                """
            )

            # ------------------------------------------------
            # Миграция старых записей.
            #
            # Старые брони не имели времени.
            # Для них используем:
            #
            # получение 10:00
            # возврат 17:00
            # ------------------------------------------------

            cur.execute(
                """
                UPDATE bookings
                SET start_at =
                    (
                        start_date::timestamp
                        + TIME '10:00'
                    ) AT TIME ZONE 'Europe/Kaliningrad'
                WHERE start_at IS NULL
                  AND start_date IS NOT NULL
                """
            )

            cur.execute(
                """
                UPDATE bookings
                SET end_at =
                    (
                        end_date::timestamp
                        + TIME '17:00'
                    ) AT TIME ZONE 'Europe/Kaliningrad'
                WHERE end_at IS NULL
                  AND end_date IS NOT NULL
                """
            )

            cur.execute(
                """
                UPDATE bookings
                SET start_date = (
                    start_at AT TIME ZONE
                    'Europe/Kaliningrad'
                )::date
                WHERE start_date IS NULL
                  AND start_at IS NOT NULL
                """
            )

            cur.execute(
                """
                UPDATE bookings
                SET end_date = (
                    end_at AT TIME ZONE
                    'Europe/Kaliningrad'
                )::date
                WHERE end_date IS NULL
                  AND end_at IS NOT NULL
                """
            )

            cur.execute(
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

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_bookings_user
                ON bookings (user_id)
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_bookings_status
                ON bookings (status)
                """
            )

        con.commit()

    finally:

        con.close()


def cleanup_pending():

    con = db()

    try:

        con.execute(
            """
            UPDATE bookings
            SET status = 'expired'
            WHERE status = 'pending'
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

def ensure_tz(
    value: datetime
) -> datetime:

    if value.tzinfo is None:
        return value.replace(
            tzinfo=TZ
        )

    return value.astimezone(TZ)


def local_dt(
    d: date,
    t: time
) -> datetime:

    return datetime(
        d.year,
        d.month,
        d.day,
        t.hour,
        t.minute,
        tzinfo=TZ
    )


def format_date_time(
    value: datetime
) -> str:

    value = ensure_tz(value)

    return value.strftime(
        "%d.%m.%Y %H:%M"
    )


def money(
    value: int
) -> str:

    return (
        f"{value:,}"
        .replace(",", " ")
        + " ₽"
    )


def rental_days(
    start_at: datetime,
    end_at: datetime
) -> int:

    start_at = ensure_tz(start_at)

    end_at = ensure_tz(end_at)

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
        1
    )


def rate_for_days(
    car_id: str,
    days: int
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
        PICKUP_START_HOUR
        * 60
    )

    end = (
        PICKUP_END_HOUR
        * 60
    )

    while current <= end:

        hour = current // 60

        minute = current % 60

        values.append(
            time(
                hour,
                minute
            )
        )

        current += TIME_STEP_MINUTES

    return values


def is_valid_time(
    selected_time: time
) -> bool:

    minutes = (
        selected_time.hour
        * 60
        + selected_time.minute
    )

    return (
        PICKUP_START_HOUR * 60
        <= minutes
        <= PICKUP_END_HOUR * 60
    )


# ============================================================
# AVAILABILITY
# ============================================================

def available(
    car_id: str,
    start_at: datetime,
    end_at: datetime
) -> bool:

    start_at = ensure_tz(
        start_at
    )

    end_at = ensure_tz(
        end_at
    )

    cleanup_pending()

    buffer_delta = timedelta(
        hours=BUFFER_HOURS
    )

    check_start = (
        start_at
        - buffer_delta
    )

    check_end = (
        end_at
        + buffer_delta
    )

    con = db()

    try:

        row = con.execute(
            """
            SELECT id
            FROM bookings

            WHERE car_id = %s

              AND status IN (
                  'pending',
                  'confirmed'
              )

              AND start_at < %s

              AND end_at > %s

            LIMIT 1
            """,
            (
                car_id,
                check_end,
                check_start
            )
        ).fetchone()

        return row is None

    finally:

        con.close()


# ============================================================
# MAIN KEYBOARD
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
            ]
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
# CAR KEYBOARDS
# ============================================================

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


def car_actions_keyboard(
    cid: str
):

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
            ]
        ]
    )


# ============================================================
# CAR TEXT
# ============================================================

def car_text(
    cid: str
):

    car = CARS[cid]

    return (
        f"<b>{car['name']}</b>\n\n"

        f"⚙️ Коробка: "
        f"<b>{car['gear']}</b>\n"

        f"⛽ Топливо: "
        f"<b>{car['fuel']}</b>\n"

        f"👥 Мест: "
        f"<b>{car['seats']}</b>\n\n"

        f"{car['description']}\n\n"

        f"💰 1–3 суток: "
        f"<b>{money(car['rates'][0])}/сутки</b>\n"

        f"💰 4–6 суток: "
        f"<b>{money(car['rates'][1])}/сутки</b>\n"

        f"💰 7+ суток: "
        f"<b>{money(car['rates'][2])}/сутки</b>"
    )


async def send_car(
    bot: Bot,
    chat_id: int,
    cid: str
):

    car = CARS[cid]

    for photo in car["photos"]:

        if not os.path.exists(photo):
            print(
                f"Фото не найдено: {photo}"
            )
            continue

        try:

            await bot.send_photo(
                chat_id,
                FSInputFile(photo)
            )

        except Exception as e:

            print(
                f"Ошибка отправки фото "
                f"{photo}: {e}"
            )

    await bot.send_message(
        chat_id,
        car_text(cid),
        reply_markup=car_actions_keyboard(cid)
    )


# ============================================================
# CALENDAR
# ============================================================

def calendar_keyboard(
    car_id: str,
    year: int,
    month: int
):

    first = date(
        year,
        month,
        1
    )

    next_first = (
        date(
            year + 1,
            1,
            1
        )
        if month == 12
        else date(
            year,
            month + 1,
            1
        )
    )

    prev_first = (
        date(
            year - 1,
            12,
            1
        )
        if month == 1
        else date(
            year,
            month - 1,
            1
        )
    )

    days = (
        next_first - first
    ).days

    today = datetime.now(
        TZ
    ).date()

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
        "Декабрь"
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
                "Вс"
            ]
        ]
    ]

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop"
        )

        for _ in range(
            first.weekday()
        )
    ]

    for number in range(
        1,
        days + 1
    ):

        current = date(
            year,
            month,
            number
        )

        if current < today:

            text = "⚪"

            callback = "noop"

        else:

            day_start = local_dt(
                current,
                time(0, 0)
            )

            day_end = (
                day_start
                + timedelta(days=1)
            )

            if available(
                car_id,
                day_start,
                day_end
            ):

                text = f"🟢{number}"

                callback = (
                    f"day:"
                    f"{car_id}:"
                    f"{current.isoformat()}"
                )

            else:

                text = f"🔴{number}"

                callback = "noop"

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback
            )
        )

        if len(week) == 7:

            rows.append(week)

            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop"
                )
            )

        rows.append(week)

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
                text=(
                    f"{months[month - 1]} "
                    f"{year}"
                ),
                callback_data="noop"
            ),

            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"month:"
                    f"{car_id}:"
                    f"{next_first.isoformat()}"
                )
            )
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
# START DATE
# ============================================================

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

    today = datetime.now(
        TZ
    ).date()

    await callback.message.answer(
        f"📅 <b>{CARS[cid]['name']}</b>\n\n"
        "Выберите дату получения.\n\n"
        "🟢 свободно   "
        "🔴 занято   "
        "⚪ недоступно",
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

    _, cid, iso = (
        callback.data.split(":")
    )

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

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
# START DAY
# ============================================================

async def start_day(
    callback: CallbackQuery,
    state: FSMContext
):

    _, cid, iso = (
        callback.data.split(":")
    )

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    start_d = date.fromisoformat(
        iso
    )

    if (
        start_d
        < datetime.now(TZ).date()
    ):

        await callback.answer(
            "Нельзя выбрать прошедшую дату.",
            show_alert=True
        )

        return

    await state.update_data(
        car_id=cid,
        start_date=iso
    )

    title, keyboard = time_keyboard(
        cid,
        start_d,
        "pickup"
    )

    await state.set_state(
        Booking.start_time
    )

    await callback.message.edit_reply_markup(
        reply_markup=keyboard
    )

    await callback.message.answer(
        title
    )

    await callback.answer()


# ============================================================
# PICKUP TIME
# ============================================================

def time_keyboard(
    cid: str,
    selected_date: date,
    prefix: str
):

    rows = []

    row = []

    for selected_time in (
        generate_time_values()
    ):

        callback_data = (
            f"{prefix}:"
            f"{cid}:"
            f"{selected_date.isoformat()}:"
            f"{selected_time.strftime('%H:%M')}"
        )

        row.append(
            InlineKeyboardButton(
                text=selected_time.strftime(
                    "%H:%M"
                ),
                callback_data=callback_data
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
                text="◀️ Назад",
                callback_data=(
                    f"backstart:"
                    f"{cid}:"
                    f"{selected_date.isoformat()}"
                )
            )
        ]
    )

    title = (
        f"⏰ <b>Выберите время "
        f"{'получения' if prefix == 'pickup' else 'возврата'}:</b>\n\n"
        f"Доступно: "
        f"{PICKUP_START_HOUR:02d}:00 — "
        f"{PICKUP_END_HOUR:02d}:00"
    )

    return (
        title,
        InlineKeyboardMarkup(
            inline_keyboard=rows
        )
    )


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

    selected_date = date.fromisoformat(
        date_iso
    )

    hour, minute = map(
        int,
        time_text.split(":")
    )

    selected_time = time(
        hour,
        minute
    )

    if not is_valid_time(
        selected_time
    ):

        await callback.answer(
            "Это время недоступно.",
            show_alert=True
        )

        return

    start_at = local_dt(
        selected_date,
        selected_time
    )

    if (
        start_at
        <= datetime.now(TZ)
    ):

        await callback.answer(
            "Это время уже прошло.",
            show_alert=True
        )

        return

    await state.update_data(
        car_id=cid,
        start_date=date_iso,
        start_at=start_at.isoformat()
    )

    await state.set_state(
        Booking.end
    )

    await callback.message.edit_text(
        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"
        "Теперь выберите дату возврата."
    )

    await callback.message.answer(
        "📅 <b>Дата возврата</b>\n\n"
        "🟢 доступно\n"
        "🔴 занято\n"
        "⚪ недоступно",
        reply_markup=end_calendar_keyboard(
            cid,
            start_at,
            selected_date.year,
            selected_date.month
        )
    )

    await callback.answer()


# ============================================================
# BACK TO START TIME
# ============================================================

async def backstart(
    callback: CallbackQuery
):

    _, cid, start_iso = (
        callback.data.split(":")
    )

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


async def backstarttime(
    callback: CallbackQuery
):

    _, cid, start_iso = (
        callback.data.split(":")
    )

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
# END DATE CALENDAR
# ============================================================

def end_calendar_keyboard(
    car_id: str,
    start_at: datetime,
    year: int,
    month: int
):

    start_at = ensure_tz(
        start_at
    )

    first = date(
        year,
        month,
        1
    )

    next_first = (
        date(
            year + 1,
            1,
            1
        )
        if month == 12
        else date(
            year,
            month + 1,
            1
        )
    )

    previous_first = (
        date(
            year - 1,
            12,
            1
        )
        if month == 1
        else date(
            year,
            month - 1,
            1
        )
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
        "Декабрь"
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
                "Вс"
            ]
        ]
    ]

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop"
        )

        for _ in range(
            first.weekday()
        )
    ]

    for number in range(
        1,
        days + 1
    ):

        current = date(
            year,
            month,
            number
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

            if available(
                car_id,
                start_at,
                day_end
            ):

                text = f"🟢{number}"

                callback_data = (
                    f"end:"
                    f"{car_id}:"
                    f"{current.isoformat()}"
                )

            else:

                text = f"🔴{number}"

                callback_data = "noop"

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback_data
            )
        )

        if len(week) == 7:

            rows.append(week)

            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop"
                )
            )

        rows.append(week)

    rows.append(
        [
            InlineKeyboardButton(
                text="‹",
                callback_data=(
                    f"endmonth:"
                    f"{car_id}:"
                    f"{start_at.isoformat()}:"
                    f"{previous_first.isoformat()}"
                )
            ),

            InlineKeyboardButton(
                text=(
                    f"{months[month - 1]} "
                    f"{year}"
                ),
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
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=(
                    f"backend:"
                    f"{car_id}:"
                    f"{start_at.isoformat()}"
                )
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
    callback: CallbackQuery
):

    _, cid, start_iso, iso = (
        callback.data.split(":")
    )

    if cid not in CARS:

        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True
        )

        return

    start_at = ensure_tz(
        datetime.fromisoformat(
            start_iso
        )
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
# BACK TO END CALENDAR
# ============================================================

async def backend(
    callback: CallbackQuery
):

    _, cid, start_iso = (
        callback.data.split(
            ":",
            2
        )
    )

    start_at = ensure_tz(
        datetime.fromisoformat(
            start_iso
        )
    )

    start_d = start_at.date()

    await callback.message.edit_reply_markup(
        reply_markup=end_calendar_keyboard(
            cid,
            start_at,
            start_d.year,
            start_d.month
        )
    )

    await callback.answer()


# ============================================================
# END DAY
# ============================================================

async def end_day(
    callback: CallbackQuery,
    state: FSMContext
):

    _, cid, end_iso = (
        callback.data.split(":")
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

    start_at = ensure_tz(
        datetime.fromisoformat(
            data["start_at"]
        )
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

    await state.update_data(
        end_date=end_iso
    )

    title, keyboard = time_keyboard(
        cid,
        end_d,
        "return"
    )

    await state.set_state(
        Booking.end_time
    )

    await callback.message.edit_text(
        f"📅 Возврат:\n"
        f"<b>{end_d.strftime('%d.%m.%Y')}</b>\n\n"
        "⏰ Выберите время возврата:"
    )

    await callback.message.answer(
        title,
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# END TIME
#
# ВАЖНО:
# Эта функция находится ДО main().
# Именно её не хватало в ошибочном bot.py.
# ============================================================

async def end_time_handler(
    callback: CallbackQuery,
    state: FSMContext
):

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

    start_at = ensure_tz(
        datetime.fromisoformat(
            data["start_at"]
        )
    )

    end_d = date.fromisoformat(
        end_date_iso
    )

    try:

        hour, minute = map(
            int,
            time_text.split(":")
        )

        selected_time = time(
            hour,
            minute
        )

    except ValueError:

        await callback.answer(
            "Некорректное время.",
            show_alert=True
        )

        return

    if not is_valid_time(
        selected_time
    ):

        await callback.answer(
            "Это время недоступно.",
            show_alert=True
        )

        return

    end_at = local_dt(
        end_d,
        selected_time
    )

    if end_at <= start_at:

        await callback.answer(
            "Возврат должен быть позже получения.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # ФИНАЛЬНАЯ ПРОВЕРКА
    #
    # ВАЖНО:
    # psycopg синхронный, поэтому запрос выполняем
    # в отдельном потоке и не блокируем Telegram event loop.
    # --------------------------------------------------------

    is_available = await asyncio.to_thread(
        available,
        cid,
        start_at,
        end_at
    )

    if not is_available:

        await callback.answer(
            "Автомобиль уже занят.",
            show_alert=True
        )

        await callback.message.answer(
            "❌ Автомобиль уже занят на выбранный период.\n\n"
            "Пожалуйста, выберите другие дату или время."
        )

        return

    days = rental_days(
        start_at,
        end_at
    )

    rate = rate_for_days(
        cid,
        days
    )

    total = (
        days
        * rate
    )

    await state.update_data(
        car_id=cid,
        start_at=start_at.isoformat(),
        end_at=end_at.isoformat(),
        start_date=start_at.date().isoformat(),
        end_date=end_at.date().isoformat(),
        days=days,
        total=total
    )

    await state.set_state(
        Booking.name
    )

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
            "Похоже, номер слишком короткий.\n"
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
        "Если комментарий не нужен — отправьте «-»."
    )


# ============================================================
# CREATE BOOKING
# ============================================================

async def comment_handler(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    required = [
        "car_id",
        "start_at",
        "end_at",
        "name",
        "phone",
        "total",
        "days"
    ]

    if not all(
        key in data
        for key in required
    ):

        await state.clear()

        await message.answer(
            "Сессия бронирования устарела.\n\n"
            "Начните бронирование заново.",
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

    created_at = datetime.now(
        TZ
    )

    # --------------------------------------------------------
    # АТОМАРНОЕ СОЗДАНИЕ БРОНИ
    #
    # Блокировка PostgreSQL не позволяет двум клиентам
    # одновременно забронировать одну машину на один период.
    # --------------------------------------------------------

    con = db()

    try:

        with con.transaction():

            with con.cursor() as cur:

                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtext(
                            'balticar-bookings'
                        )
                    )
                    """
                )

                # --------------------------------------------
                # Истёкшие pending освобождаем.
                # --------------------------------------------

                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'expired'
                    WHERE status = 'pending'
                      AND expires_at IS NOT NULL
                      AND expires_at < NOW()
                    """
                )

                buffer_delta = timedelta(
                    hours=BUFFER_HOURS
                )

                check_start = (
                    start_at
                    - buffer_delta
                )

                check_end = (
                    end_at
                    + buffer_delta
                )

                overlap = cur.execute(
                    """
                    SELECT id, status
                    FROM bookings
                    WHERE car_id = %s
                      AND status IN (
                          'pending',
                          'confirmed'
                      )
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
                        "другой клиент занял выбранный "
                        "период.\n\n"
                        "Пожалуйста, выберите другие "
                        "дату или время.",
                        reply_markup=main_keyboard()
                    )

                    return

                start_date = start_at.date()

                end_date = end_at.date()

                row = cur.execute(
                    """
                    INSERT INTO bookings
                    (
                        user_id,
                        username,
                        car_id,
                        start_date,
                        end_date,
                        name,
                        phone,
                        comment,
                        total,
                        status,
                        created_at,
                        expires_at,
                        start_at,
                        end_at
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'pending',
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    RETURNING id
                    """,
                    (
                        message.from_user.id,
                        message.from_user.username or "",
                        cid,
                        start_date,
                        end_date,
                        data["name"],
                        data["phone"],
                        comment,
                        total,
                        created_at,
                        expires,
                        start_at,
                        end_at
                    )
                ).fetchone()

                bid = int(
                    row["id"]
                )

        await state.clear()

        # ----------------------------------------------------
        # CLIENT
        # ----------------------------------------------------

        await message.answer(
            f"📩 <b>Заявка №{bid} отправлена!</b>\n\n"

            f"🚗 {CARS[cid]['name']}\n\n"

            f"📅 Получение:\n"
            f"<b>{format_date_time(start_at)}</b>\n\n"

            f"📅 Возврат:\n"
            f"<b>{format_date_time(end_at)}</b>\n\n"

            f"⏱ {days} суток\n"

            f"💰 <b>{money(total)}</b>\n\n"

            f"⏳ Заявка удерживает выбранный "
            f"период до "
            f"{expires.strftime('%d.%m.%Y %H:%M')}.\n\n"

            "Мы свяжемся с вами после подтверждения заявки.",
            reply_markup=main_keyboard()
        )

        # ----------------------------------------------------
        # ADMIN
        # ----------------------------------------------------

        if ADMIN_ID:

            username = (
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

                f"Telegram: {username}\n"

                f"📝 {comment or '—'}\n\n"

                f"⏳ Ожидает подтверждения до "
                f"{expires.strftime('%d.%m.%Y %H:%M')}"
            )

            await message.bot.send_message(
                ADMIN_ID,
                text,
                reply_markup=admin_buttons(bid)
            )

    finally:

        con.close()


# ============================================================
# MY BOOKINGS
# ============================================================

def status_label(
    status: str
) -> str:

    return {
        "pending": "🟡 Ожидает подтверждения",
        "confirmed": "🟢 Подтверждена",
        "rejected": "🔴 Отклонена",
        "expired": "⚪ Истекла"
    }.get(
        status,
        status
    )


async def mybookings(
    callback: CallbackQuery
):

    await asyncio.to_thread(
        cleanup_pending
    )

    con = db()

    try:

        rows = con.execute(
            """
            SELECT *
            FROM bookings
            WHERE user_id = %s
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
            "У вас пока нет заявок.\n\n"
            "Вы можете выбрать автомобиль "
            "и оформить бронирование."
        )

    else:

        out = [
            "📋 <b>Мои заявки</b>"
        ]

        for row in rows:

            car = CARS.get(
                row["car_id"],
                {
                    "name": row["car_id"]
                }
            )

            start_at = row["start_at"]

            end_at = row["end_at"]

            if start_at:

                start_text = format_date_time(
                    start_at
                )

            else:

                start_text = (
                    str(row["start_date"])
                )

            if end_at:

                end_text = format_date_time(
                    end_at
                )

            else:

                end_text = (
                    str(row["end_date"])
                )

            if start_at and end_at:

                days = rental_days(
                    start_at,
                    end_at
                )

            else:

                days = (
                    date.fromisoformat(
                        str(row["end_date"])
                    )
                    - date.fromisoformat(
                        str(row["start_date"])
                    )
                ).days

            out.append(
                f"\n<b>№{row['id']} — "
                f"{car['name']}</b>\n"

                f"📅 {start_text} — "
                f"{end_text}\n"

                f"⏱ {days} суток\n"

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

        "• Бронирование оформляется после "
        "подтверждения заявки.\n"

        "• Цена рассчитывается автоматически "
        "по количеству суток.\n"

        "• Выбранные даты временно удерживаются "
        "за клиентом до подтверждения заявки.\n"

        "• Если заявка не подтверждена "
        "в установленный срок, удержание "
        "автоматически снимается.\n"

        "• Детали получения и возврата автомобиля "
        "согласовываются с менеджером.",
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

        "Также можно оформить заявку прямо здесь — "
        "менеджер свяжется с вами после её получения.",

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
# START
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


# ============================================================
# HOME
# ============================================================

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


# ============================================================
# ID
# ============================================================

async def id_handler(
    message: Message
):

    await message.answer(
        f"Ваш Telegram ID: "
        f"<code>{message.from_user.id}</code>"
    )


# ============================================================
# CATALOG
# ============================================================

async def catalog(
    callback: CallbackQuery
):

    await callback.message.edit_text(
        "🚗 <b>Автомобили Balticar</b>\n\n"

        "Выберите автомобиль, чтобы "
        "посмотреть фото, характеристики "
        "и стоимость:",

        reply_markup=car_keyboard()
    )

    await callback.answer()


# ============================================================
# CAR SELECTED
# ============================================================

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

    await send_car(
        callback.bot,
        callback.message.chat.id,
        cid
    )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================

def admin_buttons(
    bid: int
):

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
                )
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
            ]
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


async def admin_panel(
    message: Message
):

    if (
        message.from_user.id
        != ADMIN_ID
    ):

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

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

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

def admin_busy_calendar_keyboard(
    car_id: str,
    year: int,
    month: int
):

    first = date(
        year,
        month,
        1
    )

    next_first = (
        date(
            year + 1,
            1,
            1
        )
        if month == 12
        else date(
            year,
            month + 1,
            1
        )
    )

    previous_first = (
        date(
            year - 1,
            12,
            1
        )
        if month == 1
        else date(
            year,
            month - 1,
            1
        )
    )

    days = (
        next_first - first
    ).days

    today = datetime.now(
        TZ
    ).date()

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
        "Декабрь"
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
                "Вс"
            ]
        ]
    ]

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop"
        )

        for _ in range(
            first.weekday()
        )
    ]

    con = db()

    try:

        month_start = local_dt(
            first,
            time(0, 0)
        )

        month_end = local_dt(
            next_first,
            time(0, 0)
        )

        bookings = con.execute(
            """
            SELECT *
            FROM bookings
            WHERE car_id = %s
              AND status IN (
                  'pending',
                  'confirmed'
              )
              AND start_at < %s
              AND end_at > %s
            """,
            (
                car_id,
                month_end,
                month_start
            )
        ).fetchall()

    finally:

        con.close()

    for number in range(
        1,
        days + 1
    ):

        current = date(
            year,
            month,
            number
        )

        if current < today:

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

            status = None

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

                    if (
                        row["status"]
                        == "confirmed"
                    ):

                        status = "confirmed"

                    elif status != "confirmed":

                        status = "pending"

            if status == "confirmed":

                text = f"🔴{number}"

                callback_data = (
                    f"adminday:"
                    f"{car_id}:"
                    f"{current.isoformat()}"
                )

            elif status == "pending":

                text = f"🟡{number}"

                callback_data = (
                    f"adminday:"
                    f"{car_id}:"
                    f"{current.isoformat()}"
                )

            else:

                text = f"🟢{number}"

                callback_data = (
                    f"adminday:"
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

            rows.append(week)

            week = []

    if week:

        while len(week) < 7:

            week.append(
                InlineKeyboardButton(
                    text=" ",
                    callback_data="noop"
                )
            )

        rows.append(week)

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
                text=(
                    f"{months[month - 1]} "
                    f"{year}"
                ),
                callback_data="noop"
            ),

            InlineKeyboardButton(
                text="›",
                callback_data=(
                    f"adminmonth:"
                    f"{car_id}:"
                    f"{next_first.isoformat()}"
                )
            )
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


async def admin_calendar(
    callback: CallbackQuery
):

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

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

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

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

    today = datetime.now(
        TZ
    ).date()

    await callback.message.edit_text(
        "📅 <b>Календарь занятости</b>\n\n"

        f"🚗 <b>{CARS[cid]['name']}</b>\n\n"

        "🟢 свободно\n"
        "🟡 ожидает подтверждения\n"
        "🔴 подтверждено\n"
        "⚪ прошедшая дата",

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

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    _, cid, iso = (
        callback.data.split(":")
    )

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


async def admin_day(
    callback: CallbackQuery
):

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    _, car_id, iso = (
        callback.data.split(":")
    )

    selected = date.fromisoformat(
        iso
    )

    await asyncio.to_thread(
        cleanup_pending
    )

    con = db()

    try:

        day_start = local_dt(
            selected,
            time(0, 0)
        )

        day_end = (
            day_start
            + timedelta(days=1)
        )

        row = con.execute(
            """
            SELECT *
            FROM bookings
            WHERE car_id = %s
              AND status IN (
                  'pending',
                  'confirmed'
              )
              AND start_at < %s
              AND end_at > %s
            ORDER BY
                CASE
                    WHEN status = 'confirmed'
                    THEN 1
                    WHEN status = 'pending'
                    THEN 2
                    ELSE 3
                END,
                id
            LIMIT 1
            """,
            (
                car_id,
                day_end,
                day_start
            )
        ).fetchone()

    finally:

        con.close()

    if not row:

        await callback.answer(
            "В этот день бронирований нет.",
            show_alert=True
        )

        return

    start_at = ensure_tz(
        row["start_at"]
    )

    end_at = ensure_tz(
        row["end_at"]
    )

    await callback.message.answer(
        f"📋 <b>Бронирование №{row['id']}</b>\n\n"

        f"🚗 {CARS[car_id]['name']}\n"

        f"{status_label(row['status'])}\n\n"

        f"📅 {format_date_time(start_at)}\n"
        f"— {format_date_time(end_at)}\n\n"

        f"👤 {row['name']}\n"

        f"📞 {row['phone']}\n"

        f"💰 {money(row['total'])}\n"

        f"📝 {row['comment'] or '—'}"
    )

    await callback.answer()


# ============================================================
# ADMIN NEW
# ============================================================

async def admin_new(
    callback: CallbackQuery
):

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await asyncio.to_thread(
        cleanup_pending
    )

    con = db()

    try:

        rows = con.execute(
            """
            SELECT *
            FROM bookings
            WHERE status = 'pending'
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

        f"Найдено заявок: "
        f"<b>{len(rows)}</b>\n\n"

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

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

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

    await asyncio.to_thread(
        cleanup_pending
    )

    con = db()

    try:

        row = con.execute(
            """
            SELECT *
            FROM bookings
            WHERE id = %s
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

    start_at = ensure_tz(
        row["start_at"]
    )

    end_at = ensure_tz(
        row["end_at"]
    )

    days = rental_days(
        start_at,
        end_at
    )

    username = (
        f"@{row['username']}"
        if row["username"]
        else "нет username"
    )

    text = (
        f"📋 <b>Заявка №{bid}</b>\n\n"

        f"🚗 <b>{CARS[row['car_id']]['name']}</b>\n"

        f"📅 Получение:\n"
        f"<b>{format_date_time(start_at)}</b>\n\n"

        f"📅 Возврат:\n"
        f"<b>{format_date_time(end_at)}</b>\n\n"

        f"⏱ {days} суток\n"

        f"💰 <b>{money(row['total'])}</b>\n\n"

        f"👤 {row['name']}\n"

        f"📞 {row['phone']}\n"

        f"Telegram: {username}\n"

        f"📝 {row['comment'] or '—'}\n\n"

        f"Статус: "
        f"{status_label(row['status'])}"
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

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await asyncio.to_thread(
        cleanup_pending
    )

    con = db()

    try:

        rows = con.execute(
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

    keyboard = []

    for row in rows:

        keyboard.append(
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
        )

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

        f"Показаны последние "
        f"{len(rows)} заявок.",

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

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=(
                    f"admincarinfo:{cid}"
                )
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

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

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
# ADMIN ACTION
# ============================================================

async def admin_action(
    callback: CallbackQuery
):

    if (
        callback.from_user.id
        != ADMIN_ID
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    action, bid_s = (
        callback.data.split(":")
    )

    try:

        bid = int(bid_s)

    except ValueError:

        await callback.answer(
            "Некорректный номер заявки.",
            show_alert=True
        )

        return

    await asyncio.to_thread(
        cleanup_pending
    )

    con = db()

    try:

        row = con.execute(
            """
            SELECT *
            FROM bookings
            WHERE id = %s
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

    if row["status"] != "pending":

        await callback.answer(
            "Заявка уже обработана.",
            show_alert=True
        )

        return

    # ========================================================
    # CONFIRM
    # ========================================================

    if action == "confirm":

        start_at = ensure_tz(
            row["start_at"]
        )

        end_at = ensure_tz(
            row["end_at"]
        )

        con = db()

        conflict_found = False

        updated = None

        try:

            with con.transaction():

                with con.cursor() as cur:

                    cur.execute(
                        """
                        SELECT pg_advisory_xact_lock(
                            hashtext(
                                'balticar-bookings'
                            )
                        )
                        """
                    )

                    cur.execute(
                        """
                        UPDATE bookings
                        SET status = 'expired'
                        WHERE status = 'pending'
                          AND expires_at IS NOT NULL
                          AND expires_at < NOW()
                        """
                    )

                    buffer_delta = timedelta(
                        hours=BUFFER_HOURS
                    )

                    check_start = (
                        start_at
                        - buffer_delta
                    )

                    check_end = (
                        end_at
                        + buffer_delta
                    )

                    conflict = cur.execute(
                        """
                        SELECT id
                        FROM bookings
                        WHERE car_id = %s
                          AND id <> %s
                          AND status IN (
                              'pending',
                              'confirmed'
                          )
                          AND start_at < %s
                          AND end_at > %s
                        LIMIT 1
                        """,
                        (
                            row["car_id"],
                            bid,
                            check_end,
                            check_start
                        )
                    ).fetchone()

                    if conflict:

                        cur.execute(
                            """
                            UPDATE bookings
                            SET status = 'rejected'
                            WHERE id = %s
                              AND status = 'pending'
                            """,
                            (bid,)
                        )

                        conflict_found = True

                    else:

                        updated = cur.execute(
                            """
                            UPDATE bookings
                            SET status = 'confirmed',
                                expires_at = NULL
                            WHERE id = %s
                              AND status = 'pending'
                            RETURNING id
                            """,
                            (bid,)
                        ).fetchone()

        finally:

            con.close()

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

                    f"❌ <b>Заявка №{bid} "
                    f"отклонена</b>\n\n"

                    "Выбранный период уже занят.",

                    reply_markup=main_keyboard()
                )

            except Exception as e:

                print(
                    f"Ошибка уведомления клиента: "
                    f"{e}"
                )

            await callback.answer(
                "Даты уже заняты.",
                show_alert=True
            )

            return

        if not updated:

            await callback.answer(
                "Заявка уже обработана.",
                show_alert=True
            )

            return

        try:

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        await callback.bot.send_message(
            row["user_id"],

            f"✅ <b>Заявка №{bid} "
            f"подтверждена!</b>\n\n"

            f"🚗 {CARS[row['car_id']]['name']}\n\n"

            f"📅 Получение:\n"
            f"<b>{format_date_time(start_at)}</b>\n\n"

            f"📅 Возврат:\n"
            f"<b>{format_date_time(end_at)}</b>\n\n"

            f"⏱ {rental_days(start_at, end_at)} суток\n"

            f"💰 {money(row['total'])}\n\n"

            "Менеджер свяжется с вами "
            "для согласования деталей "
            "получения автомобиля.",

            reply_markup=main_keyboard()
        )

        await callback.answer(
            "Заявка подтверждена."
        )

        return

    # ========================================================
    # REJECT
    # ========================================================

    if action == "reject":

        con = db()

        try:

            with con.transaction():

                result = con.execute(
                    """
                    UPDATE bookings
                    SET status = 'rejected'
                    WHERE id = %s
                      AND status = 'pending'
                    """,
                    (bid,)
                )

        finally:

            con.close()

        if result.rowcount == 0:

            await callback.answer(
                "Заявка уже обработана.",
                show_alert=True
            )

            return

        try:

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        await callback.bot.send_message(
            row["user_id"],

            f"❌ <b>Заявка №{bid} "
            f"отклонена.</b>\n\n"

            "Если хотите, вы можете выбрать "
            "другой автомобиль или другие даты.",

            reply_markup=main_keyboard()
        )

        await callback.answer(
            "Заявка отклонена."
        )

        return

    await callback.answer(
        "Неизвестное действие.",
        show_alert=True
    )


# ============================================================
# PUBLISH
# ============================================================

async def publish(
    message: Message
):

    if (
        message.from_user.id
        != ADMIN_ID
    ):

        return

    try:

        me = await message.bot.get_me()

        if not me.username:

            await message.answer(
                "У бота отсутствует username."
            )

            return

        text = (
            "🚗 <b>Balticar — аренда автомобилей "
            "в Калининграде</b>\n\n"

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
                            f"{me.username}"
                        )
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
            "Готово: пост с кнопкой "
            "опубликован в канале."
        )

    except Exception as e:

        print(
            f"Ошибка публикации: {e}"
        )

        await message.answer(
            "❌ Не удалось опубликовать пост.\n"
            f"Ошибка: {e}"
        )


# ============================================================
# HEALTH
# ============================================================

async def health(
    _: web.Request
):

    return web.Response(
        text="Balticar bot is running"
    )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

async def telegram_webhook(
    request: web.Request
):

    bot = request.app["bot"]

    dp = request.app["dp"]

    if WEBHOOK_SECRET:

        incoming_secret = (
            request.headers.get(
                "X-Telegram-Bot-Api-Secret-Token"
            )
        )

        if (
            incoming_secret
            != WEBHOOK_SECRET
        ):

            raise web.HTTPForbidden(
                text="Invalid webhook secret"
            )

    try:

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

    except Exception as e:

        print(
            f"Webhook error: {e}"
        )

        return web.Response(
            status=500,
            text="Webhook error"
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не задан "
            "в Environment Variables."
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    await asyncio.to_thread(
        init_db
    )

    print(
        "Neon PostgreSQL connected successfully."
    )

    # --------------------------------------------------------
    # BOT
    # --------------------------------------------------------

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

    # ========================================================
    # ВОТ ЭТА РЕГИСТРАЦИЯ ТЕПЕРЬ КОРРЕКТНА:
    #
    # end_time_handler объявлена выше.
    # ========================================================

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

    # ========================================================
    # NOOP
    # ========================================================

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
    # RENDER
    # ========================================================

    external_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        ""
    ).strip().rstrip("/")

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

        drop_pending_updates=False
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
        f"Timezone: "
        f"{TZ}"
    )

    print(
        f"Pickup time: "
        f"{PICKUP_START_HOUR:02d}:00 - "
        f"{PICKUP_END_HOUR:02d}:00"
    )

    print(
        f"Buffer: "
        f"{BUFFER_HOURS} hours"
    )

    print(
        "Database: PostgreSQL / Neon"
    )

    print(
        "========================================"
    )

    # --------------------------------------------------------
    # WEB APP
    # --------------------------------------------------------

    app = web.Application()

    app["bot"] = bot

    app["dp"] = dp

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
        f"Server started on port {PORT}"
    )

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

        await bot.session.close()


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
