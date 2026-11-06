"""Печать этикеток прямо на принтер и режим «только сканером» (продажа, поставка, инвентаризация, списание)."""

from datetime import datetime

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox, QWidget

from app import stock_window
from app.client_flow import ClientFlow, Incoming
from app.db import Database
from app.labels import Label, print_labels, render_label_image
from app.stock import Product, Recipe

NOW = datetime(2026, 11, 5, 12, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def store(monkeypatch):
    values = {}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: values.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: values.__setitem__(key, value))
    return values


class FakeMain(QWidget):
    def __init__(self, db, user):
        super().__init__()
        self.db, self.user, self.shift, self.log = db, user, None, []

    def audit(self, action, details=""):
        self.log.append((action, details))

    def sale_shift_id(self):
        return None


def test_print_to_pdf_and_image(tmp_path):
    pdf = tmp_path / "labels.pdf"
    pages = print_labels([Label("н15", "2000000000015", 150, 2), Label("Шар", "AB-12", None, 1)],
                         "thermal", pdf_path=str(pdf))
    assert pages == 3 and pdf.read_bytes().startswith(b"%PDF")
    assert print_labels([Label("x", "2000000000015", 100, 30)], "a4", pdf_path=str(tmp_path / "a4.pdf")) == 2
    image = render_label_image(Label("н15", "2000000000015", 150), "thermal", 203)
    assert (image.width(), image.height()) == (round(58 * 203 / 25.4), round(40 * 203 / 25.4))


def test_labels_dialog_prints_to_chosen_printer(db, store, monkeypatch):
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250))
    printed, opened = [], []
    monkeypatch.setattr(stock_window, "printers", lambda: (["Microsoft Print to PDF", "Xprinter XP-365B"],
                                                           "Xprinter XP-365B"))
    monkeypatch.setattr(stock_window, "print_labels", lambda labels, layout, name, **kw: printed.append((labels, layout, name, kw)))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url))
    dialog = stock_window.LabelsDialog(None, db, "product", rose)
    assert dialog.printer.currentData() == "Xprinter XP-365B"  # принтер этикеток выбран сам
    dialog.copies[0].setValue(3)
    dialog._print()
    labels, layout, name, kw = printed[0]
    assert name == "Xprinter XP-365B" and layout == "thermal" and labels[0].copies == 3 and not opened
    assert kw == {"size": (58, 40), "offset": (0.0, 0.0)} and labels[0].price == 250
    assert store["label_printer"] == "Xprinter XP-365B" and db.get_product(rose).barcode.startswith("200")

    def broken(*args, **kw):
        raise OSError("принтер не отвечает")
    monkeypatch.setattr(stock_window, "print_labels", broken)
    monkeypatch.setattr(stock_window, "_confirm", lambda *args: True)
    again = stock_window.LabelsDialog(None, db, "product", rose)
    again._print()  # не напечаталось — предложили браузер
    assert opened

    store["label_printer"] = "Microsoft Print to PDF"  # выбор запомнился
    assert stock_window.LabelsDialog(None, db).printer.currentData() == "Microsoft Print to PDF"


def test_labels_filter_without_barcode(db, store, monkeypatch):
    monkeypatch.setattr(stock_window, "printers", lambda: ([], ""))
    db.save_product(Product(0, "Кения", "Цветы", price=250, barcode="4600000000015"))
    db.save_product(Product(0, "Пион", "Цветы", price=300))
    dialog = stock_window.LabelsDialog(None, db)
    dialog.only_new.setChecked(True)
    dialog._mark_shown()
    marked = [dialog.items[i][2] for i, c in enumerate(dialog.checks) if c.isChecked()]
    assert marked == ["Пион"] and dialog.printer.currentData() == ""


def test_sale_only_by_scanner(db, store, monkeypatch):
    store["scan_sale"] = "true"
    seller = db.add_user("anna", "Анна", "1")
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250, barcode="4600000000015"))
    db.add_supply("База", [(rose, 20, 80)], None, NOW)
    w = stock_window.StockWindow(FakeMain(db, seller))
    assert w.scan_only and w.scan_note.isVisibleTo(w)
    w.grid.tiles[0].on_add(*w.grid.tiles[0].key)  # нажали плитку — не добавилось
    w.search_edit.setText("кен")
    w._refresh_catalog()
    w._add_selected()  # Enter по названию — тоже нет
    assert not w.rows and "без штрихкода" in w.scan_note.text()
    w.search_edit.setText("4600000000015")
    w._add_selected()
    assert [r.key for r in w.rows] == [("Кения", None, rose)]
    w.rows[0].qty.setValue(3)  # количество после скана поправить можно
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: shown.append(args[2]))
    w._add_free()
    assert "только у администратора" in shown[0] and len(w.rows) == 1
    w.deleteLater()


