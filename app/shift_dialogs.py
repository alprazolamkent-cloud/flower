"""Окна смен: начало, сдача, расходы и изъятия, личный кабинет."""

import calendar
import html
from collections.abc import Callable
from datetime import date, datetime, time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .db import Database, User, UserError
from .delivery_dialog import MAX_MONEY, money, money_box
from .salary import PenaltyKind, SalaryRules, calculate, forecast, monthly_ranking, shop_writeoff
from .shifts import (
    KIND_TEXT, PROBLEM_STATUSES, STATUS_TEXT, WRITEOFF_REASONS, ChecklistItem, Penalty, Shift, WaterSchedule,
    expected_cash, revenue_mismatch, window,
)
from .salary_view import SalaryPanel
from .theme import C

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
    return C.gray if diff is None else (C.green if diff == 0 else C.red)


def _html(text: str) -> str:
    """Текст сотрудника для QLabel: без разметки, с переносами строк."""
    return html.escape(text).replace("\n", "<br>")


def count_box(placeholder: str = "— пересчитайте —") -> QSpinBox:
    """Поле для суммы, которую обязательно ввести самому: пустое, пока не введут число."""
    box = money_box()
    box.setRange(-1, MAX_MONEY)
    box.setSpecialValueText(placeholder)
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
    label.setStyleSheet(f"color: {C.gray}")
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
            note.setStyleSheet(f"background: {C.note_bg}; border: 1px solid {C.note_border}; "
                               f"color: {C.note_fg}; padding: 6px")
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
            self.diff_label.setText(f"<b style='color:{C.green}'>Сходится с предыдущей сменой</b>")
        else:
            self.diff_label.setText(f"<b style='color:{C.red}'>Не сходится: {diff_text(diff)}</b>")

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
    """Сдача смены: выручка за смену, касса, расчёт «должно быть» и разница, смена воды."""

    def __init__(self, parent, db: Database, shift: Shift, change: time, grace: int,
                 water: WaterSchedule | None = None):
        super().__init__(parent)
        self.db, self.change, self.grace = db, change, grace
        self.shift = db.get_shift(shift.id)  # свежие суммы расходов и изъятий
        s = self.shift
        delivery_sum, _ = db.delivery_cash_for_shift(s.day, change)
        own = db.deliveries_for_shift(s.day, change)
        # такси оплачивают из кассы, когда букет повезли: считаем только отмеченные доставленными
        courier_sum = sum(d.courier_pay for d in own if d.delivered)
        not_marked = [d for d in own if not d.delivered and d.courier_pay and d.delete_requested_at is None]
        self.setWindowTitle("Сдача смены")
        self.setMinimumWidth(1040)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<h3>Сдача смены</h3>{s.user_name or '—'} · с {fmt_dt(s.opened_at)} "
                                f"до {fmt_dt(s.end(change))}"))
        if datetime.now() >= s.end(change):
            late = QLabel(f"<b style='color:{C.orange}'>Пересменка уже прошла — смена будет отмечена "
                          "«сдана с опозданием».</b>")
            late.setWordWrap(True)
            layout.addWidget(late)

        columns = QHBoxLayout()
        layout.addLayout(columns)

        sales = QGroupBox("Выручка за смену (из Hoog)")
        sales_form = QFormLayout(sales)
        self.revenue_box = count_box("— введите —")
        self.revenue_box.setToolTip("Вся сумма за смену: наличные, терминал, QR-код — всё вместе")
        sales_form.addRow("Приход:", self.revenue_box)
        self.flowers_box = count_box("— введите —")
        self.flowers_box.setToolTip("Сколько из прихода — продажа цветов")
        sales_form.addRow("Цветы:", self.flowers_box)
        self.terminal_box = money_box()
        sales_form.addRow("Терминал:", self.terminal_box)
        self.qr_box = money_box()
        sales_form.addRow("QR-код:", self.qr_box)
        self.sales_box = count_box("— введите —")
        self.sales_box.setToolTip("Продажи наличными за смену из отчёта Hoog")
        sales_form.addRow("Продажи наличными:", self.sales_box)
        self.check_label = QLabel()  # две строки разбиты сами; запас по высоте — значок ⚠ выше обычных букв
        self.check_label.setMinimumHeight(self.check_label.fontMetrics().lineSpacing() * 2 + 8)
        sales_form.addRow(self.check_label)
        self.delivery_box = money_box()
        self.delivery_box.setValue(delivery_sum)
        sales_form.addRow("Взяли за доставки:", self.delivery_box)
        sales_form.addRow("", _gray("перевод / QR — в кассу не идёт"))
        columns.addWidget(sales)

        writeoff = QGroupBox("Списано цветов за смену")
        writeoff_form = QFormLayout(writeoff)
        writeoff_form.addRow(_gray("На какую сумму списали, по причинам. Не было — оставьте 0."))
        self.writeoff_boxes: dict[str, QSpinBox] = {}
        for reason in WRITEOFF_REASONS:
            box = money_box()
            self.writeoff_boxes[reason] = box
            writeoff_form.addRow(f"{reason}:", box)
        self.writeoff_total = QLabel()
        writeoff_form.addRow("Всего списано:", self.writeoff_total)
        columns.addWidget(writeoff)

        cash = QGroupBox("Касса (наличные)")
        cash_form = QFormLayout(cash)
        cash_form.addRow("На начало смены (приняли):", QLabel(money(s.opening_cash)))
        self.cash_sales_label = QLabel()
        cash_form.addRow("+ продажи наличными:", self.cash_sales_label)
        self.courier_box = money_box()
        self.courier_box.setValue(courier_sum)
        cash_form.addRow("− отдали курьерам (такси):", self.courier_box)
        if not_marked:
            hint = _gray(f"Не отмечены доставленными: {len(not_marked)} (курьеру "
                         f"{money(sum(d.courier_pay for d in not_marked))}). Если уже отвезли — добавьте сумму.")
            hint.setStyleSheet(f"color: {C.orange}")
            cash_form.addRow("", hint)
        cash_form.addRow("− расходы из кассы:", QLabel(money(s.expenses)))
        cash_form.addRow("− изъятия из кассы:", QLabel(money(s.withdrawals)))
        self.expected_label = QLabel()
        cash_form.addRow("= должно быть в кассе:", self.expected_label)
        self.cash_box = count_box()
        cash_form.addRow("Фактически в кассе:", self.cash_box)
        self.diff_label = QLabel()
        cash_form.addRow("Разница:", self.diff_label)
        columns.addWidget(cash)

        self.water_check: QCheckBox | None = None
        self.water_done = None
        if water is not None and water.is_water_day(s.day):
            self.water_done = db.water_change(s.day)
            if self.water_done:
                who, when = self.water_done
                self.water_check = QCheckBox(f"💧 Вода поменяна — отметил(а) {who or '—'} в {when:%H:%M}")
                self.water_check.setChecked(True)
                self.water_check.setEnabled(False)
            else:
                self.water_check = QCheckBox("💧 Вода поменяна (сегодня день смены воды)")
            layout.addWidget(self.water_check)

        self.duty_boxes: list[tuple[ChecklistItem, QCheckBox]] = []
        items = db.checklist(s)
        if items:
            duties = QGroupBox("Обязанности смены")
            grid = QGridLayout(duties)
            for i, item in enumerate(items):
                box = QCheckBox(item.text)
                box.setChecked(item.done)
                grid.addWidget(box, i // 2, i % 2)
                self.duty_boxes.append((item, box))
            layout.addWidget(duties)

        note_form = QFormLayout()
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("Что важно знать: почему не сходится касса, что случилось "
                                          "за смену, что передать следующей смене")
        self.note_edit.setFixedHeight(64)
        note_form.addRow("Комментарий к смене\n(по желанию):", self.note_edit)
        note_form.addRow("", _gray("Комментарий увидит администратор и следующая смена."))
        layout.addLayout(note_form)
        layout.addWidget(_buttons(self, "Сдать смену"))

        for box in (self.revenue_box, self.terminal_box, self.qr_box, self.sales_box,
                    self.courier_box, self.cash_box, *self.writeoff_boxes.values()):
            box.valueChanged.connect(self._update)
        self._update()

    def expected(self) -> int:
        s = self.shift
        return expected_cash(s.opening_cash, max(self.sales_box.value(), 0), 0,
                             self.courier_box.value(), s.expenses, s.withdrawals)

    def difference(self) -> int | None:
        if self.cash_box.value() < 0 or self.sales_box.value() < 0:
            return None
        return self.cash_box.value() - self.expected()

    def revenue_check(self) -> int | None:
        """Приход − (наличные + терминал + QR), None — пока не всё введено."""
        if self.revenue_box.value() < 0 or self.sales_box.value() < 0:
            return None
        return revenue_mismatch(self.revenue_box.value(), self.sales_box.value(),
                                self.terminal_box.value(), self.qr_box.value())

    def _update(self):
        self.writeoff_total.setText(f"<b>{money(sum(b.value() for b in self.writeoff_boxes.values()))}</b>")
        self.cash_sales_label.setText(money(max(self.sales_box.value(), 0)))
        self.expected_label.setText(f"<b>{money(self.expected())}</b>")
        diff = self.difference()
        self.diff_label.setText(f"<b style='color:{diff_color(diff)}'>{diff_text(diff)}</b>")
        check = self.revenue_check()
        parts = money(max(self.sales_box.value(), 0) + self.terminal_box.value() + self.qr_box.value())
        if check is None:
            self.check_label.setText("")
        elif check == 0:
            self.check_label.setText(f"<span style='color:{C.green}'>✓ нал. + терминал + QR = приход</span>")
        else:
            self.check_label.setText(f"<span style='color:{C.orange}'>⚠ нал. + терминал + QR = {parts},<br>"
                                     f"с приходом разница {money(abs(check))}</span>")

    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def accept(self):
        required = ((self.revenue_box, "приход"), (self.flowers_box, "сколько продано цветов"),
                    (self.sales_box, "продажи наличными"))
        for box, what in required:
            if box.value() < 0:
                QMessageBox.warning(self, "Сдача смены", f"Введите {what} (если не было — 0).")
                box.setFocus()
                return
        if self.cash_box.value() < 0:
            QMessageBox.warning(self, "Сдача смены", "Пересчитайте наличные в кассе и введите сумму.")
            self.cash_box.setFocus()
            return
        if self.flowers_box.value() > self.revenue_box.value():
            QMessageBox.warning(self, "Сдача смены", "Цветов не может быть продано больше, чем весь приход.")
            return
        check = self.revenue_check()
        if check and not self._ask(
                "Приход не сходится",
                f"Наличные + терминал + QR-код = "
                f"{money(self.sales_box.value() + self.terminal_box.value() + self.qr_box.value())}, "
                f"а приход {money(self.revenue_box.value())}.\n\nПроверьте цифры. Всё равно сдать смену?"):
            return
        diff = self.difference()
        if diff and not self._ask(
                "Касса не сходится",
                f"Должно быть {money(self.expected())}, а в кассе {money(self.cash_box.value())} "
                f"({diff_text(diff)}).\n\nВсё равно сдать смену? Это увидит администратор."):
            return
        water_now = self.water_check is not None and self.water_done is None and self.water_check.isChecked()
        if self.water_check is not None and self.water_done is None and not water_now and not self._ask(
                "Смена воды", "Сегодня день смены воды, а отметки нет.\n\nСдать смену без замены воды?"):
            return
        missed = [item.text for item, box in self.duty_boxes if not box.isChecked()]
        if missed and not self._ask("Обязанности", "Не отмечено:\n• " + "\n• ".join(missed)
                                    + "\n\nВсё равно сдать смену? Это увидит администратор."):
            return
        if not self.shift.can_close(datetime.now(), self.change, self.grace):
            QMessageBox.warning(self, "Сдача смены", "Время на сдачу этой смены уже вышло.")
            self.reject()
            return
        for item, box in self.duty_boxes:
            if box.isChecked() != item.done:
                self.db.set_duty_done(self.shift.id, item.duty_id, item.text, box.isChecked(), datetime.now())
        try:
            self.shift = self.db.close_shift(
                self.shift.id, datetime.now(), self.cash_box.value(), self.sales_box.value(),
                self.courier_box.value(), self.note_edit.toPlainText(),
                revenue=self.revenue_box.value(), flowers=self.flowers_box.value(),
                terminal=self.terminal_box.value(), qr=self.qr_box.value(),
                deliveries=self.delivery_box.value(),
                writeoff_items={r: b.value() for r, b in self.writeoff_boxes.items() if b.value()})
        except UserError as e:
            QMessageBox.warning(self, "Сдача смены", str(e))
            return
        if water_now:
            self.db.mark_water(self.shift.day, self.shift.user_id, datetime.now())
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
                    "withdrawal": "кто забрал деньги, например администратор или инкассация"}

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
        self.db.log_action(self.user, f"{KIND_TEXT[self.kind_box.currentData()]} из кассы",
                           f"{money(self.amount_box.value())}: {self.reason_edit.text().strip()}")
        super().accept()


