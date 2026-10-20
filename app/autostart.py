"""Запуск программы вместе с Windows (запись в реестре текущего пользователя)."""

import sys

from PySide6.QtCore import QSettings

RUN_KEY = r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "FlowerDeliveries"


def _run() -> QSettings:
    return QSettings(RUN_KEY, QSettings.NativeFormat)


def is_enabled() -> bool:
    return sys.platform == "win32" and _run().contains(NAME)


def set_enabled(enabled: bool):
    if sys.platform != "win32":
        return
    run = _run()
    if enabled:
        command = f'"{sys.executable}"' if getattr(sys, "frozen", False) \
            else f'"{sys.executable}" "{sys.argv[0]}"'
        run.setValue(NAME, command + " --minimized")
    else:
        run.remove(NAME)
