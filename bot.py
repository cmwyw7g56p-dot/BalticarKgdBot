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


class Booking(StatesGroup):
    start = State()
    end = State()
    name = State()
    phone = State()
    comment = State()


# =========================================================
# DATABASE
# =========================================================

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


def available(car_id, start_d, end_d):
    cleanup_pending()

    con = db()

    row = con.execute(
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
            car_id,
            end_d.isoformat(),
            start_d.isoformat(),
        ),
    ).fetchone()

    con.close()

    return row is None


# =========================================================
# HELPERS
# =========================================================

def rate_for_days(car_id, days):
    rates = CARS[car_id]["rates"]

    if days <= 3:
        return rates[0]

    if days <= 6:
        return rates[1]

    return rates[2]


def money(n):
    return f"{n:,}".replace(",", " ") + " ₽"


def status_text(status):
    return {
        "pending": "🟡 Ожидает подтверждения",
        "confirmed": "🔴 Подтверждено",
        "rejected": "❌ Отклонено",
        "expired": "⌛ Истекло",
    }.get(status, status)


# =========================================================
# USER KEYBOARDS
# =========================================================

def car_keyboard():
    rows = []

    for cid, car in CARS.items():
        rows.append([
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=f"car:{cid}",
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


# =========================================================
# USER CALENDAR
# =========================================================

def calendar_keyboard(car_id, year, month):
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    prev_first = first - timedelta(days=1)
    prev_first = date(
        prev_first.year,
        prev_first.month,
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
        cur = date(year, month, d)

        if cur < today:
            text = "⚪"
            callback = "noop"

        elif not available(
            car_id,
            cur,
            cur + timedelta(days=1),
        ):
            text = f"🔴{d}"
            callback = "noop"

        else:
            text = f"🟢{d}"
            callback = f"day:{car_id}:{cur.isoformat()}"

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
                f"month:{car_id}:{prev_first.isoformat()}"
            ),
        ),
        InlineKeyboardButton(
            text=f"{months[month - 1]} {year}",
            callback_data="noop",
        ),
        InlineKeyboardButton(
            text="›",
            callback_data=(
                f"month:{car_id}:{next_first.isoformat()}"
            ),
        ),
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def end_calendar_keyboard(car_id, start_d, year, month):
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    prev_day = first - timedelta(days=1)

    prev_first = date(
        prev_day.year,
        prev_day.month,
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
        cur = date(year, month, d)

        if cur <= start_d:
            text = "⚪"
            callback = "noop"

        elif not available(car_id, start_d, cur):
            text = f"🔴{d}"
            callback = "noop"

        else:
            text = f"🟢{d}"
            callback = f"end:{car_id}:{cur.isoformat()}"

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
                f"{prev_first.isoformat()}"
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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# =========================================================
# ADMIN KEYBOARDS
# =========================================================

def admin_buttons(bid):
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
                    text="◀️ Назад",
                    callback_data="admin:back",
                )
            ]
        ]
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа.")
        return

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


# =========================================================
# ADMIN — NEW BOOKINGS
# =========================================================

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
        ORDER BY id DESC
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

    text_parts = [
        "🔔 <b>Новые заявки</b>",
        "",
    ]

    for row in rows:
        start_d = date.fromisoformat(row["start_date"])
        end_d = date.fromisoformat(row["end_date"])

        username = (
            f"@{row['username']}"
            if row["username"]
            else "без username"
        )

        text_parts.append(
            f"📩 <b>Заявка №{row['id']}</b>\n"
            f"🚗 {CARS[row['car_id']]['name']}\n"
            f"📅 {start_d.strftime('%d.%m.%Y')} — "
            f"{end_d.strftime('%d.%m.%Y')}\n"
            f"⏱ {(end_d - start_d).days} суток\n"
            f"💰 <b>{money(row['total'])}</b>\n"
            f"👤 {row['name']}\n"
            f"📞 {row['phone']}\n"
            f"Telegram: {username}\n"
            f"📝 {row['comment'] or '—'}"
        )

        if row["expires_at"]:
            expires = datetime.fromisoformat(
                row["expires_at"]
            ).astimezone(TZ)

            text_parts.append(
                f"⏳ Удержание до "
                f"{expires.strftime('%d.%m.%Y %H:%M')}"
            )

        text_parts.append("")

    await callback.message.edit_text(
        "\n".join(text_parts),
        reply_markup=admin_back_keyboard(),
    )

    # Отдельные сообщения с кнопками действий.
    for row in rows:
        start_d = date.fromisoformat(row["start_date"])
        end_d = date.fromisoformat(row["end_date"])

        await callback.message.answer(
            f"📩 <b>Заявка №{row['id']}</b>\n\n"
            f"🚗 {CARS[row['car_id']]['name']}\n"
            f"📅 {start_d.strftime('%d.%m.%Y')} — "
            f"{end_d.strftime('%d.%m.%Y')}\n"
            f"⏱ {(end_d - start_d).days} суток\n"
            f"💰 <b>{money(row['total'])}</b>\n"
            f"👤 {row['name']}\n"
            f"📞 {row['phone']}\n"
            f"📝 {row['comment'] or '—'}",
            reply_markup=admin_buttons(row["id"]),
        )

    await callback.answer()


