"""Смены: пересменка, к какой смене относится момент времени, статусы и расчёт кассы.

Смена «за 20 октября» идёт с пересменки 20-го (по умолчанию 8:00) до пересменки 21-го.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .reminders import parse_start_time

DEFAULT_CHANGE = time(8, 0)
DEFAULT_GRACE_MINUTES = 30  # сколько после пересменки ещё можно сдать смену (с опозданием)
REMIND_BEFORE_MINUTES = 30  # за сколько до пересменки напомнить, что пора сдавать смену
EARLY_START = timedelta(hours=1)  # пришли за час до пересменки — это уже следующая смена

STATUS_TEXT = {
    "open": "идёт",
    "closing": "ждёт сдачи",
    "closed": "сдана",
    "late": "сдана с опозданием",
    "not_closed": "не сдана",
}
PROBLEM_STATUSES = {"closing", "late", "not_closed"}

KIND_TEXT = {"expense": "Расход", "withdrawal": "Изъятие"}


def business_day(moment: datetime, change: time) -> date:
    """К смене какого дня относится момент: ночь до пересменки — это ещё вчерашняя смена."""
    return moment.date() if moment.time() >= change else moment.date() - timedelta(days=1)


def opening_business_day(moment: datetime, change: time) -> date:
    """Какую смену начинают в этот момент (пришли чуть раньше пересменки — уже новая)."""
    return business_day(moment + EARLY_START, change)


def window(day: date, change: time) -> tuple[datetime, datetime]:
    start = datetime.combine(day, change)
    return start, start + timedelta(days=1)


def delivery_business_day(d, change: time) -> date:
    """Смена, на которую приходится доставка (без понятного времени — дневная)."""
    t = parse_start_time(d.time)
    if t is None:
        return d.day
    return business_day(datetime.combine(d.day, t), change)


def expected_cash(opening: int, cash_sales: int, delivery_cash: int, courier_cash: int,
                  expenses: int, withdrawals: int) -> int:
    """Сколько наличных должно быть в кассе в конце смены.

    delivery_cash — только у старых смен: за доставку платят переводом или по QR, в кассу это не идёт.
    """
    return opening + cash_sales + delivery_cash - courier_cash - expenses - withdrawals


def revenue_mismatch(revenue: int, cash_sales: int, terminal: int, qr: int) -> int:
    """Приход − (наличные + терминал + QR): не 0 — где-то ошиблись или были переводы."""
    return revenue - (cash_sales + terminal + qr)


@dataclass
class WaterSchedule:
    """Вода меняется раз в every дней, начиная с anchor (смена anchor — день замены)."""
    anchor: date
    every: int = 2

    def is_water_day(self, day: date) -> bool:
        return (day - self.anchor).days % max(self.every, 1) == 0

    def status(self, day: date, done: bool, now: datetime, change: time) -> str | None:
        """None — не день воды (или до начала учёта); done — поменяли; missed — не поменяли; planned."""
        if day < self.anchor or not self.is_water_day(day):
            return None
        if done:
            return "done"
        return "missed" if now >= window(day, change)[1] else "planned"


def short_name(name: str) -> str:
    return name.split()[0] if name.strip() else "—"


@dataclass
class CashOp:
    id: int
    kind: str  # expense | withdrawal
    amount: int
    reason: str
    created_at: datetime
    created_by_name: str = ""


@dataclass
class Shift:
    id: int
    user_id: int | None
    user_name: str
    day: date
    opened_at: datetime
    opening_cash: int
    closed_at: datetime | None = None
    closing_cash: int | None = None
    cash_sales: int = 0  # продажи наличными из Hoog
    delivery_cash: int = 0  # только у старых смен
    courier_cash: int = 0
    note: str = ""
    revenue: int = 0  # приход: вся выручка за смену любыми способами
    flowers: int = 0  # из прихода — цветы
    terminal: int = 0  # оплаты картой
    qr: int = 0  # оплаты по QR-коду
    deliveries: int = 0  # взяли за доставки (перевод / QR), в кассу не идёт
    expenses: int = 0
    withdrawals: int = 0

    def end(self, change: time) -> datetime:
        return window(self.day, change)[1]

    def deadline(self, change: time, grace_minutes: int) -> datetime:
        """До какого времени ещё можно сдать смену."""
        return self.end(change) + timedelta(minutes=grace_minutes)

    def expected_cash(self) -> int:
        return expected_cash(self.opening_cash, self.cash_sales, self.delivery_cash,
                             self.courier_cash, self.expenses, self.withdrawals)

    def difference(self) -> int | None:
        """Фактически − должно быть: минус — недостача, плюс — излишек."""
        if self.closed_at is None or self.closing_cash is None:
            return None
        return self.closing_cash - self.expected_cash()

    def status(self, now: datetime, change: time, grace_minutes: int) -> str:
        end = self.end(change)
        if self.closed_at is not None:
            return "closed" if self.closed_at <= end else "late"
        if now < end:
            return "open"
        if now < self.deadline(change, grace_minutes):
            return "closing"
        return "not_closed"

    def can_close(self, now: datetime, change: time, grace_minutes: int) -> bool:
        return self.status(now, change, grace_minutes) in ("open", "closing")
