"""Окно настроек — только для администратора."""

import sys

from datetime import time

from PySide6.QtCore import QSettings, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout,
)

from . import autostart
from .shifts import DEFAULT_CHANGE, DEFAULT_GRACE_MINUTES
from .templates_dialog import TemplatesDialog

DEFAULT_LEAD_MINUTES = 20


def _settings() -> QSettings:
    return QSettings("FlowerDeliveries", "FlowerDeliveries")


def lead_minutes() -> int:
    """За сколько минут до доставки напоминать."""
    return int(_settings().value("lead_minutes", DEFAULT_LEAD_MINUTES))


def shift_change() -> time:
    """Время пересменки."""
    try:
        h, m = str(_settings().value("shift_change", DEFAULT_CHANGE.strftime("%H:%M"))).split(":")
        return time(int(h), int(m))
    except ValueError:
        return DEFAULT_CHANGE


def grace_minutes() -> int:
    """Сколько минут после пересменки ещё можно сдать смену."""
    return int(_settings().value("grace_minutes", DEFAULT_GRACE_MINUTES))


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.main = window
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        reminders = QGroupBox("Напоминания о доставках")
        form = QFormLayout(reminders)
        self.lead_box = QSpinBox()
        self.lead_box.setRange(1, 180)
        self.lead_box.setSuffix(" мин")
        self.lead_box.setValue(lead_minutes())
        form.addRow("Напоминать за:", self.lead_box)
        self.autostart_check = QCheckBox("Запускать вместе с Windows")
        self.autostart_check.setChecked(autostart.is_enabled())
        self.autostart_check.setVisible(sys.platform == "win32")
        form.addRow(self.autostart_check)
        hint = QLabel("Напоминания приходят, пока программа запущена. Крестик сворачивает её "
                      "к часам, выход — через значок у часов.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        form.addRow(hint)
        layout.addWidget(reminders)

        shifts = QGroupBox("Смены")
        shift_form = QFormLayout(shifts)
        self.change_edit = QTimeEdit()
        self.change_edit.setDisplayFormat("HH:mm")
        change = shift_change()
        self.change_edit.setTime(QTime(change.hour, change.minute))
        shift_form.addRow("Пересменка в:", self.change_edit)
        self.grace_box = QSpinBox()
        self.grace_box.setRange(0, 180)
        self.grace_box.setSuffix(" мин")
        self.grace_box.setValue(grace_minutes())
        shift_form.addRow("Сдать после пересменки можно ещё:", self.grace_box)
        shift_hint = QLabel("За 30 минут до пересменки программа напомнит сдать смену. В момент "
                            "пересменки сотрудника выводит из программы; сданная после этого смена "
                            "помечается «с опозданием», а не сданная вовсе — «не сдана».")
        shift_hint.setWordWrap(True)
        shift_hint.setStyleSheet("color: gray")
        shift_form.addRow(shift_hint)
        layout.addWidget(shifts)

        shop = QGroupBox("Магазин")
        row = QHBoxLayout(shop)
        users = QPushButton("👥 Сотрудники…")
        users.setToolTip("Добавить, удалить, сменить пароль, назначить администратором")
        users.clicked.connect(self._open_users)
        texts = QPushButton("💬 Тексты сообщений…")
        texts.setToolTip("Что получает заказчик: «курьер выехал», «доставлено», «отзыв»")
        texts.clicked.connect(lambda: TemplatesDialog(self).exec())
        row.addWidget(users)
        row.addWidget(texts)
        layout.addWidget(shop)

        layout.addWidget(window.updater.build_box())

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_users(self):
        # Сначала закрываем настройки: после удаления себя из списка программа просит войти заново.
        self.reject()
        QTimer.singleShot(0, self.main.manage_users)

    def accept(self):
        settings = _settings()
        settings.setValue("lead_minutes", self.lead_box.value())
        settings.setValue("shift_change", self.change_edit.time().toString("HH:mm"))
        settings.setValue("grace_minutes", self.grace_box.value())
        if sys.platform == "win32":
            autostart.set_enabled(self.autostart_check.isChecked())
        super().accept()
        self.main.check_reminders()