CABINET_HEADERS = ["Смена", "Сотрудник", "Открыта", "Сдана", "Статус",
                   "Приход", "Цветы", "Списано", "Терминал", "QR-код", "Наличные", "Доставки",
                   "Приняли", "При приёме", "Курьерам", "Расходы", "Изъятия",
                   "Должно быть", "Сдали", "Касса", "Вода", "Обязанности", "Санкции", "Комментарий"]
(C_DAY, C_STATUS, C_WRITEOFF, C_HANDOVER, C_CASH, C_WATER, C_DUTIES, C_PENALTY, C_NOTE) = (0, 4, 7, 13, 19, 20, 21, 22, 23)
MONEY_COLS = {5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18}
WATER_TEXT = {"done": ("поменяли", "green"), "missed": ("не поменяли", "red"), "planned": ("надо поменять", "orange")}


class CabinetDialog(QDialog):
    """Личный кабинет: смены и итоги за месяц. Админ может смотреть любого сотрудника или всех."""

    def __init__(self, parent, db: Database, user: User, change: time, grace: int, year: int, month: int,
                 water: WaterSchedule | None = None,
                 rules: Callable[[int], SalaryRules] | None = None,
                 kinds: Callable[[], list[PenaltyKind]] | None = None,
                 on_dispute: Callable[[Penalty], bool] | None = None,
                 on_penalty: Callable[[Penalty, str], None] | None = None):
        super().__init__(parent)
        self.db, self.user, self.change, self.grace, self.water = db, user, change, grace, water
        self.on_dispute = on_dispute  # сообщить администраторам, что сотрудник оспорил санкцию
        self.on_penalty = on_penalty  # админ поставил / снял санкцию в программе — сообщить второму администратору
        self.rules = rules  # правила зарплаты для сотрудника (у кого-то могут быть свои ступени)
        self.kinds = kinds  # список штрафных санкций (из настроек)
        self.month_shifts: list[Shift] = []
        self.year, self.month = year, month
        self.shifts: list[Shift] = []
        self.setWindowTitle("Личный кабинет")
        self.resize(1500, 800)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:  # на ноутбуке 1366×768 — не больше экрана
            area = screen.availableGeometry()
            self.resize(min(1500, area.width() - 20), min(800, area.height() - 60))
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.person_box = QComboBox()
        if user.is_admin:
            self.person_box.addItem("Все сотрудники", None)
            for u in db.users():
                self.person_box.addItem(u.name or u.login, u.id)
        else:
            self.person_box.addItem(user.name or user.login, user.id)
            self.person_box.setVisible(False)  # свой кабинет: имя надписью, а не серым выключенным списком
        self.person_box.currentIndexChanged.connect(self.refresh)
        top.addWidget(QLabel("Сотрудник:" if user.is_admin else f"Сотрудник: <b>{_html(user.name or user.login)}</b>"))
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

        self.salary_panel = SalaryPanel()
        self.adjust_btn = QPushButton("± Аванс / премия / доплата…")
        self.adjust_btn.clicked.connect(self._adjust)
        self.penalty_btn = QPushButton("⚠ Санкции смены…")
        self.penalty_btn.setToolTip("Поставить штрафную санкцию за выбранную смену или убрать")
        self.penalty_btn.clicked.connect(self._penalties)
        self.dispute_btn = QPushButton("✋ Оспорить санкцию…")
        self.dispute_btn.setToolTip("Не согласны с санкцией — напишите почему, решит администратор")
        self.dispute_btn.clicked.connect(self._dispute)
        for b in (self.adjust_btn, self.penalty_btn, self.dispute_btn):
            self.salary_panel.buttons.addWidget(b)
        self.salary_panel.buttons.addStretch(1)
        self.salary_panel.setVisible(rules is not None)
        layout.addWidget(self.salary_panel)

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
        first = date(self.year, self.month, 1)
        last = date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])
        self.shifts = self.db.shifts_between(first, last, self.person_box.currentData())
        waters = self.db.water_changes_between(first, last)
        now = datetime.now()
        month_penalties = self.db.penalties_between(first, last, self.person_box.currentData())
        shift_penalties: dict[int, list] = {}
        for p in month_penalties:
            shift_penalties.setdefault(p.shift_id, []).append(p)
        self.month_shifts = self.db.shifts_between(first, last)  # весь магазин: списания считаются по всем
        totals = dict.fromkeys(("revenue", "flowers", "terminal", "qr", "cash", "writeoff"), 0)
        stats = {"short": 0, "extra": 0, "late": 0, "not_closed": 0, "handover": 0,
                 "water_days": 0, "water_done": 0}

        self.table.setRowCount(len(self.shifts))
        rules_for = (lambda s: self.rules(s.user_id)) if self.rules else None
        for row, s in enumerate(self.shifts):
            status = s.status(now, self.change, self.grace)
            diff = s.difference()
            handover = self.db.handover_difference(s)
            closed = s.closed_at is not None
            if closed:
                totals["revenue"] += s.revenue
                totals["flowers"] += s.flowers
                totals["writeoff"] += s.writeoff
                totals["terminal"] += s.terminal
                totals["qr"] += s.qr
                totals["cash"] += s.cash_sales
            if diff:
                stats["short" if diff < 0 else "extra"] += abs(diff)
            if status in ("late", "not_closed"):
                stats[status] += 1
            if handover:
                stats["handover"] += 1
            water = self.water.status(s.day, s.day in waters, now, self.change) if self.water else None
            if water is not None:
                stats["water_days"] += 1
                stats["water_done"] += water == "done"

            def m(value: int) -> str:
                return money(value) if closed else ""

            start, end = window(s.day, self.change)
            kind = rules_for(s).shift_kind(s.day) if self.rules else "normal"
            cells = [
                f"{start:%d.%m} → {end:%d.%m}" + (" 🎉" if kind == "holiday" else ""), s.user_name or "—", fmt_dt(s.opened_at),
                fmt_dt(s.closed_at), STATUS_TEXT[status],
                m(s.revenue), m(s.flowers), m(s.writeoff), m(s.terminal), m(s.qr), m(s.cash_sales), m(s.deliveries),
                money(s.opening_cash), diff_text(handover) if handover is not None else "",
                m(s.courier_cash), money(s.expenses), money(s.withdrawals),
                m(s.expected_cash()), m(s.closing_cash or 0), diff_text(diff),
                WATER_TEXT[water][0] if water else "",
                f"{s.duties_done} из {s.duties_total}" if s.duties_total else "",
                ", ".join(f"{p.title} −{money(p.amount)}" + _dispute_mark(p) for p in shift_penalties.get(s.id, [])),
                " ".join(s.note.split()),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in MONEY_COLS:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == C_STATUS and status in PROBLEM_STATUSES:
                    item.setForeground(QColor(C.orange if status == "closing" else C.red))
                if col == C_HANDOVER and handover:
                    item.setForeground(QColor(C.red))
                if col == C_CASH:
                    item.setForeground(QColor(diff_color(diff)))
                if col == C_WATER and water:
                    item.setForeground(QColor(getattr(C, WATER_TEXT[water][1])))
                    if s.day in waters:
                        who, when = waters[s.day]
                        item.setToolTip(f"Отметил(а) {who or '—'} {when:%d.%m %H:%M}")
                if col == C_DUTIES and s.duties_total:
                    missed = [i.text for i in self.db.checklist(s) if not i.done]
                    if closed:
                        item.setForeground(QColor(C.red if missed else C.green))
                    if missed:
                        item.setToolTip("Не сделано: " + ", ".join(missed))
                if col == C_PENALTY and shift_penalties.get(s.id):
                    item.setForeground(QColor(C.red))
                    item.setToolTip("\n".join(f"{p.title}: −{money(p.amount)} ({p.created_by or '—'}, "
                                               f"{p.created_at:%d.%m %H:%M})" + _dispute_note(p)
                                               for p in shift_penalties[s.id]))
                if col == C_DAY and kind == "holiday":
                    item.setToolTip("Праздничная смена: премия за выручку не начисляется")
                if col == C_WRITEOFF and s.writeoff:
                    reasons = self.db.writeoff_items_for_shift(s.id)
                    if reasons:
                        item.setToolTip("\n".join(f"{r}: {money(a)}" for r, a in reasons.items()))
                if col == C_NOTE and s.note:
                    item.setToolTip(s.note)
                self.table.setItem(row, col, item)

        parts = [f"Смен: <b>{len(self.shifts)}</b>",
                 f"Приход: <b>{money(totals['revenue'])}</b>",
                 f"Цветы: <b>{money(totals['flowers'])}</b>",
                 f"Списано: <b>{money(totals['writeoff'])}</b>",
                 f"Терминал: <b>{money(totals['terminal'])}</b>",
                 f"QR-код: <b>{money(totals['qr'])}</b>",
                 f"Наличные: <b>{money(totals['cash'])}</b>"]
        problems = []
        if stats["short"]:
            problems.append(f"<span style='color:{C.red}'>Недостача: <b>{money(stats['short'])}</b></span>")
        if stats["extra"]:
            problems.append(f"Излишек: <b>{money(stats['extra'])}</b>")
        if stats["handover"]:
            problems.append(f"<span style='color:{C.red}'>Не сошлось при приёме: <b>{stats['handover']}</b></span>")
        if stats["late"]:
            problems.append(f"<span style='color:{C.red}'>Сдано с опозданием: <b>{stats['late']}</b></span>")
        if stats["not_closed"]:
            problems.append(f"<span style='color:{C.red}'>Не сдано: <b>{stats['not_closed']}</b></span>")
        if stats["water_days"]:
            color = C.green if stats["water_done"] == stats["water_days"] else C.red
            problems.append(f"<span style='color:{color}'>💧 Вода: поменяли <b>{stats['water_done']}</b> "
                            f"из {stats['water_days']}</span>")
        text = " &nbsp;·&nbsp; ".join(parts)
        if problems:
            text += "<br>" + " &nbsp;·&nbsp; ".join(problems)
        self.summary.setText(text)
        self._refresh_salary()
        self._show_ops()

    def month_key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def _refresh_salary(self):
        if self.rules is None:
            return
        person = self.person_box.currentData()
        self.adjust_btn.setVisible(self.user.is_admin and person is not None)
        self.penalty_btn.setVisible(self.user.is_admin and self.kinds is not None)
        self.dispute_btn.setVisible(not self.user.is_admin)
        name = MONTHS[self.month - 1]
        reasons = self.db.writeoff_by_reason(*self._month_range())
        if person is None:  # все сотрудники — таблица по людям
            rows = [(u.name or u.login, self._salary(u.id, own)) for u in self.db.users()
                    if (own := [s for s in self.shifts if s.user_id == u.id])]
            self.salary_panel.show_all(rows, name, monthly_ranking(self.month_shifts, self.rules(None)), reasons)
            return
        rules = self.rules(person)
        sal = self._salary(person, self.shifts)
        today = date.today()
        plan = forecast(sal, rules, today) if (self.year, self.month) == (today.year, today.month) else None
        self.salary_panel.show_person(sal, rules, name, plan, monthly_ranking(self.month_shifts, rules), reasons)

    def _salary(self, user_id: int, shifts: list[Shift]):
        """Зарплата сотрудника за месяц: санкции — его, списания — по всему магазину."""
        rules = self.rules(user_id)
        penalties = self.db.penalties_between(*self._month_range(), user_id)
        return calculate(shifts, self.db.adjustments(user_id, self.month_key()), rules,
                         penalties, shop_writeoff(self.month_shifts))

    def _month_range(self) -> tuple[date, date]:
        last = calendar.monthrange(self.year, self.month)[1]
        return date(self.year, self.month, 1), date(self.year, self.month, last)

    def _dispute(self):
        row = self.table.currentRow()
        shift = self.shifts[row] if 0 <= row < len(self.shifts) and self.table.selectionModel().hasSelection() else None
        items = [p for p in self.db.penalties_between(*self._month_range(), self.user.id)
                 if shift is None or p.shift_id == shift.id]
        if not items:
            QMessageBox.information(self, "Санкции", "Санкций нет (если нужна конкретная смена — выберите её в таблице).")
            return
        dlg = DisputeDialog(self, self.db, items)
        if dlg.exec() == QDialog.Accepted and dlg.result_penalty is not None:
            in_telegram = bool(self.on_dispute and self.on_dispute(dlg.result_penalty))
            QMessageBox.information(
                self, "Отправлено",
                "Администратор получил ваше объяснение в Telegram и решит." if in_telegram
                else "Администратор увидит ваше объяснение в программе и решит.")
            self.refresh()

    def _adjust(self):
        person = self.person_box.currentData()
        if person is not None:
            AdjustmentDialog(self, self.db, person, self.person_box.currentText(), self.month_key(),
                             self.user).exec()
            self._refresh_salary()

    def _penalties(self):
        row = self.table.currentRow()
        if not (0 <= row < len(self.shifts) and self.table.selectionModel().hasSelection()):
            QMessageBox.information(self, "Санкции", "Выберите смену в таблице.")
            return
        PenaltyDialog(self, self.db, self.shifts[row], self.kinds(), self.user, self.on_penalty).exec()
        self.refresh()

    def _show_ops(self):
        row = self.table.currentRow()
        has = 0 <= row < len(self.shifts) and self.table.selectionModel().hasSelection()
        shift = self.shifts[row] if has else None
        self.ops_title.setText(
            f"<b>Расходы и изъятия смены {shift.day:%d.%m}</b>"
            + (f" · комментарий: {_html(shift.note)}" if shift.note else "")
            if shift else "<b>Расходы и изъятия</b> — выберите смену в таблице")
        _fill_ops(self.ops, self.db, shift.id if shift else None)


class AdjustmentDialog(QDialog):
    """Аванс, премия, штраф — доплаты и удержания к зарплате за месяц (только админ)."""

    # Штрафа здесь нет: штраф — это санкция («⚠ Санкции смены…»), он считается в лимит для премии.
    KINDS = (("Аванс (выдали заранее)", -1, "аванс"), ("Премия", 1, "премия"),
             ("Доплата", 1, "доплата"), ("Удержание (не штраф)", -1, "удержание"))

    def __init__(self, parent, db: Database, user_id: int, name: str, month: str, admin: User):
        super().__init__(parent)
        self.db, self.user_id, self.month, self.admin = db, user_id, month, admin
        self.items = []
        self.setWindowTitle(f"Зарплата: {name}, {month[5:]}.{month[:4]}")
        self.resize(560, 420)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Дата", "За что", "Сумма", "Добавил"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        delete = QPushButton("🗑 Удалить выбранное")
        delete.clicked.connect(self._delete)
        layout.addWidget(delete)

        form = QFormLayout()
        self.kind_box = QComboBox()
        for title, _, _ in self.KINDS:
            self.kind_box.addItem(title)
        self.kind_box.currentIndexChanged.connect(self._kind_changed)
        form.addRow("Что:", self.kind_box)
        self.amount_box = money_box()
        form.addRow("Сумма:", self.amount_box)
        self.reason_edit = QLineEdit(self.KINDS[0][2])
        form.addRow("Комментарий:", self.reason_edit)
        layout.addLayout(form)
        add = QPushButton("➕ Добавить")
        add.clicked.connect(self._add)
        layout.addWidget(add)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Готово")
        close.rejected.connect(self.accept)
        layout.addWidget(close)
        self.refresh()

    def _kind_changed(self, index: int):
        self.reason_edit.setText(self.KINDS[index][2])

    def refresh(self):
        self.items = self.db.adjustments(self.user_id, self.month)
        self.table.setRowCount(len(self.items))
        for row, a in enumerate(self.items):
            sign = "+" if a.amount > 0 else "−"
            cells = [f"{a.created_at:%d.%m.%Y}", a.reason, f"{sign}{money(abs(a.amount))}", a.created_by_name]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setForeground(QColor(C.green if a.amount > 0 else C.red))
                self.table.setItem(row, col, item)

    def _add(self):
        sign = self.KINDS[self.kind_box.currentIndex()][1]
        try:
            self.db.add_adjustment(self.user_id, self.month, sign * self.amount_box.value(),
                                   self.reason_edit.text(), self.admin.id, datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Зарплата", str(e))
            return
        self.db.log_action(self.admin, f"{self.KINDS[self.kind_box.currentIndex()][0].split(' (')[0]} сотруднику",
                           f"{self.windowTitle().split(': ', 1)[-1]}: {'+' if sign > 0 else '−'}"
                           f"{money(self.amount_box.value())}, {self.reason_edit.text().strip()}")
        self.amount_box.setValue(0)
        self.refresh()

    def _delete(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.items) and self.table.selectionModel().hasSelection():
            if QMessageBox.question(self, "Удалить", f"Удалить «{self.items[row].reason}»?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                adj = self.items[row]
                self.db.delete_adjustment(adj.id)
                self.db.log_action(self.admin, "Доплата / удержание удалены",
                                   f"{self.windowTitle().split(': ', 1)[-1]}: {adj.reason}, {money(adj.amount)}")
                self.refresh()


class PenaltyDialog(QDialog):
    """Штрафные санкции выбранной смены: поставить из списка или убрать (только админ)."""

    def __init__(self, parent, db: Database, shift: Shift, kinds: list[PenaltyKind], admin: User,
                 on_change: Callable[[Penalty, str], None] | None = None):
        super().__init__(parent)
        self.db, self.shift, self.kinds, self.admin = db, shift, kinds, admin
        self.on_change = on_change  # (санкция, "set" / "removed" / "kept") — сообщить второму администратору
        self.items = []
        self.setWindowTitle(f"Санкции: {shift.user_name or '—'}, смена {shift.day:%d.%m}")
        self.resize(720, 400)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Санкция", "Сумма", "Кто поставил", "Когда", "Спор"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setWordWrap(True)  # объяснение сотрудника — целиком, а не «…»
        layout.addWidget(self.table, 1)
        row_buttons = QHBoxLayout()
        remove = QPushButton("🗑 Убрать выбранную")
        remove.setToolTip("Снять санкцию (в том числе если сотрудник оспорил её и он прав)")
        remove.clicked.connect(self._remove)
        keep = QPushButton("✔ Оставить после спора")
        keep.setToolTip("Сотрудник оспорил санкцию, но она остаётся — спор закрыт")
        keep.clicked.connect(self._keep)
        row_buttons.addWidget(remove)
        row_buttons.addWidget(keep)
        layout.addLayout(row_buttons)

        form = QFormLayout()
        self.kind_box = QComboBox()
        for kind in kinds:
            self.kind_box.addItem(f"{kind.title} — {money(kind.amount)}", kind)
        self.kind_box.addItem("Другое — своя сумма и причина (штраф)", None)
        self.kind_box.currentIndexChanged.connect(self._kind_changed)
        form.addRow("Поставить:", self.kind_box)
        self.other_reason = QLineEdit()
        self.other_reason.setPlaceholderText("за что, например «разбила вазу»")
        self.other_amount = money_box()
        self.other_row = QWidget()
        other = QHBoxLayout(self.other_row)
        other.setContentsMargins(0, 0, 0, 0)
        other.addWidget(self.other_reason, 1)
        other.addWidget(self.other_amount)
        form.addRow("", self.other_row)
        layout.addLayout(form)
        self._kind_changed()
        add = QPushButton("⚠ Поставить санкцию")
        add.clicked.connect(self._add)
        layout.addWidget(add)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Готово")
        close.rejected.connect(self.accept)
        layout.addWidget(close)
        self.refresh()

    def refresh(self):
        self.items = self.db.penalties_for_shift(self.shift.id)
        self.table.setRowCount(len(self.items))
        for row, p in enumerate(self.items):
            cells = (p.title, f"−{money(p.amount)}", p.created_by or "—", f"{p.created_at:%d.%m %H:%M}",
                     _dispute_note(p).lstrip(" ·"))
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(QColor(C.red))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 4 and p.disputed_at and not p.dispute_result:
                    item.setForeground(QColor(C.orange))
                    item.setToolTip(p.dispute_text)
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()

    def _kind_changed(self, *_):
        self.other_row.setVisible(self.kind_box.currentData() is None)

    def _add(self):
        kind = self.kind_box.currentData()
        if kind is None:  # своя санкция: штраф с суммой и причиной
            reason = self.other_reason.text().strip()
            if not reason or self.other_amount.value() <= 0:
                QMessageBox.information(self, "Санкции", "Напишите, за что, и сумму.")
                return
            kind = PenaltyKind(0, reason[:1].upper() + reason[1:], self.other_amount.value())
        who = self.admin.name or self.admin.login
        penalty = self.db.add_penalty(self.shift.id, kind.id, kind.title, kind.amount, who, datetime.now())
        if penalty is None:
            QMessageBox.information(self, "Санкции", "Такая санкция за эту смену уже поставлена.")
            return
        self.db.log_action(self.admin, "Штрафная санкция поставлена",
                           f"{self.shift.user_name or '—'}, смена {self.shift.day:%d.%m}: {kind.title}, −{money(kind.amount)}")
        if self.on_change:
            self.on_change(penalty, "set")
        self.refresh()

    def _selected(self) -> Penalty | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.items) and self.table.selectionModel().hasSelection():
            return self.items[row]
        QMessageBox.information(self, "Санкции", "Выберите санкцию в списке.")
        return None

    def _remove(self):
        selected = self._selected()
        if selected is not None:
            p = self.db.delete_penalty(selected.id)
            if p is not None:
                self.db.log_action(self.admin, "Штрафная санкция снята",
                                   f"{p.user_name or '—'}, смена {p.shift_day:%d.%m}: {p.title}, −{money(p.amount)}")
                if self.on_change:
                    self.on_change(p, "removed")
            self.refresh()

    def _keep(self):
        p = self._selected()
        if p is None:
            return
        if p.disputed_at is None or p.dispute_result:
            QMessageBox.information(self, "Санкции", "Эту санкцию не оспаривали (или спор уже решён).")
            return
        self.db.resolve_dispute(p.id)
        self.db.log_action(self.admin, "Спор по санкции: оставлена",
                           f"{p.user_name or '—'}, смена {p.shift_day:%d.%m}: {p.title}")
        if self.on_change:
            self.on_change(p, "kept")
        self.refresh()


def _dispute_note(p: Penalty) -> str:
    """Что со спором: для подсказок и списка санкций у администратора."""
    if p.disputed_at is None:
        return ""
    if p.dispute_result:
        return " · спор решён: санкция оставлена"
    return f" · ✋ оспорено: «{p.dispute_text}»"


def _dispute_mark(p: Penalty) -> str:
    if p.disputed_at is None:
        return ""
    return " (оставлена после спора)" if p.dispute_result else " ✋ оспорена"


class DisputeDialog(QDialog):
    """Сотрудник не согласен с санкцией: выбирает её и пишет почему."""

    def __init__(self, parent, db: Database, penalties: list[Penalty]):
        super().__init__(parent)
        self.db = db
        self.result_penalty: Penalty | None = None
        self.setWindowTitle("Оспорить санкцию")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.box = QComboBox()
        for p in penalties:
            mark = " — оспорено" if p.disputed_at else ""
            self.box.addItem(f"{p.shift_day:%d.%m} · {p.title} −{money(p.amount)}{mark}", p)
        form.addRow("Санкция:", self.box)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Почему вы не согласны")
        self.text.setFixedHeight(90)
        form.addRow("Объяснение:", self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Отправить администратору")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self):
        penalty = self.box.currentData()
        try:
            self.result_penalty = self.db.dispute_penalty(penalty.id, self.text.toPlainText(), datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Санкция", str(e))
            return
        super().accept()
