"""Бонусы клиентов: балансы, история операций, ручные корректировки и памятные даты.

Правила программы (проценты, лимиты, сгорание) настраиваются отдельно:
Настройки → Лояльность. Здесь — сами данные клиентов, только для администратора.
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .theme import C, fit_to_screen


def _phone_text(phone: str) -> str:
    """79991234567 → +7 999 123-45-67, остальное — как есть."""
    if len(phone) == 11 and phone.startswith("7"):
        return f"+7 {phone[1:4]} {phone[4:7]}-{phone[7:9]}-{phone[9:11]}"
    return phone


def _num(value: int) -> str:
    return f"{value:,}".replace(",", " ")


class DatesDialog(QDialog):
    """Памятные даты клиента: посмотреть, добавить, изменить (та же подпись — новая дата)."""

    def __init__(self, parent, loyalty, phone: str):
        super().__init__(parent)
        self.loyalty, self.phone = loyalty, phone
        self.setWindowTitle(f"Памятные даты — {_phone_text(phone)}")
        fit_to_screen(self, 420, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("За 3 дня до даты бот напомнит клиенту и пообещает двойные бонусы\n"
                                "за букет к этому дню (±1 день)."))
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.label = QLineEdit()
        self.label.setPlaceholderText("Подпись: жена, мама, годовщина…")
        self.day = QLineEdit()
        self.day.setPlaceholderText("ДД.ММ")
        self.day.setMaximumWidth(90)
        add = QPushButton("Сохранить")
        add.clicked.connect(self._add)
        row.addWidget(self.label, 1)
        row.addWidget(self.day)
        row.addWidget(add)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for d in self.loyalty.dates(self.phone):
            self.list.addItem(f"{d['day']} — {d['label']}")

    def _add(self):
        try:
            self.loyalty.set_date(self.phone, self.label.text(), self.day.text())
        except ValueError as e:
            QMessageBox.warning(self, "Памятные даты", str(e))
            return
        self.label.clear()
        self.day.clear()
        self.refresh()


class LoyaltyDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.db = main.db
        self.setWindowTitle("Бонусы клиентов")
        fit_to_screen(self, 1020, 640)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Телефон:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по цифрам телефона…")
        top.addWidget(self.search, 1)
        self.adjust_btn = QPushButton("✏️ Корректировка")
        self.adjust_btn.setToolTip("Начислить или списать баллы вручную (с причиной — она останется в истории)")
        self.adjust_btn.clicked.connect(self._adjust)
        self.dates_btn = QPushButton("📅 Памятные даты")
        self.dates_btn.clicked.connect(self._dates)
        self.consent_btn = QPushButton("✅ Согласие")
        self.consent_btn.setToolTip("Клиент согласился на обработку данных устно (на кассе) — отметить. "
                                    "В боте согласие записывается само.")
        self.consent_btn.clicked.connect(self._consent)
        top.addWidget(self.adjust_btn)
        top.addWidget(self.dates_btn)
        top.addWidget(self.consent_btn)
        layout.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Телефон", "Баланс", "Уровень", "Покупки за 12 мес.", "Последняя активность", "Сгорят", "Согласие"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col in range(7):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 3)

        layout.addWidget(QLabel("История операций выбранного клиента:"))
        self.history = QTableWidget(0, 5)
        self.history.setHorizontalHeaderLabels(["Когда", "Операция", "Баллы", "Источник", "Кто / примечание"])
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history.verticalHeader().setVisible(False)
        h_header = self.history.horizontalHeader()
        for col in range(4):
            h_header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        h_header.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.history, 2)

        self.count = QLabel()
        self.count.setStyleSheet(f"color: {C.gray}")
        layout.addWidget(self.count)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self.search.textChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._refresh_history)
        self.refresh()

    def _selected_phone(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else ""

    def refresh(self):
        loyalty = self.db.loyalty()
        now = datetime.now()
        accounts = loyalty.accounts(self.search.text())
        self.table.setRowCount(len(accounts))
        for row, acc in enumerate(accounts):
            tier = loyalty.tier_info(acc["phone"], now)
            consent = acc["consent_at"].replace("T", " ")[:10] if acc["consent_at"] else "—"
            values = (_phone_text(acc["phone"]), _num(acc["balance"]), f"{tier['rate']}%",
                      _num(tier["spend"]), acc["last_activity"] or "—",
                      acc["expires_at"] or "—", consent)
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 0:
                    cell.setData(Qt.UserRole, acc["phone"])
                self.table.setItem(row, col, cell)
        self.count.setText(f"Клиентов: {len(accounts)}. Правила программы — Настройки → Лояльность.")
        self._refresh_history()

    def _refresh_history(self):
        phone = self._selected_phone()
        has = bool(phone)
        self.adjust_btn.setEnabled(has)
        self.dates_btn.setEnabled(has)
        self.consent_btn.setEnabled(has)
        entries = self.db.loyalty().history(phone, limit=200) if has else []
        self.history.setRowCount(len(entries))
        for row, e in enumerate(entries):
            who = " / ".join(x for x in (e["author"], e["note"]) if x)
            when = e["at"].replace("T", " ")[:16]
            for col, value in enumerate((when, e["label"], f"{e['amount']:+}", e["source"], who)):
                self.history.setItem(row, col, QTableWidgetItem(value))

    def _adjust(self):
        phone = self._selected_phone()
        if not phone:
            return
        delta, ok = QInputDialog.getInt(self, "Корректировка", "Баллы (минус — списать):",
                                        0, -100000, 100000)
        if not ok or delta == 0:
            return
        reason, ok = QInputDialog.getText(self, "Корректировка", "Причина (останется в истории):")
        if not ok or not reason.strip():
            if ok:
                QMessageBox.warning(self, "Корректировка", "Без причины корректировать нельзя.")
            return
        author = self.main.user.name if self.main.user else ""
        try:
            applied = self.db.loyalty().adjust(phone, delta, note=reason.strip(), author=author)
        except ValueError as e:
            QMessageBox.warning(self, "Корректировка", str(e))
            return
        self.main.audit("Корректировка бонусов", f"{_phone_text(phone)}: {applied:+} — {reason.strip()}")
        self.refresh()

    def _dates(self):
        phone = self._selected_phone()
        if phone:
            DatesDialog(self, self.db.loyalty(), phone).exec()

    def _consent(self):
        phone = self._selected_phone()
        if not phone:
            return
        self.db.loyalty().set_consent(phone, "касса")
        self.main.audit("Согласие на обработку данных", f"{_phone_text(phone)} — отмечено вручную")
        self.refresh()
