"""Хранение доставок, сотрудников и смен в SQLite."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .auth import hash_password, verify_password
from .shifts import (
    KIND_TEXT, Adjustment, CashOp, ChecklistItem, Duty, Penalty, Shift, delivery_business_day,
    opening_business_day,
)


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
    delete_requested_at: datetime | None = None  # сотрудник попросил удалить — ждёт решения администратора
    delete_requested_by_name: str = ""
    assembled: bool = False  # букет собран (доставленный — всегда собран)
    assembled_by: int | None = None
    assembled_at: datetime | None = None
    assembled_by_name: str = ""
    client_chat: str = ""  # заказ из бота клиентов: чат заказчика (статусы ему уходят сами)
    photos: int = 0  # сколько фото букета прикреплено

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
class Announcement:
    id: int
    text: str
    created_at: datetime
    readers: list[tuple[str, datetime]] = field(default_factory=list)  # (кто, когда прочитал)


@dataclass
class Totals:
    count: int = 0
    delivered: int = 0
    price: int = 0
    courier_pay: int = 0
    assembled: int = 0  # собрано (в том числе уже доставленные)

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
CREATE TABLE IF NOT EXISTS water_changes (
    day        TEXT PRIMARY KEY,
    user_id    INTEGER,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS salary_adjustments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    month      TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    reason     TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    created_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_salary_adj ON salary_adjustments(user_id, month);
CREATE TABLE IF NOT EXISTS telegram_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT UNIQUE,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at    TEXT
);
CREATE TABLE IF NOT EXISTS telegram_chats (
    chat_id   TEXT PRIMARY KEY,
    title     TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS duties (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    text     TEXT    NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_time ON notifications(created_at);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT    NOT NULL,
    user_id   INTEGER,
    user_name TEXT    NOT NULL DEFAULT '',
    action    TEXT    NOT NULL,
    details   TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(at);
CREATE TABLE IF NOT EXISTS penalties (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id   INTEGER NOT NULL,
    user_id    INTEGER,
    kind_id    INTEGER NOT NULL DEFAULT 0,
    title      TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    created_by TEXT    NOT NULL DEFAULT '',
    UNIQUE (shift_id, title)
);
CREATE INDEX IF NOT EXISTS idx_penalties_user ON penalties(user_id);
CREATE TABLE IF NOT EXISTS writeoff_items (
    shift_id INTEGER NOT NULL,
    reason   TEXT    NOT NULL,
    amount   INTEGER NOT NULL,
    PRIMARY KEY (shift_id, reason)
);
CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id  INTEGER UNIQUE,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS announcement_reads (
    announcement_id INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    read_at         TEXT    NOT NULL,
    PRIMARY KEY (announcement_id, user_id)
);
CREATE TABLE IF NOT EXISTS work_phones (
    chat_id   TEXT PRIMARY KEY,
    linked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_files (
    name TEXT PRIMARY KEY,
    data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS showcase (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    price       INTEGER NOT NULL DEFAULT 0,
    photo       BLOB,
    file_id     TEXT    NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    position    INTEGER NOT NULL DEFAULT 0,
    single      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS client_chats (
    chat_id   TEXT PRIMARY KEY,
    name      TEXT NOT NULL DEFAULT '',
    username  TEXT NOT NULL DEFAULT '',
    phone     TEXT NOT NULL DEFAULT '',
    state     TEXT NOT NULL DEFAULT '',
    data      TEXT NOT NULL DEFAULT '{}',
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    showcase_id  INTEGER,
    items        TEXT    NOT NULL DEFAULT '',
    day          TEXT    NOT NULL,
    time         TEXT    NOT NULL DEFAULT '',
    address      TEXT    NOT NULL DEFAULT '',
    recipient    TEXT    NOT NULL DEFAULT '',
    client_phone TEXT    NOT NULL DEFAULT '',
    card         TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'new',
    amount       INTEGER NOT NULL DEFAULT 0,
    delivery_fee INTEGER NOT NULL DEFAULT 0,
    paid_photo   BLOB,
    delivery_id  INTEGER,
    note         TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS client_messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   TEXT    NOT NULL,
    at        TEXT    NOT NULL,
    incoming  INTEGER NOT NULL,
    text      TEXT    NOT NULL DEFAULT '',
    photo     BLOB,
    author    TEXT    NOT NULL DEFAULT '',
    read      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_client_messages_chat ON client_messages(chat_id, id);
CREATE TABLE IF NOT EXISTS client_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    markup     TEXT NOT NULL DEFAULT '',
    photo      BLOB,
    file_id    TEXT NOT NULL DEFAULT '',
    ref        TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at    TEXT
);
CREATE TABLE IF NOT EXISTS delivery_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL,
    at          TEXT    NOT NULL,
    photo       BLOB    NOT NULL,
    by_name     TEXT    NOT NULL DEFAULT '',
    sent_admins INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shift_duties (
    shift_id INTEGER NOT NULL,
    duty_id  INTEGER NOT NULL,
    text     TEXT    NOT NULL,
    done_at  TEXT,
    PRIMARY KEY (shift_id, duty_id)
);
"""

# Столбцы смен, добавленные позже.
SHIFT_MIGRATIONS = {
    column: f"ALTER TABLE shifts ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
    for column in ("revenue", "flowers", "terminal", "qr", "deliveries", "writeoff")
}

SHOWCASE_MIGRATIONS = {
    "single": "ALTER TABLE showcase ADD COLUMN single INTEGER NOT NULL DEFAULT 0",
}

PENALTY_MIGRATIONS = {
    "dispute_text": "ALTER TABLE penalties ADD COLUMN dispute_text TEXT NOT NULL DEFAULT ''",
    "dispute_at": "ALTER TABLE penalties ADD COLUMN dispute_at TEXT",
    "dispute_result": "ALTER TABLE penalties ADD COLUMN dispute_result TEXT NOT NULL DEFAULT ''",
}

