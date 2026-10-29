"""Окно «Заказы вперёд»: все будущие доставки списком по дням — для праздников и предзаказов."""

import html
import os
import tempfile
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .db import Delivery
from .holidays import holiday_name
from .icon import flower_icon
from .reminders import parse_start_time
from .settings_dialog import holidays_text
from .theme import C

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
PERIODS = ((7, "7 дней"), (14, "2 недели"), (31, "месяц"), (92, "3 месяца"))
HEADERS = ["Время", "Что", "Кому", "Адрес", "Откуда", "Состояние"]


def day_title(day: date, today: date, holidays: str = "") -> str:
    when = "сегодня" if day == today else "завтра" if day == today + timedelta(days=1) else ""
    name = holiday_name(holidays, day) if holidays else None
    return (f"{day.day} {MONTHS[day.month - 1]}, {WEEKDAYS[day.weekday()]}" + (f" ({when})" if when else "")
            + (f" — 🎉 {name or 'праздник'}" if name is not None else ""))


def state_text(d: Delivery) -> str:
    return "✓ доставлено" if d.delivered else "💐 собрано" if d.assembled else "не собрано"


def future_deliveries(db, today: date, days: int, hide_delivered: bool = True, query: str = "") -> list[Delivery]:
    query = query.strip().lower()
    found = []
    for k in range(days + 1):
        for d in db.for_day(today + timedelta(days=k)):
            if d.delete_requested_at is not None or (hide_delivered and d.delivered):
                continue
            text = " ".join((d.items, d.recipient, d.phone, d.address, d.customer_contact, d.source)).lower()
            if query and query not in text:
                continue
            found.append(d)
    return sorted(found, key=lambda d: (d.day, parse_start_time(d.time) is None, parse_start_time(d.time) or 0, d.id))


def as_text(items: list[Delivery], today: date, holidays: str = "") -> str:
    """Список для копирования (в мессенджер курьеру или себе)."""
    lines, current = [], None
    for d in items:
        if d.day != current:
            current = d.day
            count = sum(x.day == d.day for x in items)
            lines += ["", f"{day_title(d.day, today, holidays)} — {count}"]
        what = (d.items.splitlines() or ["—"])[0]
        lines.append(f"{d.time or '—'} · {what} · {d.address or 'адрес не указан'}"
                     + (f" · {d.recipient}" if d.recipient else "") + f" · {state_text(d)}")
    return "\n".join(lines).strip()


def as_html(items: list[Delivery], today: date, holidays: str = "") -> str:
    """Страница для печати из браузера."""
    rows, current = [], None
    for d in items:
        if d.day != current:
            current = d.day
            count = sum(x.day == d.day for x in items)
            rows.append(f"<tr><th colspan='6'>{html.escape(day_title(d.day, today, holidays))} — {count}</th></tr>")
        cells = [d.time or "—", d.items, "\n".join(x for x in (d.recipient, d.phone) if x), d.address,
                 d.source, state_text(d)]
        rows.append("<tr>" + "".join(f"<td>{html.escape(c).replace(chr(10), '<br>')}</td>" for c in cells) + "</tr>")
    return ("<html><head><meta charset='utf-8'><title>Заказы вперёд</title><style>"
            "body{font-family:sans-serif;font-size:12px} table{border-collapse:collapse;width:100%}"
            "td,th{border:1px solid #999;padding:4px;vertical-align:top;text-align:left}"
            "th{background:#eee;font-size:14px}</style></head><body>"
            f"<h2>Заказы вперёд (на {datetime.now():%d.%m.%Y %H:%M})</h2><table>"
            "<tr>" + "".join(f"<td><b>{h}</b></td>" for h in HEADERS) + "</tr>"
            + "".join(rows) + "</table></body></html>")


