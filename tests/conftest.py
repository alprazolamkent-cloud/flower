import os

# Тесты окон не должны требовать экрана (сборка на GitHub и любой компьютер без монитора).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Одно приложение Qt на все тесты и до их конца: если его создаёт и теряет отдельный тест, процесс
# падает при выходе (на Windows это даёт код 1 после «passed»).
import sys  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

QT_APP = QApplication.instance() or QApplication(sys.argv)

import pytest  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402


@pytest.fixture(autouse=True)
def _close_windows():
    """После теста — закрыть и удалить все окна, пока жив Qt. Иначе их удаляет сборщик мусора уже при выходе
    из Python, и процесс падает после «passed» (на Windows — код 1, сборка не выходит)."""
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
