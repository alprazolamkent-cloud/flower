"""Правила бонусной программы: баллы, уровни, сроки, памятные даты."""

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from app.loyalty import LOYALTY_SCHEMA, Loyalty, add_months, norm_phone

PHONE = "+7 (999) 123-45-67"
KEY = "79991234567"
NOW = datetime(2026, 11, 23, 12, 0)


@pytest.fixture
def loyalty():
    conn = sqlite3.connect(":memory:")
    conn.executescript(LOYALTY_SCHEMA)
    engine = Loyalty(conn, config={"expire_reminders": 1})
    yield engine
    conn.close()


def test_norm_phone():
    assert norm_phone(PHONE) == KEY
    assert norm_phone("8 999 123-45-67") == KEY  # 8… и +7… — один клиент, один баланс
    assert norm_phone("+7 999 123 45 67") == KEY
    assert norm_phone("123") == ""
    assert norm_phone(None) == ""


def test_add_months_is_calendar_not_30_days():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 11, 23), 6) == date(2027, 5, 23)


def test_welcome_only_once(loyalty):
    assert loyalty.welcome(PHONE, NOW) == 200
    assert loyalty.balance(KEY) == 200
    assert loyalty.has_welcome(KEY)
    assert loyalty.welcome(KEY, NOW) == 0
    assert loyalty.balance(KEY) == 200


def test_accrue_rounds_down_and_levels_grow(loyalty):
    assert loyalty.accrue(KEY, 999, now=NOW) == 49  # 5% = 49.95 → 49
    assert loyalty.accrue(KEY, 29001, now=NOW) == 1450  # уровень ещё 5%
    assert loyalty.rate(KEY, NOW) == 7  # 30 000 ₽ за 12 мес.
    assert loyalty.accrue(KEY, 1000, now=NOW) == 70
    assert loyalty.accrue(KEY, 70000, now=NOW) == 4900
    assert loyalty.rate(KEY, NOW) == 10  # 100 000 ₽
    assert loyalty.accrue(KEY, 1000, now=NOW) == 100
    assert loyalty.tier_info(KEY, NOW)["next_rate"] is None


def test_level_uses_rolling_12_months_only(loyalty):
    loyalty.accrue(KEY, 50000, now=NOW - timedelta(days=366))  # старая покупка
    assert loyalty.annual_spend(KEY, NOW) == 0
    assert loyalty.rate(KEY, NOW) == 5

    loyalty.accrue(KEY, 1000, now=NOW)
    assert loyalty.annual_spend(KEY, NOW) == 1000
    # приветственные и корректировки не двигают уровень
    loyalty.welcome(KEY, NOW)
    loyalty.adjust(KEY, 100000, note="Подарок", now=NOW)
    assert loyalty.annual_spend(KEY, NOW) == 1000


def test_spend_no_more_than_30_percent_and_balance(loyalty):
    loyalty.welcome(KEY, NOW)  # 200 баллов
    assert loyalty.max_spend(KEY, 1000, NOW) == 200  # лимит 300, но баланс 200
    assert loyalty.max_spend(KEY, 100, NOW) == 30
    assert loyalty.max_spend(KEY, 0, NOW) == 0
    assert loyalty.spend(KEY, 500, now=NOW) == 200
    assert loyalty.balance(KEY) == 0
    assert loyalty.max_spend(KEY, 10000, NOW) == 0


def test_purchase_extends_all_points(loyalty):
    loyalty.accrue(KEY, 1000, now=NOW)  # сгорели бы 24.05.2027
    loyalty.accrue(KEY, 1000, now=datetime(2027, 5, 1))
    assert loyalty.expire(datetime(2027, 5, 24)) == []
    assert loyalty.balance(KEY) == 100


def test_expire_burns_and_does_not_repeat(loyalty):
    loyalty.accrue(KEY, 1000, now=NOW)
    assert loyalty.expire(NOW) == []
    assert loyalty.expire(datetime(2027, 5, 24)) == [(KEY, 50)]
    assert loyalty.balance(KEY) == 0
    assert loyalty.expire(datetime(2027, 5, 25)) == []


def test_expire_reminders_at_30_and_7_days(loyalty):
    loyalty.accrue(KEY, 1000, now=NOW)
    before30 = loyalty.due_reminders(datetime(2027, 4, 23))
    assert len(before30) == 1 and before30[0].phone == KEY and before30[0].kind == "expire"
    assert "30 дн" in before30[0].text
    assert loyalty.due_reminders(datetime(2027, 4, 23)) == []  # не шлём дважды

    before7 = loyalty.due_reminders(datetime(2027, 5, 16))
    assert len(before7) == 1 and "7 дн" in before7[0].text


def test_memorable_date_double_points_and_reminder(loyalty):
    loyalty.set_date(PHONE, "жена", "14.02", now=NOW)
    assert loyalty.dates(KEY) == [{"label": "жена", "day": "14.02"}]
    assert loyalty.is_occasion(KEY, date(2027, 2, 13))
    assert loyalty.is_occasion(KEY, date(2027, 2, 14))
    assert loyalty.is_occasion(KEY, date(2027, 2, 15))
    assert not loyalty.is_occasion(KEY, date(2027, 2, 20))

    assert loyalty.accrue(KEY, 1000, now=NOW, doubled=True) == 100  # 5% × 2
    assert loyalty.annual_spend(KEY, NOW) == 1000  # двойные баллы не двигают уровень

    due = loyalty.due_reminders(datetime(2027, 2, 11))
    assert len(due) == 1 and due[0].kind == "date" and "жена" in due[0].text
    assert loyalty.due_reminders(datetime(2027, 2, 11)) == []


