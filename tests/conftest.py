import os

# Тесты окон не должны требовать экрана (сборка на GitHub и любой компьютер без монитора).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Одно приложение Qt на все тесты и до их конца: если его создаёт и теряет отдельный тест, процесс
# падает при выходе (на Windows это даёт код 1 после «passed»).
import sys  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

QT_APP = QApplication.instance() or QApplication(sys.argv)
