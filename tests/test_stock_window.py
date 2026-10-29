"""Окно «Продажи и склад»: продажа через чек, списание, аналитика; сдача смены берёт суммы из продаж."""

from datetime import date, datetime, time, timedelta

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from app.db import Database
from app.shift_dialogs import CloseShiftDialog
from app.stock import Product, Recipe
from app.stock_window import StockWindow, delta_text, period_range, previous_range

CH = time(8, 0)


class FakeMain(QWidget):
    def __init__(self, db, user, shift):
        super().__init__()
        self.db, self.user, self.shift, self.log = db, user, shift, []

    def audit(self, action, details=""):
        self.log.append((action, details))

    def sale_shift_id(self):
        return self.shift.id if self.shift else None


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def main(db):
    user = db.add_user("boss", "Ольга", "1", is_admin=True)
    shift = db.open_shift(user.id, 5000, datetime.now() - timedelta(minutes=30), CH)
    rose = db.save_product(Product(0, "Роза", "Цветы", price=250))
    paper = db.save_product(Product(0, "Крафт", "Упаковка", price=150))
    db.add_supply("База", [(rose, 30, 90), (paper, 10, 20)], user.id, datetime.now() - timedelta(hours=1))
    db.save_recipe(Recipe(0, "Букет из 9 роз", 2900, items=[(rose, 9), (paper, 1)]))
    return FakeMain(db, user, shift)


def test_periods():
    today = date(2026, 10, 29)
    assert period_range("7 дней", today) == (date(2026, 10, 23), today)
    assert period_range("Прошлый месяц", today) == (date(2026, 9, 1), date(2026, 9, 30))
    assert previous_range(date(2026, 10, 23), today) == (date(2026, 10, 16), date(2026, 10, 22))
    assert delta_text(120, 100)[0].startswith("▲ 20 %") and "0" in delta_text(5, 0)[0]
    assert delta_text(80, 100, money_like=False)[1] != delta_text(80, 100)[1]  # меньше списаний — хорошо


def test_sell_from_window_and_close_shift(main, db):
    w = StockWindow(main)
    assert [c.text() for c in w.chips.buttons()] == ["Все", "💐 Букеты", "Цветы", "Упаковка"]
    assert len(w.grid.tiles) == 3
    w.search_edit.setText("букет")
    w._refresh_catalog()  # поиск ждёт, пока допечатают
    assert len(w.grid.tiles) == 1
    w._add_selected()
    w.grid.tiles[0].on_add(*w.grid.tiles[0].key)  # плитка — тот же букет, количество 2
    assert len(w.rows) == 1 and w.rows[0].qty.value() == 2
    w.search_edit.setText("крафт")
    w._add_selected()
    w.set_adjust(200, "Постоянный клиент")
    assert "Скидка" in w.adjust_label.text() and "Постоянный клиент" in w.adjust_label.text()
    assert "5 750" in w.total_label.text()
    w._sell("card")
    assert not w.rows and w.adjust == 0 and w.today_table.rowCount() == 1
    assert w.chips.buttons()[0].text() == "⭐ Частые" and w.chips.checkedButton().text() == "Все"  # выбор не сбили
    fresh = StockWindow(main)
    assert fresh.chips.checkedButton().text() == "⭐ Частые" and len(fresh.grid.tiles) == 2
    fresh.deleteLater()
    assert db.stock_of(db.products()[1].id) == 12  # роза: 30 − 2 × 9 (первым в списке идёт крафт)
    assert main.log[-1][0] == "Продажа" and "5 750" in main.log[-1][1] and "Постоянный клиент" in main.log[-1][1]

    w.tabs.setCurrentIndex(1)
    w.stock_table.selectRow(0)  # «Крафт»: раздел «Упаковка» по алфавиту раньше «Цветов»
    p = db.get_product(w._selected_product_id())
    db.write_off(p.id, 1, "Завяло", main.user.id, main.shift.id, datetime.now())
    w.tabs.setCurrentIndex(w.analytics_index)
    assert "5 750" in w.cards["revenue"].value.text()

    dlg = CloseShiftDialog(None, db, main.shift, CH, 30)
    assert (dlg.revenue_box.value(), dlg.terminal_box.value(), dlg.sales_box.value()) == (5750, 5750, 0)
    assert dlg.flowers_box.value() == round(5800 * 5750 / 5950)  # цветы — букеты, без крафта
    assert dlg.writeoff_boxes["Завяло"].value() == round(p.avg_cost)
    dlg.deleteLater()
    w.deleteLater()


