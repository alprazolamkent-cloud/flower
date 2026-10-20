"""Обязанности смены: редактор списка (админ) и чек-лист для сотрудника на смене."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from .db import Database
from .shifts import Shift


class DutiesEditor(QDialog):
    """Список дел на каждую смену. Двойной щелчок — изменить текст."""

    def __init__(self, parent, db: Database):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Обязанности смены")
        self.resize(480, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Что сотрудник должен сделать за каждую смену. "
                                "Он отмечает пункты галочками, а при сдаче смены видно, что не сделано."))
        layout.itemAt(0).widget().setWordWrap(True)

        self.list = QListWidget()
        for duty in db.duties():
            self._add_item(duty.text, duty.id)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        for text, slot in (("➕ Добавить", self._add), ("🗑 Удалить", self._remove),
                           ("▲", lambda: self._move(-1)), ("▼", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addLayout(row)
        hint = QLabel("Например: протереть витрину, проверить холодильник, вынести мусор. "
                      "Смена воды отмечается отдельно.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_item(self, text: str, duty_id: int | None) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, duty_id)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.list.addItem(item)
        return item

    def _add(self):
        item = self._add_item("Новая обязанность", None)
        self.list.setCurrentItem(item)
        self.list.editItem(item)

    def _remove(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)

    def _move(self, step: int):
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def accept(self):
        items = [(self.list.item(i).data(Qt.UserRole), self.list.item(i).text()) for i in range(self.list.count())]
        self.db.save_duties(items)
        super().accept()


class ChecklistDialog(QDialog):
    """Отметки по обязанностям во время смены — сохраняются сразу."""

    def __init__(self, parent, db: Database, shift: Shift):
        super().__init__(parent)
        self.db, self.shift = db, shift
        self.setWindowTitle("Обязанности смены")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        items = db.checklist(shift)
        if not items:
            layout.addWidget(QLabel("Администратор пока не задал обязанности."))
        for item in items:
            box = QCheckBox(self._title(item.text, item.done_at))
            box.setChecked(item.done)
            box.toggled.connect(lambda on, i=item, b=box: self._toggle(i, b, on))
            layout.addWidget(box)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Готово")
        close.rejected.connect(self.accept)
        layout.addWidget(close)

    @staticmethod
    def _title(text: str, done_at: datetime | None) -> str:
        return f"{text}  (в {done_at:%H:%M})" if done_at else text

    def _toggle(self, item, box: QCheckBox, on: bool):
        now = datetime.now()
        self.db.set_duty_done(self.shift.id, item.duty_id, item.text, on, now)
        box.setText(self._title(item.text, now if on else None))
