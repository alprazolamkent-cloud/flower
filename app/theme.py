"""Светлая и тёмная тема: палитра окон и свои цвета (подсветки строк, статусы, подсказки).

Свои цвета берутся из C в момент рисования, поэтому после apply() достаточно обновить окна.
"""

import os
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QObject, QPointF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QDialogButtonBox, QPushButton

THEMES = {"light": "Светлая", "dark": "Тёмная", "system": "Как в Windows"}

LIGHT = {
    # текстовые цвета подобраны так, чтобы читались и на белом, и на сером фоне окна
    "red": "#c62828", "green": "#2e7d5b", "orange": "#bf5000", "blue": "#1565c0", "gray": "#6b6b6b",
    "weekend": "#c62828",
    "delivered_bg": "#e3f4e4", "delivered_fg": "#1b1f1b",
    "pending_day": "#ffe0b2", "done_day": "#c8e6c9", "shift_name": "#546e7a",
    "note_bg": "#fff8e1", "note_border": "#ffe082", "info_bg": "#e3f2fd", "info_border": "#90caf9",
    "note_fg": "#212121",
    "card_bg": "#ffffff", "card_border": "#e1e5e0", "bar": "#a5d6a7",
    # фирменные: зелёный — главный (кнопки, вкладки, выбранный день), розовый — второй (выделение, «новое»)
    "accent": "#2e7d5b", "accent_hover": "#246649", "accent_soft": "#e6f2ec", "on_accent": "#ffffff",
    "pink": "#d6336c", "pink_soft": "#fce4ec",
    "window": "#f5f6f3", "base": "#ffffff", "text": "#1f2421", "muted": "#6b706c", "border": "#dfe3de",
    "header": "#f0f2ee", "scroll": "#c9cec8",
}
DARK = {
    "red": "#ff6b6b", "green": "#66bb6a", "orange": "#ffa726", "blue": "#64b5f6", "gray": "#9aa0a6",
    "weekend": "#ff8a80",
    "delivered_bg": "#1f3a24", "delivered_fg": "#e8f5e9",
    "pending_day": "#4a3218", "done_day": "#1e3a24", "shift_name": "#b0bec5",
    "note_bg": "#3a3320", "note_border": "#6d5a1e", "info_bg": "#1c2b3a", "info_border": "#2f5d8a",
    "note_fg": "#f1f1f1",
    "card_bg": "#262928", "card_border": "#3a3f3c", "bar": "#2f6a36",
    "accent": "#4caf85", "accent_hover": "#5cc196", "accent_soft": "#22362c", "on_accent": "#0f1a14",
    "pink": "#f06292", "pink_soft": "#4a2433",
    "window": "#1b1d1c", "base": "#222524", "text": "#e8eae9", "muted": "#9aa09c", "border": "#353a37",
    "header": "#282c2a", "scroll": "#4a504c",
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
        QPalette.Window: DARK["window"], QPalette.WindowText: "#e8eaed", QPalette.Base: DARK["base"],
        QPalette.AlternateBase: "#282b2a", QPalette.ToolTipBase: "#2b2d31", QPalette.ToolTipText: "#e8eaed",
        QPalette.PlaceholderText: "#8a8d91", QPalette.Text: "#e8eaed", QPalette.Button: "#2b2d31",
        QPalette.ButtonText: "#e8eaed", QPalette.BrightText: "#ff6b6b", QPalette.Light: "#3a3c41",
        QPalette.Midlight: "#33353a", QPalette.Mid: "#2a2c30", QPalette.Dark: "#151618",
        QPalette.Shadow: "#000000", QPalette.Highlight: DARK["pink_soft"], QPalette.HighlightedText: "#ffffff",
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


def _arrow_image(direction: str, color: str) -> str:
    """Стрелочка ▲/▼ для полей с числами и списков (рисуется сама, без файлов программы)."""
    name = f"flower-arrow-{direction}-{color.lstrip('#')}.png"
    path = os.path.join(QStandardPaths.writableLocation(QStandardPaths.TempLocation), name)
    if not os.path.exists(path):
        pix = QPixmap(20, 20)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(color), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        points = [QPointF(5, 13), QPointF(10, 8), QPointF(15, 13)] if direction == "up" else \
            [QPointF(5, 8), QPointF(10, 13), QPointF(15, 8)]
        p.drawPolyline(points)
        p.end()
        pix.save(path)
    return path.replace("\\", "/")


def light_palette(app) -> QPalette:
    p = app.style().standardPalette()
    for role, color in ((QPalette.Window, LIGHT["window"]), (QPalette.Base, LIGHT["base"]),
                        (QPalette.Button, LIGHT["base"]), (QPalette.Highlight, LIGHT["pink_soft"]),
                        (QPalette.HighlightedText, LIGHT["text"]), (QPalette.Text, LIGHT["text"]),
                        (QPalette.WindowText, LIGHT["text"]), (QPalette.ButtonText, LIGHT["text"]),
                        (QPalette.AlternateBase, "#f8faf7"), (QPalette.Link, LIGHT["accent"])):
        p.setColor(role, QColor(color))
    return p


def stylesheet(dark: bool) -> str:
    """Внешний вид всей программы: скруглённые кнопки и поля, вкладки с зелёной полосой, таблицы-карточки.
    Главные кнопки — setProperty("primary", True): залиты зелёным."""
    c = SimpleNamespace(**(DARK if dark else LIGHT))
    check = _check_image()
    up, down = _arrow_image("up", c.muted), _arrow_image("down", c.muted)
    return f"""
    QMainWindow, QDialog {{ background: {c.window}; }}
    QToolTip {{ background: {c.text}; color: {c.base}; border: none; padding: 6px 8px; border-radius: 6px; }}

    QPushButton, QToolButton {{
        background: {c.base}; color: {c.text}; border: 1px solid {c.border}; border-radius: 8px; padding: 6px 12px;
    }}
    QPushButton:hover, QToolButton:hover {{ border-color: {c.accent}; background: {c.accent_soft}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {c.accent_soft}; border-color: {c.accent_hover}; }}
    QPushButton:checked {{ background: {c.accent}; color: {c.on_accent}; border-color: {c.accent}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {c.muted}; background: {c.window}; border-color: {c.border}; }}
    QPushButton[primary="true"] {{
        background: {c.accent}; color: {c.on_accent}; border: 1px solid {c.accent}; font-weight: bold;
    }}
    QPushButton[primary="true"]:hover {{ background: {c.accent_hover}; border-color: {c.accent_hover}; }}
    QPushButton[primary="true"]:disabled {{ background: {c.accent_soft}; color: {c.muted}; border-color: {c.border}; }}
    QPushButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: right center; right: 6px; }}
    QPushButton[menu="true"] {{ padding-right: 22px; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit {{
        background: {c.base}; color: {c.text}; border: 1px solid {c.border}; border-radius: 6px; padding: 4px 6px;
        selection-background-color: {c.accent}; selection-color: {c.on_accent};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QComboBox:focus, QDateEdit:focus, QTimeEdit:focus {{ border-color: {c.accent}; }}
    QAbstractSpinBox {{ padding-right: 20px; }}
    QAbstractSpinBox[small="true"] {{ padding: 2px; }}
    QAbstractSpinBox[small="true"]::up-button, QAbstractSpinBox[small="true"]::down-button {{ width: 0; margin: 0; }}
    QAbstractSpinBox[small="true"]::up-arrow, QAbstractSpinBox[small="true"]::down-arrow {{ image: none; width: 0; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        subcontrol-origin: border; width: 18px; border: none; background: transparent; margin-right: 2px;
    }}
    QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
    QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {c.accent_soft}; }}
    QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
    QAbstractSpinBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
    QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::down-arrow:disabled {{ image: none; }}
    QComboBox {{ padding-right: 22px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 20px; border: none; }}
    QComboBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
    QDateEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 20px; border: none; }}
    QDateEdit::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
    QPushButton[small="true"] {{ padding: 2px; }}
    QComboBox QAbstractItemView {{
        background: {c.base}; border: 1px solid {c.border}; selection-background-color: {c.accent_soft};
        selection-color: {c.text}; outline: none;
    }}

    QTabWidget::pane {{ border: 1px solid {c.border}; border-radius: 10px; background: {c.base}; top: -1px; }}
    QTabBar::tab {{
        background: transparent; color: {c.muted}; padding: 8px 16px; border: none;
        border-bottom: 3px solid transparent; margin-right: 2px;
    }}
    QTabBar::tab:hover {{ color: {c.text}; }}
    QTabBar::tab:selected {{ color: {c.accent}; border-bottom: 3px solid {c.accent}; font-weight: bold; }}

    QTableView, QTableWidget, QListView, QListWidget, QTreeView {{
        background: {c.base}; color: {c.text}; border: 1px solid {c.border}; border-radius: 8px;
        gridline-color: {c.border}; selection-background-color: {c.pink_soft}; selection-color: {c.text};
        alternate-background-color: {c.header};
    }}
    QHeaderView::section {{
        background: {c.header}; color: {c.muted}; border: none; border-bottom: 1px solid {c.border};
        border-right: 1px solid {c.border}; padding: 6px 6px; font-weight: bold;
    }}
    QTableCornerButton::section {{ background: {c.header}; border: none; }}

    QGroupBox {{
        background: {c.base}; border: 1px solid {c.border}; border-radius: 10px; margin-top: 16px;
        padding: 12px 8px 8px 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 4px;
        color: {c.accent}; font-weight: bold;
    }}

    QMenu {{ background: {c.base}; color: {c.text}; border: 1px solid {c.border}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c.accent_soft}; color: {c.text}; }}
    QMenu::separator {{ height: 1px; background: {c.border}; margin: 4px 8px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c.scroll}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:horizontal {{ background: {c.scroll}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::handle:hover {{ background: {c.accent}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QCheckBox::indicator, QTableView::indicator {{
        width: 16px; height: 16px; border: 1px solid {c.scroll}; border-radius: 4px; background: {c.base};
    }}
    QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {c.accent}; }}
    QCheckBox::indicator:checked, QTableView::indicator:checked {{
        background: {c.accent}; border-color: {c.accent}; image: url("{check}");
    }}
    QCheckBox::indicator:disabled {{ border-color: {c.border}; background: {c.window}; }}
    QCheckBox::indicator:checked:disabled {{ background: {c.accent_soft}; border-color: {c.accent_soft}; }}

    QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {c.accent}; border-top-left-radius: 8px;
        border-top-right-radius: 8px; }}
    QCalendarWidget QToolButton {{ color: {c.on_accent}; background: transparent; border: none; font-weight: bold;
        padding: 4px 8px; }}
    QCalendarWidget QToolButton:hover {{ background: {c.accent_hover}; }}
    QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
    QCalendarWidget QSpinBox {{ background: {c.base}; color: {c.text}; }}
    QCalendarWidget QAbstractItemView {{
        background: {c.base}; selection-background-color: {c.accent}; selection-color: {c.on_accent};
        border: 1px solid {c.border}; outline: none;
    }}
    """


def primary(button: QPushButton, on: bool = True) -> QPushButton:
    """Главная кнопка экрана — залита зелёным."""
    button.setProperty("primary", on)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


class _PrimaryOk(QObject):
    """«ОК» / «Сохранить» во всех окнах с QDialogButtonBox — главные, без правки каждого окна."""

    def eventFilter(self, obj, event):  # noqa: N802 — имя из Qt
        if event.type() == QEvent.Polish and isinstance(obj, QPushButton):
            box = obj.parent()
            if isinstance(box, QDialogButtonBox) and box.buttonRole(obj) in (QDialogButtonBox.AcceptRole,
                                                                             QDialogButtonBox.YesRole):
                primary(obj)
        return False


_filter = _PrimaryOk()


def apply(app, mode: str) -> bool:
    """Включить тему ("light", "dark", "system"). Возвращает True, если тёмная."""
    dark = mode == "dark" or (mode == "system" and _system_is_dark(app))
    C.__dict__.update(DARK if dark else LIGHT)
    scheme = getattr(Qt, "ColorScheme", None)
    if scheme is not None and hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(scheme.Dark if dark else scheme.Light)
    app.setPalette(dark_palette() if dark else light_palette(app))
    app.setStyleSheet(stylesheet(dark))
    app.removeEventFilter(_filter)
    app.installEventFilter(_filter)
    return dark
