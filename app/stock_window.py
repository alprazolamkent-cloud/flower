"""Окно «🛒 Продажи и склад»: касса (продажа), остатки, поставки, техкарты букетов, аналитика.

Чек сотрудник пробивает на кассе АТОЛ сам — здесь продажа нужна для склада, выручки смены и отчётов.
"""

import html
import os
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QRadioButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import stock_import
from .db import UserError
from .delivery_dialog import money, money_box
from .icon import flower_icon
from .photos import pick_photo, pixmap
from .shifts import WRITEOFF_REASONS
from .stock import CATEGORIES, PAYMENTS, CartLine, Product, Recipe, day_range, qty_text
from .theme import C, primary

MOVE_KINDS = {"supply": "Поставка", "sale": "Продажа", "writeoff": "Списание", "count": "Инвентаризация",
              "return": "Возврат", "assembly": "Собрали букет", "disassembly": "Разобрали букет",
              "assemble": "Собрали", "disassemble": "Разобрали"}
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


TILE_PHOTO = 150  # ширина плитки и фото в продаже


def initials(title: str) -> str:
    """«Гелий» → «ГЕ», «Хриз Алтай» → «ХА» — вместо фото, пока его нет."""
    words = [w for w in title.replace("«", " ").replace("»", " ").split() if w[:1].isalpha()]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (words[0][:2] if words else "?").upper()