def test_staff_sees_no_costs(main, db):
    main.user = db.add_user("anna", "Анна", "1")
    w = StockWindow(main)
    assert not w.tabs.isTabVisible(w.analytics_index) and not w.add_product_btn.isVisibleTo(w)
    w.tabs.setCurrentIndex(1)
    assert w.stock_table.item(0, 4).text() == "—"
    w.deleteLater()


def test_supply_and_recipe_dialogs(main, db):
    from app.stock_window import RecipeDialog, SupplyDialog
    rose = db.products()[1]
    dlg = SupplyDialog(None, db, main.user.id)
    dlg.supplier_box.setCurrentText("Голландия")
    dlg.lines.add(rose.id, 20, 110)
    dlg.lines.add(rose.id, 5)  # та же роза — прибавилось к строке
    assert dlg.lines.lines() == [(rose.id, 25, 110)] and "2 750" in dlg.lines.total.text()
    dlg.accept()
    assert db.stock_of(rose.id) == 55 and db.supplies(1)[0].supplier == "Голландия"
    recipe = db.recipes()[0]
    edit = RecipeDialog(None, db, recipe)
    assert "Себестоимость" in edit.cost_label.text()
    edit.price_box.setValue(3100)
    edit.accept()
    assert db.get_recipe(recipe.id).price == 3100
    dlg.deleteLater()
    edit.deleteLater()


def test_markup_and_change_total(main, db, monkeypatch):
    w = StockWindow(main)
    w.search_edit.setText("крафт")
    w._add_selected()
    w.rows[0].qty.setValue(2)
    w.set_adjust(-100, "Срочно")  # наценка
    assert "Наценка: +100" in w.adjust_label.text() and "400" in w.total_label.text()
    w._sell("cash")
    sale = db.sales_between(datetime.now() - timedelta(minutes=1), datetime.now() + timedelta(minutes=1))[0]
    assert (sale.total, sale.discount, sale.adjust_note) == (400, -100, "Срочно")
    assert "наценка 100 ₽ — Срочно" in w.today_table.item(0, 1).text()
    from app.stock_window import AdjustDialog, initials
    dlg = AdjustDialog(None, "discount", 2000)
    dlg.kind_box.setCurrentIndex(1)
    dlg.value_box.setValue(10)
    assert dlg.amount() == 200 and "1 800" in dlg.result_label.text()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    dlg.note_box.setCurrentText("")
    dlg.accept()  # без причины не применить
    assert warned and "причину" in warned[0] and dlg.result() != QDialog.Accepted
    total = AdjustDialog(None, "total", 2000)
    total.value_box.setValue(2300)
    assert total.amount() == -300 and "наценка 300" in total.result_label.text()
    markup = AdjustDialog(None, "markup", 2000)
    markup.value_box.setValue(150)
    assert markup.amount() == -150 and markup.note_box.itemText(0) == "Срочно"
    assert initials("Гелий") == "ГЕ" and initials("Хриз Алтай") == "ХА" and initials("Букет «Нежность»") == "БН"
    db.set_photo("product", db.products()[0].id, b"not an image")  # битое фото — плитка с буквами, без падения
    w._refresh_catalog()
    dlg.deleteLater()
    w.deleteLater()


