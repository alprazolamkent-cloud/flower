import os

# Тесты окон не должны требовать экрана (сборка на GitHub и любой компьютер без монитора).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Одно приложение Qt на все тесты и до их конца: если его создаёт и теряет отдельный тест, процесс
# падает при выходе (на Windows это даёт код 1 после «passed»).
import sys  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

QT_APP = QApplication.instance() or QApplication(sys.argv)

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QSettings  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Свои настройки на каждый тест: реальные настройки магазина в тесты не попадают.

    Иначе, например, включённая в программе «Продажа только сканером» ломает тесты кассы.
    Отдельные тесты переопределяют эти методы через monkeypatch — это нормально, их patch снимет последним.
    """
    store: dict[str, object] = {}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: store.__setitem__(key, value))
    monkeypatch.setattr(QSettings, "remove", lambda self, key: store.pop(key, None))


@pytest.fixture(autouse=True)
def _close_windows():
    """После теста — закрыть и удалить все окна, пока жив Qt. Иначе их удаляет сборщик мусора уже при выходе
    из Python, и процесс падает после «passed» (на Windows — код 1, сборка не выходит)."""
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
