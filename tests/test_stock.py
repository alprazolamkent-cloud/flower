"""Склад и продажи: поставки, средняя себестоимость, техкарты, продажи, списания, итог смены."""

from datetime import date, datetime, time

import pytest

from app.db import Database, UserError
from app.stock import CartLine, Product, Recipe, day_range

NOW = datetime(2026, 10, 29, 12, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def shop(db):
    user = db.add_user("anna", "Анна", "secret12")
    rose = db.save_product(Product(0, "Роза красная", "Цветы", price=250))
    eucalyptus = db.save_product(Product(0, "Эвкалипт", "Зелень", unit="ветка", price=100))
    paper = db.save_product(Product(0, "Крафт", "Упаковка", unit="лист", price=150, min_stock=5))
    db.add_supply("Флора-опт", [(rose, 50, 80), (eucalyptus, 20, 30), (paper, 10, 20)], user.id, NOW)
    shift = db.open_shift(user.id, 5000, NOW, time(8, 0))
    return db, user, shift, rose, eucalyptus, paper


def test_supply_average_cost(shop):
    db, user, _, rose, *_ = shop
    assert db.stock_of(rose) == 50 and db.get_product(rose).avg_cost == 80
    db.add_supply("Другой", [(rose, 50, 120)], user.id, NOW)
    p = db.get_product(rose)
    assert p.stock == 100 and p.avg_cost == 100  # (50·80 + 50·120) / 100
    assert db.suppliers() == ["Другой", "Флора-опт"]
    assert db.supplies()[0].lines == [("Роза красная", 50, 120)]


def test_duplicate_and_empty_product(db):
    db.save_product(Product(0, "Роза"))
    with pytest.raises(UserError, match="уже есть"):
        db.save_product(Product(0, "роза"))
    with pytest.raises(UserError):
        db.save_product(Product(0, "  "))


def test_sale_by_recipe_deducts_stock_and_cancel_returns(shop):
    db, user, shift, rose, eucalyptus, paper = shop
    recipe = db.save_recipe(Recipe(0, "Букет «Классика»", 3500, items=[(rose, 11), (eucalyptus, 3), (paper, 2)]))
    assert db.recipe_cost(db.get_recipe(recipe)) == 11 * 80 + 3 * 30 + 2 * 20
    lines = [CartLine("Букет «Классика»", 1, 3500, recipe_id=recipe), CartLine("Открытка", 1, 100)]
    with pytest.raises(UserError, match="почему скидка"):
        db.add_sale(lines, "card", user.id, shift.id, NOW, discount=360)
    sale = db.add_sale(lines, "card", user.id, shift.id, NOW, discount=360, adjust_note="Постоянный клиент")
    assert (db.stock_of(rose), db.stock_of(eucalyptus), db.stock_of(paper)) == (39, 17, 8)
    s = db.sales_of_shift(shift.id)[0]
    assert s.total == 3240 and s.flowers == 3150  # букет 3500 из 3600, скидка пропорционально
    assert s.cost == 1010 and s.user_name == "Анна" and "Открытка" in s.what
    assert s.adjust_text == "скидка 360 ₽ — Постоянный клиент"
    db.cancel_sale(sale, "Передумал", user.id, NOW)
    assert db.stock_of(rose) == 50 and db.shift_sales(shift.id).revenue == 0
    with pytest.raises(UserError, match="уже отменена"):
        db.cancel_sale(sale, "", user.id, NOW)


def test_stock_can_go_negative_and_is_low(shop):
    db, user, shift, rose, *_ = shop
    db.add_sale([CartLine("Роза", 60, 250, product_id=rose)], "cash", user.id, shift.id, NOW)
    p = db.get_product(rose)
    assert p.stock == -10 and p.low


def test_writeoff_count_and_shift_summary(shop):
    db, user, shift, rose, eucalyptus, paper = shop
    db.write_off(rose, 5, "Завяло", user.id, shift.id, NOW)
    db.write_off(paper, 1, "Брак поставки", user.id, shift.id, NOW)
    with pytest.raises(UserError):
        db.write_off(rose, 0, "Завяло", user.id, shift.id, NOW)
    assert db.count_stock(paper, 4, user.id, NOW) == -5  # было 9, нашли 4
    assert db.get_product(paper).low
    db.add_sale([CartLine("Роза", 3, 250, product_id=rose)], "cash", user.id, shift.id, NOW)
    db.add_sale([CartLine("Крафт", 1, 150, product_id=paper)], "qr", user.id, shift.id, NOW)
    db.add_sale([CartLine("Эвкалипт", 2, 100, product_id=eucalyptus)], "card", user.id, None, NOW)  # не в смене
    summary = db.shift_sales(shift.id)
    assert (summary.count, summary.revenue, summary.flowers) == (2, 900, 750)
    assert (summary.cash, summary.card, summary.qr) == (750, 0, 150)
    assert summary.writeoffs == {"Завяло": 400, "Брак поставки": 20}


def test_sale_validation(shop):
    db, user, shift, rose, *_ = shop
    with pytest.raises(UserError):
        db.add_sale([], "cash", user.id, shift.id, NOW)
    with pytest.raises(UserError):
        db.add_sale([CartLine("Роза", 1, 250, product_id=rose)], "bitcoin", user.id, shift.id, NOW)
    with pytest.raises(UserError):
        db.add_sale([CartLine("Роза", 1, 250, product_id=rose)], "cash", user.id, shift.id, NOW, discount=300)


def test_sales_report(shop):
    db, user, shift, rose, eucalyptus, _ = shop
    db.add_sale([CartLine("Роза красная", 5, 250, product_id=rose)], "cash", user.id, shift.id, NOW)
    db.add_sale([CartLine("Эвкалипт", 2, 100, product_id=eucalyptus)], "qr", None, None,
                datetime(2026, 10, 30, 9, 0))
    cancelled = db.add_sale([CartLine("Роза красная", 1, 250, product_id=rose)], "cash", user.id, shift.id, NOW)
    db.cancel_sale(cancelled, "", user.id, NOW)
    report = db.sales_report(*day_range(date(2026, 10, 29), date(2026, 10, 30)))
    assert (report.count, report.revenue, report.average) == (2, 1450, 725)
    assert report.profit == 1450 - 5 * 80 - 2 * 30
    assert report.by_day == {date(2026, 10, 29): 1250, date(2026, 10, 30): 200}
    assert report.by_user == {"Анна": (1, 1250), "Бот / без смены": (1, 200)}
    assert report.top[0] == ("Роза красная", 5, 1250)
    assert db.stock_value() == 45 * 80 + 18 * 30 + 10 * 20


def test_markup_note_in_report(shop):
    db, user, shift, rose, *_ = shop
    db.add_sale([CartLine("Роза", 3, 250, product_id=rose)], "cash", user.id, shift.id, NOW, discount=-300,
                adjust_note="Срочно, за 30 минут")
    db.add_sale([CartLine("Роза", 2, 250, product_id=rose)], "card", user.id, shift.id, NOW, discount=100,
                adjust_note="Постоянный клиент")
    report = db.sales_report(*day_range(NOW.date(), NOW.date()))
    assert (report.revenue, report.discount, report.markup) == (1050 + 400, 100, 300)
    assert report.adjust_notes == {"Наценка: Срочно, за 30 минут": 300, "Скидка: Постоянный клиент": 100}
    assert db.sales_of_shift(shift.id)[1].adjust_text == "наценка 300 ₽ — Срочно, за 30 минут"
