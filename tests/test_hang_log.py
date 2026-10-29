"""«Чёрный ящик»: окно не отвечает — в файл пишется, где застряла программа; при запуске — отчёт один раз."""

import faulthandler
import sys
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import hang_log


def stuck_in_slow_code(seconds):
    time.sleep(seconds)  # «зависли» — события не обрабатываются


def test_hang_is_recorded_and_reported_once(tmp_path, monkeypatch):
    QApplication.instance()
    monkeypatch.setattr(hang_log, "HANG_AFTER", 1)
    log = hang_log.HangLog(str(tmp_path), "1.48")
    stuck_in_slow_code(2.5)
    log.timer.stop()
    faulthandler.enable(sys.stderr)  # дальше тесты — как обычно
    text = (tmp_path / hang_log.FILE_NAME).read_text(encoding="utf-8")
    assert "версия 1.48" in text and "не отвечает" in text and "stuck_in_slow_code" in text

    store = {}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: store.__setitem__(key, value))
    report = hang_log.unsent_report(log.path, QSettings())
    assert "stuck_in_slow_code" in report and len(report) <= 3500
    assert hang_log.unsent_report(log.path, QSettings()) == ""  # второй раз — уже отправлено