def test_assemble_disassemble_from_window(main, db, monkeypatch):
    from app.stock_window import AssembleDialog, DisassembleDialog, ReadyWriteOffDialog
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    w = StockWindow(main)
    w.tabs.setCurrentIndex(3)
    w.recipes_table.selectRow(0)
    recipe = db.recipes()[0]
    rose = db.products()[1]
    monkeypatch.setattr(AssembleDialog, "exec", lambda self: (self.qty_box.setValue(2), QDialog.Accepted)[1])
    w._assemble()
    assert db.ready_of(recipe.id) == 2 and db.stock_of(rose.id) == 30 - 18
    assert w.recipes_table.item(0, 3).text() == "2" and main.log[-1] == ("Букет собран на витрину",
                                                                          "Букет из 9 роз × 2")
    assert "Цветы: Роза × 9" in w.recipes_table.item(0, 1).text()
    shown = db.showcase(only_active=True)
    assert len(shown) == 1 and shown[0].stock == 2  # и в боте

    other = db.save_recipe(Recipe(0, "Мини", 1500, items=[(rose.id, 5)]))

    def disassemble(self):
        self.broken_boxes[0][2].setValue(1)  # одна роза сломана
        self.rebuild.setChecked(True)
        self.target_box.setCurrentIndex(self.target_box.findData(other))
        return QDialog.Accepted
    monkeypatch.setattr(DisassembleDialog, "exec", disassemble)
    w._disassemble()
    assert db.ready_of(recipe.id) == 1 and db.ready_of(other) == 1
    assert db.stock_of(rose.id) == 12 + 9 - 1 - 5
    assert "пересобрали в «Мини»" in main.log[-1][1] and "Роза × 1" in main.log[-1][1]

    monkeypatch.setattr(ReadyWriteOffDialog, "exec", lambda self: QDialog.Accepted)
    w._write_off_ready()
    assert db.ready_of(recipe.id) == 0 and main.log[-1][0] == "Букет списан с витрины"
    w.tabs.setCurrentIndex(0)
    tile = next(t for t in w.grid.tiles if t.key == ("recipe", other))
    assert any("готово: 1" in lbl.text() for lbl in tile.findChildren(type(w.adjust_label)))
    w.deleteLater()


def test_import_button(main, db, tmp_path, monkeypatch):
    from app.stock_window import ImportDialog
    path = tmp_path / "t.csv"
    path.write_text("Название;Раздел;Цена;Закупочная цена;Остаток\nАстильба;Цветы;350;120;12\nРоза;Цветы;260;;\n",
                    encoding="utf-8")
    seen = []
    monkeypatch.setattr(ImportDialog, "exec", lambda self: seen.append(self.windowTitle()) or QDialog.Accepted)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: seen.append(a[2]))
    w = StockWindow(main)
    w._import(str(path))
    assert seen[-1] == "Готово: добавлено 1, обновлено 1."
    assert next(p for p in db.products() if p.name == "Астильба").stock == 12
    assert next(p for p in db.products() if p.name == "Роза").price == 260
    assert main.log[-1][0] == "Товары загружены из таблицы"
    w.deleteLater()


def test_keyboard_sale(main, db):
    w = StockWindow(main)
    w.tabs.setCurrentIndex(1)
    w.start_sale()  # F2: на продажу, курсор в поиске
    assert w.tabs.currentIndex() == 0
    w.search_edit.setText("букет")
    w._add_selected()  # Enter
    w._sell_key("qr")  # F7
    sale = db.sales_between(datetime.now() - timedelta(minutes=1), datetime.now() + timedelta(minutes=1))[0]
    assert (sale.total, sale.payment) == (2900, "qr") and not w.rows
    w._sell_key("cash")  # пустая корзина — ничего
    assert len(db.sales_between(datetime.now() - timedelta(minutes=1), datetime.now() + timedelta(minutes=1))) == 1
    assert "F5" in w.pay_buttons[0].text() and "F7" in w.pay_buttons[2].text()
    w.deleteLater()


