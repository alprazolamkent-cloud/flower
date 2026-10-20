from datetime import date

import pytest

from app.db import Database, Delivery


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def test_add_and_list_sorted_by_time(db):
    day = date(2026, 10, 20)
    db.add(Delivery(day=day, time="16:00", items="Розы"))
    db.add(Delivery(day=day, time="10:00", items="Тюльпаны"))
    db.add(Delivery(day=date(2026, 10, 21), items="Другой день"))

    items = [d.items for d in db.for_day(day)]
    assert items == ["Тюльпаны", "Розы"]


def test_remainder(db):
    d = Delivery(day=date(2026, 10, 20), price=700, courier_pay=400)
    assert d.remainder == 300


def test_update_delivered_and_delete(db):
    d = Delivery(day=date(2026, 10, 20), items="Букет")
    db.add(d)
    db.set_delivered(d.id, True)
    assert db.for_day(d.day)[0].delivered

    d.items = "Букет и шары"
    d.delivered = False
    db.update(d)
    stored = db.for_day(d.day)[0]
    assert stored.items == "Букет и шары" and not stored.delivered

    db.delete(d.id)
    assert db.for_day(d.day) == []


def test_month_counts_and_totals(db):
    db.add(Delivery(day=date(2026, 10, 1), price=500, courier_pay=300, delivered=True))
    db.add(Delivery(day=date(2026, 10, 1), price=600, courier_pay=350))
    db.add(Delivery(day=date(2026, 10, 15), price=1000, courier_pay=400, delivered=True))
    db.add(Delivery(day=date(2026, 11, 1), price=9999, courier_pay=1))

    counts = db.counts_for_month(2026, 10)
    assert counts == {date(2026, 10, 1): (2, 1), date(2026, 10, 15): (1, 1)}

    t = db.totals_for_month(2026, 10)
    assert (t.count, t.delivered, t.price, t.courier_pay, t.remainder) == (3, 2, 2100, 1050, 1050)

    day = db.totals_for_day(date(2026, 10, 1))
    assert (day.count, day.remainder) == (2, 450)

    empty = db.totals_for_day(date(2026, 12, 31))
    assert (empty.count, empty.price, empty.remainder) == (0, 0, 0)