# =========================================================
# ADMIN — ALL BOOKINGS
# =========================================================

async def admin_all_bookings(callback: CallbackQuery):
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
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()

    con.close()

    if not rows:
        await callback.message.edit_text(
            "📋 <b>Все бронирования</b>\n\n"
            "Бронирований пока нет.",
            reply_markup=admin_back_keyboard(),
        )

        await callback.answer()
        return

    text_parts = [
        "📋 <b>Все бронирования</b>",
        "",
    ]

    for row in rows:
        start_d = date.fromisoformat(row["start_date"])
        end_d = date.fromisoformat(row["end_date"])

        text_parts.append(
            f"<b>№{row['id']}</b> — "
            f"{status_text(row['status'])}\n"
            f"🚗 {CARS[row['car_id']]['name']}\n"
            f"📅 {start_d.strftime('%d.%m.%Y')} — "
            f"{end_d.strftime('%d.%m.%Y')}\n"
            f"👤 {row['name']}\n"
            f"📞 {row['phone']}\n"
            f"💰 {money(row['total'])}\n"
        )

    await callback.message.edit_text(
        "\n".join(text_parts),
        reply_markup=admin_back_keyboard(),
    )

    await callback.answer()


# =========================================================
# ADMIN — CARS
# =========================================================

async def admin_cars(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    rows = []

    for cid, car in CARS.items():
        rows.append([
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=f"admincardetail:{cid}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="admin:back",
        )
    ])

    await callback.message.edit_text(
        "🚗 <b>Автомобили</b>\n\n"
        "Выберите автомобиль:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        ),
    )

    await callback.answer()


async def admin_car_detail(callback: CallbackQuery):
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

    car = CARS[cid]

    await callback.message.edit_text(
        f"🚗 <b>{car['name']}</b>\n\n"
        f"⚙️ Коробка: <b>{car['gear']}</b>\n\n"
        f"💰 1–3 суток: "
        f"<b>{money(car['rates'][0])}/сутки</b>\n"
        f"💰 4–6 суток: "
        f"<b>{money(car['rates'][1])}/сутки</b>\n"
        f"💰 7+ суток: "
        f"<b>{money(car['rates'][2])}/сутки</b>\n\n"
        f"📷 Фотографий: {len(car['photos'])}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ К автомобилям",
                        callback_data="admin:cars",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📅 Календарь",
                        callback_data=f"admincar:{cid}",
                    )
                ],
            ]
        ),
    )

    await callback.answer()


# =========================================================
# ADMIN — OCCUPANCY CALENDAR
# =========================================================

