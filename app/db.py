"""Хранение доставок, сотрудников и смен в SQLite."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .auth import hash_password, verify_password
from .shifts import KIND_TEXT, CashOp, Shift, delivery_business_day, opening_business_day


@dataclass
class Delivery:
    day: date
    time: str = ""
    items: str = ""
    recipient: str = ""
    phone: str = ""
    address: str = ""
    price: int = 0  # сколько взяли с покупателя за доставку
    courier_pay: int = 0  # сколько отдали курьеру
    delivered: bool = False
    id: int | None = None
    created_by: int | None = None  # id сотрудника, который добавил
    delivered_by: int | None = None  # id сотрудника, который отметил «Доставлено»
    created_by_name: str = ""
    delivered_by_name: str = ""
    source: str = ""  # откуда заказ: WhatsApp, Telegram, ВК …
    customer_contact: str = ""  # телефон, @логин или ссылка заказчика
    messages: str = ""  # какие сообщения уже отправляли: «departed,delivered»

    def sent(self, kind: str) -> bool:
        return kind in self.messages.split(",")

    @property
    def remainder(self) -> int:
        """Сколько осталось магазину."""
        return self.price - self.courier_pay


@dataclass
class User:
    id: int
    login: str
    name: str
    is_admin: bool


@dataclass
class Totals:
    count: int = 0
    delivered: int = 0
    price: int = 0
    courier_pay: int = 0

    @property
    def remainder(self) -> int:
        return self.price - self.courier_pay


SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT    NOT NULL,
    time        TEXT    NOT NULL DEFAULT '',
    items       TEXT    NOT NULL DEFAULT '',
    recipient   TEXT    NOT NULL DEFAULT '',
    phone       TEXT    NOT NULL DEFAULT '',
    address     TEXT    NOT NULL DEFAULT '',
    price       INTEGER NOT NULL DEFAULT 0,
    courier_pay INTEGER NOT NULL DEFAULT 0,
    delivered   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_deliveries_day ON deliveries(day);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT    NOT NULL DEFAULT '',
    salt          BLOB    NOT NULL,
    password_hash BLOB    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shifts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    day           TEXT    NOT NULL,
    opened_at     TEXT    NOT NULL,
    opening_cash  INTEGER NOT NULL DEFAULT 0,
    closed_at     TEXT,
    closing_cash  INTEGER,
    cash_sales    INTEGER NOT NULL DEFAULT 0,
    delivery_cash INTEGER NOT NULL DEFAULT 0,
    courier_cash  INTEGER NOT NULL DEFAULT 0,
    note          TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_shifts_day ON shifts(day);
CREATE TABLE IF NOT EXISTS cash_ops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id   INTEGER NOT NULL,
    kind       TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    reason     TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    created_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cash_ops_shift ON cash_ops(shift_id);
"""

SHIFT_SELECT = """
SELECT s.id, s.user_id, COALESCE(NULLIF(u.name, ''), u.login, ''), s.day, s.opened_at,
       s.opening_cash, s.closed_at, s.closing_cash, s.cash_sales, s.delivery_cash,
       s.courier_cash, s.note,
       COALESCE((SELECT SUM(amount) FROM cash_ops c WHERE c.shift_id = s.id AND c.kind = 'expense'), 0),
       COALESCE((SELECT SUM(amount) FROM cash_ops c WHERE c.shift_id = s.id AND c.kind = 'withdrawal'), 0)
FROM shifts s LEFT JOIN users u ON u.id = s.user_id
"""


