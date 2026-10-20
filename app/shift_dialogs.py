"""Окна смен: начало, сдача, расходы и изъятия, личный кабинет."""

import calendar
import html
from datetime import date, datetime, time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .db import Database, User, UserError
from .delivery_dialog import MAX_MONEY, money, money_box
from .shifts import KIND_TEXT, PROBLEM_STATUSES, STATUS_TEXT, Shift, expected_cash, window

RED, GREEN, ORANGE, GRAY = "#c62828", "#2e7d32", "#ef6c00", "#757575"
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def fmt_dt(moment: datetime | None) -> str:
    return f"{moment:%d.%m %H:%M}" if moment else ""


def diff_text(diff: int | None) -> str:
    if diff is None:
        return ""
    if diff == 0:
        return "сходится"
    return f"недостача {money(-diff)}" if diff < 0 else f"излишек {money(diff)}"


def diff_color(diff: int | None) -> str:
    return GRAY if diff is None else (GREEN if diff == 0 else RED)


def _html(text: str) -> str:
    """Текст сотрудника для QLabel: без разметки, с переносами строк."""
    return html.escape(text).replace("\n", "<br>")


def count_box() -> QSpinBox:
    """Поле для пересчитанной суммы: пустое, пока человек не введёт число сам."""
    box = money_box()
    box.setRange(-1, MAX_MONEY)
    box.setSpecialValueText("— пересчитайте —")
    box.setValue(-1)
    return box


def _buttons(dialog: QDialog, ok_text: str) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText(ok_text)
    buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


