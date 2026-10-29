"""Склад и продажи: поставки, средняя себестоимость, техкарты, продажи, списания, итог смены."""

from datetime import date, datetime, time, timedelta

import pytest

from app.db import Database, UserError
from app.stock import CartLine, Product, Recipe, day_range, reorder_text

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


def test_assemble_sell_from_showcase_and_bot_sync(shop):
    db, user, shift, rose, eucalyptus, paper = shop
    recipe_id = db.save_recipe(Recipe(0, "Классика", 3500, items=[(rose, 11), (eucalyptus, 3), (paper, 1.5)]))
    assert db.showcase() == []  # пока не собран — в боте его нет
    db.assemble(recipe_id, 2, user.id, shift.id, NOW)
    assert (db.stock_of(rose), db.stock_of(eucalyptus), db.stock_of(paper)) == (28, 14, 7)  # списалось сразу
    assert db.get_recipe(recipe_id).ready == 2
    item = db.showcase(only_active=True)[0]  # сам появился в боте
    assert (item.title, item.price, item.recipe_id, item.stock) == ("Классика", 3500, recipe_id, 2)
    assert "Роза красная × 11" in item.description and "Крафт" not in item.description

    # продажа в магазине берёт собранный — цветы второй раз не списываются
    db.add_sale([CartLine("Классика", 1, 3500, recipe_id=recipe_id)], "card", user.id, shift.id, NOW)
    assert db.stock_of(rose) == 28 and db.ready_of(recipe_id) == 1
    assert db.sales_of_shift(shift.id)[0].cost == 11 * 80 + 3 * 30 + 1.5 * 20
    assert db.showcase(only_active=True)[0].stock == 1

    # заказ из бота бронирует последний — для остальных пропал
    db.touch_client("42", "Иван", "", NOW)
    order = db.add_client_order("42", NOW, NOW.date(), "15:00", "Классика", "Ленина, 1", "Маша", "", "", item.id)
    assert db.showcase(only_active=True) == [] and db.get_showcase_item(item.id).stock == 0
    db.set_client_order_status(order.id, "rejected", NOW)  # отказ — снова в наличии
    assert db.showcase(only_active=True)[0].stock == 1
    order = db.add_client_order("42", NOW, NOW.date(), "15:00", "Классика", "Ленина, 1", "Маша", "", "", item.id)
    db.price_client_order(order.id, 4000, 500, NOW)
    db.confirm_client_order(order.id, user.id, NOW, shift.id)  # продажа с витрины, без нового списания
    assert db.ready_of(recipe_id) == 0 and db.stock_of(rose) == 28 and db.showcase(only_active=True) == []

    # без собранных продажа списывает по техкарте, как раньше
    db.add_sale([CartLine("Классика", 1, 3500, recipe_id=recipe_id)], "cash", user.id, shift.id, NOW)
    assert db.stock_of(rose) == 17 and db.ready_of(recipe_id) == 0
    db.assemble(recipe_id, 1, user.id, shift.id, NOW)
    assert db.showcase(only_active=True)[0].id == item.id  # тот же пункт витрины, не дубль


def test_cancel_returns_bouquet_to_showcase(shop):
    db, user, shift, rose, *_ = shop
    recipe_id = db.save_recipe(Recipe(0, "Моно", 2900, items=[(rose, 9)]))
    db.assemble(recipe_id, 1, user.id, shift.id, NOW)
    sale = db.add_sale([CartLine("Моно", 1, 2900, recipe_id=recipe_id)], "cash", user.id, shift.id, NOW)
    db.cancel_sale(sale, "Ошиблись", user.id, NOW)
    assert db.ready_of(recipe_id) == 1 and db.stock_of(rose) == 41


