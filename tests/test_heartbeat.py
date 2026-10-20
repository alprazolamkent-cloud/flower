"""«Компьютер на связи»: закреплённое сообщение, «снова на связи» и сторож на GitHub."""

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import heartbeat, telegram

spec = importlib.util.spec_from_file_location(
    "watchdog", os.path.join(os.path.dirname(__file__), "..", "tools", "watchdog.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

NOW = datetime(2026, 10, 20, 22, 0)


def test_back_online_text():
    assert heartbeat.back_online_text(NOW - timedelta(minutes=4), NOW) is None
    text = heartbeat.back_online_text(NOW - timedelta(minutes=35), NOW)
    assert "снова на связи" in text and "35 мин" in text and "с 20.10 21:25 до 22:00" in text
    assert "1 ч 30 мин" in heartbeat.back_online_text(NOW - timedelta(minutes=90), NOW)


def test_watchdog_alert_text():
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)  # 22:00 по Москве
    fresh = {"text": "🟢 на связи", "date": 0, "edit_date": int((now - timedelta(minutes=3)).timestamp())}
    old = {"text": "🟢 на связи", "date": int((now - timedelta(minutes=25)).timestamp())}
    paused = {"text": "⏸ Слежение выключено", "date": int((now - timedelta(hours=5)).timestamp())}
    assert watchdog.alert_text(fresh, now, tz) is None
    text = watchdog.alert_text(old, now, tz)
    assert "не на связи уже 25 мин" in text and "20.10 21:35" in text and "через 10 минут" in text
    assert watchdog.alert_text(paused, now, tz) is None  # выключили нарочно
    assert watchdog.alert_text(None, now, tz) is None  # ещё не настроено


def test_watchdog_without_secrets(monkeypatch, capsys):
    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHATS", raising=False)
    assert watchdog.main() == 0 and "не заданы" in capsys.readouterr().out


@pytest.fixture
def store(monkeypatch):
    data = {"tg_token": "x", "tg_chat_id": "111, 222"}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: data.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: data.__setitem__(key, value))
    return data


def test_heartbeat_pins_then_edits(store, monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    calls = []
    monkeypatch.setattr(telegram, "send_message", lambda t, c, text: calls.append(("send", c)) or 77)
    monkeypatch.setattr(telegram, "pin_message", lambda t, c, m: calls.append(("pin", c, m)))
    monkeypatch.setattr(telegram, "edit_message", lambda t, c, m, text: calls.append(("edit", c, m, text[:1])))
    hb = heartbeat.Heartbeat()
    hb._send("x", "111", 0, heartbeat.status_text(NOW))  # первый раз — прислать и закрепить
    app.processEvents()
    assert calls == [("send", "111"), ("pin", "111", 77)]
    assert (store["tg_status_chat"], store["tg_status_msg"]) == ("111", 77)
    calls.clear()
    hb._send("x", "111", 77, heartbeat.status_text(NOW))  # дальше — только править
    app.processEvents()
    assert calls == [("edit", "111", 77, "🟢")]

    def gone(*a):
        raise telegram.TelegramError("message to edit not found", permanent=True)
    monkeypatch.setattr(telegram, "edit_message", gone)
    calls.clear()
    hb._send("x", "111", 77, heartbeat.status_text(NOW))  # открепили / удалили — новое
    app.processEvents()
    assert calls == [("send", "111"), ("pin", "111", 77)]
    hb.timer.stop()
    hb.deleteLater()


def test_back_online_after_gap(store):
    app = QApplication.instance() or QApplication(sys.argv)
    hb = heartbeat.Heartbeat()
    got = []
    hb.back_online.connect(got.append)
    store["tg_last_beat"] = (datetime.now() - timedelta(minutes=40)).isoformat(timespec="seconds")
    hb._done("111", 77, "")
    assert got and "снова на связи" in got[0]
    hb._done("111", 77, "")  # сразу ещё раз — перерыва не было
    assert len(got) == 1
    hb._done("111", 77, "нет связи")  # ошибка — время не обновляем
    assert len(got) == 1
    hb.timer.stop()
    hb.deleteLater()
    app.processEvents()