class Tile(QFrame):
    """Плитка товара или букета: фото, название, цена, остаток, «+»."""

    def __init__(self, key: tuple[str, int], title: str, price: int, stock: str, low: bool,
                 photo: QPixmap | None, on_add):
        super().__init__()
        self.key, self.on_add = key, on_add
        self.setObjectName("tile")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"#tile {{ background: {C.card_bg}; border: 1px solid {C.card_border}; "
                           "border-radius: 10px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        image = QLabel()
        image.setFixedSize(TILE_PHOTO, round(TILE_PHOTO * 0.72))
        image.setAlignment(Qt.AlignCenter)
        if photo is not None and not photo.isNull():
            cover = photo.scaled(image.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x, y = (cover.width() - image.width()) // 2, (cover.height() - image.height()) // 2
            image.setPixmap(cover.copy(x, y, image.width(), image.height()))
        else:
            image.setText(("💐 " if key[0] == "recipe" else "") + initials(title))
            soft, ink = (C.pink_soft, C.pink) if key[0] == "recipe" else (C.accent_soft, C.accent)
            image.setStyleSheet(f"color: {ink}; font-size: 20pt; font-weight: bold; "
                                f"background: {soft}; border-radius: 8px;")
        layout.addWidget(image)
        name = QLabel(title)
        name.setWordWrap(True)
        name.setFixedSize(TILE_PHOTO, name.fontMetrics().lineSpacing() * 2 + 2)
        name.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(name)
        line = QHBoxLayout()
        price_label = QLabel(f"<b style='font-size:12pt'>{money(price)}</b>")
        line.addWidget(price_label)
        line.addStretch(1)
        if stock:
            left = QLabel(stock)
            left.setStyleSheet(f"color: {C.red if low else C.gray}")
            left.setToolTip("Осталось на складе")
            line.addWidget(left)
        layout.addLayout(line)
        plus = QPushButton("+")
        plus.setMinimumHeight(30)
        plus.setStyleSheet(f"QPushButton {{ font-size: 15pt; color: {C.accent}; border-color: {C.accent}; }}"
                           f"QPushButton:hover {{ background: {C.accent}; color: {C.on_accent}; }}")
        plus.clicked.connect(lambda: on_add(*key))
        layout.addWidget(plus)
        self.ensurePolished()  # размер — уже с крупными шрифтами цены и «+» из стилей
        self.setFixedSize(TILE_PHOTO + 16, self.sizeHint().height())  # все плитки одинаковые, сетка их не сжимает

    def mouseReleaseEvent(self, event):  # noqa: N802 — щелчок по плитке, как «+»
        if event.button() == Qt.LeftButton:
            self.on_add(*self.key)


class TileGrid(QWidget):
    """Плитки сеткой: столбцов столько, сколько влезает по ширине."""

    def __init__(self, on_add):
        super().__init__()
        self.on_add = on_add
        self.tiles: list[Tile] = []
        self.columns = 0
        self.layout_ = QGridLayout(self)
        self.layout_.setSpacing(10)
        self.layout_.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.empty = _gray("Ничего не нашлось. Нет товара — добавьте его в «📦 Остатки» "
                           "или продайте как «✏️ Своя позиция».")
        self.layout_.addWidget(self.empty, 0, 0)

    def set_tiles(self, tiles: list[tuple]):
        for tile in self.tiles:  # убрать сразу: deleteLater сработает позже, а до того старые видны под новыми
            self.layout_.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.tiles = [Tile((kind, ident), title, price, stock, low, photo, self.on_add)
                      for kind, ident, title, price, stock, low, photo in tiles]
        self.columns = 0
        self._place()

    def _place(self):
        columns = max(1, (self.width() - 10) // (TILE_PHOTO + 26))
        if columns == self.columns and all(t.parent() is self for t in self.tiles):
            return
        self.columns = columns
        for tile in self.tiles:
            self.layout_.removeWidget(tile)
        self.empty.setVisible(not self.tiles)
        for i, tile in enumerate(self.tiles):
            self.layout_.addWidget(tile, i // columns, i % columns)
        rows = -(-len(self.tiles) // columns)
        margins = self.layout_.contentsMargins()
        height = self.tiles[0].height() if self.tiles else 0
        # прокрутка сама не узнаёт высоту сетки — иначе она ужимает ряды, и плитки наезжают друг на друга
        self.setMinimumHeight(rows * height + max(rows - 1, 0) * self.layout_.verticalSpacing()
                              + margins.top() + margins.bottom())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place()


class CartRow(QFrame):
    """Строка корзины: название, − сколько +, цена за штуку, сумма, ✕."""

    def __init__(self, line: CartLine, unit: str, on_change, on_remove):
        super().__init__()
        self.key = (line.title, line.recipe_id, line.product_id)
        self.setObjectName("cartrow")
        self.setStyleSheet(f"#cartrow {{ border-bottom: 1px solid {C.card_border}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        head = QHBoxLayout()
        title = QLabel(line.title)
        title.setWordWrap(True)
        head.addWidget(title, 1)
        remove = QPushButton("✕")
        remove.setFixedWidth(28)
        remove.setProperty("small", True)
        remove.setToolTip("Убрать из корзины")
        remove.clicked.connect(lambda: on_remove(self))
        head.addWidget(remove)
        layout.addLayout(head)
        row = QHBoxLayout()
        minus = QPushButton("−")
        plus = QPushButton("+")
        for b in (minus, plus):
            b.setFixedSize(34, 30)
            b.setProperty("small", True)
        self.qty = QDoubleSpinBox()
        self.qty.setProperty("small", True)  # стрелочки нулевой ширины: NoButtons под стилем ломает поле в Qt
        self.qty.setAlignment(Qt.AlignCenter)
        self.qty.setFixedWidth(64)
        self.qty.setRange(1 if line.recipe_id else 0.01, 100_000)
        self.qty.setDecimals(0 if line.recipe_id or unit in ("", "шт") else 2)
        self.qty.setValue(line.qty)
        minus.clicked.connect(lambda: self.qty.setValue(max(self.qty.value() - 1, self.qty.minimum())))
        plus.clicked.connect(lambda: self.qty.setValue(self.qty.value() + 1))
        for widget in (minus, self.qty, plus):
            row.addWidget(widget)
        row.addWidget(QLabel("×"))
        self.price = money_box()
        self.price.setSingleStep(10)
        self.price.setValue(line.price)
        self.price.setToolTip("Цена за штуку — можно поменять")
        row.addWidget(self.price)
        row.addStretch(1)
        self.amount = QLabel()
        row.addWidget(self.amount)
        layout.addLayout(row)
        self.qty.valueChanged.connect(on_change)
        self.price.valueChanged.connect(on_change)
        self.qty.valueChanged.connect(self._show)
        self.price.valueChanged.connect(self._show)
        self._show()

    def line(self) -> CartLine:
        title, recipe_id, product_id = self.key
        return CartLine(title, self.qty.value(), self.price.value(), recipe_id, product_id)

    def _show(self):
        self.amount.setText(f"<b>{money(self.line().amount)}</b>")


ADJUST_REASONS = {
    "discount": ("Постоянный клиент", "Вялый / сломанный цветок", "Акция", "Не было сдачи — округлили"),
    "markup": ("Срочно", "Сложная упаковка", "Доставка к двери", "Подбор под заказ"),
}


class AdjustDialog(QDialog):
    """Скидка или наценка (в ₽ или %) или новая итоговая сумма — всегда с причиной."""

    def __init__(self, parent, mode: str, gross: int, current: int = 0, note: str = ""):
        super().__init__(parent)
        self.mode, self.gross = mode, gross  # mode: "discount", "markup" или "total"
        self.setWindowTitle({"discount": "Скидка", "markup": "Наценка", "total": "Изменить сумму"}[mode])
        form = QFormLayout(self)
        form.addRow(QLabel(f"Сумма по корзине: <b>{money(gross)}</b>"))
        line = QHBoxLayout()
        self.value_box = QSpinBox()
        self.value_box.setRange(0, 10_000_000)
        self.value_box.setGroupSeparatorShown(True)
        line.addWidget(self.value_box, 1)
        self.kind_box = QComboBox()
        self.kind_box.addItems(["₽", "%"])
        if mode == "total":
            self.kind_box.hide()
            self.value_box.setSuffix(" ₽")
            self.value_box.setSingleStep(50)
            self.value_box.setValue(gross - current)
        line.addWidget(self.kind_box)
        form.addRow({"discount": "Скидка:", "markup": "Наценка:", "total": "К оплате:"}[mode], line)
        if mode != "total":
            quick = QHBoxLayout()
            for pct in (5, 10, 15, 20):
                b = QPushButton(f"{pct} %")
                b.clicked.connect(lambda _=False, v=pct: (self.kind_box.setCurrentIndex(1), self.value_box.setValue(v)))
                quick.addWidget(b)
            form.addRow(quick)
        self.note_box = QComboBox()
        self.note_box.setEditable(True)
        reasons = ADJUST_REASONS["markup" if mode == "markup" else "discount"]
        self.note_box.addItems(reasons if mode != "total" else (*ADJUST_REASONS["discount"], *ADJUST_REASONS["markup"]))
        self.note_box.setCurrentText(note)
        self.note_box.lineEdit().setPlaceholderText("Почему — обязательно (выберите или напишите)")
        form.addRow("Причина:", self.note_box)
        self.result_label = QLabel()
        form.addRow(self.result_label)
        form.addRow(_buttons(self, "Применить"))
        self.value_box.valueChanged.connect(self._show)
        self.kind_box.currentIndexChanged.connect(self._show)
        self._show()
        self.value_box.setFocus()
        self.value_box.selectAll()

    def amount(self) -> int:
        """Скидка (больше нуля) или наценка (меньше нуля) в рублях."""
        value = self.value_box.value()
        if self.mode == "total":
            return self.gross - value
        rub = round(self.gross * value / 100) if self.kind_box.currentIndex() == 1 else value
        return -rub if self.mode == "markup" else min(rub, self.gross)

    def note(self) -> str:
        return self.note_box.currentText().strip()

    def _show(self):
        adjust = self.amount()
        what = "" if not adjust else f" ({'скидка' if adjust > 0 else 'наценка'} {money(abs(adjust))})"
        self.result_label.setText(f"К оплате: <b>{money(self.gross - adjust)}</b>{what}")

    def accept(self):
        if self.amount() and not self.note():
            QMessageBox.warning(self, self.windowTitle(), "Напишите причину — её увидит администратор.")
            self.note_box.setFocus()
            return
        super().accept()


def _buttons(dialog: QDialog, ok: str = "Сохранить") -> QDialogButtonBox:
    """«Сохранить» / «Отмена» по-русски, даже если перевод Qt не загрузился."""
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText(ok)
    buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


# ----- диалоги -----

class PhotoPicker(QWidget):
    """Фото товара или букета для плитки в продаже: выбрать с компьютера или убрать."""

    def __init__(self, photo: bytes | None):
        super().__init__()
        self.photo, self.changed = photo, False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview = QLabel()
        self.preview.setFixedSize(96, 70)
        self.preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.preview)
        pick = QPushButton("📷 Выбрать…")
        pick.clicked.connect(self._pick)
        layout.addWidget(pick)
        self.remove = QPushButton("Убрать")
        self.remove.clicked.connect(lambda: self._set(None))
        layout.addWidget(self.remove)
        layout.addStretch(1)
        self._show()

    def _pick(self):
        data = pick_photo(self, "Фото для плитки в продаже", 800, 85)
        if data:
            self._set(data)

    def _set(self, data: bytes | None):
        self.photo, self.changed = data, True
        self._show()

    def _show(self):
        pm = pixmap(self.photo, 96)
        if pm.isNull():
            self.preview.setText("нет фото")
            self.preview.setStyleSheet(f"color: {C.gray}; border: 1px dashed {C.card_border};")
        else:
            self.preview.setPixmap(pm.scaled(96, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview.setStyleSheet("")
        self.remove.setVisible(self.photo is not None)


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
        self.photo = PhotoPicker(parent.db.photo_of("product", product.id) if product.id else None)
        form.addRow("Фото:", self.photo)
        form.addRow(_gray("Себестоимость считается сама — по ценам из поставок. 0 в «мало» — не следить."))
        form.addRow(_buttons(self))

    def accept(self):
        p = self.product
        p.name, p.category = self.name_edit.text(), self.category_box.currentText().strip() or "Цветы"
        p.unit, p.price, p.min_stock = self.unit_edit.text(), self.price_box.value(), self.min_box.value()
        try:
            self.parent().db.save_product(p)
        except UserError as e:
            QMessageBox.warning(self, "Товар", str(e))
            return
        if self.photo.changed:
            self.parent().db.set_photo("product", p.id, self.photo.photo)
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
        order = {c: i for i, c in enumerate(CATEGORIES)}
        rank = order.get(p.category, len(order))
        row = self.table.rowCount()  # по разделам: цветы, зелень, упаковка…
        for r in range(self.table.rowCount()):
            other = self._product(self.table.item(r, 0).data(Qt.UserRole))
            if other is not None and order.get(other.category, len(order)) > rank:
                row = r
                break
        self.table.insertRow(row)
        name = _item(f"{p.name}  ·  {p.category.lower()}", pid)
        self.table.setItem(row, 0, name)
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
        remove.setProperty("small", True)
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
        layout.addWidget(_buttons(self, "Принять поставку"))

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
        self.photo = PhotoPicker(db.photo_of("recipe", recipe.id) if recipe.id else None)
        form.addRow("Фото:", self.photo)
        self.in_bot = QCheckBox("Собранные показывать в витрине ботов (Telegram и MAX)")
        self.in_bot.setChecked(recipe.in_bot)
        form.addRow(self.in_bot)
        layout.addLayout(form)
        layout.addWidget(_gray("Состав до последнего стебля: основной цветок, заполнители, зелень и расходники "
                               "(бумага, лента, анкерная лента, аквапак). Можно дробно: 1,5 м ленты."))
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
        layout.addWidget(_buttons(self))
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
        r.in_bot = self.in_bot.isChecked()
        try:
            self.db.save_recipe(r)
        except UserError as e:
            QMessageBox.warning(self, "Техкарта", str(e))
            return
        if self.photo.changed:
            self.db.set_photo("recipe", r.id, self.photo.photo)
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
        form.addRow(_buttons(self, "Списать"))


class AssembleDialog(QDialog):
    """Собрать букет(ы) по техкарте: что и сколько уйдёт со склада, хватает ли."""

    def __init__(self, parent, db, recipe: Recipe):
        super().__init__(parent)
        self.db, self.recipe = db, recipe
        self.setWindowTitle(f"Собрать — {recipe.title}")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.qty_box = QSpinBox()
        self.qty_box.setRange(1, 100)
        self.qty_box.setSuffix(" шт")
        form.addRow("Сколько букетов собрали:", self.qty_box)
        layout.addLayout(form)
        self.table = _table(["Со склада", "На букет", "Нужно", "Есть на складе"])
        layout.addWidget(self.table, 1)
        self.short = QLabel()
        self.short.setWordWrap(True)
        layout.addWidget(self.short)
        layout.addWidget(_gray("Цветы и упаковка спишутся со склада сейчас. Букет появится на витрине "
                               + ("и в ботах." if recipe.in_bot else "(в ботах не показывается — так в техкарте).")))
        layout.addWidget(_buttons(self, "💐 Собрал"))
        self.qty_box.valueChanged.connect(self._show)
        self._show()

    def _show(self):
        n = self.qty_box.value()
        names = {p.id: p for p in self.db.products(active_only=False)}
        self.table.setRowCount(len(self.recipe.items))
        short = []
        for i, (pid, q) in enumerate(self.recipe.items):
            p = names.get(pid)
            need, have = q * n, p.stock if p else 0
            unit = f" {p.unit}" if p else ""
            for col, text in enumerate((p.name if p else "?", qty_text(q) + unit, qty_text(need) + unit,
                                        qty_text(have) + unit)):
                item = _item(text, align_right=col > 0)
                if col == 3 and have < need:
                    item.setForeground(QColor(C.red))
                self.table.setItem(i, col, item)
            if have < need and p:
                short.append(f"{p.name} (не хватает {qty_text(need - have)}{unit})")
        self.short.setText(f"<span style='color:{C.orange}'>⚠ По программе не хватает: {html.escape(', '.join(short))}. "
                           "Если на самом деле есть — значит, поставку не внесли; собрать всё равно можно.</span>"
                           if short else "")


class ReadyWriteOffDialog(QDialog):
    def __init__(self, parent, recipe: Recipe):
        super().__init__(parent)
        self.setWindowTitle("Списать с витрины")
        form = QFormLayout(self)
        form.addRow(QLabel(f"<b>{html.escape(recipe.title)}</b> · на витрине {qty_text(recipe.ready)} шт"))
        self.qty_box = QSpinBox()
        self.qty_box.setRange(1, max(int(recipe.ready), 1))
        self.qty_box.setSuffix(" шт")
        form.addRow("Сколько списать:", self.qty_box)
        self.reason_box = QComboBox()
        self.reason_box.addItems(WRITEOFF_REASONS)
        form.addRow("Причина:", self.reason_box)
        form.addRow(_gray("Списывается весь букет по себестоимости сборки — сумма попадёт в сдачу смены. "
                          "Часть цветов ещё живая? Тогда лучше «🔧 Разобрать»."))
        form.addRow(_buttons(self, "Списать"))


class DisassembleDialog(QDialog):
    """Разобрать готовый букет: что выкинули — в списание, остальное на склад; можно сразу пересобрать."""

    def __init__(self, parent, db, recipe: Recipe):
        super().__init__(parent)
        self.db, self.recipe = db, recipe
        self.setWindowTitle(f"Разобрать — {recipe.title}")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.qty_box = QSpinBox()
        self.qty_box.setRange(1, max(int(recipe.ready), 1))
        self.qty_box.setSuffix(f" шт (на витрине {qty_text(recipe.ready)})")
        form.addRow("Сколько букетов разобрали:", self.qty_box)
        layout.addLayout(form)
        layout.addWidget(QLabel("Что выкинули (завяло, сломалось) — остальное вернётся на склад:"))
        names = {p.id: p for p in db.products(active_only=False)}
        self.table = _table(["Товар", "В букетах", "Выкинули", "Вернётся на склад"])
        self.table.setRowCount(len(recipe.items))
        self.broken_boxes: list[tuple[int, float, QDoubleSpinBox]] = []
        for i, (pid, q) in enumerate(recipe.items):
            p = names.get(pid)
            self.table.setItem(i, 0, _item(p.name if p else "?"))
            self.table.setItem(i, 1, _item("", align_right=True))
            box = qty_box(p.unit if p else "")
            box.setValue(0)
            box.valueChanged.connect(self._show)
            self.table.setCellWidget(i, 2, box)
            self.table.setItem(i, 3, _item("", align_right=True))
            self.broken_boxes.append((pid, q, box))
        layout.addWidget(self.table, 1)
        reason = QFormLayout()
        self.reason_box = QComboBox()
        self.reason_box.addItems(WRITEOFF_REASONS)
        reason.addRow("Причина списания:", self.reason_box)
        layout.addLayout(reason)
        self.to_stock = QRadioButton("Всё целое — вернуть на склад")
        self.to_stock.setChecked(True)
        self.rebuild = QRadioButton("Пересобрать в:")
        row = QHBoxLayout()
        row.addWidget(self.rebuild)
        self.target_box = QComboBox()
        for other in db.recipes():
            if other.id != recipe.id:
                self.target_box.addItem(other.title, other.id)
        row.addWidget(self.target_box, 1)
        self.target_qty = QSpinBox()
        self.target_qty.setRange(1, 100)
        self.target_qty.setSuffix(" шт")
        row.addWidget(self.target_qty)
        layout.addWidget(self.to_stock)
        layout.addLayout(row)
        self.rebuild.setEnabled(self.target_box.count() > 0)
        layout.addWidget(_gray("Пересобрать: целое из разобранного пойдёт в новый букет, чего не хватит — "
                               "возьмётся со склада, лишнее останется на складе."))
        layout.addWidget(_buttons(self, "🔧 Разобрал"))
        self.qty_box.valueChanged.connect(self._show)
        self._show()

    def broken(self) -> dict[int, float]:
        return {pid: box.value() for pid, _, box in self.broken_boxes if box.value() > 0}

    def target(self) -> tuple[int, int] | None:
        if self.rebuild.isChecked() and self.target_box.currentData() is not None:
            return self.target_box.currentData(), self.target_qty.value()
        return None

    def _show(self):
        n = self.qty_box.value()
        for i, (pid, q, box) in enumerate(self.broken_boxes):
            total = q * n
            box.setMaximum(total)
            self.table.item(i, 1).setText(qty_text(total))
            self.table.item(i, 3).setText(qty_text(max(total - box.value(), 0)))


class ImportDialog(QDialog):
    """Предпросмотр загрузки из таблицы: что добавится, что обновится, что не так."""

    def __init__(self, parent, rows: list, problems: list[str], file_name: str):
        super().__init__(parent)
        self.setWindowTitle("Загрузить товары из таблицы")
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        new = sum(r.existing is None for r in rows)
        layout.addWidget(QLabel(f"<b>{html.escape(file_name)}</b>: товаров {len(rows)} — добавить {new}, "
                                f"обновить {len(rows) - new}"))
        table = _table(["Что сделать", "Название", "Раздел", "Ед.", "Цена", "Закупка", "Остаток", "Мало ≤"], stretch=1)
        table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = [r.action, r.name, r.category or "—", r.unit or "—",
                     money(r.price) if r.price is not None else "—",
                     f"{r.cost:g} ₽".replace(".", ",") if r.cost is not None else "—",
                     qty_text(r.stock) if r.stock is not None else "—",
                     qty_text(r.min_stock) if r.min_stock is not None else "—"]
            for col, text in enumerate(cells):
                item = _item(text, align_right=col >= 4)
                if col == 0:
                    item.setForeground(QColor(C.blue if r.existing else C.green))
                table.setItem(i, col, item)
        layout.addWidget(table, 1)
        if problems:
            warn = QLabel("<br>".join(f"⚠ {html.escape(p)}" for p in problems[:12])
                          + ("<br>…" if len(problems) > 12 else ""))
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {C.orange}")
            layout.addWidget(warn)
        layout.addWidget(_gray("Остатки новых товаров занесутся поставкой «Загрузка из таблицы» с закупочными ценами, "
                               "у уже заведённых — инвентаризацией (станет ровно как в таблице)."))
        buttons = _buttons(self, f"📥 Загрузить ({len(rows)})")
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(rows))
        layout.addWidget(buttons)


# ----- окно -----

class StockWindow(QDialog):
    def __init__(self, window):
        super().__init__(window, Qt.Window)
        self.main = window
        self.db = window.db
        self.setWindowTitle("Продажи и склад")
        self.setWindowIcon(flower_icon())
        self._photos: dict[tuple[str, int], QPixmap] = {}
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
        for widget in (self.add_product_btn, self.edit_product_btn, self.count_btn, self.archive_btn, self.import_btn,
                       self.add_recipe_btn, self.edit_recipe_btn, self.archive_recipe_btn):
            widget.setVisible(self.admin)
        page = self.tabs.currentIndex()
        if page == 0:
            self._refresh_chips()
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
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Букет или товар (Ctrl+F), Enter — в корзину")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refresh_catalog)
        self.search_edit.returnPressed.connect(self._add_selected)
        top.addWidget(self.search_edit, 1)
        free_btn = QPushButton("✏️ Своя позиция")
        free_btn.setToolTip("Букет не по техкарте, открытка, услуга — без списания со склада")
        free_btn.clicked.connect(self._add_free)
        top.addWidget(free_btn)
        left.addLayout(top)
        self.chips_row = QHBoxLayout()
        self.chips_row.setSpacing(6)
        self.chips = QButtonGroup(self)
        self.chips.setExclusive(True)
        self.chips.buttonClicked.connect(lambda _: self._refresh_catalog())
        left.addLayout(self.chips_row)
        self.grid = TileGrid(self._add_tile)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.grid)
        left.addWidget(scroll, 1)
        layout.addLayout(left, 3)

        right = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(QLabel("<b style='font-size:13pt'>Корзина</b>"))
        head.addStretch(1)
        self.adjust_btn = QPushButton("🧮 Скидка / наценка")
        menu = QMenu(self.adjust_btn)
        menu.addAction("💸 Добавить наценку", lambda: self._ask_adjust("markup"))
        menu.addAction("％ Добавить скидку", lambda: self._ask_adjust("discount"))
        menu.addAction("✏️ Изменить сумму", lambda: self._ask_adjust("total"))
        self.adjust_btn.setMenu(menu)
        head.addWidget(self.adjust_btn)
        clear_btn = QPushButton("🗑")
        clear_btn.setToolTip("Очистить корзину")
        clear_btn.clicked.connect(self._clear_cart)
        head.addWidget(clear_btn)
        right.addLayout(head)
        self.cart_box = QWidget()
        self.cart_layout = QVBoxLayout(self.cart_box)
        self.cart_layout.setContentsMargins(0, 0, 0, 0)
        self.cart_empty = _gray("Корзина пуста — нажмите на букет или товар слева.")
        self.cart_layout.addWidget(self.cart_empty)
        self.cart_layout.addStretch(1)
        cart_scroll = QScrollArea()
        cart_scroll.setWidgetResizable(True)
        cart_scroll.setWidget(self.cart_box)
        cart_scroll.setMinimumHeight(200)
        right.addWidget(cart_scroll, 3)
        self.rows: list[CartRow] = []
        self.adjust = 0  # больше нуля — скидка, меньше — наценка
        self.adjust_note = ""  # почему

        self.sum_label = QLabel()
        self.adjust_row = QHBoxLayout()
        self.adjust_label = QLabel()
        self.adjust_row.addWidget(self.adjust_label, 1)
        self.adjust_clear = QPushButton("✕")
        self.adjust_clear.setFixedWidth(28)
        self.adjust_clear.setProperty("small", True)
        self.adjust_clear.setToolTip("Убрать скидку / наценку")
        self.adjust_clear.clicked.connect(lambda: self.set_adjust(0))
        self.adjust_label.setWordWrap(True)
        self.adjust_row.addWidget(self.adjust_clear)
        right.addWidget(self.sum_label)
        right.addLayout(self.adjust_row)
        total_row = QHBoxLayout()
        total_row.addWidget(QLabel("К оплате"))
        total_row.addStretch(1)
        self.total_label = QLabel()
        total_row.addWidget(self.total_label)
        right.addLayout(total_row)
        pay = QHBoxLayout()
        self.pay_buttons = []
        for code, title in PAYMENTS.items():
            icon = {"cash": "💵", "card": "💳", "qr": "📱"}[code]
            button = QPushButton(f"{icon} {title}")
            button.setMinimumHeight(46)
            primary(button)
            button.clicked.connect(lambda _=False, c=code: self._sell(c))
            pay.addWidget(button)
            self.pay_buttons.append(button)
        right.addLayout(pay)
        self.shift_hint = _gray("")
        right.addWidget(self.shift_hint)
        right.addWidget(_gray("🧾 Чек пробейте на АТОЛ, как обычно — при сдаче смены суммы подставятся сами."))
        today_head = QHBoxLayout()
        self.today_total = QLabel()
        today_head.addWidget(self.today_total, 1)
        cancel_btn = QPushButton("↩ Отменить продажу")
        cancel_btn.setToolTip("Возврат или ошиблись: товар вернётся на склад, сумма уйдёт из выручки")
        cancel_btn.clicked.connect(self._cancel_sale)
        today_head.addWidget(cancel_btn)
        right.addLayout(today_head)
        self.today_table = _table(["Время", "Что", "Сумма", "Оплата", "Кто"], stretch=1)
        right.addWidget(self.today_table, 2)
        layout.addLayout(right, 2)
        self._update_cart()
        return page

    def _refresh_chips(self):
        current = self.chips.checkedButton().property("chip") if self.chips.checkedButton() else None
        for button in self.chips.buttons():
            self.chips.removeButton(button)
            button.deleteLater()
        while self.chips_row.count():
            self.chips_row.takeAt(0)
        names = []
        if self.db.popular(datetime.now() - timedelta(days=30), 1):
            names.append(("popular", "⭐ Частые"))
        names.append(("all", "Все"))
        if self.db.recipes():
            names.append(("recipes", "💐 Букеты"))
        present = {p.category for p in self.db.products()}
        names += [(c, c) for c in (*CATEGORIES, *sorted(present - set(CATEGORIES))) if c in present]
        for key, title in names:
            chip = QPushButton(title)
            chip.setCheckable(True)
            chip.setProperty("chip", key)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(
                f"QPushButton {{ border: 1px solid {C.pink_line}; border-radius: 11px; padding: 4px 14px; }}"
                f"QPushButton:checked {{ background: {C.accent}; color: {C.on_accent}; border-color: {C.accent}; }}")
            self.chips.addButton(chip)
            self.chips_row.addWidget(chip)
            chip.setChecked(key == (current or ("popular" if names[0][0] == "popular" else "all")))
        if self.chips.checkedButton() is None and self.chips.buttons():
            self.chips.buttons()[0].setChecked(True)
        self.chips_row.addStretch(1)

    def _refresh_catalog(self):
        self._photos.clear()  # фото могли поменять в «Остатках» / «Техкартах»
        query = self.search_edit.text().strip().lower()
        chip = self.chips.checkedButton().property("chip") if self.chips.checkedButton() else "all"
        if query:
            chip = "all"  # ищем везде
        recipes = {r.id: r for r in self.db.recipes()}
        products = {p.id: p for p in self.db.products()}
        if chip == "popular":
            keys = self.db.popular(datetime.now() - timedelta(days=30), 24)
        else:
            keys = ([("recipe", r) for r in recipes] if chip in ("all", "recipes") else [])
            keys += [("product", p.id) for p in products.values() if chip == "all" or p.category == chip]
        tiles = []
        for kind, ident in keys:
            item = recipes.get(ident) if kind == "recipe" else products.get(ident)
            if item is None:
                continue
            title = item.title if kind == "recipe" else item.name
            if query and query not in title.lower() and (kind == "recipe" or query not in item.category.lower()):
                continue
            stock = (f"готово: {qty_text(item.ready)}" if item.ready > 0 else "") if kind == "recipe" else \
                f"{qty_text(item.stock)} {item.unit}"
            tiles.append((kind, ident, title, item.price, stock, kind == "product" and item.low,
                          self._photo(kind, ident) if item.has_photo else None))
        self.grid.set_tiles(tiles)

    def _photo(self, kind: str, ident: int) -> QPixmap:
        key = (kind, ident)
        if key not in self._photos:
            self._photos[key] = pixmap(self.db.photo_of(kind, ident), TILE_PHOTO * 2)
        return self._photos[key]

    def _add_selected(self):
        """Enter в поиске — первая плитка в корзину."""
        if self.grid.tiles:
            kind, ident = self.grid.tiles[0].key
            self._add_tile(kind, ident)
            self.search_edit.selectAll()
            self.search_edit.setFocus()

    def _add_tile(self, kind: str, ident: int):
        if kind == "recipe":
            r = self.db.get_recipe(ident)
            self._add_line(CartLine(r.title, 1, r.price, recipe_id=r.id))
        else:
            p = self.db.get_product(ident)
            self._add_line(CartLine(p.name, 1, p.price, product_id=p.id), unit=p.unit)

    def _add_free(self):
        title, ok = QInputDialog.getText(self, "Своя позиция", "Что продали:")
        if not ok or not title.strip():
            return
        price, ok = QInputDialog.getInt(self, "Своя позиция", f"Цена «{title.strip()}», ₽:", 0, 0, 10_000_000, 50)
        if ok:
            self._add_line(CartLine(title.strip(), 1, price))

    def _add_line(self, line: CartLine, unit: str = ""):
        for row in self.rows:
            if (line.recipe_id or line.product_id) and row.key == (line.title, line.recipe_id, line.product_id):
                row.qty.setValue(row.qty.value() + 1)
                return
        row = CartRow(line, unit, self._update_cart, self._remove_row)
        self.rows.append(row)
        self.cart_layout.insertWidget(self.cart_layout.count() - 1, row)
        self._update_cart()

    def _remove_row(self, row: "CartRow"):
        self.rows.remove(row)
        self.cart_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._update_cart()

    def _clear_cart(self):
        for row in list(self.rows):
            self._remove_row(row)
        self.set_adjust(0)

    def cart_lines(self) -> list[CartLine]:
        return [row.line() for row in self.rows]

    def gross(self) -> int:
        return sum(line.amount for line in self.cart_lines())

    def set_adjust(self, value: int, note: str = ""):
        self.adjust, self.adjust_note = value, note.strip() if value else ""
        self._update_cart()

    def _ask_adjust(self, mode: str):
        gross = self.gross()
        if not gross:
            return
        dialog = AdjustDialog(self, mode, gross, self.adjust, self.adjust_note)
        if dialog.exec() == QDialog.Accepted:
            self.set_adjust(dialog.amount(), dialog.note())

    def _update_cart(self):
        lines = self.cart_lines()
        gross = sum(line.amount for line in lines)
        if not lines:
            self.adjust = 0
        self.adjust = min(self.adjust, gross)
        total = gross - self.adjust
        self.cart_empty.setVisible(not lines)
        self.sum_label.setText(f"Сумма: {money(gross)}" if self.adjust else "")
        self.sum_label.setVisible(bool(self.adjust))
        what = f"Скидка: −{money(self.adjust)}" if self.adjust > 0 else f"Наценка: +{money(-self.adjust)}"
        self.adjust_label.setText(f"<span style='color:{C.green if self.adjust > 0 else C.orange}'>{what}</span>"
                                  + (f" ({100 * abs(self.adjust) / gross:.0f} %)" if gross else "")
                                  + (f" — {html.escape(self.adjust_note)}" if self.adjust_note else ""))
        for widget in (self.adjust_label, self.adjust_clear):
            widget.setVisible(bool(self.adjust))
        self.total_label.setText(f"<span style='font-size:20pt; font-weight:bold'>{money(total)}</span>")
        self.adjust_btn.setEnabled(bool(lines))
        for button in self.pay_buttons:
            button.setEnabled(bool(lines))
        shift_id = self.main.sale_shift_id()
        self.shift_hint.setText("" if shift_id else "⚠ Смена не начата — продажа не попадёт в выручку смены.")
        self.shift_hint.setStyleSheet(f"color: {C.orange}")

    def _sell(self, payment: str):
        lines = self.cart_lines()
        try:
            sale_id = self.db.add_sale(lines, payment, self.user_id, self.main.sale_shift_id(), datetime.now(),
                                       discount=self.adjust, adjust_note=self.adjust_note)
        except UserError as e:
            QMessageBox.warning(self, "Продажа", str(e))
            return
        total = sum(line.amount for line in lines) - self.adjust
        adjust = ("скидка" if self.adjust > 0 else "наценка") + f" {money(abs(self.adjust))} — {self.adjust_note}"
        self.main.audit("Продажа", f"№{sale_id}: {money(total)}, {PAYMENTS[payment]} — "
                        + ", ".join(f"{x.title} × {qty_text(x.qty)}" for x in lines)
                        + (f"; {adjust}" if self.adjust else ""))
        self._clear_cart()
        self._refresh_chips()
        self._refresh_catalog()
        self._refresh_today()
        self.search_edit.setFocus()

    def _refresh_today(self):
        sales = self.db.sales_between(*day_range(date.today(), date.today()))
        self.today_table.setRowCount(len(sales))
        total = count = 0
        for i, s in enumerate(sales):
            extra = f" ({s.adjust_text})" if s.discount else ""
            cells = [f"{s.at:%H:%M}", s.what + extra, money(s.total), PAYMENTS.get(s.payment, s.payment),
                     s.user_name or "бот"]
            for col, text in enumerate(cells):
                item = _item(text, s.id, align_right=col == 2)
                if s.cancelled_at:
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setForeground(QColor(C.gray))
                    item.setToolTip(f"Отменена {s.cancelled_at:%H:%M}: {s.cancel_reason or 'без причины'}")
                elif col == 1 or s.note:  # «Что» обрезается — полностью во всплывающей подсказке
                    item.setToolTip("\n".join(x for x in (cells[1] if col == 1 else "", s.note) if x))
                self.today_table.setItem(i, col, item)
            if not s.cancelled_at:
                total += s.total
                count += 1
        self.today_total.setText(f"<b>Продажи за сегодня:</b> {count} на <b>{money(total)}</b>")

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
        self.import_btn = QPushButton("📥 Загрузить из таблицы")
        self.import_btn.setToolTip("Excel (.xlsx) или CSV: шаблон программы или выгрузка из Hoog")
        self.import_btn.clicked.connect(self._import)
        for b in (self.add_product_btn, self.edit_product_btn, writeoff_btn, self.count_btn, history_btn,
                  self.archive_btn, self.import_btn):
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

    def _import(self, path: str = ""):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Таблица с товарами", "",
                                                  "Таблицы (*.xlsx *.xlsm *.csv);;Все файлы (*)")
        if not path:
            return
        try:
            rows, problems = stock_import.parse(self.db, stock_import.read_table(path))
        except UserError as e:
            QMessageBox.warning(self, "Загрузка из таблицы", str(e))
            return
        if ImportDialog(self, rows, problems, os.path.basename(path)).exec() != QDialog.Accepted:
            return
        try:
            added, updated = stock_import.apply(self.db, rows, self.user_id, datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Загрузка из таблицы", str(e))
            return
        self.main.audit("Товары загружены из таблицы", f"{os.path.basename(path)}: добавлено {added}, "
                        f"обновлено {updated}")
        QMessageBox.information(self, "Загрузка из таблицы", f"Готово: добавлено {added}, обновлено {updated}.")
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
        layout.addWidget(_gray("Техкарта — из чего собирается букет. «💐 Собрать» — цветы и упаковка списываются "
                               "со склада сразу, букет встаёт на витрину (и в боты). Продали — со склада второй раз "
                               "не списывается. Не собирали заранее — спишется при продаже."))
        self.recipes_table = _table(["Букет", "Состав", "Цена", "На витрине", "Себестоимость", "Наценка"], stretch=1)
        self.recipes_table.setWordWrap(True)
        self.recipes_table.itemDoubleClicked.connect(lambda _: self._edit_recipe() if self.admin else None)
        layout.addWidget(self.recipes_table, 1)
        buttons = QHBoxLayout()
        assemble_btn = QPushButton("💐 Собрать на витрину")
        assemble_btn.clicked.connect(self._assemble)
        disassemble_btn = QPushButton("🔧 Разобрать / пересобрать")
        disassemble_btn.clicked.connect(self._disassemble)
        writeoff_btn = QPushButton("🗑 Списать с витрины")
        writeoff_btn.setToolTip("Готовый букет завял или сломался — списать целиком")
        writeoff_btn.clicked.connect(self._write_off_ready)
        history_btn = QPushButton("📜 История")
        history_btn.clicked.connect(self._recipe_history)
        self.add_recipe_btn = QPushButton("➕ Техкарта")
        self.add_recipe_btn.clicked.connect(lambda: self._edit_recipe(new=True))
        self.edit_recipe_btn = QPushButton("✏️ Изменить")
        self.edit_recipe_btn.clicked.connect(self._edit_recipe)
        self.archive_recipe_btn = QPushButton("📁 В архив")
        self.archive_recipe_btn.clicked.connect(self._archive_recipe)
        for b in (assemble_btn, disassemble_btn, writeoff_btn, history_btn):
            b.setMinimumHeight(32)
            buttons.addWidget(b)
        buttons.addStretch(1)
        for b in (self.add_recipe_btn, self.edit_recipe_btn, self.archive_recipe_btn):
            b.setMinimumHeight(32)
            buttons.addWidget(b)
        layout.addLayout(buttons)
        return page

    def _refresh_recipes(self):
        selected = self._selected_recipe_id()
        recipes = self.db.recipes()
        self.recipes_table.setRowCount(len(recipes))
        for i, r in enumerate(recipes):
            cost = self.db.recipe_cost(r)
            margin = 100 * (r.price - cost) / r.price if r.price else 0
            parts = "\n".join(f"{group}: {', '.join(items)}" for group, items in self.db.composition(r))
            reserved = self.db.reserved_of(r.id)
            ready = qty_text(r.ready) + (f" (заказано в боте: {reserved})" if reserved else "") if r.ready else "—"
            cells = [r.title + ("" if r.in_bot else "\n(не в боте)"), parts, money(r.price), ready,
                     money(round(cost)) if self.admin else "—", f"{margin:.0f} %" if self.admin else "—"]
            for col, text in enumerate(cells):
                item = _item(text, r.id, align_right=col >= 2)
                if col == 3 and r.ready:
                    item.setForeground(QColor(C.green))
                if col == 5 and self.admin:
                    item.setForeground(QColor(C.green if margin >= 50 else C.orange if margin >= 30 else C.red))
                self.recipes_table.setItem(i, col, item)
            if r.id == selected:
                self.recipes_table.selectRow(i)
        self.recipes_table.resizeRowsToContents()

    def _selected_recipe_id(self) -> int | None:
        row = self.recipes_table.currentRow()
        item = self.recipes_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def _selected_recipe(self) -> Recipe | None:
        rid = self._selected_recipe_id()
        if rid is None:
            QMessageBox.information(self, "Техкарты", "Выберите техкарту в списке.")
            return None
        return self.db.get_recipe(rid)

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

    def _assemble(self):
        r = self._selected_recipe()
        if r is None:
            return
        dialog = AssembleDialog(self, self.db, r)
        if dialog.exec() != QDialog.Accepted:
            return
        qty = dialog.qty_box.value()
        try:
            self.db.assemble(r.id, qty, self.user_id, self.main.sale_shift_id(), datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Собрать", str(e))
            return
        self.main.audit("Букет собран на витрину", f"{r.title} × {qty}")
        self._showcase_changed()

    def _write_off_ready(self):
        r = self._selected_recipe()
        if r is None:
            return
        if r.ready <= 0:
            QMessageBox.information(self, "Списать с витрины", f"Собранных «{r.title}» на витрине нет.")
            return
        dialog = ReadyWriteOffDialog(self, r)
        if dialog.exec() != QDialog.Accepted:
            return
        qty, reason = dialog.qty_box.value(), dialog.reason_box.currentText()
        try:
            self.db.write_off_ready(r.id, qty, reason, self.user_id, self.main.sale_shift_id(), datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Списать с витрины", str(e))
            return
        self.main.audit("Букет списан с витрины", f"{r.title} × {qty} — {reason}")
        self._showcase_changed()

    def _disassemble(self):
        r = self._selected_recipe()
        if r is None:
            return
        if r.ready <= 0:
            QMessageBox.information(self, "Разобрать", f"Собранных «{r.title}» на витрине нет.")
            return
        dialog = DisassembleDialog(self, self.db, r)
        if dialog.exec() != QDialog.Accepted:
            return
        qty, now, shift_id = dialog.qty_box.value(), datetime.now(), self.main.sale_shift_id()
        try:
            self.db.disassemble(r.id, qty, dialog.broken(), dialog.reason_box.currentText(), self.user_id, shift_id, now)
            target = dialog.target()
            if target:
                self.db.assemble(target[0], target[1], self.user_id, shift_id, now)
        except UserError as e:
            QMessageBox.warning(self, "Разобрать", str(e))
            self._showcase_changed()
            return
        broken = ", ".join(f"{self.db.get_product(pid).name} × {qty_text(q)}" for pid, q in dialog.broken().items() if q)
        then = (f" → пересобрали в «{self.db.get_recipe(target[0]).title}» × {target[1]}" if target
                else " → на склад")
        self.main.audit("Букет разобран", f"{r.title} × {qty}{then}" + (f"; выкинули: {broken}" if broken else ""))
        self._showcase_changed()

    def _recipe_history(self):
        r = self._selected_recipe()
        if r is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Витрина — {r.title}")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        table = _table(["Когда", "Что", "Сколько", "Причина"], stretch=3)
        moves = self.db.ready_moves_of(r.id)
        table.setRowCount(len(moves))
        for i, (at, kind, qty, reason) in enumerate(moves):
            for col, text in enumerate((f"{at:%d.%m %H:%M}", MOVE_KINDS.get(kind, kind),
                                        ("+" if qty > 0 else "") + qty_text(qty), reason)):
                item = _item(text, align_right=col == 2)
                if col == 2:
                    item.setForeground(QColor(C.green if qty > 0 else C.red))
                table.setItem(i, col, item)
        layout.addWidget(table)
        layout.addWidget(QLabel(f"Сейчас на витрине: <b>{qty_text(r.ready)}</b>"))
        dialog.exec()

    def _showcase_changed(self):
        """Собрали / продали / разобрали — обновить здесь и витрину в «🛍 Клиенты» (боты читают базу сами)."""
        self._refresh_recipes()
        center = getattr(self.main, "client_center", None)
        if center is not None and center.isVisible():
            center.refresh_showcase()

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
        rows += [(f"{'％' if key.startswith('Скидка') else '💸'} {key}", amount)
                 for key, amount in sorted(now.adjust_notes.items(), key=lambda x: -x[1])]
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
