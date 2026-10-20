"""Настройка ступеней премии: общие и свои для отдельных сотрудников."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .db import Database
from .holidays import parse_holidays
from .salary import MAX_TIERS, PenaltyKind, Tier


class TiersEditor(QWidget):
    """До 5 ступеней: «от N ₽ в среднем за смену → P %». Пустые строки (0 %) не считаются."""

    def __init__(self, tiers: list[Tier] | None = None):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(QLabel("<b>Средний приход за смену</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Премия</b>"), 0, 2)
        self.rows: list[tuple[QSpinBox, QDoubleSpinBox]] = []
        for i in range(MAX_TIERS):
            amount = QSpinBox()
            amount.setRange(0, 10_000_000)
            amount.setSingleStep(1000)
            amount.setGroupSeparatorShown(True)
            amount.setPrefix("от ")
            amount.setSuffix(" ₽")
            percent = QDoubleSpinBox()
            percent.setRange(0, 100)
            percent.setDecimals(1)
            percent.setSuffix(" %")
            percent.setSpecialValueText("—")
            grid.addWidget(QLabel(f"Ступень {i + 1}:"), i + 1, 0)
            grid.addWidget(amount, i + 1, 1)
            grid.addWidget(percent, i + 1, 2)
            self.rows.append((amount, percent))
        self.set_tiers(tiers or [])

    def set_tiers(self, tiers: list[Tier]):
        for i, (amount, percent) in enumerate(self.rows):
            tier = tiers[i] if i < len(tiers) else Tier(0, 0)
            amount.setValue(tier.per_shift)
            percent.setValue(tier.percent)

    def tiers(self) -> list[Tier]:
        return [Tier(a.value(), p.value()) for a, p in self.rows if p.value() > 0]


class EmployeeTiersDialog(QDialog):
    """Свои ступени для отдельных сотрудников (например, новенькому план поменьше)."""

    def __init__(self, parent, db: Database, common: list[Tier], overrides: dict[int, list[Tier]]):
        super().__init__(parent)
        self.common = common
        self.overrides = {k: list(v) for k, v in overrides.items()}
        self.current: int | None = None
        self.setWindowTitle("Свои ступени для сотрудника")
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.person = QComboBox()
        for u in db.users():
            self.person.addItem(u.name or u.login, u.id)
        form.addRow("Сотрудник:", self.person)
        layout.addLayout(form)
        self.own = QCheckBox("Свои ступени (иначе — общие для всех)")
        layout.addWidget(self.own)
        self.editor = TiersEditor()
        layout.addWidget(self.editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.person.currentIndexChanged.connect(self._switch)
        self.own.toggled.connect(self.editor.setEnabled)
        self._switch()

    def _store_current(self):
        if self.current is None:
            return
        if self.own.isChecked():
            self.overrides[self.current] = self.editor.tiers()
        else:
            self.overrides.pop(self.current, None)

    def _switch(self):
        self._store_current()
        self.current = self.person.currentData()
        own = self.current in self.overrides
        self.own.setChecked(own)
        self.editor.set_tiers(self.overrides[self.current] if own else self.common)
        self.editor.setEnabled(own)

    def accept(self):
        self._store_current()
        super().accept()


class PenaltyCatalogEditor(QWidget):
    """Список штрафных санкций: название и сумма. У каждой есть постоянный номер — по нему работают
    ссылки в Telegram, поэтому переименование и порядок их не ломают."""

    def __init__(self, kinds: list[PenaltyKind]):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Санкция", "Сумма, ₽"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("➕ Добавить")
        add.clicked.connect(lambda: self._add_row(None, "Новая санкция", 500))
        remove = QPushButton("🗑 Убрать")
        remove.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        for kind in kinds:
            self._add_row(kind.id, kind.title, kind.amount)

    def _add_row(self, kind_id: int | None, title: str, amount: int):
        row = self.table.rowCount()
        self.table.insertRow(row)
        name = QTableWidgetItem(title)
        name.setData(Qt.UserRole, kind_id)
        self.table.setItem(row, 0, name)
        spin = QSpinBox()
        spin.setRange(0, 1_000_000)
        spin.setSingleStep(100)
        spin.setGroupSeparatorShown(True)
        spin.setValue(amount)
        self.table.setCellWidget(row, 1, spin)

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def kinds(self) -> list[PenaltyKind]:
        """Новые санкции получают следующий свободный номер; убранные номера не используются повторно."""
        existing = [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())]
        next_id = max([i for i in existing if i] + [self.max_used_id]) + 1
        result = []
        for r in range(self.table.rowCount()):
            title = self.table.item(r, 0).text().strip()
            if not title:
                continue
            kind_id = self.table.item(r, 0).data(Qt.UserRole)
            if kind_id is None:
                kind_id, next_id = next_id, next_id + 1
            result.append(PenaltyKind(kind_id, title, self.table.cellWidget(r, 1).value()))
        return result

    max_used_id = 0  # наибольший номер, который уже использовался (даже убранный)


class HolidaysDialog(QDialog):
    """Список праздничных дней: по строке на дату. В такие смены премия за выручку не начисляется."""

    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle("Праздничные дни")
        self.resize(460, 420)
        layout = QVBoxLayout(self)
        hint = QLabel("По одной дате в строке. <b>08.03</b> — каждый год, <b>23.11.2026</b> — только в этот год "
                      "(для праздников, которые каждый год в другой день, например День матери). "
                      "После даты можно написать пояснение.<br><br>"
                      "Дата — это день, <b>с которого начинается смена</b> (смена с 8:00 до 8:00 следующего дня). "
                      "В праздничную смену выручка не идёт в премию, а ставка за смену — праздничная.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.edit = QPlainTextEdit(text)
        layout.addWidget(self.edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Готово")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        _, bad = parse_holidays(self.edit.toPlainText())
        if bad:
            QMessageBox.warning(self, "Праздничные дни", "Не получилось понять строки:\n• " + "\n• ".join(bad)
                                + "\n\nНужен вид «08.03» или «23.11.2026».")
            return
        super().accept()

    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.edit.toPlainText().splitlines() if line.strip())