def test_sale_asks_when_stock_is_short(main, db, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    w = StockWindow(main)
    w.search_edit.setText("крафт")
    w._add_selected()
    w.rows[0].qty.setValue(4100)  # вписали цену в количество
    assert w.rows[0].qty.suffix() == " шт"
    w._sell("cash")
    assert asked and "Крафт: на складе 10 шт, нужно 4 100 шт" in asked[0]
    assert w.rows and db.stock_of(db.products()[0].id) == 10  # отказались — ничего не продано
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w._sell("cash")
    assert not w.rows and db.get_product(db.products()[0].id).stock == -4090
    w.tabs.setCurrentIndex(1)
    assert w.stock_table.item(0, 2).text() == "−4 090 шт"
    w.deleteLater()


def test_shortages_for_reserved_and_recipe(main, db):
    from app.stock import CartLine
    recipe = db.recipes()[0]  # 9 роз и крафт
    assert db.shortages([CartLine("", 3, 0, recipe_id=recipe.id)]) == []  # 27 роз из 30 — хватает
    assert db.shortages([CartLine("", 4, 0, recipe_id=recipe.id)]) == ["Роза: на складе 30 шт, нужно 36 шт"]
    db.assemble(recipe.id, 1, None, None, datetime.now())
    item = db.showcase()[0]
    db.touch_client("1", "", "", datetime.now())
    db.add_client_order("1", datetime.now(), date.today(), "", "", "", "", "", "", item.id)
    assert "заказаны в боте" in db.shortages([CartLine("", 1, 0, recipe_id=recipe.id)])[0]


def test_many_products_shown_by_pages(main, db):
    from app.stock_window import TILE_PAGE
    for i in range(TILE_PAGE + 5):
        db.save_product(Product(0, f"Шар н{i}", "Сопутствующие", price=100))
    w = StockWindow(main)
    w.chips.buttons()[0].click()  # «Все»
    assert len(w.grid.tiles) == TILE_PAGE and "осталось" in w.grid.more.text()
    w.grid.more.click()
    assert len(w.grid.tiles) == len(w.grid.data) and w.grid.more.isHidden()
    w.search_edit.setText("шар н4")
    w._add_selected()  # Enter до окончания паузы — всё равно ищет и добавляет
    assert w.rows and w.rows[0].key[0].startswith("Шар н4")
    w.deleteLater()


def test_barcode_reorder_inventory_and_time_charts(main, db, monkeypatch):
    from app.stock_window import InventoryDialog, ProductDialog, ReorderDialog
    paper = db.products()[0]
    paper.barcode = "4600000000017"
    db.save_product(paper)
    w = StockWindow(main)
    w.search_edit.setText("4600000000017")  # сканер «напечатал» код и Enter
    w._add_selected()
    assert len(w.rows) == 1 and w.rows[0].key[2] == paper.id and w.search_edit.text() == ""

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    def new_product(self):
        assert self.barcode_edit.text() == "2000000000015"
        self.name_edit.setText("Шар сердце")
        self.price_box.setValue(300)
        self.accept()
        return QDialog.Accepted
    monkeypatch.setattr(ProductDialog, "exec", new_product)
    w.search_edit.setText("2000000000015")  # незнакомый код — завести товар и сразу в корзину
    w._add_selected()
    assert db.find_barcode("2000000000015").name == "Шар сердце" and len(w.rows) == 2

    db.write_off(paper.id, 9, "Брак поставки", None, None, datetime.now())  # крафта остался 1
    dialog = ReorderDialog(None, db.reorder(datetime.now()))
    assert "• Крафт — " in dialog.text()
    dialog.boxes[next(i for i, x in enumerate(dialog.lines) if x.product.id == paper.id)].setValue(25)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    dialog._copy()
    from PySide6.QtWidgets import QApplication
    assert "Крафт — 25 шт" in QApplication.clipboard().text()

    inv = InventoryDialog(None, db.products())
    k = next(i for i, p in enumerate(inv.products) if p.id == paper.id)
    inv.boxes[k].setValue(3)  # крафта на самом деле 3, остальное не считали
    assert inv.actual() == {paper.id: 3} and "излишек" in inv.total.text()
    monkeypatch.setattr(InventoryDialog, "exec", lambda self: (self.boxes[k].setValue(0), QDialog.Accepted)[1])
    w._inventory()
    assert db.stock_of(paper.id) == 0 and main.log[-1][0] == "Инвентаризация списком"

    w.tabs.setCurrentIndex(w.analytics_index)
    assert w.hours_chart.values and len(w.days_chart.values) == 7 and w.stale_table.rowCount() >= 1
    for d in (dialog, inv):
        d.deleteLater()
    w.deleteLater()
