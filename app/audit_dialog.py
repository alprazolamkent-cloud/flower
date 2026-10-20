"""Журнал действий: кто и что менял — только для администратора, только просмотр."""

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .db import Database

PERIODS = (("За сутки", 1), ("За неделю", 7), ("За месяц", 30), ("За всё время (до 180 дней)", 180))


class AuditDialog(QDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Журнал действий")
        self.resize(980, 520)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.period = QComboBox()
        for title, days in PERIODS:
            self.period.addItem(title, days)
        self.period.setCurrentIndex(1)
        self.person = QComboBox()
        self.person.addItem("Все", None)
        for name in db.audit_people():
            self.person.addItem(name, name)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по тексту…")
        for widget, label in ((self.period, "Период:"), (self.person, "Кто:")):
            top.addWidget(QLabel(label))
            top.addWidget(widget)
        top.addWidget(self.search, 1)
        layout.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Когда", "Кто", "Действие", "Подробности"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.count = QLabel()
        self.count.setStyleSheet("color: gray")
        layout.addWidget(self.count)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self.period.currentIndexChanged.connect(self.refresh)
        self.person.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        since = datetime.now() - timedelta(days=self.period.currentData())
        entries = self.db.audit_entries(since, self.person.currentData(), limit=2000)
        needle = self.search.text().strip().casefold()
        if needle:
            entries = [e for e in entries if needle in f"{e[1]} {e[2]} {e[3]}".casefold()]
        self.table.setRowCount(len(entries))
        for row, (when, who, action, details) in enumerate(entries):
            for col, value in enumerate((f"{when:%d.%m.%Y %H:%M}", who or "—", action, details)):
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                self.table.setItem(row, col, cell)
        self.table.resizeRowsToContents()
        self.count.setText(f"Записей: {len(entries)}. Журнал хранится 180 дней; править его нельзя.")
