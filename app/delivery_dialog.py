"""Окно добавления / редактирования доставки."""

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QSpinBox,
)

from .db import Delivery
from .messaging import CHANNELS

MAX_MONEY = 10_000_000


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def money_box() -> QSpinBox:
    box = QSpinBox()
    box.setRange(0, MAX_MONEY)
    box.setSingleStep(100)
    box.setSuffix(" ₽")
    box.setGroupSeparatorShown(True)
    return box


class DeliveryDialog(QDialog):
    def __init__(self, parent, delivery: Delivery):
        super().__init__(parent)
        self.delivery = delivery
        self.setWindowTitle("Новая доставка" if delivery.id is None else "Изменить доставку")
        self.setMinimumWidth(460)

        form = QFormLayout(self)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        d = delivery.day
        self.date_edit.setDate(QDate(d.year, d.month, d.day))
        form.addRow("Дата:", self.date_edit)

        self.time_edit = QLineEdit(delivery.time)
        self.time_edit.setPlaceholderText("например 14:00 или 14:00–16:00 (по нему придёт напоминание)")
        form.addRow("Время:", self.time_edit)

        self.items_edit = QPlainTextEdit(delivery.items)
        self.items_edit.setPlaceholderText("Букеты, наполнение, открытка, шары и т.п.")
        self.items_edit.setFixedHeight(90)
        form.addRow("Что доставить:", self.items_edit)

        self.recipient_edit = QLineEdit(delivery.recipient)
        self.recipient_edit.setPlaceholderText("необязательно")
        form.addRow("Кому (имя):", self.recipient_edit)

        self.phone_edit = QLineEdit(delivery.phone)
        self.phone_edit.setPlaceholderText("+7 ...")
        form.addRow("Телефон:", self.phone_edit)

        self.address_edit = QLineEdit(delivery.address)
        form.addRow("Адрес:", self.address_edit)

        self.source_box = QComboBox()
        self.source_box.addItem("— не указано —", "")
        for channel in CHANNELS:
            self.source_box.addItem(channel, channel)
        index = self.source_box.findData(delivery.source)
        self.source_box.setCurrentIndex(max(index, 0))
        self.source_box.currentIndexChanged.connect(self._update_contact_hint)
        form.addRow("Откуда заказ:", self.source_box)

        self.contact_edit = QLineEdit(delivery.customer_contact)
        form.addRow("Контакт заказчика:", self.contact_edit)
        self._update_contact_hint()

        self.price_box = money_box()
        self.price_box.setValue(delivery.price)
        form.addRow("Взяли с покупателя:", self.price_box)

        self.courier_box = money_box()
        self.courier_box.setValue(delivery.courier_pay)
        form.addRow("Отдали курьеру:", self.courier_box)

        self.remainder_label = QLabel()
        form.addRow("Осталось:", self.remainder_label)
        self.price_box.valueChanged.connect(self._update_remainder)
        self.courier_box.valueChanged.connect(self._update_remainder)
        self._update_remainder()

        self.delivered_check = QCheckBox("Доставлено")
        self.delivered_check.setChecked(delivery.delivered)
        form.addRow("", self.delivered_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _update_contact_hint(self):
        hints = {
            "WhatsApp": "номер телефона, например +7 900 123-45-67",
            "Telegram": "логин @имя (тогда текст подставится сам) или ссылка на чат",
            "ВКонтакте": "ссылка на страницу, короткое имя или id",
            "Макс": "ссылка на чат (скопируйте из адресной строки)",
        }
        self.contact_edit.setPlaceholderText(
            hints.get(self.source_box.currentData(), "телефон, логин или ссылка заказчика"))

    def _update_remainder(self):
        rest = self.price_box.value() - self.courier_box.value()
        color = "#c62828" if rest < 0 else "#2e7d32"
        self.remainder_label.setText(f"<b style='color:{color}'>{money(rest)}</b>")

    def accept(self):
        if not self.items_edit.toPlainText().strip() and not self.address_edit.text().strip():
            QMessageBox.warning(self, "Заполните поля", "Укажите, что доставить, или адрес.")
            return
        qd = self.date_edit.date()
        d = self.delivery
        d.day = date(qd.year(), qd.month(), qd.day())
        d.time = self.time_edit.text().strip()
        d.items = self.items_edit.toPlainText().strip()
        d.recipient = self.recipient_edit.text().strip()
        d.phone = self.phone_edit.text().strip()
        d.address = self.address_edit.text().strip()
        d.source = self.source_box.currentData() or ""
        d.customer_contact = self.contact_edit.text().strip()
        d.price = self.price_box.value()
        d.courier_pay = self.courier_box.value()
        d.delivered = self.delivered_check.isChecked()
        super().accept()
