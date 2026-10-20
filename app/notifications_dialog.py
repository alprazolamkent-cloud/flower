"""Журнал уведомлений за последние сутки — только просмотр."""

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from .db import Database
from .theme import C

WINDOW = timedelta(hours=24)


class NotificationsDialog(QDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent)
        self.setWindowTitle("Уведомления")
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        hint = QLabel("Всё, что программа показывала в углу экрана за последние 24 часа. Только просмотр.")
        hint.setStyleSheet(f"color: {C.gray}")
        layout.addWidget(hint)

        items = db.notifications_since(datetime.now() - WINDOW)
        self.table = QTableWidget(len(items), 3)
        self.table.setHorizontalHeaderLabels(["Когда", "Уведомление", "Подробности"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for row, (when, title, text) in enumerate(items):
            for col, value in enumerate((f"{when:%d.%m %H:%M}", title, text)):
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                self.table.setItem(row, col, cell)
        self.table.resizeRowsToContents()
        layout.addWidget(self.table, 1)
        if not items:
            layout.addWidget(QLabel("За последние сутки уведомлений не было."))

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
