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
    CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
    Message
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
        "photos": ["photos/solaris_2021_1.png", "photos/solaris_2021_2.jpeg"],
    },
    "solaris20": {
        "name": "Hyundai Solaris 2020",
        "gear": "АКПП",
        "rates": (2700, 2600, 2500),
        "photos": ["photos/solaris_2020.jpeg"],
    },
    "solaris17": {
        "name": "Hyundai Solaris 2017",
        "gear": "АКПП",
        "rates": (2400, 2300, 2200),
        "photos": ["photos/solaris_2017_1.webp", "photos/solaris_2017_2.jpeg"],
    },
    "i30": {
        "name": "Hyundai i30 2014",
        "gear": "МКПП",
        "rates": (2300, 2200, 2100),
        "photos": ["photos/i30_2014.png"],
    },
}

class Booking(StatesGroup):
    start = State()
    end = State()
    name = State()
    phone = State()
    comment = State()

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
        "UPDATE bookings SET status='expired' WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < ?",
        (now,)
    )
    con.commit()
    con.close()

def available(car_id, start_d, end_d):
    cleanup_pending()
    con = db()
    row = con.execute("""
      SELECT id FROM bookings
      WHERE car_id = ?
        AND status IN ('pending','confirmed')
        AND date(start_date) < date(?)
        AND date(end_date) > date(?)
      LIMIT 1
    """, (car_id, end_d.isoformat(), start_d.isoformat())).fetchone()
    con.close()
    return row is None

def rate_for_days(car_id, days):
    r = CARS[car_id]["rates"]
    if days <= 3: return r[0]
    if days <= 6: return r[1]
    return r[2]

def money(n):
    return f"{n:,}".replace(",", " ") + " ₽"

def car_keyboard():
    rows = []
    for cid, c in CARS.items():
        rows.append([InlineKeyboardButton(text=f"🚗 {c['name']}", callback_data=f"car:{cid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Забронировать автомобиль", callback_data="catalog")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="mybookings")],
        [InlineKeyboardButton(text="ℹ️ Условия", callback_data="terms")],
    ])

def calendar_keyboard(car_id, year, month):
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    prev_first = first - timedelta(days=1)
    prev_first = date(prev_first.year, prev_first.month, 1)

    days = (next_first - first).days
    today = datetime.now(TZ).date()

    months = [
        "Январь", "Февраль", "Март", "Апрель",
        "Май", "Июнь", "Июль", "Август",
        "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    rows = []

    # Заголовок дней недели
    rows.append([
        InlineKeyboardButton(text=x, callback_data="noop")
        for x in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    ])

    week = [
        InlineKeyboardButton(text=" ", callback_data="noop")
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
            cur + timedelta(days=1)
        ):
            text = f"🔴{d}"
            callback = "noop"

        else:
            text = f"🟢{d}"
            callback = f"day:{car_id}:{cur.isoformat()}"

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

    rows.append([
        InlineKeyboardButton(
            text="‹",
            callback_data=f"month:{car_id}:{prev_first.isoformat()}"
        ),
        InlineKeyboardButton(
            text=f"{months[month - 1]} {year}",
            callback_data="noop"
        ),
        InlineKeyboardButton(
            text="›",
            callback_data=f"month:{car_id}:{next_first.isoformat()}"
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def end_calendar_keyboard(car_id, start_d, year, month):
    first = date(year, month, 1)

    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)

    prev_day = first - timedelta(days=1)
    prev_first = date(prev_day.year, prev_day.month, 1)

    days = (next_first - first).days

    months = [
        "Январь", "Февраль", "Март", "Апрель",
        "Май", "Июнь", "Июль", "Август",
        "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    rows = [[
        InlineKeyboardButton(text=x, callback_data="noop")
        for x in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    ]]

    week = [
        InlineKeyboardButton(text=" ", callback_data="noop")
        for _ in range(first.weekday())
    ]

    for d in range(1, days + 1):
        cur = date(year, month, d)

        # До даты получения вернуть автомобиль нельзя
        if cur <= start_d:
            text = "⚪"
            callback = "noop"

        # Проверяем весь интервал от получения до возврата
        elif not available(car_id, start_d, cur):
            text = f"🔴{d}"
            callback = "noop"

        else:
            text = f"🟢{d}"
            callback = f"end:{car_id}:{cur.isoformat()}"

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

    rows.append([
        InlineKeyboardButton(
            text="‹",
            callback_data=(
                f"endmonth:{car_id}:"
                f"{start_d.isoformat()}:"
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
                f"endmonth:{car_id}:"
                f"{start_d.isoformat()}:"
                f"{next_first.isoformat()}"
            )
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_buttons(bid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{bid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{bid}"),
    ]])
def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
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
    ])


async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа.")
        return

    await message.answer(
        "👨‍💼 <b>Админ-панель Balticar</b>\n\n"
        "Выберите раздел:",
        reply_markup=admin_panel_keyboard()
    )
async def admin_calendar(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    rows = []

    for cid, car in CARS.items():
        rows.append([
            InlineKeyboardButton(
                text=f"🚗 {car['name']}",
                callback_data=f"admincar:{cid}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="admin:back"
        )
    ])

    await callback.message.edit_text(
        "📅 <b>Календарь занятости</b>\n\n"
        "Выберите автомобиль:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

    await callback.answer()

    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="admin:back"
        )
    ])

    await callback.message.edit_text(
        "📅 <b>Календарь занятости</b>\n\n"
        "Выберите автомобиль:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

    await callback.answer()
async def admin_car_calendar(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
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
            today.month
        )
    )

    await callback.answer()
def car_text(cid):
    c = CARS[cid]
    return (
        f"<b>{c['name']}</b>\n"
        f"⚙️ {c['gear']}\n\n"
        f"💰 1–3 суток: <b>{money(c['rates'][0])}/сутки</b>\n"
        f"💰 4–6 суток: <b>{money(c['rates'][1])}/сутки</b>\n"
        f"💰 7+ суток: <b>{money(c['rates'][2])}/сутки</b>"
    )

async def send_car(bot, chat_id, cid):
    c = CARS[cid]
    for p in c["photos"]:
        if Path(p).exists():
            await bot.send_photo(chat_id, FSInputFile(p))
    await bot.send_message(chat_id, car_text(cid), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Выбрать даты", callback_data=f"pick:{cid}")],
        [InlineKeyboardButton(text="◀️ К автомобилям", callback_data="catalog")],
    ]))

