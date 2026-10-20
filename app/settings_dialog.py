"""Окно настроек — только для администратора."""

import sys

from datetime import date, time

from PySide6.QtCore import QDate, QSettings, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout,
)

from . import autostart
from .shifts import DEFAULT_CHANGE, DEFAULT_GRACE_MINUTES, WaterSchedule
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


def water_schedule() -> WaterSchedule:
    """График смены воды. Пока админ его не задал — считаем от сегодняшней смены."""
    settings = _settings()
    try:
        anchor = date.fromisoformat(str(settings.value("water_anchor", "")))
    except ValueError:
        anchor = date.today()
        settings.setValue("water_anchor", anchor.isoformat())
    return WaterSchedule(anchor, int(settings.value("water_every", 2)))


def _gray_label(text: str) -> QLabel:
    """Подсказка с переносами строк, расставленными вручную (автоперенос в форме обрезается)."""
    label = QLabel(text)
    label.setStyleSheet("color: gray")
    return label


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.main = window
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(560)
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
        form.addRow(_gray_label("Напоминания приходят, пока программа запущена.\n"
                                "Крестик сворачивает её к часам, выход — через значок у часов."))
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
        shift_form.addRow(_gray_label("За 30 минут до пересменки — напоминание сдать смену.\n"
                                      "В пересменку сотрудника выводит из программы.\n"
                                      "Сданная позже смена — «с опозданием», не сданная — «не сдана»."))
        layout.addWidget(shifts)

        water = QGroupBox("Смена воды")
        water_form = QFormLayout(water)
        schedule = water_schedule()
        self.water_every = QSpinBox()
        self.water_every.setRange(1, 7)
        self.water_every.setPrefix("раз в ")
        self.water_every.setSuffix(" дн.")
        self.water_every.setValue(schedule.every)
        water_form.addRow("Менять воду:", self.water_every)
        self.water_anchor = QDateEdit()
        self.water_anchor.setCalendarPopup(True)
        self.water_anchor.setDisplayFormat("dd.MM.yyyy")
        self.water_anchor.setDate(QDate(schedule.anchor.year, schedule.anchor.month, schedule.anchor.day))
        water_form.addRow("Один из дней замены:", self.water_anchor)
        water_form.addRow(_gray_label("От этого дня считаются следующие дни замены.\n"
                                      "Если график сбился — укажите день, когда воду меняли последний раз."))
        layout.addWidget(water)

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
        settings.setValue("water_every", self.water_every.value())
        settings.setValue("water_anchor", self.water_anchor.date().toString("yyyy-MM-dd"))
        if sys.platform == "win32":
            autostart.set_enabled(self.autostart_check.isChecked())
        super().accept()
        self.main.check_reminders()
