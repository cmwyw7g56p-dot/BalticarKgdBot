import asyncio
import calendar
import hashlib
import hmac
import json
import os
import secrets
import time as monotonic_time
from pathlib import Path
from datetime import date, datetime, timedelta, time
from html import escape as escape_html
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from aiohttp import ClientSession, ClientTimeout, web
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
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "0") or 0
)

ADMIN_IDS = {ADMIN_ID} if ADMIN_ID else set()
for _aid in (os.getenv("ADMIN_IDS", "") or "").split(","):
    _aid = _aid.strip()
    if _aid.isdigit():
        ADMIN_IDS.add(int(_aid))

def is_admin(uid) -> bool:
    try:
        return int(uid) in ADMIN_IDS
    except (TypeError, ValueError):
        return False


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

# Максимальный возраст подписи Telegram WebApp initData (секунды).
INIT_DATA_MAX_AGE = int(
    os.getenv(
        "INIT_DATA_MAX_AGE_SECONDS",
        "86400"
    )
)

_RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
MINIAPP_BASE_URL = (_RENDER_URL + "/app") if _RENDER_URL else "https://balticarkgdbot.onrender.com/app"
MINIAPP_URL = MINIAPP_BASE_URL + "?v=55"
ADMIN_WEBAPP_URL = ((_RENDER_URL + "/admin") if _RENDER_URL else "https://balticarkgdbot.onrender.com/admin") + "?v=74"


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

# Льготный период после полных суток: возврат с опозданием до N часов
# не добавляет следующие оплачиваемые сутки.
# Пример: 20.10 09:00 → 23.10 09:00 = 3 суток; до 23.10 12:00 всё ещё 3 суток.
DAY_GRACE_HOURS = int(
    os.getenv(
        "DAY_GRACE_HOURS",
        "3"
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
        "photos": ["photos/solaris21_1_front.jpg",
        "photos/solaris21_2_rear.jpg",
        "photos/solaris21_3_interior.jpg"
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
        "photos": ["photos/solaris20_1_front.jpg",
        "photos/solaris20_2_rear.jpg",
        "photos/solaris20_3_interior.jpg"
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
        "photos": ["photos/solaris17_1_front.jpg",
        "photos/solaris17_2_rear.jpg",
        "photos/solaris17_3_interior.jpg"
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
        "photos": ["photos/i30_1_front.jpg",
        "photos/i30_2_rear.jpg",
        "photos/i30_3_interior.jpg"
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
    client_search = State()
    client_notes = State()


# ============================================================
# BACKGROUND TASKS
# ============================================================

# Ссылки на фоновые задачи: без них event loop держит задачу слабой ссылкой,
# и сборщик мусора может убить её посреди работы.
_BG_TASKS = set()


def spawn_task(coro):
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


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
                CREATE TABLE IF NOT EXISTS expenses (
                    id BIGSERIAL PRIMARY KEY,
                    expense_date DATE NOT NULL,
                    car_id TEXT,
                    category TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    comment TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_expenses_date
                ON expenses(expense_date)
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_maintenance_car_datetime
                ON car_maintenance(car_id, start_at, end_at)
                """
            )


            # --- Clients CRM + planner columns ---
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE,
                    full_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    username TEXT,
                    notes TEXT,
                    blacklisted BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients (phone)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_clients_name ON clients (full_name)")

            cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS client_id BIGINT")
            cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS issued_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS returned_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS issue_notes TEXT")

            cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS return_notes TEXT")

            for sql in (
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS passport_series TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS passport_number TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS passport_issued_by TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS passport_issued_date TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS license_number TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS license_issued_date TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS license_categories TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS address TEXT",
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS birth_date TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS deposit_amount INTEGER DEFAULT 0",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS deposit_status TEXT DEFAULT 'none'",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS mileage_out INTEGER",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS mileage_in INTEGER",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fuel_out TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS fuel_in TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS contract_number TEXT",
            ):
                cur.execute(sql)


            # Dynamic fleet fields on car_settings
            for sql in (
                "ALTER TABLE car_settings ADD COLUMN IF NOT EXISTS gear TEXT DEFAULT 'АКПП'",
                "ALTER TABLE car_settings ADD COLUMN IF NOT EXISTS fuel TEXT DEFAULT 'Бензин'",
                "ALTER TABLE car_settings ADD COLUMN IF NOT EXISTS seats INTEGER DEFAULT 5",
                "ALTER TABLE car_settings ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''",
                "ALTER TABLE car_settings ADD COLUMN IF NOT EXISTS is_custom BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE car_settings ADD COLUMN IF NOT EXISTS plate TEXT DEFAULT ''",
                "ALTER TABLE car_settings ADD COLUMN IF NOT EXISTS vin TEXT DEFAULT ''",
            ):
                cur.execute(sql)

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
    """Загружает настройки и кастомные автомобили из PostgreSQL в CARS."""
    con = db()
    try:
        with con.cursor() as cur:
            rows = cur.execute("SELECT * FROM car_settings").fetchall()
        known = set(CARS.keys())
        for row in rows:
            cid = row['car_id']
            rates = (row['rate_1_3'], row['rate_4_6'], row['rate_7_plus'])
            if cid in CARS:
                CARS[cid]['name'] = row['name']
                CARS[cid]['rates'] = rates
                CARS[cid]['active'] = row['active']
                if row.get('gear'):
                    CARS[cid]['gear'] = row['gear']
                if row.get('fuel'):
                    CARS[cid]['fuel'] = row['fuel']
                if row.get('seats'):
                    CARS[cid]['seats'] = row['seats']
                if row.get('description') is not None:
                    CARS[cid]['description'] = row['description'] or CARS[cid].get('description', '')
                if row.get('plate') is not None:
                    CARS[cid]['plate'] = row.get('plate') or ''
                if row.get('vin') is not None:
                    CARS[cid]['vin'] = row.get('vin') or ''
            else:
                # custom car added from admin
                CARS[cid] = {
                    'name': row['name'],
                    'gear': row.get('gear') or 'АКПП',
                    'fuel': row.get('fuel') or 'Бензин',
                    'seats': row.get('seats') or 5,
                    'rates': rates,
                    'active': row['active'],
                    'photos': [],
                    'description': row.get('description') or '',
                    'is_custom': True,
                    'plate': row.get('plate') or '',
                    'vin': row.get('vin') or '',
                }
        # soft-remove custom cars deleted from DB
        for cid in list(CARS.keys()):
            if cid not in known and not any(r['car_id'] == cid for r in rows):
                CARS.pop(cid, None)
    finally:
        con.close()

# ============================================================
# CLIENTS CRM
# ============================================================

def upsert_client_sync(user_id, username, full_name, phone, notes=None):
    """Find client by user_id or phone, update or create."""
    con = db()
    try:
        with con.cursor() as cur:
            row = None
            if user_id:
                row = cur.execute(
                    "SELECT * FROM clients WHERE user_id=%s",
                    (user_id,)
                ).fetchone()
            if not row and phone:
                row = cur.execute(
                    "SELECT * FROM clients WHERE phone=%s ORDER BY id DESC LIMIT 1",
                    (phone,)
                ).fetchone()

            if row:
                cur.execute(
                    """
                    UPDATE clients
                    SET full_name=%s,
                        phone=%s,
                        username=COALESCE(%s, username),
                        notes=COALESCE(%s, notes),
                        updated_at=NOW()
                    WHERE id=%s
                    RETURNING *
                    """,
                    (full_name, phone, username, notes, row["id"])
                )
                client = cur.fetchone()
            else:
                client = cur.execute(
                    """
                    INSERT INTO clients (user_id, full_name, phone, username, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (user_id, full_name, phone, username, notes)
                ).fetchone()
        con.commit()
        return client
    finally:
        con.close()


def get_client_sync(client_id=None, user_id=None, phone=None):
    con = db()
    try:
        with con.cursor() as cur:
            if client_id is not None:
                return cur.execute("SELECT * FROM clients WHERE id=%s", (client_id,)).fetchone()
            if user_id is not None:
                return cur.execute("SELECT * FROM clients WHERE user_id=%s", (user_id,)).fetchone()
            if phone:
                return cur.execute(
                    "SELECT * FROM clients WHERE phone=%s ORDER BY id DESC LIMIT 1",
                    (phone,)
                ).fetchone()
            return None
    finally:
        con.close()


def search_clients_sync(query, limit=30):
    q = f"%{(query or '').strip()}%"
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                """
                SELECT *
                FROM clients
                WHERE full_name ILIKE %s
                   OR phone ILIKE %s
                   OR COALESCE(username,'') ILIKE %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (q, q, q, limit)
            ).fetchall()
    finally:
        con.close()


def client_bookings_sync(client_id, limit=20):
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE client_id=%s
                   OR (
                        client_id IS NULL
                        AND phone = (SELECT phone FROM clients WHERE id=%s)
                   )
                ORDER BY COALESCE(start_at, created_at) DESC
                LIMIT %s
                """,
                (client_id, client_id, limit)
            ).fetchall()
    finally:
        con.close()


def set_client_blacklist_sync(client_id, value: bool):
    con = db()
    try:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE clients SET blacklisted=%s, updated_at=NOW() WHERE id=%s",
                (value, client_id)
            )
        con.commit()
    finally:
        con.close()


def set_client_notes_sync(client_id, notes: str):
    con = db()
    try:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE clients SET notes=%s, updated_at=NOW() WHERE id=%s",
                (notes, client_id)
            )
        con.commit()
    finally:
        con.close()


def list_recent_clients_sync(limit=30):
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                "SELECT * FROM clients ORDER BY updated_at DESC LIMIT %s",
                (limit,)
            ).fetchall()
    finally:
        con.close()


# ============================================================
# PLANNER (issue / return)
# ============================================================

def get_today_pickups_sync():
    today = datetime.now(TZ).date()
    day_start = local_dt(today, time(0, 0))
    day_end = day_start + timedelta(days=1)
    con = db()
    try:
        with con.cursor() as cur:
            try:
                return cur.execute(
                    """
                    SELECT *
                    FROM bookings
                    WHERE status = 'confirmed'
                      AND issued_at IS NULL
                      AND start_at >= %s
                      AND start_at < %s
                    ORDER BY start_at
                    """,
                    (day_start, day_end)
                ).fetchall()
            except Exception as exc:
                con.rollback()
                print(f"[PLANNER] pickups fallback: {exc}", flush=True)
                return cur.execute(
                    """
                    SELECT *
                    FROM bookings
                    WHERE status = 'confirmed'
                      AND start_at >= %s
                      AND start_at < %s
                    ORDER BY start_at
                    """,
                    (day_start, day_end)
                ).fetchall()
    finally:
        con.close()


def get_today_returns_sync():
    today = datetime.now(TZ).date()
    day_start = local_dt(today, time(0, 0))
    day_end = day_start + timedelta(days=1)
    con = db()
    try:
        with con.cursor() as cur:
            try:
                return cur.execute(
                    """
                    SELECT *
                    FROM bookings
                    WHERE status = 'confirmed'
                      AND issued_at IS NOT NULL
                      AND returned_at IS NULL
                      AND end_at >= %s
                      AND end_at < %s
                    ORDER BY end_at
                    """,
                    (day_start, day_end)
                ).fetchall()
            except Exception as exc:
                con.rollback()
                print(f"[PLANNER] returns fallback: {exc}", flush=True)
                return []
    finally:
        con.close()


def get_tomorrow_pickups_sync():
    day = datetime.now(TZ).date() + timedelta(days=1)
    day_start = local_dt(day, time(0, 0))
    day_end = day_start + timedelta(days=1)
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE status = 'confirmed'
                  AND issued_at IS NULL
                  AND start_at >= %s
                  AND start_at < %s
                ORDER BY start_at
                """,
                (day_start, day_end)
            ).fetchall()
    finally:
        con.close()


def get_in_progress_rentals_sync():
    con = db()
    try:
        with con.cursor() as cur:
            return cur.execute(
                """
                SELECT *
                FROM bookings
                WHERE status = 'confirmed'
                  AND issued_at IS NOT NULL
                  AND returned_at IS NULL
                ORDER BY end_at
                """
            ).fetchall()
    finally:
        con.close()


def mark_issued_sync(bid, notes=None, deposit_amount=None, mileage_out=None, fuel_out=None):
    con = db()
    try:
        with con.cursor() as cur:
            row = cur.execute(
                """
                UPDATE bookings
                SET issued_at = NOW(),
                    issue_notes = COALESCE(%s, issue_notes),
                    deposit_amount = COALESCE(%s, deposit_amount, 0),
                    deposit_status = CASE WHEN COALESCE(%s, deposit_amount, 0) > 0 THEN 'held' ELSE COALESCE(deposit_status, 'none') END,
                    mileage_out = COALESCE(%s, mileage_out),
                    fuel_out = COALESCE(%s, fuel_out)
                WHERE id=%s AND status='confirmed' AND issued_at IS NULL
                RETURNING *
                """,
                (notes, deposit_amount, deposit_amount, mileage_out, fuel_out, bid)
            ).fetchone()
        con.commit()
        return row
    finally:
        con.close()


def mark_returned_sync(bid, notes=None, mileage_in=None, fuel_in=None, deposit_status=None):
    con = db()
    try:
        with con.cursor() as cur:
            row = cur.execute(
                """
                UPDATE bookings
                SET returned_at = NOW(),
                    return_notes = COALESCE(%s, return_notes),
                    status = 'completed',
                    mileage_in = COALESCE(%s, mileage_in),
                    fuel_in = COALESCE(%s, fuel_in),
                    deposit_status = COALESCE(%s, deposit_status, deposit_status)
                WHERE id=%s
                  AND status='confirmed'
                  AND issued_at IS NOT NULL
                  AND returned_at IS NULL
                RETURNING *
                """,
                (notes, mileage_in, fuel_in, deposit_status, bid)
            ).fetchone()
        con.commit()
        return row
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

            expire_pending(cur)

        con.commit()

    finally:

        con.close()


# ============================================================
# ОБЩИЕ SQL-ХЕЛПЕРЫ (одна копия вместо 3-4 одинаковых)
# ============================================================

def lock_bookings(cur):
    """Транзакционная advisory-блокировка на все операции с бронями."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtext('balticar-bookings'))"
    )


def expire_pending(cur):
    """Истёкшие pending-заявки -> expired."""
    cur.execute(
        """
        UPDATE bookings
        SET status='expired'
        WHERE status='pending'
          AND expires_at IS NOT NULL
          AND expires_at < NOW()
        """
    )


def lock_booking_row(cur, bid):
    """SELECT ... FOR UPDATE по одной брони."""
    return cur.execute(
        "SELECT * FROM bookings WHERE id=%s FOR UPDATE",
        (bid,)
    ).fetchone()


def find_maintenance(cur, car_id, start_at, end_at):
    """Пересечение периода с техобслуживанием (без буфера)."""
    return cur.execute(
        """
        SELECT id FROM car_maintenance
        WHERE car_id=%s AND start_at < %s AND end_at > %s
        LIMIT 1
        """,
        (car_id, end_at, start_at)
    ).fetchone()


def find_overlap(cur, car_id, start_at, end_at, exclude_id=None):
    """
    Пересечение с другой активной бронью с учётом технического буфера
    BUFFER_HOURS. exclude_id — бронь, которую нужно не учитывать
    (при переносе дат / подтверждении).
    """
    buffer_delta = timedelta(hours=BUFFER_HOURS)
    return cur.execute(
        """
        SELECT id, status FROM bookings
        WHERE car_id=%s
          AND id IS DISTINCT FROM %s::bigint
          AND status IN ('pending','confirmed')
          AND start_at < %s
          AND end_at > %s
        LIMIT 1
        """,
        (car_id, exclude_id, end_at + buffer_delta, start_at - buffer_delta)
    ).fetchone()


def mark_booking_cancelled(cur, bid):
    cur.execute(
        "UPDATE bookings SET status='cancelled', expires_at=NULL WHERE id=%s",
        (bid,)
    )


def mark_booking_rejected(cur, bid):
    cur.execute(
        "UPDATE bookings SET status='rejected' WHERE id=%s",
        (bid,)
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
    Оплачиваемые сутки с льготным периодом DAY_GRACE_HOURS.

    Полные 24 ч = 1 сутки. Опоздание до DAY_GRACE_HOURS после
    кратного 24 ч не добавляет следующие сутки.

    При DAY_GRACE_HOURS=3:
      20.10 09:00 → 23.10 09:00 = 3 суток (ровно 72 ч)
      20.10 09:00 → 23.10 12:00 = 3 суток (72+3 ч)
      20.10 09:00 → 23.10 13:00 = 4 суток
    """

    hours = rental_hours(
        start_at,
        end_at
    )
    if hours <= 0:
        return 1

    # Вычитаем льготу, затем округляем сутки вверх
    billable = max(0.0, hours - float(DAY_GRACE_HOURS))
    days = int((billable + 23.999999) // 24)

    return max(1, days)


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
        "completed": "🔵 Завершена",
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
    # Минимальное стартовое меню: одна кнопка открывает Mini App.
    # Доступ к Mini App также постоянно есть в меню чата Telegram.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 Открыть BALTICAR",
                    web_app=WebAppInfo(url=MINIAPP_URL),
                )
            ]
        ]
    )


async def configure_client_menu(bot: Bot):
    """Настройка стартового экрана бота и постоянной кнопки Mini App в меню чата Telegram.

    Описание и краткое описание отображаются на экране «Что умеет этот бот?»
    до нажатия кнопки «Старт» (как в стандартном интерфейсе Telegram).
    """
    # Полное описание бота (до 512 символов) — экран «Что умеет этот бот?»
    bot_description = (
        "Официальный бот аренды автомобилей BALTICAR в Калининграде.\n\n"
        "Каталог автомобилей, наличие в реальном времени, "
        "онлайн-бронирование и связь с менеджером."
    )
    # Краткое описание (до 120 символов) — отображается в списке чатов / поиске
    bot_short_description = (
        "Аренда автомобилей в Калининграде. "
        "Каталог, свободные даты, онлайн-бронирование."
    )

    try:
        await bot.set_my_description(description=bot_description)
        await bot.set_my_short_description(short_description=bot_short_description)
        print("[TELEGRAM] Bot description and short description set")
    except Exception as exc:
        print(f"[TELEGRAM] Failed to set bot description: {type(exc).__name__}: {exc}")

    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🚗 BALTICAR",
                web_app=WebAppInfo(url=MINIAPP_URL),
            )
        )
        print(f"[TELEGRAM] Mini App menu button configured: {MINIAPP_URL}")
    except Exception as exc:
        # Не блокируем запуск бота, если Telegram временно не принял настройку меню.
        print(f"[TELEGRAM] Failed to configure Mini App menu button: {type(exc).__name__}: {exc}")


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
            text=(
                f"🚗 {car['name']}  •  от {money(car['rates'][2])}/сутки  •  "
                f"{car['gear']}  •  {car['seats']} мест"
            ),
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


async def replace_client_message(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
):
    """Показывает новый клиентский экран независимо от типа текущего сообщения.

    Карточка автомобиля отправляется как photo-message, поэтому Telegram
    не позволяет использовать edit_text() для её навигационных кнопок.
    Для photo-message удаляем карточку и отправляем новый текстовый экран.
    """
    message = callback.message
    if getattr(message, "photo", None):
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup)
        return

    await message.edit_text(text, reply_markup=reply_markup)


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
                    text="🖥 Открыть панель учёта",
                    web_app=WebAppInfo(url=ADMIN_WEBAPP_URL)
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

    await replace_client_message(
        callback,
        welcome_text(),
        main_keyboard()
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

    # Всегда перечитываем тарифы из БД перед показом каталога,
    # чтобы клиент сразу видел изменения, сделанные администратором.
    await asyncio.to_thread(load_car_settings)

    await replace_client_message(
        callback,
        "🚗 <b>Автомобили BALTICAR</b>\n\n"
        "Выберите модель — откроется её фотокарточка, характеристики и актуальные тарифы.\n\n"
        "💡 <b>Цена за выбранный период рассчитывается автоматически.</b>",
        car_keyboard()
    )


async def car_selected(
    callback: CallbackQuery
):

    await safe_callback_answer(callback)

    # Обновляем настройки перед открытием карточки автомобиля,
    # чтобы тарифы в уже существующих кнопках тоже не устаревали.
    await asyncio.to_thread(load_car_settings)

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

    title, keyboard = await asyncio.to_thread(
        time_keyboard,
        cid,
        start_d,
        "pickup"
    )

    await callback.message.answer(
        title,
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

    title, keyboard = await asyncio.to_thread(
        time_keyboard,
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

    title, keyboard = await asyncio.to_thread(
        time_keyboard,
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

            lock_bookings(cur)

            # ==================================================
            # Истёкшие pending
            # ==================================================

            expire_pending(cur)

            maintenance = find_maintenance(cur, cid, start_at, end_at)

            if maintenance:
                con.rollback()
                return {"ok": False, "reason": "maintenance"}

            overlap = find_overlap(cur, cid, start_at, end_at)

            print(
                f"[CREATE_OVERLAP] car={cid} start={start_at.isoformat()} "
                f"end={end_at.isoformat()} buffer_h={BUFFER_HOURS} overlap={overlap!r}"
            )

            if overlap:

                con.rollback()

                return {
                    "ok": False,
                    "reason": "overlap"
                }

            start_date = start_at.date()
            end_date = end_at.date()

            # CRM: upsert client inside same transaction connection
            client_row = None
            if message_user_id:
                client_row = cur.execute(
                    "SELECT * FROM clients WHERE user_id=%s",
                    (message_user_id,)
                ).fetchone()
            if not client_row and phone:
                client_row = cur.execute(
                    "SELECT * FROM clients WHERE phone=%s ORDER BY id DESC LIMIT 1",
                    (phone,)
                ).fetchone()
            if client_row:
                if client_row.get("blacklisted"):
                    con.rollback()
                    return {"ok": False, "reason": "blacklisted"}
                cur.execute(
                    """
                    UPDATE clients
                    SET full_name=%s,
                        phone=%s,
                        username=COALESCE(%s, username),
                        updated_at=NOW()
                    WHERE id=%s
                    """,
                    (name, phone, username, client_row["id"])
                )
                client_id = client_row["id"]
            else:
                client_row = cur.execute(
                    """
                    INSERT INTO clients (user_id, full_name, phone, username)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (message_user_id, name, phone, username)
                ).fetchone()
                client_id = client_row["id"]

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
                    expires_at,
                    client_id
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    'pending', %s, %s, %s
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
                    expires,
                    client_id
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
                ORDER BY id ASC
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
    await replace_client_message(
        callback,
        "📋 <b>Мои бронирования</b>\n\n"
        "Здесь хранятся ваши последние заявки.\n"
        "Нажмите на заявку, чтобы открыть детали.",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


def get_user_booking_sync(user_id, bid):
    cleanup_pending(); con=db()
    try:
        with con.cursor() as cur:
            return cur.execute("SELECT * FROM bookings WHERE id=%s AND user_id=%s",(bid,user_id)).fetchone()
    finally: con.close()


def cancel_booking_by_user_sync(user_id, bid):
    """
    Отмена брони самим клиентом (общая логика бота и мини-приложения).

    Возвращает (state, row): state = 'ok' | 'not_found' | 'closed'.
    """
    con = db()
    try:
        with con.cursor() as cur:
            row = cur.execute(
                "SELECT * FROM bookings WHERE id=%s AND user_id=%s FOR UPDATE",
                (bid, user_id)
            ).fetchone()
            if not row:
                con.rollback()
                return "not_found", None
            if row["status"] not in ("pending", "confirmed"):
                con.rollback()
                return "closed", row
            mark_booking_cancelled(cur, bid)
        con.commit()
        return "ok", row
    finally:
        con.close()


def user_cancel_booking_sync(user_id, bid):
    state, row = cancel_booking_by_user_sync(user_id, bid)
    return row if state == "ok" else None


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


def create_review_for_user_sync(bid, user_id, rating, text):
    """
    Создание отзыва (общая логика бота и мини-приложения).

    Возвращает 'ok' | 'not_found' | 'not_finished'.
    """
    con = db()
    try:
        with con.cursor() as cur:
            row = cur.execute(
                "SELECT * FROM bookings WHERE id=%s AND user_id=%s AND status='confirmed'",
                (bid, user_id)
            ).fetchone()
            if not row:
                con.rollback()
                return "not_found"
            end_at = (
                ensure_tz(row["end_at"]) if row.get("end_at")
                else local_dt(row["end_date"], time(17, 0))
            )
            if end_at >= datetime.now(TZ):
                con.rollback()
                return "not_finished"
            cur.execute(
                """INSERT INTO reviews(booking_id,user_id,car_id,rating,review_text)
                   VALUES(%s,%s,%s,%s,%s) ON CONFLICT(booking_id) DO NOTHING""",
                (bid, user_id, row["car_id"], rating, text)
            )
        con.commit()
        return "ok"
    finally:
        con.close()


def create_review_sync(bid, user_id, rating, text):
    return create_review_for_user_sync(bid, user_id, rating, text) == "ok"

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

def admin_all_calendar_sync(year, month):
    first = date(year, month, 1)
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    month_start = local_dt(first, time(0, 0))
    month_end = local_dt(next_first, time(0, 0))

    con = db()
    try:
        with con.cursor() as cur:
            bookings = cur.execute("""
                SELECT car_id, id, status, start_at, end_at
                FROM bookings
                WHERE status IN ('pending','confirmed')
                  AND start_at < %s AND end_at > %s
                ORDER BY car_id, start_at
            """, (month_end, month_start)).fetchall()
            maintenance = cur.execute("""
                SELECT car_id, id, 'maintenance' AS status, start_at, end_at
                FROM car_maintenance
                WHERE start_at < %s AND end_at > %s
                ORDER BY car_id, start_at
            """, (month_end, month_start)).fetchall()
            return list(bookings) + list(maintenance)
    finally:
        con.close()


def admin_all_calendar_text(year, month, rows):
    """Compact, readable fleet calendar for Telegram.

    The month is split into calendar weeks so the day numbers never run
    together. All cars remain visible in every week block, and a separate
    occupancy line makes overlaps immediately obvious.
    """
    days = calendar.monthrange(year, month)[1]
    today = datetime.now(TZ).date()
    by_car = {cid: [] for cid in CARS}
    for row in rows:
        if row['car_id'] in by_car:
            by_car[row['car_id']].append(row)

    months = [
        'Январь','Февраль','Март','Апрель','Май','Июнь',
        'Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'
    ]
    weekdays = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']

    # Stable short labels so car names never collide or get cut off.
    labels = {}
    for cid, car in CARS.items():
        name = car.get('name', cid)
        if 'solaris21' in cid:
            label = 'S21'
        elif 'solaris20' in cid:
            label = 'S20'
        elif 'solaris17' in cid:
            label = 'S17'
        elif 'i30' in cid:
            label = 'i30'
        else:
            label = cid[:4]
        labels[cid] = label

    def day_status(cid, current):
        if current < today:
            return '—'
        day_start = local_dt(current, time(0, 0))
        day_end = day_start + timedelta(days=1)
        matched = [
            r for r in by_car[cid]
            if ensure_tz(r['start_at']) < day_end and ensure_tz(r['end_at']) > day_start
        ]
        if any(r['status'] == 'maintenance' for r in matched):
            return 'M'
        if any(r['status'] == 'confirmed' for r in matched):
            return '■'
        if any(r['status'] == 'pending' for r in matched):
            return 'P'
        return '·'

    # Build Monday-Sunday calendar rows, including leading/trailing days
    # only inside their actual month. Each block is max 7 columns wide.
    weeks = calendar.monthcalendar(year, month)
    lines = [
        f"📅 <b>Занятость автопарка</b>",
        f"<b>{months[month-1]} {year}</b>",
        '',
        '<code>■ бронь   P заявка   M сервис   · свободно   — прошедший день</code>',
        '<code>⚠ = одновременно занято 2+ авто</code>',
    ]

    for week_no, week in enumerate(weeks, 1):
        active = [(i, d) for i, d in enumerate(week) if d]
        if not active:
            continue
        first_day = active[0][1]
        last_day = active[-1][1]
        lines.append('')
        lines.append(f'<b>{first_day}–{last_day} {months[month-1].lower()}</b>')
        lines.append('<code>       ' + ' '.join(f'{weekdays[i]:>2}' for i, d in enumerate(week) if d) + '</code>')
        lines.append('<code>       ' + ' '.join(f'{d:>2}' for d in week if d) + '</code>')

        counts = []
        for i, d in enumerate(week):
            if not d:
                continue
            current = date(year, month, d)
            count = 0
            for cid in CARS:
                st = day_status(cid, current)
                if st in ('■', 'P', 'M'):
                    count += 1
            counts.append(count)

        for cid in CARS:
            cells = []
            for d in week:
                if not d:
                    continue
                cells.append(day_status(cid, date(year, month, d)))
            lines.append(f"<code>{labels[cid]:>4}   {' '.join(cells)}</code>")

        overlap_marks = ['!' if n >= 2 else str(n) for n in counts]
        lines.append('<code>занято ' + '   '.join(f'{x:>2}' for x in overlap_marks) + '</code>')

    lines += [
        '',
        '<b>Пересечение:</b> <code>!</code> означает, что в этот день одновременно занято 2 или больше автомобилей.',
        'Нажмите «Все бронирования», чтобы открыть заявки и их статусы.',
    ]
    return '\n'.join(lines)


def admin_all_calendar_keyboard(year, month):
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    if month == 1:
        prev_first = date(year - 1, 12, 1)
    else:
        prev_first = date(year, month - 1, 1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='‹', callback_data=f'adminallmonth:{prev_first.isoformat()}'),
            InlineKeyboardButton(text='📅 Месяц', callback_data='noop'),
            InlineKeyboardButton(text='›', callback_data=f'adminallmonth:{next_first.isoformat()}'),
        ],
        [InlineKeyboardButton(text='🚗 Календарь отдельного авто', callback_data='admin:calendar_cars')],
        [InlineKeyboardButton(text='📋 Все бронирования', callback_data='admin:bookings')],
        [InlineKeyboardButton(text='◀️ В админ-панель', callback_data='admin:back')],
    ])