async def admin_calendar(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    rows = []

    for cid, car in CARS.items():
        rows.append([
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
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

    if cid not in CARS:
        await callback.answer(
            "Автомобиль не найден.",
            show_alert=True,
        )
        return

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
        current = date(
            year,
            month,
            day_number,
        )

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
            callback_data = (
                f"adminday:{car_id}:"
                f"{current.isoformat()}"
            )

        elif status == "pending":
            text = f"🟡{day_number}"
            callback_data = (
                f"adminday:{car_id}:"
                f"{current.isoformat()}"
            )

        elif status == "past":
            text = "⚪"
            callback_data = "noop"

        else:
            text = f"🟢{day_number}"
            callback_data = (
                f"adminday:{car_id}:"
                f"{current.isoformat()}"
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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


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

    row = con.execute(
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
            END
        LIMIT 1
        """,
        (
            car_id,
            selected.isoformat(),
            selected.isoformat(),
        ),
    ).fetchone()

    con.close()

    if not row:
        await callback.answer(
            "В этот день бронирований нет.",
            show_alert=True,
        )
        return

    status = (
        "🔴 Подтверждено"
        if row["status"] == "confirmed"
        else "🟡 Ожидает подтверждения"
    )

    await callback.message.answer(
        f"📋 <b>Бронирование №{row['id']}</b>\n\n"
        f"🚗 {CARS[car_id]['name']}\n"
        f"{status}\n"
        f"📅 "
        f"{date.fromisoformat(row['start_date']).strftime('%d.%m.%Y')}"
        f" — "
        f"{date.fromisoformat(row['end_date']).strftime('%d.%m.%Y')}\n"
        f"👤 {row['name']}\n"
        f"📞 {row['phone']}\n"
        f"💰 {money(row['total'])}\n"
        f"📝 {row['comment'] or '—'}",
        reply_markup=(
            admin_buttons(row["id"])
            if row["status"] == "pending"
            else None
        ),
    )

    await callback.answer()


# =========================================================
# CAR INFO
# =========================================================

def car_text(cid):
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


async def send_car(bot, chat_id, cid):
    car = CARS[cid]

    for photo in car["photos"]:
        if Path(photo).exists():
            await bot.send_photo(
                chat_id,
                FSInputFile(photo),
            )

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


# =========================================================
# USER HANDLERS
# =========================================================

async def start_handler(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "🚗 <b>Balticar</b>\n\n"
        "Аренда автомобилей в Калининграде.\n"
        "Выберите действие:",
        reply_markup=main_keyboard(),
    )


async def id_handler(message: Message):
    await message.answer(
        f"Ваш Telegram ID: "
        f"<code>{message.from_user.id}</code>"
    )


async def catalog(callback: CallbackQuery):
    await callback.message.answer(
        "🚗 <b>Выберите автомобиль:</b>",
        reply_markup=car_keyboard(),
    )

    await callback.answer()


async def car_selected(callback: CallbackQuery):
    cid = callback.data.split(":")[1]

    await send_car(
        callback.bot,
        callback.message.chat.id,
        cid,
    )

    await callback.answer()


async def pick_dates(callback: CallbackQuery):
    cid = callback.data.split(":")[1]

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

    await state.update_data(
        car_id=cid,
        start_date=iso,
    )

    await state.set_state(Booking.end)

    await callback.message.answer(
        f"📅 Получение: "
        f"<b>{start_d.strftime('%d.%m.%Y')}</b>\n"
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

    start_d = date.fromisoformat(
        data["start_date"]
    )

    end_d = date.fromisoformat(end_iso)

    if not available(cid, start_d, end_d):
        await callback.answer(
            "Эти даты уже заняты.",
            show_alert=True,
        )
        return

    days = (end_d - start_d).days

    total = (
        days
        * rate_for_days(cid, days)
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
        "Введите ваше имя:"
    )

    await callback.answer()


async def name_handler(
    message: Message,
    state: FSMContext,
):
    if len(message.text.strip()) < 2:
        await message.answer(
            "Пожалуйста, введите имя."
        )
        return

    await state.update_data(
        name=message.text.strip()
    )

    await state.set_state(Booking.phone)

    await message.answer(
        "📞 Введите номер телефона:"
    )


async def phone_handler(
    message: Message,
    state: FSMContext,
):
    phone = message.text.strip()

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
        "📝 Комментарий к заказу "
        "(или отправьте «-»):"
    )


async def comment_handler(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    comment = (
        ""
        if message.text.strip() == "-"
        else message.text.strip()
    )

    cid = data["car_id"]

    start_d = date.fromisoformat(
        data["start_date"]
    )

    end_d = date.fromisoformat(
        data["end_date"]
    )

    if not available(cid, start_d, end_d):
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
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            'pending', ?, ?
        )
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
        "Мы свяжемся с вами после "
        "подтверждения заявки."
    )

    if ADMIN_ID:
        username = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else "без username"
        )

        text = (
            f"🔔 <b>Новая заявка №{bid}</b>\n\n"
            f"🚗 {CARS[cid]['name']} "
            f"({CARS[cid]['gear']})\n"
            f"📅 {start_d.strftime('%d.%m.%Y')} — "
            f"{end_d.strftime('%d.%m.%Y')}\n"
            f"⏱ {data['days']} суток\n"
            f"💰 <b>{money(data['total'])}</b>\n"
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
            reply_markup=admin_buttons(bid),
        )


async def mybookings(callback: CallbackQuery):
    cleanup_pending()

    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM bookings
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (callback.from_user.id,),
    ).fetchall()

    con.close()

    if not rows:
        await callback.message.answer(
            "У вас пока нет заявок."
        )

    else:
        out = [
            "📋 <b>Ваши заявки:</b>"
        ]

        for row in rows:
            out.append(
                f"\n№{row['id']} — "
                f"{CARS[row['car_id']]['name']}\n"
                f"📅 "
                f"{date.fromisoformat(row['start_date']).strftime('%d.%m.%Y')}"
                f" — "
                f"{date.fromisoformat(row['end_date']).strftime('%d.%m.%Y')}\n"
                f"💰 {money(row['total'])} — "
                f"{status_text(row['status'])}"
            )

        await callback.message.answer(
            "\n".join(out)
        )

    await callback.answer()


async def terms(callback: CallbackQuery):
    await callback.message.answer(
        "ℹ️ <b>Условия</b>\n\n"
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


# =========================================================
# ADMIN — CONFIRM / REJECT
# =========================================================

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
        current_status = status_text(
            row["status"]
        )

        con.close()

        await callback.answer(
            f"Заявка уже обработана: {current_status}",
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

        others = con.execute(
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
                row["end_date"],
                row["start_date"],
            ),
        ).fetchone()

        if others:
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

            await callback.message.edit_reply_markup(
                reply_markup=None
            )

            await callback.bot.send_message(
                row["user_id"],
                f"❌ Заявка №{bid} отклонена:\n\n"
                "выбранные даты уже заняты.",
            )

            await callback.answer(
                "Даты уже заняты.",
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

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

        await callback.bot.send_message(
            row["user_id"],
            f"✅ <b>Заявка №{bid} подтверждена!</b>\n\n"
            f"🚗 {CARS[row['car_id']]['name']}\n"
            f"📅 "
            f"{start_d.strftime('%d.%m.%Y')} — "
            f"{end_d.strftime('%d.%m.%Y')}\n"
            f"💰 {money(row['total'])}",
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

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

        await callback.bot.send_message(
            row["user_id"],
            f"❌ Заявка №{bid} отклонена.",
        )

        await callback.answer(
            "Заявка отклонена."
        )


# =========================================================
# PUBLISH
# =========================================================

async def publish(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    text = (
        "🚗 <b>Balticar — аренда автомобилей</b>\n\n"
        "Выберите автомобиль, посмотрите стоимость "
        "и забронируйте его прямо в Telegram."
    )

    me = await message.bot.me()

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
        "Готово: пост с кнопкой опубликован в канале."
    )


# =========================================================
# MAIN
# =========================================================

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

    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # USER CALLBACKS
    # -----------------------------------------------------

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
        terms,
        F.data == "terms",
    )

    # -----------------------------------------------------
    # ADMIN CALLBACKS
    # -----------------------------------------------------

    dp.callback_query.register(
        admin_back,
        F.data == "admin:back",
    )

    dp.callback_query.register(
        admin_new_bookings,
        F.data == "admin:new",
    )

    dp.callback_query.register(
        admin_calendar,
        F.data == "admin:calendar",
    )

    dp.callback_query.register(
        admin_all_bookings,
        F.data == "admin:bookings",
    )

    dp.callback_query.register(
        admin_cars,
        F.data == "admin:cars",
    )

    dp.callback_query.register(
        admin_car_detail,
        F.data.startswith("admincardetail:"),
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
        admin_action,
        F.data.startswith("confirm:"),
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("reject:"),
    )

    dp.callback_query.register(
        lambda c: c.answer(),
        F.data == "noop",
    )

    # -----------------------------------------------------
    # FSM
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # WEBHOOK / POLLING
    # -----------------------------------------------------

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
