"""Хранение доставок в SQLite."""

import sqlite3
from dataclasses import dataclass
from datetime import date


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

    @property
    def remainder(self) -> int:
        """Сколько осталось магазину."""
        return self.price - self.courier_pay


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
"""

COLUMNS = "id, day, time, items, recipient, phone, address, price, courier_pay, delivered"


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
    )


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def add(self, d: Delivery) -> int:
        cur = self.conn.execute(
            "INSERT INTO deliveries (day, time, items, recipient, phone, address,"
            " price, courier_pay, delivered) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered)),
        )
        self.conn.commit()
        d.id = cur.lastrowid
        return d.id

    def update(self, d: Delivery):
        self.conn.execute(
            "UPDATE deliveries SET day=?, time=?, items=?, recipient=?, phone=?,"
            " address=?, price=?, courier_pay=?, delivered=? WHERE id=?",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered), d.id),
        )
        self.conn.commit()

    def set_delivered(self, delivery_id: int, delivered: bool):
        self.conn.execute(
            "UPDATE deliveries SET delivered=? WHERE id=?", (int(delivered), delivery_id)
        )
        self.conn.commit()

    def delete(self, delivery_id: int):
        self.conn.execute("DELETE FROM deliveries WHERE id=?", (delivery_id,))
        self.conn.commit()

    def for_day(self, day: date) -> list[Delivery]:
        rows = self.conn.execute(
            f"SELECT {COLUMNS} FROM deliveries WHERE day=? ORDER BY time, id",
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
