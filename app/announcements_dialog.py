"""Объявления для сотрудников: всплывающее окно «прочитал» и история."""

import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .db import Announcement, Database, User
from .theme import C


def short(text: str, length: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length - 1] + "…"


class UnreadDialog(QDialog):
    """Новые объявления от администратора. Закрыть можно только кнопкой «Прочитал(а)»."""

    def __init__(self, parent, items: list[Announcement], name: str):
        super().__init__(parent)
        self.setWindowTitle("📢 Объявление" if len(items) == 1 else f"📢 Объявления: {len(items)}")
        self.setMinimumSize(480, 260)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)

        content = QWidget()
        inner = QVBoxLayout(content)
        for item in items:
            label = QLabel(f"<span style='color:gray'>{item.created_at:%d.%m %H:%M}</span><br>"
                           f"<span style='font-size:16px'>{html.escape(item.text).replace(chr(10), '<br>')}</span>")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setStyleSheet(f"background: {C.note_bg}; border: 1px solid {C.note_border}; "
                                f"color: {C.note_fg}; padding: 10px")
            inner.addWidget(label)
        inner.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        button = QPushButton(f"✅ Прочитал(а) — {name}")
        button.setMinimumHeight(36)
        button.clicked.connect(self.accept)
        layout.addWidget(button)

    def reject(self):
        pass  # Esc не закрывает — только «Прочитал(а)»


class HistoryDialog(QDialog):
    """Все объявления и кто их прочитал. Админ может написать объявление с компьютера или удалить."""

    def __init__(self, parent, db: Database, user: User):
        super().__init__(parent)
        self.db, self.user = db, user
        self.items: list[Announcement] = []
        self.setWindowTitle("Объявления")
        self.resize(820, 460)
        layout = QVBoxLayout(self)
        hint = QLabel("Объявления пишет администратор: сообщением Telegram-боту, начиная с «!», "
                      "например «! С понедельника розы по 150 ₽».")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C.gray}")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Когда", "Объявление", "Прочитали"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        if user.is_admin:
            write = QPushButton("✏️ Написать объявление…")
            write.clicked.connect(self._write)
            delete = QPushButton("🗑 Удалить")
            delete.clicked.connect(self._delete)
            row.addWidget(write)
            row.addWidget(delete)
        row.addStretch(1)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self):
        self.items = self.db.announcements()
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            readers = ", ".join(f"{name or '—'} {when:%d.%m %H:%M}" for name, when in item.readers) or "пока никто"
            for col, text in enumerate((f"{item.created_at:%d.%m.%Y %H:%M}", item.text, readers)):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                self.table.setItem(row, col, cell)
        self.table.resizeRowsToContents()

    def _write(self):
        text, ok = QInputDialog.getMultiLineText(self, "Новое объявление", "Текст для сотрудников:")
        if ok and self.db.add_announcement(text, datetime.now()) is not None:
            self.refresh()

    def _delete(self):
        row = self.table.currentRow()
        if not (0 <= row < len(self.items) and self.table.selectionModel().hasSelection()):
            return
        item = self.items[row]
        if QMessageBox.question(self, "Удалить", f"Удалить объявление «{short(item.text)}»?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.db.delete_announcement(item.id)
            self.refresh()