def _gray(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {GRAY}")
    return label


class StartShiftDialog(QDialog):
    """Вход в смену: пересчитать кассу и сверить с тем, что оставила прошлая смена."""

    def __init__(self, parent, db: Database, user: User, change: time):
        super().__init__(parent)
        self.db, self.user, self.change = db, user, change
        self.shift: Shift | None = None
        self.prev = db.shift_before(datetime.now())
        self.setWindowTitle("Начало смены")
        self.setMinimumWidth(440)

        form = QFormLayout(self)
        form.addRow(QLabel(f"<h3>Начало смены</h3>{user.name or user.login}, "
                           f"{datetime.now():%d.%m.%Y %H:%M}"))
        if self.prev is None:
            info = "Это первая смена в программе."
        elif self.prev.closing_cash is not None:
            info = (f"Предыдущая смена ({self.prev.user_name or '—'}) оставила в кассе: "
                    f"<b>{money(self.prev.closing_cash)}</b>")
        else:
            info = (f"Предыдущая смена ({self.prev.user_name or '—'}) ещё не сдана — "
                    "сверка будет видна, когда её сдадут.")
        info_label = QLabel(info)
        info_label.setWordWrap(True)
        form.addRow(info_label)
        if self.prev is not None and self.prev.note:
            note = QLabel(f"<b>Комментарий предыдущей смены:</b><br>{_html(self.prev.note)}")
            note.setWordWrap(True)
            note.setStyleSheet("background: #fff8e1; border: 1px solid #ffe082; padding: 6px")
            form.addRow(note)

        self.cash_box = count_box()
        self.cash_box.valueChanged.connect(self._update)
        form.addRow("Пересчитайте наличные в кассе:", self.cash_box)
        self.diff_label = QLabel()
        form.addRow("", self.diff_label)
        form.addRow(_buttons(self, "Начать смену"))
        self._update()

    def _handover_diff(self) -> int | None:
        if self.cash_box.value() < 0 or self.prev is None or self.prev.closing_cash is None:
            return None
        return self.cash_box.value() - self.prev.closing_cash

    def _update(self):
        diff = self._handover_diff()
        if diff is None:
            self.diff_label.setText("")
        elif diff == 0:
            self.diff_label.setText(f"<b style='color:{GREEN}'>Сходится с предыдущей сменой</b>")
        else:
            self.diff_label.setText(f"<b style='color:{RED}'>Не сходится: {diff_text(diff)}</b>")

    def accept(self):
        cash = self.cash_box.value()
        if cash < 0:
            QMessageBox.warning(self, "Касса", "Пересчитайте наличные и введите сумму.")
            return
        diff = self._handover_diff()
        if diff:
            answer = QMessageBox.question(
                self, "Касса не сходится",
                f"Предыдущая смена оставила {money(self.prev.closing_cash)}, а вы насчитали "
                f"{money(cash)} ({diff_text(diff)}).\n\nНачать смену с суммой {money(cash)}? "
                "Расхождение увидит администратор.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.shift = self.db.open_shift(self.user.id, cash, datetime.now(), self.change)
        super().accept()


class CloseShiftDialog(QDialog):
    """Сдача смены: продажи, касса, расчёт «должно быть» и разница."""

    def __init__(self, parent, db: Database, shift: Shift, change: time, grace: int):
        super().__init__(parent)
        self.db, self.change, self.grace = db, change, grace
        self.shift = db.get_shift(shift.id)  # свежие суммы расходов и изъятий
        s = self.shift
        delivery_cash, courier_cash = db.delivery_cash_for_shift(s.day, change)
        self.setWindowTitle("Сдача смены")
        self.setMinimumWidth(500)

        form = QFormLayout(self)
        form.addRow(QLabel(f"<h3>Сдача смены</h3>{s.user_name or '—'} · с {fmt_dt(s.opened_at)} "
                           f"до {fmt_dt(s.end(change))}"))
        if datetime.now() >= s.end(change):
            late = QLabel(f"<b style='color:{ORANGE}'>Пересменка уже прошла — смена будет отмечена "
                          "«сдана с опозданием».</b>")
            late.setWordWrap(True)
            form.addRow(late)

        form.addRow("На начало смены (приняли):", QLabel(money(s.opening_cash)))
        self.sales_box = count_box()
        self.sales_box.setToolTip("Сумма продаж наличными за смену из отчёта Hoog")
        form.addRow("Продажи наличными (из Hoog):", self.sales_box)
        self.delivery_box = money_box()
        self.delivery_box.setValue(delivery_cash)
        form.addRow("Взяли наличными за доставки:", self.delivery_box)
        self.courier_box = money_box()
        self.courier_box.setValue(courier_cash)
        form.addRow("Отдали курьерам (такси) из кассы:", self.courier_box)
        form.addRow("", _gray("Доставки посчитаны по записям этой смены. Если какие-то оплачены "
                              "картой или переводом — поправьте суммы."))
        form.addRow("Расходы из кассы:", QLabel(f"− {money(s.expenses)}"))
        form.addRow("Изъятия из кассы:", QLabel(f"− {money(s.withdrawals)}"))
        self.expected_label = QLabel()
        form.addRow("Должно быть в кассе:", self.expected_label)
        self.cash_box = count_box()
        form.addRow("Фактически в кассе (пересчитайте):", self.cash_box)
        self.diff_label = QLabel()
        form.addRow("Разница:", self.diff_label)
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("Что важно знать: почему не сходится касса, что случилось "
                                          "за смену, что передать следующей смене")
        self.note_edit.setFixedHeight(70)
        form.addRow("Комментарий к смене\n(по желанию):", self.note_edit)
        form.addRow("", _gray("Комментарий увидит администратор и следующая смена."))
        form.addRow(_buttons(self, "Сдать смену"))

        for box in (self.sales_box, self.delivery_box, self.courier_box, self.cash_box):
            box.valueChanged.connect(self._update)
        self._update()

    def expected(self) -> int:
        s = self.shift
        return expected_cash(s.opening_cash, max(self.sales_box.value(), 0), self.delivery_box.value(),
                             self.courier_box.value(), s.expenses, s.withdrawals)

    def difference(self) -> int | None:
        if self.cash_box.value() < 0 or self.sales_box.value() < 0:
            return None
        return self.cash_box.value() - self.expected()

    def _update(self):
        self.expected_label.setText(f"<b>{money(self.expected())}</b>")
        diff = self.difference()
        self.diff_label.setText(f"<b style='color:{diff_color(diff)}'>{diff_text(diff)}</b>")

    def accept(self):
        if self.sales_box.value() < 0:
            QMessageBox.warning(self, "Сдача смены", "Введите продажи наличными из Hoog (если не было — 0).")
            return
        if self.cash_box.value() < 0:
            QMessageBox.warning(self, "Сдача смены", "Пересчитайте наличные в кассе и введите сумму.")
            return
        diff = self.difference()
        if diff:
            answer = QMessageBox.question(
                self, "Касса не сходится",
                f"Должно быть {money(self.expected())}, а в кассе {money(self.cash_box.value())} "
                f"({diff_text(diff)}).\n\nВсё равно сдать смену? Это увидит администратор.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if not self.shift.can_close(datetime.now(), self.change, self.grace):
            QMessageBox.warning(self, "Сдача смены", "Время на сдачу этой смены уже вышло.")
            self.reject()
            return
        try:
            self.shift = self.db.close_shift(
                self.shift.id, datetime.now(), self.cash_box.value(), self.sales_box.value(),
                self.delivery_box.value(), self.courier_box.value(), self.note_edit.toPlainText())
        except UserError as e:
            QMessageBox.warning(self, "Сдача смены", str(e))
            return
        super().accept()


def _ops_table() -> QTableWidget:
    table = QTableWidget(0, 5)
    table.setHorizontalHeaderLabels(["Время", "Тип", "Сумма", "Причина / кто забрал", "Записал"])
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.Stretch)
    return table


def _fill_ops(table: QTableWidget, db: Database, shift_id: int | None):
    ops = db.cash_ops(shift_id) if shift_id is not None else []
    table.setRowCount(len(ops))
    for row, op in enumerate(ops):
        cells = [fmt_dt(op.created_at), KIND_TEXT[op.kind], money(op.amount), op.reason, op.created_by_name]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col == 2:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, col, item)


class CashOpDialog(QDialog):
    """Расход или изъятие из кассы во время смены."""

    PLACEHOLDERS = {"expense": "на что потратили, например скотч или вода",
                    "withdrawal": "кто забрал деньги, например хозяйка или инкассация"}

    def __init__(self, parent, db: Database, shift: Shift, user: User):
        super().__init__(parent)
        self.db, self.shift, self.user = db, shift, user
        self.setWindowTitle("Расход / изъятие из кассы")
        self.resize(560, 420)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Уже записано за смену</b>"))
        self.table = _ops_table()
        _fill_ops(self.table, db, shift.id)
        layout.addWidget(self.table, 1)

        form = QFormLayout()
        self.kind_box = QComboBox()
        self.kind_box.addItem("Расход — потратили на нужды магазина", "expense")
        self.kind_box.addItem("Изъятие — забрали деньги из кассы", "withdrawal")
        self.kind_box.currentIndexChanged.connect(self._update_hint)
        form.addRow("Что:", self.kind_box)
        self.amount_box = money_box()
        form.addRow("Сумма:", self.amount_box)
        self.reason_edit = QLineEdit()
        form.addRow("Причина:", self.reason_edit)
        layout.addLayout(form)
        layout.addWidget(_buttons(self, "Записать"))
        self._update_hint()

    def _update_hint(self):
        self.reason_edit.setPlaceholderText(self.PLACEHOLDERS[self.kind_box.currentData()])

    def accept(self):
        try:
            self.db.add_cash_op(self.shift.id, self.kind_box.currentData(), self.amount_box.value(),
                                self.reason_edit.text(), self.user.id, datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Касса", str(e))
            return
        super().accept()


CABINET_HEADERS = ["Смена", "Сотрудник", "Открыта", "Сдана", "Статус", "Приняли", "При приёме",
                   "Продажи нал.", "За доставки", "Курьерам", "Расходы", "Изъятия",
                   "Должно быть", "Сдали", "Касса", "Комментарий"]
MONEY_COLS = {5, 7, 8, 9, 10, 11, 12, 13}


class CabinetDialog(QDialog):
    """Личный кабинет: смены и итоги за месяц. Админ может смотреть любого сотрудника или всех."""

    def __init__(self, parent, db: Database, user: User, change: time, grace: int, year: int, month: int):
        super().__init__(parent)
        self.db, self.user, self.change, self.grace = db, user, change, grace
        self.year, self.month = year, month
        self.shifts: list[Shift] = []
        self.setWindowTitle("Личный кабинет")
        self.resize(1440, 640)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.person_box = QComboBox()
        if user.is_admin:
            self.person_box.addItem("Все сотрудники", None)
            for u in db.users():
                self.person_box.addItem(u.name or u.login, u.id)
        else:
            self.person_box.addItem(user.name or user.login, user.id)
            self.person_box.setEnabled(False)
        self.person_box.currentIndexChanged.connect(self.refresh)
        top.addWidget(QLabel("Сотрудник:"))
        top.addWidget(self.person_box)
        top.addStretch(1)
        prev_btn, next_btn = QPushButton("◀"), QPushButton("▶")
        prev_btn.clicked.connect(lambda: self._shift_month(-1))
        next_btn.clicked.connect(lambda: self._shift_month(1))
        self.month_label = QLabel()
        self.month_label.setMinimumWidth(130)
        self.month_label.setAlignment(Qt.AlignCenter)
        for w in (prev_btn, self.month_label, next_btn):
            top.addWidget(w)
        layout.addLayout(top)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 14px; padding: 6px 0")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, len(CABINET_HEADERS))
        self.table.setHorizontalHeaderLabels(CABINET_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(CABINET_HEADERS) - 1, QHeaderView.Stretch)
        header.setMinimumSectionSize(40)
        self.table.itemSelectionChanged.connect(self._show_ops)
        layout.addWidget(self.table, 3)

        self.ops_title = QLabel()
        layout.addWidget(self.ops_title)
        self.ops = _ops_table()
        layout.addWidget(self.ops, 1)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def _shift_month(self, step: int):
        index = self.year * 12 + self.month - 1 + step
        self.year, self.month = divmod(index, 12)
        self.month += 1
        self.refresh()

    def refresh(self):
        self.month_label.setText(f"<b>{MONTHS[self.month - 1]} {self.year}</b>")
        last = calendar.monthrange(self.year, self.month)[1]
        self.shifts = self.db.shifts_between(date(self.year, self.month, 1),
                                             date(self.year, self.month, last),
                                             self.person_box.currentData())
        now = datetime.now()
        stats = {"sold": 0, "short": 0, "extra": 0, "late": 0, "not_closed": 0, "handover": 0}

        self.table.setRowCount(len(self.shifts))
        for row, s in enumerate(self.shifts):
            status = s.status(now, self.change, self.grace)
            diff = s.difference()
            handover = self.db.handover_difference(s)
            closed = s.closed_at is not None
            if closed:
                stats["sold"] += s.cash_sales
            if diff:
                stats["short" if diff < 0 else "extra"] += abs(diff)
            if status in ("late", "not_closed"):
                stats[status] += 1
            if handover:
                stats["handover"] += 1

            start, end = window(s.day, self.change)
            cells = [
                f"{start:%d.%m} → {end:%d.%m}", s.user_name or "—", fmt_dt(s.opened_at),
                fmt_dt(s.closed_at), STATUS_TEXT[status], money(s.opening_cash),
                diff_text(handover) if handover is not None else "",
                money(s.cash_sales) if closed else "", money(s.delivery_cash) if closed else "",
                money(s.courier_cash) if closed else "", money(s.expenses), money(s.withdrawals),
                money(s.expected_cash()) if closed else "",
                money(s.closing_cash) if closed else "", diff_text(diff), " ".join(s.note.split()),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in MONEY_COLS:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 4 and status in PROBLEM_STATUSES:
                    item.setForeground(QColor(ORANGE if status == "closing" else RED))
                if col == 6 and handover:
                    item.setForeground(QColor(RED))
                if col == 14:
                    item.setForeground(QColor(diff_color(diff)))
                if col == 15 and s.note:
                    item.setToolTip(s.note)
                self.table.setItem(row, col, item)

        parts = [f"Смен: <b>{len(self.shifts)}</b>",
                 f"Продано наличными: <b>{money(stats['sold'])}</b>"]
        if stats["short"]:
            parts.append(f"<span style='color:{RED}'>Недостача: <b>{money(stats['short'])}</b></span>")
        if stats["extra"]:
            parts.append(f"Излишек: <b>{money(stats['extra'])}</b>")
        if stats["handover"]:
            parts.append(f"<span style='color:{RED}'>Не сошлось при приёме: <b>{stats['handover']}</b></span>")
        if stats["late"]:
            parts.append(f"<span style='color:{RED}'>Сдано с опозданием: <b>{stats['late']}</b></span>")
        if stats["not_closed"]:
            parts.append(f"<span style='color:{RED}'>Не сдано: <b>{stats['not_closed']}</b></span>")
        self.summary.setText(" &nbsp;·&nbsp; ".join(parts))
        self._show_ops()

    def _show_ops(self):
        row = self.table.currentRow()
        has = 0 <= row < len(self.shifts) and self.table.selectionModel().hasSelection()
        shift = self.shifts[row] if has else None
        self.ops_title.setText(
            f"<b>Расходы и изъятия смены {shift.day:%d.%m}</b>"
            + (f" · комментарий: {_html(shift.note)}" if shift.note else "")
            if shift else "<b>Расходы и изъятия</b> — выберите смену в таблице")
        _fill_ops(self.ops, self.db, shift.id if shift else None)
