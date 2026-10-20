"""Хранение доставок в SQLite."""

import sqlite3
from dataclasses import dataclass
from datetime import date

from .auth import hash_password, verify_password


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
"""

# Столбцы, добавленные после первой версии: старые базы дополняются при запуске.
MIGRATIONS = {
    "created_by": "ALTER TABLE deliveries ADD COLUMN created_by INTEGER",
    "delivered_by": "ALTER TABLE deliveries ADD COLUMN delivered_by INTEGER",
}

COLUMNS = ("d.id, d.day, d.time, d.items, d.recipient, d.phone, d.address, d.price,"
           " d.courier_pay, d.delivered, d.created_by, d.delivered_by,"
           " COALESCE(NULLIF(uc.name, ''), uc.login, ''), COALESCE(NULLIF(ud.name, ''), ud.login, '')")
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
    )


class UserError(Exception):
    """Ошибка с понятным пользователю текстом."""


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(deliveries)")}
        for column, sql in MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(sql)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def add(self, d: Delivery) -> int:
        cur = self.conn.execute(
            "INSERT INTO deliveries (day, time, items, recipient, phone, address,"
            " price, courier_pay, delivered, created_by, delivered_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered), d.created_by,
             d.delivered_by if d.delivered else None),
        )
        self.conn.commit()
        d.id = cur.lastrowid
        return d.id

    def update(self, d: Delivery):
        self.conn.execute(
            "UPDATE deliveries SET day=?, time=?, items=?, recipient=?, phone=?,"
            " address=?, price=?, courier_pay=?, delivered=?, delivered_by=? WHERE id=?",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered),
             d.delivered_by if d.delivered else None, d.id),
        )
        self.conn.commit()

    def set_delivered(self, delivery_id: int, delivered: bool, user_id: int | None = None):
        self.conn.execute(
            "UPDATE deliveries SET delivered=?, delivered_by=? WHERE id=?",
            (int(delivered), user_id if delivered else None, delivery_id),
        )
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
