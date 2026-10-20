"""Светлая и тёмная тема: палитра окон и свои цвета (подсветки строк, статусы, подсказки).

Свои цвета берутся из C в момент рисования, поэтому после apply() достаточно обновить окна.
"""

import os
from types import SimpleNamespace

from PySide6.QtCore import QPointF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap

THEMES = {"light": "Светлая", "dark": "Тёмная", "system": "Как в Windows"}

LIGHT = {
    "red": "#c62828", "green": "#2e7d32", "orange": "#ef6c00", "blue": "#1e88e5", "gray": "#757575",
    "delivered_bg": "#e3f4e4", "delivered_fg": "#1b1f1b",
    "pending_day": "#ffe0b2", "done_day": "#c8e6c9", "shift_name": "#546e7a",
    "note_bg": "#fff8e1", "note_border": "#ffe082", "info_bg": "#e3f2fd", "info_border": "#90caf9",
    "note_fg": "#212121",
}
DARK = {
    "red": "#ef5350", "green": "#66bb6a", "orange": "#ffa726", "blue": "#64b5f6", "gray": "#9aa0a6",
    "delivered_bg": "#1f3a24", "delivered_fg": "#e8f5e9",
    "pending_day": "#5d4022", "done_day": "#24452b", "shift_name": "#b0bec5",
    "note_bg": "#3a3320", "note_border": "#6d5a1e", "info_bg": "#1c2b3a", "info_border": "#2f5d8a",
    "note_fg": "#f1f1f1",
}

C = SimpleNamespace(**LIGHT)


def _system_is_dark(app) -> bool:
    scheme = getattr(Qt, "ColorScheme", None)
    hints = app.styleHints()
    if scheme is None or not hasattr(hints, "colorScheme"):
        return False
    if hasattr(hints, "unsetColorScheme"):
        hints.unsetColorScheme()  # узнать, что выбрано в самой Windows
    elif hasattr(hints, "setColorScheme"):
        hints.setColorScheme(scheme.Unknown)
    return hints.colorScheme() == scheme.Dark


def dark_palette() -> QPalette:
    p = QPalette()
    colors = {
        QPalette.Window: "#202124", QPalette.WindowText: "#e8eaed", QPalette.Base: "#18191c",
        QPalette.AlternateBase: "#26272b", QPalette.ToolTipBase: "#2b2d31", QPalette.ToolTipText: "#e8eaed",
        QPalette.PlaceholderText: "#8a8d91", QPalette.Text: "#e8eaed", QPalette.Button: "#2b2d31",
        QPalette.ButtonText: "#e8eaed", QPalette.BrightText: "#ff6b6b", QPalette.Light: "#3a3c41",
        QPalette.Midlight: "#33353a", QPalette.Mid: "#2a2c30", QPalette.Dark: "#151618",
        QPalette.Shadow: "#000000", QPalette.Highlight: "#3d7bd9", QPalette.HighlightedText: "#ffffff",
        QPalette.Link: "#64b5f6",
    }
    for role, color in colors.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#6b6e73"))
    return p


def _check_image() -> str:
    """Белая галочка для отмеченных квадратиков тёмной темы (рисуется сама, без файлов программы)."""
    path = os.path.join(QStandardPaths.writableLocation(QStandardPaths.TempLocation), "flower-check.png")
    if not os.path.exists(path):
        pix = QPixmap(28, 28)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("white"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(6, 15), QPointF(11.5, 20.5), QPointF(22, 8)])
        p.end()
        pix.save(path)
    return path.replace("\\", "/")


def dark_stylesheet() -> str:
    """Квадратики-галочки: в тёмной палитре стандартные почти не видны."""
    return f"""
    QCheckBox::indicator, QTableView::indicator {{
        width: 14px; height: 14px; border: 1px solid #8a8d91; border-radius: 3px; background: #2b2d31;
    }}
    QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: #c4c7cc; }}
    QCheckBox::indicator:checked, QTableView::indicator:checked {{
        background: #3d7bd9; border-color: #3d7bd9; image: url("{_check_image()}");
    }}
    QCheckBox::indicator:disabled {{ border-color: #55585d; background: #24262a; }}
    QCheckBox::indicator:checked:disabled {{ background: #34507a; border-color: #34507a; }}
    """


def apply(app, mode: str) -> bool:
    """Включить тему ("light", "dark", "system"). Возвращает True, если тёмная."""
    dark = mode == "dark" or (mode == "system" and _system_is_dark(app))
    C.__dict__.update(DARK if dark else LIGHT)
    scheme = getattr(Qt, "ColorScheme", None)
    if scheme is not None and hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(scheme.Dark if dark else scheme.Light)
    app.setPalette(dark_palette() if dark else app.style().standardPalette())
    app.setStyleSheet(dark_stylesheet() if dark else "")
    return dark
