import asyncio
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@Balticar_kgd")

DB_PATH = Path(os.getenv("DB_PATH", "data/balticar.sqlite3"))

HOLD_MINUTES = int(os.getenv("PENDING_HOLD_MINUTES", "60"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Kaliningrad"))
PORT = int(os.getenv("PORT", "10000"))

WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


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
# СОСТОЯНИЯ БРОНИРОВАНИЯ
# ============================================================

class Booking(StatesGroup):
    start = State()
    end = State()
    name = State()
    phone = State()
    comment = State()


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    return con


def init_db():
    con = db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            car_id TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            comment TEXT,
            total INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS cars (
            car_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)

    for cid in CARS:
        con.execute(
            """
            INSERT OR IGNORE INTO cars (car_id, enabled)
            VALUES (?, 1)
            """,
            (cid,),
        )

    con.commit()
    con.close()


def cleanup_pending():
    con = db()

    now = datetime.now(TZ).isoformat()

    con.execute(
        """
        UPDATE bookings
        SET status = 'expired'
        WHERE status = 'pending'
          AND expires_at IS NOT NULL
          AND expires_at < ?
        """,
        (now,),
    )

    con.commit()
    con.close()


def is_car_enabled(car_id):
    con = db()

    row = con.execute(
        """
        SELECT enabled
        FROM cars
        WHERE car_id = ?
        """,
        (car_id,),
    ).fetchone()

    con.close()

    if row is None:
        return True

    return bool(row["enabled"])


def set_car_enabled(car_id, enabled):
    con = db()

    con.execute(
        """
        INSERT INTO cars (car_id, enabled)
        VALUES (?, ?)
        ON CONFLICT(car_id)
        DO UPDATE SET enabled = excluded.enabled
        """,
        (car_id, 1 if enabled else 0),
    )

    con.commit()
    con.close()


# ============================================================
# ПРОВЕРКА ДОСТУПНОСТИ
# ============================================================

def available(car_id, start_d, end_d, exclude_booking_id=None):
    cleanup_pending()

    if not is_car_enabled(car_id):
        return False

    con = db()

    query = """
        SELECT id
        FROM bookings
        WHERE car_id = ?
          AND status IN ('pending', 'confirmed')
          AND date(start_date) < date(?)
          AND date(end_date) > date(?)
    """

    params = [
        car_id,
        end_d.isoformat(),
        start_d.isoformat(),
    ]

    if exclude_booking_id is not None:
        query += " AND id <> ?"
        params.append(exclude_booking_id)

    query += " LIMIT 1"

    row = con.execute(query, params).fetchone()

    con.close()

    return row is None


def booking_overlaps_existing(
    car_id,
    start_d,
    end_d,
    exclude_booking_id=None,
):
    return not available(
        car_id,
        start_d,
        end_d,
        exclude_booking_id=exclude_booking_id,
    )


# ============================================================
# ЦЕНЫ
# ============================================================

def rate_for_days(car_id, days):
    rates = CARS[car_id]["rates"]

    if days <= 3:
        return rates[0]

    if days <= 6:
        return rates[1]

    return rates[2]


def money(n):
    return f"{n:,}".replace(",", " ") + " ₽"


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def car_keyboard():
    rows = []

    for cid, car in CARS.items():
        if not is_car_enabled(cid):
            continue

        rows.append([
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=f"car:{cid}",
            )
        ])

    if not rows:
        rows.append([
            InlineKeyboardButton(
                text="😔 Автомобилей пока нет",
                callback_data="noop",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def admin_panel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔔 Новые заявки",
                    callback_data="admin:new",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Календарь занятости",
                    callback_data="admin:calendar",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Все бронирования",
                    callback_data="admin:bookings",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚗 Автомобили",
                    callback_data="admin:cars",
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
                    callback_data="admin:back",
                )
            ]
        ]
    )


def admin_booking_buttons(bid):
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
            ],
            [
                InlineKeyboardButton(
                    text="📄 Договор",
                    callback_data=f"contract:{bid}",
                )
            ],
        ]
    )


# ============================================================
# КАЛЕНДАРЬ КЛИЕНТА
# ============================================================

