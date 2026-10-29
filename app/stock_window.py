"""Окно «🛒 Продажи и склад»: касса (продажа), остатки, поставки, техкарты букетов, аналитика.

Чек сотрудник пробивает на кассе АТОЛ сам — здесь продажа нужна для склада, выручки смены и отчётов.
"""

from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .db import UserError
from .delivery_dialog import money, money_box
from .icon import flower_icon
from .shifts import WRITEOFF_REASONS
from .stock import CATEGORIES, PAYMENTS, CartLine, Product, Recipe, day_range, qty_text
from .theme import C

MOVE_KINDS = {"supply": "Поставка", "sale": "Продажа", "writeoff": "Списание", "count": "Инвентаризация",
              "return": "Возврат"}
PERIODS = ("Сегодня", "Вчера", "7 дней", "30 дней", "Этот месяц", "Прошлый месяц")
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]


def period_range(name: str, today: date) -> tuple[date, date]:
    """Первый и последний день периода."""
    if name == "Вчера":
        return today - timedelta(days=1), today - timedelta(days=1)
    if name == "7 дней":
        return today - timedelta(days=6), today
    if name == "30 дней":
        return today - timedelta(days=29), today
    if name == "Этот месяц":
        return today.replace(day=1), today
    if name == "Прошлый месяц":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    return today, today


def previous_range(first: date, last: date) -> tuple[date, date]:
    """Такой же по длине период прямо перед этим — для сравнения."""
    days = (last - first).days + 1
    return first - timedelta(days=days), first - timedelta(days=1)


def delta_text(now: float, before: float, money_like: bool = True) -> tuple[str, str]:
    """(«▲ 12 %», цвет) — изменение к прошлому периоду."""
    if not before:
        return "в прошлом периоде — 0", C.gray
    change = 100 * (now - before) / abs(before)
    if abs(change) < 0.5:
        return "как в прошлом периоде", C.gray
    good = change > 0 if money_like else change < 0
    return f"{'▲' if change > 0 else '▼'} {abs(change):.0f} % к прошлому периоду", C.green if good else C.red