def test_supply_only_by_scanner(db, store):
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250, barcode="4600000000015"))
    editor = stock_window.LinesEditor(db.products(), with_cost=True, scan_only=True)
    editor.pick.setEditText("кения")
    editor._add_picked()
    assert editor.lines() == [] and "не добавляется" in editor.scan_note.text()
    editor.pick.setEditText("4600000000015")
    editor._add_picked()
    editor.pick.setEditText("4600000000015")
    editor._add_picked()
    assert editor.lines() == [(rose, 2, 0)]
    store["scan_supply"] = "true"
    dialog = stock_window.SupplyDialog(None, db, None, admin=True)
    assert dialog.lines.scan_only


def test_inventory_only_by_scanner(db):
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250, barcode="4600000000015"))
    db.save_product(Product(0, "Пион", "Цветы", price=300))
    db.add_supply("База", [(rose, 20, 80)], None, NOW)
    dialog = stock_window.InventoryDialog(None, db.products(), scan_only=True)
    assert all(box.isReadOnly() for box in dialog.boxes)  # вписать количество нельзя — только сканы
    for _ in range(3):
        dialog.scan_edit.setText("4600000000015")
        dialog._scanned()
    dialog._undo()  # пиканули лишний раз
    assert dialog.actual() == {rose: 2}
    dialog.scan_edit.setText("123456789")
    dialog._scanned()
    assert "нет ни у одного" in dialog.scan_note.text() and dialog.actual() == {rose: 2}


def test_writeoff_needs_matching_scan(db, store):
    store["scan_writeoff"] = "true"
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250, barcode="4600000000015"))
    dialog = stock_window.WriteOffDialog(None, db.get_product(rose))
    ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
    assert not ok.isEnabled()
    dialog.gate.edit.setText("2000000000015")
    dialog.gate._check()
    assert not ok.isEnabled() and "не тот" in dialog.gate.status.text()
    dialog.gate.edit.setText("4600000000015")
    dialog.gate._check()
    assert ok.isEnabled()
    bare = stock_window.WriteOffDialog(None, Product(0, "Без кода", "Цветы"))
    assert not bare.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).isEnabled()
    assert "нет штрихкода" in bare.gate.status.text()
    recipe = db.save_recipe(Recipe(0, "Классика", 3500, items=[(rose, 9)]))
    assert stock_window.ReadyWriteOffDialog(None, db.get_recipe(recipe)).gate is not None
    store["scan_writeoff"] = "false"
    assert stock_window.WriteOffDialog(None, db.get_product(rose)).gate is None


def test_phone_writeoff_refused_when_scan_only(db):
    db.link_work_phone(db.work_phone_code(), "555", NOW)
    flow = ClientFlow(db, "Страна Чудес")
    flow.scan_writeoff = True
    res = flow.handle(Incoming("555", "", name="Иван", callback="kw:"), NOW)
    assert "только сканером" in res.replies[0].text and "555" not in flow.staff_state


def test_weekly_count_rotation_and_shortage(db, monkeypatch):
    from datetime import timedelta
    from app.stock import week_key
    anna, olga = db.add_user("anna", "Анна", "1").id, db.add_user("olga", "Ольга", "1").id
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250, barcode="4600000000015"))
    tape = db.save_product(Product(0, "Лента", "Упаковка", price=50, barcode="4600000000022"))
    bare = db.save_product(Product(0, "Без кода", "Цветы", price=50))
    db.add_supply("База", [(rose, 20, 80), (tape, 5, 10), (bare, 3, 10)], None, NOW)
    week = week_key(NOW.date())
    first = db.assign_weekly_count(week, [anna, olga], NOW)
    assert first.user_id == anna and db.assign_weekly_count(week, [olga], NOW).user_id == anna  # уже назначена
    nxt = week_key(NOW.date() + timedelta(days=7))
    assert db.assign_weekly_count(nxt, [anna, olga], NOW + timedelta(days=7)).user_id == olga  # по очереди

    dialog = stock_window.InventoryDialog(None, db.products(), weekly=True)
    assert dialog.scan_only and dialog.table.isColumnHidden(2)  # «по программе» не видно
    for _ in range(18):
        dialog.scan_edit.setText("4600000000015")
        dialog._scanned()
    asked = []
    monkeypatch.setattr(stock_window, "_confirm", lambda parent, title, text: asked.append(text) or False)
    dialog.accept()  # ленту не отсканировали — переспросили, «Нет» — назад досканировать
    assert "Лента" in asked[0] and "Без кода" not in asked[0] and dialog.result() == 0
    monkeypatch.setattr(stock_window, "_confirm", lambda *args: True)
    dialog.accept()
    assert dialog.missing == [tape]
    done = db.finish_weekly_count(week, dialog.actual(), dialog.missing, anna, NOW)
    assert (done.scanned, done.missing, round(done.money)) == (1, 1, -(2 * 80 + 5 * 10))
    assert db.stock_of(rose) == 18 and db.stock_of(tape) == 0 and db.stock_of(bare) == 3


