"""Окно «Продажи и склад»: продажа через чек, списание, аналитика; сдача смены берёт суммы из продаж."""

from datetime import date, datetime, time, timedelta

import pytest
from PySide6.QtWidgets import QWidget

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
    w.search_edit.setText("букет")
    assert w.catalog.rowCount() == 1
    w._add_selected()
    w._add_selected()  # второй раз — тот же букет, количество 2
    assert w.cart.rowCount() == 1 and w.cart.cellWidget(0, 1).value() == 2
    w.search_edit.setText("крафт")
    w._add_selected()
    w.discount_box.setValue(200)
    w._sell("card")
    assert w.cart.rowCount() == 0 and w.today_table.rowCount() == 1
    assert db.stock_of(db.products()[1].id) == 12  # роза: 30 − 2 × 9 (первым в списке идёт крафт)
    assert main.log[-1][0] == "Продажа" and "5 750" in main.log[-1][1]

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
