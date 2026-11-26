"""Лояльность покупателей: баллы, уровни, памятные даты и напоминания.

Ключ клиента — номер телефона (только цифры). Баллы равны рублям, начисляются
с оплаченных покупок и списываются не больше чем на ``pay_percent`` суммы.
Вся работа идёт через один SQLite-connection программы: сеть трогать нельзя,
поэтому модуль чисто синхронный и рассчитан на главный поток.

Настройки хранятся в тех же QSettings, что и остальные настройки программы.
Для тестов можно передать собственный словарь ``config`` — тогда QSettings
не используется вообще.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from PySide6.QtCore import QSettings

SETTINGS_GROUP = "FlowerDeliveries"

DEFAULT_CONFIG: dict[str, int] = {
    "rate_base": 5,
    "rate_mid": 7,
    "rate_high": 10,
    "tier_mid": 30000,
    "tier_high": 100000,
    "pay_percent": 30,
    "expire_months": 6,
    "welcome": 200,
    "double_dates": 1,
    "expire_reminders": 1,
}

LOYALTY_SCHEMA = """
CREATE TABLE IF NOT EXISTS loyalty_accounts (
    phone        TEXT PRIMARY KEY,
    balance      INTEGER NOT NULL DEFAULT 0,
    last_activity TEXT NOT NULL DEFAULT '',
    expires_at   TEXT,
    welcome      INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS loyalty_txns (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    phone    TEXT NOT NULL,
    at       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    amount   INTEGER NOT NULL,
    rate     INTEGER NOT NULL DEFAULT 0,
    base     INTEGER NOT NULL DEFAULT 0,
    source   TEXT NOT NULL DEFAULT '',
    author   TEXT NOT NULL DEFAULT '',
    note     TEXT NOT NULL DEFAULT '',
    doubled  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_loyalty_txns_phone ON loyalty_txns(phone, id);
CREATE TABLE IF NOT EXISTS loyalty_dates (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    phone    TEXT NOT NULL,
    label    TEXT NOT NULL,
    day      TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0,
    UNIQUE(phone, label, day)
);
CREATE TABLE IF NOT EXISTS loyalty_reminders (
    phone TEXT NOT NULL,
    kind  TEXT NOT NULL,
    key   TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (phone, kind, key)
);
"""


def norm_phone(value: str | int | None) -> str:
    """Привести телефон к единому ключу: только цифры, без +, скобок и пробелов."""
    if value is None:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("8"):  # 8 999… и +7 999… — один и тот же клиент
        digits = "7" + digits[1:]
    if 10 <= len(digits) <= 15:
        return digits
    return ""


def add_months(day: date, months: int) -> date:
    """Сдвиг на календарные месяцы (не на 30 дней). 31 января + 1 → 28/29 февраля."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def load_config(settings: QSettings | None = None) -> dict[str, int]:
    s = settings or QSettings(SETTINGS_GROUP, SETTINGS_GROUP)
    result = dict(DEFAULT_CONFIG)
    for key in result:
        try:
            result[key] = int(s.value(f"loyalty/{key}", result[key]))
        except (TypeError, ValueError):
            pass
    return result


@dataclass
class Account:
    phone: str
    balance: int = 0
    last_activity: str = ""
    expires_at: str = ""
    welcome: bool = False

    @property
    def expires(self) -> date | None:
        return date.fromisoformat(self.expires_at) if self.expires_at else None


@dataclass
class Reminder:
    phone: str
    kind: str
    text: str
    key: str


class Loyalty:
    """Все операции с бонусами одного магазина."""

    def __init__(self, conn, config: dict[str, int] | None = None):
        self.conn = conn
        self.cfg = load_config() if config is None else {**DEFAULT_CONFIG, **config}

    # ----- служебное -----

    def _now(self, now: datetime | None) -> datetime:
        return now or datetime.now()

    def _account_row(self, phone: str):
        return self.conn.execute(
            "SELECT phone, balance, last_activity, COALESCE(expires_at, ''), welcome"
            " FROM loyalty_accounts WHERE phone=?", (phone,)).fetchone()

    def _account(self, phone: str) -> Account:
        row = self._account_row(phone)
        if row is None:
            return Account(phone=phone)
        return Account(phone=row[0], balance=row[1], last_activity=row[2],
                       expires_at=row[3], welcome=bool(row[4]))

    def _ensure(self, phone: str):
        if not phone:
            raise ValueError("Пустой телефон клиента")
        if self._account_row(phone) is None:
            self.conn.execute(
                "INSERT INTO loyalty_accounts (phone, updated_at) VALUES (?, ?)",
                (phone, self._now(None).isoformat(timespec="seconds")))

    def _touch(self, phone: str, now: datetime):
        """Любая покупка (или регистрация) отодвигает сгорание на ``expire_months``."""
        self._ensure(phone)
        today = now.date()
        expires = add_months(today, self.cfg["expire_months"])
        self.conn.execute(
            "UPDATE loyalty_accounts SET last_activity=?, expires_at=?, updated_at=? WHERE phone=?",
            (today.isoformat(), expires.isoformat(), now.isoformat(timespec="seconds"), phone))

    def _txn(self, phone: str, now: datetime, kind: str, amount: int, *,
             rate: int = 0, base: int = 0, source: str = "", author: str = "",
             note: str = "", doubled: bool = False):
        self._ensure(phone)
        self.conn.execute(
            "INSERT INTO loyalty_txns (phone, at, kind, amount, rate, base, source, author, note, doubled)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (phone, now.isoformat(timespec="seconds"), kind, amount, rate, base,
             source, author, note, int(doubled)))
        self.conn.execute(
            "UPDATE loyalty_accounts SET balance=balance+?, updated_at=? WHERE phone=?",
            (amount, now.isoformat(timespec="seconds"), phone))
        self.conn.commit()

    # ----- уровни -----

    def annual_spend(self, phone: str, now: datetime | None = None) -> int:
        """Сумма реально оплаченных покупок за скользящие 12 месяцев."""
        now = self._now(now)
        start = (now - timedelta(days=365)).date().isoformat()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(base), 0) FROM loyalty_txns"
            " WHERE phone=? AND kind='accrue' AND date(at)>=?", (phone, start)).fetchone()
        return int(row[0] or 0)

    def rate(self, phone: str, now: datetime | None = None) -> int:
        spend = self.annual_spend(phone, now)
        if spend >= self.cfg["tier_high"]:
            return self.cfg["rate_high"]
        if spend >= self.cfg["tier_mid"]:
            return self.cfg["rate_mid"]
        return self.cfg["rate_base"]

    def tier_info(self, phone: str, now: datetime | None = None) -> dict[str, Any]:
        """Что показать клиенту: «до 7%, осталось 3 000 ₽»."""
        spend = self.annual_spend(phone, now)
        rate = self.rate(phone, now)
        mid, high = self.cfg["tier_mid"], self.cfg["tier_high"]
        if rate >= self.cfg["rate_high"]:
            return {"rate": rate, "spend": spend, "next_rate": None, "left": 0}
        target = mid if spend < mid else high
        next_rate = self.cfg["rate_mid"] if spend < mid else self.cfg["rate_high"]
        return {"rate": rate, "spend": spend, "next_rate": next_rate,
                "left": max(0, target - spend)}

    # ----- операции -----

    def has_welcome(self, phone: str) -> bool:
        return bool(self._account(norm_phone(phone)).welcome)

    def welcome(self, phone: str, now: datetime | None = None) -> int:
        """Приветственные баллы за первый указанный телефон. Только один раз."""
        phone = norm_phone(phone)
        if not phone:
            return 0
        now = self._now(now)
        if self._account(phone).welcome:
            return 0
        points = int(self.cfg["welcome"])
        self._touch(phone, now)
        # флаг — в той же транзакции, что и начисление: иначе падение между коммитами даст баллы дважды
        self.conn.execute("UPDATE loyalty_accounts SET welcome=1 WHERE phone=?", (phone,))
        self._txn(phone, now, "welcome", points, source="Регистрация в боте",
                  note="Приветственные баллы")
        return points

    def eligible_amount(self, lines: list[dict[str, Any]]) -> int:
        """Сумма покупки без уценённых позиций (они не дают и не тратят баллы)."""
        total = 0
        for line in lines:
            if line.get("discounted") or line.get("promo"):
                continue
            try:
                price = int(line.get("price", 0))
                qty = float(line.get("qty", 1))
            except (TypeError, ValueError):
                continue
            total += int(round(price * qty))
        return max(0, total)

    def max_spend(self, phone: str, eligible: int, now: datetime | None = None) -> int:
        """Сколько баллов можно списать: не больше баланса и не больше лимита %."""
        phone = norm_phone(phone)
        if not phone or eligible <= 0:
            return 0
        limit = int(eligible * self.cfg["pay_percent"] / 100)
        return max(0, min(self.balance(phone, now), limit))

    def balance(self, phone: str, now: datetime | None = None) -> int:
        return self._account(norm_phone(phone)).balance

    def accrue(self, phone: str, amount: int, *, now: datetime | None = None,
               source: str = "", author: str = "", doubled: bool = False) -> int:
        """Начислить баллы с оплаченной покупки. ``amount`` уже без уценённых позиций."""
        phone = norm_phone(phone)
        if not phone or amount <= 0:
            return 0
        now = self._now(now)
        rate = self.rate(phone, now)
        points = int(amount * rate / 100)  # в пользу магазина: дроби не округляем вверх
        if doubled and self.cfg["double_dates"]:
            points *= 2
        if points <= 0:
            return 0
        self._touch(phone, now)
        self._txn(phone, now, "accrue", points, rate=rate, base=int(amount),
                  source=source, author=author, doubled=doubled)
        return points

    def spend(self, phone: str, points: int, *, now: datetime | None = None,
              source: str = "", author: str = "") -> int:
        """Списать баллы. Возвращает реально списанное (может быть меньше запрошенного)."""
        phone = norm_phone(phone)
        points = int(points)
        if not phone or points <= 0:
            return 0
        now = self._now(now)
        can = self.balance(phone, now)
        spent = min(can, points)
        if spent <= 0:
            return 0
        self._txn(phone, now, "spend", -spent, source=source, author=author)
        return spent

    def adjust(self, phone: str, delta: int, *, note: str, author: str = "",
               now: datetime | None = None) -> int:
        """Ручная корректировка администратора. ``delta`` может быть отрицательным,
        но баланс ниже нуля не уходит — списывается не больше, чем есть."""
        phone = norm_phone(phone)
        delta = int(delta)
        if not phone or delta == 0 or not note.strip():
            raise ValueError("Нужны телефон, ненулевая сумма и причина")
        now = self._now(now)
        if delta < 0:
            delta = max(delta, -self.balance(phone, now))
            if delta == 0:
                return 0
        self._txn(phone, now, "adjust", delta, source="Корректировка",
                  author=author, note=note.strip())
        if delta > 0:
            self._touch(phone, now)
        return delta

    def expire(self, now: datetime | None = None) -> list[tuple[str, int]]:
        """Сжечь баллы, у которых прошел срок. Возвращает (телефон, сумма)."""
        now = self._now(now)
        today = now.date().isoformat()
        rows = self.conn.execute(
            "SELECT phone, balance FROM loyalty_accounts"
            " WHERE balance>0 AND expires_at<>'' AND expires_at<?", (today,)).fetchall()
        burnt: list[tuple[str, int]] = []
        for phone, balance in rows:
            self._txn(phone, now, "expire", -int(balance), source="Срок действия истёк")
            self.conn.execute(
                "UPDATE loyalty_accounts SET expires_at=NULL WHERE phone=?", (phone,))
            burnt.append((phone, int(balance)))
        self.conn.commit()
        return burnt

    # ----- памятные даты и напоминания -----

    def set_date(self, phone: str, label: str, day: str | date, *, now: datetime | None = None) -> None:
        """day — 'ДД.ММ' или date. Повторная запись с тем же label меняет дату."""
        phone = norm_phone(phone)
        if not phone or not label.strip():
            raise ValueError("Нужны телефон и подпись даты")
        if isinstance(day, date):
            text = f"{day.day:02d}.{day.month:02d}"
        else:
            text = str(day).strip().replace("-", ".")
            parts = text.split(".")
            if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
                raise ValueError("Дата должна быть в формате ДД.ММ")
            text = f"{int(parts[0]):02d}.{int(parts[1]):02d}"
        now = self._now(now)
        self._ensure(phone)
        old = self.conn.execute(
            "SELECT id FROM loyalty_dates WHERE phone=? AND label=?", (phone, label.strip())).fetchone()
        if old:
            self.conn.execute(
                "UPDATE loyalty_dates SET day=?, notified=0 WHERE id=?", (text, old[0]))
        else:
            self.conn.execute(
                "INSERT INTO loyalty_dates (phone, label, day) VALUES (?, ?, ?)",
                (phone, label.strip(), text))
        self.conn.commit()

    def dates(self, phone: str) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT label, day FROM loyalty_dates WHERE phone=? ORDER BY day, label",
            (norm_phone(phone),)).fetchall()
        return [{"label": r[0], "day": r[1]} for r in rows]

    def is_occasion(self, phone: str, delivery_day: date | str) -> bool:
        """Доставка на памятную дату ±1 день — повод начислить двойные баллы."""
        day = date.fromisoformat(delivery_day) if isinstance(delivery_day, str) else delivery_day
        target = f"{day.day:02d}.{day.month:02d}"
        before = day - timedelta(days=1)
        after = day + timedelta(days=1)
        variants = {target,
                    f"{before.day:02d}.{before.month:02d}",
                    f"{after.day:02d}.{after.month:02d}"}
        for row in self.dates(phone):
            if row["day"] in variants:
                return True
        return False

    def due_reminders(self, now: datetime | None = None) -> list[Reminder]:
        """Напоминания, которые пора отправить ботом (без повторной отправки)."""
        now = self._now(now)
        today = now.date()
        out: list[Reminder] = []

        if self.cfg["expire_reminders"]:
            rows = self.conn.execute(
                "SELECT phone, expires_at FROM loyalty_accounts"
                " WHERE balance>0 AND expires_at<>'' AND expires_at>=?",
                (today.isoformat(),)).fetchall()
            for phone, expires in rows:
                end = date.fromisoformat(expires)
                for days in (30, 7):
                    if end - timedelta(days=days) != today:
                        continue
                    key = f"{end.isoformat()}:{days}"
                    if self._remember(phone, "expire", key, now):
                        out.append(Reminder(
                            phone=phone, kind="expire", key=key,
                            text=(f"🎁 У вас {self.balance(phone, now)} бонусов. "
                                  f"Они сгорят через {days} дн. — {end:%d.%m}. "
                                  "Закажите букет, чтобы сохранить их.")))

        year = today.year
        rows = self.conn.execute(
            "SELECT id, phone, label, day, notified FROM loyalty_dates").fetchall()
        for row_id, phone, label, day, notified in rows:
            if notified >= year:
                continue
            dd, mm = (int(x) for x in day.split(".")[:2])
            try:
                occasion = date(year, mm, dd)
            except ValueError:  # 29.02 в невисокосный год
                occasion = date(year, mm, dd - 1)
            if occasion - timedelta(days=3) != today:
                continue
            key = f"{occasion.isoformat()}:3"
            if self._remember(phone, "date", key, now):
                self.conn.execute("UPDATE loyalty_dates SET notified=? WHERE id=?", (year, row_id))
                self.conn.commit()
                out.append(Reminder(
                    phone=phone, kind="date", key=key,
                    text=(f"💐 Через 3 дня — {label} ({occasion:%d.%m}). "
                          "Закажите букет с доставкой на этот день — начислим двойные бонусы!")))
        return out

    def _remember(self, phone: str, kind: str, key: str, now: datetime) -> bool:
        """True — напоминание новое, записать и отправить."""
        exists = self.conn.execute(
            "SELECT 1 FROM loyalty_reminders WHERE phone=? AND kind=? AND key=?",
            (phone, kind, key)).fetchone()
        if exists:
            return False
        self.conn.execute(
            "INSERT INTO loyalty_reminders (phone, kind, key, sent_at) VALUES (?, ?, ?, ?)",
            (phone, kind, key, now.isoformat(timespec="seconds")))
        self.conn.commit()
        return True

    # ----- история -----

    def accounts(self, query: str = "") -> list[dict[str, Any]]:
        """Все клиенты программы лояльности — для окна «Бонусы клиентов». ``query`` — цифры телефона."""
        digits = re.sub(r"\D", "", query)
        rows = self.conn.execute(
            "SELECT phone, balance, last_activity, COALESCE(expires_at, ''), welcome"
            " FROM loyalty_accounts ORDER BY balance DESC, last_activity DESC, phone").fetchall()
        return [{"phone": phone, "balance": balance, "last_activity": last,
                 "expires_at": expires, "welcome": bool(welcome)}
                for phone, balance, last, expires, welcome in rows
                if not digits or digits in phone]

    def history(self, phone: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT at, kind, amount, rate, doubled, source, author, note"
            " FROM loyalty_txns WHERE phone=? ORDER BY id DESC LIMIT ?",
            (norm_phone(phone), limit)).fetchall()
        names = {"accrue": "Начисление", "spend": "Списание", "expire": "Сгорели",
                 "welcome": "Приветственные", "adjust": "Корректировка"}
        return [{"at": r[0], "kind": r[1], "label": names.get(r[1], r[1]),
                 "amount": r[2], "rate": r[3], "doubled": bool(r[4]),
                 "source": r[5], "author": r[6], "note": r[7]} for r in rows]

    def summary(self, phone: str, now: datetime | None = None) -> str:
        phone = norm_phone(phone)
        acc = self._account(phone)
        tier = self.tier_info(phone, now)
        lines = [f"🎁 Баланс: {acc.balance} бонусов (= {acc.balance} ₽).",
                 f"Сейчас начисляем {tier['rate']}% с покупок."]
        if tier["next_rate"]:
            lines.append(f"До {tier['next_rate']}% осталось {tier['left']:,} ₽".replace(",", " "))
        if acc.expires:
            lines.append(f"Срок действия: до {acc.expires:%d.%m} (продлится после покупки).")
        if self.dates(phone):
            lines.append("Памятные даты: " + ", ".join(
                f"{d['label']} — {d['day']}" for d in self.dates(phone)))
        return "\n".join(lines)
