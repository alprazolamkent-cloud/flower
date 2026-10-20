import os

# Тесты окон не должны требовать экрана (сборка на GitHub и любой компьютер без монитора).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