def _ts(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _row_to_shift(r) -> Shift:
    return Shift(
        id=r[0], user_id=r[1], user_name=r[2], day=date.fromisoformat(r[3]),
        opened_at=datetime.fromisoformat(r[4]), opening_cash=r[5],
        closed_at=datetime.fromisoformat(r[6]) if r[6] else None, closing_cash=r[7],
        cash_sales=r[8], delivery_cash=r[9], courier_cash=r[10], note=r[11],
        expenses=r[12], withdrawals=r[13],
    )

# Столбцы, добавленные после первой версии: старые базы дополняются при запуске.
MIGRATIONS = {
    "created_by": "ALTER TABLE deliveries ADD COLUMN created_by INTEGER",
    "delivered_by": "ALTER TABLE deliveries ADD COLUMN delivered_by INTEGER",
    "source": "ALTER TABLE deliveries ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "customer_contact": "ALTER TABLE deliveries ADD COLUMN customer_contact TEXT NOT NULL DEFAULT ''",
    "messages": "ALTER TABLE deliveries ADD COLUMN messages TEXT NOT NULL DEFAULT ''",
}

COLUMNS = ("d.id, d.day, d.time, d.items, d.recipient, d.phone, d.address, d.price,"
           " d.courier_pay, d.delivered, d.created_by, d.delivered_by,"
           " COALESCE(NULLIF(uc.name, ''), uc.login, ''), COALESCE(NULLIF(ud.name, ''), ud.login, ''),"
           " d.source, d.customer_contact, d.messages")
FROM = ("deliveries d LEFT JOIN users uc ON uc.id = d.created_by"
        " LEFT JOIN users ud ON ud.id = d.delivered_by")


def _row_to_delivery(row) -> Delivery:
    return Delivery(
        id=row[0],
        day=date.fromisoformat(row[1]),
        time=row[2],
        items=row[3],
        recipient=row[4],
        phone=row[5],
        address=row[6],
        price=row[7],
        courier_pay=row[8],
        delivered=bool(row[9]),
        created_by=row[10],
        delivered_by=row[11],
        created_by_name=row[12],
        delivered_by_name=row[13],
        source=row[14],
        customer_contact=row[15],
        messages=row[16],
    )


class UserError(Exception):
    """Ошибка с понятным пользователю текстом."""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(deliveries)")}
        for column, sql in MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(sql)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def backup(self, path: str):
        """Целостная копия базы (даже если с ней сейчас работают)."""
        dest = sqlite3.connect(path)
        try:
            self.conn.backup(dest)
        finally:
            dest.close()

    def add(self, d: Delivery) -> int:
        cur = self.conn.execute(
            "INSERT INTO deliveries (day, time, items, recipient, phone, address,"
            " price, courier_pay, delivered, created_by, delivered_by,"
            " source, customer_contact, messages)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered), d.created_by,
             d.delivered_by if d.delivered else None, d.source, d.customer_contact, d.messages),
        )
        self.conn.commit()
        d.id = cur.lastrowid
        return d.id

    def update(self, d: Delivery):
        self.conn.execute(
            "UPDATE deliveries SET day=?, time=?, items=?, recipient=?, phone=?,"
            " address=?, price=?, courier_pay=?, delivered=?, delivered_by=?,"
            " source=?, customer_contact=? WHERE id=?",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered),
             d.delivered_by if d.delivered else None, d.source, d.customer_contact, d.id),
        )
        self.conn.commit()

    def set_delivered(self, delivery_id: int, delivered: bool, user_id: int | None = None):
        self.conn.execute(
            "UPDATE deliveries SET delivered=?, delivered_by=? WHERE id=?",
            (int(delivered), user_id if delivered else None, delivery_id),
        )
        self.conn.commit()

    def mark_message(self, delivery_id: int, kind: str):
        row = self.conn.execute("SELECT messages FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if row is None:
            return
        kinds = [k for k in row[0].split(",") if k]
        if kind not in kinds:
            kinds.append(kind)
        self.conn.execute("UPDATE deliveries SET messages=? WHERE id=?", (",".join(kinds), delivery_id))
        self.conn.commit()

    def delete(self, delivery_id: int):
        self.conn.execute("DELETE FROM deliveries WHERE id=?", (delivery_id,))
        self.conn.commit()

    def for_day(self, day: date) -> list[Delivery]:
        rows = self.conn.execute(
            f"SELECT {COLUMNS} FROM {FROM} WHERE d.day=? ORDER BY d.time, d.id",
            (day.isoformat(),),
        ).fetchall()
        return [_row_to_delivery(r) for r in rows]

    def counts_for_month(self, year: int, month: int) -> dict[date, tuple[int, int]]:
        """Для каждого дня месяца: (всего доставок, из них доставлено)."""
        prefix = f"{year:04d}-{month:02d}-%"
        rows = self.conn.execute(
            "SELECT day, COUNT(*), SUM(delivered) FROM deliveries"
            " WHERE day LIKE ? GROUP BY day",
            (prefix,),
        ).fetchall()
        return {date.fromisoformat(r[0]): (r[1], r[2] or 0) for r in rows}

    def _totals(self, where: str, param: str) -> Totals:
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(delivered), 0), COALESCE(SUM(price), 0),"
            f" COALESCE(SUM(courier_pay), 0) FROM deliveries WHERE {where}",
            (param,),
        ).fetchone()
        return Totals(*row)

    def totals_for_day(self, day: date) -> Totals:
        return self._totals("day = ?", day.isoformat())

    def totals_for_month(self, year: int, month: int) -> Totals:
        return self._totals("day LIKE ?", f"{year:04d}-{month:02d}-%")

    # ----- сотрудники -----

    def has_users(self) -> bool:
        return self.conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def users(self) -> list[User]:
        rows = self.conn.execute(
            "SELECT id, login, name, is_admin FROM users ORDER BY is_admin DESC, login"
        ).fetchall()
        return [User(r[0], r[1], r[2], bool(r[3])) for r in rows]

    def add_user(self, login: str, name: str, password: str, is_admin: bool = False) -> User:
        login = login.strip()
        if not login:
            raise UserError("Введите логин.")
        if not password:
            raise UserError("Введите пароль.")
        salt, digest = hash_password(password)
        try:
            cur = self.conn.execute(
                "INSERT INTO users (login, name, salt, password_hash, is_admin)"
                " VALUES (?, ?, ?, ?, ?)",
                (login, name.strip(), salt, digest, int(is_admin)),
            )
        except sqlite3.IntegrityError:
            raise UserError(f"Логин «{login}» уже занят.") from None
        self.conn.commit()
        return User(cur.lastrowid, login, name.strip(), is_admin)

    def authenticate(self, login: str, password: str) -> User | None:
        row = self.conn.execute(
            "SELECT id, login, name, is_admin, salt, password_hash FROM users WHERE login=?",
            (login.strip(),),
        ).fetchone()
        if row and verify_password(password, row[4], row[5]):
            return User(row[0], row[1], row[2], bool(row[3]))
        return None

    def set_password(self, user_id: int, password: str):
        if not password:
            raise UserError("Введите пароль.")
        salt, digest = hash_password(password)
        self.conn.execute(
            "UPDATE users SET salt=?, password_hash=? WHERE id=?", (salt, digest, user_id)
        )
        self.conn.commit()

    def update_user(self, user_id: int, name: str, is_admin: bool):
        if not is_admin:
            self._ensure_other_admin(user_id)
        self.conn.execute(
            "UPDATE users SET name=?, is_admin=? WHERE id=?", (name.strip(), int(is_admin), user_id)
        )
        self.conn.commit()

    def delete_user(self, user_id: int):
        self._ensure_other_admin(user_id)
        # Доставки остаются, просто без автора.
        self.conn.execute("UPDATE deliveries SET created_by=NULL WHERE created_by=?", (user_id,))
        self.conn.execute("UPDATE deliveries SET delivered_by=NULL WHERE delivered_by=?", (user_id,))
        self.conn.execute("UPDATE shifts SET user_id=NULL WHERE user_id=?", (user_id,))
        self.conn.execute("UPDATE cash_ops SET created_by=NULL WHERE created_by=?", (user_id,))
        self.conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        self.conn.commit()

    def _ensure_other_admin(self, user_id: int):
        others = self.conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin=1 AND id<>?", (user_id,)
        ).fetchone()[0]
        is_admin = self.conn.execute(
            "SELECT is_admin FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if is_admin and is_admin[0] and others == 0:
            raise UserError("Должен остаться хотя бы один администратор.")

    # ----- смены и касса -----

    def open_shift(self, user_id: int, opening_cash: int, now: datetime, change: time) -> Shift:
        if opening_cash < 0:
            raise UserError("Сумма в кассе не может быть отрицательной.")
        cur = self.conn.execute(
            "INSERT INTO shifts (user_id, day, opened_at, opening_cash) VALUES (?, ?, ?, ?)",
            (user_id, opening_business_day(now, change).isoformat(), _ts(now), opening_cash),
        )
        self.conn.commit()
        return self.get_shift(cur.lastrowid)

    def get_shift(self, shift_id: int) -> Shift | None:
        row = self.conn.execute(SHIFT_SELECT + " WHERE s.id = ?", (shift_id,)).fetchone()
        return _row_to_shift(row) if row else None

    def current_shift(self, user_id: int) -> Shift | None:
        """Последняя несданная смена сотрудника."""
        row = self.conn.execute(
            SHIFT_SELECT + " WHERE s.user_id = ? AND s.closed_at IS NULL"
            " ORDER BY s.opened_at DESC, s.id DESC LIMIT 1", (user_id,),
        ).fetchone()
        return _row_to_shift(row) if row else None

    def close_shift(self, shift_id: int, now: datetime, closing_cash: int, cash_sales: int,
                    delivery_cash: int, courier_cash: int, note: str = "") -> Shift:
        if min(closing_cash, cash_sales, delivery_cash, courier_cash) < 0:
            raise UserError("Суммы не могут быть отрицательными.")
        cur = self.conn.execute(
            "UPDATE shifts SET closed_at=?, closing_cash=?, cash_sales=?, delivery_cash=?,"
            " courier_cash=?, note=? WHERE id=? AND closed_at IS NULL",
            (_ts(now), closing_cash, cash_sales, delivery_cash, courier_cash, note.strip(), shift_id),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            raise UserError("Эта смена уже сдана.")
        return self.get_shift(shift_id)

    def previous_shift(self, shift: Shift) -> Shift | None:
        """Смена, которая шла перед этой (у неё принимали кассу)."""
        return self.shift_before(shift.opened_at, shift.id)

    def shift_before(self, moment: datetime, before_id: int | None = None) -> Shift | None:
        ts = _ts(moment)
        if before_id is None:
            row = self.conn.execute(
                SHIFT_SELECT + " WHERE s.opened_at <= ? ORDER BY s.opened_at DESC, s.id DESC LIMIT 1",
                (ts,)).fetchone()
        else:
            row = self.conn.execute(
                SHIFT_SELECT + " WHERE (s.opened_at < ? OR (s.opened_at = ? AND s.id < ?))"
                " ORDER BY s.opened_at DESC, s.id DESC LIMIT 1", (ts, ts, before_id)).fetchone()
        return _row_to_shift(row) if row else None

    def handover_difference(self, shift: Shift) -> int | None:
        """Принял − сдал предыдущий: минус — при приёме денег оказалось меньше."""
        prev = self.previous_shift(shift)
        if prev is None or prev.closing_cash is None:
            return None
        return shift.opening_cash - prev.closing_cash

    def shifts_between(self, first: date, last: date, user_id: int | None = None) -> list[Shift]:
        sql = SHIFT_SELECT + " WHERE s.day BETWEEN ? AND ?"
        params: list = [first.isoformat(), last.isoformat()]
        if user_id is not None:
            sql += " AND s.user_id = ?"
            params.append(user_id)
        rows = self.conn.execute(sql + " ORDER BY s.opened_at, s.id", params).fetchall()
        return [_row_to_shift(r) for r in rows]

    def add_cash_op(self, shift_id: int, kind: str, amount: int, reason: str,
                    user_id: int | None, now: datetime) -> int:
        if kind not in KIND_TEXT:
            raise UserError("Неизвестный тип операции.")
        if amount <= 0:
            raise UserError("Введите сумму.")
        if not reason.strip():
            raise UserError("Напишите причину." if kind == "expense" else "Напишите, кто забрал деньги.")
        cur = self.conn.execute(
            "INSERT INTO cash_ops (shift_id, kind, amount, reason, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?)", (shift_id, kind, amount, reason.strip(), _ts(now), user_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def cash_ops(self, shift_id: int) -> list[CashOp]:
        rows = self.conn.execute(
            "SELECT c.id, c.kind, c.amount, c.reason, c.created_at,"
            " COALESCE(NULLIF(u.name, ''), u.login, '')"
            " FROM cash_ops c LEFT JOIN users u ON u.id = c.created_by"
            " WHERE c.shift_id = ? ORDER BY c.created_at, c.id", (shift_id,),
        ).fetchall()
        return [CashOp(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4]), r[5]) for r in rows]

    def delivery_cash_for_shift(self, day: date, change: time) -> tuple[int, int]:
        """(взяли за доставки, отдали курьерам) по доставкам, которые приходятся на смену."""
        candidates = self.for_day(day) + self.for_day(day + timedelta(days=1))
        own = [d for d in candidates if delivery_business_day(d, change) == day]
        return sum(d.price for d in own), sum(d.courier_pay for d in own)