def test_scan_in_russian_layout(db):
    """Сканер «печатает» как клавиатура: при русской раскладке заводской «AB-12» приходит как «ФИ-12»."""
    from app.stock import code_in
    box = db.save_product(Product(0, "Коробка", "Упаковка", price=300, barcode="AB-12"))
    assert db.find_code("ФИ-12") == ("product", box) and db.find_code("AB-12") == ("product", box)
    assert code_in("ФИ-12", "AB-12") and not code_in("ФИ-13", "AB-12") and not code_in("", "AB-12")
    editor = stock_window.LinesEditor(db.products(), with_cost=False, scan_only=True)
    editor.pick.setEditText("ФИ-12")
    editor._add_picked()
    assert editor.lines() == [(box, 1)]


def test_label_size_offset_and_zero_price(db, store, monkeypatch, tmp_path):
    """Рулон другого размера, сдвиг под принтер, пробная с рамкой; цена 0 ₽ не печатается."""
    pdf = tmp_path / "small.pdf"
    assert print_labels([Label("x", "2000000000015", 100)], "thermal", pdf_path=str(pdf), size=(40, 30),
                        offset=(0, -3), frame=True) == 1
    box = pdf.read_bytes()
    assert b"MediaBox [0 0 113" in box  # 40×30 мм = 113×85 pt — страница нужного размера
    free = db.save_product(Product(0, "Кенделайт", "Цветы", price=0))
    printed = []
    monkeypatch.setattr(stock_window, "printers", lambda: (["Xprinter XP-365B"], "Xprinter XP-365B"))
    monkeypatch.setattr(stock_window, "print_labels", lambda labels, layout, name, **kw: printed.append((labels, kw)))
    dialog = stock_window.LabelsDialog(None, db, "product", free)
    assert "Цена 0 ₽" in dialog.summary.text()
    dialog.size_box.setCurrentIndex(dialog.size_box.findData("43x25"))
    dialog.shift_y.setValue(-3)
    dialog._print_test()
    assert printed[0][1] == {"size": (43, 25), "offset": (0.0, -3.0), "frame": True}
    assert store["label_size"] == "43x25" and store["label_offset_y"] == -3.0
    dialog._print()
    assert printed[1][0][0].price is None  # «0 ₽» не печатаем
    again = stock_window.LabelsDialog(None, db)
    assert again.size_box.currentData() == "43x25" and again.shift_y.value() == -3.0


def test_scanner_enter_does_not_press_window_buttons(db, store, monkeypatch):
    """Сканер «печатает» код и жмёт Enter: в продаже это не открывает «Своя позиция», в поставке —
    не принимает её после первого же скана, в карточке товара — не сохраняет, а ждёт следующий код."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QInputDialog
    seller = db.add_user("anna", "Анна", "1")
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250, barcode="4600000000015"))
    db.add_supply("База", [(rose, 20, 80)], None, NOW)
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (asked.append(1), ("", False))[1])
    w = stock_window.StockWindow(FakeMain(db, seller))
    w.show()
    QTest.keyClicks(w.search_edit, "4600000000015")
    QTest.keyClick(w.search_edit, Qt.Key_Return)
    assert len(w.rows) == 1 and not asked and w.isVisible()

    supply = stock_window.SupplyDialog(None, db, None, admin=True)
    supply.show()
    edit = supply.lines.pick.lineEdit()
    for _ in range(2):
        QTest.keyClicks(edit, "4600000000015")
        QTest.keyClick(edit, Qt.Key_Return)
    assert supply.isVisible() and supply.lines.lines() == [(rose, 2, 80)]

    codes = stock_window.CodesEdit(db, "")
    QTest.keyClicks(codes.edit, "4600000000022")
    QTest.keyClick(codes.edit, Qt.Key_Return)
    QTest.keyClicks(codes.edit, "AB-12")
    assert codes.text() == "4600000000022, AB-12"
    supply.reject()
    w.close()