class FutureOrdersDialog(QDialog):
    """Не модальное: можно держать открытым рядом с главным окном и отмечать «собрано»."""

    def __init__(self, window, day: date | None = None):
        super().__init__(window, Qt.Window)
        self.main, self.db = window, window.db
        self.setWindowTitle("Заказы вперёд")
        self.setWindowIcon(flower_icon())
        self.resize(1100, 640)
        self.items: list[Delivery] = []
        self.rows: list[Delivery | None] = []  # строка таблицы → доставка (None — заголовок дня)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Период:"))
        self.period_box = QComboBox()
        for days, title in PERIODS:
            self.period_box.addItem(title, days)
        self.period_box.setCurrentIndex(2)
        filters.addWidget(self.period_box)
        filters.addWidget(QLabel("День:"))
        self.day_box = QComboBox()
        self.day_box.setMinimumWidth(220)
        filters.addWidget(self.day_box)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск: адрес, получатель, телефон, что доставить…")
        self.search_edit.setClearButtonEnabled(True)
        filters.addWidget(self.search_edit, 1)
        self.hide_done = QCheckBox("Скрыть доставленные")
        self.hide_done.setChecked(True)
        filters.addWidget(self.hide_done)
        layout.addLayout(filters)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col in range(len(HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.Stretch if col in (1, 3) else QHeaderView.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda *_: self._open())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.assembled_btn = QPushButton("💐 Собрано")
        self.assembled_btn.clicked.connect(self._toggle_assembled)
        self.open_btn = QPushButton("Показать в доставках")
        self.open_btn.clicked.connect(self._open)
        copy_btn = QPushButton("📋 Скопировать список")
        copy_btn.clicked.connect(self._copy)
        print_btn = QPushButton("🖨 Для печати…")
        print_btn.setToolTip("Откроет список в браузере — оттуда можно распечатать или сохранить в PDF")
        print_btn.clicked.connect(self._print)
        for b in (self.assembled_btn, self.open_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(copy_btn)
        buttons.addWidget(print_btn)
        layout.addLayout(buttons)
        hint = QLabel("Двойной щелчок — открыть доставку в главном окне. Праздники — из списка «Праздничные дни…» "
                      "в настройках зарплаты.")
        hint.setStyleSheet(f"color: {C.gray}")
        layout.addWidget(hint)
        self._wanted_day = day
        self.period_box.currentIndexChanged.connect(lambda _: self.refresh())
        self.day_box.currentIndexChanged.connect(lambda _: self._fill_table())
        self.search_edit.textChanged.connect(lambda _: self.refresh())
        self.hide_done.toggled.connect(lambda _: self.refresh())
        self.refresh()

    @staticmethod
    def holidays() -> str:
        return holidays_text()

    def show_day(self, day: date | None):
        self._wanted_day = day
        self.refresh()

    def refresh(self):
        today = date.today()
        days = self.period_box.currentData()
        if self._wanted_day is not None and (self._wanted_day - today).days > days:
            self.period_box.blockSignals(True)
            self.period_box.setCurrentIndex(len(PERIODS) - 1)
            self.period_box.blockSignals(False)
            days = self.period_box.currentData()
        self.items = future_deliveries(self.db, today, days, self.hide_done.isChecked(), self.search_edit.text())
        # день в списке храним строкой: QComboBox надёжно ищет только простые значения
        current = self._wanted_day.isoformat() if self._wanted_day else self.day_box.currentData()
        self._wanted_day = None
        self.day_box.blockSignals(True)
        self.day_box.clear()
        self.day_box.addItem("Все дни", None)
        holidays = self.holidays()
        for day in sorted({d.day for d in self.items}):
            count = sum(d.day == day for d in self.items)
            self.day_box.addItem(f"{day_title(day, today, holidays)} — {count}", day.isoformat())
        index = self.day_box.findData(current) if current else 0
        self.day_box.setCurrentIndex(max(index, 0))
        self.day_box.blockSignals(False)
        self._fill_table()

    def _visible(self) -> list[Delivery]:
        day = self.day_box.currentData()
        return [d for d in self.items if not day or d.day.isoformat() == day]

    def _fill_table(self):
        today, holidays = date.today(), self.holidays()
        items = self._visible()
        self.rows = []
        self.table.setRowCount(0)
        bold = QFont(self.table.font())
        bold.setBold(True)
        current = None
        for d in items:
            if d.day != current:
                current = d.day
                count = sum(x.day == d.day for x in items)
                done = sum(x.day == d.day and (x.assembled or x.delivered) for x in items)
                row = self.table.rowCount()
                self.table.insertRow(row)
                head = QTableWidgetItem(f"{day_title(d.day, today, holidays)} — заказов: {count}, собрано: {done}")
                head.setFont(bold)
                head.setBackground(QColor(C.info_bg))
                head.setForeground(QColor(C.note_fg))
                head.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(row, 0, head)
                self.table.setSpan(row, 0, 1, len(HEADERS))
                self.rows.append(None)
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = [d.time or "—", d.items, "\n".join(x for x in (d.recipient, d.phone) if x), d.address,
                     d.source, state_text(d)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if d.delivered:
                    item.setForeground(QColor(C.gray))
                self.table.setItem(row, col, item)
            state = self.table.item(row, 5)
            state.setForeground(QColor(C.green if (d.assembled or d.delivered) else C.orange))
            self.rows.append(d)
        self.table.resizeRowsToContents()
        total = len(items)
        done = sum(d.assembled or d.delivered for d in items)
        self.summary.setText(f"Заказов: <b>{total}</b> · собрано: <b>{done}</b> · "
                             f"<span style='color:{C.orange}'>не собрано: <b>{total - done}</b></span>"
                             if total else f"<span style='color:{C.gray}'>Будущих заказов нет.</span>")
        self._update_buttons()

    def _current(self) -> Delivery | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rows) and self.table.selectionModel().hasSelection():
            return self.rows[row]
        return None

    def _update_buttons(self):
        d = self._current()
        self.open_btn.setEnabled(d is not None)
        self.assembled_btn.setEnabled(d is not None and not d.delivered)
        self.assembled_btn.setText("↩ Не собрано" if d is not None and d.assembled else "💐 Собрано")

    def _toggle_assembled(self):
        d = self._current()
        if d is None or d.delivered:
            return
        self.main._set_assembled(d, not d.assembled)
        keep = d.id
        self.refresh()
        for row, x in enumerate(self.rows):
            if x is not None and x.id == keep:
                self.table.selectRow(row)

    def _open(self):
        d = self._current()
        if d is not None:
            self.main.show_delivery(d)

    def _copy(self):
        QApplication.clipboard().setText(as_text(self._visible(), date.today(), self.holidays()))
        self.summary.setText(self.summary.text() + " · <i>список скопирован</i>")

    def _print(self):
        path = os.path.join(tempfile.gettempdir(), "AloFlorist-zakazy.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(as_html(self._visible(), date.today(), self.holidays()))
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