def test_set_date_replaces_label_and_validates(loyalty):
    loyalty.set_date(KEY, "жена", "14.02", now=NOW)
    loyalty.set_date(KEY, "жена", "15.02", now=NOW)
    assert loyalty.dates(KEY) == [{"label": "жена", "day": "15.02"}]
    with pytest.raises(ValueError):
        loyalty.set_date(KEY, "жена", "не дата", now=NOW)


def test_discounted_lines_are_ignored(loyalty):
    lines = [{"price": 1000, "qty": 1},
             {"price": 500, "qty": 2, "promo": True},
             {"price": 300, "qty": 1, "discounted": True}]
    assert loyalty.eligible_amount(lines) == 1000


def test_summary_is_simple_for_client(loyalty):
    loyalty.accrue(KEY, 27000, now=NOW)  # 1350 баллов, до 7% — 3000 ₽
    text = loyalty.summary(KEY, NOW)
    assert "1350 бонусов" in text
    assert "До 7% осталось 3 000 ₽" in text


def test_config_override_without_qsettings():
    conn = sqlite3.connect(":memory:")
    conn.executescript(LOYALTY_SCHEMA)
    engine = Loyalty(conn, config={"rate_base": 10, "welcome": 0, "pay_percent": 10,
                                   "expire_months": 1, "double_dates": 0})
    try:
        assert engine.welcome(KEY, NOW) == 0
        assert engine.accrue(KEY, 1000, now=NOW) == 100
        assert engine.max_spend(KEY, 1000, NOW) == 100  # 10% от 1000
        assert engine.accrue(KEY, 1000, now=NOW, doubled=True) == 100  # двойные выключены
        assert engine.due_reminders(datetime(2026, 12, 23)) == []  # в сам день сгорания напоминаний нет
        assert engine._account(KEY).expires == date(2026, 12, 23)
    finally:
        conn.close()


def test_adjust_never_below_zero(loyalty):
    loyalty.adjust(KEY, 100, note="тест")
    assert loyalty.adjust(KEY, -500, note="ошибка") == -100  # списали только то, что было
    assert loyalty.balance(KEY) == 0
    assert loyalty.adjust(KEY, -50, note="ещё") == 0  # в минус не уходит


def test_accounts_list_and_search(loyalty):
    loyalty.welcome(PHONE, NOW)
    loyalty.adjust("79160000000", 50, note="тест", now=NOW)
    assert [a["phone"] for a in loyalty.accounts()] == [KEY, "79160000000"]  # по балансу
    assert [a["phone"] for a in loyalty.accounts("916")] == ["79160000000"]
    assert loyalty.accounts("555") == []


def test_consent_and_forget(loyalty):
    assert not loyalty.has_consent(KEY)
    loyalty.set_consent(PHONE, "бот", NOW)
    assert loyalty.has_consent(KEY)
    assert loyalty.accounts()[0]["consent_at"].startswith("2026-11-23")
    loyalty.set_consent(KEY, "касса", NOW + timedelta(days=1))  # хранится первое согласие
    assert loyalty.accounts()[0]["consent_at"].startswith("2026-11-23")
    loyalty.welcome(KEY, NOW)
    loyalty.set_date(KEY, "жена", "14.02")
    assert loyalty.forget(KEY)
    assert loyalty.balance(KEY) == 0 and loyalty.dates(KEY) == [] and not loyalty.has_consent(KEY)
    assert not loyalty.forget(KEY)  # уже нечего удалять


def test_cancel_sale_reverses_bonuses():
    from app.db import Database
    from app.stock import CartLine

    db = Database(":memory:")
    try:
        loyalty = db.loyalty()
        assert loyalty.welcome(KEY, NOW) == 200
        points = loyalty.accrue(KEY, 1000, now=NOW)
        assert points == 50
        rate = loyalty.rate(KEY, NOW)
        sale_id = db.add_sale([CartLine("Букет", 1, 1000)], "card", None, None, NOW,
                              client_phone=PHONE, loyalty_rate=rate)
        db.set_sale_bonus(sale_id, points, rate)
        assert loyalty.balance(KEY) == 250
        db.cancel_sale(sale_id, "Возврат", None, NOW)
        assert loyalty.balance(KEY) == 200  # начисление снято, приветственные остались

        loyalty.spend(KEY, 100, now=NOW)
        assert loyalty.balance(KEY) == 100
        sale_id = db.add_sale([CartLine("Букет", 1, 1000)], "card", None, None, NOW,
                              client_phone=PHONE, bonus_spent=100)
        db.cancel_sale(sale_id, "Возврат", None, NOW)
        assert loyalty.balance(KEY) == 200  # баллы, заплаченные за покупку, вернулись
    finally:
        db.close()


def test_database_exposes_loyalty_schema_and_columns():
    from app.db import Database

    db = Database(":memory:")
    try:
        assert db.loyalty().welcome(KEY, NOW) == 200
        assert db.conn.execute("SELECT balance FROM loyalty_accounts WHERE phone=?", (KEY,)).fetchone()[0] == 200
        order_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(client_orders)")}
        assert {"bonus_spent", "bonus_accrued", "loyalty_rate"} <= order_cols
        sale_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(sales)")}
        assert {"client_phone", "bonus_spent", "bonus_accrued", "loyalty_rate"} <= sale_cols
    finally:
        db.close()