def test_write_off_and_disassemble_ready(shop):
    db, user, shift, rose, eucalyptus, paper = shop
    recipe_id = db.save_recipe(Recipe(0, "Классика", 3500, items=[(rose, 10), (eucalyptus, 2), (paper, 1)]))
    db.assemble(recipe_id, 3, user.id, shift.id, NOW)
    with pytest.raises(UserError, match="только 3"):
        db.write_off_ready(recipe_id, 4, "Завяло", user.id, shift.id, NOW)
    db.write_off_ready(recipe_id, 1, "Завяло", user.id, shift.id, NOW)  # завял на витрине — целиком
    assert db.shift_sales(shift.id).writeoffs == {"Завяло": 10 * 80 + 2 * 30 + 20}
    # разобрали один: 2 розы сломаны — в списание, остальное обратно на склад
    db.disassemble(recipe_id, 1, {rose: 2}, "Разбито", user.id, shift.id, NOW)
    assert db.ready_of(recipe_id) == 1
    assert (db.stock_of(rose), db.stock_of(eucalyptus), db.stock_of(paper)) == (50 - 30 + 10 - 2, 20 - 6 + 2, 10 - 3 + 1)
    assert db.shift_sales(shift.id).writeoffs["Разбито"] == 160
    # пересобрать: разобрать и собрать по другой техкарте — недостающее со склада, лишнее остаётся там
    small = db.save_recipe(Recipe(0, "Мини", 1500, items=[(rose, 5), (paper, 1)]))
    db.disassemble(recipe_id, 1, {}, "", user.id, shift.id, NOW)
    db.assemble(small, 2, user.id, shift.id, NOW)
    assert (db.ready_of(recipe_id), db.ready_of(small)) == (0, 2)
    assert db.stock_of(rose) == 28 + 10 - 10 and db.stock_of(paper) == 8 + 1 - 2
    assert [k for _, k, _, _ in db.ready_moves_of(recipe_id)][:2] == ["disassemble", "disassemble"]
    assert db.stock_value() == round(db.stock_of(rose) * 80 + db.stock_of(eucalyptus) * 30 + db.stock_of(paper) * 20
                                     + 2 * (5 * 80 + 20))


def test_recipe_composition_and_hidden_from_bot(shop):
    db, user, shift, rose, eucalyptus, paper = shop
    r = Recipe(0, "Для своих", 1000, items=[(paper, 1.5), (eucalyptus, 2), (rose, 7)], in_bot=False)
    db.save_recipe(r)
    assert db.composition(db.get_recipe(r.id)) == [("Цветы", ["Роза красная × 7"]), ("Зелень", ["Эвкалипт × 2 ветка"]),
                                                   ("Упаковка", ["Крафт × 1,5 лист"])]
    db.assemble(r.id, 1, user.id, shift.id, NOW)
    assert db.showcase() == []  # «не показывать в боте»
    shown = db.save_recipe(Recipe(0, "В бот", 2000, items=[(rose, 5)]))
    db.assemble(shown, 1, user.id, shift.id, NOW)
    db.archive_recipe(shown)
    assert db.showcase(only_active=True) == []  # в архиве — из бота убрали


def test_barcode(shop):
    db, *_ , paper = shop
    card = db.save_product(Product(0, "Открытка", "Сопутствующие", price=100, barcode="4 600000 000017"))
    assert db.find_barcode("4600000000017").id == card and db.find_barcode("123") is None
    with pytest.raises(UserError, match="уже у товара «Открытка»"):
        db.save_product(Product(0, "Шар", barcode="4600000000017"))


def test_reorder_count_many_time_and_stale(shop):
    db, user, shift, rose, eucalyptus, paper = shop
    for day in range(14):  # по 3 розы в день две недели
        db.add_sale([CartLine("Роза", 3, 250, product_id=rose)], "cash", user.id, shift.id,
                    NOW - timedelta(days=day, hours=1))
    db.write_off(eucalyptus, 18, "Завяло", user.id, shift.id, NOW)  # эвкалипта осталось 2
    lines = {x.product.name: x for x in db.reorder(NOW)}
    assert lines["Роза красная"].per_day == 3 and lines["Роза красная"].need == 9 - 8  # осталось 8, на 3 дня — 9
    assert "Эвкалипт" in lines and "Крафт" not in lines  # крафта 10 при минимуме 5 — хватает
    text = reorder_text(list(lines.values()), NOW.date())
    assert text.startswith("Заказ на 29.10") and "• Роза красная — 1 шт" in text

    changed, diff = db.count_many({rose: 8, paper: 7, eucalyptus: 2}, user.id, NOW)
    assert (changed, diff) == (1, -3 * 20)  # крафта нашли 7 вместо 10 — недостача 60 ₽
    hours, weekdays = db.sales_by_time(*day_range(NOW.date() - timedelta(days=13), NOW.date()))
    assert hours[11] == 14 * 750 and sum(weekdays) == 14 * 750
    stale = [p.name for p, _ in db.stale(NOW)]
    assert "Крафт" in stale and "Роза красная" not in stale
    assert db.writeoff_top(*day_range(NOW.date(), NOW.date()))[0] == ("Эвкалипт", 18, 18 * 30)
