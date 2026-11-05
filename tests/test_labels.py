"""Свои и заводские штрихкоды, этикетки, сканирование товара и букета."""

import sqlite3
from datetime import datetime

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget

from app.db import Database, UserError
from app.labels import Label, barcode_svg, code128_modules, ean13_modules, labels_html
from app.stock import Product, Recipe, ean13_check, is_ean13, split_codes

NOW = datetime(2026, 11, 5, 12, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def test_ean13_and_split():
    assert ean13_check("460000000001") == "5" and is_ean13("4600000000015") and not is_ean13("4600000000017")
    assert split_codes("4 600000 000015, ABC-1;ABC-1\n2000000000015") == ["4600000000015", "ABC-1", "2000000000015"]
    assert len(ean13_modules("2000000000015")) == 95 and ean13_modules("2000000000015").startswith("101")
    assert code128_modules("ABC-123").endswith("11")  # стоп заканчивается полосой
    with pytest.raises(ValueError):
        code128_modules("роза")


def test_own_and_factory_codes(db):
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250))
    first, second = db.ensure_code("product", rose), db.new_own_code()
    assert first == "2000000000015" and is_ean13(first) and second == "2000000000022"
    assert db.ensure_code("product", rose) == first  # уже есть — тот же
    card = Product(0, "Открытка", "Сопутствующие", price=100, barcode="4600000000015, " + second)
    db.save_product(card)  # заводской и свой сразу
    assert db.find_code("4600000000015") == ("product", card.id) and db.find_code(second) == ("product", card.id)
    assert db.get_product(card.id).barcode.count(",") == 1
    with pytest.raises(UserError, match="уже у товара «Кения»"):
        db.save_product(Product(0, "Шар", barcode=first))
    with pytest.raises(UserError, match="контрольная"):
        db.save_product(Product(0, "Шар", barcode="4600000000017"))

    bouquet = Recipe(0, "Классика", 3500, items=[(rose, 9)], barcode="")
    db.save_recipe(bouquet)
    code = db.ensure_code("recipe", bouquet.id)
    assert db.find_code(code) == ("recipe", bouquet.id) and db.get_recipe(bouquet.id).barcode == code
    with pytest.raises(UserError, match="уже у букета «Классика»"):
        db.save_product(Product(0, "Шар", barcode=code))
    db.archive_recipe(bouquet.id)  # в архиве — код свободен
    assert db.find_code(code) is None
    db.save_product(Product(0, "Шар", barcode=code))
    assert db.find_code(code)[0] == "product"


def test_codes_from_previous_version_move_to_table(tmp_path):
    path = str(tmp_path / "old.db")
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO products (name, category, unit, price, barcode) VALUES ('Шар', 'Сопутствующие', 'шт', 100,"
                 " '4600000000015')")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.find_barcode("4600000000015").name == "Шар" and db.products()[0].barcode == "4600000000015"
    db.close()


def test_labels_page():
    page = labels_html([Label("Кения 60 см", "2000000000015", 250, 3), Label("Шар <сердце>", "AB-12", None, 1)],
                       "a4")
    assert page.count('class="label"') == 4 and "250 ₽" in page and "&lt;сердце&gt;" in page
    assert "size: A4" in page and "Этикеток: 4" in page
    svg = barcode_svg("2000000000015", 48, 11)
    assert svg.count("<rect") == 30 and 'width="48mm"' in svg  # у EAN-13 ровно 30 полос
    assert "58mm 40mm" in labels_html([Label("x", "2000000000015")], "thermal")


class FakeMain(QWidget):
    def __init__(self, db, user):
        super().__init__()
        self.db, self.user, self.shift, self.log = db, user, None, []

    def audit(self, action, details=""):
        self.log.append((action, details))

    def sale_shift_id(self):
        return None


def test_labels_dialog_and_scanning(db, monkeypatch):
    from app.stock_window import LabelsDialog, StockWindow
    admin = db.add_user("boss", "Ольга", "1", is_admin=True)
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250))
    db.add_supply("База", [(rose, 20, 80)], None, NOW)
    recipe = db.save_recipe(Recipe(0, "Классика", 3500, items=[(rose, 9)]))
    db.assemble(recipe, 1, None, None, NOW)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    dialog = LabelsDialog(None, db, "recipe", recipe)
    assert [c.isChecked() for c in dialog.checks].count(True) == 1
    dialog.checks[[k for k, *_ in dialog.items].index("product")].setChecked(True)
    dialog.copies[0].setValue(2)
    assert "этикеток: <b>3</b>" in dialog.summary.text()
    dialog._print()
    assert dialog.created == 2 and opened and "Этикеток: 3" in open(opened[0], encoding="utf-8").read()
    rose_code, bouquet_code = db.ensure_code("product", rose), db.ensure_code("recipe", recipe)

    w = StockWindow(FakeMain(db, admin))
    w.search_edit.setText(bouquet_code)  # пиканули этикетку на букете — с витрины, без нового списания
    w._add_selected()
    w.search_edit.setText(rose_code)
    w._add_selected()
    assert [r.key for r in w.rows] == [("Классика", recipe, None), ("Кения", None, rose)]
    w._sell("cash")
    assert db.ready_of(recipe) == 0 and db.stock_of(rose) == 20 - 9 - 1
    dialog.deleteLater()
    w.deleteLater()


def test_short_label_on_top(db, monkeypatch):
    from app.stock import short_label
    from app.stock_window import LabelsDialog
    assert short_label("Открытка большая н13") == "н13" and short_label("Конверт для денег н90-06-s") == "н90-06-s"
    assert short_label("Роза Кения 60 см") == "Роза Кения 60 см"
    card = db.save_product(Product(0, "Открытка большая н15", "Сопутствующие", price=150))
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250))
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    dialog = LabelsDialog(None, db)
    rows = {item[1]: i for i, item in enumerate(dialog.items)}
    assert dialog.tops[rows[card]].text() == "н15"
    dialog.checks[rows[card]].setChecked(True)
    dialog.checks[rows[rose]].setChecked(True)
    dialog.tops[rows[rose]].setText("Р60")  # своя подпись — запомнится
    dialog._print()
    page = open(opened[0], encoding="utf-8").read()
    assert '<div class="title">н15</div><div class="price">150 ₽</div>' in page and '"title">Р60<' in page
    assert db.get_product(rose).label_text == "Р60" and db.get_product(card).label_text == ""
    again = LabelsDialog(None, db)
    assert again.tops[rows[rose]].text() == "Р60"
