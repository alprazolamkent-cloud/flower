from datetime import date, datetime, time

from app.db import Delivery
from app.reminders import due_reminders, parse_start_time, reminder_key


def test_parse_start_time():
    assert parse_start_time("14:00") == time(14, 0)
    assert parse_start_time("9.30") == time(9, 30)
    assert parse_start_time("14:00–16:00") == time(14, 0)
    assert parse_start_time("с 10:15 до 12") == time(10, 15)
    assert parse_start_time("к 9") == time(9, 0)
    assert parse_start_time("") is None
    assert parse_start_time("вечером") is None
    assert parse_start_time("25:00") is None


def test_due_reminders():
    day = date(2026, 10, 20)
    now = datetime(2026, 10, 20, 13, 45)
    soon = Delivery(id=1, day=day, time="14:00")
    later = Delivery(id=2, day=day, time="15:00")
    past = Delivery(id=3, day=day, time="13:30")
    done = Delivery(id=4, day=day, time="14:00", delivered=True)
    no_time = Delivery(id=5, day=day, time="")
    deliveries = [soon, later, past, done, no_time]

    assert due_reminders(deliveries, now, 20, set()) == [soon]
    assert due_reminders(deliveries, now, 20, {reminder_key(soon)}) == []

    soon.time = "14:05"  # время поменяли — напоминаем снова
    assert due_reminders(deliveries, now, 20, {(1, day, "14:00")}) == [soon]
