"""Сквозной день магазина: склад, витрина, бот, продажи, списания, сдача смены и аналитика сходятся."""

from datetime import date, datetime, time, timedelta

from app.db import Database
from app.stock import CartLine, Product, Recipe, day_range

CH = time(8, 0)


def test_whole_day_numbers_add_up(tmp_path):
    db = Database(str(tmp_path / "shop.db"))
    morning = datetime(2026, 10, 29, 9, 0)
    anna = db.add_user("anna", "Анна", "1")
    shift = db.open_shift(anna.id, 5000, morning, CH)
    rose = db.save_product(Product(0, "Кения", "Цветы", price=250))
    green = db.save_product(Product(0, "Эвкалипт", "Зелень", unit="ветка", price=120))
    paper = db.save_product(Product(0, "Бумага", "Упаковка", unit="лист", price=150))
    card = db.save_product(Product(0, "Открытка", "Сопутствующие", price=100))
    db.add_supply("База", [(rose, 100, 80), (green, 20, 30), (paper, 30, 20), (card, 10, 25)], anna.id, morning)
    classic = db.save_recipe(Recipe(0, "Классика", 3500, items=[(rose, 15), (green, 3), (paper, 2)]))
    unit_cost = 15 * 80 + 3 * 30 + 2 * 20  # 1330

    db.assemble(classic, 3, anna.id, shift.id, morning)  # три на витрину — и в бот
    item = db.showcase(only_active=True)[0]
    assert item.stock == 3

    t = morning + timedelta(hours=1)
    db.add_sale([CartLine("Классика", 1, 3500, recipe_id=classic), CartLine("Открытка", 1, 100, product_id=card)],
                "cash", anna.id, shift.id, t, discount=100, adjust_note="Постоянный клиент")  # 3500
    db.add_sale([CartLine("Кения", 5, 250, product_id=rose)], "card", anna.id, shift.id, t)  # 1250
    cancelled = db.add_sale([CartLine("Кения", 2, 250, product_id=rose)], "card", anna.id, shift.id, t)
    db.cancel_sale(cancelled, "ошиблись", anna.id, t)

    db.touch_client("42", "Иван", "", t)
    order = db.add_client_order("42", t, t.date(), "18:00", "Классика — 3 500 ₽", "Ленина 1", "Маша", "", "",
                                item.id)
    assert db.get_showcase_item(item.id).stock == 1  # 3 − 1 продали − 1 забронирован
    db.price_client_order(order.id, 4000, 500, t)
    db.confirm_client_order(order.id, anna.id, t, shift.id)  # 3500 по QR, доставка 500 — отдельно

    db.write_off_ready(classic, 1, "Завяло", anna.id, shift.id, t)  # последний собранный завял
    assert db.showcase(only_active=True) == [] and db.ready_of(classic) == 0
    db.write_off(green, 2, "Разбито", anna.id, shift.id, t)

    # склад сходится: розы 100 − 45 (сборка) − 5 (продажа), отменённые 2 вернулись
    assert db.stock_of(rose) == 50 and db.stock_of(green) == 20 - 9 - 2 and db.stock_of(paper) == 24
    assert db.stock_of(card) == 9

    s = db.shift_sales(shift.id)
    assert (s.count, s.revenue, s.cash, s.card, s.qr) == (3, 3500 + 1250 + 3500, 3500, 1250, 3500)
    assert s.flowers == round(3500 * 3500 / 3600) + 1250 + 3500
    assert s.writeoffs == {"Завяло": unit_cost, "Разбито": 60}

    closed = db.close_shift(shift.id, t + timedelta(hours=10), closing_cash=8500, cash_sales=s.cash,
                            courier_cash=0, revenue=s.revenue, flowers=s.flowers, terminal=s.card, qr=s.qr,
                            deliveries=500, writeoff_items=s.writeoffs)
    assert closed.writeoff == unit_cost + 60

    report = db.sales_report(*day_range(date(2026, 10, 29), date(2026, 10, 29)))
    assert report.revenue == s.revenue and report.count == 3
    assert report.cost == unit_cost + 25 + 5 * 80 + unit_cost  # два букета с витрины, открытка, 5 роз
    assert report.adjust_notes == {"Скидка: Постоянный клиент": 100}
    assert ("Классика", 2, 3500 + 3500) in report.top  # из бота — тем же названием, без «— 3 500 ₽»
    assert db.writeoffs_between(*day_range(date(2026, 10, 29), date(2026, 10, 29))) == s.writeoffs
    assert db.stock_value() == 50 * 80 + 9 * 30 + 24 * 20 + 9 * 25
    db.close()
