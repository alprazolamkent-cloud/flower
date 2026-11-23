"""Премия по ступенчатой шкале считается с выручки каждой смены, а не со среднего прихода за месяц."""

from datetime import date, datetime, time
from types import SimpleNamespace

import pytest

from app.db import Database
from app.holidays import Holidays
from app.salary import SalaryRules, Tier, bonus_for_shift, calculate, forecast


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


CH = time(8, 0)
# Та самая шкала из общих правил зарплаты: «от 40 000 — 3%, от 50 000 — 5% …»
TIERS = [Tier(40000, 3.0), Tier(50000, 5.0), Tier(70000, 7.0), Tier(100000, 10.0)]
RULES = SalaryRules(0, TIERS)


def fake(day, revenue):
    return SimpleNamespace(day=day, revenue=revenue, closed_at=datetime.combine(day, time(23)),
                           user_name="Анна", difference=lambda: 0)


@pytest.mark.parametrize("revenue,bonus", [
    (39000, 0),      # до первой ступени — премии нет
    (40000, 1200),   # 3% с 40 000
    (41000, 1230),   # 3% со всей выручки смены — сразу, без «сверх плана»
    (49999, 1500),
    (50000, 2500),   # 5% со всей выручки
    (69999, 3500),
    (70000, 4900),   # 7%
    (99999, 7000),
    (100000, 10000),  # 10%
])
def test_tier_applies_to_whole_shift_revenue(revenue, bonus):
    tier, amount = bonus_for_shift(revenue, RULES)
    assert amount == bonus
    assert (tier is not None) == (bonus > 0)


def test_two_shifts_are_paid_separately_not_by_month_average():
    sal = calculate([fake(date(2026, 10, 1), 41000), fake(date(2026, 10, 2), 1000)], [], RULES)
    assert sal.bonus == 1230  # 41 000 даёт 3%, 1 000 не дотягивает до плана
    assert [amount for _, _, _, amount in sal.shift_bonuses] == [1230, 0]
    assert sal.average == 21000 and sal.tier == Tier(40000, 3)  # среднее в расчёте не участвует
    assert sal.bonus_base == 41000
    assert forecast(sal, RULES).needed_revenue == 50000


def test_holiday_revenue_and_own_tiers():
    rules = SalaryRules(0, TIERS, holidays=Holidays(yearly={(1, 10)}))
    assert bonus_for_shift(150000, rules, date(2026, 10, 1))[1] == 0
    assert bonus_for_shift(41000, rules, date(2026, 10, 2))[1] == 1230
    own = SalaryRules(0, [Tier(20000, 10)])
    assert bonus_for_shift(41000, own)[1] == 4100  # свои ступени сотрудника работают так же


def test_general_rules_from_settings(db, monkeypatch):
    """Шкала берётся из общих правил зарплаты; старый ключ «сверх плана» больше не влияет."""
    from PySide6.QtCore import QSettings
    from app import settings_dialog

    store = {"bonus_tiers": "[[40000, 3.0], [50000, 5.0]]", "bonus_above_plan": "true"}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    rules = settings_dialog.salary_rules()
    assert not hasattr(rules, "above_plan_only")
    assert calculate([fake(date(2026, 10, 20), 41000)], [], rules).bonus == 1230


def test_close_shift_shows_and_saves_bonus(db, monkeypatch, request):
    import shiboken6
    from app import telegram
    from app.shift_dialogs import CloseShiftDialog
    from app.shifts import Shift

    monkeypatch.setattr(Shift, "can_close", lambda self, now, change, grace: True)
    anna = db.add_user("anna", "Анна", "1")
    shift = db.open_shift(anna.id, 0, datetime.now(), CH)
    dlg = CloseShiftDialog(None, db, shift, CH, 30, rules=lambda uid: RULES)
    request.addfinalizer(lambda: shiboken6.delete(dlg))

    dlg.revenue_box.setValue(41000)
    dlg.flowers_box.setValue(39000)
    dlg.terminal_box.setValue(41000)
    dlg.sales_box.setValue(0)
    dlg.cash_box.setValue(0)
    assert "1 230 ₽" in dlg.bonus_label.text()
    dlg.accept()

    closed = db.get_shift(shift.id)
    assert closed.bonus == 1230
    assert "💰 Премия за смену: 1 230 ₽" in telegram.shift_closed_text(closed, CH, False, None, [])