def _item(text: str, data=None, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        item.setData(Qt.UserRole, data)
    if align_right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item


def _table(headers: list[str], stretch: int = 0) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    for col in range(len(headers)):
        header.setSectionResizeMode(col, QHeaderView.Stretch if col == stretch else QHeaderView.ResizeToContents)
    return table


def _gray(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {C.gray}")
    return label


def qty_box(unit: str = "") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 100_000)
    box.setDecimals(2)
    box.setValue(1)
    if unit:
        box.setSuffix(f" {unit}")
    return box


class BarChart(QWidget):
    """Простая столбчатая диаграмма выручки по дням."""

    def __init__(self):
        super().__init__()
        self.values: list[tuple[str, int]] = []
        self.setMinimumHeight(170)

    def set_values(self, values: list[tuple[str, int]]):
        self.values = values
        self.update()

    def paintEvent(self, event):  # noqa: N802 — имя из Qt
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        top, bottom = 18, 22
        if not self.values or not max(v for _, v in self.values):
            p.setPen(QColor(C.gray))
            p.drawText(self.rect(), Qt.AlignCenter, "Продаж за период нет")
            return
        peak = max(v for _, v in self.values)
        n = len(self.values)
        slot = w / n
        bar = max(min(slot * 0.7, 48), 2)
        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 1.5, 7))
        p.setFont(small)
        label_every = max(1, round(n / max(w // 46, 1)))
        for i, (label, value) in enumerate(self.values):
            height = (h - top - bottom) * value / peak
            x = i * slot + (slot - bar) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C.green))
            p.drawRoundedRect(QRectF(x, h - bottom - height, bar, height), 3, 3)
            p.setPen(QColor(C.gray))
            if i % label_every == 0:
                p.drawText(QRectF(i * slot - 10, h - bottom + 3, slot + 20, bottom), Qt.AlignHCenter, label)
            if value == peak or n <= 10:
                p.drawText(QRectF(i * slot - 20, h - bottom - height - top, slot + 40, top),
                           Qt.AlignHCenter | Qt.AlignBottom, f"{value / 1000:.1f}k" if value >= 10_000 else str(value))


class Card(QFrame):
    """Плашка с цифрой (KPI) и изменением к прошлому периоду."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.title = QLabel(title)
        self.value = QLabel()
        font = self.value.font()
        font.setPointSizeF(font.pointSizeF() + 6)
        font.setBold(True)
        self.value.setFont(font)
        self.delta = QLabel()
        for widget in (self.title, self.value, self.delta):
            layout.addWidget(widget)

    def set(self, value: str, delta: tuple[str, str]):
        self.setStyleSheet(f"#card {{ background: {C.card_bg}; border: 1px solid {C.card_border}; border-radius: 8px; }}")
        self.title.setStyleSheet(f"color: {C.gray}")
        self.value.setText(value)
        self.delta.setText(f"<span style='color:{delta[1]}'>{delta[0]}</span>")


# ----- диалоги -----

class ProductDialog(QDialog):
    def __init__(self, parent, product: Product):
        super().__init__(parent)
        self.product = product
        self.setWindowTitle("Новый товар" if not product.id else "Товар")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(product.name)
        self.name_edit.setPlaceholderText("Роза Эквадор 60 см красная")
        form.addRow("Название:", self.name_edit)
        self.category_box = QComboBox()
        self.category_box.addItems(CATEGORIES)
        self.category_box.setEditable(True)
        self.category_box.setCurrentText(product.category)
        form.addRow("Раздел:", self.category_box)
        self.unit_edit = QLineEdit(product.unit)
        self.unit_edit.setPlaceholderText("шт, ветка, лист, м")
        form.addRow("Единица:", self.unit_edit)
        self.price_box = money_box()
        self.price_box.setSingleStep(10)
        self.price_box.setValue(product.price)
        form.addRow("Цена продажи:", self.price_box)
        self.min_box = qty_box()
        self.min_box.setValue(product.min_stock)
        form.addRow("Мало, если остаток ≤:", self.min_box)
        form.addRow(_gray("Себестоимость считается сама — по ценам из поставок. 0 в «мало» — не следить."))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self):
        p = self.product
        p.name, p.category = self.name_edit.text(), self.category_box.currentText().strip() or "Цветы"
        p.unit, p.price, p.min_stock = self.unit_edit.text(), self.price_box.value(), self.min_box.value()
        try:
            self.parent().db.save_product(p)
        except UserError as e:
            QMessageBox.warning(self, "Товар", str(e))
            return
        super().accept()


class LinesEditor(QWidget):
    """Таблица «товар — сколько [— цена]» для поставки и техкарты."""

    def __init__(self, products: list[Product], with_cost: bool):
        super().__init__()
        self.products, self.with_cost = products, with_cost
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        add = QHBoxLayout()
        self.pick = QComboBox()
        self.pick.setEditable(True)
        self.pick.setInsertPolicy(QComboBox.NoInsert)
        for p in products:
            self.pick.addItem(f"{p.name} · {p.category}", p.id)
        self.pick.setCurrentIndex(-1)
        self.pick.lineEdit().setPlaceholderText("Начните писать название…")
        add.addWidget(self.pick, 1)
        add_btn = QPushButton("＋ Добавить")
        add_btn.clicked.connect(self._add_picked)
        self.pick.lineEdit().returnPressed.connect(self._add_picked)
        add.addWidget(add_btn)
        layout.addLayout(add)
        headers = ["Товар", "Сколько"] + (["Цена закупки, ₽ за ед.", "Сумма"] if with_cost else []) + [""]
        self.table = _table(headers)
        layout.addWidget(self.table, 1)
        self.total = QLabel()
        if with_cost:
            layout.addWidget(self.total)

    def _product(self, pid: int) -> Product | None:
        return next((p for p in self.products if p.id == pid), None)

    def _add_picked(self):
        pid = self.pick.currentData()
        if pid is None:
            text = self.pick.currentText().strip().lower()
            pid = next((p.id for p in self.products if text and text in p.name.lower()), None)
        if pid is not None:
            self.add(pid, 1, self._product(pid).avg_cost)
            self.pick.setCurrentIndex(-1)
            self.pick.clearEditText()

    def add(self, pid: int, qty: float, cost: float = 0):
        p = self._product(pid)
        if p is None:
            return
        for row in range(self.table.rowCount()):  # такой товар уже есть — прибавить
            if self.table.item(row, 0).data(Qt.UserRole) == pid:
                box = self.table.cellWidget(row, 1)
                box.setValue(box.value() + qty)
                return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, _item(p.name, pid))
        box = qty_box(p.unit)
        box.setValue(qty)
        box.valueChanged.connect(self._update)
        self.table.setCellWidget(row, 1, box)
        col = 2
        if self.with_cost:
            cost_box = QDoubleSpinBox()
            cost_box.setRange(0, 1_000_000)
            cost_box.setDecimals(2)
            cost_box.setSuffix(" ₽")
            cost_box.setValue(round(cost, 2))
            cost_box.valueChanged.connect(self._update)
            self.table.setCellWidget(row, 2, cost_box)
            self.table.setItem(row, 3, _item("", align_right=True))
            col = 4
        remove = QPushButton("✕")
        remove.setToolTip("Убрать строку")
        remove.clicked.connect(lambda: self._remove(remove))
        self.table.setCellWidget(row, col, remove)
        self._update()
        box.setFocus()
        box.selectAll()

    def _remove(self, button):
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, self.table.columnCount() - 1) is button:
                self.table.removeRow(row)
                break
        self._update()

    def lines(self) -> list[tuple]:
        result = []
        for row in range(self.table.rowCount()):
            pid = self.table.item(row, 0).data(Qt.UserRole)
            qty = self.table.cellWidget(row, 1).value()
            result.append((pid, qty, self.table.cellWidget(row, 2).value()) if self.with_cost else (pid, qty))
        return result

    def _update(self):
        if not self.with_cost:
            return
        total = 0.0
        for row in range(self.table.rowCount()):
            amount = self.table.cellWidget(row, 1).value() * self.table.cellWidget(row, 2).value()
            total += amount
            self.table.item(row, 3).setText(money(round(amount)))
        self.total.setText(f"<b>Итого по поставке: {money(round(total))}</b>")


class SupplyDialog(QDialog):
    def __init__(self, parent, db, user_id: int | None):
        super().__init__(parent)
        self.db, self.user_id = db, user_id
        self.setWindowTitle("Новая поставка")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.supplier_box = QComboBox()
        self.supplier_box.setEditable(True)
        self.supplier_box.addItems(db.suppliers())
        self.supplier_box.setCurrentText("")
        self.supplier_box.lineEdit().setPlaceholderText("Кто привёз (база, оптовик)")
        form.addRow("Поставщик:", self.supplier_box)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Номер накладной, что не довезли…")
        form.addRow("Заметка:", self.note_edit)
        layout.addLayout(form)
        self.lines = LinesEditor(db.products(), with_cost=True)
        layout.addWidget(self.lines, 1)
        layout.addWidget(_gray("Нет товара в списке — сначала добавьте его в «📦 Остатки» (администратор). "
                               "Цена закупки — за одну штуку / ветку; себестоимость пересчитается сама."))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Принять поставку")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            self.supply_id = self.db.add_supply(self.supplier_box.currentText(), self.lines.lines(), self.user_id,
                                                datetime.now(), self.note_edit.text())
        except UserError as e:
            QMessageBox.warning(self, "Поставка", str(e))
            return
        super().accept()


class RecipeDialog(QDialog):
    def __init__(self, parent, db, recipe: Recipe):
        super().__init__(parent)
        self.db, self.recipe = db, recipe
        self.setWindowTitle("Новая техкарта" if not recipe.id else "Техкарта")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(recipe.title)
        self.title_edit.setPlaceholderText("Букет «Нежность» (15 роз)")
        form.addRow("Название:", self.title_edit)
        self.price_box = money_box()
        self.price_box.setValue(recipe.price)
        form.addRow("Цена продажи:", self.price_box)
        layout.addLayout(form)
        self.lines = LinesEditor(db.products(active_only=False), with_cost=False)
        for pid, qty in recipe.items:
            self.lines.add(pid, qty)
        layout.addWidget(self.lines, 1)
        self.cost_label = QLabel()
        layout.addWidget(self.cost_label)
        self.lines.table.model().rowsInserted.connect(self._update)
        self.lines.table.model().rowsRemoved.connect(self._update)
        self.price_box.valueChanged.connect(self._update)
        self.lines.table.cellChanged.connect(self._update)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update()
        self.startTimer(700)  # количество меняют в полях — пересчитывать себестоимость

    def timerEvent(self, event):  # noqa: N802
        self._update()

    def _update(self, *args):
        r = Recipe(0, "", self.price_box.value(), items=self.lines.lines())
        cost = self.db.recipe_cost(r)
        margin = 100 * (r.price - cost) / r.price if r.price else 0
        color = C.green if margin >= 50 else C.orange if margin >= 30 else C.red
        self.cost_label.setText(f"Себестоимость: <b>{money(round(cost))}</b> · наценка: "
                                f"<b style='color:{color}'>{money(round(r.price - cost))} ({margin:.0f} %)</b>")

    def accept(self):
        r = self.recipe
        r.title, r.price, r.items = self.title_edit.text(), self.price_box.value(), self.lines.lines()
        try:
            self.db.save_recipe(r)
        except UserError as e:
            QMessageBox.warning(self, "Техкарта", str(e))
            return
        super().accept()


class WriteOffDialog(QDialog):
    def __init__(self, parent, product: Product):
        super().__init__(parent)
        self.setWindowTitle("Списание")
        form = QFormLayout(self)
        form.addRow(QLabel(f"<b>{product.name}</b> · на складе {qty_text(product.stock)} {product.unit}"))
        self.qty_box = qty_box(product.unit)
        form.addRow("Сколько списать:", self.qty_box)
        self.reason_box = QComboBox()
        self.reason_box.addItems(WRITEOFF_REASONS)
        form.addRow("Причина:", self.reason_box)
        form.addRow(_gray("Сумма по себестоимости сама попадёт в «Списано» при сдаче смены."))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Списать")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


# ----- окно -----

class StockWindow(QDialog):
    def __init__(self, window):
        super().__init__(window, Qt.Window)
        self.main = window
        self.db = window.db
        self.setWindowTitle("Продажи и склад")
        self.setWindowIcon(flower_icon())
        self.resize(1180, 720)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._sale_tab(), "🛒 Продажа")
        self.tabs.addTab(self._stock_tab(), "📦 Остатки")
        self.tabs.addTab(self._supplies_tab(), "🚚 Поставки")
        self.tabs.addTab(self._recipes_tab(), "📋 Техкарты")
        self.analytics_index = self.tabs.addTab(self._analytics_tab(), "📊 Аналитика")
        layout.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.tabs.setCurrentIndex(0),
                                                                     self.search_edit.setFocus()))
        self.refresh()

    @property
    def admin(self) -> bool:
        return bool(self.main.user and self.main.user.is_admin)

    @property
    def user_id(self) -> int | None:
        return self.main.user.id if self.main.user else None

    def refresh(self):
        self.tabs.setTabVisible(self.analytics_index, self.admin)
        for widget in (self.add_product_btn, self.edit_product_btn, self.count_btn, self.archive_btn,
                       self.add_recipe_btn, self.edit_recipe_btn, self.archive_recipe_btn):
            widget.setVisible(self.admin)
        page = self.tabs.currentIndex()
        if page == 0:
            self._refresh_catalog()
            self._refresh_today()
        elif page == 1:
            self._refresh_stock()
        elif page == 2:
            self._refresh_supplies()
        elif page == 3:
            self._refresh_recipes()
        elif page == self.analytics_index:
            self._refresh_analytics()

    # ----- продажа -----

    def _sale_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        left = QVBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Букет или товар (Ctrl+F)…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refresh_catalog)
        self.search_edit.returnPressed.connect(self._add_selected)
        left.addWidget(self.search_edit)
        self.catalog = _table(["Что", "Цена", "Остаток"])
        self.catalog.itemDoubleClicked.connect(lambda _: self._add_selected())
        left.addWidget(self.catalog, 1)
        row = QHBoxLayout()
        add_btn = QPushButton("➕ В чек")
        add_btn.clicked.connect(self._add_selected)
        free_btn = QPushButton("✏️ Своя позиция")
        free_btn.setToolTip("Букет не по техкарте, открытка, услуга — без списания со склада")
        free_btn.clicked.connect(self._add_free)
        row.addWidget(add_btn)
        row.addWidget(free_btn)
        left.addLayout(row)
        layout.addLayout(left, 4)

        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Чек</b>"))
        self.cart = _table(["Что", "Сколько", "Цена", "Сумма", ""])
        self.cart.setMinimumHeight(180)
        right.addWidget(self.cart, 2)
        totals = QFormLayout()
        self.discount_box = money_box()
        self.discount_box.setSingleStep(50)
        self.discount_box.valueChanged.connect(self._update_cart)
        totals.addRow("Скидка:", self.discount_box)
        self.total_label = QLabel()
        totals.addRow("К оплате:", self.total_label)
        right.addLayout(totals)
        pay = QHBoxLayout()
        self.pay_buttons = []
        for code, title in PAYMENTS.items():
            icon = {"cash": "💵", "card": "💳", "qr": "📱"}[code]
            button = QPushButton(f"{icon} {title}")
            button.setMinimumHeight(44)
            button.clicked.connect(lambda _=False, c=code: self._sell(c))
            pay.addWidget(button)
            self.pay_buttons.append(button)
        right.addLayout(pay)
        self.shift_hint = _gray("")
        right.addWidget(self.shift_hint)
        right.addWidget(_gray("🧾 Чек пробейте на кассе АТОЛ, как обычно. Здесь продажа нужна для склада и "
                              "выручки смены: при сдаче смены суммы подставятся сами."))
        right.addWidget(QLabel("<b>Продажи за сегодня</b>"))
        self.today_table = _table(["Время", "Что", "Сумма", "Оплата", "Кто"], stretch=1)
        right.addWidget(self.today_table, 3)
        bottom = QHBoxLayout()
        self.today_total = QLabel()
        bottom.addWidget(self.today_total, 1)
        cancel_btn = QPushButton("↩ Отменить продажу")
        cancel_btn.setToolTip("Возврат или ошиблись: товар вернётся на склад, сумма уйдёт из выручки")
        cancel_btn.clicked.connect(self._cancel_sale)
        bottom.addWidget(cancel_btn)
        right.addLayout(bottom)
        layout.addLayout(right, 5)
        self._update_cart()
        return page

    def _refresh_catalog(self):
        query = self.search_edit.text().strip().lower()
        rows = [("💐 " + r.title, r.price, "", ("recipe", r.id)) for r in self.db.recipes()
                if query in r.title.lower()]
        rows += [(p.name, p.price, f"{qty_text(p.stock)} {p.unit}", ("product", p.id), p.low)
                 for p in self.db.products() if query in p.name.lower() or query in p.category.lower()]
        self.catalog.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.catalog.setItem(i, 0, _item(row[0], row[3]))
            self.catalog.setItem(i, 1, _item(money(row[1]), align_right=True))
            stock = _item(row[2], align_right=True)
            if len(row) > 4 and row[4]:
                stock.setForeground(QColor(C.red))
            self.catalog.setItem(i, 2, stock)
        if rows:
            self.catalog.selectRow(0)

    def _add_selected(self):
        row = self.catalog.currentRow()
        if row < 0:
            return
        kind, ident = self.catalog.item(row, 0).data(Qt.UserRole)
        if kind == "recipe":
            r = self.db.get_recipe(ident)
            self._add_line(CartLine(r.title, 1, r.price, recipe_id=r.id))
        else:
            p = self.db.get_product(ident)
            self._add_line(CartLine(p.name, 1, p.price, product_id=p.id))
        self.search_edit.selectAll()
        self.search_edit.setFocus()

    def _add_free(self):
        title, ok = QInputDialog.getText(self, "Своя позиция", "Что продали:")
        if not ok or not title.strip():
            return
        price, ok = QInputDialog.getInt(self, "Своя позиция", f"Цена «{title.strip()}», ₽:", 0, 0, 10_000_000, 50)
        if ok:
            self._add_line(CartLine(title.strip(), 1, price))

    def _add_line(self, line: CartLine):
        for row in range(self.cart.rowCount()):
            same = self.cart.item(row, 0).data(Qt.UserRole)
            if (line.recipe_id or line.product_id) and same == (line.title, line.recipe_id, line.product_id):
                box = self.cart.cellWidget(row, 1)
                box.setValue(box.value() + 1)
                return
        row = self.cart.rowCount()
        self.cart.insertRow(row)
        self.cart.setItem(row, 0, _item(line.title, (line.title, line.recipe_id, line.product_id)))
        qty = qty_box()
        qty.setDecimals(0 if line.recipe_id else 2)
        qty.setMinimum(1 if line.recipe_id else 0.01)
        qty.setValue(line.qty)
        qty.valueChanged.connect(self._update_cart)
        self.cart.setCellWidget(row, 1, qty)
        price = money_box()
        price.setSingleStep(10)
        price.setValue(line.price)
        price.valueChanged.connect(self._update_cart)
        self.cart.setCellWidget(row, 2, price)
        self.cart.setItem(row, 3, _item("", align_right=True))
        remove = QPushButton("✕")
        remove.clicked.connect(lambda: self._remove_line(remove))
        self.cart.setCellWidget(row, 4, remove)
        self._update_cart()

    def _remove_line(self, button):
        for row in range(self.cart.rowCount()):
            if self.cart.cellWidget(row, 4) is button:
                self.cart.removeRow(row)
                break
        self._update_cart()

    def cart_lines(self) -> list[CartLine]:
        lines = []
        for row in range(self.cart.rowCount()):
            title, recipe_id, product_id = self.cart.item(row, 0).data(Qt.UserRole)
            lines.append(CartLine(title, self.cart.cellWidget(row, 1).value(), self.cart.cellWidget(row, 2).value(),
                                  recipe_id, product_id))
        return lines

    def _update_cart(self):
        lines = self.cart_lines()
        for row, line in enumerate(lines):
            self.cart.item(row, 3).setText(money(line.amount))
        gross = sum(line.amount for line in lines)
        total = gross - self.discount_box.value()
        self.total_label.setText(f"<span style='font-size:18pt; font-weight:bold; "
                                 f"color:{C.red if total < 0 else C.green}'>{money(total)}</span>")
        for button in getattr(self, "pay_buttons", []):
            button.setEnabled(bool(lines) and total >= 0)
        shift_id = self.main.sale_shift_id()
        if hasattr(self, "shift_hint"):
            self.shift_hint.setText("" if shift_id else "⚠ Смена не начата — продажа не попадёт в выручку смены.")
            self.shift_hint.setStyleSheet(f"color: {C.orange}")

    def _sell(self, payment: str):
        lines = self.cart_lines()
        try:
            sale_id = self.db.add_sale(lines, payment, self.user_id, self.main.sale_shift_id(), datetime.now(),
                                       discount=self.discount_box.value())
        except UserError as e:
            QMessageBox.warning(self, "Продажа", str(e))
            return
        total = sum(line.amount for line in lines) - self.discount_box.value()
        self.main.audit("Продажа", f"№{sale_id}: {money(total)}, {PAYMENTS[payment]} — "
                        + ", ".join(f"{x.title} × {qty_text(x.qty)}" for x in lines))
        self.cart.setRowCount(0)
        self.discount_box.setValue(0)
        self._update_cart()
        self._refresh_catalog()
        self._refresh_today()
        self.search_edit.setFocus()

    def _refresh_today(self):
        sales = self.db.sales_between(*day_range(date.today(), date.today()))
        self.today_table.setRowCount(len(sales))
        total = count = 0
        for i, s in enumerate(sales):
            cells = [f"{s.at:%H:%M}", s.what + (f" (скидка {money(s.discount)})" if s.discount else ""),
                     money(s.total), PAYMENTS.get(s.payment, s.payment), s.user_name or "бот"]
            for col, text in enumerate(cells):
                item = _item(text, s.id, align_right=col == 2)
                if s.cancelled_at:
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setForeground(QColor(C.gray))
                    item.setToolTip(f"Отменена {s.cancelled_at:%H:%M}: {s.cancel_reason or 'без причины'}")
                elif s.note:
                    item.setToolTip(s.note)
                self.today_table.setItem(i, col, item)
            if not s.cancelled_at:
                total += s.total
                count += 1
        self.today_total.setText(f"Сегодня: <b>{count}</b> продаж на <b>{money(total)}</b>")

    def _cancel_sale(self):
        row = self.today_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Отменить продажу", "Выберите продажу в списке «Продажи за сегодня».")
            return
        sale_id = self.today_table.item(row, 0).data(Qt.UserRole)
        reason, ok = QInputDialog.getText(self, "Отменить продажу",
                                          f"Продажа: {self.today_table.item(row, 1).text()} — "
                                          f"{self.today_table.item(row, 2).text()}\n\nПричина (возврат, ошиблись…):")
        if not ok:
            return
        try:
            self.db.cancel_sale(sale_id, reason, self.user_id, datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Отменить продажу", str(e))
            return
        self.main.audit("Продажа отменена", f"№{sale_id}: {reason.strip() or 'без причины'}")
        self._refresh_today()
        self._refresh_catalog()

    # ----- остатки -----

    def _stock_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.stock_search = QLineEdit()
        self.stock_search.setPlaceholderText("🔍 Поиск товара…")
        self.stock_search.setClearButtonEnabled(True)
        self.stock_search.textChanged.connect(self._refresh_stock)
        filters.addWidget(self.stock_search, 1)
        self.category_filter = QComboBox()
        self.category_filter.addItem("Все разделы", "")
        for c in CATEGORIES:
            self.category_filter.addItem(c, c)
        self.category_filter.currentIndexChanged.connect(self._refresh_stock)
        filters.addWidget(self.category_filter)
        self.low_only = QCheckBox("Только заканчивается")
        self.low_only.toggled.connect(self._refresh_stock)
        filters.addWidget(self.low_only)
        layout.addLayout(filters)
        self.stock_table = _table(["Товар", "Раздел", "Остаток", "Цена", "Себестоимость", "Мало, если ≤"])
        self.stock_table.itemDoubleClicked.connect(lambda _: self._edit_product() if self.admin else self._history())
        layout.addWidget(self.stock_table, 1)
        buttons = QHBoxLayout()
        self.add_product_btn = QPushButton("➕ Товар")
        self.add_product_btn.clicked.connect(lambda: self._edit_product(new=True))
        self.edit_product_btn = QPushButton("✏️ Изменить")
        self.edit_product_btn.clicked.connect(self._edit_product)
        writeoff_btn = QPushButton("🗑 Списать")
        writeoff_btn.setToolTip("Завяло, сломалось, брак — спишется со склада и попадёт в сдачу смены")
        writeoff_btn.clicked.connect(self._write_off)
        self.count_btn = QPushButton("🔢 Инвентаризация")
        self.count_btn.setToolTip("Пересчитали — ввести, сколько на самом деле")
        self.count_btn.clicked.connect(self._count)
        history_btn = QPushButton("📜 История")
        history_btn.clicked.connect(self._history)
        self.archive_btn = QPushButton("📁 В архив")
        self.archive_btn.setToolTip("Больше не возим — скрыть из списков (история останется)")
        self.archive_btn.clicked.connect(self._archive_product)
        for b in (self.add_product_btn, self.edit_product_btn, writeoff_btn, self.count_btn, history_btn,
                  self.archive_btn):
            b.setMinimumHeight(30)
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.stock_summary = QLabel()
        buttons.addWidget(self.stock_summary)
        layout.addLayout(buttons)
        return page

    def _refresh_stock(self):
        query = self.stock_search.text().strip().lower()
        category = self.category_filter.currentData()
        products = [p for p in self.db.products()
                    if query in p.name.lower() and (not category or p.category == category)
                    and (p.low or not self.low_only.isChecked())]
        selected = self._selected_product_id()
        self.stock_table.setRowCount(len(products))
        for i, p in enumerate(products):
            cells = [p.name, p.category, f"{qty_text(p.stock)} {p.unit}", money(p.price),
                     money(round(p.avg_cost)) if self.admin else "—",
                     f"{qty_text(p.min_stock)} {p.unit}" if p.min_stock else ""]
            for col, text in enumerate(cells):
                item = _item(text, p.id, align_right=col >= 2)
                if p.low and col == 2:
                    item.setForeground(QColor(C.red))
                    item.setToolTip("Отрицательный остаток — значит, поставку не внесли" if p.stock < 0
                                    else "Заканчивается — пора заказать")
                self.stock_table.setItem(i, col, item)
            if p.id == selected:
                self.stock_table.selectRow(i)
        low = sum(p.low for p in self.db.products())
        text = f"Заканчивается: <b style='color:{C.red if low else C.green}'>{low}</b>"
        if self.admin:
            text += f" · на складе на <b>{money(self.db.stock_value())}</b> (по себестоимости)"
        self.stock_summary.setText(text)

    def _selected_product_id(self) -> int | None:
        row = self.stock_table.currentRow()
        return self.stock_table.item(row, 0).data(Qt.UserRole) if row >= 0 and self.stock_table.item(row, 0) else None

    def _selected_product(self) -> Product | None:
        pid = self._selected_product_id()
        if pid is None:
            QMessageBox.information(self, "Остатки", "Выберите товар в списке.")
            return None
        return self.db.get_product(pid)

    def _edit_product(self, new: bool = False):
        p = Product(0, "") if new else self._selected_product()
        if p is None:
            return
        if ProductDialog(self, p).exec() == QDialog.Accepted:
            self.main.audit("Товар " + ("добавлен" if new else "изменён"), f"{p.name}, {money(p.price)}")
            self._refresh_stock()

    def _write_off(self):
        p = self._selected_product()
        if p is None:
            return
        dialog = WriteOffDialog(self, p)
        if dialog.exec() != QDialog.Accepted:
            return
        qty, reason = dialog.qty_box.value(), dialog.reason_box.currentText()
        try:
            self.db.write_off(p.id, qty, reason, self.user_id, self.main.sale_shift_id(), datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Списание", str(e))
            return
        self.main.audit("Списание", f"{p.name} × {qty_text(qty)} {p.unit} — {reason}, "
                        f"{money(round(qty * p.avg_cost))}")
        self._refresh_stock()

    def _count(self):
        p = self._selected_product()
        if p is None:
            return
        actual, ok = QInputDialog.getDouble(self, "Инвентаризация",
                                            f"{p.name}\nПо программе: {qty_text(p.stock)} {p.unit}\n\n"
                                            "Сколько на самом деле:", max(p.stock, 0), 0, 1_000_000, 2)
        if not ok:
            return
        diff = self.db.count_stock(p.id, actual, self.user_id, datetime.now())
        if diff:
            self.main.audit("Инвентаризация", f"{p.name}: {qty_text(p.stock)} → {qty_text(actual)} {p.unit}")
        self._refresh_stock()

    def _history(self):
        p = self._selected_product()
        if p is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"История — {p.name}")
        dialog.resize(560, 440)
        layout = QVBoxLayout(dialog)
        table = _table(["Когда", "Что", "Сколько", "Причина"], stretch=3)
        moves = self.db.stock_moves_of(p.id)
        table.setRowCount(len(moves))
        for i, (at, kind, qty, reason) in enumerate(moves):
            for col, text in enumerate((f"{at:%d.%m %H:%M}", MOVE_KINDS.get(kind, kind),
                                        ("+" if qty > 0 else "") + qty_text(qty), reason)):
                item = _item(text, align_right=col == 2)
                if col == 2:
                    item.setForeground(QColor(C.green if qty > 0 else C.red))
                table.setItem(i, col, item)
        layout.addWidget(table)
        layout.addWidget(QLabel(f"Сейчас на складе: <b>{qty_text(p.stock)} {p.unit}</b>"))
        dialog.exec()

    def _archive_product(self):
        p = self._selected_product()
        if p is None:
            return
        if QMessageBox.question(self, "В архив", f"Убрать «{p.name}» из списков? История продаж останется.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        p.active = False
        self.db.save_product(p)
        self.main.audit("Товар в архиве", p.name)
        self._refresh_stock()

    # ----- поставки -----

    def _supplies_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        add = QPushButton("➕ Новая поставка")
        add.setMinimumHeight(32)
        add.clicked.connect(self._add_supply)
        top.addWidget(add)
        top.addStretch(1)
        self.supplies_summary = QLabel()
        top.addWidget(self.supplies_summary)
        layout.addLayout(top)
        self.supplies_table = _table(["Когда", "Поставщик", "Что привезли", "Сумма", "Принял(а)"], stretch=2)
        self.supplies_table.setWordWrap(True)
        layout.addWidget(self.supplies_table, 1)
        return page

    def _refresh_supplies(self):
        supplies = self.db.supplies()
        self.supplies_table.setRowCount(len(supplies))
        for i, s in enumerate(supplies):
            what = ", ".join(f"{name} × {qty_text(qty)}" for name, qty, _ in s.lines)
            cells = [f"{s.at:%d.%m.%Y %H:%M}", s.supplier or "—", what + (f"\n{s.note}" if s.note else ""),
                     money(s.total), s.user_name]
            for col, text in enumerate(cells):
                self.supplies_table.setItem(i, col, _item(text, align_right=col == 3))
        self.supplies_table.resizeRowsToContents()
        month = date.today().replace(day=1)
        self.supplies_summary.setText(f"Поставок за {MONTHS[month.month - 1]}: "
                                      f"<b>{money(self.db.supplies_total(*day_range(month, date.today())))}</b>")

    def _add_supply(self):
        if not self.db.products():
            QMessageBox.information(self, "Поставка", "Сначала заведите товары во вкладке «📦 Остатки» "
                                    "(кнопка «➕ Товар», нужен администратор).")
            return
        dialog = SupplyDialog(self, self.db, self.user_id)
        if dialog.exec() == QDialog.Accepted:
            s = self.db.supplies(1)[0]
            self.main.audit("Поставка", f"№{s.id} от {s.supplier or '—'} на {money(s.total)}")
            self._refresh_supplies()

    # ----- техкарты -----

    def _recipes_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_gray("Техкарта — из чего собирается букет. При продаже букета цветы и упаковка "
                               "спишутся со склада сами."))
        self.recipes_table = _table(["Букет", "Состав", "Цена", "Себестоимость", "Наценка"], stretch=1)
        self.recipes_table.setWordWrap(True)
        self.recipes_table.itemDoubleClicked.connect(lambda _: self._edit_recipe() if self.admin else None)
        layout.addWidget(self.recipes_table, 1)
        buttons = QHBoxLayout()
        self.add_recipe_btn = QPushButton("➕ Техкарта")
        self.add_recipe_btn.clicked.connect(lambda: self._edit_recipe(new=True))
        self.edit_recipe_btn = QPushButton("✏️ Изменить")
        self.edit_recipe_btn.clicked.connect(self._edit_recipe)
        self.archive_recipe_btn = QPushButton("📁 В архив")
        self.archive_recipe_btn.clicked.connect(self._archive_recipe)
        for b in (self.add_recipe_btn, self.edit_recipe_btn, self.archive_recipe_btn):
            b.setMinimumHeight(30)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _refresh_recipes(self):
        names = {p.id: p for p in self.db.products(active_only=False)}
        recipes = self.db.recipes()
        self.recipes_table.setRowCount(len(recipes))
        for i, r in enumerate(recipes):
            cost = self.db.recipe_cost(r)
            margin = 100 * (r.price - cost) / r.price if r.price else 0
            parts = ", ".join(f"{names[pid].name} × {qty_text(qty)}" for pid, qty in r.items if pid in names)
            cells = [r.title, parts, money(r.price), money(round(cost)) if self.admin else "—",
                     f"{margin:.0f} %" if self.admin else "—"]
            for col, text in enumerate(cells):
                item = _item(text, r.id, align_right=col >= 2)
                if col == 4 and self.admin:
                    item.setForeground(QColor(C.green if margin >= 50 else C.orange if margin >= 30 else C.red))
                self.recipes_table.setItem(i, col, item)
        self.recipes_table.resizeRowsToContents()

    def _selected_recipe(self) -> Recipe | None:
        row = self.recipes_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Техкарты", "Выберите техкарту в списке.")
            return None
        return self.db.get_recipe(self.recipes_table.item(row, 0).data(Qt.UserRole))

    def _edit_recipe(self, new: bool = False):
        if new and not self.db.products():
            QMessageBox.information(self, "Техкарта", "Сначала заведите цветы и упаковку во вкладке «📦 Остатки».")
            return
        r = Recipe(0, "") if new else self._selected_recipe()
        if r is None:
            return
        if RecipeDialog(self, self.db, r).exec() == QDialog.Accepted:
            self.main.audit("Техкарта " + ("добавлена" if new else "изменена"), f"{r.title}, {money(r.price)}")
            self._refresh_recipes()

    def _archive_recipe(self):
        r = self._selected_recipe()
        if r is None:
            return
        if QMessageBox.question(self, "В архив", f"Убрать техкарту «{r.title}»?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.db.archive_recipe(r.id)
            self.main.audit("Техкарта в архиве", r.title)
            self._refresh_recipes()

    # ----- аналитика -----

    def _analytics_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        top.addWidget(QLabel("Период:"))
        self.period_box = QComboBox()
        self.period_box.addItems(PERIODS)
        self.period_box.setCurrentText("7 дней")
        self.period_box.currentIndexChanged.connect(self._refresh_analytics)
        top.addWidget(self.period_box)
        self.period_label = QLabel()
        self.period_label.setStyleSheet(f"color: {C.gray}")
        top.addWidget(self.period_label)
        top.addStretch(1)
        layout.addLayout(top)
        cards = QGridLayout()
        self.cards = {}
        for i, (key, title) in enumerate((("revenue", "Выручка"), ("count", "Продаж"), ("average", "Средний чек"),
                                          ("profit", "Валовая прибыль"), ("margin", "Маржа"),
                                          ("writeoff", "Списано"))):
            self.cards[key] = Card(title)
            cards.addWidget(self.cards[key], 0, i)
        layout.addLayout(cards)
        layout.addWidget(QLabel("<b>Выручка по дням</b>"))
        self.chart = BarChart()
        layout.addWidget(self.chart)
        tables = QHBoxLayout()
        self.top_table = _table(["Топ продаж", "Сколько", "Выручка"])
        self.people_table = _table(["Сотрудник", "Продаж", "Выручка"])
        self.money_table = _table(["Оплата / списание", "Сумма"])
        for table in (self.top_table, self.people_table, self.money_table):
            tables.addWidget(table)
        layout.addLayout(tables, 1)
        self.stock_line = QLabel()
        layout.addWidget(self.stock_line)
        return page

    def _refresh_analytics(self):
        today = date.today()
        first, last = period_range(self.period_box.currentText(), today)
        before = previous_range(first, last)
        now, then = self.db.sales_report(*day_range(first, last)), self.db.sales_report(*day_range(*before))
        writeoffs = self.db.writeoffs_between(*day_range(first, last))
        writeoffs_before = sum(self.db.writeoffs_between(*day_range(*before)).values())
        self.period_label.setText(f"{first:%d.%m.%Y}" + (f" — {last:%d.%m.%Y}" if last != first else "")
                                  + f" · сравнение с {before[0]:%d.%m} — {before[1]:%d.%m}")
        self.cards["revenue"].set(money(now.revenue), delta_text(now.revenue, then.revenue))
        self.cards["count"].set(str(now.count), delta_text(now.count, then.count))
        self.cards["average"].set(money(now.average), delta_text(now.average, then.average))
        self.cards["profit"].set(money(now.profit), delta_text(now.profit, then.profit))
        margin_delta = now.margin - then.margin
        self.cards["margin"].set(f"{now.margin:.0f} %", (
            (f"{'▲' if margin_delta > 0 else '▼'} {abs(margin_delta):.0f} п.п. к прошлому периоду",
             C.green if margin_delta > 0 else C.red) if then.revenue and abs(margin_delta) >= 0.5
            else ("в прошлом периоде — 0" if not then.revenue else "как в прошлом периоде", C.gray)))
        written = sum(writeoffs.values())
        self.cards["writeoff"].set(money(written), delta_text(written, writeoffs_before, money_like=False))
        days = [(first + timedelta(days=k)) for k in range((last - first).days + 1)]
        self.chart.set_values([(f"{d:%d.%m}", now.by_day.get(d, 0)) for d in days])

        self.top_table.setRowCount(len(now.top))
        for i, (title, qty, amount) in enumerate(now.top):
            for col, text in enumerate((title, qty_text(qty), money(amount))):
                self.top_table.setItem(i, col, _item(text, align_right=col > 0))
        people = sorted(now.by_user.items(), key=lambda x: -x[1][1])
        self.people_table.setRowCount(len(people))
        for i, (who, (n, amount)) in enumerate(people):
            for col, text in enumerate((who, str(n), money(amount))):
                self.people_table.setItem(i, col, _item(text, align_right=col > 0))
        rows = [(PAYMENTS.get(k, k), v) for k, v in sorted(now.by_payment.items(), key=lambda x: -x[1])]
        rows += [(f"🗑 {reason}", amount) for reason, amount in writeoffs.items()]
        if now.discount:
            rows.append(("Скидки", now.discount))
        self.money_table.setRowCount(len(rows))
        for i, (title, amount) in enumerate(rows):
            self.money_table.setItem(i, 0, _item(title))
            self.money_table.setItem(i, 1, _item(money(amount), align_right=True))
        low = [p for p in self.db.products() if p.low]
        self.stock_line.setText(
            f"На складе на <b>{money(self.db.stock_value())}</b> · поставок за период "
            f"<b>{money(self.db.supplies_total(*day_range(first, last)))}</b> · заканчивается: "
            + (f"<span style='color:{C.red}'>{', '.join(p.name for p in low[:8])}"
               f"{' …' if len(low) > 8 else ''}</span>" if low else f"<span style='color:{C.green}'>ничего</span>"))
