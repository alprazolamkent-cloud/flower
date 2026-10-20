"""Настройка ступеней премии: общие и свои для отдельных сотрудников."""

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout,
    QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from .db import Database
from .salary import MAX_TIERS, Tier


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
