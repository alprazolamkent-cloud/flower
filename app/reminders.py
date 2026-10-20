"""Расчёт, о каких доставках пора напомнить."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import Delivery

TIME_RE = re.compile(r"(\d{1,2})\s*[:.\-]\s*(\d{2})")
HOUR_RE = re.compile(r"\b(\d{1,2})\b")


def parse_start_time(text: str) -> time | None:
    """Время начала доставки из строки: «14:00», «14.30», «14:00–16:00», «к 9»."""
    m = TIME_RE.search(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = HOUR_RE.search(text)
        if not m:
            return None
        h, mi = int(m.group(1)), 0
    if h > 23 or mi > 59:
        return None
    return time(h, mi)


def reminder_key(d: Delivery) -> tuple:
    # Если поменяли дату или время — напомним заново.
    return (d.id, d.day, d.time)


def due_reminders(deliveries: list[Delivery], now: datetime, lead_minutes: int,
                  already: set) -> list[Delivery]:
    """Недоставленные доставки, до которых осталось не больше lead_minutes минут."""
    due = []
    for d in deliveries:
        if d.delivered or reminder_key(d) in already:
            continue
        start = parse_start_time(d.time)
        if start is None:
            continue
        left = datetime.combine(d.day, start) - now
        if timedelta(0) <= left <= timedelta(minutes=lead_minutes):
            due.append(d)
    return due