def calendar_keyboard(car_id, year, month):
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    previous_day = first - timedelta(days=1)

    previous_first = date(
        previous_day.year,
        previous_day.month,
        1,
    )

    days = (next_first - first).days
    today = datetime.now(TZ).date()

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

    rows = []

    rows.append([
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
    ])

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop",
        )
        for _ in range(first.weekday())
    ]

    for d in range(1, days + 1):
        current = date(year, month, d)

        if current < today:
            text = "⚪"
            callback = "noop"

        elif not is_car_enabled(car_id):
            text = "⚪"
            callback = "noop"

        elif not available(
            car_id,
            current,
            current + timedelta(days=1),
        ):
            text = f"🔴{d}"
            callback = "noop"

        else:
            text = f"🟢{d}"
            callback = f"day:{car_id}:{current.isoformat()}"

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback,
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
                    callback_data="noop",
                )
            )

        rows.append(week)

    rows.append([
        InlineKeyboardButton(
            text="‹",
            callback_data=(
                f"month:{car_id}:"
                f"{previous_first.isoformat()}"
            ),
        ),
        InlineKeyboardButton(
            text=f"{months[month - 1]} {year}",
            callback_data="noop",
        ),
        InlineKeyboardButton(
            text="›",
            callback_data=(
                f"month:{car_id}:"
                f"{next_first.isoformat()}"
            ),
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ К автомобилям",
            callback_data="catalog",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# КАЛЕНДАРЬ ДАТЫ ВОЗВРАТА
# ============================================================

def end_calendar_keyboard(car_id, start_d, year, month):
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    previous_day = first - timedelta(days=1)

    previous_first = date(
        previous_day.year,
        previous_day.month,
        1,
    )

    days = (next_first - first).days

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

    rows = [[
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
    ]]

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop",
        )
        for _ in range(first.weekday())
    ]

    for d in range(1, days + 1):
        current = date(year, month, d)

        if current <= start_d:
            text = "⚪"
            callback = "noop"

        elif not available(
            car_id,
            start_d,
            current,
        ):
            text = f"🔴{d}"
            callback = "noop"

        else:
            text = f"🟢{d}"
            callback = f"end:{car_id}:{current.isoformat()}"

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback,
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
                    callback_data="noop",
                )
            )

        rows.append(week)

    rows.append([
        InlineKeyboardButton(
            text="‹",
            callback_data=(
                f"endmonth:{car_id}:"
                f"{start_d.isoformat()}:"
                f"{previous_first.isoformat()}"
            ),
        ),
        InlineKeyboardButton(
            text=f"{months[month - 1]} {year}",
            callback_data="noop",
        ),
        InlineKeyboardButton(
            text="›",
            callback_data=(
                f"endmonth:{car_id}:"
                f"{start_d.isoformat()}:"
                f"{next_first.isoformat()}"
            ),
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ К автомобилям",
            callback_data="catalog",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# АДМИНСКИЙ КАЛЕНДАРЬ
# ============================================================

def admin_busy_calendar_keyboard(car_id, year, month):
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    previous_day = first - timedelta(days=1)

    previous_first = date(
        previous_day.year,
        previous_day.month,
        1,
    )

    days = (next_first - first).days
    today = datetime.now(TZ).date()

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

    rows = []

    rows.append([
        InlineKeyboardButton(
            text=day,
            callback_data="noop",
        )
        for day in [
            "Пн",
            "Вт",
            "Ср",
            "Чт",
            "Пт",
            "Сб",
            "Вс",
        ]
    ])

    week = [
        InlineKeyboardButton(
            text=" ",
            callback_data="noop",
        )
        for _ in range(first.weekday())
    ]

    for day_number in range(1, days + 1):
        current = date(year, month, day_number)

        status = "free"

        if current < today:
            status = "past"

        else:
            cleanup_pending()

            con = db()

            confirmed = con.execute(
                """
                SELECT id
                FROM bookings
                WHERE car_id = ?
                  AND status = 'confirmed'
                  AND date(start_date) <= date(?)
                  AND date(end_date) > date(?)
                LIMIT 1
                """,
                (
                    car_id,
                    current.isoformat(),
                    current.isoformat(),
                ),
            ).fetchone()

            pending = con.execute(
                """
                SELECT id
                FROM bookings
                WHERE car_id = ?
                  AND status = 'pending'
                  AND date(start_date) <= date(?)
                  AND date(end_date) > date(?)
                LIMIT 1
                """,
                (
                    car_id,
                    current.isoformat(),
                    current.isoformat(),
                ),
            ).fetchone()

            con.close()

            if confirmed:
                status = "confirmed"

            elif pending:
                status = "pending"

        if status == "confirmed":
            text = f"🔴{day_number}"

        elif status == "pending":
            text = f"🟡{day_number}"

        elif status == "past":
            text = "⚪"

        else:
            text = f"🟢{day_number}"

        callback_data = (
            "noop"
            if status == "past"
            else f"adminday:{car_id}:{current.isoformat()}"
        )

        week.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback_data,
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
                    callback_data="noop",
                )
            )

        rows.append(week)

    rows.append([
        InlineKeyboardButton(
            text="‹",
            callback_data=(
                f"adminmonth:{car_id}:"
                f"{previous_first.isoformat()}"
            ),
        ),
        InlineKeyboardButton(
            text=f"{months[month - 1]} {year}",
            callback_data="noop",
        ),
        InlineKeyboardButton(
            text="›",
            callback_data=(
                f"adminmonth:{car_id}:"
                f"{next_first.isoformat()}"
            ),
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ К автомобилям",
            callback_data="admin:calendar",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# ТЕКСТ АВТОМОБИЛЯ
# ============================================================

def car_text(cid):
    car = CARS[cid]

    enabled_text = (
        "🟢 Доступен для бронирования"
        if is_car_enabled(cid)
        else "🔴 Временно недоступен"
    )

    return (
        f"<b>{car['name']}</b>\n"
        f"⚙️ {car['gear']}\n"
        f"{enabled_text}\n\n"
        f"💰 1–3 суток: "
        f"<b>{money(car['rates'][0])}/сутки</b>\n"
        f"💰 4–6 суток: "
        f"<b>{money(car['rates'][1])}/сутки</b>\n"
        f"💰 7+ суток: "
        f"<b>{money(car['rates'][2])}/сутки</b>"
    )


async def send_car(bot, chat_id, cid):
    car = CARS[cid]

    for photo in car["photos"]:
        if Path(photo).exists():
            await bot.send_photo(
                chat_id,
                FSInputFile(photo),
            )

    if not is_car_enabled(cid):
        await bot.send_message(
            chat_id,
            car_text(cid),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="◀️ К автомобилям",
                            callback_data="catalog",
                        )
                    ]
                ]
            ),
        )
        return

    await bot.send_message(
        chat_id,
        car_text(cid),
        reply_markup=InlineKeyboardMarkup(
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
        ),
    )


