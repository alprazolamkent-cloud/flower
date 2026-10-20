"""Журнал уведомлений: что всплывало в углу экрана, можно посмотреть ещё раз."""

import sys
import time
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from app import main_window as mw
from app.db import Database


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def test_notifications_window_and_pruning(db):
    now = datetime(2026, 10, 20, 15, 0)
    db.add_notification("Скоро доставка!", "14:00 — розы", now - timedelta(days=9))
    db.add_notification("Смена воды", "поменяйте воду", now - timedelta(hours=30))
    db.add_notification("Скоро пересменка", "до 08:00", now - timedelta(hours=2))
    db.add_notification("📢 Объявление", "Розы по 150", now)  # старше недели уже удалено
    last_day = db.notifications_since(now - timedelta(hours=24))
    assert [t for _, t, _ in last_day] == ["📢 Объявление", "Скоро пересменка"]  # новые сверху, сутки
    assert [t for _, t, _ in db.notifications_since(now - timedelta(days=3))][-1] == "Смена воды"
    assert db.notifications_since(now - timedelta(days=30))[-1][1] == "Смена воды"  # 9-дневное удалено


def test_tray_logs_every_message(app, db, monkeypatch):
    shown = []
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a: shown.append(a))
    store = {}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: store.__setitem__(key, value))
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    w = mw.MainWindow(db)
    w.set_user(anna)
    w.tray.showMessage("Скоро доставка!", "14:00 — розы, ул. Ленина", QSystemTrayIcon.Warning, 60_000)
    w.tray.showMessage("Смена воды", "Сегодня день смены воды", QSystemTrayIcon.Information, 60_000)
    assert len(shown) == 2  # уведомление по-прежнему всплывает, как раньше
    assert [t for _, t, _ in db.notifications_since(datetime.now() - timedelta(hours=1))] == \
        ["Смена воды", "Скоро доставка!"]
    assert w.notif_btn.text() == "🔔 Уведомления (2)"

    mw.NotificationsDialog.exec = lambda self: 0  # окно «открыли»
    w.open_notifications()
    assert w.notif_btn.text() == "🔔 Уведомления"  # прочитано
    time.sleep(1.1)  # время хранится с точностью до секунды
    w.tray.showMessage("Скоро пересменка", "до 08:00")
    assert w.notif_btn.text() == "🔔 Уведомления (1)"
    w.set_user(oleg)  # у другого сотрудника своё «прочитано»: ему всё ещё 3 новых
    assert w.notif_btn.text() == "🔔 Уведомления (3)"


def test_dialog_is_read_only_and_lists_entries(app, db):
    from app.notifications_dialog import NotificationsDialog
    from PySide6.QtWidgets import QAbstractItemView
    db.add_notification("Смена воды", "поменяйте воду", datetime.now() - timedelta(hours=1))
    dialog = NotificationsDialog(None, db)
    assert dialog.table.rowCount() == 1 and dialog.table.item(0, 1).text() == "Смена воды"
    assert dialog.table.editTriggers() == QAbstractItemView.NoEditTriggers
    empty = NotificationsDialog(None, Database(":memory:"))
    assert empty.table.rowCount() == 0
