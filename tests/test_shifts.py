from datetime import date, datetime, time

import pytest

from app.db import Database, Delivery, UserError
from app.shifts import (
    business_day, delivery_business_day, expected_cash, opening_business_day, window,
)

CHANGE = time(8, 0)
GRACE = 30


def test_business_day():
    assert business_day(datetime(2026, 10, 20, 8, 0), CHANGE) == date(2026, 10, 20)
    assert business_day(datetime(2026, 10, 20, 23, 59), CHANGE) == date(2026, 10, 20)
    assert business_day(datetime(2026, 10, 21, 7, 59), CHANGE) == date(2026, 10, 20)  # ночь — та же смена
    assert window(date(2026, 10, 20), CHANGE) == (datetime(2026, 10, 20, 8), datetime(2026, 10, 21, 8))


def test_opening_slightly_early_starts_next_shift():
    assert opening_business_day(datetime(2026, 10, 21, 7, 40), CHANGE) == date(2026, 10, 21)
    assert opening_business_day(datetime(2026, 10, 21, 8, 10), CHANGE) == date(2026, 10, 21)
    assert opening_business_day(datetime(2026, 10, 21, 3, 0), CHANGE) == date(2026, 10, 20)


def test_delivery_business_day():
    assert delivery_business_day(Delivery(day=date(2026, 10, 21), time="02:00"), CHANGE) == date(2026, 10, 20)
    assert delivery_business_day(Delivery(day=date(2026, 10, 21), time="14:00"), CHANGE) == date(2026, 10, 21)
    assert delivery_business_day(Delivery(day=date(2026, 10, 21), time=""), CHANGE) == date(2026, 10, 21)


def test_expected_cash_formula():
    # 5000 на начало + 12000 продажи + 1000 за доставки − 400 такси − 300 расход − 6000 забрали
    assert expected_cash(5000, 12000, 1000, 400, 300, 6000) == 11300


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def test_shift_lifecycle_and_statuses(db):
    anna = db.add_user("anna", "Анна Петрова", "1")
    s = db.open_shift(anna.id, 5000, datetime(2026, 10, 20, 8, 10), CHANGE)
    assert s.day == date(2026, 10, 20) and s.user_name == "Анна Петрова"
    assert db.current_shift(anna.id).id == s.id

    assert s.status(datetime(2026, 10, 21, 7, 50), CHANGE, GRACE) == "open"
    assert s.status(datetime(2026, 10, 21, 8, 15), CHANGE, GRACE) == "closing"
    assert s.status(datetime(2026, 10, 21, 8, 31), CHANGE, GRACE) == "not_closed"
    assert not s.can_close(datetime(2026, 10, 21, 8, 31), CHANGE, GRACE)

    db.add_cash_op(s.id, "expense", 300, "скотч", anna.id, datetime(2026, 10, 20, 12, 0))
    db.add_cash_op(s.id, "withdrawal", 6000, "хозяйка", anna.id, datetime(2026, 10, 20, 20, 0))
    closed = db.close_shift(s.id, datetime(2026, 10, 21, 7, 55), 11000, 12000, 1000, 400, "")
    assert (closed.expenses, closed.withdrawals) == (300, 6000)
    assert closed.expected_cash() == 11300
    assert closed.difference() == -300  # недостача 300
    assert closed.status(datetime(2026, 10, 21, 9, 0), CHANGE, GRACE) == "closed"
    assert db.current_shift(anna.id) is None
    assert [o.reason for o in db.cash_ops(s.id)] == ["скотч", "хозяйка"]

    with pytest.raises(UserError):  # дважды сдать нельзя
        db.close_shift(s.id, datetime(2026, 10, 21, 7, 56), 1, 1, 1, 1)


def test_late_close(db):
    u = db.add_user("oleg", "", "1")
    s = db.open_shift(u.id, 0, datetime(2026, 10, 20, 8, 5), CHANGE)
    s = db.close_shift(s.id, datetime(2026, 10, 21, 8, 20), 0, 0, 0, 0)
    assert s.status(datetime(2026, 10, 21, 9, 0), CHANGE, GRACE) == "late"
    assert s.user_name == "oleg"  # без имени — логин


def test_handover_between_shifts(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    first = db.open_shift(anna.id, 5000, datetime(2026, 10, 20, 8, 10), CHANGE)
    db.close_shift(first.id, datetime(2026, 10, 21, 7, 55), 12000, 7000, 0, 0)
    assert db.shift_before(datetime(2026, 10, 21, 8, 10)).closing_cash == 12000

    second = db.open_shift(oleg.id, 11500, datetime(2026, 10, 21, 8, 10), CHANGE)
    assert db.previous_shift(second).id == first.id
    assert db.handover_difference(second) == -500  # приняли на 500 меньше, чем сдали
    assert db.handover_difference(first) is None  # до первой смены ничего не было


def test_cash_op_validation(db):
    u = db.add_user("anna", "", "1")
    s = db.open_shift(u.id, 0, datetime(2026, 10, 20, 9, 0), CHANGE)
    for kind, amount, reason in (("expense", 0, "x"), ("expense", 100, " "), ("gift", 100, "x")):
        with pytest.raises(UserError):
            db.add_cash_op(s.id, kind, amount, reason, u.id, datetime(2026, 10, 20, 10, 0))


def test_shifts_between_and_deleted_user(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    db.open_shift(anna.id, 0, datetime(2026, 10, 1, 8, 0), CHANGE)
    db.open_shift(oleg.id, 0, datetime(2026, 10, 2, 8, 0), CHANGE)
    db.open_shift(anna.id, 0, datetime(2026, 11, 1, 8, 0), CHANGE)
    october = db.shifts_between(date(2026, 10, 1), date(2026, 10, 31))
    assert [s.user_name for s in october] == ["Анна", "Олег"]
    assert len(db.shifts_between(date(2026, 10, 1), date(2026, 11, 30), anna.id)) == 2

    db.delete_user(oleg.id)  # смены остаются в истории
    assert [s.user_name for s in db.shifts_between(date(2026, 10, 2), date(2026, 10, 2))] == [""]


def test_delivery_cash_for_shift(db):
    db.add(Delivery(day=date(2026, 10, 20), time="14:00", price=500, courier_pay=300))
    db.add(Delivery(day=date(2026, 10, 21), time="02:00", price=700, courier_pay=400))  # ночь — смена 20-го
    db.add(Delivery(day=date(2026, 10, 21), time="10:00", price=900, courier_pay=500))  # уже смена 21-го
    db.add(Delivery(day=date(2026, 10, 20), time="07:00", price=100, courier_pay=50))  # ещё смена 19-го
    assert db.delivery_cash_for_shift(date(2026, 10, 20), CHANGE) == (1200, 700)


def test_shift_note_is_kept_for_next_shift(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    s = db.open_shift(anna.id, 1000, datetime(2026, 10, 20, 8, 5), CHANGE)
    db.close_shift(s.id, datetime(2026, 10, 21, 7, 50), 1000, 0, 0, 0, "  закончился скотч\nклиентка заберёт в 10:00  ")
    prev = db.shift_before(datetime(2026, 10, 21, 8, 10))
    assert prev.note == "закончился скотч\nклиентка заберёт в 10:00"
    nxt = db.open_shift(oleg.id, 1000, datetime(2026, 10, 21, 8, 10), CHANGE)
    assert db.previous_shift(nxt).note.startswith("закончился скотч")