async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚗 <b>Balticar</b>\n\nАренда автомобилей в Калининграде.\nВыберите действие:",
        reply_markup=main_keyboard()
    )

async def id_handler(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

async def catalog(callback: CallbackQuery):
    await callback.message.answer("🚗 <b>Выберите автомобиль:</b>", reply_markup=car_keyboard())
    await callback.answer()

async def car_selected(callback: CallbackQuery):
    cid = callback.data.split(":")[1]
    await send_car(callback.bot, callback.message.chat.id, cid)
    await callback.answer()

async def pick_dates(callback: CallbackQuery):
    cid = callback.data.split(":")[1]
    today = datetime.now(TZ).date()
    await callback.message.answer(
        f"📅 <b>{CARS[cid]['name']}</b>\n\n"
"🟢 свободно   🔴 занято   ⚪ недоступно\n\n"
"Выберите дату получения:",
        reply_markup=calendar_keyboard(cid, today.year, today.month)
    )
    await callback.answer()

async def month(callback: CallbackQuery):
    _, cid, iso = callback.data.split(":")
    d = date.fromisoformat(iso)
    await callback.message.edit_reply_markup(reply_markup=calendar_keyboard(cid, d.year, d.month))
    await callback.answer()

async def start_day(callback: CallbackQuery, state: FSMContext):
    _, cid, iso = callback.data.split(":")
    start_d = date.fromisoformat(iso)
    await state.update_data(car_id=cid, start_date=iso)
    await state.set_state(Booking.end)
    await callback.message.answer(
        f"📅 Получение: <b>{start_d.strftime('%d.%m.%Y')}</b>\nТеперь выберите дату возврата:",
        reply_markup=end_calendar_keyboard(cid, start_d, start_d.year, start_d.month)
    )
    await callback.answer()

async def endmonth(callback: CallbackQuery):
    _, cid, start_iso, iso = callback.data.split(":")
    start_d = date.fromisoformat(start_iso)
    d = date.fromisoformat(iso)
    await callback.message.edit_reply_markup(reply_markup=end_calendar_keyboard(cid, start_d, d.year, d.month))
    await callback.answer()

async def end_day(callback: CallbackQuery, state: FSMContext):
    _, cid, end_iso = callback.data.split(":")
    data = await state.get_data()
    start_d = date.fromisoformat(data["start_date"])
    end_d = date.fromisoformat(end_iso)
    if not available(cid, start_d, end_d):
        await callback.answer("Эти даты уже заняты.", show_alert=True)
        return
    days = (end_d - start_d).days
    total = days * rate_for_days(cid, days)
    await state.update_data(end_date=end_iso, days=days, total=total)
    await state.set_state(Booking.name)
    await callback.message.answer(
        f"✅ Даты: <b>{start_d.strftime('%d.%m.%Y')} — {end_d.strftime('%d.%m.%Y')}</b>\n"
        f"⏱ {days} суток\n💰 <b>{money(total)}</b>\n\n"
        "Введите ваше имя:"
    )
    await callback.answer()

async def name_handler(message: Message, state: FSMContext):
    if len(message.text.strip()) < 2:
        await message.answer("Пожалуйста, введите имя.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(Booking.phone)
    await message.answer("📞 Введите номер телефона:")

async def phone_handler(message: Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) < 7:
        await message.answer("Похоже, номер слишком короткий. Введите телефон ещё раз.")
        return
    await state.update_data(phone=phone)
    await state.set_state(Booking.comment)
    await message.answer("📝 Комментарий к заказу (или отправьте «-»):")

async def comment_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    comment = "" if message.text.strip() == "-" else message.text.strip()
    cid = data["car_id"]
    start_d = date.fromisoformat(data["start_date"])
    end_d = date.fromisoformat(data["end_date"])
    if not available(cid, start_d, end_d):
        await message.answer("К сожалению, автомобиль только что забронировали на эти даты. Начните заново: /start")
        await state.clear()
        return

    expires = datetime.now(TZ) + timedelta(minutes=HOLD_MINUTES)
    con = db()
    cur = con.execute("""
      INSERT INTO bookings
      (user_id, username, car_id, start_date, end_date, name, phone, comment, total, status, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (
        message.from_user.id, message.from_user.username or "", cid,
        start_d.isoformat(), end_d.isoformat(), data["name"], data["phone"],
        comment, data["total"], datetime.now(TZ).isoformat(), expires.isoformat()
    ))
    bid = cur.lastrowid
    con.commit(); con.close()
    await state.clear()

    await message.answer(
        f"📩 <b>Заявка №{bid} отправлена!</b>\n\n"
        f"🚗 {CARS[cid]['name']}\n"
        f"📅 {start_d.strftime('%d.%m.%Y')} — {end_d.strftime('%d.%m.%Y')}\n"
        f"⏱ {data['days']} суток\n"
        f"💰 <b>{money(data['total'])}</b>\n\n"
        "Мы свяжемся с вами после подтверждения заявки."
    )
    if ADMIN_ID:
        uname = f"@{message.from_user.username}" if message.from_user.username else "без username"
        text = (
            f"🔔 <b>Новая заявка №{bid}</b>\n\n"
            f"🚗 {CARS[cid]['name']} ({CARS[cid]['gear']})\n"
            f"📅 {start_d.strftime('%d.%m.%Y')} — {end_d.strftime('%d.%m.%Y')}\n"
            f"⏱ {data['days']} суток\n"
            f"💰 <b>{money(data['total'])}</b>\n"
            f"👤 {data['name']}\n"
            f"📞 {data['phone']}\n"
            f"Telegram: {uname}\n"
            f"📝 {comment or '—'}\n\n"
            f"⏳ Ожидает подтверждения до {expires.astimezone(TZ).strftime('%d.%m.%Y %H:%M')}"
        )
        await message.bot.send_message(ADMIN_ID, text, reply_markup=admin_buttons(bid))

async def mybookings(callback: CallbackQuery):
    cleanup_pending()
    con = db()
    rows = con.execute("""
      SELECT * FROM bookings WHERE user_id = ? ORDER BY id DESC LIMIT 10
    """, (callback.from_user.id,)).fetchall()
    con.close()
    if not rows:
        await callback.message.answer("У вас пока нет заявок.")
    else:
        out = ["📋 <b>Ваши заявки:</b>"]
        labels = {"pending":"⏳ ожидает", "confirmed":"✅ подтверждена", "rejected":"❌ отклонена", "expired":"⌛ истекла"}
        for r in rows:
            out.append(
                f"\n№{r['id']} — {CARS[r['car_id']]['name']}\n"
                f"📅 {date.fromisoformat(r['start_date']).strftime('%d.%m.%Y')} — {date.fromisoformat(r['end_date']).strftime('%d.%m.%Y')}\n"
                f"💰 {money(r['total'])} — {labels.get(r['status'], r['status'])}"
            )
        await callback.message.answer("\n".join(out))
    await callback.answer()

async def terms(callback: CallbackQuery):
    await callback.message.answer(
        "ℹ️ <b>Условия</b>\n\n"
        "• Бронирование оформляется после подтверждения заявки.\n"
        "• Цена рассчитывается автоматически по количеству суток.\n"
        "• Заявка удерживает выбранные даты ограниченное время до подтверждения.\n"
        "• Детали получения и возврата согласовываются с менеджером."
    )
    await callback.answer()

async def admin_action(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    action, bid_s = callback.data.split(":")
    bid = int(bid_s)
    con = db()
    row = con.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not row:
        con.close(); await callback.answer("Заявка не найдена.", show_alert=True); return
    if action == "confirm":
        if not available(row["car_id"], date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])):
            # The row itself is an overlap, so check whether another active booking exists.
            others = con.execute("""
              SELECT id FROM bookings WHERE car_id=? AND id<>? AND status IN ('pending','confirmed')
                AND date(start_date) < date(?) AND date(end_date) > date(?) LIMIT 1
            """, (row["car_id"], bid, row["end_date"], row["start_date"])).fetchone()
            if others:
                con.execute("UPDATE bookings SET status='rejected' WHERE id=?", (bid,))
                con.commit(); con.close()
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.bot.send_message(row["user_id"], f"❌ Заявка №{bid} отклонена: выбранные даты уже заняты.")
                await callback.answer("Даты заняты.", show_alert=True); return
        con.execute("UPDATE bookings SET status='confirmed', expires_at=NULL WHERE id=?", (bid,))
        con.commit(); con.close()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.bot.send_message(row["user_id"], f"✅ <b>Заявка №{bid} подтверждена!</b>\n\n🚗 {CARS[row['car_id']]['name']}\n📅 {date.fromisoformat(row['start_date']).strftime('%d.%m.%Y')} — {date.fromisoformat(row['end_date']).strftime('%d.%m.%Y')}\n💰 {money(row['total'])}")
        await callback.answer("Заявка подтверждена.")
    else:
        con.execute("UPDATE bookings SET status='rejected' WHERE id=?", (bid,))
        con.commit(); con.close()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.bot.send_message(row["user_id"], f"❌ Заявка №{bid} отклонена.")
        await callback.answer("Заявка отклонена.")

async def publish(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = (
        "🚗 <b>Balticar — аренда автомобилей</b>\n\n"
        "Выберите автомобиль, посмотрите стоимость и забронируйте его прямо в Telegram."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚗 Забронировать автомобиль", url=f"https://t.me/{message.bot.me.username}")
    ]])
    await message.bot.send_message(CHANNEL_USERNAME, text, reply_markup=kb)
    await message.answer("Готово: пост с кнопкой опубликован в канале.")

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в .env")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.message.register(start_handler, Command("start"))
    dp.message.register(id_handler, Command("id"))
    dp.message.register(publish, Command("publish"))
    dp.message.register(admin_panel, Command("admin"))
    dp.callback_query.register(catalog, F.data == "catalog")
    dp.callback_query.register(
    admin_car_calendar,
    F.data.startswith("admincar:")
)
    dp.callback_query.register(car_selected, F.data.startswith("car:"))
    dp.callback_query.register(pick_dates, F.data.startswith("pick:"))
    dp.callback_query.register(month, F.data.startswith("month:"))
    dp.callback_query.register(start_day, F.data.startswith("day:"))
    dp.callback_query.register(endmonth, F.data.startswith("endmonth:"))
    dp.callback_query.register(end_day, F.data.startswith("end:"))
    dp.callback_query.register(mybookings, F.data == "mybookings")
    dp.callback_query.register(terms, F.data == "terms")
    dp.callback_query.register(admin_action, F.data.startswith("confirm:"))
    dp.callback_query.register(admin_action, F.data.startswith("reject:"))
    dp.callback_query.register(admin_calendar, F.data == "admin:calendar")
    dp.callback_query.register(lambda c: c.answer(), F.data == "noop")

    dp.message.register(name_handler, Booking.name)
    dp.message.register(phone_handler, Booking.phone)
    dp.message.register(comment_handler, Booking.comment)

    # Render provides a public HTTPS URL in RENDER_EXTERNAL_URL.
    # We use Telegram webhook mode instead of polling so this can run as a free Web Service.
    external_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not external_url:
        # Local fallback: keep the traditional polling mode when no Render URL exists.
        print("RENDER_EXTERNAL_URL is not set; starting polling mode.")
        await dp.start_polling(bot)
        return

    webhook_url = f"{external_url}{WEBHOOK_PATH}"
    secret = WEBHOOK_SECRET or secrets.token_urlsafe(32)
    await bot.set_webhook(
        webhook_url,
        secret_token=secret,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )

    async def health(_: web.Request):
        return web.Response(text="Balticar bot is running")

    async def telegram_webhook(request: web.Request):
        if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            raise web.HTTPForbidden(text="Invalid webhook secret")
        data = await request.json()
        from aiogram.types import Update
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post(WEBHOOK_PATH, telegram_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Balticar bot started in webhook mode: {webhook_url}")

    try:
        await asyncio.Event().wait()
    finally:
        await bot.delete_webhook(drop_pending_updates=False)
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