# ============================================================
# АДМИН: ФОРМАТ ЗАЯВКИ
# ============================================================

def booking_status_text(status):
    return {
        "pending": "🟡 Ожидает подтверждения",
        "confirmed": "✅ Подтверждена",
        "rejected": "❌ Отклонена",
        "expired": "⌛ Истекла",
    }.get(status, status)


def format_booking(row):
    start_d = date.fromisoformat(row["start_date"])
    end_d = date.fromisoformat(row["end_date"])

    return (
        f"📋 <b>Бронирование №{row['id']}</b>\n\n"
        f"🚗 <b>{CARS[row['car_id']]['name']}</b>\n"
        f"⚙️ {CARS[row['car_id']]['gear']}\n"
        f"📅 {start_d.strftime('%d.%m.%Y')} — "
        f"{end_d.strftime('%d.%m.%Y')}\n"
        f"⏱ {(end_d - start_d).days} суток\n"
        f"💰 <b>{money(row['total'])}</b>\n\n"
        f"👤 <b>{row['name']}</b>\n"
        f"📞 {row['phone']}\n"
        f"Telegram: "
        f"@{row['username'] if row['username'] else 'без username'}\n"
        f"📝 {row['comment'] or '—'}\n\n"
        f"{booking_status_text(row['status'])}"
    )


# ============================================================
# START
# ============================================================

async def start_handler(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "🚗 <b>Balticar</b>\n\n"
        "Аренда автомобилей в Калининграде.\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard(),
    )


async def id_handler(message: Message):
    await message.answer(
        f"Ваш Telegram ID: "
        f"<code>{message.from_user.id}</code>"
    )


# ============================================================
# КАТАЛОГ
# ============================================================

async def catalog(callback: CallbackQuery):
    await callback.message.answer(
        "🚗 <b>Выберите автомобиль:</b>",
        reply_markup=car_keyboard(),
    )

    await callback.answer()


async def car_selected(callback: CallbackQuery):
    cid = callback.data.split(":", 1)[1]

    if cid not in CARS:
        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    await send_car(
        callback.bot,
        callback.message.chat.id,
        cid,
    )

    await callback.answer()


# ============================================================
# ВЫБОР ДАТ
# ============================================================

async def pick_dates(callback: CallbackQuery):
    cid = callback.data.split(":", 1)[1]

    if not is_car_enabled(cid):
        await callback.answer(
            "Этот автомобиль временно недоступен.",
            show_alert=True,
        )
        return

    today = datetime.now(TZ).date()

    await callback.message.answer(
        f"📅 <b>{CARS[cid]['name']}</b>\n\n"
        "🟢 свободно   🔴 занято   ⚪ недоступно\n\n"
        "Выберите дату получения:",
        reply_markup=calendar_keyboard(
            cid,
            today.year,
            today.month,
        ),
    )

    await callback.answer()


async def month(callback: CallbackQuery):
    _, cid, iso = callback.data.split(":")

    d = date.fromisoformat(iso)

    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(
            cid,
            d.year,
            d.month,
        )
    )

    await callback.answer()


async def start_day(
    callback: CallbackQuery,
    state: FSMContext,
):
    _, cid, iso = callback.data.split(":")

    start_d = date.fromisoformat(iso)

    if not is_car_enabled(cid):
        await callback.answer(
            "Автомобиль временно недоступен.",
            show_alert=True,
        )
        return

    if not available(
        cid,
        start_d,
        start_d + timedelta(days=1),
    ):
        await callback.answer(
            "Эта дата уже занята.",
            show_alert=True,
        )
        return

    await state.update_data(
        car_id=cid,
        start_date=iso,
    )

    await state.set_state(Booking.end)

    await callback.message.answer(
        f"📅 Получение: "
        f"<b>{start_d.strftime('%d.%m.%Y')}</b>\n\n"
        "Теперь выберите дату возврата:",
        reply_markup=end_calendar_keyboard(
            cid,
            start_d,
            start_d.year,
            start_d.month,
        ),
    )

    await callback.answer()


async def endmonth(callback: CallbackQuery):
    _, cid, start_iso, iso = callback.data.split(":")

    start_d = date.fromisoformat(start_iso)
    d = date.fromisoformat(iso)

    await callback.message.edit_reply_markup(
        reply_markup=end_calendar_keyboard(
            cid,
            start_d,
            d.year,
            d.month,
        )
    )

    await callback.answer()


async def end_day(
    callback: CallbackQuery,
    state: FSMContext,
):
    _, cid, end_iso = callback.data.split(":")

    data = await state.get_data()

    if not data.get("start_date"):
        await callback.answer(
            "Сессия бронирования истекла.",
            show_alert=True,
        )
        await state.clear()
        return

    start_d = date.fromisoformat(
        data["start_date"]
    )

    end_d = date.fromisoformat(end_iso)

    if end_d <= start_d:
        await callback.answer(
            "Дата возврата должна быть позже даты получения.",
            show_alert=True,
        )
        return

    if not available(
        cid,
        start_d,
        end_d,
    ):
        await callback.answer(
            "Эти даты уже заняты.",
            show_alert=True,
        )
        return

    days = (end_d - start_d).days

    total = (
        days *
        rate_for_days(cid, days)
    )

    await state.update_data(
        end_date=end_iso,
        days=days,
        total=total,
    )

    await state.set_state(Booking.name)

    await callback.message.answer(
        f"✅ Даты: "
        f"<b>{start_d.strftime('%d.%m.%Y')} — "
        f"{end_d.strftime('%d.%m.%Y')}</b>\n"
        f"⏱ {days} суток\n"
        f"💰 <b>{money(total)}</b>\n\n"
        "Введите ваше имя:",
    )

    await callback.answer()


