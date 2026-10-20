"""Резервные копии, зарплата, Telegram и обязанности смены."""

import os
from datetime import date, datetime, time

import pytest

from app import backup, telegram
from app.db import Database, Delivery, UserError
from app.salary import SalaryRules, calculate

CH = time(8, 0)


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "shop.db"))
    yield database
    database.close()


# ----- резервные копии -----

def test_backup_local_and_extra(db, tmp_path):
    db.add(Delivery(day=date(2026, 10, 20), items="Розы"))
    usb = tmp_path / "usb"
    usb.mkdir()
    r = backup.make_backups(db, str(usb), 30, datetime(2026, 10, 20, 9, 0, 0))
    assert os.path.exists(r.local) and os.path.dirname(r.local) == backup.local_dir(db.path)
    assert r.extra and os.path.exists(r.extra) and r.extra_error is None
    copy = Database(r.extra)
    assert [d.items for d in copy.for_day(date(2026, 10, 20))] == ["Розы"]
    copy.close()


def test_backup_without_usb_still_saves_locally(db, tmp_path):
    r = backup.make_backups(db, str(tmp_path / "нет_флешки"), 30, datetime(2026, 10, 20, 9, 0))
    assert os.path.exists(r.local) and r.extra is None and "не найдена" in r.extra_error


def test_backup_keeps_only_last_copies(db):
    for minute in range(5):
        backup.make_backups(db, "", 3, datetime(2026, 10, 20, 9, minute))
    copies = sorted(os.listdir(backup.local_dir(db.path)))
    assert len(copies) == 3 and copies[-1].endswith("09-04-00.db")
    assert backup.latest(backup.local_dir(db.path)).endswith("09-04-00.db")


def test_restore_from_backup(db, tmp_path):
    db.add(Delivery(day=date(2026, 10, 20), items="Старая"))
    r = backup.make_backups(db, "", 30, datetime(2026, 10, 20, 9, 0))
    db.add(Delivery(day=date(2026, 10, 20), items="Новая"))
    db.restore(r.local)
    assert [d.items for d in db.for_day(date(2026, 10, 20))] == ["Старая"]
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database")
    with pytest.raises(UserError):
        db.restore(str(junk))
    assert [d.items for d in db.for_day(date(2026, 10, 20))] == ["Старая"]  # данные не пострадали


# ----- зарплата -----

def _closed_shift(db, user, day, closing, cash_sales, flowers):
    s = db.open_shift(user.id, 0, datetime.combine(day, time(8, 5)), CH)
    return db.close_shift(s.id, datetime.combine(day, time(23, 0)), closing, cash_sales, 0,
                          revenue=flowers, flowers=flowers)


def test_salary_calculation(db):
    anna = db.add_user("anna", "Анна", "1")
    _closed_shift(db, anna, date(2026, 10, 1), 10000, 10000, 30000)  # сходится
    _closed_shift(db, anna, date(2026, 10, 3), 9700, 10000, 20000)  # недостача 300
    db.open_shift(anna.id, 0, datetime(2026, 10, 5, 8, 0), CH)  # не сдана
    db.add_adjustment(anna.id, "2026-10", -5000, "аванс", None, datetime(2026, 10, 10, 12, 0))
    db.add_adjustment(anna.id, "2026-10", 1000, "премия", None, datetime(2026, 10, 15, 12, 0))
    db.add_adjustment(anna.id, "2026-11", -9999, "другой месяц", None, datetime(2026, 11, 1, 12, 0))

    shifts = db.shifts_between(date(2026, 10, 1), date(2026, 10, 31), anna.id)
    sal = calculate(shifts, db.adjustments(anna.id, "2026-10"), SalaryRules(2000, 5, True))
    assert (sal.shifts, sal.base, sal.flowers, sal.percent_part) == (2, 4000, 50000, 2500)
    assert (sal.shortage, sal.not_closed, sal.adjustments_total) == (300, 1, -4000)
    assert sal.total == 4000 + 2500 - 300 - 4000

    no_deduct = calculate(shifts, [], SalaryRules(2000, 5, False))
    assert no_deduct.shortage == 0 and no_deduct.total == 6500


def test_adjustment_validation_and_delete(db):
    anna = db.add_user("anna", "", "1")
    with pytest.raises(UserError):
        db.add_adjustment(anna.id, "2026-10", 0, "аванс", None, datetime(2026, 10, 1))
    with pytest.raises(UserError):
        db.add_adjustment(anna.id, "2026-10", 100, " ", None, datetime(2026, 10, 1))
    adj = db.add_adjustment(anna.id, "2026-10", 100, "премия", None, datetime(2026, 10, 1))
    db.delete_adjustment(adj)
    assert db.adjustments(anna.id, "2026-10") == []


# ----- Telegram -----

def test_outbox_dedup_and_sent(db):
    assert db.queue_message("привет", datetime(2026, 10, 20, 9, 0), key="not_closed:1")
    assert not db.queue_message("привет ещё раз", datetime(2026, 10, 20, 9, 1), key="not_closed:1")
    db.queue_message("без ключа", datetime(2026, 10, 20, 9, 2))
    items = db.unsent_messages()
    assert [t for _, t in items] == ["привет", "без ключа"]
    db.mark_message_sent(items[0][0], datetime(2026, 10, 20, 9, 3))
    assert [t for _, t in db.unsent_messages()] == ["без ключа"]