async def admin_calendar(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.message.answer('Нет доступа.')
        return
    today = datetime.now(TZ).date()
    rows = await asyncio.to_thread(admin_all_calendar_sync, today.year, today.month)
    await callback.message.edit_text(
        admin_all_calendar_text(today.year, today.month, rows),
        reply_markup=admin_all_calendar_keyboard(today.year, today.month),
    )


async def admin_calendar_cars(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    rows = [[InlineKeyboardButton(text=f"🚗 {car['name']}", callback_data=f'admincar:{cid}')] for cid, car in CARS.items()]
    rows.append([InlineKeyboardButton(text='◀️ Все автомобили на одном листе', callback_data='admin:calendar')])
    rows.append([InlineKeyboardButton(text='◀️ В админ-панель', callback_data='admin:back')])
    await callback.message.edit_text(
        '📅 <b>Календарь отдельного автомобиля</b>\n\nВыберите автомобиль:',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def admin_all_month(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    _, iso = callback.data.split(':', 1)
    d = date.fromisoformat(iso)
    rows = await asyncio.to_thread(admin_all_calendar_sync, d.year, d.month)
    await callback.message.edit_text(
        admin_all_calendar_text(d.year, d.month, rows),
        reply_markup=admin_all_calendar_keyboard(d.year, d.month),
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
            lock_bookings(cur)
            row=lock_booking_row(cur,bid)
            if not row:
                con.rollback(); return {"ok":False,"message":"Заявка не найдена."}
            if row["status"] not in ("pending","confirmed"):
                con.rollback(); return {"ok":False,"message":"Заявку нельзя изменить в текущем статусе."}
            if end_at <= start_at:
                con.rollback(); return {"ok":False,"message":"Возврат должен быть позже получения."}

            maintenance = find_maintenance(cur, row["car_id"], start_at, end_at)
            if maintenance:
                con.rollback(); return {"ok":False,"message":"❌ Новый период попадает на обслуживание автомобиля."}

            other=find_overlap(cur,row["car_id"],start_at,end_at,exclude_id=bid)
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
        elif row["status"] == "rejected":
            buttons.append([
                InlineKeyboardButton(
                    text="🗑 Удалить отклонённую бронь",
                    callback_data=f"deleterej:{bid}"
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

def delete_booking_sync(bid, allowed_statuses=("cancelled", "rejected")):

    con = db()

    try:

        with con.cursor() as cur:

            row = lock_booking_row(cur, bid)

            if not row:
                con.rollback()
                return {"ok": False, "reason": "not_found"}

            if row["status"] not in allowed_statuses:
                con.rollback()
                return {
                    "ok": False,
                    "reason": "not_deletable",
                    "status": row["status"]
                }

            cur.execute(
                """
                DELETE FROM bookings
                WHERE id=%s
                  AND status = ANY(%s)
                """,
                (bid, list(allowed_statuses))
            )

            con.commit()

            return {"ok": True, "row": row}

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


def delete_cancelled_booking_sync(bid):
    return delete_booking_sync(bid, ("cancelled",))


def delete_rejected_booking_sync(bid):
    return delete_booking_sync(bid, ("rejected",))


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
        if row["status"] in ("cancelled", "rejected"):
            status = row["status"]
            callback_data = f"deletecancel:{row['id']}" if status == "cancelled" else f"deleterej:{row['id']}"
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑 Удалить бронь №{row['id']}",
                    callback_data=callback_data
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


async def delete_rejected_booking(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.message.answer("Нет доступа.")
        return
    try:
        bid = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Некорректный номер брони.")
        return

    result = await asyncio.to_thread(delete_rejected_booking_sync, bid)
    if not result["ok"]:
        if result["reason"] == "not_found":
            await callback.message.answer("Бронь не найдена или уже удалена.")
        else:
            await callback.message.answer("Удалять можно только отклонённые брони.")
        return

    rows = await asyncio.to_thread(get_all_bookings_sync)
    if not rows:
        await callback.message.edit_text(
            "📋 <b>Все бронирования</b>\n\nБронирований пока нет.",
            reply_markup=admin_back_keyboard()
        )
        return

    keyboard = admin_bookings_markup(rows)
    keyboard.append([
        InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin:back")
    ])
    await callback.message.edit_text(
        "📋 <b>Все бронирования</b>\n\n"
        f"Показаны последние {len(rows)} заявок.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


# ============================================================
# ADMIN ALL BOOKINGS / SEARCH / FILTER
# ============================================================

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
            sql += " ORDER BY id ASC LIMIT %s"; params.append(limit)
            return cur.execute(sql, params).fetchall()
    finally:
        con.close()


def admin_bookings_markup(rows, show_delete=True):
    keyboard=[]
    for row in rows:
        keyboard.append([InlineKeyboardButton(text=f"№{row['id']} • {CARS[row['car_id']]['name'][:18]} • {status_label(row['status'])[:2]}", callback_data=f"adminbooking:{row['id']}")])
        if show_delete and row['status'] in ('cancelled','rejected'):
            status = row['status']
            callback_data = f"deletecancel:{row['id']}" if status == 'cancelled' else f"deleterej:{row['id']}"
            keyboard.append([InlineKeyboardButton(text=f"🗑 Удалить №{row['id']}", callback_data=callback_data)])
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

            lock_bookings(cur)

            expire_pending(cur)

            row = lock_booking_row(cur, bid)

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

                mark_booking_cancelled(cur, bid)

                con.commit()

                return {
                    "ok": True,
                    "action": "cancel",
                    "row": row,
                    "start_at": start_at,
                    "end_at": end_at
                }

            if action == "confirm":

                maintenance = find_maintenance(cur, row["car_id"], start_at, end_at)
                if maintenance:
                    mark_booking_rejected(cur, bid)
                    con.commit()
                    return {"ok":False,"reason":"maintenance","row":row,"start_at":start_at,"end_at":end_at}

                other = find_overlap(cur, row["car_id"], start_at, end_at, exclude_id=bid)

                if other:

                    mark_booking_rejected(cur, bid)

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

                mark_booking_rejected(cur, bid)

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


def admin_reviews_sync(limit=50):
    con=db()
    try:
        with con.cursor() as cur:
            return cur.execute("""
                SELECT r.id, r.booking_id, r.rating, r.review_text, r.created_at,
                       b.name, b.phone, r.car_id
                FROM reviews r
                LEFT JOIN bookings b ON b.id=r.booking_id
                ORDER BY r.created_at DESC
                LIMIT %s
            """,(limit,)).fetchall()
    finally:
        con.close()


async def admin_reviews(callback:CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    rows=await asyncio.to_thread(admin_reviews_sync,50)
    if not rows:
        text="⭐ <b>Отзывы клиентов</b>\n\nПока отзывов нет."
    else:
        parts=["⭐ <b>Отзывы клиентов</b>",f"\nВсего показано: {len(rows)}",""]
        for row in rows:
            stars="⭐"*int(row["rating"] or 0)
            name=escape_html(row.get("name") or "Клиент")
            car=escape_html(CARS.get(row.get("car_id"),{}).get("name",row.get("car_id") or "Автомобиль"))
            review=escape_html((row.get("review_text") or "Без текста").strip())
            if len(review)>500:
                review=review[:497]+"..."
            created=ensure_tz(row["created_at"]).strftime("%d.%m.%Y %H:%M") if row.get("created_at") else ""
            parts.append(f"№{row['booking_id']} · <b>{name}</b> · {car}")
            parts.append(f"{stars} · {created}")
            parts.append(review)
            parts.append("━━━━━━━━━━━━━━━━━━")
        text="\n".join(parts)
    await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В админ-панель",callback_data="admin:back")]]))


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

def complete_past_bookings_sync():
    """confirmed с прошедшим end_at -> completed."""
    con=db()
    try:
        with con.cursor() as cur:
            cur.execute("""
                UPDATE bookings
                SET status='completed'
                WHERE status='confirmed'
                  AND end_at IS NOT NULL
                  AND end_at < NOW()
            """)
            n = cur.rowcount
        con.commit()
        return n
    finally:
        con.close()


def reminder_candidates_sync():
    """Одним соединением обслуживает просроченные заявки и выбирает напоминания."""
    con = db()
    try:
        with con.cursor() as cur:
            # Раньше эти две операции открывали отдельные соединения.
            # Теперь все фоновые проверки выполняются в одной транзакции.
            expire_pending(cur)
            cur.execute("""
                UPDATE bookings
                SET status='completed'
                WHERE status='confirmed'
                  AND end_at IS NOT NULL
                  AND end_at < NOW()
            """)
            rows = cur.execute("""
                SELECT * FROM bookings
                WHERE status='confirmed'
                  AND ((start_at > NOW() AND start_at <= NOW()+interval '2 hours' AND reminder_2_sent=FALSE)
                    OR (start_at > NOW()+interval '2 hours' AND start_at <= NOW()+interval '24 hours' AND reminder_24_sent=FALSE))
                ORDER BY start_at
            """).fetchall()
        con.commit()
        return rows
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


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
                    kind='2'; text=(f"⏰ <b>Напоминание о бронировании №{row['id']}</b>\n\n🚗 {CARS.get(row['car_id'],{}).get('name',row['car_id'])}\n📅 Получение: <b>{format_date_time(start_at)}</b>\n↩️ Возврат: <b>{format_date_time(row['end_at'])}</b>\n\nДо начала аренды осталось около 2 часов.")
                elif delta <= timedelta(hours=24):
                    kind='24'; text=(f"🔔 <b>Напоминание о бронировании №{row['id']}</b>\n\n🚗 {CARS.get(row['car_id'],{}).get('name',row['car_id'])}\n📅 Получение: <b>{format_date_time(start_at)}</b>\n↩️ Возврат: <b>{format_date_time(row['end_at'])}</b>\n💰 {money(row['total'])}\n\nДо начала аренды менее 24 часов.")
                else: continue
                try:
                    await bot.send_message(row['user_id'],text,reply_markup=main_keyboard())
                    await asyncio.to_thread(mark_reminder_sync,row['id'],kind)
                except Exception as exc:
                    print(f"[REMINDER] booking={row['id']} ERROR={type(exc).__name__}: {exc}")
        except Exception as exc:
            print(f"[REMINDER LOOP] ERROR={type(exc).__name__}: {exc}")
        # Проверяем напоминания раз в 30 минут: меньше фоновых обращений к Neon.
        # Клиентские напоминания могут отправляться с задержкой до 30 минут.
        await asyncio.sleep(1800)


async def keepalive_loop():
    """Периодический HTTP-пинг публичного /health, чтобы free-тариф Render
    реже засыпал из-за отсутствия входящего трафика.

    Полностью надёжно сон предотвращает только внешний мониторинг
    (UptimeRobot / cron-job.org) раз в 5–10 минут.
    """
    base = (_RENDER_URL or "").strip().rstrip("/")
    if not base:
        print("[KEEPALIVE] RENDER_EXTERNAL_URL не задан — пинг отключён")
        return

    url = f"{base}/health"
    # Интервал меньше типичного idle timeout Render free (~15 мин).
    interval = int(os.getenv("KEEPALIVE_INTERVAL_SECONDS", "480"))  # 8 мин
    print(f"[KEEPALIVE] started → {url} every {interval}s")

    # Небольшая пауза после старта, чтобы сервер успел поднять порты.
    await asyncio.sleep(30)

    while True:
        try:
            timeout = ClientTimeout(total=20)
            async with ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    print(f"[KEEPALIVE] {url} → {resp.status}")
        except Exception as exc:
            print(f"[KEEPALIVE] ERROR={type(exc).__name__}: {exc}")
        await asyncio.sleep(interval)


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


# ============================================================
# ADMIN PLANNER + CLIENTS
# ============================================================

async def admin_planner(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return

    pickups = await asyncio.to_thread(get_today_pickups_sync)
    returns = await asyncio.to_thread(get_today_returns_sync)
    tomorrow = await asyncio.to_thread(get_tomorrow_pickups_sync)
    in_progress = await asyncio.to_thread(get_in_progress_rentals_sync)

    lines = ["📅 <b>Планировщик</b>\n"]

    lines.append("<b>🔑 Выдачи сегодня:</b>")
    if not pickups:
        lines.append("нет")
    else:
        for r in pickups:
            car = CARS.get(r["car_id"], {}).get("name", r["car_id"])
            lines.append(
                f"• №{r['id']} {car}\n"
                f"  {format_date_time(r['start_at'])} → {r['name']} / {r['phone']}"
            )

    lines.append("\n<b>↩️ Возвраты сегодня:</b>")
    if not returns:
        lines.append("нет")
    else:
        for r in returns:
            car = CARS.get(r["car_id"], {}).get("name", r["car_id"])
            lines.append(
                f"• №{r['id']} {car}\n"
                f"  {format_date_time(r['end_at'])} → {r['name']} / {r['phone']}"
            )

    lines.append("\n<b>🚗 Сейчас в аренде:</b>")
    if not in_progress:
        lines.append("нет")
    else:
        for r in in_progress[:15]:
            car = CARS.get(r["car_id"], {}).get("name", r["car_id"])
            lines.append(
                f"• №{r['id']} {car} до {format_date_time(r['end_at'])} — {r['name']}"
            )

    lines.append("\n<b>📌 Выдачи завтра:</b>")
    if not tomorrow:
        lines.append("нет")
    else:
        for r in tomorrow[:10]:
            car = CARS.get(r["car_id"], {}).get("name", r["car_id"])
            lines.append(
                f"• №{r['id']} {car} {format_date_time(r['start_at'])} — {r['name']}"
            )

    kb = []
    for r in pickups:
        kb.append([InlineKeyboardButton(
            text=f"✅ Выдать №{r['id']}",
            callback_data=f"issue:{r['id']}"
        )])
    for r in returns:
        kb.append([InlineKeyboardButton(
            text=f"📥 Принять №{r['id']}",
            callback_data=f"returncar:{r['id']}"
        )])
    # allow return any in-progress
    for r in in_progress:
        if r not in returns:
            kb.append([InlineKeyboardButton(
                text=f"📥 Досрочный возврат №{r['id']}",
                callback_data=f"returncar:{r['id']}"
            )])
    kb.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:planner")])
    kb.append([InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin:back")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def issue_booking(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        bid = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.answer("Некорректный номер брони.")
        return
    row = await asyncio.to_thread(mark_issued_sync, bid)
    if not row:
        await callback.message.answer("Не удалось отметить выдачу (уже выдано или статус другой).")
        return
    await callback.message.answer(f"✅ Бронь №{bid} отмечена как выданная.")
    await admin_planner(callback)


async def return_booking_cmd(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        bid = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.answer("Некорректный номер брони.")
        return
    row = await asyncio.to_thread(mark_returned_sync, bid)
    if not row:
        await callback.message.answer("Не удалось отметить возврат (сначала нужна выдача).")
        return
    await callback.message.answer(f"📥 Бронь №{bid} закрыта (возврат).")
    await admin_planner(callback)


async def admin_clients(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    recent = await asyncio.to_thread(list_recent_clients_sync, 20)
    lines = ["👥 <b>Клиенты</b>\n", "Последние обновлённые:\n"]
    kb = []
    if not recent:
        lines.append("База пока пуста — клиенты появятся после новых броней.")
    else:
        for c in recent:
            flag = "🚫" if c.get("blacklisted") else "👤"
            lines.append(f"{flag} {c['full_name']} — {c['phone']}")
            kb.append([InlineKeyboardButton(
                text=f"{flag} {c['full_name'][:28]}",
                callback_data=f"client:{c['id']}"
            )])
    kb.append([InlineKeyboardButton(text="🔍 Поиск клиента", callback_data="admin:client_search")])
    kb.append([InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin:back")])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def admin_client_search_start(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminFeature.client_search)
    await callback.message.edit_text(
        "🔍 <b>Поиск клиента</b>\n\nВведите имя, телефон или @username:",
        reply_markup=admin_back_keyboard()
    )


async def admin_client_search_message(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    q = (message.text or "").strip()
    if not q:
        return
    await state.clear()
    rows = await asyncio.to_thread(search_clients_sync, q, 30)
    if not rows:
        await message.answer(
            f"Ничего не найдено по запросу: <b>{q}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👥 К клиентам", callback_data="admin:clients")],
                [InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin:back")],
            ])
        )
        return
    kb = []
    for c in rows:
        flag = "🚫" if c.get("blacklisted") else "👤"
        kb.append([InlineKeyboardButton(
            text=f"{flag} {c['full_name'][:24]} · {c['phone']}",
            callback_data=f"client:{c['id']}"
        )])
    kb.append([InlineKeyboardButton(text="👥 К клиентам", callback_data="admin:clients")])
    await message.answer(
        f"🔍 Найдено: <b>{len(rows)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_client_card(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        cid = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return
    client = await asyncio.to_thread(get_client_sync, client_id=cid)
    if not client:
        await callback.message.answer("Клиент не найден.")
        return
    bookings = await asyncio.to_thread(client_bookings_sync, cid, 15)
    text = (
        f"👤 <b>{client['full_name']}</b>\n"
        f"📞 {client['phone']}\n"
        f"🆔 TG: <code>{client['user_id'] or '—'}</code>\n"
        f"@{client['username'] or '—'}\n"
        f"{'🚫 В чёрном списке' if client['blacklisted'] else '✅ Обычный клиент'}\n"
        f"📝 {client['notes'] or '—'}\n\n"
        f"<b>История аренд ({len(bookings)}):</b>\n"
    )
    for b in bookings[:12]:
        car = CARS.get(b["car_id"], {}).get("name", b["car_id"])
        text += f"• №{b['id']} {car} — {status_label(b['status'])}\n"
    kb = [
        [InlineKeyboardButton(
            text="🚫 В ЧС" if not client["blacklisted"] else "✅ Убрать из ЧС",
            callback_data=f"clientbl:{cid}"
        )],
        [InlineKeyboardButton(text="📝 Заметка", callback_data=f"clientnote:{cid}")],
        [InlineKeyboardButton(text="◀️ К клиентам", callback_data="admin:clients")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


async def client_blacklist_toggle(callback: CallbackQuery):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        cid = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return
    client = await asyncio.to_thread(get_client_sync, client_id=cid)
    if not client:
        return
    new_val = not bool(client.get("blacklisted"))
    await asyncio.to_thread(set_client_blacklist_sync, cid, new_val)
    # refresh card
    callback.data = f"client:{cid}"
    await show_client_card(callback)


async def client_notes_start(callback: CallbackQuery, state: FSMContext):
    await safe_callback_answer(callback)
    if callback.from_user.id != ADMIN_ID:
        return
    try:
        cid = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return
    await state.set_state(AdminFeature.client_notes)
    await state.update_data(edit_client_id=cid)
    await callback.message.answer("Введите заметку по клиенту (или «-» чтобы очистить):")


async def client_notes_message(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    cid = data.get("edit_client_id")
    await state.clear()
    if not cid:
        return
    notes = (message.text or "").strip()
    if notes == "-":
        notes = ""
    await asyncio.to_thread(set_client_notes_sync, cid, notes)
    await message.answer("✅ Заметка сохранена.")
    # fake callback for card refresh is hard; just confirm




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

    global MINI_BOT

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )
    MINI_BOT = bot

    # Постоянная кнопка BALTICAR в меню Telegram.
    # Клиент сможет открыть Mini App повторно даже после закрытия старого окна.
    await configure_client_menu(bot)

    dp = Dispatcher()

    spawn_task(reminder_loop(bot))
    spawn_task(keepalive_loop())

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

    # month: и backstart: обрабатываются одним хендлером
    # (формат callback_data одинаковый: <prefix>:<car_id>:<YYYY-MM-DD>).
    dp.callback_query.register(
        month,
        F.data.startswith(("month:", "backstart:"))
    )

    dp.callback_query.register(
        start_day,
        F.data.startswith("day:")
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
        admin_calendar_cars,
        F.data == "admin:calendar_cars"
    )
    dp.callback_query.register(
        admin_all_month,
        F.data.startswith("adminallmonth:")
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
        delete_rejected_booking,
        F.data.startswith("deleterej:")
    )

    dp.callback_query.register(
        admin_action,
        F.data.startswith("cancel:")
    )
    dp.callback_query.register(admin_planner, F.data == "admin:planner")
    dp.callback_query.register(issue_booking, F.data.startswith("issue:"))
    dp.callback_query.register(return_booking_cmd, F.data.startswith("returncar:"))
    dp.callback_query.register(admin_clients, F.data == "admin:clients")
    dp.callback_query.register(admin_client_search_start, F.data == "admin:client_search")
    dp.callback_query.register(show_client_card, F.data.startswith("client:"))
    dp.callback_query.register(client_blacklist_toggle, F.data.startswith("clientbl:"))
    dp.callback_query.register(client_notes_start, F.data.startswith("clientnote:"))
    dp.callback_query.register(admin_reviews, F.data == "admin:reviews")
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
    dp.message.register(admin_client_search_message, AdminFeature.client_search)
    dp.message.register(client_notes_message, AdminFeature.client_notes)
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

        # Сравниваем с тем секретом, который реально передан в set_webhook
        # (если WEBHOOK_SECRET не задан, это случайный секрет на время работы).
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )

        if not hmac.compare_digest(
            received_secret.encode("utf-8"),
            secret.encode("utf-8")
        ):

            raise web.HTTPForbidden(
                text="Invalid webhook secret"
            )

        data = await request.json()

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

        spawn_task(
            process_update()
        )

        return web.Response(
            text="OK"
        )

    app = web.Application()

    await mini_app_routes(app)

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
# TELEGRAM MINI APP / WEB APP
# ============================================================

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"
MINI_BOT = None
PHOTOS_DIR = Path(__file__).resolve().parent / "photos"
DEPOSIT_AMOUNT = 10000
LOCATION_TYPES = {
    "airport": ("Аэропорт Храброво", 0),
    "station": ("Южный вокзал", 0),
    "city": ("Калининград", None),
    "region": ("Калининградская область", None),
    "other": ("Другое место", None),
}

PAYMENT_METHODS = {
    "cash": "💵 Наличные",
    "terminal": "💳 Терминал / карта",
    "bank": "🏦 Безналичный перевод",
}

EXTRA_SERVICES = {
    "child_seat": ("👶 Детское кресло", None),
    "second_driver": ("👤 Второй водитель", None),
    "full_tank": ("⛽ Полный бак при выдаче", None),
}


def mini_validate_init_data(init_data: str):
    """Validate Telegram WebApp initData and return user id when available."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        if not received_hash:
            return None
        # Свежесть подписи: старые initData (утёкшие из логов/истории) не принимаем.
        auth_date = int(pairs.get("auth_date", "0") or 0)
        if auth_date <= 0 or abs(monotonic_time.time() - auth_date) > INIT_DATA_MAX_AGE:
            return None
        data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        user_raw = pairs.get("user", "")
        user = json.loads(user_raw) if user_raw else {}
        uid = user.get("id")
        return int(uid) if uid is not None else None
    except Exception:
        return None


def mini_user_id(request):
    # Header first; query fallback for iOS/WebView cases that drop custom headers.
    init_data = (
        request.headers.get("X-Telegram-Init-Data", "")
        or request.query.get("init_data", "")
    )
    return mini_validate_init_data(init_data)


def mini_json(data, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


def mini_car_payload():
    # Настройки уже загружены при старте и обновляются после действий администратора.
    # Не открываем новое соединение с Neon на каждый запрос каталога Mini App.
    base = Path(__file__).resolve().parent
    # Какие авто сейчас в аренде (подтверждённая бронь пересекает "сейчас")
    now = datetime.now(TZ)
    busy_ids = set()
    try:
        con = db()
        try:
            with con.cursor() as cur:
                rows = cur.execute(
                    """
                    SELECT DISTINCT car_id FROM bookings
                    WHERE status = 'confirmed'
                      AND start_at <= %s
                      AND end_at > %s
                    """,
                    (now, now),
                ).fetchall()
                busy_ids = {r["car_id"] for r in rows}
        finally:
            con.close()
    except Exception as exc:
        print(f"[MINI/CARS] busy check failed: {exc}", flush=True)

    result=[]
    for cid, c in active_cars().items():
        photos=[]
        for p in c.get("photos", []):
            rel = str(p).replace("\\", "/")
            full = base / rel
            if full.is_file():
                photos.append(rel)
            elif (PHOTOS_DIR / Path(rel).name).is_file():
                photos.append(f"photos/{Path(rel).name}")
        busy = cid in busy_ids
        result.append({
            "id": cid,
            "name": c.get("name", cid),
            "gear": c.get("gear", "АКПП"),
            "fuel": c.get("fuel", "Бензин"),
            "seats": int(c.get("seats", 5)),
            "description": c.get("description", ""),
            "rates": list(c.get("rates", (0,0,0))),
            "photos": photos,
            "color": "Белый",
            "busy_now": busy,
            "available_now": not busy,
            "status_label": "ЗАНЯТА СЕЙЧАС" if busy else "ДОСТУПНО ДЛЯ БРОНИРОВАНИЯ",
        })
    return result


def mini_parse_local_iso(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def mini_slots_sync(car_id, year, month, start_at=None):
    if car_id not in active_cars():
        return {"mode":"start", "slots":{}}
    slots={}
    days=calendar.monthrange(year, month)[1]
    now=datetime.now(TZ)
    start_dt=mini_parse_local_iso(start_at) if start_at else None
    for day_n in range(1, days+1):
        d=date(year,month,day_n)
        arr=[]
        for hour in range(PICKUP_START_HOUR, PICKUP_END_HOUR+1):
            candidate=local_dt(d,time(hour,0))
            if start_dt is None:
                if candidate <= now:
                    continue
                if candidate < now + timedelta(hours=0):
                    continue
                if booking_overlaps(car_id,candidate,candidate+timedelta(hours=1)):
                    continue
            else:
                if candidate <= start_dt:
                    continue
                if booking_overlaps(car_id,start_dt,candidate):
                    continue
            arr.append(hour)
        slots[d.isoformat()]=arr
    return {"mode":"end" if start_dt else "start", "slots":slots}


def mini_create_booking_sync(user_id, cid, start_at, end_at, name, phone, comment, location_type, location_note, payment_method="cash", extras=None, terms_accepted=False):
    start_at=mini_parse_local_iso(start_at)
    end_at=mini_parse_local_iso(end_at)
    name = (name or "").strip()
    phone = (phone or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not name or len(digits) < 10:
        return {"ok":False,"reason":"contact"}
    if cid not in active_cars():
        return {"ok":False,"reason":"car"}
    if end_at <= start_at:
        return {"ok":False,"reason":"period"}
    now=datetime.now(TZ)
    if start_at <= now:
        return {"ok":False,"reason":"past"}
    if start_at.time() < time(PICKUP_START_HOUR,0) or start_at.time() > time(PICKUP_END_HOUR,0):
        return {"ok":False,"reason":"time"}
    if end_at.time() < time(PICKUP_START_HOUR,0) or end_at.time() > time(PICKUP_END_HOUR,0):
        return {"ok":False,"reason":"time"}
    if location_type not in LOCATION_TYPES:
        return {"ok":False,"reason":"location"}
    if location_type == "other" and not (location_note or "").strip():
        return {"ok":False,"reason":"location_note"}
    payment_method = (payment_method or "cash").strip().lower()
    if payment_method not in PAYMENT_METHODS:
        return {"ok":False,"reason":"payment"}
    if not terms_accepted:
        return {"ok":False,"reason":"terms"}
    extras_list = []
    if extras:
        if isinstance(extras, str):
            try:
                extras = json.loads(extras)
            except Exception:
                extras = []
        for key in extras:
            key = str(key).strip()
            if key in EXTRA_SERVICES:
                extras_list.append(key)
    extras_json = json.dumps(extras_list, ensure_ascii=False)
    days=rental_days(start_at,end_at)
    daily=rate_for_days(cid,days)
    rental_total=days*daily
    location_fee=0 if location_type in ("airport","station") else None
    con=db()
    try:
        with con.cursor() as cur:
            lock_bookings(cur)
            expire_pending(cur)
            maintenance=find_maintenance(cur,cid,start_at,end_at)
            if maintenance:
                con.rollback(); return {"ok":False,"reason":"maintenance"}
            overlap=find_overlap(cur,cid,start_at,end_at)
            if overlap:
                con.rollback(); return {"ok":False,"reason":"overlap"}
            expires=datetime.now(TZ)+timedelta(minutes=HOLD_MINUTES)
            # CRM upsert for mini-app bookings
            client_row = None
            if user_id:
                client_row = cur.execute("SELECT * FROM clients WHERE user_id=%s", (user_id,)).fetchone()
            if not client_row and phone:
                client_row = cur.execute("SELECT * FROM clients WHERE phone=%s ORDER BY id DESC LIMIT 1", (phone,)).fetchone()
            if client_row:
                if client_row.get("blacklisted"):
                    con.rollback(); return {"ok": False, "reason": "blacklisted"}
                cur.execute("UPDATE clients SET full_name=%s, phone=%s, updated_at=NOW() WHERE id=%s", (name, phone, client_row["id"]))
                client_id = client_row["id"]
            else:
                client_row = cur.execute(
                    "INSERT INTO clients (user_id, full_name, phone, username) VALUES (%s,%s,%s,%s) RETURNING id",
                    (user_id, name, phone, "")
                ).fetchone()
                client_id = client_row["id"]
            cur.execute("""INSERT INTO bookings (user_id,username,car_id,start_date,end_date,start_at,end_at,name,phone,comment,total,status,created_at,expires_at,pickup_location,pickup_location_type,pickup_location_note,pickup_location_fee,payment_method,extras,terms_accepted,client_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",(
                user_id,"",cid,start_at.date(),end_at.date(),start_at,end_at,name,phone,comment,rental_total,datetime.now(TZ),expires,
                LOCATION_TYPES[location_type][0],location_type,location_note or "",location_fee,payment_method,extras_json,bool(terms_accepted),client_id))
            bid=cur.fetchone()["id"]
        con.commit()
        extras_labels = [EXTRA_SERVICES[k][0] for k in extras_list]
        return {"ok":True,"bid":bid,"days":days,"total":rental_total,"rental_total":rental_total,"location_fee":location_fee,"location":LOCATION_TYPES[location_type][0],"payment_method":payment_method,"payment_label":PAYMENT_METHODS[payment_method],"extras":extras_list,"extras_labels":extras_labels,"expires":expires.isoformat()}
    except Exception:
        con.rollback(); raise
    finally:
        con.close()


def mini_add_location_columns():
    con=db()
    try:
        with con.cursor() as cur:
            for sql in [
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS pickup_location TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS pickup_location_type TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS pickup_location_note TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS pickup_location_fee INTEGER",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_method TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS extras TEXT",
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS terms_accepted BOOLEAN DEFAULT FALSE",
            ]:
                cur.execute(sql)
        con.commit()
    finally:
        con.close()


def mini_auth_or_401(request):
    uid=mini_user_id(request)
    if uid is None:
        raise web.HTTPUnauthorized(text="Telegram authorization required")
    return uid

async def mini_app(request):
    if not WEBAPP_DIR.exists():
        raise web.HTTPNotFound(text="Mini App not found")
    return web.FileResponse(
        WEBAPP_DIR / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

async def mini_photo(request):
    name=request.match_info.get("name","")
    p=(PHOTOS_DIR / name).resolve()
    if PHOTOS_DIR.resolve() not in p.parents or not p.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(p)

async def mini_cars(request):
    cars=await asyncio.to_thread(mini_car_payload)
    return mini_json({"cars":cars})


def mini_calendar_payload_sync(car_id, year, month):
    """Загружает занятость месяца двумя запросами: брони + ТО."""
    if car_id not in active_cars():
        return {"bookings": [], "maintenance": []}

    bookings = get_month_bookings(car_id, year, month)
    maintenance = get_month_maintenance(car_id, year, month)

    def serialize(row, include_reason=False):
        item = {
            "id": row.get("id"),
            "status": row.get("status", "confirmed"),
            "start_at": ensure_tz(row["start_at"]).isoformat(),
            "end_at": ensure_tz(row["end_at"]).isoformat(),
        }
        if include_reason:
            item["reason"] = row.get("reason") or ""
        return item

    return {
        "bookings": [serialize(r) for r in bookings],
        "maintenance": [serialize(r, True) for r in maintenance],
    }


async def mini_availability(request):
    try:
        cid = request.query.get("car_id", "").strip()
        year = int(request.query.get("year"))
        month = int(request.query.get("month"))
        if month < 1 or month > 12:
            raise ValueError("invalid month")
        data = await asyncio.to_thread(mini_calendar_payload_sync, cid, year, month)
        return mini_json(data)
    except Exception as exc:
        print(f"[MINI/AVAILABILITY] {type(exc).__name__}: {exc}")
        return mini_json({"message": "Не удалось загрузить календарь. Попробуйте ещё раз."}, 500)

async def mini_slots(request):
    try:
        cid=request.query.get("car_id","")
        year=int(request.query.get("year")); month=int(request.query.get("month"))
        start_at=request.query.get("start_at")
        return mini_json(await asyncio.to_thread(mini_slots_sync,cid,year,month,start_at))
    except Exception as exc:
        print(f"[MINI/SLOTS] {type(exc).__name__}: {exc}")
        return mini_json({"message":"Не удалось загрузить доступное время."},500)

def mini_mybookings_sync(uid):
    con=db()
    try:
        with con.cursor() as cur:
            rows=cur.execute("""SELECT b.*, COALESCE(r.id,0) AS review_id FROM bookings b LEFT JOIN reviews r ON r.booking_id=b.id WHERE b.user_id=%s ORDER BY b.id DESC LIMIT 20""",(uid,)).fetchall()
    finally:
        con.close()
    out=[]
    now=datetime.now(TZ)
    for r in rows:
        sa=ensure_tz(r.get("start_at")) if r.get("start_at") else local_dt(r["start_date"],time(10,0))
        ea=ensure_tz(r.get("end_at")) if r.get("end_at") else local_dt(r["end_date"],time(17,0))
        pm=r.get("payment_method") or ""
        raw_ex=r.get("extras") or "[]"
        try:
            ex_list=json.loads(raw_ex) if isinstance(raw_ex,str) else (raw_ex or [])
        except Exception:
            ex_list=[]
        if not isinstance(ex_list, list):
            ex_list=[]
        ex_labels=[EXTRA_SERVICES[k][0] for k in ex_list if k in EXTRA_SERVICES]
        out.append({"id":r["id"],"status":r["status"],"car_name":CARS.get(r["car_id"],{}).get("name",r["car_id"]),"car_id":r["car_id"],"start_at":sa.strftime("%d.%m.%Y · %H:%M"),"end_at":ea.strftime("%d.%m.%Y · %H:%M"),"total":r["total"],"past":ea < now,"reviewed":bool(r.get("review_id")),"comment":r.get("comment") or "","pickup_location":r.get("pickup_location") or "","pickup_location_type":r.get("pickup_location_type") or "","pickup_location_note":r.get("pickup_location_note") or "","pickup_location_fee":r.get("pickup_location_fee"),"payment_method":pm,"payment_label":PAYMENT_METHODS.get(pm, pm),"extras":ex_list,"extras_labels":ex_labels})
    return out

async def mini_mybookings(request):
    uid=mini_auth_or_401(request)
    out=await asyncio.to_thread(mini_mybookings_sync,uid)
    return mini_json({"bookings":out})

async def mini_bookings(request):
    print(f"[MINI/BOOKING] REQUEST /api/bookings init_data={'yes' if request.headers.get('X-Telegram-Init-Data') else 'NO'}", flush=True)
    try:
        uid=mini_auth_or_401(request)
    except Exception as exc:
        print(f"[MINI/BOOKING] AUTH FAILED: {type(exc).__name__}: {exc!r}", flush=True)
        raise
    try:
        data=await request.json()
        name=str(data.get("name","")).strip(); phone=str(data.get("phone","")).strip()
        cid=str(data.get("car_id","")).strip(); comment=str(data.get("comment","")).strip()
        location_type=str(data.get("pickup_location_type",data.get("location_type","airport"))).strip()
        location_note=str(data.get("pickup_location_note",data.get("location_note",""))).strip()
        payment_method=str(data.get("payment_method","cash")).strip().lower()
        extras=data.get("extras") or []
        terms_accepted=bool(data.get("terms_accepted"))
        result=await asyncio.to_thread(mini_create_booking_sync,uid,cid,str(data.get("start_at")),str(data.get("end_at")),name,phone,comment,location_type,location_note,payment_method,extras,terms_accepted)
        if not result["ok"]:
            print(f"[MINI/BOOKING] REJECTED reason={result.get('reason')}", flush=True)
            messages={"car":"Автомобиль недоступен.","period":"Проверьте даты и время.","past":"Выбранное время уже прошло.","time":"Выдача и возврат доступны с 08:00 до 20:00.","location":"Выберите место подачи.","location_note":"Укажите адрес или комментарий для другого места.","maintenance":"Автомобиль недоступен в выбранный период.","overlap":"Эти даты или время уже заняты. Выберите другой вариант.","contact":"Введите имя и корректный телефон (не менее 10 цифр).","payment":"Выберите способ оплаты.","terms":"Подтвердите согласие с условиями аренды."}
            return mini_json({"message":messages.get(result["reason"],"Не удалось создать заявку.")},409)
        print(f"[MINI/BOOKING] CREATED bid={result['bid']} car={cid} uid={uid}", flush=True)
        # V15: уведомление напрямую через Telegram Bot API.
        # Так мы исключаем проблемы с отдельной Bot-сессией и сразу видим
        # HTTP-ответ Telegram в Render Logs.
        try:
            print(f"[MINI/ADMIN_NOTIFY] START bid={result['bid']} admin={ADMIN_ID} token={'set' if BOT_TOKEN else 'EMPTY'}", flush=True)
            if not ADMIN_ID:
                raise RuntimeError("ADMIN_ID is empty or 0")
            if not BOT_TOKEN:
                raise RuntimeError("BOT_TOKEN is empty")

            loc=result["location"]
            loc_line=f"📍 Место подачи: <b>{escape_html(loc)}</b>"
            if location_note:
                loc_line += f"\n📝 {escape_html(location_note)}"
            pay_label = result.get("payment_label") or PAYMENT_METHODS.get(result.get("payment_method"), "—")
            extras_line = ""
            if result.get("extras_labels"):
                extras_line = "\n✨ " + ", ".join(escape_html(x) for x in result["extras_labels"])
            admin_text=(
                f"📥 <b>Новая заявка из Mini App №{result['bid']}</b>\n\n"
                f"🚗 <b>{escape_html(CARS[cid]['name'])}</b>\n"
                f"👤 {escape_html(name)}\n"
                f"📞 {escape_html(phone)}\n"
                f"📅 {mini_parse_local_iso(str(data.get('start_at'))).strftime('%d.%m.%Y %H:%M')} → "
                f"{mini_parse_local_iso(str(data.get('end_at'))).strftime('%d.%m.%Y %H:%M')}\n"
                f"{loc_line}\n"
                f"💳 Оплата: <b>{escape_html(pay_label)}</b>"
                f"{extras_line}\n"
                f"💰 Аренда: <b>{money(result['rental_total'])}</b>\n"
                f"🔐 Залог: <b>{money(DEPOSIT_AMOUNT)}</b>"
            )
            # Не сериализуем aiogram InlineKeyboardMarkup через model_dump():
            # в текущей версии aiogram это может добавить пустое поле
            # icon_custom_emoji_id, которое Telegram API принимает только числом.
            # Передаём минимальный чистый JSON keyboard вручную.
            payload={
                "chat_id": ADMIN_ID,
                "text": admin_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[
                        {
                            "text": "✅ Подтвердить",
                            "callback_data": f"confirm:{result['bid']}"
                        },
                        {
                            "text": "❌ Отклонить",
                            "callback_data": f"reject:{result['bid']}"
                        }
                    ]]
                },
            }
            async with ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json=payload,
                    timeout=ClientTimeout(total=15),
                ) as resp:
                    body=await resp.text()
                    print(f"[MINI/ADMIN_NOTIFY] TELEGRAM_HTTP status={resp.status} body={body[:1500]}", flush=True)
                    if resp.status != 200:
                        raise RuntimeError(f"Telegram sendMessage HTTP {resp.status}: {body[:500]}")
            print(f"[MINI/ADMIN_NOTIFY] SENT bid={result['bid']} admin={ADMIN_ID}", flush=True)
        except Exception as exc:
            print(f"[MINI/ADMIN_NOTIFY] FAILED bid={result.get('bid')}: {type(exc).__name__}: {exc!r}", flush=True)
        return mini_json({"id":result["bid"],"days":result["days"],"total":result["total"],"location_fee":result["location_fee"],"location":result["location"],"payment_method":result.get("payment_method"),"payment_label":result.get("payment_label"),"extras":result.get("extras"),"extras_labels":result.get("extras_labels"),"expires":result["expires"]})
    except Exception as exc:
        print(f"[MINI/BOOKING] {type(exc).__name__}: {exc}", flush=True)
        return mini_json({"message":"Не удалось отправить заявку. Попробуйте ещё раз."},500)

async def mini_cancel_booking(request):
    uid=mini_auth_or_401(request)
    try:
        data=await request.json()
        bid=int(data.get("id",0))
    except (ValueError,TypeError,AttributeError):
        return mini_json({"message":"Некорректный запрос."},400)
    state,_=await asyncio.to_thread(cancel_booking_by_user_sync,uid,bid)
    if state=="not_found":
        return mini_json({"message":"Бронирование не найдено."},404)
    if state=="closed":
        return mini_json({"message":"Это бронирование уже обработано."},409)
    return mini_json({"ok":True})

def mini_reviews_get_sync():
    con=db()
    try:
        with con.cursor() as cur:
            rows=cur.execute("""
                SELECT r.id, r.booking_id, r.rating, r.review_text AS text, r.created_at,
                       b.name, r.car_id
                FROM reviews r
                LEFT JOIN bookings b ON b.id = r.booking_id
                ORDER BY r.created_at DESC
                LIMIT 30
            """).fetchall()
        return [{
            "id": r["id"],
            "booking_id": r["booking_id"],
            "rating": r["rating"],
            "text": r.get("text") or "",
            "name": r.get("name") or "Клиент",
            "car_name": CARS.get(r["car_id"], {}).get("name", r["car_id"] or "Автомобиль"),
            "created_at": ensure_tz(r["created_at"]).strftime("%d.%m.%Y") if r.get("created_at") else "",
        } for r in rows]
    finally:
        con.close()

async def mini_reviews_get(request):
    reviews=await asyncio.to_thread(mini_reviews_get_sync)
    return mini_json({"reviews":reviews})

async def mini_reviews_post(request):
    uid=mini_auth_or_401(request)
    try:
        data=await request.json()
        bid=int(data.get("booking_id",0))
        rating=max(1,min(5,int(data.get("rating",5))))
        text=str(data.get("text","")).strip()[:1000]
    except (ValueError,TypeError,AttributeError):
        return mini_json({"message":"Некорректный запрос."},400)
    state=await asyncio.to_thread(create_review_for_user_sync,bid,uid,rating,text)
    if state=="not_found":
        return mini_json({"message":"Отзыв доступен после подтверждённой аренды."},409)
    if state=="not_finished":
        return mini_json({"message":"Оставить отзыв можно после завершения аренды."},409)
    return mini_json({"ok":True})


def admin_web_auth_or_403(request):
    # Telegram Web Apps normally send initData in a custom header.
    # Some Telegram/iOS WebView paths can omit custom headers on fetch(),
    # so accept the same signed initData from the query string as a fallback.
    init_data = request.headers.get("X-Telegram-Init-Data", "") or request.query.get("init_data", "")
    uid = mini_validate_init_data(init_data)
    if not is_admin(uid):
        raise web.HTTPForbidden(text="Admin authorization required")
    return uid

async def admin_web_page(request):
    # Сам HTML можно открыть только как Telegram Web App: API дополнительно
    # проверяет initData и ADMIN_ID. Это не создаёт публичную админку без авторизации.
    p = WEBAPP_DIR / "admin.html"
    if not p.is_file():
        raise web.HTTPNotFound(text="Admin app not found")
    return web.FileResponse(p, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"})


def admin_calendar_payload_sync(year, month):
    """Данные для веб-календаря автопарка.

    Используем только поля, которые гарантированно есть в рабочей схеме,
    а дополнительные данные (имя/телефон/место) подхватываем безопасно.
    Это не должно ломать календарь из-за одной необязательной колонки.
    """
    first = date(year, month, 1)
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    month_start = local_dt(first, time(0, 0))
    month_end = local_dt(next_first, time(0, 0))
    con = db()
    try:
        with con.cursor() as cur:
            # Базовые поля — те же, что используются существующим календарём.
            booking_rows = cur.execute("""
                SELECT id, car_id, status, start_at, end_at
                FROM bookings
                WHERE status IN ('pending','confirmed','completed')
                  AND start_at < %s AND end_at > %s
                ORDER BY car_id, start_at, id
            """, (month_end, month_start)).fetchall()

            # Дополнительные данные нужны только для карточки при нажатии.
            extra = {}
            if booking_rows:
                ids = [r['id'] for r in booking_rows]
                try:
                    rows = cur.execute("""
                        SELECT id, name, phone, total, pickup_location, payment_method
                        FROM bookings WHERE id = ANY(%s)
                    """, (ids,)).fetchall()
                    extra = {r['id']: r for r in rows}
                except Exception as exc:
                    print(f"[ADMIN/WEB/CALENDAR] optional booking fields skipped: {type(exc).__name__}: {exc}", flush=True)
                    con.rollback()

            try:
                maintenance_rows = cur.execute("""
                    SELECT id, car_id, start_at, end_at, reason
                    FROM car_maintenance
                    WHERE start_at < %s AND end_at > %s
                    ORDER BY car_id, start_at, id
                """, (month_end, month_start)).fetchall()
            except Exception as exc:
                print(f"[ADMIN/WEB/CALENDAR] maintenance skipped: {type(exc).__name__}: {exc}", flush=True)
                con.rollback()
                maintenance_rows = []
    finally:
        con.close()

    days = (next_first - first).days
    by_car = {cid: [] for cid in active_cars()}

    def block(row, maintenance=False):
        sa = ensure_tz(row['start_at'])
        ea = ensure_tz(row['end_at'])
        start_day = 1 if sa.date() < first else sa.day
        end_day = days if ea.date() > next_first else (ea.date() - timedelta(microseconds=1)).day
        end_day = max(start_day, min(days, end_day))
        if maintenance:
            return {
                'id': row['id'], 'status': 'maintenance', 'name': row.get('reason') or 'ТО',
                'car_id': row.get('car_id') or '',
                'car_name': CARS.get(row.get('car_id'), {}).get('name', row.get('car_id') or ''),
                'phone': '', 'total': None, 'pickup': '',
                'start_label': sa.strftime('%d.%m.%Y %H:%M'),
                'end_label': ea.strftime('%d.%m.%Y %H:%M'),
                'start_day': max(1, start_day), 'end_day': end_day,
            }
        ex = extra.get(row['id'], {})
        pm = ex.get('payment_method') or ''
        return {
            'id': row['id'], 'status': row['status'], 'name': ex.get('name') or f"Бронь №{row['id']}",
            'phone': ex.get('phone') or '', 'total': ex.get('total'), 'pickup': ex.get('pickup_location') or '',
            'payment_method': pm, 'payment_label': PAYMENT_METHODS.get(pm, pm),
            'start_label': sa.strftime('%d.%m.%Y %H:%M'),
            'end_label': ea.strftime('%d.%m.%Y %H:%M'),
            'start_day': max(1, start_day), 'end_day': end_day,
        }

    all_blocks = []
    for r in booking_rows:
        if r['car_id'] in by_car:
            b = block(r)
            by_car[r['car_id']].append(b)
            all_blocks.append((r['car_id'], r['start_at'], r['end_at'], b))
    for r in maintenance_rows:
        if r['car_id'] in by_car:
            b = block(r, True)
            by_car[r['car_id']].append(b)
            all_blocks.append((r['car_id'], r['start_at'], r['end_at'], b))

    cars_out = []
    for cid, car in active_cars().items():
        cars_out.append({
            'id': cid, 'name': car.get('name', cid), 'gear': car.get('gear',''),
            'blocks': sorted(by_car.get(cid, []), key=lambda x: (x['start_day'], x['end_day']))
        })

    overlap = []
    for n in range(1, days + 1):
        day_start = local_dt(date(year, month, n), time(0, 0))
        day_end = day_start + timedelta(days=1)
        occupied = 0
        for cid in by_car:
            if any(ensure_tz(sa) < day_end and ensure_tz(ea) > day_start
                   for ccid, sa, ea, b in all_blocks if ccid == cid and b['status'] != 'maintenance'):
                occupied += 1
        if occupied >= 2:
            overlap.append({'date': date(year, month, n).strftime('%d.%m'), 'count': occupied})

    return {
        'year': year, 'month': month, 'days': days,
        'cars': cars_out,
        'bookings': [b for cid, sa, ea, b in all_blocks],
        'overlaps': overlap,
    }


async def admin_calendar_api(request):
    admin_web_auth_or_403(request)
    try:
        year = int(request.query.get('year'))
        month = int(request.query.get('month'))
        if not 1 <= month <= 12:
            raise ValueError
        return mini_json(await asyncio.to_thread(admin_calendar_payload_sync, year, month))
    except Exception as exc:
        print(f"[ADMIN/WEB/CALENDAR] {type(exc).__name__}: {exc!r}", flush=True)
        return mini_json({'message':f'Не удалось загрузить календарь: {type(exc).__name__}'}, 500)


def _parse_extras_labels(raw):
    try:
        if isinstance(raw, str):
            raw = json.loads(raw or "[]")
        if not isinstance(raw, list):
            return []
        return [EXTRA_SERVICES[k][0] for k in raw if k in EXTRA_SERVICES]
    except Exception:
        return []


def admin_bookings_sync():
    con = db()
    try:
        with con.cursor() as cur:
            rows = cur.execute("""
                SELECT id, car_id, status, name, phone, start_at, end_at, total, created_at,
                       pickup_location, pickup_location_type, pickup_location_note, pickup_location_fee,
                       payment_method, extras
                FROM bookings ORDER BY id DESC LIMIT 200
            """).fetchall()
    finally:
        con.close()
    out=[]
    now = datetime.now(TZ)
    for r in rows:
        sa=ensure_tz(r['start_at']) if r.get('start_at') else None
        ea=ensure_tz(r['end_at']) if r.get('end_at') else None
        # UI "completed": confirmed rental that already ended
        status = r['status']
        if status == 'confirmed' and ea is not None and ea < now:
            status = 'completed'
        out.append({'id':r['id'],'car_id':r['car_id'],'car_name':CARS.get(r['car_id'],{}).get('name',r['car_id']),
                    'status':status,'status_label':status_label(status) if status != 'completed' else '🔵 Завершена',
                    'name':r.get('name') or '','phone':r.get('phone') or '',
                    'start_label':sa.strftime('%d.%m.%Y %H:%M') if sa else '',
                    'end_label':ea.strftime('%d.%m.%Y %H:%M') if ea else '',
                    'start_ms':int(sa.timestamp()*1000) if sa else 0,
                    'end_ms':int(ea.timestamp()*1000) if ea else 0,
                    'pickup':r.get('pickup_location') or '',
                    'payment_method':r.get('payment_method') or '',
                    'payment_label':PAYMENT_METHODS.get(r.get('payment_method') or '', r.get('payment_method') or ''),
                    'extras_labels':_parse_extras_labels(r.get('extras')),
                    'pickup_location_fee': r.get('pickup_location_fee'),
                    'total':r.get('total') or 0})
    return out

async def admin_bookings_api(request):
    admin_web_auth_or_403(request)
    out=await asyncio.to_thread(admin_bookings_sync)
    return mini_json({'bookings':out,'fleet_count':len(active_cars())})


def _finance_date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except Exception:
        return default


def admin_finance_sync(start_day, end_day):
    """Detailed revenue report for the web admin.
    Revenue is counted from confirmed bookings whose rental starts inside the selected period.
    Operational occupancy uses confirmed bookings overlapping the period, clipped to its bounds.
    """
    start_dt = local_dt(start_day, time(0, 0))
    end_dt = local_dt(end_day + timedelta(days=1), time(0, 0))
    now = datetime.now(TZ)
    con = db()
    try:
        with con.cursor() as cur:
            rows = cur.execute("""
                SELECT id, car_id, status, name, start_at, end_at, total,
                       created_at, pickup_location, pickup_location_type,
                       pickup_location_fee
                FROM bookings
                WHERE end_at > %s AND start_at < %s
                ORDER BY start_at ASC, id ASC
            """, (start_dt, end_dt)).fetchall()

            future = cur.execute("""
                SELECT COALESCE(SUM(total),0) AS revenue, COUNT(*) AS cnt
                FROM bookings
                WHERE status='confirmed' AND start_at >= %s
            """, (now,)).fetchone()

            expense_rows = cur.execute(
                """
                SELECT category, car_id, amount
                FROM expenses
                WHERE expense_date BETWEEN %s AND %s
                """, (start_day, end_day)
            ).fetchall()

        # Выручка и загрузка: confirmed + completed (после конца аренды статус становится completed)
        revenue_statuses = ('confirmed', 'completed')
        confirmed = [r for r in rows if r.get('status') in revenue_statuses]
        pending = [r for r in rows if r.get('status') == 'pending']
        rejected = [r for r in rows if r.get('status') in ('rejected', 'cancelled')]

        # Revenue for the selected period: confirmed/completed rentals beginning in the period.
        period_confirmed = [r for r in confirmed if r.get('start_at') and start_dt <= ensure_tz(r['start_at']) < end_dt]
        revenue = sum(int(r.get('total') or 0) for r in period_confirmed)
        booking_count = len(period_confirmed)
        rental_days = sum(max(0, (ensure_tz(r['end_at']) - ensure_tz(r['start_at'])).total_seconds()/86400) for r in period_confirmed if r.get('start_at') and r.get('end_at'))
        avg_check = round(revenue / booking_count) if booking_count else 0
        avg_day = round(revenue / rental_days) if rental_days else 0

        # Occupancy: union overlapping confirmed intervals per car, clipped to the period.
        intervals = {}
        for r in confirmed:
            if not r.get('start_at') or not r.get('end_at'): continue
            a=max(start_dt, ensure_tz(r['start_at'])); b=min(end_dt, ensure_tz(r['end_at']))
            if a < b:
                intervals.setdefault(r['car_id'], []).append((a,b))
        occupied_days_by_car={}
        occupied_total=0.0
        for cid, arr in intervals.items():
            arr.sort(); merged=[]
            for a,b in arr:
                if not merged or a > merged[-1][1]: merged.append([a,b])
                elif b > merged[-1][1]: merged[-1][1]=b
            val=sum((b-a).total_seconds()/86400 for a,b in merged)
            occupied_days_by_car[cid]=val; occupied_total += val
        fleet_count=len(active_cars())
        period_days=max(1,(end_dt-start_dt).total_seconds()/86400)
        occupancy=round((occupied_total/(fleet_count*period_days))*100,1) if fleet_count else 0

        cars=[]
        for cid, car in CARS.items():
            cr=[r for r in period_confirmed if r.get('car_id')==cid]
            rev=sum(int(r.get('total') or 0) for r in cr)
            days=sum(max(0,(ensure_tz(r['end_at'])-ensure_tz(r['start_at'])).total_seconds()/86400) for r in cr if r.get('start_at') and r.get('end_at'))
            cars.append({
                'car_id':cid,'car_name':car.get('name',cid),'bookings':len(cr),'revenue':rev,
                'rental_days':round(days,1),'avg_check':round(rev/len(cr)) if cr else 0,
                'occupancy':round((occupied_days_by_car.get(cid,0)/period_days)*100,1) if fleet_count else 0
            })
        cars.sort(key=lambda x:(-x['revenue'], x['car_name']))

        # Daily revenue and booking counts for the selected period.
        daily=[]; cur_day=start_day
        while cur_day <= end_day:
            ds=local_dt(cur_day,time(0,0)); de=ds+timedelta(days=1)
            dr=[r for r in period_confirmed if r.get('start_at') and ds <= ensure_tz(r['start_at']) < de]
            daily.append({'date':cur_day.isoformat(),'label':cur_day.strftime('%d.%m'),'revenue':sum(int(r.get('total') or 0) for r in dr),'bookings':len(dr)})
            cur_day += timedelta(days=1)

        expense_total = sum(int(r.get('amount') or 0) for r in expense_rows)
        expense_by_category = {}
        expense_by_car = {}
        for r in expense_rows:
            cat = r.get('category') or 'other'
            expense_by_category[cat] = expense_by_category.get(cat, 0) + int(r.get('amount') or 0)
            cid = r.get('car_id')
            if cid:
                expense_by_car[cid] = expense_by_car.get(cid, 0) + int(r.get('amount') or 0)

        locations={}
        for r in period_confirmed:
            key=r.get('pickup_location_type') or 'unknown'
            label=r.get('pickup_location') or {'airport':'Аэропорт Храброво','station':'Южный вокзал','city':'Калининград','region':'Калининградская область','other':'Другое место'}.get(key,'Не указано')
            item=locations.setdefault(key,{'type':key,'label':label,'bookings':0,'fees':0})
            item['bookings']+=1
            if r.get('pickup_location_fee') is not None:
                item['fees'] += int(r.get('pickup_location_fee') or 0)

        return {
            'from':start_day.isoformat(),'to':end_day.isoformat(),
            'revenue':revenue,'booking_count':booking_count,'rental_days':round(rental_days,1),
            'avg_check':avg_check,'avg_day':avg_day,'occupancy':occupancy,
            'pending_count':len(pending),'rejected_count':len(rejected),
            'future_revenue':int(future['revenue'] or 0),'future_count':int(future['cnt'] or 0),
            'cars':cars,'daily':daily,'locations':sorted(locations.values(), key=lambda x:-x['bookings']),
            'expenses_total':expense_total,
            'profit':revenue-expense_total,
            'expense_count':len(expense_rows),
            'expense_by_category':[{'category':k,'label':EXPENSE_CATEGORIES.get(k,'📦 Прочее'),'amount':v} for k,v in sorted(expense_by_category.items(), key=lambda x:-x[1])],
            'expense_by_car':[{'car_id':cid,'car_name':CARS.get(cid,{}).get('name',cid),'amount':v} for cid,v in sorted(expense_by_car.items(), key=lambda x:-x[1])]
        }
    finally:
        con.close()


async def admin_finance_api(request):
    admin_web_auth_or_403(request)
    today=datetime.now(TZ).date()
    start_default=today.replace(day=1)
    start_day=_finance_date(request.query.get('from'), start_default)
    end_day=_finance_date(request.query.get('to'), today)
    if end_day < start_day:
        return mini_json({'message':'Дата окончания раньше даты начала.'},400)
    if (end_day-start_day).days > 370:
        return mini_json({'message':'Период отчёта не может быть больше 371 дня.'},400)
    try:
        return mini_json(await asyncio.to_thread(admin_finance_sync,start_day,end_day))
    except Exception as exc:
        print(f"[ADMIN/WEB/FINANCE] {type(exc).__name__}: {exc!r}", flush=True)
        return mini_json({'message':f'Не удалось сформировать отчёт: {type(exc).__name__}'},500)


EXPENSE_CATEGORIES = {
    'maintenance': '🔧 ТО / обслуживание',
    'repair': '🛠 Ремонт',
    'wash': '🧽 Мойка / химчистка',
    'insurance': '🛡 Страховка',
    'tax': '📄 Налог / сборы',
    'parking': '🅿️ Парковка',
    'fuel': '⛽ Топливо',
    'advertising': '📣 Реклама',
    'other': '📦 Прочее',
}

def admin_expenses_list_sync(start_day, end_day):
    con=db()
    try:
        with con.cursor() as cur:
            rows=cur.execute("""
                SELECT e.id,e.expense_date,e.car_id,e.category,e.amount,e.comment
                FROM expenses e
                WHERE e.expense_date BETWEEN %s AND %s
                ORDER BY e.expense_date DESC,e.id DESC
                LIMIT 500
            """,(start_day,end_day)).fetchall()
        return [{
            'id':r['id'],'date':r['expense_date'].isoformat() if r.get('expense_date') else '',
            'car_id':r.get('car_id') or '',
            'car_name':CARS.get(r.get('car_id'),{}).get('name','Все автомобили') if r.get('car_id') else 'Все автомобили',
            'category':r.get('category') or 'other',
            'category_label':EXPENSE_CATEGORIES.get(r.get('category'),'📦 Прочее'),
            'amount':int(r.get('amount') or 0),'comment':r.get('comment') or ''
        } for r in rows]
    finally:
        con.close()

def admin_expense_add_sync(expense_date, car_id, category, amount, comment):
    con=db()
    try:
        with con.cursor() as cur:
            row=cur.execute("""
                INSERT INTO expenses(expense_date,car_id,category,amount,comment)
                VALUES(%s,%s,%s,%s,%s)
                RETURNING id
            """,(expense_date,car_id or None,category,amount,comment or None)).fetchone()
        con.commit(); return int(row['id'])
    finally:
        con.close()

def admin_expense_delete_sync(eid):
    con=db()
    try:
        with con.cursor() as cur:
            cur.execute('DELETE FROM expenses WHERE id=%s',(eid,))
        con.commit()
    finally:
        con.close()

async def admin_expenses_api(request):
    admin_web_auth_or_403(request)
    today=datetime.now(TZ).date()
    start_day=_finance_date(request.query.get('from'),today.replace(day=1))
    end_day=_finance_date(request.query.get('to'),today)
    if request.method=='GET':
        if end_day < start_day:
            return mini_json({'message':'Дата окончания раньше даты начала.'},400)
        return mini_json({'expenses':await asyncio.to_thread(admin_expenses_list_sync,start_day,end_day), 'categories':EXPENSE_CATEGORIES})
    try:
        data=await request.json()
        expense_date=date.fromisoformat(str(data.get('date',''))); category=str(data.get('category','other')).strip()
        amount=int(data.get('amount',0)); car_id=str(data.get('car_id') or '').strip() or None; comment=str(data.get('comment') or '').strip()
        if category not in EXPENSE_CATEGORIES: raise ValueError('invalid category')
        if amount<=0: raise ValueError('invalid amount')
        if car_id and car_id not in CARS: raise ValueError('invalid car')
        eid=await asyncio.to_thread(admin_expense_add_sync,expense_date,car_id,category,amount,comment)
        return mini_json({'ok':True,'id':eid})
    except Exception as exc:
        print(f"[ADMIN/EXPENSE] ADD FAILED: {type(exc).__name__}: {exc!r}",flush=True)
        return mini_json({'message':'Не удалось добавить расход.'},400)

async def admin_expense_delete_api(request):
    admin_web_auth_or_403(request)
    try:
        data=await request.json(); eid=int(data.get('id'))
        await asyncio.to_thread(admin_expense_delete_sync,eid)
        return mini_json({'ok':True})
    except Exception as exc:
        print(f"[ADMIN/EXPENSE] DELETE FAILED: {type(exc).__name__}: {exc!r}",flush=True)
        return mini_json({'message':'Не удалось удалить расход.'},400)

def admin_reviews_web_sync():
    con = db()
    try:
        with con.cursor() as cur:
            rows = cur.execute("""
                SELECT r.id, r.booking_id, r.rating, r.review_text, r.created_at,
                       b.name, b.car_id
                FROM reviews r LEFT JOIN bookings b ON b.id=r.booking_id
                ORDER BY r.created_at DESC LIMIT 200
            """).fetchall()
    finally:
        con.close()
    reviews=[]
    for r in rows:
        reviews.append({'id':r['id'],'booking_id':r['booking_id'],'rating':r['rating'],
                        'text':r.get('review_text') or '','name':r.get('name') or 'Клиент',
                        'car_name':CARS.get(r.get('car_id'),{}).get('name',r.get('car_id') or 'Автомобиль'),
                        'created_at':ensure_tz(r['created_at']).strftime('%d.%m.%Y %H:%M') if r.get('created_at') else ''})
    return reviews

async def admin_reviews_api(request):
    admin_web_auth_or_403(request)
    reviews=await asyncio.to_thread(admin_reviews_web_sync)
    avg=round(sum(float(x['rating']) for x in reviews)/len(reviews),1) if reviews else None
    return mini_json({'reviews':reviews,'average':avg})



# ============================================================
# PROFILE + ADMIN WEB ACTIONS
# ============================================================

def mini_profile_sync(uid):
    """Последние имя/телефон клиента для автозаполнения формы."""
    con = db()
    try:
        with con.cursor() as cur:
            row = cur.execute(
                """
                SELECT name, phone FROM bookings
                WHERE user_id=%s AND name IS NOT NULL AND phone IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (uid,),
            ).fetchone()
        if not row:
            return {"name": "", "phone": ""}
        return {"name": row.get("name") or "", "phone": row.get("phone") or ""}
    finally:
        con.close()


async def mini_profile(request):
    uid = mini_auth_or_401(request)
    data = await asyncio.to_thread(mini_profile_sync, uid)
    return mini_json(data)


def admin_set_fee_sync(bid, fee):
    con = db()
    try:
        with con.cursor() as cur:
            lock_bookings(cur)
            row = lock_booking_row(cur, bid)
            if not row:
                con.rollback()
                return {"ok": False, "reason": "not_found"}
            if fee is None:
                cur.execute(
                    "UPDATE bookings SET pickup_location_fee=NULL WHERE id=%s",
                    (bid,),
                )
            else:
                fee = int(fee)
                if fee < 0:
                    con.rollback()
                    return {"ok": False, "reason": "invalid"}
                cur.execute(
                    "UPDATE bookings SET pickup_location_fee=%s WHERE id=%s",
                    (fee, bid),
                )
        con.commit()
        return {"ok": True, "id": bid, "fee": fee}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


async def admin_booking_action_api(request):
    """Подтвердить / отклонить / отменить бронь из веб-админки."""
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
        bid = int(data.get("id", 0))
        action = str(data.get("action", "")).strip().lower()
        if action not in ("confirm", "reject", "cancel", "issue", "return") or bid <= 0:
            return mini_json({"message": "Некорректный запрос."}, 400)
        if action == "issue":
            dep = data.get("deposit_amount")
            try:
                dep = int(dep) if dep is not None and str(dep).strip() != "" else None
            except (TypeError, ValueError):
                dep = None
            mo = data.get("mileage_out")
            try:
                mo = int(mo) if mo is not None and str(mo).strip() != "" else None
            except (TypeError, ValueError):
                mo = None
            fuel = (data.get("fuel_out") or None)
            notes = data.get("notes")
            row = await asyncio.to_thread(mark_issued_sync, bid, notes, dep, mo, fuel)
            if not row:
                return mini_json({"message": "Не удалось отметить выдачу."}, 409)
            return mini_json({"ok": True, "action": "issue", "id": bid})
        if action == "return":
            mi = data.get("mileage_in")
            try:
                mi = int(mi) if mi is not None and str(mi).strip() != "" else None
            except (TypeError, ValueError):
                mi = None
            fuel = (data.get("fuel_in") or None)
            dstat = data.get("deposit_status")
            notes = data.get("notes")
            row = await asyncio.to_thread(mark_returned_sync, bid, notes, mi, fuel, dstat)
            if not row:
                return mini_json({"message": "Не удалось отметить возврат (нужна выдача)."}, 409)
            return mini_json({"ok": True, "action": "return", "id": bid})
        result = await asyncio.to_thread(admin_action_sync, action, bid)
        if not result.get("ok"):
            reason = result.get("reason")
            messages = {
                "not_found": "Заявка не найдена.",
                "already_processed": f"Заявка уже обработана ({result.get('status')}).",
                "overlap": "Пересечение с другой бронью — заявка отклонена.",
                "maintenance": "Период обслуживания — заявка отклонена.",
            }
            return mini_json({"message": messages.get(reason, "Не удалось выполнить действие.")}, 409)
        row = result["row"]
        try:
            if result["action"] == "confirm":
                text = (
                    f"✅ <b>Бронирование №{bid} подтверждено</b>\n\n"
                    f"🚗 {escape_html(CARS.get(row['car_id'], {}).get('name', row['car_id']))}\n"
                    f"📅 {format_date_time(result['start_at'])} → {format_date_time(result['end_at'])}\n"
                    f"💰 {money(row.get('total') or 0)}\n\n"
                    "Менеджер свяжется с вами по деталям выдачи."
                )
            elif result["action"] == "cancel":
                text = (
                    f"⚫ <b>Бронирование №{bid} отменено</b>\n\n"
                    "Если нужна новая дата — оформите заявку в Mini App."
                )
            else:
                text = (
                    f"❌ <b>Заявка №{bid} отклонена</b>\n\n"
                    "Выберите другие даты или свяжитесь с менеджером."
                )
            async with ClientSession() as session:
                await session.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": row["user_id"], "text": text, "parse_mode": "HTML"},
                    timeout=ClientTimeout(total=15),
                )
        except Exception as exc:
            print(f"[ADMIN/WEB/ACTION] notify failed: {type(exc).__name__}: {exc}", flush=True)
        return mini_json({"ok": True, "action": result["action"], "id": bid})
    except Exception as exc:
        print(f"[ADMIN/WEB/ACTION] {type(exc).__name__}: {exc!r}", flush=True)
        return mini_json({"message": "Ошибка сервера."}, 500)


async def admin_booking_fee_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
        bid = int(data.get("id", 0))
        fee_raw = data.get("fee", None)
        fee = None if fee_raw is None or fee_raw == "" else int(fee_raw)
        if bid <= 0:
            return mini_json({"message": "Некорректный id."}, 400)
        result = await asyncio.to_thread(admin_set_fee_sync, bid, fee)
        if not result.get("ok"):
            return mini_json({"message": "Не удалось сохранить стоимость подачи."}, 409)
        return mini_json(result)
    except Exception as exc:
        print(f"[ADMIN/WEB/FEE] {type(exc).__name__}: {exc!r}", flush=True)
        return mini_json({"message": "Ошибка сервера."}, 500)



async def admin_block_create_api(request):
    """Ручная занятость: бронь с сайта / внешняя бронь / ТО.

    Пишет в car_maintenance — период блокируется для клиентов бота.
    """
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
    except Exception:
        return mini_json({"message": "Некорректный JSON."}, 400)

    cid = str(data.get("car_id") or "").strip()
    reason = str(data.get("reason") or "Бронь с сайта").strip() or "Бронь с сайта"
    start_date = str(data.get("start_date") or "").strip()
    end_date = str(data.get("end_date") or "").strip()
    start_time = str(data.get("start_time") or "10:00").strip()
    end_time = str(data.get("end_time") or "10:00").strip()

    if cid not in CARS:
        return mini_json({"message": "Автомобиль не найден."}, 400)
    if not CARS[cid].get("active", True):
        return mini_json({"message": "Автомобиль отключён."}, 400)

    try:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
        sh, sm = [int(x) for x in start_time.split(":")[:2]]
        eh, em = [int(x) for x in end_time.split(":")[:2]]
        start_at = local_dt(sd, time(sh, sm))
        end_at = local_dt(ed, time(eh, em))
    except Exception:
        return mini_json({"message": "Проверьте даты и время (ГГГГ-ММ-ДД и ЧЧ:ММ)."}, 400)

    if end_at <= start_at:
        return mini_json({"message": "Конец периода должен быть позже начала."}, 400)
    if (end_at - start_at).days > 60:
        return mini_json({"message": "Период блокировки не больше 60 дней."}, 400)

    try:
        row = await asyncio.to_thread(add_maintenance_sync, cid, start_at, end_at, reason)
        return mini_json({
            "ok": True,
            "id": row["id"],
            "car_id": cid,
            "car_name": CARS[cid].get("name", cid),
            "start_label": ensure_tz(row["start_at"]).strftime("%d.%m.%Y %H:%M"),
            "end_label": ensure_tz(row["end_at"]).strftime("%d.%m.%Y %H:%M"),
            "reason": row.get("reason") or reason,
        })
    except Exception as exc:
        print(f"[ADMIN/WEB/BLOCK] {type(exc).__name__}: {exc!r}", flush=True)
        return mini_json({"message": "Не удалось сохранить занятость."}, 500)


async def admin_block_delete_api(request):
    """Удалить ручную занятость / ТО из веб-админки."""
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
        mid = int(data.get("id", 0))
    except Exception:
        return mini_json({"message": "Некорректный запрос."}, 400)
    if mid <= 0:
        return mini_json({"message": "Некорректный id."}, 400)
    try:
        await asyncio.to_thread(delete_maintenance_sync, mid)
        return mini_json({"ok": True, "id": mid})
    except Exception as exc:
        print(f"[ADMIN/WEB/BLOCK/DEL] {type(exc).__name__}: {exc!r}", flush=True)
        return mini_json({"message": "Не удалось удалить блок."}, 500)




def admin_list_cars_sync():
    load_car_settings()
    out = []
    for cid, car in CARS.items():
        out.append({
            'car_id': cid,
            'name': car.get('name', cid),
            'gear': car.get('gear', 'АКПП'),
            'fuel': car.get('fuel', 'Бензин'),
            'seats': car.get('seats', 5),
            'rates': list(car.get('rates', (0, 0, 0))),
            'active': bool(car.get('active', True)),
            'description': car.get('description') or '',
            'plate': car.get('plate') or '',
            'vin': car.get('vin') or '',
            'is_custom': bool(car.get('is_custom')) or cid not in ('solaris21', 'solaris20', 'solaris17', 'i30'),
        })
    out.sort(key=lambda x: x['name'])
    return out


def admin_save_car_sync(data):
    """Create or update car in car_settings and CARS."""
    car_id = (data.get('car_id') or '').strip()
    name = (data.get('name') or '').strip()
    if not name:
        return {'ok': False, 'reason': 'name'}
    is_new = not car_id
    if is_new:
        base = re.sub(r'[^a-z0-9]+', '', name.lower())[:16] or 'car'
        car_id = base
        n = 1
        while car_id in CARS:
            n += 1
            car_id = f'{base}{n}'
    try:
        r1 = int(data.get('rate_1_3') or data.get('rates', [0])[0])
        r2 = int(data.get('rate_4_6') or data.get('rates', [0, 0])[1])
        r3 = int(data.get('rate_7_plus') or data.get('rates', [0, 0, 0])[2])
    except (TypeError, ValueError, IndexError):
        return {'ok': False, 'reason': 'rates'}
    if min(r1, r2, r3) <= 0:
        return {'ok': False, 'reason': 'rates'}
    gear = (data.get('gear') or 'АКПП').strip()
    fuel = (data.get('fuel') or 'Бензин').strip()
    try:
        seats = int(data.get('seats') or 5)
    except (TypeError, ValueError):
        seats = 5
    description = (data.get('description') or '').strip()
    plate = (data.get('plate') or '').strip()
    vin = (data.get('vin') or '').strip()
    active = bool(data.get('active', True))
    is_custom = car_id not in ('solaris21', 'solaris20', 'solaris17', 'i30')

    con = db()
    try:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO car_settings (
                    car_id, name, active, rate_1_3, rate_4_6, rate_7_plus,
                    gear, fuel, seats, description, is_custom, plate, vin, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (car_id) DO UPDATE SET
                    name=EXCLUDED.name,
                    active=EXCLUDED.active,
                    rate_1_3=EXCLUDED.rate_1_3,
                    rate_4_6=EXCLUDED.rate_4_6,
                    rate_7_plus=EXCLUDED.rate_7_plus,
                    gear=EXCLUDED.gear,
                    fuel=EXCLUDED.fuel,
                    seats=EXCLUDED.seats,
                    description=EXCLUDED.description,
                    plate=EXCLUDED.plate,
                    vin=EXCLUDED.vin,
                    updated_at=NOW()
                """,
                (car_id, name, active, r1, r2, r3, gear, fuel, seats, description, is_custom, plate, vin)
            )
        con.commit()
    finally:
        con.close()
    load_car_settings()
    return {'ok': True, 'car_id': car_id}


def admin_delete_car_sync(car_id):
    if car_id in ('solaris21', 'solaris20', 'solaris17', 'i30'):
        # built-in: soft disable instead of delete
        con = db()
        try:
            with con.cursor() as cur:
                cur.execute(
                    "UPDATE car_settings SET active=FALSE, updated_at=NOW() WHERE car_id=%s",
                    (car_id,)
                )
            con.commit()
        finally:
            con.close()
        load_car_settings()
        return {'ok': True, 'soft': True}
    con = db()
    try:
        with con.cursor() as cur:
            active = cur.execute(
                """
                SELECT id FROM bookings
                WHERE car_id=%s AND status IN ('pending','confirmed')
                LIMIT 1
                """,
                (car_id,)
            ).fetchone()
            if active:
                return {'ok': False, 'reason': 'has_bookings'}
            cur.execute("DELETE FROM car_settings WHERE car_id=%s", (car_id,))
            cur.execute("DELETE FROM car_maintenance WHERE car_id=%s", (car_id,))
        con.commit()
    finally:
        con.close()
    CARS.pop(car_id, None)
    load_car_settings()
    return {'ok': True, 'soft': False}


def admin_create_client_sync(full_name, phone, notes='', username='', **extra):
    full_name = (full_name or '').strip()
    phone = (phone or '').strip()
    if len(full_name) < 2 or len(phone) < 5:
        return {'ok': False, 'reason': 'fields'}
    existing = get_client_sync(phone=phone)
    fields = {
        'passport_series': (extra.get('passport_series') or '').strip() or None,
        'passport_number': (extra.get('passport_number') or '').strip() or None,
        'passport_issued_by': (extra.get('passport_issued_by') or '').strip() or None,
        'passport_issued_date': (extra.get('passport_issued_date') or '').strip() or None,
        'license_number': (extra.get('license_number') or '').strip() or None,
        'license_issued_date': (extra.get('license_issued_date') or '').strip() or None,
        'license_categories': (extra.get('license_categories') or '').strip() or None,
        'address': (extra.get('address') or '').strip() or None,
        'birth_date': (extra.get('birth_date') or '').strip() or None,
        'notes': (notes or '').strip() or None,
        'username': (username or '').strip() or None,
    }
    con = db()
    try:
        with con.cursor() as cur:
            if existing:
                sets = ['full_name=%s', 'phone=%s', 'updated_at=NOW()']
                vals = [full_name, phone]
                for k, v in fields.items():
                    if v is not None:
                        sets.append(f'{k}=%s')
                        vals.append(v)
                vals.append(existing['id'])
                row = cur.execute(
                    f"UPDATE clients SET {', '.join(sets)} WHERE id=%s RETURNING *",
                    vals,
                ).fetchone()
            else:
                row = cur.execute(
                    """
                    INSERT INTO clients (
                        full_name, phone, username, notes,
                        passport_series, passport_number, passport_issued_by, passport_issued_date,
                        license_number, license_issued_date, license_categories,
                        address, birth_date
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (
                        full_name, phone, fields['username'], fields['notes'],
                        fields['passport_series'], fields['passport_number'],
                        fields['passport_issued_by'], fields['passport_issued_date'],
                        fields['license_number'], fields['license_issued_date'],
                        fields['license_categories'], fields['address'], fields['birth_date'],
                    ),
                ).fetchone()
        con.commit()
        return {'ok': True, 'client': dict(row)}
    finally:
        con.close()



def admin_planner_sync():
    """Данные вкладки «Сегодня» для Mini App."""
    def pack(rows):
        out = []
        for r in rows:
            try:
                sa = ensure_tz(r.get("start_at")) if r.get("start_at") else None
            except Exception:
                sa = None
            try:
                ea = ensure_tz(r.get("end_at")) if r.get("end_at") else None
            except Exception:
                ea = None
            out.append({
                "id": r["id"],
                "car_id": r.get("car_id"),
                "car_name": CARS.get(r.get("car_id"), {}).get("name", r.get("car_id") or ""),
                "name": r.get("name") or "",
                "phone": r.get("phone") or "",
                "status": r.get("status"),
                "issued_at": ensure_tz(r["issued_at"]).isoformat() if r.get("issued_at") else None,
                "returned_at": ensure_tz(r["returned_at"]).isoformat() if r.get("returned_at") else None,
                "start_label": sa.strftime("%d.%m.%Y %H:%M") if sa else "",
                "end_label": ea.strftime("%d.%m.%Y %H:%M") if ea else "",
                "total": r.get("total") or 0,
                "deposit_amount": r.get("deposit_amount") or 0,
                "deposit_status": r.get("deposit_status") or "",
            })
        return out

    try:
        pickups = get_today_pickups_sync()
    except Exception as exc:
        print(f"[PLANNER] pickups error: {exc}", flush=True)
        pickups = []
    try:
        returns = get_today_returns_sync()
    except Exception as exc:
        print(f"[PLANNER] returns error: {exc}", flush=True)
        returns = []
    try:
        tomorrow = get_tomorrow_pickups_sync()
    except Exception as exc:
        print(f"[PLANNER] tomorrow error: {exc}", flush=True)
        tomorrow = []
    try:
        in_progress = get_in_progress_rentals_sync()
    except Exception as exc:
        print(f"[PLANNER] in_progress error: {exc}", flush=True)
        in_progress = []

    return {
        "pickups": pack(pickups),
        "returns": pack(returns),
        "tomorrow": pack(tomorrow),
        "in_progress": pack(in_progress),
    }


async def admin_planner_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await asyncio.to_thread(admin_planner_sync)
        return mini_json(data)
    except Exception as exc:
        print(f"[PLANNER/API] {type(exc).__name__}: {exc}", flush=True)
        return mini_json({"message": f"Ошибка планировщика: {exc}", "pickups": [], "returns": [], "tomorrow": [], "in_progress": []}, 500)


def admin_clients_list_sync(query=None, limit=50):
    if query and query.strip():
        rows = search_clients_sync(query.strip(), limit)
    else:
        rows = list_recent_clients_sync(limit)
    out = []
    for c in rows:
        out.append({
            "id": c["id"],
            "full_name": c.get("full_name") or "",
            "phone": c.get("phone") or "",
            "username": c.get("username") or "",
            "user_id": c.get("user_id"),
            "notes": c.get("notes") or "",
            "blacklisted": bool(c.get("blacklisted")),
            "updated_at": ensure_tz(c["updated_at"]).strftime("%d.%m.%Y %H:%M") if c.get("updated_at") else "",
        })
    return out


async def admin_clients_api(request):
    admin_web_auth_or_403(request)
    q = request.query.get("q") or request.query.get("query") or ""
    rows = await asyncio.to_thread(admin_clients_list_sync, q, 50)
    return mini_json({"clients": rows})


def admin_client_detail_sync(client_id):
    client = get_client_sync(client_id=client_id)
    if not client:
        return None
    bookings = client_bookings_sync(client_id, 30)
    b_out = []
    for b in bookings:
        sa = ensure_tz(b.get("start_at")) if b.get("start_at") else None
        ea = ensure_tz(b.get("end_at")) if b.get("end_at") else None
        b_out.append({
            "id": b["id"],
            "car_name": CARS.get(b["car_id"], {}).get("name", b["car_id"]),
            "status": b.get("status"),
            "status_label": status_label(b.get("status")),
            "start_label": sa.strftime("%d.%m.%Y %H:%M") if sa else "",
            "end_label": ea.strftime("%d.%m.%Y %H:%M") if ea else "",
            "total": b.get("total") or 0,
        })
    return {
        "client": {
            "id": client["id"],
            "full_name": client.get("full_name") or "",
            "phone": client.get("phone") or "",
            "username": client.get("username") or "",
            "user_id": client.get("user_id"),
            "notes": client.get("notes") or "",
            "blacklisted": bool(client.get("blacklisted")),
            "passport_series": client.get("passport_series") or "",
            "passport_number": client.get("passport_number") or "",
            "passport_issued_by": client.get("passport_issued_by") or "",
            "passport_issued_date": client.get("passport_issued_date") or "",
            "license_number": client.get("license_number") or "",
            "license_issued_date": client.get("license_issued_date") or "",
            "license_categories": client.get("license_categories") or "",
            "address": client.get("address") or "",
            "birth_date": client.get("birth_date") or "",
        },
        "bookings": b_out,
    }


async def admin_client_detail_api(request):
    admin_web_auth_or_403(request)
    try:
        cid = int(request.query.get("id") or 0)
    except ValueError:
        return mini_json({"message": "Некорректный id"}, 400)
    data = await asyncio.to_thread(admin_client_detail_sync, cid)
    if not data:
        return mini_json({"message": "Клиент не найден"}, 404)
    return mini_json(data)


async def admin_client_action_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
        cid = int(data.get("id", 0))
        action = str(data.get("action", "")).strip().lower()
    except Exception:
        return mini_json({"message": "Некорректный запрос"}, 400)
    if cid <= 0:
        return mini_json({"message": "Некорректный id"}, 400)
    if action == "blacklist":
        client = await asyncio.to_thread(get_client_sync, client_id=cid)
        if not client:
            return mini_json({"message": "Клиент не найден"}, 404)
        new_val = not bool(client.get("blacklisted"))
        await asyncio.to_thread(set_client_blacklist_sync, cid, new_val)
        return mini_json({"ok": True, "blacklisted": new_val})
    if action == "notes":
        notes = str(data.get("notes", "")).strip()
        await asyncio.to_thread(set_client_notes_sync, cid, notes)
        return mini_json({"ok": True})
    if action == "update":
        row = await asyncio.to_thread(admin_update_client_docs_sync, cid, data)
        if not row:
            return mini_json({"message": "Клиент не найден"}, 404)
        return mini_json({"ok": True})
    return mini_json({"message": "Неизвестное действие"}, 400)




async def admin_cars_api(request):
    admin_web_auth_or_403(request)
    rows = await asyncio.to_thread(admin_list_cars_sync)
    return mini_json({'cars': rows})


async def admin_car_save_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
    except Exception:
        return mini_json({'message': 'Некорректный JSON'}, 400)
    result = await asyncio.to_thread(admin_save_car_sync, data)
    if not result.get('ok'):
        msg = {
            'name': 'Укажите название автомобиля',
            'rates': 'Проверьте тарифы (числа > 0)',
        }.get(result.get('reason'), 'Не удалось сохранить')
        return mini_json({'message': msg}, 400)
    return mini_json(result)


async def admin_car_delete_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
        car_id = str(data.get('car_id') or '').strip()
    except Exception:
        return mini_json({'message': 'Некорректный запрос'}, 400)
    if not car_id:
        return mini_json({'message': 'car_id обязателен'}, 400)
    result = await asyncio.to_thread(admin_delete_car_sync, car_id)
    if not result.get('ok'):
        if result.get('reason') == 'has_bookings':
            return mini_json({'message': 'Есть активные брони — сначала отмените их или отключите авто.'}, 409)
        return mini_json({'message': 'Не удалось удалить'}, 400)
    return mini_json(result)


async def admin_client_create_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
    except Exception:
        return mini_json({'message': 'Некорректный JSON'}, 400)
    result = await asyncio.to_thread(
        admin_create_client_sync,
        data.get('full_name'),
        data.get('phone'),
        data.get('notes') or '',
        data.get('username') or '',
        passport_series=data.get('passport_series'),
        passport_number=data.get('passport_number'),
        passport_issued_by=data.get('passport_issued_by'),
        passport_issued_date=data.get('passport_issued_date'),
        license_number=data.get('license_number'),
        license_issued_date=data.get('license_issued_date'),
        license_categories=data.get('license_categories'),
        address=data.get('address'),
        birth_date=data.get('birth_date'),
    )
    if not result.get('ok'):
        if result.get('reason') == 'exists':
            return mini_json({'message': 'Клиент с таким телефоном уже есть', 'id': result.get('id')}, 409)
        return mini_json({'message': 'Укажите ФИО и телефон'}, 400)
    return mini_json(result)


# ============================================================
# MANUAL BOOKING / TEMPLATES / EXPORT / CONTRACT
# (merged into bot.py)
# ============================================================

MESSAGE_TEMPLATES = [
    {
        "id": "confirmed",
        "title": "Бронь подтверждена",
        "body": (
            "Здравствуйте, {name}!\n\nВаша бронь №{bid} подтверждена.\n"
            "🚗 {car}\n📅 {start} → {end}\n💰 {total}\n\nЖдём вас вовремя. BALTICAR"
        ),
    },
    {
        "id": "pickup_tomorrow",
        "title": "Напоминание: выдача завтра",
        "body": (
            "Здравствуйте, {name}!\n\nНапоминаем: завтра выдача автомобиля.\n"
            "🚗 {car}\n📅 {start}\n\nВозьмите паспорт и водительское удостоверение.\nBALTICAR"
        ),
    },
    {
        "id": "return_today",
        "title": "Напоминание: возврат сегодня",
        "body": (
            "Здравствуйте, {name}!\n\nСегодня возврат автомобиля.\n"
            "🚗 {car}\n📅 {end}\n\nПожалуйста, верните авто вовремя и с тем же уровнем топлива.\nBALTICAR"
        ),
    },
    {
        "id": "docs_request",
        "title": "Запрос документов",
        "body": (
            "Здравствуйте, {name}!\n\nДля договора аренды пришлите, пожалуйста:\n"
            "• паспорт (разворот)\n• водительское удостоверение\n\nBALTICAR"
        ),
    },
]


def admin_update_client_docs_sync(client_id, data):
    con = db()
    try:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE clients SET
                    full_name = COALESCE(%s, full_name),
                    phone = COALESCE(%s, phone),
                    notes = COALESCE(%s, notes),
                    passport_series = COALESCE(%s, passport_series),
                    passport_number = COALESCE(%s, passport_number),
                    passport_issued_by = COALESCE(%s, passport_issued_by),
                    passport_issued_date = COALESCE(%s, passport_issued_date),
                    license_number = COALESCE(%s, license_number),
                    license_issued_date = COALESCE(%s, license_issued_date),
                    license_categories = COALESCE(%s, license_categories),
                    address = COALESCE(%s, address),
                    birth_date = COALESCE(%s, birth_date),
                    updated_at = NOW()
                WHERE id=%s
                RETURNING *
                """,
                (
                    data.get("full_name") or None,
                    data.get("phone") or None,
                    data.get("notes") if data.get("notes") is not None else None,
                    data.get("passport_series") or None,
                    data.get("passport_number") or None,
                    data.get("passport_issued_by") or None,
                    data.get("passport_issued_date") or None,
                    data.get("license_number") or None,
                    data.get("license_issued_date") or None,
                    data.get("license_categories") or None,
                    data.get("address") or None,
                    data.get("birth_date") or None,
                    client_id,
                ),
            )
            row = cur.fetchone()
        con.commit()
        return row
    finally:
        con.close()


def admin_manual_booking_sync(data):
    car_id = (data.get("car_id") or "").strip()
    if car_id not in CARS:
        return {"ok": False, "reason": "car"}
    try:
        def _parse_dt(val):
            s = str(val).strip().replace("Z", "+00:00")
            # datetime-local often comes as "2026-11-24T10:30" without tz
            if "T" in s and len(s) == 16:
                s = s + ":00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            return ensure_tz(dt)
        start_at = _parse_dt(data.get("start_at"))
        end_at = _parse_dt(data.get("end_at"))
    except Exception:
        return {"ok": False, "reason": "dates"}
    if end_at <= start_at:
        return {"ok": False, "reason": "dates"}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    client_id = data.get("client_id")
    try:
        client_id = int(client_id) if client_id else None
    except (TypeError, ValueError):
        client_id = None
    if not name or not phone:
        if client_id:
            cl = get_client_sync(client_id=client_id)
            if not cl:
                return {"ok": False, "reason": "client"}
            name = cl["full_name"]
            phone = cl["phone"]
        else:
            return {"ok": False, "reason": "client"}
    comment = (data.get("comment") or "Ручная бронь админа").strip()
    try:
        user_id = int(data.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if not available(car_id, start_at, end_at):
        return {"ok": False, "reason": "overlap"}
    days = rental_days(start_at, end_at)
    total = days * rate_for_days(car_id, days)
    if data.get("total") is not None:
        try:
            total = int(data.get("total"))
        except (TypeError, ValueError):
            pass
    client = upsert_client_sync(user_id or None, None, name, phone, None)
    client_id = client["id"] if client else client_id
    if client and client.get("blacklisted"):
        return {"ok": False, "reason": "blacklisted"}
    contract_no = f"BK-{datetime.now(TZ).strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"
    con = db()
    try:
        with con.cursor() as cur:
            lock_bookings(cur)
            expire_pending(cur)
            if find_maintenance(cur, car_id, start_at, end_at):
                con.rollback()
                return {"ok": False, "reason": "maintenance"}
            if find_overlap(cur, car_id, start_at, end_at):
                con.rollback()
                return {"ok": False, "reason": "overlap"}
            row = cur.execute(
                """
                INSERT INTO bookings (
                    user_id, username, car_id, start_date, end_date, start_at, end_at,
                    name, phone, comment, total, status, created_at, expires_at,
                    client_id, contract_number
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, 'confirmed', %s, NULL,
                    %s, %s
                ) RETURNING *
                """,
                (
                    user_id, "", car_id, start_at.date(), end_at.date(), start_at, end_at,
                    name, phone, comment, total, datetime.now(TZ),
                    client_id, contract_no,
                ),
            ).fetchone()
        con.commit()
        return {
            "ok": True,
            "id": row["id"],
            "contract_number": contract_no,
            "total": int(row.get("total") or total),
            "car_id": car_id,
            "name": name,
            "phone": phone,
        }
    except Exception as exc:
        con.rollback()
        print(f"[MANUAL/BOOKING] {type(exc).__name__}: {exc}", flush=True)
        return {"ok": False, "reason": "db", "error": str(exc)}
    finally:
        con.close()


def admin_booking_full_sync(bid):
    con = db()
    try:
        with con.cursor() as cur:
            b = cur.execute("SELECT * FROM bookings WHERE id=%s", (bid,)).fetchone()
            if not b:
                return None
            client = None
            if b.get("client_id"):
                client = cur.execute("SELECT * FROM clients WHERE id=%s", (b["client_id"],)).fetchone()
            if not client and b.get("phone"):
                client = cur.execute(
                    "SELECT * FROM clients WHERE phone=%s ORDER BY id DESC LIMIT 1",
                    (b["phone"],),
                ).fetchone()
        return {"booking": b, "client": client}
    finally:
        con.close()


def build_contract_html(bid):
    """Договор по шаблону BALTICAR (как в Word-форме Сорокоумова)."""
    data = admin_booking_full_sync(bid)
    if not data:
        return None
    b = data["booking"]
    c = data["client"] or {}
    car = CARS.get(b["car_id"], {}) or {}
    sa = ensure_tz(b["start_at"]) if b.get("start_at") else None
    ea = ensure_tz(b["end_at"]) if b.get("end_at") else None
    days = rental_days(sa, ea) if sa and ea else 1
    rate = rate_for_days(b["car_id"], days) if b.get("car_id") in CARS else 0
    total = int(b.get("total") or (rate * days))
    deposit = int(b.get("deposit_amount") or 0)
    contract_no = b.get("contract_number") or f"BK-{b['id']}"
    if not b.get("contract_number"):
        con = db()
        try:
            with con.cursor() as cur:
                cur.execute(
                    "UPDATE bookings SET contract_number=%s WHERE id=%s AND (contract_number IS NULL OR contract_number='')",
                    (contract_no, bid),
                )
            con.commit()
        finally:
            con.close()

    def v(x, blank="________________"):
        s = str(x).strip() if x is not None else ""
        return escape_html(s if s else blank)

    def dmy(dt):
        return dt.strftime("%d.%m.%Y") if dt else "____________"

    def hm(dt):
        return dt.strftime("%H:%M") if dt else "____"

    lessor_name = os.getenv("LESSOR_NAME", "Сорокоумов Тимофей Викторович")
    lessor_address = os.getenv("LESSOR_ADDRESS", "г. Калининград ул. Шахматная д. 4В")
    lessor_inn = os.getenv("LESSOR_INN", "650302181070")
    lessor_rs = os.getenv("LESSOR_RS", "40817810220861910843")
    lessor_ks = os.getenv("LESSOR_KS", "30101810100000000634")
    lessor_bank = os.getenv("LESSOR_BANK", "Калининградское отделение №8626 ПАО Сбербанк")
    lessor_bik = os.getenv("LESSOR_BIK", "042748634")
    lessor_phone = os.getenv("LESSOR_PHONE", "89097770133")
    lessor_email = os.getenv("LESSOR_EMAIL", "tvsorokoumov@gmail.com")
    lessor_schedule = os.getenv(
        "LESSOR_SCHEDULE",
        "c 08:00-20:00 Пн-Пт; 09:00-19:00 выходные и праздничные дни",
    )
    pickup_place = (
        b.get("pickup_location")
        or os.getenv("DEFAULT_PICKUP_PLACE", "г. Калининград")
    )
    return_place = pickup_place
    car_name = car.get("name") or b.get("car_id") or "_______________"
    plate = car.get("plate") or ""
    vin = car.get("vin") or ""
    passport = " ".join(
        x for x in [
            (c.get("passport_series") or ""),
            (("№" + c["passport_number"]) if c.get("passport_number") else ""),
            (("выдан " + c["passport_issued_date"]) if c.get("passport_issued_date") else ""),
            (c.get("passport_issued_by") or ""),
        ] if x
    ).strip()
    license_line = " ".join(
        x for x in [
            (c.get("license_number") or ""),
            (("кат. " + c["license_categories"]) if c.get("license_categories") else ""),
            (c.get("license_issued_date") or ""),
        ] if x
    ).strip()
    renter = c.get("full_name") or b.get("name") or ""
    phone = c.get("phone") or b.get("phone") or ""
    address = c.get("address") or ""
    contract_date = dmy(sa) if sa else dmy(datetime.now(TZ))

    css = (
        "body{font-family:'Times New Roman',Times,serif;font-size:13px;line-height:1.35;"
        "color:#111;max-width:900px;margin:16px auto;padding:12px 18px}"
        "h1{text-align:center;font-size:18px;margin:0 0 6px}"
        "h2{font-size:14px;margin:14px 0 6px}"
        ".head{display:flex;justify-content:space-between;margin-bottom:10px}"
        "table{width:100%;border-collapse:collapse;margin:8px 0}"
        "td,th{border:1px solid #333;padding:5px 7px;vertical-align:top}"
        ".noborder td{border:none;padding:2px 0}"
        ".muted{color:#444;font-size:12px}"
        ".sign{display:flex;justify-content:space-between;margin-top:28px;gap:24px}"
        ".sign div{width:46%}"
        "@media print{body{margin:0;padding:8mm}}"
    )

    rows = []
    rows.append("<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"utf-8\">")
    rows.append(f"<title>Договор № {v(contract_no)}</title><style>{css}</style></head><body>")
    rows.append("<h1>Договор аренды транспортного средства</h1>")
    rows.append(
        f"<div class=\"head\"><div>г. Калининград</div><div>{v(contract_date)}</div></div>"
    )
    rows.append("<table><tr>")
    rows.append(f"<td style=\"width:18%\"><b>№:{v(contract_no,'')}</b></td>")
    rows.append(
        "<td><b>АРЕНДОДАТЕЛЬ:</b> "
        f"{v(lessor_name)}. Юридический адрес: {v(lessor_address)}. "
        f"ИНН:{v(lessor_inn)}, р/с: № {v(lessor_rs)} к/с: № {v(lessor_ks)} "
        f"в Банк: {v(lessor_bank)}, БИК: {v(lessor_bik)}."
        "</td></tr></table>"
    )
    rows.append(
        f"<p class=\"muted\"><b>График работы:</b> {v(lessor_schedule)}. "
        f"Телефон: <b>{v(lessor_phone)}</b>, email: <b>{v(lessor_email)}</b></p>"
    )

    rows.append("<table>")
    rows.append("<tr><td colspan=\"2\"><b>1. Арендатор:</b></td></tr>")
    rows.append(f"<tr><td style=\"width:8%\">1.1</td><td><b>Арендатор:</b> {v(renter)}</td></tr>")
    rows.append(
        f"<tr><td>1.2</td><td><b>Документ, удостоверяющий личность:</b> Паспорт: {v(passport)}</td></tr>"
    )
    rows.append(f"<tr><td>1.3</td><td><b>Адрес проживания:</b> {v(address)}</td></tr>")
    rows.append(f"<tr><td>1.4</td><td><b>Телефон дом./моб.:</b> {v(phone)}</td></tr>")
    rows.append(f"<tr><td>1.5</td><td><b>Водительское удостоверение:</b> {v(license_line)}</td></tr>")
    rows.append("</table>")

    rows.append(
        f"<p>{v(lessor_name)}, именуемый в дальнейшем «Арендодатель», с одной стороны, "
        f"и гражданин(ка) {v(renter)}, с другой стороны, заключили настоящий Договор "
        "о нижеследующем.</p>"
    )

    rows.append("<h2>2. Предмет договора</h2>")
    rows.append("<table><tr>")
    rows.append(f"<td><b>2.1.1</b> {v(car_name)}</td>")
    rows.append(f"<td><b>2.1.2 Гос. номер</b> {v(plate)}</td>")
    rows.append(f"<td><b>2.1.3 VIN:</b> {v(vin)}</td>")
    rows.append("<td><b>2.1.4 Категория</b> B</td>")
    rows.append("</tr></table>")
    rows.append(
        "<p>2.2. Комплектация Автомобиля, передаваемого в аренду, указана в Акте приёма-передачи "
        "(Приложение № 1). При утрате, повреждении или отсутствии указанных принадлежностей "
        "при возврате Автомобиля Арендодателю Арендатор обязан оплатить стоимость услуг "
        "Арендодателя по доукомплектованию Автомобиля по тарифам, указанным в Акте приёма-передачи.</p>"
    )
    rows.append(
        "<p>2.3. Арендодатель предоставляет Арендатору за плату во временное владение и пользование "
        "транспортное средство, указанное в п. 2 настоящего Договора, без оказания услуг по управлению "
        "им и его технической эксплуатации для использования Арендатором в потребительских целях.</p>"
    )
    rows.append(
        "<p>2.4. Передача транспортного средства в аренду осуществляется по акту приёмки-передачи "
        "(Приложение № 1), который является неотъемлемой частью настоящего Договора.</p>"
    )
    rows.append(
        "<p>2.5. Автотранспортное средство является пригодным к использованию и имеет все необходимые "
        "разрешительные документы и государственные номера для нормальной эксплуатации. Точные "
        "характеристики отражены в Приложении № 1.</p>"
    )

    rows.append("<h2>3. Цена договора и тарифы</h2>")
    rows.append("<table>")
    rows.append(
        "<tr><th>3.1 Цены/Категории</th><th>Марка</th><th>Ед. измер.</th>"
        "<th>Кол-во</th><th>Цена</th><th>Общая сумма</th></tr>"
    )
    rows.append(
        f"<tr><td>Суточная аренда</td><td>{v(car_name)}</td><td>сутки</td>"
        f"<td><b>{days}</b></td><td><b>{rate}</b></td><td><b>{total}</b></td></tr>"
    )
    rows.append("</table>")
    if deposit:
        rows.append(f"<p>Залог: <b>{deposit} ₽</b> (статус: {v(b.get('deposit_status') or 'не внесён')}).</p>")
    rows.append(
        "<p>3.2. Арендная плата устанавливается в соответствии с тарифами Арендодателя.</p>"
        "<p>3.3. Тариф применяется исходя из срока непрерывной аренды Автомобиля.</p>"
        "<p>3.4. Оплата аренды и внесение залога производится в день и время передачи автомобиля "
        "либо безналичным путём на счёт Арендодателя.</p>"
    )

    rows.append("<h2>4. Срок аренды. Место выдачи и получения автомобиля</h2>")
    rows.append("<table>")
    rows.append(
        f"<tr><td><b>4.1 Автомобиль выдан:</b></td>"
        f"<td><b>Дата:</b> {dmy(sa)}</td>"
        f"<td><b>Время:</b> {hm(sa)}</td>"
        f"<td><b>Место:</b> {v(pickup_place)}</td></tr>"
    )
    rows.append(
        f"<tr><td><b>4.2 Срок и место возврата:</b></td>"
        f"<td><b>Дата:</b> {dmy(ea)}</td>"
        f"<td><b>Время:</b> {hm(ea)}</td>"
        f"<td><b>Место:</b> {v(return_place)}</td></tr>"
    )
    rows.append("</table>")
    rows.append(
        "<p>4.3. При продлении срока аренды стороны заключают письменное соглашение либо при взаимном "
        "согласии вправе устно продлить договор (в т.ч. через сообщения).</p>"
    )

    if b.get("mileage_out") or b.get("fuel_out") or b.get("mileage_in") or b.get("fuel_in"):
        rows.append(
            f"<p><b>Показания при выдаче:</b> пробег {v(b.get('mileage_out'),'—')} км, "
            f"топливо {v(b.get('fuel_out'),'—')}. "
            f"<b>При возврате:</b> пробег {v(b.get('mileage_in'),'—')} км, "
            f"топливо {v(b.get('fuel_in'),'—')}.</p>"
        )

    rows.append("<h2>5. Права, обязанности и ответственность сторон</h2>")
    rows.append("<p><b>5.1. Права и обязанности Арендодателя</b></p>")
    rows.append(
        "<p>5.1.1. Арендодатель имеет право требовать возврата сданного в аренду транспортного средства "
        "в срок и в состоянии, соответствующем условиям Договора и Приложению № 1.</p>"
        "<p>5.1.2. Арендодатель имеет право требовать своевременной уплаты всех причитающихся платежей. "
        "При нарушении сроков оплаты Арендатор уплачивает неустойку 0,5% от суммы просроченного платежа "
        "за каждый день просрочки.</p>"
        "<p>5.1.3. Арендодатель обязан предоставить транспортное средство в состоянии, пригодном для "
        "эксплуатации, согласно Приложению № 1.</p>"
        "<p>5.1.4. Арендодатель обязан передать вместе с транспортным средством документы "
        "(свидетельство о регистрации, полис/документы, необходимые для эксплуатации).</p>"
    )
    rows.append("<p><b>5.2. Права и обязанности Арендатора</b></p>")
    rows.append(
        "<p>5.2.1. Предоставлять право управления только лицам, указанным в п. 1.5, если иное не "
        "согласовано сторонами.</p>"
        "<p>5.2.2–5.2.4. Использовать ТС по условиям Договора; вносить арендную плату в срок; "
        "осмотреть ТС до подписания акта приёма-передачи.</p>"
        "<p>5.2.5. Поддерживать ТС в рабочем состоянии. При просрочке возврата свыше 60 минут "
        "Арендатор уплачивает арендную плату за следующие сутки.</p>"
        "<p>5.2.6–5.2.8. Обеспечить сохранность автомобиля и комплектующих; при ущербе Арендодатель "
        "вправе удержать залог пропорционально ущербу; вернуть документы и принадлежности по акту.</p>"
        "<p>5.2.9. Не сдавать автомобиль в субаренду и не использовать для перевозок третьих лиц "
        "без согласия Арендодателя (штраф — трёхкратная суточная аренда).</p>"
        "<p>5.2.10. Не эксплуатировать автомобиль за пределами Калининградской области без письменного "
        "согласия Арендодателя (штраф — трёхкратная суточная аренда).</p>"
        "<p>5.2.11. Не использовать ТС для буксировки, езды с прицепом, по бездорожью и обучения вождению "
        "(штраф — суточная аренда).</p>"
        "<p>5.2.12. Вернуть автомобиль в чистом виде и исправном состоянии.</p>"
        "<p>5.2.14. Не курить в салоне (штраф 5 000 ₽).</p>"
    )
    rows.append(
        "<p><b>5.5. При ДТП / повреждении</b> Арендатор обязан немедленно вызвать ГИБДД, сообщить "
        "Арендодателю, зафиксировать данные участников и свидетелей, получить документы ГИБДД и "
        "передать их Арендодателю, предоставить автомобиль для осмотра.</p>"
        "<p>5.5.3. При выполнении п. 5.5 и признании Арендатора виновником — возмещение ущерба "
        "до 100 000 ₽ пропорционально размеру ущерба.</p>"
        "<p>5.5.4. Полное возмещение ущерба, если водитель был в состоянии опьянения, нарушены "
        "правила пожарной безопасности либо не выполнены п. 5.5–5.5.2.</p>"
    )

    rows.append("<h2>6. Ответственность сторон</h2>")
    rows.append(
        f"<p>6.1. При нарушении условий договора Арендатор несёт ответственность в размере залога "
        f"{deposit if deposit else 10000} ₽, а также за повреждения или кражу автомобиля / его частей.</p>"
        "<p>6.2. Повреждения салона, подвески или ходовой части, не связанные со страховым случаем, "
        "оплачиваются Арендатором по тарифам дилерской станции на выбор Арендодателя.</p>"
        "<p>6.3. Невозврат автомобиля в срок может квалифицироваться как незаконное завладение; "
        "Арендодатель вправе принудительно изъять ТС с отнесением расходов на Арендатора.</p>"
        "<p>6.4. При возврате с неполным баком Арендатор компенсирует стоимость топлива.</p>"
        "<p>6.5. Арендодатель не отвечает за вещи, оставленные в автомобиле.</p>"
        "<p>6.6. В остальных случаях стороны несут ответственность по законодательству РФ.</p>"
    )

    rows.append(
        "<h2>7. Форс-мажор</h2>"
        "<p>Стороны освобождаются от ответственности при обстоятельствах непреодолимой силы. "
        "Сторона, которая не может исполнить обязательство, извещает другую в течение 2 дней.</p>"
    )
    rows.append(
        "<h2>8. Изменение и расторжение договора</h2>"
        "<p>8.1. Изменения действительны при оформлении письменного соглашения.</p>"
        "<p>8.2. Каждая сторона вправе отказаться от договора, уведомив другую за сутки.</p>"
        "<p>8.3. При досрочном расторжении по инициативе Арендатора производится перерасчёт "
        "арендных платежей за фактический срок.</p>"
    )
    rows.append(
        "<h2>9. Разрешение споров</h2>"
        "<p>9.1. Споры разрешаются путём переговоров.</p>"
        "<p>9.2. Неурегулированные споры подлежат рассмотрению в суде по законодательству РФ.</p>"
    )
    rows.append(
        "<h2>10. Заключительные положения</h2>"
        "<p>10.1. Договор составлен в двух экземплярах, по одному для каждой стороны.</p>"
        "<p>10.2. Подписи сторон:</p>"
    )
    rows.append(
        "<div class=\"sign\">"
        f"<div><b>Арендодатель</b><br><br>____________________ / {v(lessor_name)} /</div>"
        f"<div><b>Арендатор</b><br><br>____________________ / {v(renter)} /</div>"
        "</div>"
    )
    rows.append(
        "<p class=\"muted\" style=\"margin-top:18px\">Приложение № 1 — Акт приёма-передачи "
        "(заполняется при выдаче/возврате). Комментарий к брони: "
        f"{v(b.get('comment'),'—')}</p>"
    )
    rows.append("<script>window.onload=function(){try{window.print()}catch(e){}}</script>")
    rows.append("</body></html>")
    return "".join(rows)



def admin_finance_export_csv_sync(start_day, end_day):
    import csv
    import io

    start_dt = local_dt(start_day, time(0, 0))
    end_dt = local_dt(end_day + timedelta(days=1), time(0, 0))
    con = db()
    try:
        with con.cursor() as cur:
            rows = cur.execute(
                """
                SELECT id, car_id, name, phone, status, start_at, end_at, total,
                       deposit_amount, deposit_status, contract_number, issued_at, returned_at
                FROM bookings
                WHERE status IN ('confirmed', 'completed')
                  AND start_at >= %s AND start_at < %s
                ORDER BY start_at
                """,
                (start_dt, end_dt),
            ).fetchall()
    finally:
        con.close()
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "ID", "Договор", "Авто", "Клиент", "Телефон", "Статус",
        "Начало", "Конец", "Сумма", "Залог", "Статус залога", "Выдача", "Возврат",
    ])
    for r in rows:
        w.writerow([
            r["id"],
            r.get("contract_number") or "",
            CARS.get(r["car_id"], {}).get("name", r["car_id"]),
            r.get("name") or "",
            r.get("phone") or "",
            r.get("status") or "",
            format_date_time(r["start_at"]) if r.get("start_at") else "",
            format_date_time(r["end_at"]) if r.get("end_at") else "",
            r.get("total") or 0,
            r.get("deposit_amount") or 0,
            r.get("deposit_status") or "",
            format_date_time(r["issued_at"]) if r.get("issued_at") else "",
            format_date_time(r["returned_at"]) if r.get("returned_at") else "",
        ])
    return buf.getvalue()


async def admin_manual_booking_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
    except Exception:
        return mini_json({"message": "Некорректный JSON"}, 400)
    try:
        result = await asyncio.to_thread(admin_manual_booking_sync, data)
    except Exception as exc:
        print(f"[MANUAL/BOOKING/API] {type(exc).__name__}: {exc}", flush=True)
        return mini_json({"message": f"Ошибка сервера: {exc}"}, 500)
    if not result.get("ok"):
        messages = {
            "car": "Выберите автомобиль",
            "dates": "Проверьте даты начала и окончания",
            "client": "Укажите клиента или ФИО и телефон",
            "overlap": "Период занят",
            "maintenance": "На период назначено ТО",
            "blacklisted": "Клиент в чёрном списке",
            "db": "Ошибка базы (проверьте миграции / поля брони)",
        }
        msg = messages.get(result.get("reason"), "Не удалось создать бронь")
        if result.get("error"):
            msg = f"{msg}: {result['error']}"
        return mini_json({"message": msg}, 409)
    return mini_json(result)


async def admin_templates_api(request):
    admin_web_auth_or_403(request)
    return mini_json({"templates": [{"id": t["id"], "title": t["title"]} for t in MESSAGE_TEMPLATES]})


async def admin_send_template_api(request):
    admin_web_auth_or_403(request)
    try:
        data = await request.json()
        bid = int(data.get("booking_id") or 0)
        tid = str(data.get("template_id") or "")
    except Exception:
        return mini_json({"message": "Некорректный запрос"}, 400)
    tpl = next((t for t in MESSAGE_TEMPLATES if t["id"] == tid), None)
    if not tpl or bid <= 0:
        return mini_json({"message": "Шаблон или бронь не найдены"}, 404)
    full = await asyncio.to_thread(admin_booking_full_sync, bid)
    if not full:
        return mini_json({"message": "Бронь не найдена"}, 404)
    b = full["booking"]
    car = CARS.get(b["car_id"], {}).get("name", b["car_id"])
    sa = ensure_tz(b["start_at"]) if b.get("start_at") else None
    ea = ensure_tz(b["end_at"]) if b.get("end_at") else None
    body = tpl["body"].format(
        name=b.get("name") or "клиент",
        bid=b["id"],
        car=car,
        start=format_date_time(sa) if sa else "—",
        end=format_date_time(ea) if ea else "—",
        total=money(b.get("total") or 0),
    )
    uid = b.get("user_id")
    if not uid:
        return mini_json({"message": "У брони нет Telegram user_id — отправьте текст вручную", "text": body}, 409)
    try:
        async with ClientSession() as session:
            await session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": uid, "text": body},
                timeout=ClientTimeout(total=15),
            )
    except Exception as exc:
        return mini_json({"message": f"Не отправлено: {exc}", "text": body}, 500)
    return mini_json({"ok": True, "text": body})


async def admin_contract_api(request):
    admin_web_auth_or_403(request)
    try:
        bid = int(request.query.get("id") or 0)
    except ValueError:
        raise web.HTTPBadRequest(text="bad id")
    html = await asyncio.to_thread(build_contract_html, bid)
    if not html:
        raise web.HTTPNotFound(text="booking not found")
    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={"Content-Disposition": f'inline; filename="contract_{bid}.html"'},
    )


async def admin_export_csv_api(request):
    admin_web_auth_or_403(request)
    today = datetime.now(TZ).date()
    try:
        start_day = date.fromisoformat(request.query.get("from") or today.replace(day=1).isoformat())
        end_day = date.fromisoformat(request.query.get("to") or today.isoformat())
    except ValueError:
        start_day, end_day = today.replace(day=1), today
    csv_data = await asyncio.to_thread(admin_finance_export_csv_sync, start_day, end_day)
    return web.Response(
        text=csv_data,
        content_type="text/csv",
        charset="utf-8",
        headers={"Content-Disposition": f'attachment; filename="balticar_{start_day}_{end_day}.csv"'},
    )


async def mini_app_routes(app):
    mini_add_location_columns()
    app.router.add_get("/app", mini_app)
    app.router.add_get("/admin", admin_web_page)
    app.router.add_get("/api/admin/calendar", admin_calendar_api)
    app.router.add_get("/api/admin/bookings", admin_bookings_api)
    app.router.add_post("/api/admin/booking/action", admin_booking_action_api)
    app.router.add_post("/api/admin/booking/fee", admin_booking_fee_api)
    app.router.add_post("/api/admin/block", admin_block_create_api)
    app.router.add_post("/api/admin/block/delete", admin_block_delete_api)
    app.router.add_get("/api/admin/finance", admin_finance_api)
    app.router.add_get("/api/admin/expenses", admin_expenses_api)
    app.router.add_post("/api/admin/expenses", admin_expenses_api)
    app.router.add_post("/api/admin/expenses/delete", admin_expense_delete_api)
    app.router.add_get("/api/admin/reviews", admin_reviews_api)
    app.router.add_get("/api/admin/planner", admin_planner_api)
    app.router.add_get("/api/admin/clients", admin_clients_api)
    app.router.add_get("/api/admin/client", admin_client_detail_api)
    app.router.add_post("/api/admin/client/action", admin_client_action_api)
    app.router.add_post("/api/admin/client/create", admin_client_create_api)
    app.router.add_get("/api/admin/cars", admin_cars_api)
    app.router.add_post("/api/admin/cars/save", admin_car_save_api)
    app.router.add_post("/api/admin/cars/delete", admin_car_delete_api)
    app.router.add_post("/api/admin/booking/manual", admin_manual_booking_api)
    app.router.add_get("/api/admin/templates", admin_templates_api)
    app.router.add_post("/api/admin/templates/send", admin_send_template_api)
    app.router.add_get("/api/admin/contract", admin_contract_api)
    app.router.add_get("/api/admin/export.csv", admin_export_csv_api)
    app.router.add_get("/photos/{name:.*}", mini_photo)
    app.router.add_get("/api/cars", mini_cars)
    app.router.add_get("/api/availability", mini_availability)
    app.router.add_get("/api/slots", mini_slots)
    app.router.add_get("/api/mybookings", mini_mybookings)
    app.router.add_get("/api/profile", mini_profile)
    app.router.add_post("/api/bookings", mini_bookings)
    app.router.add_post("/api/bookings/cancel", mini_cancel_booking)
    app.router.add_get("/api/reviews", mini_reviews_get)
    app.router.add_post("/api/reviews", mini_reviews_post)

# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
