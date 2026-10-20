"""Редактирование текстов сообщений заказчику."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QPlainTextEdit, QPushButton,
)

from .messaging import DEFAULT_TEMPLATES, KINDS, PLACEHOLDERS


def load_templates() -> dict[str, str]:
    settings = QSettings("FlowerDeliveries", "FlowerDeliveries")
    return {k: str(settings.value(f"msg_{k}", default)) for k, default in DEFAULT_TEMPLATES.items()}


class TemplatesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.settings = QSettings("FlowerDeliveries", "FlowerDeliveries")
        self.setWindowTitle("Тексты сообщений")
        self.setMinimumWidth(560)

        form = QFormLayout(self)
        hint = QLabel("В тексте можно использовать подстановки: " + ", ".join(
            f"<b>{key}</b> — {what}" for key, what in PLACEHOLDERS.items()))
        hint.setWordWrap(True)
        form.addRow(hint)

        self.edits: dict[str, QPlainTextEdit] = {}
        current = load_templates()
        for kind, title in KINDS.items():
            edit = QPlainTextEdit(current[kind])
            edit.setFixedHeight(80)
            self.edits[kind] = edit
            form.addRow(title + ":", edit)

        reset = QPushButton("Вернуть стандартные тексты")
        reset.clicked.connect(self._reset)
        form.addRow(reset)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _reset(self):
        for kind, edit in self.edits.items():
            edit.setPlainText(DEFAULT_TEMPLATES[kind])

    def accept(self):
        for kind, edit in self.edits.items():
            text = edit.toPlainText().strip() or DEFAULT_TEMPLATES[kind]
            self.settings.setValue(f"msg_{kind}", text)
        super().accept()
