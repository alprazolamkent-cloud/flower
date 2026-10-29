"""Заказы вперёд, праздники: напоминание накануне, строка в плане смены."""

import sys
from datetime import date, datetime, time, timedelta

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from app import main_window as mw
from app.db import Database, Delivery
from app.future_orders import as_html, as_text, day_title, future_deliveries
from app.holidays import holiday_name
from app.shift_plan import plan_html

TODAY = date.today()


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


def fill(db):
    a = Delivery(day=TODAY + timedelta(days=1), time="10:00", items="Розы", address="Ленина, 5", recipient="Маша")
    b = Delivery(day=TODAY + timedelta(days=1), time="с 12 до 14", items="Пионы <b>", address="Мира, 1",
                 assembled=True)
    c = Delivery(day=TODAY + timedelta(days=3), time="9:00", items="Тюльпаны", address="Садовая, 3")
    gone = Delivery(day=TODAY + timedelta(days=2), time="9:00", items="Уже отвезли", delivered=True)
    for d in (b, a, c, gone):
        db.add(d)
    return a, b, c, gone


def test_list_text_and_html(db):
    a, b, c, gone = fill(db)
    items = future_deliveries(db, TODAY, 7)
    assert [d.id for d in items] == [a.id, b.id, c.id]  # по дню и времени, доставленные скрыты
    assert len(future_deliveries(db, TODAY, 7, hide_delivered=False)) == 4
    assert [d.id for d in future_deliveries(db, TODAY, 7, query="мира")] == [b.id]
    text = as_text(items, TODAY)
    assert "(завтра) — 2" in text and "10:00 · Розы · Ленина, 5 · Маша · не собрано" in text
    page = as_html(items, TODAY)
    assert "Пионы &lt;b&gt;" in page and "<th colspan='6'>" in page  # чужой текст экранируется
    holidays = f"{TODAY + timedelta(days=1):%d.%m} Праздник цветов"
    assert "🎉 Праздник цветов" in day_title(TODAY + timedelta(days=1), TODAY, holidays)
    assert holiday_name(holidays, TODAY + timedelta(days=1)) == "Праздник цветов"
    assert holiday_name(holidays, TODAY) is None


@pytest.fixture
def window(app, db, monkeypatch):
    store = {"tg_token": "x", "tg_chat_id": "111", "holiday_eve_time": "18:00",
             "holidays_text": f"{TODAY + timedelta(days=1):%d.%m} Праздник цветов"}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: store.__setitem__(key, value))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    w = mw.MainWindow(db)
    w.tray.showMessage = lambda *a: None
    w.queued = []
    w.telegram.queue = lambda text, key=None, chat=None, except_chat=None: w.queued.append(text)
    w.set_user(db.add_user("anna", "Анна", "1"))
    yield w
    w.assembly.hide()
    if w.future_orders is not None:
        w.future_orders.hide()


def test_window_and_assemble(window):
    w, db = window, window.db
    a, b, c, gone = fill(db)
    w.open_future()
    dlg = w.future_orders
    assert dlg.day_box.count() == 3  # «Все дни» + 2 дня с заказами
    assert "Заказов: <b>3</b>" in dlg.summary.text()
    assert dlg.rows[0] is None and dlg.rows[1].id == a.id  # заголовок дня, потом заказы
    assert "🎉 Праздник цветов" in dlg.table.item(0, 0).text()
    dlg.table.selectRow(1)
    dlg._toggle_assembled()
    assert db.get_delivery(a.id).assembled and "собрано: <b>2</b>" in dlg.summary.text()
    w.open_future(TODAY + timedelta(days=3))
    assert dlg.day_box.currentData() == (TODAY + timedelta(days=3)).isoformat() and [x.id for x in dlg.rows if x] == [c.id]


def test_holiday_eve_once(window):
    w, db = window, window.db
    fill(db)
    evening = datetime.combine(TODAY, time(18, 30))
    w._check_holiday_eve(datetime.combine(TODAY, time(17, 0)))
    assert w.queued == []  # рано
    w._check_holiday_eve(evening)
    assert len(w.queued) == 1 and "Завтра Праздник цветов" in w.queued[0] and "собрать 1" in w.queued[0]
    w._check_holiday_eve(evening + timedelta(minutes=30))
    assert len(w.queued) == 1  # один раз


def test_plan_mentions_holiday(db):
    fill(db)
    shift = db.open_shift(db.add_user("anna", "Анна", "1").id, 5000, datetime.combine(TODAY, time(8, 5)), time(8, 0))
    holidays = f"{TODAY + timedelta(days=1):%d.%m} Праздник цветов"
    text = plan_html(db, shift, time(8, 0), None, 60, datetime.combine(TODAY, time(9, 0)), holidays=holidays)
    assert "Праздник цветов" in text and "с вечера накануне" in text
    assert "Праздник цветов" not in plan_html(db, shift, time(8, 0), None, 60, datetime.combine(TODAY, time(9, 0)))
