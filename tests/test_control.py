"""Контроль: опоздавшие доставки, журнал действий, удаление доставок только с согласия админа."""

import sys
from datetime import date, datetime, time, timedelta

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from app import main_window as mw
from app.bot_commands import BotContext, BotHandler, parse_command
from app.db import Database, Delivery
from app.reminders import overdue_deliveries, parse_deadline_time
from app.salary import SalaryRules
from app.shifts import WaterSchedule

CH = time(8, 0)
NOW = datetime(2026, 10, 20, 15, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


# ----- доставка не отмечена вовремя -----

def test_parse_deadline_time():
    assert parse_deadline_time("14:00") == time(14, 0)
    assert parse_deadline_time("14:00–16:00") == time(16, 0)  # ждём до конца окна, а не до начала
    assert parse_deadline_time("с 10.30 до 12.15") == time(12, 15)
    assert parse_deadline_time("к 9") == time(9, 0)
    assert parse_deadline_time("вечером") is None


def test_overdue_deliveries():
    day = NOW.date()
    mk = lambda i, t, **kw: Delivery(id=i, day=day, time=t, **kw)
    late = mk(1, "14:00")  # 14:00 + 20 мин = 14:20 → уже 40 минут просрочки
    early = mk(2, "14:50")  # ещё идёт запас 20 минут
    window_ok = mk(3, "13:00–15:00")  # окно до 15:00: 15:20 ещё не наступило
    done = mk(4, "13:00", delivered=True)
    no_time = mk(5, "")
    pending_delete = mk(6, "13:00", delete_requested_at=NOW)
    ancient = Delivery(id=7, day=day - timedelta(days=2), time="14:00")
    items = [late, early, window_ok, done, no_time, pending_delete, ancient]
    assert overdue_deliveries(items, NOW, 20, set()) == [late]
    assert overdue_deliveries(items, NOW, 0, set()) == []  # 0 — не сообщать
    assert overdue_deliveries(items, NOW, 20, {("late", 1, day, "14:00")}) == []  # про эту уже говорили
    later = overdue_deliveries(items, NOW + timedelta(minutes=30), 20, set())  # через полчаса — уже все трое
    assert [d.id for d in later] == [1, 2, 3]


# ----- журнал действий -----

def test_audit_log(db):
    anna = db.add_user("anna", "Анна", "1")
    db.log_action(anna, "Доставка создана", "12:00 розы", NOW)
    db.log_action(None, "Неудачный вход", "логин: x", NOW + timedelta(minutes=1), who="x")
    db.log_action(anna, "Доставка изменена", "адрес: «а» → «б»", NOW + timedelta(minutes=2))
    entries = db.audit_entries(NOW - timedelta(hours=1))
    assert [(who, action) for _, who, action, _ in entries] == \
        [("Анна", "Доставка изменена"), ("x", "Неудачный вход"), ("Анна", "Доставка создана")]
    assert [a for _, _, a, _ in db.audit_entries(NOW - timedelta(hours=1), "Анна")] == \
        ["Доставка изменена", "Доставка создана"]
    assert db.audit_people() == ["x", "Анна"]
    # журнал хранит имя на момент действия: удалили сотрудника — записи остались читаемыми
    db.delete_user(anna.id)
    assert any(who == "Анна" for _, who, _, _ in db.audit_entries(NOW - timedelta(hours=1)))
    db.log_action(None, "Старое", "", NOW + timedelta(days=200), who="x")  # запись «через 200 дней» чистит старые
    assert all(a != "Доставка создана" for _, _, a, _ in db.audit_entries(NOW - timedelta(days=1)))


def test_delivery_changes_text():
    old = Delivery(day=date(2026, 10, 20), time="12:00", address="а", price=500, delivered=False)
    new = Delivery(day=date(2026, 10, 21), time="12:00", address="б", price=700, delivered=True)
    text = mw.delivery_changes(old, new)
    assert "дата: 20.10.2026 → 21.10.2026" in text and "адрес: «а» → «б»" in text
    assert "взяли: 500 → 700" in text and "доставлено: нет → да" in text and "время" not in text
    assert mw.delivery_changes(old, old) == ""


# ----- запрос на удаление и решение админа в Telegram -----

def make_ctx(db, **kw):
    return BotContext(db=db, now=NOW, change=CH, grace=30, water=WaterSchedule(date(2026, 10, 18), 2),
                      rules=lambda uid: SalaryRules(), version="1", **kw)


def test_parse_delete_links():
    assert parse_command("/del_17") == ("del", "17") and parse_command("/keep_5@MyBot") == ("keep", "5")
    assert parse_command("/удалить 17") == ("del", "17") and parse_command("/оставить 17") == ("keep", "17")
    assert parse_command("del_17") is None  # без «/» это просто текст


def test_admin_approves_delete_in_telegram(db):
    anna = db.add_user("anna", "Анна", "1")
    d = Delivery(day=NOW.date(), time="12:00", items="Букет роз", address="ул. Ленина, 10", price=500)
    db.add(d)
    db.request_delete(d.id, anna.id, NOW)
    pending = db.get_delivery(d.id)
    assert pending.delete_requested_at == NOW and pending.delete_requested_by_name == "Анна"

    audit, decided = [], []
    ctx = make_ctx(db, audit=lambda a, t: audit.append((a, t)), decided=lambda dd, ok, who: decided.append((dd.id, ok, who)))
    answer = BotHandler().handle(f"/del_{d.id}", ctx, "111", "Мария")
    assert answer.startswith("🗑 Удалено: №1") and db.get_delivery(d.id) is None
    assert decided == [(1, True, "Мария")]
    assert audit[0][0] == "Удаление доставки подтверждено" and "просил(а) Анна" in audit[0][1] and "решил(а) Мария" in audit[0][1]
    # второй администратор нажал позже — уже решено, ничего не ломается
    assert "уже нет" in BotHandler().handle(f"/del_{d.id}", make_ctx(db), "222", "Игорь")


def test_admin_keeps_delivery_in_telegram(db):
    anna = db.add_user("anna", "Анна", "1")
    d = Delivery(day=NOW.date(), items="Розы")
    db.add(d)
    db.request_delete(d.id, anna.id, NOW)
    assert BotHandler().handle(f"/keep_{d.id}", make_ctx(db), "111", "Мария").startswith("↩ Оставлено")
    kept = db.get_delivery(d.id)
    assert kept is not None and kept.delete_requested_at is None  # доставка на месте, запрос снят
    assert "нет запроса" in BotHandler().handle(f"/del_{d.id}", make_ctx(db), "111", "Мария")  # без запроса не удалить
    assert db.get_delivery(d.id) is not None
    assert "Укажите номер" in BotHandler().handle("/удалить", make_ctx(db), "111", "Мария")


def test_delete_cannot_be_done_without_request(db):
    d = Delivery(day=NOW.date(), items="Розы")
    db.add(d)
    assert "нет запроса" in BotHandler().handle(f"/del_{d.id}", make_ctx(db), "111", "Мария")
    assert db.get_delivery(d.id) is not None


# ----- в программе -----

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(app, db, monkeypatch):
    store = {}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: store.__setitem__(key, value))
    answers = {"yes": True}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes if answers["yes"] else QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    admin, anna = db.add_user("admin", "Мария", "1", is_admin=True), db.add_user("anna", "Анна", "1")
    w = mw.MainWindow(db)
    w.tray.showMessage = lambda *a: None
    w.queued = []
    w.telegram.queue = lambda text, key=None, chat=None, except_chat=None: w.queued.append((text, chat, except_chat))
    w.admin, w.anna, w.answers = admin, anna, answers
    return w


