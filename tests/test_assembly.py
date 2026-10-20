"""Фаза «собрано», окно «Не собрано!», план на смену и копия базы в Telegram."""

import os
import sqlite3
import sys
import zipfile
from datetime import date, datetime, time, timedelta

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from app import backup, telegram
from app import main_window as mw
from app.assembly_dialog import AssemblyReminder, left_text
from app.bot_commands import BotContext, BotHandler
from app.db import Database, Delivery, UserError
from app.reminders import assembly_due, assembly_escalations
from app.salary import SalaryRules
from app.shift_plan import plan_html
from app.shifts import WaterSchedule

CH = time(8, 0)
NOW = datetime(2026, 10, 20, 11, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


# ----- база -----

def test_old_database_delivered_become_assembled(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""CREATE TABLE deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
        time TEXT NOT NULL DEFAULT '', items TEXT NOT NULL DEFAULT '', recipient TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL DEFAULT 0,
        courier_pay INTEGER NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0);
        INSERT INTO deliveries (day, items, delivered) VALUES ('2026-10-19', 'старая доставлена', 1);
        INSERT INTO deliveries (day, items, delivered) VALUES ('2026-10-25', 'на потом', 0);""")
    conn.commit()
    conn.close()
    db = Database(path)
    done, later = db.for_day(date(2026, 10, 19))[0], db.for_day(date(2026, 10, 25))[0]
    assert done.assembled and not later.assembled
    db.close()


def test_assembled_flags(db):
    anna = db.add_user("anna", "Анна", "1")
    d = Delivery(day=NOW.date(), time="12:00", items="Розы")
    db.add(d)
    assert not db.get_delivery(d.id).assembled
    db.set_assembled(d.id, True, anna.id, NOW)
    got = db.get_delivery(d.id)
    assert got.assembled and got.assembled_by_name == "Анна" and got.assembled_at == NOW
    db.set_assembled(d.id, False, anna.id, NOW)
    assert not db.get_delivery(d.id).assembled
    db.set_delivered(d.id, True, anna.id, NOW)  # доставили, не отметив сборку — значит, собрано
    got = db.get_delivery(d.id)
    assert got.assembled and got.assembled_by == anna.id
    db.set_assembled(d.id, False, anna.id, NOW)  # у доставленного «собрано» не снимается
    assert db.get_delivery(d.id).assembled
    totals = db.totals_for_day(NOW.date())
    assert (totals.count, totals.assembled, totals.delivered) == (1, 1, 1)


def test_add_and_update_keep_assembly(db):
    d = Delivery(day=NOW.date(), time="12:00", items="Розы", assembled=True, assembled_at=NOW)
    db.add(d)
    assert db.get_delivery(d.id).assembled
    d.assembled = False
    db.update(d)
    got = db.get_delivery(d.id)
    assert not got.assembled and got.assembled_at is None


# ----- когда напоминать -----

def test_assembly_due():
    day = NOW.date()
    mk = lambda i, t, **kw: Delivery(id=i, day=day, time=t, **kw)
    soon = mk(1, "11:45")  # через 45 минут
    later = mk(2, "12:30")  # через полтора часа — рано
    done = mk(3, "11:30", assembled=True)
    delivered = mk(4, "11:30", delivered=True)
    no_time = mk(5, "")
    asked_delete = mk(6, "11:30", delete_requested_at=NOW)
    window = mk(7, "10:00–12:00")  # окно уже началось, а букет не собран
    week = Delivery(id=8, day=day + timedelta(days=7), time="11:30")
    gone = mk(9, "09:00")  # прошло больше часа после времени — тут уже «не отмечена»
    items = [soon, later, done, delivered, no_time, asked_delete, window, week, gone]
    assert [d.id for d in assembly_due(items, NOW, 60)] == [7, 1]
    assert assembly_due(items, NOW, 0) == []
    assert [d.id for d in assembly_due(items, NOW, 120)] == [7, 1, 2]
    assert [d.id for d in assembly_escalations(items, NOW, 30, set())] == [7]  # «через 45 мин» — ещё не тревога
    assert assembly_escalations(items, NOW, 30, {("assembly", 7, day, "10:00–12:00")}) == []


def test_left_text():
    d = Delivery(day=NOW.date(), time="12:10")
    assert left_text(d, NOW) == "через 1 ч 10 мин"
    assert left_text(d, NOW + timedelta(minutes=50)) == "через 20 мин"
    assert left_text(d, NOW + timedelta(hours=2)) == "время уже прошло!"


# ----- план на смену -----

def test_plan_html(db):
    anna = db.add_user("anna", "Анна", "1")
    db.save_duties([(None, "Протереть витрину")])
    shift = db.open_shift(anna.id, 5000, datetime(2026, 10, 20, 8, 5), CH)
    db.add(Delivery(day=date(2026, 10, 20), time="12:00", items="Розы\nоткрытка", address="Ленина, 1"))
    db.add(Delivery(day=date(2026, 10, 20), time="15:00", items="Тюльпаны", assembled=True))
    db.add(Delivery(day=date(2026, 10, 21), time="02:00", items="Ночной букет"))  # ночь — ещё эта смена
    db.add(Delivery(day=date(2026, 10, 24), time="10:00", items="На субботу"))
    html = plan_html(db, shift, CH, WaterSchedule(date(2026, 10, 20), 2), 60, datetime(2026, 10, 20, 8, 10))
    assert "Доставки за смену: 3" in html and "собрать: <b>2</b>" in html
    assert "собрать к 11:00" in html and "💐 собрано" in html and "Ночной букет" in html and "(21.10)" in html
    assert "Розы" in html and "открытка" not in html  # только первая строка
    assert "на следующие 7 дней: <b>1</b>" in html and "собирать заранее не нужно" in html
    assert "день смены воды" in html and "Протереть витрину" in html
    later = plan_html(db, shift, CH, None, 60, datetime(2026, 10, 20, 11, 30))
    assert "собрать сейчас!" in later  # 12:00, а сборка — с 11:00
    assert "время прошло" in plan_html(db, shift, CH, None, 60, datetime(2026, 10, 20, 12, 30))


def test_plan_empty(db):
    anna = db.add_user("anna", "Анна", "1")
    shift = db.open_shift(anna.id, 0, datetime(2026, 10, 20, 8, 5), CH)
    assert "Доставок за смену нет" in plan_html(db, shift, CH, None, 60)


# ----- копия в Telegram -----

def test_zip_and_extract(db, tmp_path):
    db.add(Delivery(day=NOW.date(), items="Розы"))
    copy = str(tmp_path / "deliveries-2026.db")
    db.backup(copy)
    archive = backup.zip_copy(copy, NOW)
    assert archive.endswith(".zip") and os.path.basename(archive).isascii()
    restored = Database(backup.extract_db(archive))
    assert restored.for_day(NOW.date())[0].items == "Розы"
    restored.close()
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(UserError):
        backup.extract_db(str(bad))
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(UserError):
        backup.extract_db(str(empty))


def test_multipart_body():
    body, content_type = telegram.multipart({"chat_id": "42", "caption": "Копия"}, "document", "файл.zip", b"DATA")
    boundary = content_type.split("boundary=")[1]
    text = body.decode("utf-8", "replace")
    assert text.startswith(f"--{boundary}") and text.endswith(f"--{boundary}--\r\n")
    assert 'name="chat_id"\r\n\r\n42' in text and "Копия" in text and 'filename="____.zip"' in text and "DATA" in text


def test_bot_deliveries_show_assembly(db):
    db.add(Delivery(day=NOW.date(), time="12:00", items="Розы", assembled=True))
    db.add(Delivery(day=NOW.date(), time="13:00", items="Тюльпаны"))
    ctx = BotContext(db=db, now=NOW, change=CH, grace=30, water=WaterSchedule(NOW.date(), 2),
                     rules=lambda uid: SalaryRules(0))
    text = BotHandler().handle("/доставки", ctx)
    assert "собрано 1" in text and "💐 12:00 Розы" in text and "⏳ 13:00 Тюльпаны" in text


# ----- в программе -----

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(app, db, monkeypatch):
    store = {"tg_token": "x", "tg_chat_id": "111"}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: store.__setitem__(key, value))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    w = mw.MainWindow(db)
    w.tray.showMessage = lambda *a: None
    w.queued = []
    w.telegram.queue = lambda text, key=None, chat=None, except_chat=None: w.queued.append((text, key))
    w.store = store
    w.set_user(db.add_user("anna", "Анна", "1"))
    yield w
    w.assembly.hide()


def test_reminder_window_until_assembled(window):
    w, db = window, window.db
    now = datetime.now().replace(second=0, microsecond=0)
    d = Delivery(day=now.date(), time=f"{(now + timedelta(minutes=20)):%H:%M}", items="Розы", address="Ленина, 1")
    if d.time < f"{now:%H:%M}":  # около полуночи — пропускаем, чтобы тест не зависел от времени запуска
        pytest.skip("доставка перешла бы на завтра")
    db.add(d)
    w._check_assembly(now)
    assert w.assembly.isVisible() and w.assembly.ids == [d.id]
    assert any("Букет ещё не собран" in text for text, _ in w.queued)  # за 30 минут — хозяевам
    w.assembly.close()  # «напомнить через 10 минут»
    assert not w.assembly.isVisible()
    w._check_assembly(now + timedelta(minutes=5))
    assert not w.assembly.isVisible()  # отложено
    w._check_assembly(now + timedelta(minutes=11))
    assert w.assembly.isVisible()  # снова
    w.assembly.assembled.emit(d.id)  # нажали «Собрано»
    assert db.get_delivery(d.id).assembled and not w.assembly.isVisible()
    assert sum("Букет ещё не собран" in text for text, _ in w.queued) == 1  # хозяевам — один раз


def test_reminder_rows_and_signals(app):
    r = AssemblyReminder()
    got, snoozed = [], []
    r.assembled.connect(got.append)
    r.snoozed.connect(lambda: snoozed.append(1))
    items = [Delivery(id=5, day=NOW.date(), time="11:40", items="Розы"),
             Delivery(id=6, day=NOW.date(), time="11:50", items="Пионы")]
    r.show_items(items, NOW)
    assert r.ids == [5, 6] and r.rows.count() == 2
    r.show()
    r.close()
    assert snoozed == [1]
    r.deleteLater()


def test_daily_backup_to_telegram(window, monkeypatch, tmp_path):
    w = window
    sent = []
    monkeypatch.setattr(w.telegram, "send_file", lambda path, caption, tag, chats=None: sent.append((path, tag)))
    monkeypatch.setattr(w, "run_backup", lambda: backup.BackupResult(str(tmp_path / "copy.db")))
    (tmp_path / "copy.db").write_bytes(b"sqlite")
    w.db.path = str(tmp_path / "live.db")  # не ":memory:"
    day = date.today()
    w._maybe_send_backup(datetime.combine(day, time(8, 10)))  # ночную смену ещё сдают — рано
    assert sent == []
    w._maybe_send_backup(datetime.combine(day, time(9, 0)))
    assert len(sent) == 1 and sent[0][1] == "daily" and sent[0][0].endswith(".zip")
    w._backup_file_sent("daily", [])
    assert w.store["tg_backup_day"] == day.isoformat()
    w._maybe_send_backup(datetime.combine(day, time(15, 0)))
    assert len(sent) == 1  # сегодня уже отправили
    w.store["backup_to_telegram"] = "false"
    w.store["tg_backup_day"] = ""
    w._backup_attempt = None
    w._maybe_send_backup(datetime.combine(day, time(15, 0)))
    assert len(sent) == 1  # выключено в настройках