def test_shift_closed_text(db):
    anna = db.add_user("anna", "Анна", "1")
    s = db.open_shift(anna.id, 5000, datetime(2026, 10, 20, 8, 5), CH)
    db.add_cash_op(s.id, "withdrawal", 10000, "хозяйка", anna.id, datetime(2026, 10, 20, 20, 0))
    s = db.close_shift(s.id, datetime(2026, 10, 21, 7, 50), 6700, 12000, 0, "всё ок",
                       revenue=30000, flowers=25000, terminal=15000, qr=3000)
    text = telegram.shift_closed_text(s, CH, False, "missed", ["протереть витрину"])
    assert "Анна" in text and "30 000 ₽" in text and "недостача 300 ₽" in text
    assert "Вода не поменяна" in text and "всё ок" in text and "Изъятия: 10 000 ₽" in text
    assert "сходится" in telegram.diff_words(0)


def test_find_chats_parses_updates(monkeypatch):
    updates = {"ok": True, "result": [
        {"message": {"chat": {"id": 111, "first_name": "Мария", "last_name": "Иванова"}}},
        {"message": {"chat": {"id": -222, "title": "Магазин"}}},
        {"message": {"chat": {"id": 111, "first_name": "Мария", "last_name": "Иванова"}}},
    ]}
    monkeypatch.setattr(telegram, "_call", lambda token, method, params=None: updates)
    assert telegram.find_chats("x") == [("111", "Мария Иванова"), ("-222", "Магазин")]


# ----- обязанности -----

def test_duties_checklist_lifecycle(db):
    anna = db.add_user("anna", "Анна", "1")
    db.save_duties([(None, "Протереть витрину"), (None, "Полить растения"), (None, "  ")])
    duties = db.duties()
    assert [d.text for d in duties] == ["Протереть витрину", "Полить растения"]

    s = db.open_shift(anna.id, 0, datetime(2026, 10, 20, 8, 5), CH)
    assert db.get_shift(s.id).duties_total == 2
    db.set_duty_done(s.id, duties[0].id, duties[0].text, True, datetime(2026, 10, 20, 10, 0))
    assert [i.done for i in db.checklist(db.get_shift(s.id))] == [True, False]
    db.set_duty_done(s.id, duties[0].id, duties[0].text, False, datetime(2026, 10, 20, 10, 5))
    db.set_duty_done(s.id, duties[1].id, duties[1].text, True, datetime(2026, 10, 20, 11, 0))

    closed = db.close_shift(s.id, datetime(2026, 10, 21, 7, 50), 0, 0, 0)
    assert (closed.duties_done, closed.duties_total) == (1, 2)

    # список поменяли — у сданной смены история прежняя
    db.save_duties([(duties[1].id, "Полить все растения"), (None, "Вынести мусор")])
    assert [i.text for i in db.checklist(closed)] == ["Протереть витрину", "Полить растения"]
    assert [d.text for d in db.duties()] == ["Полить все растения", "Вынести мусор"]


# ----- найденные при проверке ошибки -----

def test_clearing_duty_text_removes_it(db):
    db.save_duties([(None, "Протереть витрину"), (None, "Вынести мусор")])
    first, second = db.duties()
    db.save_duties([(first.id, "Протереть витрину"), (second.id, "   ")])
    assert [d.text for d in db.duties()] == ["Протереть витрину"]


def test_done_never_exceeds_total_after_duty_removed(db):
    anna = db.add_user("anna", "", "1")
    db.save_duties([(None, "А"), (None, "Б")])
    a, b = db.duties()
    s = db.open_shift(anna.id, 0, datetime.now(), CH)
    for duty in (a, b):
        db.set_duty_done(s.id, duty.id, duty.text, True, datetime.now())
    db.save_duties([(a.id, "А")])
    s = db.get_shift(s.id)
    assert (s.duties_done, s.duties_total) == (1, 1)


def test_cannot_delete_user_during_shift(db):
    anna = db.add_user("anna", "", "1")
    s = db.open_shift(anna.id, 0, datetime.now(), CH)
    with pytest.raises(UserError):
        db.delete_user(anna.id)
    db.close_shift(s.id, datetime.now(), 0, 0, 0)
    db.delete_user(anna.id)  # после сдачи — можно
    assert [u.login for u in db.users()] == []


def test_long_telegram_text_is_shortened(db, monkeypatch):
    monkeypatch.setattr(telegram, "configured", lambda: True)
    sender = telegram.TelegramSender.__new__(telegram.TelegramSender)  # без таймера Qt
    sender.db = db
    monkeypatch.setattr(telegram.QTimer, "singleShot", lambda *a: None)
    sender.queue("x" * 10_000)
    [(_, text)] = db.unsent_messages()
    assert len(text) <= telegram.MAX_TEXT