def select(w, d):
    w.calendar.setSelectedDate(__import__("PySide6.QtCore", fromlist=["QDate"]).QDate(d.day.year, d.day.month, d.day.day))
    w.refresh_day()
    w.table.selectRow(0)


def test_employee_delete_becomes_request(window):
    w, db = window, window.db
    d = Delivery(day=date.today(), time="12:00", items="Букет роз", address="ул. Ленина, 10", price=500)
    db.add(d)
    w.set_user(w.anna)
    select(w, d)
    w.delete_delivery()
    stored = db.get_delivery(d.id)
    assert stored is not None and stored.delete_requested_at is not None  # не удалена
    [(text, chat, _)] = w.queued
    assert f"/del_{d.id}" in text and f"/keep_{d.id}" in text and "Анна просит удалить" in text and chat is None
    assert [a for _, _, a, _ in db.audit_entries(datetime.now() - timedelta(hours=1))] == ["Запрос на удаление доставки"]
    # в таблице видно, что ждёт решения; сотруднику кнопка недоступна
    assert w.table.item(0, mw.COL_ITEMS).text().startswith("🗑 ждёт удаления")
    assert not w.delete_btn.isEnabled() and not w.keep_btn.isVisibleTo(w)
    w.delete_delivery()  # повторно просить не нужно
    assert len(w.queued) == 1


