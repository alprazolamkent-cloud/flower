"""Расчёт, о каких доставках пора напомнить."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import Delivery

# «14:00», «14.30» или просто час «14». Дефис — это промежуток («14-16»), а не минуты.
TIME_RE = re.compile(r"(?<!\d)(\d{1,2})(?:\s*[:.]\s*(\d{2}))?(?!\d)")


def _times(text: str) -> list[time | None]:
    found = []
    for m in TIME_RE.finditer(text or ""):
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        found.append(time(h, mi) if h <= 23 and mi <= 59 else None)
    return found


def parse_start_time(text: str) -> time | None:
    """Время начала доставки из строки: «14:00», «14.30», «14:00–16:00», «14-16», «к 9»."""
    found = _times(text)
    return found[0] if found else None


def parse_deadline_time(text: str) -> time | None:
    """Крайнее время доставки: для «14:00–16:00», «14-16» и «с 14 до 16» это 16:00, для «14:00» — 14:00."""
    valid = [t for t in _times(text) if t is not None]
    return valid[-1] if valid and parse_start_time(text) is not None else None


def reminder_key(d: Delivery) -> tuple:
    # Если поменяли дату или время — напомним заново.
    return (d.id, d.day, d.time)


def due_reminders(deliveries: list[Delivery], now: datetime, lead_minutes: int,
                  already: set) -> list[Delivery]:
    """Недоставленные доставки, до которых осталось не больше lead_minutes минут (кроме ждущих удаления)."""
    due = []
    for d in deliveries:
        if d.delivered or d.delete_requested_at is not None or reminder_key(d) in already:
            continue
        start = parse_start_time(d.time)
        if start is None:
            continue
        left = datetime.combine(d.day, start) - now
        if timedelta(0) <= left <= timedelta(minutes=lead_minutes):
            due.append(d)
    return due


OVERDUE_MAX_AGE = timedelta(hours=6)  # про совсем старые не напоминаем: скорее всего просто забыли отметить


def overdue_deliveries(deliveries: list[Delivery], now: datetime, minutes: int, already: set) -> list[Delivery]:
    """Доставки, которые не отмечены «Доставлено» уже minutes минут после крайнего времени.
    minutes = 0 — не напоминать. Те, что ждут удаления, пропускаем."""
    if minutes <= 0:
        return []
    late = []
    for d in deliveries:
        if d.delivered or d.delete_requested_at is not None or ("late",) + reminder_key(d) in already:
            continue
        deadline = parse_deadline_time(d.time)
        if deadline is None:
            continue
        overdue = now - (datetime.combine(d.day, deadline) + timedelta(minutes=minutes))
        if timedelta(0) <= overdue <= OVERDUE_MAX_AGE:
            late.append(d)
    return late


ASSEMBLY_KEEP = timedelta(hours=1)  # после крайнего времени про сборку уже не напоминаем — там «не отмечена»


def _needs_assembly(d: Delivery) -> bool:
    return not d.assembled and not d.delivered and d.delete_requested_at is None


def assembly_due(deliveries: list[Delivery], now: datetime, lead_minutes: int) -> list[Delivery]:
    """Несобранные букеты, до доставки которых осталось lead_minutes минут или меньше (и пока не поздно).
    lead_minutes = 0 — не напоминать. Заказы на дальние дни сюда не попадают: собирать их рано."""
    if lead_minutes <= 0:
        return []
    due = []
    for d in deliveries:
        start = parse_start_time(d.time)
        if not _needs_assembly(d) or start is None:
            continue
        begin = datetime.combine(d.day, start)
        end = datetime.combine(d.day, parse_deadline_time(d.time) or start) + ASSEMBLY_KEEP
        if begin - timedelta(minutes=lead_minutes) <= now <= end:
            due.append(d)
    return sorted(due, key=lambda d: (d.day, parse_start_time(d.time)))


def assembly_escalations(deliveries: list[Delivery], now: datetime, minutes: int, already: set) -> list[Delivery]:
    """Букет так и не собран за minutes минут до доставки — пора сообщить администраторам (один раз).
    minutes = 0 — не сообщать."""
    if minutes <= 0:
        return []
    return [d for d in assembly_due(deliveries, now, minutes) if ("assembly",) + reminder_key(d) not in already]