SHIFT_SELECT = """
SELECT s.id, s.user_id, COALESCE(NULLIF(u.name, ''), u.login, ''), s.day, s.opened_at,
       s.opening_cash, s.closed_at, s.closing_cash, s.cash_sales, s.delivery_cash,
       s.courier_cash, s.note,
       COALESCE((SELECT SUM(amount) FROM cash_ops c WHERE c.shift_id = s.id AND c.kind = 'expense'), 0),
       COALESCE((SELECT SUM(amount) FROM cash_ops c WHERE c.shift_id = s.id AND c.kind = 'withdrawal'), 0),
       s.revenue, s.flowers, s.terminal, s.qr, s.deliveries,
       CASE WHEN s.closed_at IS NULL THEN
                (SELECT COUNT(*) FROM shift_duties sd JOIN duties d ON d.id = sd.duty_id
                 WHERE sd.shift_id = s.id AND sd.done_at IS NOT NULL AND d.active = 1)
            ELSE (SELECT COUNT(*) FROM shift_duties sd WHERE sd.shift_id = s.id AND sd.done_at IS NOT NULL) END,
       CASE WHEN s.closed_at IS NULL THEN (SELECT COUNT(*) FROM duties WHERE active = 1)
            ELSE (SELECT COUNT(*) FROM shift_duties sd WHERE sd.shift_id = s.id) END,
       s.writeoff
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
        expenses=r[12], withdrawals=r[13], revenue=r[14], flowers=r[15], terminal=r[16], qr=r[17],
        deliveries=r[18], duties_done=r[19], duties_total=r[20], writeoff=r[21],
    )

# Столбцы, добавленные после первой версии: старые базы дополняются при запуске.
MIGRATIONS = {
    "created_by": "ALTER TABLE deliveries ADD COLUMN created_by INTEGER",
    "delivered_by": "ALTER TABLE deliveries ADD COLUMN delivered_by INTEGER",
    "source": "ALTER TABLE deliveries ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "customer_contact": "ALTER TABLE deliveries ADD COLUMN customer_contact TEXT NOT NULL DEFAULT ''",
    "messages": "ALTER TABLE deliveries ADD COLUMN messages TEXT NOT NULL DEFAULT ''",
    "delete_requested_by": "ALTER TABLE deliveries ADD COLUMN delete_requested_by INTEGER",
    "delete_requested_at": "ALTER TABLE deliveries ADD COLUMN delete_requested_at TEXT",
    "assembled": "ALTER TABLE deliveries ADD COLUMN assembled INTEGER NOT NULL DEFAULT 0",
    "assembled_by": "ALTER TABLE deliveries ADD COLUMN assembled_by INTEGER",
    "assembled_at": "ALTER TABLE deliveries ADD COLUMN assembled_at TEXT",
    "client_chat": "ALTER TABLE deliveries ADD COLUMN client_chat TEXT NOT NULL DEFAULT ''",
}

USER_MIGRATIONS = {
    "tg_chat": "ALTER TABLE users ADD COLUMN tg_chat TEXT NOT NULL DEFAULT ''",  # Telegram флориста в боте клиентов
    "tg_code": "ALTER TABLE users ADD COLUMN tg_code TEXT NOT NULL DEFAULT ''",  # код для привязки
}

COLUMNS = ("d.id, d.day, d.time, d.items, d.recipient, d.phone, d.address, d.price,"
           " d.courier_pay, d.delivered, d.created_by, d.delivered_by,"
           " COALESCE(NULLIF(uc.name, ''), uc.login, ''), COALESCE(NULLIF(ud.name, ''), ud.login, ''),"
           " d.source, d.customer_contact, d.messages, d.delete_requested_at,"
           " COALESCE(NULLIF(ur.name, ''), ur.login, ''),"
           " d.assembled, d.assembled_by, d.assembled_at, COALESCE(NULLIF(ua.name, ''), ua.login, ''),"
           " d.client_chat, (SELECT COUNT(*) FROM delivery_photos p WHERE p.delivery_id = d.id)")
FROM = ("deliveries d LEFT JOIN users uc ON uc.id = d.created_by"
        " LEFT JOIN users ud ON ud.id = d.delivered_by"
        " LEFT JOIN users ur ON ur.id = d.delete_requested_by"
        " LEFT JOIN users ua ON ua.id = d.assembled_by")


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
        delete_requested_at=datetime.fromisoformat(row[17]) if row[17] else None,
        delete_requested_by_name=row[18],
        assembled=bool(row[19]) or bool(row[9]),
        assembled_by=row[20],
        assembled_at=datetime.fromisoformat(row[21]) if row[21] else None,
        assembled_by_name=row[22],
        client_chat=row[23],
        photos=row[24],
    )


class UserError(Exception):
    """Ошибка с понятным пользователю текстом."""


# ----- бот клиентов: витрина, заказы, переписка, фото букетов -----

ORDER_STATUS = {
    "new": "новый — назначьте сумму",
    "priced": "ждёт оплаты",
    "paid_claimed": "клиент оплатил — проверьте",
    "confirmed": "принят",
    "rejected": "отклонён",
}


@dataclass
class ShowcaseItem:
    id: int
    title: str
    description: str = ""
    price: int = 0
    photo: bytes | None = None
    file_id: str = ""
    active: bool = True
    position: int = 0
    single: bool = False  # готовый букет в одном экземпляре: заказали — прячется с витрины сам


@dataclass
class ClientChat:
    chat_id: str
    name: str = ""
    username: str = ""
    phone: str = ""
    state: str = ""
    data: dict = field(default_factory=dict)
    last_seen: datetime | None = None


@dataclass
class ClientOrder:
    id: int
    chat_id: str
    created_at: datetime
    updated_at: datetime
    day: date
    time: str = ""
    items: str = ""
    address: str = ""
    recipient: str = ""
    client_phone: str = ""
    card: str = ""
    status: str = "new"
    amount: int = 0
    delivery_fee: int = 0
    has_paid_photo: bool = False
    delivery_id: int | None = None
    showcase_id: int | None = None
    note: str = ""
    client_name: str = ""


@dataclass
class ClientMessage:
    id: int
    chat_id: str
    at: datetime
    incoming: bool
    text: str
    has_photo: bool = False
    author: str = ""
    read: bool = False


ORDER_SELECT = (
    "SELECT o.id, o.chat_id, o.created_at, o.updated_at, o.day, o.time, o.items, o.address, o.recipient,"
    " o.client_phone, o.card, o.status, o.amount, o.delivery_fee, o.paid_photo IS NOT NULL, o.delivery_id,"
    " o.showcase_id, o.note, COALESCE(NULLIF(c.name, ''), c.username, '')"
    " FROM client_orders o LEFT JOIN client_chats c ON c.chat_id = o.chat_id")


def _row_to_order(r) -> ClientOrder:
    return ClientOrder(id=r[0], chat_id=r[1], created_at=datetime.fromisoformat(r[2]),
                       updated_at=datetime.fromisoformat(r[3]), day=date.fromisoformat(r[4]), time=r[5],
                       items=r[6], address=r[7], recipient=r[8], client_phone=r[9], card=r[10], status=r[11],
                       amount=r[12], delivery_fee=r[13], has_paid_photo=bool(r[14]), delivery_id=r[15],
                       showcase_id=r[16], note=r[17], client_name=r[18])


class ClientStore:
    """Методы для бота клиентов (подмешиваются в Database)."""

    conn: sqlite3.Connection

    # --- витрина ---

    def showcase(self, only_active: bool = False) -> list[ShowcaseItem]:
        sql = "SELECT id, title, description, price, photo, file_id, active, position, single FROM showcase"
        if only_active:
            sql += " WHERE active = 1"
        rows = self.conn.execute(sql + " ORDER BY position, id").fetchall()
        return [ShowcaseItem(r[0], r[1], r[2], r[3], r[4], r[5], bool(r[6]), r[7], bool(r[8])) for r in rows]

    def get_showcase_item(self, item_id: int) -> ShowcaseItem | None:
        return next((i for i in self.showcase() if i.id == item_id), None)

    def save_showcase_item(self, item: ShowcaseItem) -> int:
        if not item.title.strip():
            raise UserError("Напишите название букета.")
        if item.price < 0:
            raise UserError("Цена не может быть отрицательной.")
        if item.id:
            old = self.get_showcase_item(item.id)
            file_id = item.file_id if old is None or old.photo == item.photo else ""  # новое фото — новый file_id
            self.conn.execute("UPDATE showcase SET title=?, description=?, price=?, photo=?, file_id=?, active=?,"
                              " single=? WHERE id=?", (item.title.strip(), item.description.strip(), item.price,
                                                       item.photo, file_id, int(item.active), int(item.single),
                                                       item.id))
        else:
            position = self.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM showcase").fetchone()[0]
            cur = self.conn.execute(
                "INSERT INTO showcase (title, description, price, photo, active, position, single)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.title.strip(), item.description.strip(), item.price, item.photo, int(item.active), position,
                 int(item.single)))
            item.id = cur.lastrowid
        self.conn.commit()
        return item.id

    def set_showcase_file_id(self, item_id: int, file_id: str):
        """Telegram запомнил фото — дальше отправляем по номеру, не загружая заново."""
        self.conn.execute("UPDATE showcase SET file_id=? WHERE id=?", (file_id, item_id))
        self.conn.commit()

    def set_file(self, name: str, data: bytes | None):
        """Файл программы (например, QR-код для оплаты). None — удалить."""
        if data is None:
            self.conn.execute("DELETE FROM app_files WHERE name=?", (name,))
        else:
            self.conn.execute("INSERT INTO app_files (name, data) VALUES (?, ?)"
                              " ON CONFLICT (name) DO UPDATE SET data=excluded.data", (name, data))
        self.conn.commit()

    def get_file(self, name: str) -> bytes | None:
        r = self.conn.execute("SELECT data FROM app_files WHERE name=?", (name,)).fetchone()
        return r[0] if r else None

    def reset_showcase_file_ids(self):
        """Другой бот — старые номера фото ему не подходят, загрузим заново."""
        self.conn.execute("UPDATE showcase SET file_id=''")
        self.conn.commit()

    def delete_showcase_item(self, item_id: int):
        self.conn.execute("DELETE FROM showcase WHERE id=?", (item_id,))
        self.conn.commit()

    def move_showcase_item(self, item_id: int, step: int):
        items = self.showcase()
        index = next((i for i, x in enumerate(items) if x.id == item_id), None)
        if index is None or not 0 <= index + step < len(items):
            return
        items[index], items[index + step] = items[index + step], items[index]
        for position, x in enumerate(items):
            self.conn.execute("UPDATE showcase SET position=? WHERE id=?", (position, x.id))
        self.conn.commit()

    # --- клиенты ---

    def client_chat(self, chat_id: str) -> ClientChat | None:
        r = self.conn.execute("SELECT chat_id, name, username, phone, state, data, last_seen FROM client_chats"
                              " WHERE chat_id=?", (str(chat_id),)).fetchone()
        if r is None:
            return None
        try:
            data = json.loads(r[5] or "{}")
        except ValueError:
            data = {}
        return ClientChat(r[0], r[1], r[2], r[3], r[4], data, datetime.fromisoformat(r[6]))

    def touch_client(self, chat_id: str, name: str, username: str, now: datetime) -> ClientChat:
        self.conn.execute(
            "INSERT INTO client_chats (chat_id, name, username, last_seen) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (chat_id) DO UPDATE SET name=excluded.name, username=excluded.username,"
            " last_seen=excluded.last_seen", (str(chat_id), name, username, _ts(now)))
        self.conn.commit()
        return self.client_chat(chat_id)

    def save_client_state(self, chat_id: str, state: str, data: dict, phone: str | None = None):
        self.conn.execute("UPDATE client_chats SET state=?, data=? WHERE chat_id=?",
                          (state, json.dumps(data, ensure_ascii=False), str(chat_id)))
        if phone:
            self.conn.execute("UPDATE client_chats SET phone=? WHERE chat_id=?", (phone, str(chat_id)))
        self.conn.commit()

    def client_chats(self) -> list[tuple[ClientChat, int, ClientMessage | None]]:
        """Переписки для окна «Клиенты»: (клиент, непрочитанных, последнее сообщение), свежие сверху."""
        result = []
        for (chat_id,) in self.conn.execute(
                "SELECT chat_id FROM client_messages GROUP BY chat_id ORDER BY MAX(id) DESC").fetchall():
            unread = self.conn.execute("SELECT COUNT(*) FROM client_messages WHERE chat_id=? AND incoming=1"
                                       " AND read=0", (chat_id,)).fetchone()[0]
            last = self.client_messages(chat_id, limit=1)
            result.append((self.client_chat(chat_id) or ClientChat(chat_id), unread, last[-1] if last else None))
        return result

    # --- заказы ---

    def add_client_order(self, chat_id: str, now: datetime, day: date, time: str, items: str, address: str,
                         recipient: str, client_phone: str, card: str, showcase_id: int | None = None) -> ClientOrder:
        cur = self.conn.execute(
            "INSERT INTO client_orders (chat_id, created_at, updated_at, showcase_id, items, day, time, address,"
            " recipient, client_phone, card) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(chat_id), _ts(now), _ts(now), showcase_id, items, day.isoformat(), time, address, recipient,
             client_phone, card))
        if showcase_id:  # готовый букет в одном экземпляре — заказали, больше никому не показываем
            self.conn.execute("UPDATE showcase SET active=0 WHERE id=? AND single=1", (showcase_id,))
        self.conn.commit()
        return self.get_client_order(cur.lastrowid)

    def get_client_order(self, order_id: int) -> ClientOrder | None:
        r = self.conn.execute(ORDER_SELECT + " WHERE o.id=?", (order_id,)).fetchone()
        return _row_to_order(r) if r else None

    def client_orders(self, chat_id: str | None = None, open_only: bool = False, limit: int = 200) -> list[ClientOrder]:
        sql, params = ORDER_SELECT + " WHERE 1=1", []
        if chat_id is not None:
            sql += " AND o.chat_id=?"
            params.append(str(chat_id))
        if open_only:
            sql += " AND o.status IN ('new', 'priced', 'paid_claimed')"
        rows = self.conn.execute(sql + " ORDER BY o.id DESC LIMIT ?", (*params, limit)).fetchall()
        return [_row_to_order(r) for r in rows]

    def price_client_order(self, order_id: int, amount: int, delivery_fee: int, now: datetime) -> ClientOrder:
        if amount <= 0:
            raise UserError("Впишите сумму к оплате.")
        if not 0 <= delivery_fee <= amount:
            raise UserError("Доставка не может быть больше всей суммы.")
        self.conn.execute("UPDATE client_orders SET amount=?, delivery_fee=?, status='priced', updated_at=?"
                          " WHERE id=? AND status IN ('new', 'priced', 'paid_claimed')",
                          (amount, delivery_fee, _ts(now), order_id))
        self.conn.commit()
        return self.get_client_order(order_id)

    def set_client_order_status(self, order_id: int, status: str, now: datetime, note: str | None = None,
                                restore_single: bool = True):
        """restore_single — при отказе вернуть готовый букет «в одном экземпляре» на витрину (если не продан)."""
        self.conn.execute("UPDATE client_orders SET status=?, updated_at=? WHERE id=?", (status, _ts(now), order_id))
        if note is not None:
            self.conn.execute("UPDATE client_orders SET note=? WHERE id=?", (note, order_id))
        if status == "rejected" and restore_single:  # отказ — готовый букет снова на витрине, если его никто больше не заказал
            self.conn.execute(
                "UPDATE showcase SET active=1 WHERE single=1 AND id=(SELECT showcase_id FROM client_orders WHERE id=?)"
                " AND NOT EXISTS (SELECT 1 FROM client_orders WHERE showcase_id=showcase.id AND id<>?"
                " AND status IN ('new', 'priced', 'paid_claimed', 'confirmed'))", (order_id, order_id))
        self.conn.commit()

    def set_paid_photo(self, order_id: int, photo: bytes, now: datetime):
        self.conn.execute("UPDATE client_orders SET paid_photo=?, status='paid_claimed', updated_at=? WHERE id=?"
                          " AND status IN ('priced', 'paid_claimed')", (photo, _ts(now), order_id))
        self.conn.commit()

    def paid_photo(self, order_id: int) -> bytes | None:
        r = self.conn.execute("SELECT paid_photo FROM client_orders WHERE id=?", (order_id,)).fetchone()
        return r[0] if r else None

    def confirm_client_order(self, order_id: int, user_id: int | None, now: datetime) -> Delivery:
        """Оплата пришла: заказ становится доставкой в календаре (букет ещё не собран)."""
        order = self.get_client_order(order_id)
        if order is None:
            raise UserError("Такого заказа нет.")
        if order.status == "confirmed" and order.delivery_id:
            raise UserError("Заказ уже принят.")
        if order.amount <= 0:
            raise UserError("Сначала назначьте сумму и отправьте её клиенту.")
        chat = self.client_chat(order.chat_id)
        name = chat.name if chat else ""
        d = Delivery(day=order.day, time=order.time,
                     items=order.items + (f"\nОткрытка: {order.card}" if order.card else ""),
                     recipient=order.recipient, address=order.address, price=order.delivery_fee,
                     created_by=user_id, source="MAX-бот" if order.chat_id.startswith("max:") else "Telegram-бот",
                     customer_contact=" ".join(x for x in (name, order.client_phone) if x),
                     client_chat=order.chat_id)
        self.add(d)
        self.conn.execute("UPDATE client_orders SET status='confirmed', delivery_id=?, updated_at=? WHERE id=?",
                          (d.id, _ts(now), order_id))
        self.conn.commit()
        return d

    def order_for_delivery(self, delivery_id: int) -> ClientOrder | None:
        r = self.conn.execute(ORDER_SELECT + " WHERE o.delivery_id=?", (delivery_id,)).fetchone()
        return _row_to_order(r) if r else None

    # --- переписка ---

    def add_client_message(self, chat_id: str, incoming: bool, text: str, now: datetime, photo: bytes | None = None,
                           author: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO client_messages (chat_id, at, incoming, text, photo, author, read) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(chat_id), _ts(now), int(incoming), text, photo, author, int(not incoming)))
        self.conn.commit()
        return cur.lastrowid

    def client_messages(self, chat_id: str, limit: int = 200) -> list[ClientMessage]:
        rows = self.conn.execute(
            "SELECT id, chat_id, at, incoming, text, photo IS NOT NULL, author, read FROM client_messages"
            " WHERE chat_id=? ORDER BY id DESC LIMIT ?", (str(chat_id), limit)).fetchall()
        return [ClientMessage(r[0], r[1], datetime.fromisoformat(r[2]), bool(r[3]), r[4], bool(r[5]), r[6], bool(r[7]))
                for r in reversed(rows)]

    def client_message_photo(self, message_id: int) -> bytes | None:
        r = self.conn.execute("SELECT photo FROM client_messages WHERE id=?", (message_id,)).fetchone()
        return r[0] if r else None

    def mark_client_read(self, chat_id: str):
        self.conn.execute("UPDATE client_messages SET read=1 WHERE chat_id=? AND incoming=1", (str(chat_id),))
        self.conn.commit()

    def client_unread(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM client_messages WHERE incoming=1 AND read=0").fetchone()[0]

    # --- очередь сообщений клиентам ---

    def queue_client(self, chat_id: str, text: str, now: datetime, markup: dict | None = None,
                     photo: bytes | None = None, file_id: str = "", ref: str = ""):
        """Сообщение клиенту через бота. ref — «showcase:5»: после отправки запомнить file_id фото витрины."""
        self.conn.execute("INSERT INTO client_outbox (chat_id, text, markup, photo, file_id, ref, created_at)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (str(chat_id), text, json.dumps(markup, ensure_ascii=False) if markup else "", photo,
                           file_id, ref, _ts(now)))
        self.conn.commit()

    def unsent_client(self, limit: int = 20, max_chats: bool | None = None
                      ) -> list[tuple[int, str, str, str, bytes | None, str, str]]:
        """max_chats: True — только клиентам из MAX, False — только из Telegram, None — всем."""
        where = {None: "", True: " AND chat_id LIKE 'max:%'", False: " AND chat_id NOT LIKE 'max:%'"}[max_chats]
        return self.conn.execute("SELECT id, chat_id, text, markup, photo, file_id, ref FROM client_outbox"
                                 " WHERE sent_at IS NULL" + where + " ORDER BY id LIMIT ?", (limit,)).fetchall()

    def mark_client_sent(self, outbox_id: int, now: datetime):
        self.conn.execute("UPDATE client_outbox SET sent_at=?, photo=NULL WHERE id=?", (_ts(now), outbox_id))
        self.conn.commit()

    # --- фото букетов ---

    def add_delivery_photo(self, delivery_id: int, photo: bytes, by_name: str, now: datetime) -> int:
        cur = self.conn.execute("INSERT INTO delivery_photos (delivery_id, at, photo, by_name) VALUES (?, ?, ?, ?)",
                                (delivery_id, _ts(now), photo, by_name))
        self.conn.commit()
        return cur.lastrowid

    def delivery_photos(self, delivery_id: int) -> list[tuple[int, datetime, bytes, str]]:
        rows = self.conn.execute("SELECT id, at, photo, by_name FROM delivery_photos WHERE delivery_id=? ORDER BY id",
                                 (delivery_id,)).fetchall()
        return [(r[0], datetime.fromisoformat(r[1]), r[2], r[3]) for r in rows]

    def photos_for_admins(self) -> list[tuple[int, int, bytes, str]]:
        """Фото, которые ещё не ушли администраторам: (номер, доставка, фото, кто снял)."""
        return self.conn.execute("SELECT id, delivery_id, photo, by_name FROM delivery_photos WHERE sent_admins=0"
                                 " ORDER BY id LIMIT 5").fetchall()

    def mark_photo_sent(self, photo_id: int):
        self.conn.execute("UPDATE delivery_photos SET sent_admins=1 WHERE id=?", (photo_id,))
        self.conn.commit()

    def prune_photos(self, before: datetime) -> int:
        """Удалить фото букетов, переписки и оплат старше before (витрину не трогаем): база не пухнет.
        Возвращает, сколько удалено."""
        removed = 0
        for sql in ("DELETE FROM delivery_photos WHERE at < ?",
                    "UPDATE client_messages SET photo=NULL WHERE photo IS NOT NULL AND at < ?",
                    "UPDATE client_orders SET paid_photo=NULL WHERE paid_photo IS NOT NULL AND updated_at < ?",
                    "UPDATE client_outbox SET photo=NULL WHERE photo IS NOT NULL AND created_at < ?"):
            removed += self.conn.execute(sql, (_ts(before),)).rowcount
        self.conn.commit()
        if removed:
            self.conn.execute("VACUUM")  # вернуть место на диске (и копии станут меньше)
        return removed

    # --- флористы в боте клиентов ---

    def link_code(self, user_id: int) -> str:
        """Код (8 цифр), который флорист отправляет боту (/start КОД), чтобы бот знал его и просил фото букетов."""
        r = self.conn.execute("SELECT tg_code FROM users WHERE id=?", (user_id,)).fetchone()
        if r and r[0]:
            return r[0]
        import secrets
        code = f"{secrets.randbelow(90_000_000) + 10_000_000}"
        self.conn.execute("UPDATE users SET tg_code=? WHERE id=?", (code, user_id))
        self.conn.commit()
        return code

    def link_staff(self, code: str, chat_id: str) -> "User | None":
        code = code.strip()
        r = self.conn.execute("SELECT id FROM users WHERE tg_code=? AND tg_code<>''", (code,)).fetchone()
        if r is None:
            return None
        self.conn.execute("UPDATE users SET tg_chat='' WHERE tg_chat=?", (str(chat_id),))
        self.conn.execute("UPDATE users SET tg_chat=?, tg_code='' WHERE id=?", (str(chat_id), r[0]))
        self.conn.commit()
        return next((u for u in self.users() if u.id == r[0]), None)

    def staff_chat(self, user_id: int) -> str:
        r = self.conn.execute("SELECT tg_chat FROM users WHERE id=?", (user_id,)).fetchone()
        return r[0] if r else ""

    def staff_by_chat(self, chat_id: str) -> "User | None":
        r = self.conn.execute("SELECT id FROM users WHERE tg_chat=? AND tg_chat<>''", (str(chat_id),)).fetchone()
        return next((u for u in self.users() if u.id == r[0]), None) if r else None

    # --- общий рабочий телефон: все флористы с одного чата, кто собрал — по тому, кто на смене ---

    def work_phone_code(self) -> str:
        """Одноразовый код для привязки рабочего телефона (виден только админу в настройках)."""
        raw = self.get_file("work_phone_code")
        if raw:
            return raw.decode()
        import secrets
        code = f"{secrets.randbelow(90_000_000) + 10_000_000}"
        self.set_file("work_phone_code", code.encode())
        return code

    def link_work_phone(self, code: str, chat_id: str, now: datetime) -> bool:
        raw = self.get_file("work_phone_code")
        if not raw or code.strip() != raw.decode():
            return False
        self.conn.execute("UPDATE users SET tg_chat='' WHERE tg_chat=?", (str(chat_id),))  # личная привязка не нужна
        self.conn.execute("INSERT OR REPLACE INTO work_phones (chat_id, linked_at) VALUES (?, ?)",
                          (str(chat_id), _ts(now)))
        self.conn.commit()
        self.set_file("work_phone_code", None)  # код одноразовый
        return True

    def is_work_phone(self, chat_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM work_phones WHERE chat_id=?", (str(chat_id),)).fetchone() is not None

    def work_phones(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT chat_id FROM work_phones ORDER BY linked_at").fetchall()]

    def unlink_work_phones(self):
        self.conn.execute("DELETE FROM work_phones")
        self.conn.commit()

    def unlink_staff(self, user_id: int):
        self.conn.execute("UPDATE users SET tg_chat='' WHERE id=?", (user_id,))
        self.conn.commit()


class Database(ClientStore):
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(deliveries)")}
        for column, sql in MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(sql)
        if "assembled" not in existing:  # до появления «собрано»: доставленные, конечно, были собраны
            self.conn.execute("UPDATE deliveries SET assembled = 1 WHERE delivered = 1")
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(users)")}
        for column, sql in USER_MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(sql)
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(shifts)")}
        for column, sql in SHIFT_MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(sql)
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(showcase)")}
        for column, sql in SHOWCASE_MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(sql)
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(penalties)")}
        for column, sql in PENALTY_MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(sql)
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(telegram_outbox)")}
        if "chat_id" not in existing:  # очередь, созданная до поддержки нескольких чатов
            self.conn.execute("ALTER TABLE telegram_outbox ADD COLUMN chat_id TEXT")
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

    def restore(self, path: str):
        """Заменить все данные содержимым резервной копии."""
        try:
            src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.Error:
            raise UserError("Это не файл резервной копии программы.") from None
        try:
            if "deliveries" not in tables:
                raise UserError("Это не файл резервной копии программы.")
            src.backup(self.conn)
        finally:
            src.close()
        self.conn.close()
        Database.__init__(self, self.path)  # копия могла быть от старой версии — дополнить таблицы

    def add(self, d: Delivery) -> int:
        cur = self.conn.execute(
            "INSERT INTO deliveries (day, time, items, recipient, phone, address,"
            " price, courier_pay, delivered, created_by, delivered_by,"
            " source, customer_contact, messages, assembled, assembled_by, assembled_at, client_chat)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered), d.created_by,
             d.delivered_by if d.delivered else None, d.source, d.customer_contact, d.messages,
             *self._assembly_values(d), d.client_chat),
        )
        self.conn.commit()
        d.id = cur.lastrowid
        return d.id

    def update(self, d: Delivery):
        self.conn.execute(
            "UPDATE deliveries SET day=?, time=?, items=?, recipient=?, phone=?,"
            " address=?, price=?, courier_pay=?, delivered=?, delivered_by=?,"
            " source=?, customer_contact=?, assembled=?, assembled_by=?, assembled_at=? WHERE id=?",
            (d.day.isoformat(), d.time, d.items, d.recipient, d.phone, d.address,
             d.price, d.courier_pay, int(d.delivered),
             d.delivered_by if d.delivered else None, d.source, d.customer_contact,
             *self._assembly_values(d), d.id),
        )
        self.conn.commit()

    @staticmethod
    def _assembly_values(d: Delivery) -> tuple:
        """(собрано, кем, когда): доставленный букет считается собранным."""
        assembled = d.assembled or d.delivered
        if not assembled:
            return 0, None, None
        return 1, d.assembled_by, _ts(d.assembled_at) if d.assembled_at else None

    def set_delivered(self, delivery_id: int, delivered: bool, user_id: int | None = None,
                      now: datetime | None = None):
        self.conn.execute(
            "UPDATE deliveries SET delivered=?, delivered_by=? WHERE id=?",
            (int(delivered), user_id if delivered else None, delivery_id),
        )
        if delivered:  # доставили — значит, и собрали (если забыли отметить)
            self.conn.execute(
                "UPDATE deliveries SET assembled=1, assembled_by=COALESCE(assembled_by, ?),"
                " assembled_at=COALESCE(assembled_at, ?) WHERE id=? AND assembled=0",
                (user_id, _ts(now or datetime.now()), delivery_id))
        self.conn.commit()

    def set_assembled(self, delivery_id: int, assembled: bool, user_id: int | None, now: datetime):
        """Букет собран (или отметку сняли). У доставленного снять «собрано» нельзя."""
        self.conn.execute(
            "UPDATE deliveries SET assembled=?, assembled_by=?, assembled_at=? WHERE id=? AND delivered=0",
            (int(assembled), user_id if assembled else None, _ts(now) if assembled else None, delivery_id))
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

    def get_delivery(self, delivery_id: int) -> Delivery | None:
        row = self.conn.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE d.id=?", (delivery_id,)).fetchone()
        return _row_to_delivery(row) if row else None

    def request_delete(self, delivery_id: int, user_id: int | None, now: datetime):
        """Сотрудник просит удалить доставку: она остаётся, пока администратор не решит."""
        self.conn.execute("UPDATE deliveries SET delete_requested_by=?, delete_requested_at=? WHERE id=?",
                          (user_id, _ts(now), delivery_id))
        self.conn.commit()

    def cancel_delete_request(self, delivery_id: int):
        self.conn.execute("UPDATE deliveries SET delete_requested_by=NULL, delete_requested_at=NULL WHERE id=?",
                          (delivery_id,))
        self.conn.commit()

    def delete(self, delivery_id: int):
        self.conn.execute("DELETE FROM deliveries WHERE id=?", (delivery_id,))
        self.conn.execute("DELETE FROM delivery_photos WHERE delivery_id=?", (delivery_id,))
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
            " COALESCE(SUM(courier_pay), 0), COALESCE(SUM(assembled = 1 OR delivered = 1), 0)"
            f" FROM deliveries WHERE {where}",
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
        recent = _ts(datetime.now() - timedelta(days=2))
        if self.conn.execute("SELECT 1 FROM shifts WHERE user_id = ? AND closed_at IS NULL AND opened_at >= ?",
                             (user_id, recent)).fetchone():
            raise UserError("У сотрудника идёт или не сдана смена. Пусть сначала сдаст её, потом удалите.")
        # Доставки остаются, просто без автора.
        self.conn.execute("UPDATE deliveries SET created_by=NULL WHERE created_by=?", (user_id,))
        self.conn.execute("UPDATE deliveries SET delivered_by=NULL WHERE delivered_by=?", (user_id,))
        self.conn.execute("UPDATE shifts SET user_id=NULL WHERE user_id=?", (user_id,))
        self.conn.execute("UPDATE cash_ops SET created_by=NULL WHERE created_by=?", (user_id,))
        self.conn.execute("UPDATE water_changes SET user_id=NULL WHERE user_id=?", (user_id,))
        self.conn.execute("DELETE FROM salary_adjustments WHERE user_id=?", (user_id,))
        self.conn.execute("DELETE FROM announcement_reads WHERE user_id=?", (user_id,))
        self.conn.execute("UPDATE penalties SET user_id=NULL WHERE user_id=?", (user_id,))
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

    def open_shifts(self) -> list[Shift]:
        """Все несданные смены (кто сейчас на смене или не сдал)."""
        rows = self.conn.execute(SHIFT_SELECT + " WHERE s.closed_at IS NULL ORDER BY s.opened_at, s.id").fetchall()
        return [_row_to_shift(r) for r in rows]

    def close_shift(self, shift_id: int, now: datetime, closing_cash: int, cash_sales: int,
                    courier_cash: int, note: str = "", revenue: int = 0, flowers: int = 0,
                    terminal: int = 0, qr: int = 0, deliveries: int = 0, writeoff: int = 0,
                    writeoff_items: dict[str, int] | None = None) -> Shift:
        """writeoff_items — списания по причинам {«Завяло»: 2000}; если заданы, writeoff считается их суммой."""
        if writeoff_items is not None:
            if min(writeoff_items.values(), default=0) < 0:
                raise UserError("Суммы не могут быть отрицательными.")
            writeoff = sum(writeoff_items.values())
        if min(closing_cash, cash_sales, courier_cash, revenue, flowers, terminal, qr, deliveries, writeoff) < 0:
            raise UserError("Суммы не могут быть отрицательными.")
        if flowers > revenue:
            raise UserError("Цветов не может быть продано больше, чем весь приход.")
        cur = self.conn.execute(
            "UPDATE shifts SET closed_at=?, closing_cash=?, cash_sales=?, delivery_cash=0, courier_cash=?,"
            " note=?, revenue=?, flowers=?, terminal=?, qr=?, deliveries=?, writeoff=?"
            " WHERE id=? AND closed_at IS NULL",
            (_ts(now), closing_cash, cash_sales, courier_cash, note.strip(), revenue, flowers,
             terminal, qr, deliveries, writeoff, shift_id),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            raise UserError("Эта смена уже сдана.")
        for reason, amount in (writeoff_items or {}).items():
            if amount > 0:
                self.conn.execute("INSERT OR REPLACE INTO writeoff_items (shift_id, reason, amount) VALUES (?, ?, ?)",
                                  (shift_id, reason, amount))
        # Список обязанностей на момент сдачи: потом его могут поменять, а история останется.
        self.conn.execute(
            "INSERT OR IGNORE INTO shift_duties (shift_id, duty_id, text, done_at)"
            " SELECT ?, id, text, NULL FROM duties WHERE active = 1", (shift_id,))
        self.conn.commit()
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

    def deliveries_for_shift(self, day: date, change: time) -> list[Delivery]:
        """Доставки, которые приходятся на смену этого дня (ночные — на вчерашнюю смену)."""
        candidates = self.for_day(day) + self.for_day(day + timedelta(days=1))
        return [d for d in candidates if delivery_business_day(d, change) == day]

    def delivery_cash_for_shift(self, day: date, change: time) -> tuple[int, int]:
        """(взяли за доставки, отдали курьерам) по доставкам, которые приходятся на смену."""
        own = self.deliveries_for_shift(day, change)
        return sum(d.price for d in own), sum(d.courier_pay for d in own)

    # ----- смена воды -----

    def mark_water(self, day: date, user_id: int | None, now: datetime):
        self.conn.execute(
            "INSERT OR IGNORE INTO water_changes (day, user_id, changed_at) VALUES (?, ?, ?)",
            (day.isoformat(), user_id, _ts(now)),
        )
        self.conn.commit()

    def water_changes_between(self, first: date, last: date) -> dict[date, tuple[str, datetime]]:
        """День смены -> (кто поменял, когда)."""
        rows = self.conn.execute(
            "SELECT w.day, COALESCE(NULLIF(u.name, ''), u.login, ''), w.changed_at"
            " FROM water_changes w LEFT JOIN users u ON u.id = w.user_id"
            " WHERE w.day BETWEEN ? AND ?", (first.isoformat(), last.isoformat()),
        ).fetchall()
        return {date.fromisoformat(r[0]): (r[1], datetime.fromisoformat(r[2])) for r in rows}

    def water_change(self, day: date) -> tuple[str, datetime] | None:
        return self.water_changes_between(day, day).get(day)

    # ----- зарплата: доплаты и удержания -----

    def add_adjustment(self, user_id: int, month: str, amount: int, reason: str,
                       created_by: int | None, now: datetime) -> int:
        if amount == 0:
            raise UserError("Введите сумму.")
        if not reason.strip():
            raise UserError("Напишите, за что (например «аванс» или «премия»).")
        cur = self.conn.execute(
            "INSERT INTO salary_adjustments (user_id, month, amount, reason, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?)", (user_id, month, amount, reason.strip(), _ts(now), created_by))
        self.conn.commit()
        return cur.lastrowid

    def adjustments(self, user_id: int, month: str) -> list[Adjustment]:
        rows = self.conn.execute(
            "SELECT a.id, a.amount, a.reason, a.created_at, COALESCE(NULLIF(u.name, ''), u.login, '')"
            " FROM salary_adjustments a LEFT JOIN users u ON u.id = a.created_by"
            " WHERE a.user_id = ? AND a.month = ? ORDER BY a.created_at, a.id", (user_id, month)).fetchall()
        return [Adjustment(r[0], r[1], r[2], datetime.fromisoformat(r[3]), r[4]) for r in rows]

    def delete_adjustment(self, adjustment_id: int):
        self.conn.execute("DELETE FROM salary_adjustments WHERE id=?", (adjustment_id,))
        self.conn.commit()

    # ----- очередь сообщений в Telegram -----

    def queue_message(self, text: str, now: datetime, key: str | None = None, chat: str | None = None) -> bool:
        """Поставить сообщение в очередь для одного чата. С тем же key второй раз не ставится.
        chat=None — только у старых сообщений до появления нескольких чатов: уйдёт во все. True — поставлено."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO telegram_outbox (key, text, created_at, chat_id) VALUES (?, ?, ?, ?)",
            (key, text, _ts(now), chat))
        self.conn.commit()
        return cur.rowcount > 0

    def unsent_messages(self, limit: int = 20) -> list[tuple[int, str, str | None]]:
        """Неотправленные: (id, текст, чат или None — всем)."""
        return self.conn.execute(
            "SELECT id, text, chat_id FROM telegram_outbox WHERE sent_at IS NULL ORDER BY id LIMIT ?",
            (limit,)).fetchall()

    def remember_chat(self, chat_id: str, title: str, now: datetime):
        """Кто писал боту (в том числе посторонние) — чтобы администратор мог выбрать своих в настройках."""
        self.conn.execute(
            "INSERT INTO telegram_chats (chat_id, title, last_seen) VALUES (?, ?, ?)"
            " ON CONFLICT (chat_id) DO UPDATE SET title = excluded.title, last_seen = excluded.last_seen",
            (str(chat_id), title, _ts(now)))
        self.conn.commit()

    def seen_chats(self) -> list[tuple[str, str]]:
        """[(chat_id, имя)] — сначала писавшие недавно."""
        return self.conn.execute(
            "SELECT chat_id, title FROM telegram_chats ORDER BY last_seen DESC, chat_id").fetchall()

    def mark_message_sent(self, message_id: int, now: datetime):
        self.conn.execute("UPDATE telegram_outbox SET sent_at=? WHERE id=?", (_ts(now), message_id))
        self.conn.commit()

    # ----- обязанности смены -----

    def duties(self) -> list[Duty]:
        rows = self.conn.execute("SELECT id, text FROM duties WHERE active = 1 ORDER BY position, id").fetchall()
        return [Duty(r[0], r[1]) for r in rows]

    def save_duties(self, items: list[tuple[int | None, str]]):
        """Новый список обязанностей: (id существующей или None для новой, текст). Убранные — в архив."""
        keep = {duty_id for duty_id, text in items if duty_id is not None and text.strip()}
        for duty in self.duties():
            if duty.id not in keep:
                self.conn.execute("UPDATE duties SET active = 0 WHERE id = ?", (duty.id,))
        for position, (duty_id, text) in enumerate(items):
            text = text.strip()
            if not text:
                continue
            if duty_id is None:
                self.conn.execute("INSERT INTO duties (text, position) VALUES (?, ?)", (text, position))
            else:
                self.conn.execute("UPDATE duties SET text = ?, position = ?, active = 1 WHERE id = ?",
                                  (text, position, duty_id))
        self.conn.commit()

    def checklist(self, shift: Shift) -> list[ChecklistItem]:
        """Обязанности смены: у идущей — текущий список, у сданной — каким он был при сдаче."""
        if shift.closed_at is None:
            rows = self.conn.execute(
                "SELECT d.id, d.text, sd.done_at FROM duties d LEFT JOIN shift_duties sd"
                " ON sd.duty_id = d.id AND sd.shift_id = ? WHERE d.active = 1 ORDER BY d.position, d.id",
                (shift.id,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT sd.duty_id, sd.text, sd.done_at FROM shift_duties sd LEFT JOIN duties d"
                " ON d.id = sd.duty_id WHERE sd.shift_id = ? ORDER BY COALESCE(d.position, 0), sd.duty_id",
                (shift.id,)).fetchall()
        return [ChecklistItem(r[0], r[1], datetime.fromisoformat(r[2]) if r[2] else None) for r in rows]

    def set_duty_done(self, shift_id: int, duty_id: int, text: str, done: bool, now: datetime):
        self.conn.execute(
            "INSERT INTO shift_duties (shift_id, duty_id, text, done_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (shift_id, duty_id) DO UPDATE SET done_at = excluded.done_at",
            (shift_id, duty_id, text, _ts(now) if done else None))
        self.conn.commit()

    # ----- объявления для сотрудников -----

    def add_announcement(self, text: str, created_at: datetime, update_id: int | None = None) -> int | None:
        """Новое объявление; None — пустое или уже было (то же сообщение Telegram)."""
        text = text.strip()
        if not text:
            return None
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO announcements (update_id, text, created_at) VALUES (?, ?, ?)",
            (update_id, text, _ts(created_at)))
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def unread_announcements(self, user_id: int, since: datetime) -> list[Announcement]:
        rows = self.conn.execute(
            "SELECT id, text, created_at FROM announcements a WHERE created_at >= ? AND NOT EXISTS"
            " (SELECT 1 FROM announcement_reads r WHERE r.announcement_id = a.id AND r.user_id = ?)"
            " ORDER BY created_at, id", (_ts(since), user_id)).fetchall()
        return [Announcement(r[0], r[1], datetime.fromisoformat(r[2])) for r in rows]

    def mark_announcements_read(self, ids: list[int], user_id: int, now: datetime):
        self.conn.executemany(
            "INSERT OR IGNORE INTO announcement_reads (announcement_id, user_id, read_at) VALUES (?, ?, ?)",
            [(i, user_id, _ts(now)) for i in ids])
        self.conn.commit()

    def announcements(self, limit: int = 100) -> list[Announcement]:
        """Последние объявления, новые сверху, с теми, кто прочитал."""
        items = [Announcement(r[0], r[1], datetime.fromisoformat(r[2])) for r in self.conn.execute(
            "SELECT id, text, created_at FROM announcements ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,)).fetchall()]
        for item in items:
            item.readers = [(r[0], datetime.fromisoformat(r[1])) for r in self.conn.execute(
                "SELECT COALESCE(NULLIF(u.name, ''), u.login, ''), r.read_at FROM announcement_reads r"
                " LEFT JOIN users u ON u.id = r.user_id WHERE r.announcement_id = ? ORDER BY r.read_at",
                (item.id,)).fetchall()]
        return items

    def delete_announcement(self, announcement_id: int):
        self.conn.execute("DELETE FROM announcement_reads WHERE announcement_id = ?", (announcement_id,))
        self.conn.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
        self.conn.commit()

    # ----- журнал уведомлений (то, что всплывало в углу экрана) -----

    def add_notification(self, title: str, text: str, now: datetime, keep_days: int = 7):
        self.conn.execute("INSERT INTO notifications (title, text, created_at) VALUES (?, ?, ?)",
                          (title, text, _ts(now)))
        self.conn.execute("DELETE FROM notifications WHERE created_at < ?", (_ts(now - timedelta(days=keep_days)),))
        self.conn.commit()

    def notifications_since(self, since: datetime) -> list[tuple[datetime, str, str]]:
        """Уведомления с момента since, новые сверху: (когда, заголовок, текст)."""
        rows = self.conn.execute(
            "SELECT created_at, title, text FROM notifications WHERE created_at >= ?"
            " ORDER BY created_at DESC, id DESC", (_ts(since),)).fetchall()
        return [(datetime.fromisoformat(r[0]), r[1], r[2]) for r in rows]

    # ----- журнал действий (кто и что менял) -----

    def log_action(self, user: "User | None", action: str, details: str, now: datetime | None = None,
                   who: str = "", keep_days: int = 180):
        """Записать действие. who — имя, если действует не сотрудник программы (например «логин при неудачном входе»)."""
        now = now or datetime.now()
        name = (user.name or user.login) if user else who
        self.conn.execute("INSERT INTO audit_log (at, user_id, user_name, action, details) VALUES (?, ?, ?, ?, ?)",
                          (_ts(now), user.id if user else None, name, action, details))
        self.conn.execute("DELETE FROM audit_log WHERE at < ?", (_ts(now - timedelta(days=keep_days)),))
        self.conn.commit()

    def audit_entries(self, since: datetime, who: str | None = None, limit: int = 1000
                      ) -> list[tuple[datetime, str, str, str]]:
        """(когда, кто, действие, подробности), новые сверху. who — только действия этого человека (по имени)."""
        sql = "SELECT at, user_name, action, details FROM audit_log WHERE at >= ?"
        params: list = [_ts(since)]
        if who is not None:
            sql += " AND user_name = ?"
            params.append(who)
        rows = self.conn.execute(sql + " ORDER BY at DESC, id DESC LIMIT ?", (*params, limit)).fetchall()
        return [(datetime.fromisoformat(r[0]), r[1], r[2], r[3]) for r in rows]

    def audit_people(self) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT user_name FROM audit_log WHERE user_name <> '' ORDER BY user_name").fetchall()]

    # ----- штрафные санкции -----

    PENALTY_SELECT = (
        "SELECT p.id, p.shift_id, p.user_id, p.kind_id, p.title, p.amount, p.created_at, p.created_by, p.note,"
        " s.day, COALESCE(NULLIF(u.name, ''), u.login, ''), p.dispute_text, p.dispute_at, p.dispute_result"
        " FROM penalties p"
        " JOIN shifts s ON s.id = p.shift_id LEFT JOIN users u ON u.id = p.user_id")

    @staticmethod
    def _row_to_penalty(r) -> Penalty:
        return Penalty(id=r[0], shift_id=r[1], user_id=r[2], kind_id=r[3], title=r[4], amount=r[5],
                       created_at=datetime.fromisoformat(r[6]), created_by=r[7], note=r[8],
                       shift_day=date.fromisoformat(r[9]), user_name=r[10], dispute_text=r[11],
                       disputed_at=datetime.fromisoformat(r[12]) if r[12] else None, dispute_result=r[13])

    def add_penalty(self, shift_id: int, kind_id: int, title: str, amount: int, by: str, now: datetime,
                    note: str = "") -> Penalty | None:
        """Поставить санкцию за смену. Такая же за ту же смену уже есть — None (чтобы два администратора
        не наказали дважды за одно и то же)."""
        shift = self.get_shift(shift_id)
        if shift is None:
            raise UserError("Такой смены нет.")
        if amount < 0:
            raise UserError("Сумма не может быть отрицательной.")
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO penalties (shift_id, user_id, kind_id, title, amount, note, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (shift_id, shift.user_id, kind_id, title, amount, note.strip(), _ts(now), by))
        self.conn.commit()
        return self.get_penalty(cur.lastrowid) if cur.rowcount else None

    def get_penalty(self, penalty_id: int) -> Penalty | None:
        row = self.conn.execute(self.PENALTY_SELECT + " WHERE p.id = ?", (penalty_id,)).fetchone()
        return self._row_to_penalty(row) if row else None

    def delete_penalty(self, penalty_id: int) -> Penalty | None:
        penalty = self.get_penalty(penalty_id)
        if penalty is not None:
            self.conn.execute("DELETE FROM penalties WHERE id = ?", (penalty_id,))
            self.conn.commit()
        return penalty

    def penalties_between(self, first: date, last: date, user_id: int | None = None) -> list[Penalty]:
        """Санкции за смены с first по last (месяц считается по дню смены, а не по дню постановки)."""
        sql = self.PENALTY_SELECT + " WHERE s.day BETWEEN ? AND ?"
        params: list = [first.isoformat(), last.isoformat()]
        if user_id is not None:
            sql += " AND p.user_id = ?"
            params.append(user_id)
        rows = self.conn.execute(sql + " ORDER BY s.day, p.id", params).fetchall()
        return [self._row_to_penalty(r) for r in rows]

    def penalties_for_shift(self, shift_id: int) -> list[Penalty]:
        rows = self.conn.execute(self.PENALTY_SELECT + " WHERE p.shift_id = ? ORDER BY p.id", (shift_id,)).fetchall()
        return [self._row_to_penalty(r) for r in rows]

    def last_shift_of(self, user_id: int) -> Shift | None:
        """Последняя смена сотрудника (идущая или сданная) — к ней относится санкция «после смены»."""
        row = self.conn.execute(SHIFT_SELECT + " WHERE s.user_id = ? ORDER BY s.opened_at DESC, s.id DESC LIMIT 1",
                                (user_id,)).fetchone()
        return _row_to_shift(row) if row else None

    def dispute_penalty(self, penalty_id: int, text: str, now: datetime) -> Penalty:
        """Сотрудник не согласен с санкцией. Один раз: повторный спор не нужен, решение за администратором."""
        penalty = self.get_penalty(penalty_id)
        if penalty is None:
            raise UserError("Такой санкции уже нет.")
        if not text.strip():
            raise UserError("Напишите, почему вы не согласны.")
        if penalty.disputed_at is not None:
            raise UserError("Вы уже оспорили эту санкцию, администратор решит.")
        self.conn.execute("UPDATE penalties SET dispute_text=?, dispute_at=?, dispute_result='' WHERE id=?",
                          (text.strip(), _ts(now), penalty_id))
        self.conn.commit()
        return self.get_penalty(penalty_id)

    def resolve_dispute(self, penalty_id: int) -> Penalty | None:
        """Администратор оставил санкцию в силе: спор закрыт. (Если снял — санкция просто удаляется.)"""
        self.conn.execute("UPDATE penalties SET dispute_result='kept' WHERE id=? AND dispute_at IS NOT NULL",
                          (penalty_id,))
        self.conn.commit()
        return self.get_penalty(penalty_id)

    # ----- списания: причины и «обычный» уровень -----

    def writeoff_items_for_shift(self, shift_id: int) -> dict[str, int]:
        return dict(self.conn.execute("SELECT reason, amount FROM writeoff_items WHERE shift_id = ? ORDER BY amount DESC",
                                      (shift_id,)).fetchall())

    def writeoff_by_reason(self, first: date, last: date) -> dict[str, int]:
        """Списания магазина за период по причинам (сданные смены), по убыванию суммы."""
        rows = self.conn.execute(
            "SELECT w.reason, SUM(w.amount) FROM writeoff_items w JOIN shifts s ON s.id = w.shift_id"
            " WHERE s.closed_at IS NOT NULL AND s.day BETWEEN ? AND ? GROUP BY w.reason ORDER BY SUM(w.amount) DESC",
            (first.isoformat(), last.isoformat())).fetchall()
        return dict(rows)

    def recent_writeoff_average(self, shift: Shift, count: int = 14, at_least: int = 3) -> float | None:
        """Сколько в среднем списывали за смену раньше (последние count сданных смен до этой).
        None — сданных смен раньше было меньше at_least: сравнивать не с чем, тревогу не поднимаем."""
        row = self.conn.execute(
            "SELECT AVG(writeoff), COUNT(*) FROM (SELECT writeoff FROM shifts WHERE closed_at IS NOT NULL"
            " AND id <> ? AND opened_at < ? ORDER BY opened_at DESC LIMIT ?)",
            (shift.id, _ts(shift.opened_at), count)).fetchone()
        return row[0] if row and row[1] >= max(at_least, 1) else None