def test_employee_can_decline_request(window):
    w, db = window, window.db
    d = Delivery(day=date.today(), items="Розы")
    db.add(d)
    w.set_user(w.anna)
    select(w, d)
    w.answers["yes"] = False
    w.delete_delivery()
    assert db.get_delivery(d.id).delete_requested_at is None and w.queued == []


def test_admin_deletes_directly_and_can_keep(window):
    w, db = window, window.db
    d1, d2 = Delivery(day=date.today(), time="10:00", items="Розы"), Delivery(day=date.today(), time="11:00", items="Тюльпаны")
    db.add(d1)
    db.add(d2)
    db.request_delete(d2.id, w.anna.id, datetime.now())
    w.set_user(w.admin)
    select(w, d1)
    w.delete_delivery()  # админ удаляет сразу, без Telegram
    assert db.get_delivery(d1.id) is None and w.queued == []
    w.table.selectRow(0)  # остался d2 с запросом
    assert w.keep_btn.isVisibleTo(w)
    w.keep_delivery()
    assert db.get_delivery(d2.id).delete_requested_at is None
    actions = [a for _, _, a, _ in db.audit_entries(datetime.now() - timedelta(hours=1))]
    assert "Доставка удалена" in actions and "Удаление доставки отклонено" in actions


def test_bot_decision_refreshes_and_tells_other_owner(window):
    w, db = window, window.db
    d = Delivery(day=date.today(), items="Розы", address="а")
    db.add(d)
    db.request_delete(d.id, w.anna.id, datetime.now())
    w._current_bot_chat = "111"
    w._delete_decided(db.get_delivery(d.id), True, "Мария")
    [(text, chat, except_chat)] = w.queued
    assert "Мария: удаление подтверждено" in text and except_chat == "111"  # тому, кто решил, повторно не пишем


def test_overdue_alert_goes_to_staff_and_owners(window):
    w, db = window, window.db
    now = datetime.now()
    late_time = (now - timedelta(minutes=45)).strftime("%H:%M")
    if (now - timedelta(minutes=45)).date() != now.date():
        pytest.skip("тест в первые минуты суток")
    d = Delivery(day=now.date(), time=late_time, items="Букет роз", address="ул. Ленина, 10", phone="+7 900 1")
    db.add(d)
    shown = []
    w.tray.showMessage = lambda *a: shown.append(a[0])
    w._check_overdue(now)
    w._check_overdue(now)  # второй раз о той же доставке не напоминаем
    assert shown == ["Доставка не отмечена"] and len(w.queued) == 1
    assert "не отмечена вовремя" in w.queued[0][0] and "Телефон: +7 900 1" in w.queued[0][0]
