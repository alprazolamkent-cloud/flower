"""Окно «Не собрано!»: всплывает поверх всех окон, пока букеты к ближайшим доставкам не соберут."""

import html
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .db import Delivery
from .icon import flower_icon
from .reminders import parse_start_time
from .theme import C

SNOOZE = timedelta(minutes=10)


def left_text(d: Delivery, now: datetime) -> str:
    """«через 45 мин», «через 1 ч 10 мин» или «время уже прошло»."""
    start = parse_start_time(d.time)
    minutes = int((datetime.combine(d.day, start) - now).total_seconds() // 60) if start else 0
    if minutes < 0:
        return "время уже прошло!"
    if minutes < 60:
        return f"через {minutes} мин"
    return f"через {minutes // 60} ч {minutes % 60:02d} мин"


class AssemblyReminder(QDialog):
    """Не модальное: можно собирать и отмечать, не закрывая. Закрыть — значит «напомнить через 10 минут»."""

    assembled = Signal(int)  # id доставки
    delivered = Signal(int)  # уже отвезли (время прошло), просто не отметили
    snoozed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Не собрано!")
        self.setWindowIcon(flower_icon())
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        layout.addWidget(self.head)
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        snooze = QPushButton("⏰ Напомнить через 10 минут")
        snooze.clicked.connect(self.close)
        layout.addWidget(snooze)
        self.ids: list[int] = []

    def show_items(self, deliveries: list[Delivery], now: datetime):
        self.head.setText(f"<h3 style='color:{C.red}'>💐 Скоро доставка, а букет не собран!</h3>"
                          "Соберите и нажмите «Собрано». Окно будет появляться, пока букет не соберут.")
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.ids = [d.id for d in deliveries]
        for d in deliveries:
            self.rows.addWidget(self._row(d, now))
        self.adjustSize()

    def _row(self, d: Delivery, now: datetime) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background: {C.note_bg}; border: 1px solid {C.note_border}; border-radius: 6px; }}"
                            f"QLabel {{ border: none; background: transparent; color: {C.note_fg}; }}")
        row = QHBoxLayout(frame)
        what = d.items.splitlines()[0] if d.items else "доставка"
        late = left_text(d, now)
        text = QLabel(f"<b style='font-size:14px'>{html.escape(d.time or '—')}</b> &nbsp;"
                      f"<span style='color:{C.red if 'прошло' in late else C.orange}'><b>{late}</b></span><br>"
                      f"{html.escape(what)}<br><span style='color:{C.gray}'>{html.escape(d.address or 'адрес не указан')}"
                      f"</span>")
        text.setWordWrap(True)
        row.addWidget(text, 1)
        done = QPushButton("💐 Собрано")
        done.setMinimumHeight(36)
        done.clicked.connect(lambda _=False, i=d.id: self.assembled.emit(i))
        row.addWidget(done)
        if "прошло" in late:  # могли уже отвезти, а отметить забыли
            gone = QPushButton("✓ Уже доставлено")
            gone.setMinimumHeight(36)
            gone.clicked.connect(lambda _=False, i=d.id: self.delivered.emit(i))
            row.addWidget(gone)
        return frame

    def closeEvent(self, event):
        self.snoozed.emit()  # крестик — то же, что «напомнить через 10 минут»
        super().closeEvent(event)