# ============================================================
# ДАННЫЕ КЛИЕНТА
# ============================================================

async def name_handler(
    message: Message,
    state: FSMContext,
):
    text = (message.text or "").strip()

    if len(text) < 2:
        await message.answer(
            "Пожалуйста, введите имя."
        )
        return

    await state.update_data(name=text)

    await state.set_state(Booking.phone)

    await message.answer(
        "📞 Введите номер телефона:"
    )


async def phone_handler(
    message: Message,
    state: FSMContext,
):
    phone = (message.text or "").strip()

    if len(phone) < 7:
        await message.answer(
            "Похоже, номер слишком короткий. "
            "Введите телефон ещё раз."
        )
        return

    await state.update_data(phone=phone)

    await state.set_state(Booking.comment)

    await message.answer(
        "📝 Комментарий к заказу "
        "(или отправьте «-»):"
    )


# ============================================================
# СОЗДАНИЕ БРОНИРОВАНИЯ
# ============================================================

async def comment_handler(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    if not data.get("car_id"):
        await state.clear()

        await message.answer(
            "Сессия бронирования истекла. "
            "Начните заново: /start"
        )
        return

    comment_text = (message.text or "").strip()

    comment = (
        ""
        if comment_text == "-"
        else comment_text
    )

    cid = data["car_id"]

    start_d = date.fromisoformat(
        data["start_date"]
    )

    end_d = date.fromisoformat(
        data["end_date"]
    )

    if not is_car_enabled(cid):
        await message.answer(
            "К сожалению, этот автомобиль "
            "стал недоступен. Начните заново: /start"
        )

        await state.clear()
        return

    if not available(
        cid,
        start_d,
        end_d,
    ):
        await message.answer(
            "К сожалению, автомобиль только что "
            "забронировали на эти даты.\n\n"
            "Начните заново: /start"
        )

        await state.clear()
        return

    expires = (
        datetime.now(TZ)
        + timedelta(minutes=HOLD_MINUTES)
    )

    con = db()

    # Повторная проверка непосредственно перед INSERT.
    conflict = con.execute(
        """
        SELECT id
        FROM bookings
        WHERE car_id = ?
          AND status IN ('pending', 'confirmed')
          AND date(start_date) < date(?)
          AND date(end_date) > date(?)
        LIMIT 1
        """,
        (
            cid,
            end_d.isoformat(),
            start_d.isoformat(),
        ),
    ).fetchone()

    if conflict:
        con.close()

        await message.answer(
            "❌ Эти даты уже заняты другим клиентом.\n\n"
            "Начните заново: /start"
        )

        await state.clear()
        return

    cur = con.execute(
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
            expires_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            message.from_user.id,
            message.from_user.username or "",
            cid,
            start_d.isoformat(),
            end_d.isoformat(),
            data["name"],
            data["phone"],
            comment,
            data["total"],
            datetime.now(TZ).isoformat(),
            expires.isoformat(),
        ),
    )

    bid = cur.lastrowid

    con.commit()
    con.close()

    await state.clear()

    await message.answer(
        f"📩 <b>Заявка №{bid} отправлена!</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n"
        f"📅 {start_d.strftime('%d.%m.%Y')} — "
        f"{end_d.strftime('%d.%m.%Y')}\n"
        f"⏱ {data['days']} суток\n"
        f"💰 <b>{money(data['total'])}</b>\n\n"
        "Мы свяжемся с вами после подтверждения заявки.",
        reply_markup=main_keyboard(),
    )

    # Уведомление админу
    if ADMIN_ID:
        username = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else "без username"
        )

        text = (
            f"🔔 <b>НОВАЯ ЗАЯВКА №{bid}</b>\n\n"
            f"🚗 <b>{CARS[cid]['name']}</b>\n"
            f"⚙️ {CARS[cid]['gear']}\n"
            f"📅 {start_d.strftime('%d.%m.%Y')} — "
            f"{end_d.strftime('%d.%m.%Y')}\n"
            f"⏱ {data['days']} суток\n"
            f"💰 <b>{money(data['total'])}</b>\n\n"
            f"👤 {data['name']}\n"
            f"📞 {data['phone']}\n"
            f"Telegram: {username}\n"
            f"📝 {comment or '—'}\n\n"
            f"⏳ Ожидает подтверждения до "
            f"{expires.astimezone(TZ).strftime('%d.%m.%Y %H:%M')}"
        )

        await message.bot.send_message(
            ADMIN_ID,
            text,
            reply_markup=admin_booking_buttons(bid),
        )


# ============================================================
# МОИ ЗАЯВКИ
# ============================================================

