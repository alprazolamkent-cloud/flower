"""Главное окно: календарь на месяц и список доставок на выбранный день."""

from datetime import date

from PySide6.QtCore import QDate, QLocale, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView, QCalendarWidget, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .db import Database, Delivery, Totals
from .delivery_dialog import DeliveryDialog, money

MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

HEADERS = ["✓", "Время", "Что доставить", "Кому", "Телефон", "Адрес",
           "Взяли", "Курьеру", "Осталось"]
COL_DONE, COL_TIME, COL_ITEMS, COL_RECIPIENT, COL_PHONE, COL_ADDRESS, \
    COL_PRICE, COL_COURIER, COL_REST = range(len(HEADERS))

DELIVERED_BG = QColor("#e3f4e4")
PENDING_DAY_BG = QColor("#ffe0b2")  # в этот день есть недоставленные
DONE_DAY_BG = QColor("#c8e6c9")  # всё доставлено


def to_date(qd: QDate) -> date:
    return date(qd.year(), qd.month(), qd.day())


def totals_text(t: Totals) -> str:
    return (
        f"Доставок: <b>{t.count}</b> (доставлено {t.delivered})"
        f" &nbsp;|&nbsp; Взяли: <b>{money(t.price)}</b>"
        f" &nbsp;|&nbsp; Курьерам: <b>{money(t.courier_pay)}</b>"
        f" &nbsp;|&nbsp; Осталось: <b>{money(t.remainder)}</b>"
    )


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.deliveries: list[Delivery] = []
        self._marked_days: list[QDate] = []
        self._loading = False

        self.setWindowTitle("Доставки цветов")
        self.resize(1280, 720)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # Левая часть: календарь и итоги месяца
        left = QVBoxLayout()
        root.addLayout(left, 0)

        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale(QLocale.Russian, QLocale.Russia))
        self.calendar.setFirstDayOfWeek(Qt.Monday)
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setMinimumSize(420, 340)
        self.calendar.selectionChanged.connect(self.refresh_day)
        self.calendar.currentPageChanged.connect(lambda *_: self.refresh_month())
        left.addWidget(self.calendar)

        legend = QLabel(
            "<span style='background:#ffe0b2'>&nbsp;&nbsp;&nbsp;&nbsp;</span> есть недоставленные"
            " &nbsp; <span style='background:#c8e6c9'>&nbsp;&nbsp;&nbsp;&nbsp;</span> всё доставлено"
        )
        left.addWidget(legend)

        today_btn = QPushButton("Сегодня")
        today_btn.clicked.connect(self.go_today)
        left.addWidget(today_btn)

        self.month_box = QGroupBox()
        month_layout = QVBoxLayout(self.month_box)
        self.month_label = QLabel()
        self.month_label.setWordWrap(True)
        month_layout.addWidget(self.month_label)
        left.addWidget(self.month_box)
        left.addStretch(1)

        # Правая часть: доставки за день
        right = QVBoxLayout()
        root.addLayout(right, 1)

        self.day_title = QLabel()
        title_font = QFont()
        title_font.setPointSize(15)
        title_font.setBold(True)
        self.day_title.setFont(title_font)
        right.addWidget(self.day_title)

        buttons = QHBoxLayout()
        add_btn = QPushButton("➕ Добавить доставку")
        add_btn.clicked.connect(self.add_delivery)
        self.edit_btn = QPushButton("✏️ Изменить")
        self.edit_btn.clicked.connect(self.edit_delivery)
        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self.delete_delivery)
        for b in (add_btn, self.edit_btn, self.delete_btn):
            b.setMinimumHeight(32)
            buttons.addWidget(b)
        buttons.addStretch(1)
        right.addLayout(buttons)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col in range(len(HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_ITEMS, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_ADDRESS, QHeaderView.Stretch)
        self.table.horizontalHeaderItem(COL_DONE).setToolTip("Доставлено")
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        right.addWidget(self.table, 1)

        self.day_totals = QLabel()
        right.addWidget(self.day_totals)

        hint = QLabel("Галочка в первом столбце — «Доставлено». Двойной щелчок — изменить.")
        hint.setStyleSheet("color: gray")
        right.addWidget(hint)

        QShortcut(QKeySequence.New, self, activated=self.add_delivery)
        QShortcut(QKeySequence.Delete, self.table, activated=self.delete_delivery)

        self.refresh_month()
        self.refresh_day()

    # ----- обновление экрана -----

    def selected_day(self) -> date:
        return to_date(self.calendar.selectedDate())

    def go_today(self):
        self.calendar.setSelectedDate(QDate.currentDate())

    def refresh_month(self):
        year, month = self.calendar.yearShown(), self.calendar.monthShown()

        for qd in self._marked_days:
            self.calendar.setDateTextFormat(qd, QTextCharFormat())
        self._marked_days = []

        for day, (count, delivered) in self.db.counts_for_month(year, month).items():
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Bold)
            fmt.setBackground(QBrush(DONE_DAY_BG if delivered == count else PENDING_DAY_BG))
            fmt.setToolTip(f"Доставок: {count}, доставлено: {delivered}")
            qd = QDate(day.year, day.month, day.day)
            self.calendar.setDateTextFormat(qd, fmt)
            self._marked_days.append(qd)

        self.month_box.setTitle(f"Итого за {MONTHS[month - 1].lower()} {year}")
        self.month_label.setText(
            totals_text(self.db.totals_for_month(year, month)).replace(" &nbsp;|&nbsp; ", "<br>")
        )

    def refresh_day(self):
        day = self.selected_day()
        self.day_title.setText(
            f"Доставки на {day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year}, {WEEKDAYS[day.weekday()]}"
        )
        self.deliveries = self.db.for_day(day)

        self._loading = True
        self.table.setRowCount(len(self.deliveries))
        for row, d in enumerate(self.deliveries):
            done = QTableWidgetItem()
            done.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            done.setCheckState(Qt.Checked if d.delivered else Qt.Unchecked)
            done.setToolTip("Доставлено" if d.delivered else "Не доставлено")
            cells = {
                COL_DONE: done,
                COL_TIME: QTableWidgetItem(d.time),
                COL_ITEMS: QTableWidgetItem(d.items),
                COL_RECIPIENT: QTableWidgetItem(d.recipient),
                COL_PHONE: QTableWidgetItem(d.phone),
                COL_ADDRESS: QTableWidgetItem(d.address),
                COL_PRICE: QTableWidgetItem(money(d.price)),
                COL_COURIER: QTableWidgetItem(money(d.courier_pay)),
                COL_REST: QTableWidgetItem(money(d.remainder)),
            }
            for col in (COL_PRICE, COL_COURIER, COL_REST):
                cells[col].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if d.remainder < 0:
                cells[COL_REST].setForeground(QColor("#c62828"))
            for col, item in cells.items():
                if d.delivered:
                    item.setBackground(DELIVERED_BG)
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()
        self._loading = False

        self.day_totals.setText(totals_text(self.db.totals_for_day(day)))
        self._update_buttons()

    def refresh_all(self):
        self.refresh_month()
        self.refresh_day()

    def _update_buttons(self):
        has = self.current_delivery() is not None
        self.edit_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    # ----- действия -----

    def current_delivery(self) -> Delivery | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.deliveries) and self.table.selectionModel().hasSelection():
            return self.deliveries[row]
        return None

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() != COL_DONE:
            return
        d = self.deliveries[item.row()]
        self.db.set_delivered(d.id, item.checkState() == Qt.Checked)
        self.refresh_all()

    def _on_double_click(self, item: QTableWidgetItem):
        if item.column() != COL_DONE:
            self.edit_delivery()

    def _open_dialog(self, delivery: Delivery) -> bool:
        dlg = DeliveryDialog(self, delivery)
        return dlg.exec() == DeliveryDialog.Accepted

    def _show_day(self, day: date):
        qd = QDate(day.year, day.month, day.day)
        if self.calendar.selectedDate() != qd:
            self.calendar.setSelectedDate(qd)  # сам вызовет refresh_day
        self.refresh_all()

    def add_delivery(self):
        d = Delivery(day=self.selected_day())
        if self._open_dialog(d):
            self.db.add(d)
            self._show_day(d.day)

    def edit_delivery(self):
        d = self.current_delivery()
        if d is None:
            return
        if self._open_dialog(d):
            self.db.update(d)
            self._show_day(d.day)
        else:
            self.refresh_day()  # отменили — вернуть исходные значения

    def delete_delivery(self):
        d = self.current_delivery()
        if d is None:
            return
        what = d.items.splitlines()[0] if d.items else d.address
        answer = QMessageBox.question(
            self, "Удалить доставку",
            f"Удалить доставку «{what}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.db.delete(d.id)
            self.refresh_all()
