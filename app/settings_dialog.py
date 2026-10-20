"""Окно настроек — только для администратора."""

import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from . import autostart
from .templates_dialog import TemplatesDialog

DEFAULT_LEAD_MINUTES = 20


def lead_minutes() -> int:
    """За сколько минут до доставки напоминать."""
    settings = QSettings("FlowerDeliveries", "FlowerDeliveries")
    return int(settings.value("lead_minutes", DEFAULT_LEAD_MINUTES))


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
        QSettings("FlowerDeliveries", "FlowerDeliveries").setValue("lead_minutes", self.lead_box.value())
        if sys.platform == "win32":
            autostart.set_enabled(self.autostart_check.isChecked())
        super().accept()
        self.main.check_reminders()