async def mybookings(callback: CallbackQuery):
    cleanup_pending()

    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (callback.from_user.id,),
    ).fetchall()

    con.close()

    if not rows:
        await callback.message.answer(
            "📋 У вас пока нет заявок."
        )

        await callback.answer()
        return

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"№{row['id']} — "
                    f"{CARS[row['car_id']]['name']}"
                ),
                callback_data=f"mybooking:{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="back:main",
        )
    ])

    await callback.message.answer(
        "📋 <b>Ваши заявки</b>\n\n"
        "Выберите заявку:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


async def mybooking_detail(callback: CallbackQuery):
    bid = int(callback.data.split(":", 1)[1])

    cleanup_pending()

    con = db()

    row = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE id = ?
          AND user_id = ?
        """,
        (
            bid,
            callback.from_user.id,
        ),
    ).fetchone()

    con.close()

    if not row:
        await callback.answer(
            "Заявка не найдена.",
            show_alert=True,
        )
        return

    text = format_booking(row)

    buttons = []

    if row["status"] == "confirmed":
        buttons.append([
            InlineKeyboardButton(
                text="📄 Получить договор",
                callback_data=f"contract_user:{bid}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="◀️ Мои заявки",
            callback_data="mybookings",
        )
    ])

    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


# ============================================================
# УСЛОВИЯ
# ============================================================

async def terms(callback: CallbackQuery):
    await callback.message.answer(
        "ℹ️ <b>Условия аренды</b>\n\n"
        "• Бронирование оформляется после "
        "подтверждения заявки.\n"
        "• Цена рассчитывается автоматически "
        "по количеству суток.\n"
        "• Заявка удерживает выбранные даты "
        "ограниченное время до подтверждения.\n"
        "• Детали получения и возврата "
        "согласовываются с менеджером."
    )

    await callback.answer()


# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа.")
        return

    cleanup_pending()

    await message.answer(
        "👨‍💼 <b>Админ-панель Balticar</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_panel_keyboard(),
    )


async def admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "👨‍💼 <b>Админ-панель Balticar</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_panel_keyboard(),
    )

    await callback.answer()


# ============================================================
# НОВЫЕ ЗАЯВКИ
# ============================================================

async def admin_new_bookings(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    cleanup_pending()

    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE status = 'pending'
        ORDER BY id ASC
        """
    ).fetchall()

    con.close()

    if not rows:
        await callback.message.edit_text(
            "🔔 <b>Новые заявки</b>\n\n"
            "Новых заявок нет.",
            reply_markup=admin_back_keyboard(),
        )

        await callback.answer()
        return

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"🟡 №{row['id']} — "
                    f"{CARS[row['car_id']]['name']}"
                ),
                callback_data=f"adminbooking:{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="◀️ В админ-панель",
            callback_data="admin:back",
        )
    ])

    await callback.message.edit_text(
        f"🔔 <b>Новые заявки</b>\n\n"
        f"Ожидают подтверждения: <b>{len(rows)}</b>\n\n"
        "Выберите заявку:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


async def admin_booking_detail(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    bid = int(callback.data.split(":", 1)[1])

    cleanup_pending()

    con = db()

    row = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE id = ?
        """,
        (bid,),
    ).fetchone()

    con.close()

    if not row:
        await callback.answer(
            "Заявка не найдена.",
            show_alert=True,
        )
        return

    keyboard = []

    if row["status"] == "pending":
        keyboard.append([
            InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=f"confirm:{bid}",
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject:{bid}",
            ),
        ])

    if row["status"] == "confirmed":
        keyboard.append([
            InlineKeyboardButton(
                text="📄 Договор",
                callback_data=f"contract:{bid}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="◀️ К заявкам",
            callback_data="admin:new",
        )
    ])

    await callback.message.edit_text(
        format_booking(row),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        ),
    )

    await callback.answer()


# ============================================================
# ВСЕ БРОНИРОВАНИЯ
# ============================================================

def admin_booking_filter_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟡 Ожидают",
                    callback_data="adminfilter:pending",
                ),
                InlineKeyboardButton(
                    text="✅ Подтверждены",
                    callback_data="adminfilter:confirmed",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонены",
                    callback_data="adminfilter:rejected",
                ),
                InlineKeyboardButton(
                    text="⌛ Истекли",
                    callback_data="adminfilter:expired",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Все",
                    callback_data="adminfilter:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ В админ-панель",
                    callback_data="admin:back",
                )
            ],
        ]
    )


async def admin_bookings(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "📋 <b>Все бронирования</b>\n\n"
        "Выберите фильтр:",
        reply_markup=admin_booking_filter_keyboard(),
    )

    await callback.answer()


async def admin_filter(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    status = callback.data.split(":", 1)[1]

    cleanup_pending()

    con = db()

    if status == "all":
        rows = con.execute(
            """
            SELECT *
            FROM bookings
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

        title = "📋 Все бронирования"

    else:
        rows = con.execute(
            """
            SELECT *
            FROM bookings
            WHERE status = ?
            ORDER BY id DESC
            LIMIT 100
            """,
            (status,),
        ).fetchall()

        title = (
            f"{booking_status_text(status)}"
        )

    con.close()

    if not rows:
        await callback.message.edit_text(
            f"<b>{title}</b>\n\n"
            "Заявок нет.",
            reply_markup=admin_booking_filter_keyboard(),
        )

        await callback.answer()
        return

    buttons = []

    for row in rows:
        start_d = date.fromisoformat(
            row["start_date"]
        )

        status_icon = {
            "pending": "🟡",
            "confirmed": "✅",
            "rejected": "❌",
            "expired": "⌛",
        }.get(row["status"], "📋")

        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"{status_icon} №{row['id']} "
                    f"{CARS[row['car_id']]['name']} "
                    f"{start_d.strftime('%d.%m')}"
                ),
                callback_data=f"adminbooking:{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="◀️ Фильтры",
            callback_data="admin:bookings",
        )
    ])

    await callback.message.edit_text(
        f"<b>{title}</b>\n\n"
        f"Количество: <b>{len(rows)}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


# ============================================================
# АДМИН: АВТОМОБИЛИ
# ============================================================

def admin_cars_keyboard():
    rows = []

    for cid, car in CARS.items():
        enabled = is_car_enabled(cid)

        icon = "🟢" if enabled else "🔴"

        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {car['name']}",
                callback_data=f"admincarinfo:{cid}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ В админ-панель",
            callback_data="admin:back",
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


async def admin_cars(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "🚗 <b>Автомобили</b>\n\n"
        "🟢 автомобиль доступен клиентам\n"
        "🔴 автомобиль временно отключён\n\n"
        "Выберите автомобиль:",
        reply_markup=admin_cars_keyboard(),
    )

    await callback.answer()


async def admin_car_info(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    cid = callback.data.split(":", 1)[1]

    if cid not in CARS:
        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

    enabled = is_car_enabled(cid)

    toggle_text = (
        "🔴 Отключить автомобиль"
        if enabled
        else "🟢 Включить автомобиль"
    )

    await callback.message.edit_text(
        f"🚗 <b>{CARS[cid]['name']}</b>\n\n"
        f"⚙️ {CARS[cid]['gear']}\n\n"
        f"💰 1–3 суток: "
        f"{money(CARS[cid]['rates'][0])}/сутки\n"
        f"💰 4–6 суток: "
        f"{money(CARS[cid]['rates'][1])}/сутки\n"
        f"💰 7+ суток: "
        f"{money(CARS[cid]['rates'][2])}/сутки\n\n"
        f"Статус: "
        f"{'🟢 доступен' if enabled else '🔴 отключён'}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=toggle_text,
                        callback_data=f"cartoggle:{cid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📅 Календарь",
                        callback_data=f"admincar:{cid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ К автомобилям",
                        callback_data="admin:cars",
                    )
                ],
            ]
        ),
    )

    await callback.answer()


async def admin_car_toggle(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    cid = callback.data.split(":", 1)[1]

    current = is_car_enabled(cid)

    set_car_enabled(
        cid,
        not current,
    )

    await callback.answer(
        "Автомобиль включён."
        if not current
        else "Автомобиль отключён."
    )

    await admin_car_info(
        callback
    )


# ============================================================
# АДМИН: КАЛЕНДАРЬ
# ============================================================

async def admin_calendar(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    rows = []

    for cid, car in CARS.items():
        status = (
            "🟢"
            if is_car_enabled(cid)
            else "🔴"
        )

        rows.append([
            InlineKeyboardButton(
                text=f"{status} {car['name']}",
                callback_data=f"admincar:{cid}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="admin:back",
        )
    ])

    await callback.message.edit_text(
        "📅 <b>Календарь занятости</b>\n\n"
        "Выберите автомобиль:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        ),
    )

    await callback.answer()


async def admin_car_calendar(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    cid = callback.data.split(":", 1)[1]

    today = datetime.now(TZ).date()

    await callback.message.edit_text(
        f"📅 <b>Календарь занятости</b>\n\n"
        f"🚗 <b>{CARS[cid]['name']}</b>\n\n"
        "🟢 свободно\n"
        "🟡 ожидает подтверждения\n"
        "🔴 подтверждено\n"
        "⚪ прошедшая дата",
        reply_markup=admin_busy_calendar_keyboard(
            cid,
            today.year,
            today.month,
        ),
    )

    await callback.answer()


async def admin_month(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    _, car_id, iso = callback.data.split(":")

    d = date.fromisoformat(iso)

    await callback.message.edit_reply_markup(
        reply_markup=admin_busy_calendar_keyboard(
            car_id,
            d.year,
            d.month,
        )
    )

    await callback.answer()


async def admin_day(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    _, car_id, iso = callback.data.split(":")

    selected = date.fromisoformat(iso)

    cleanup_pending()

    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE car_id = ?
          AND status IN ('pending', 'confirmed')
          AND date(start_date) <= date(?)
          AND date(end_date) > date(?)
        ORDER BY
            CASE status
                WHEN 'confirmed' THEN 1
                WHEN 'pending' THEN 2
                ELSE 3
            END,
            id DESC
        """,
        (
            car_id,
            selected.isoformat(),
            selected.isoformat(),
        ),
    ).fetchall()

    con.close()

    if not rows:
        await callback.answer(
            "В этот день бронирований нет.",
            show_alert=True,
        )
        return

    for row in rows:
        keyboard = []

        if row["status"] == "pending":
            keyboard.append([
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"confirm:{row['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{row['id']}",
                ),
            ])

        await callback.message.answer(
            format_booking(row),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=keyboard
            ) if keyboard else None,
        )

    await callback.answer()


# ============================================================
# ПОДТВЕРЖДЕНИЕ / ОТКЛОНЕНИЕ
# ============================================================

async def admin_action(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    action, bid_s = callback.data.split(":")

    bid = int(bid_s)

    cleanup_pending()

    con = db()

    row = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE id = ?
        """,
        (bid,),
    ).fetchone()

    if not row:
        con.close()

        await callback.answer(
            "Заявка не найдена.",
            show_alert=True,
        )
        return

    if row["status"] != "pending":
        con.close()

        await callback.answer(
            f"Заявка уже имеет статус: "
            f"{booking_status_text(row['status'])}",
            show_alert=True,
        )
        return

    if action == "confirm":
        start_d = date.fromisoformat(
            row["start_date"]
        )

        end_d = date.fromisoformat(
            row["end_date"]
        )

        conflict = con.execute(
            """
            SELECT id
            FROM bookings
            WHERE car_id = ?
              AND id <> ?
              AND status IN ('pending', 'confirmed')
              AND date(start_date) < date(?)
              AND date(end_date) > date(?)
            LIMIT 1
            """,
            (
                row["car_id"],
                bid,
                end_d.isoformat(),
                start_d.isoformat(),
            ),
        ).fetchone()

        if conflict:
            con.execute(
                """
                UPDATE bookings
                SET status = 'rejected'
                WHERE id = ?
                """,
                (bid,),
            )

            con.commit()
            con.close()

            try:
                await callback.bot.send_message(
                    row["user_id"],
                    f"❌ <b>Заявка №{bid} отклонена</b>\n\n"
                    "К сожалению, выбранные даты "
                    "уже заняты.",
                )
            except Exception:
                pass

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

            await callback.answer(
                "Даты уже заняты.",
                show_alert=True,
            )

            return

        if not is_car_enabled(row["car_id"]):
            con.close()

            await callback.answer(
                "Автомобиль сейчас отключён.",
                show_alert=True,
            )

            return

        con.execute(
            """
            UPDATE bookings
            SET status = 'confirmed',
                expires_at = NULL
            WHERE id = ?
            """,
            (bid,),
        )

        con.commit()
        con.close()

        start_text = datetime.fromisoformat(
            row["start_date"]
        ).strftime("%d.%m.%Y")

        end_text = datetime.fromisoformat(
            row["end_date"]
        ).strftime("%d.%m.%Y")

        try:
            await callback.bot.send_message(
                row["user_id"],
                f"✅ <b>Заявка №{bid} подтверждена!</b>\n\n"
                f"🚗 {CARS[row['car_id']]['name']}\n"
                f"📅 {start_text} — {end_text}\n"
                f"💰 {money(row['total'])}\n\n"
                "Спасибо за бронирование!",
            )
        except Exception:
            pass

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

        await callback.answer(
            "Заявка подтверждена."
        )

    else:
        con.execute(
            """
            UPDATE bookings
            SET status = 'rejected'
            WHERE id = ?
            """,
            (bid,),
        )

        con.commit()
        con.close()

        try:
            await callback.bot.send_message(
                row["user_id"],
                f"❌ <b>Заявка №{bid} отклонена.</b>\n\n"
                "Если хотите, вы можете выбрать "
                "другой автомобиль или даты.",
            )
        except Exception:
            pass

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

        await callback.answer(
            "Заявка отклонена."
        )


# ============================================================
# ДОГОВОР PDF
# ============================================================

def create_contract_pdf(row):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    contracts_dir = Path("contracts")
    contracts_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    font_regular = None
    font_bold = None

    possible_fonts = [
        (
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/dejavu/"
            "DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/"
            "DejaVuSans-Bold.ttf",
        ),
    ]

    for regular, bold in possible_fonts:
        if (
            Path(regular).exists()
            and Path(bold).exists()
        ):
            font_regular = regular
            font_bold = bold
            break

    if font_regular and font_bold:
        try:
            pdfmetrics.registerFont(
                TTFont(
                    "BalticarRegular",
                    font_regular,
                )
            )

            pdfmetrics.registerFont(
                TTFont(
                    "BalticarBold",
                    font_bold,
                )
            )

            regular_name = "BalticarRegular"
            bold_name = "BalticarBold"

        except Exception:
            regular_name = "Helvetica"
            bold_name = "Helvetica-Bold"

    else:
        regular_name = "Helvetica"
        bold_name = "Helvetica-Bold"

    path = contracts_dir / (
        f"contract_{row['id']}.pdf"
    )

    c = canvas.Canvas(
        str(path),
        pagesize=A4,
    )

    width, height = A4

    y = height - 50

    c.setFont(
        bold_name,
        18,
    )

    c.drawString(
        50,
        y,
        "BALTICAR",
    )

    y -= 40

    c.setFont(
        bold_name,
        14,
    )

    c.drawString(
        50,
        y,
        "Договор аренды автомобиля",
    )

    y -= 40

    c.setFont(
        regular_name,
        10,
    )

    start_d = date.fromisoformat(
        row["start_date"]
    )

    end_d = date.fromisoformat(
        row["end_date"]
    )

    lines = [
        f"Номер бронирования: №{row['id']}",
        "",
        f"Арендатор: {row['name']}",
        f"Телефон: {row['phone']}",
        "",
        f"Автомобиль: {CARS[row['car_id']]['name']}",
        f"Коробка передач: {CARS[row['car_id']]['gear']}",
        "",
        (
            "Срок аренды: "
            f"{start_d.strftime('%d.%m.%Y')} — "
            f"{end_d.strftime('%d.%m.%Y')}"
        ),
        (
            "Количество суток: "
            f"{(end_d - start_d).days}"
        ),
        f"Стоимость аренды: {money(row['total'])}",
        "",
        "Условия:",
        "1. Автомобиль передаётся арендатору "
        "в исправном состоянии.",
        "2. Арендатор обязан бережно относиться "
        "к автомобилю.",
        "3. Дата и время получения и возврата "
        "согласовываются с менеджером.",
        "4. Дополнительные условия согласуются "
        "сторонами отдельно.",
        "",
        "Арендодатель: Balticar",
        "",
        "Подпись арендатора: ____________________",
        "",
        "Подпись арендодателя: __________________",
    ]

    for line in lines:
        if y < 50:
            c.showPage()
            y = height - 50
            c.setFont(
                regular_name,
                10,
            )

        c.drawString(
            50,
            y,
            line[:110],
        )

        y -= 18

    c.save()

    return path


async def send_contract(
    callback: CallbackQuery,
    row,
):
    path = create_contract_pdf(row)

    if path is None:
        await callback.answer(
            "Для PDF договора не установлен reportlab.",
            show_alert=True,
        )
        return

    await callback.message.answer_document(
        FSInputFile(path),
        caption=(
            f"📄 Договор по бронированию "
            f"№{row['id']}"
        ),
    )

    await callback.answer()


async def contract_admin(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    bid = int(callback.data.split(":", 1)[1])

    con = db()

    row = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE id = ?
          AND status = 'confirmed'
        """,
        (bid,),
    ).fetchone()

    con.close()

    if not row:
        await callback.answer(
            "Подтверждённая заявка не найдена.",
            show_alert=True,
        )
        return

    await send_contract(
        callback,
        row,
    )


async def contract_user(callback: CallbackQuery):
    bid = int(
        callback.data.split(":", 1)[1]
    )

    con = db()

    row = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE id = ?
          AND user_id = ?
          AND status = 'confirmed'
        """,
        (
            bid,
            callback.from_user.id,
        ),
    ).fetchone()

    con.close()

    if not row:
        await callback.answer(
            "Договор недоступен.",
            show_alert=True,
        )
        return

    await send_contract(
        callback,
        row,
    )


# ============================================================
# ОПЛАТА — ЗАГОТОВКА
# ============================================================

async def payment_placeholder(callback: CallbackQuery):
    await callback.answer(
        "Оплата пока не подключена.",
        show_alert=True,
    )


# ============================================================
# ПУБЛИКАЦИЯ В КАНАЛ
# ============================================================

async def publish(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    me = await message.bot.me()

    text = (
        "🚗 <b>Balticar — аренда автомобилей</b>\n\n"
        "Выберите автомобиль, "
        "посмотрите стоимость и "
        "забронируйте его прямо в Telegram."
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 Забронировать автомобиль",
                    url=f"https://t.me/{me.username}",
                )
            ]
        ]
    )

    await message.bot.send_message(
        CHANNEL_USERNAME,
        text,
        reply_markup=kb,
    )

    await message.answer(
        "Готово: пост с кнопкой опубликован "
        "в канале."
    )


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан в .env"
        )

    init_db()

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher()

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

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

    dp.message.register(
        admin_panel,
        Command("admin"),
    )

    # --------------------------------------------------------
    # CLIENT
    # --------------------------------------------------------

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

    dp.callback_query.register(
        month,
        F.data.startswith("month:"),
    )

    dp.callback_query.register(
        start_day,
        F.data.startswith("day:"),
    )

    dp.callback_query.register(
        endmonth,
        F.data.startswith("endmonth:"),
    )

    dp.callback_query.register(
        end_day,
        F.data.startswith("end:"),
    )

    dp.callback_query.register(
        mybookings,
        F.data == "mybookings",
    )

    dp.callback_query.register(
        mybooking_detail,
        F.data.startswith("mybooking:"),
    )

    dp.callback_query.register(
        terms,
        F.data == "terms",
    )

    dp.callback_query.register(
        contract_user,
        F.data.startswith("contract_user:"),
    )

    dp.callback_query.register(
        payment_placeholder,
        F.data.startswith("payment:"),
    )

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    dp.callback_query.register(
        admin_back,
        F.data == "admin:back",
    )

    dp.callback_query.register(
        admin_new_bookings,
        F.data == "admin:new",
    )

    dp.callback_query.register(
        admin_bookings,
        F.data == "admin:bookings",
    )

    dp.callback_query.register(
        admin_filter,
        F.data.startswith("adminfilter:"),
    )

    dp.callback_query.register(
        admin_cars,
        F.data == "admin:cars",
    )

    dp.callback_query.register(
        admin_car_info,
        F.data.startswith("admincarinfo:"),
    )

    dp.callback_query.register(
        admin_car_toggle,
        F.data.startswith("cartoggle:"),
    )

    dp.callback_query.register(
        admin_calendar,
        F.data == "admin:calendar",
    )

    dp.callback_query.register(
        admin_car_calendar,
        F.data.startswith("admincar:"),
    )

    dp.callback_query.register(
        admin_month,
        F.data.startswith("adminmonth:"),
    )

    dp.callback_query.register(
        admin_day,
        F.data.startswith("adminday:"),
    )

    dp.callback_query.register(
        admin_booking_detail,
        F.data.startswith("adminbooking:"),
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("confirm:"),
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("reject:"),
    )

    dp.callback_query.register(
        contract_admin,
        F.data.startswith("contract:"),
    )

    # --------------------------------------------------------
    # OTHER
    # --------------------------------------------------------

    dp.callback_query.register(
        lambda c: c.message.answer(
            "🚗 <b>Balticar</b>\n\n"
            "Выберите действие:",
            reply_markup=main_keyboard(),
        ),
        F.data == "back:main",
    )

    dp.callback_query.register(
        lambda c: c.answer(),
        F.data == "noop",
    )

    # --------------------------------------------------------
    # FSM
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # WEBHOOK / RENDER
    # --------------------------------------------------------

    external_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        "",
    ).rstrip("/")

    if not external_url:
        print(
            "RENDER_EXTERNAL_URL is not set; "
            "starting polling mode."
        )

        await dp.start_polling(bot)
        return

    webhook_url = (
        f"{external_url}{WEBHOOK_PATH}"
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

    async def health(_: web.Request):
        return web.Response(
            text="Balticar bot is running"
        )

    async def telegram_webhook(
        request: web.Request,
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
            context={"bot": bot},
        )

        await dp.feed_update(
            bot,
            update,
        )

        return web.Response(
            text="OK"
        )

    app = web.Application()

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

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
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


if __name__ == "__main__":
    asyncio.run(main())
