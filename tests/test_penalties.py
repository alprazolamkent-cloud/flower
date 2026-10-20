"""Списания цветов, штрафные санкции и условия премии."""

import json
import sys
from datetime import date, datetime, time, timedelta

import pytest
import shiboken6
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import settings_dialog, telegram
from app.bot_commands import BotContext, BotHandler, parse_command
from app.db import Database, UserError
from app.salary import (
    DEFAULT_PENALTY_KINDS, PenaltyKind, SalaryRules, ShopWriteoff, Tier, calculate, shop_writeoff,
    writeoff_percent,
)
from app.shifts import WaterSchedule

CH = time(8, 0)
NOW = datetime(2026, 10, 20, 15, 0)
KINDS = [PenaltyKind(1, "Грязная вода (не заменена вода)", 500), PenaltyKind(2, "Витрина (вялые букеты)", 500),
         PenaltyKind(3, "Не расставлена витрина", 500), PenaltyKind(4, "Нет ценников", 1000)]


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def closed_shift(db, user, day, revenue=40000, flowers=36000, writeoff=0, closing=17000):
    s = db.open_shift(user.id, 5000, datetime.combine(day, time(8, 5)), CH)
    return db.close_shift(s.id, datetime.combine(day + timedelta(days=1), time(7, 50)), closing, 12000, 0,
                          revenue=revenue, flowers=flowers, writeoff=writeoff)


# ----- списания -----

def test_writeoff_percent_both_bases():
    # продано цветов на 85 000, списано на 15 000 — пример из обсуждения
    assert writeoff_percent(15000, 85000, "total") == pytest.approx(15.0)  # 15 000 ÷ 100 000
    assert writeoff_percent(15000, 85000, "sales") == pytest.approx(17.647, rel=1e-3)  # 15 000 ÷ 85 000
    assert writeoff_percent(0, 0, "total") == 0.0 and writeoff_percent(500, 0, "sales") == 100.0


def test_writeoff_saved_with_shift(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 1), writeoff=3000)
    assert db.get_shift(s.id).writeoff == 3000
    with pytest.raises(UserError):
        s2 = db.open_shift(anna.id, 0, datetime(2026, 10, 3, 8), CH)
        db.close_shift(s2.id, datetime(2026, 10, 4, 7), 0, 0, 0, writeoff=-1)


def test_shop_writeoff_counts_only_closed_shifts(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    closed_shift(db, anna, date(2026, 10, 1), flowers=30000, writeoff=3000)
    closed_shift(db, oleg, date(2026, 10, 2), flowers=20000, writeoff=1000)
    db.open_shift(anna.id, 0, datetime(2026, 10, 5, 8, 0), CH)  # не сдана
    shop = shop_writeoff(db.shifts_between(date(2026, 10, 1), date(2026, 10, 31)))
    assert (shop.writeoff, shop.flowers) == (4000, 50000)


# ----- санкции в базе -----

def test_penalty_lifecycle(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 20))
    p = db.add_penalty(s.id, 1, "Грязная вода", 500, "Мария (Telegram)", NOW)
    assert (p.user_id, p.amount, p.created_by, p.shift_day, p.user_name) == (anna.id, 500, "Мария (Telegram)", date(2026, 10, 20), "Анна")
    assert db.add_penalty(s.id, 1, "Грязная вода", 500, "Игорь (Telegram)", NOW) is None  # второй администратор — повтор
    assert db.add_penalty(s.id, 4, "Нет ценников", 1000, "Игорь", NOW) is not None  # другая санкция — можно
    assert [x.title for x in db.penalties_for_shift(s.id)] == ["Грязная вода", "Нет ценников"]
    assert db.delete_penalty(p.id).title == "Грязная вода" and db.delete_penalty(p.id) is None
    with pytest.raises(UserError):
        db.add_penalty(999, 1, "x", 100, "x", NOW)


def test_penalties_belong_to_shift_month_and_employee(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    last_oct = closed_shift(db, anna, date(2026, 10, 31))
    first_nov = closed_shift(db, anna, date(2026, 11, 1))
    other = closed_shift(db, oleg, date(2026, 10, 15))
    # санкцию за октябрьскую смену поставили уже в ноябре — она всё равно октябрьская
    db.add_penalty(last_oct.id, 1, "Вода", 500, "М", datetime(2026, 11, 2, 9, 0))
    db.add_penalty(first_nov.id, 2, "Витрина", 500, "М", datetime(2026, 11, 2, 9, 0))
    db.add_penalty(other.id, 3, "Ценники", 1000, "М", NOW)
    october = db.penalties_between(date(2026, 10, 1), date(2026, 10, 31))
    assert sorted(p.title for p in october) == ["Вода", "Ценники"]
    assert [p.title for p in db.penalties_between(date(2026, 10, 1), date(2026, 10, 31), anna.id)] == ["Вода"]
    assert db.last_shift_of(anna.id).id == first_nov.id and db.last_shift_of(999) is None


def test_deleted_employee_keeps_penalty_history(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 20))
    db.add_penalty(s.id, 1, "Вода", 500, "М", NOW)
    db.delete_user(anna.id)
    [p] = db.penalties_between(date(2026, 10, 1), date(2026, 10, 31))
    assert p.user_id is None and p.title == "Вода"


# ----- условия премии -----

def month_shifts(db, user, count=10, revenue=40000):
    return [closed_shift(db, user, date(2026, 10, 1) + timedelta(days=2 * i), revenue=revenue,
                         flowers=min(36000, revenue)) for i in range(count)]


def penalty_list(db, shift, n):
    for kind in KINDS[:n]:
        db.add_penalty(shift.id, kind.id, kind.title, kind.amount, "М", NOW)
    return db.penalties_between(date(2026, 10, 1), date(2026, 10, 31), shift.user_id)


RULES = SalaryRules(shift_rate=2000, tiers=[Tier(30000, 5)], max_penalties=3, max_writeoff_percent=15)


def test_bonus_paid_when_conditions_met(db):
    anna = db.add_user("anna", "Анна", "1")
    shifts = month_shifts(db, anna)
    penalties = penalty_list(db, shifts[0], 2)  # 2 санкции — меньше 3
    shop = ShopWriteoff(writeoff=10000, flowers=90000)  # 10%
    sal = calculate(shifts, [], RULES, penalties, shop)
    assert sal.bonus == 20000 and sal.blocked == [] and sal.bonus_earned == 20000  # 5% с 400 000
    assert sal.penalties_total == 1000 and sal.writeoff_percent == pytest.approx(10.0)
    assert sal.total == 20000 + 20000 - 1000  # ставка + премия − санкции


def test_three_penalties_cancel_bonus(db):
    anna = db.add_user("anna", "Анна", "1")
    shifts = month_shifts(db, anna)
    penalties = penalty_list(db, shifts[0], 3)  # ровно 3 — уже не «меньше 3»
    sal = calculate(shifts, [], RULES, penalties, ShopWriteoff(1000, 99000))
    assert sal.bonus == 0 and sal.bonus_earned == 20000
    assert "штрафных санкций за месяц 3" in sal.blocked[0]
    assert sal.total == 20000 - 1500  # ставка − санкции (500 + 500 + 500), премии нет


def test_writeoff_over_limit_cancels_bonus_for_everyone():
    anna_shifts = []  # смены не нужны для проверки правила
    from types import SimpleNamespace
    shift = SimpleNamespace(closed_at=NOW, revenue=40000, flowers=36000, writeoff=0, day=NOW.date(),
                            difference=lambda: 0, user_id=1)
    rules = SalaryRules(shift_rate=0, tiers=[Tier(30000, 5)])
    for amount, expected_bonus in ((14999, 2000), (15000, 0), (30000, 0)):  # 15% — граница: «меньше» строго
        sal = calculate([shift], [], rules, [], ShopWriteoff(writeoff=amount, flowers=100000 - amount))
        assert sal.bonus == expected_bonus, amount
    assert anna_shifts == []


def test_both_writeoff_bases_decide_differently():
    from types import SimpleNamespace
    shift = SimpleNamespace(closed_at=NOW, revenue=40000, flowers=36000, writeoff=0, day=NOW.date(),
                            difference=lambda: 0, user_id=1)
    shop = ShopWriteoff(writeoff=15000, flowers=85000)  # 15,0% от всех цветов, но 17,6% от проданных
    total = calculate([shift], [], SalaryRules(tiers=[Tier(30000, 5)], writeoff_base="total", max_writeoff_percent=15.5), [], shop)
    sales = calculate([shift], [], SalaryRules(tiers=[Tier(30000, 5)], writeoff_base="sales", max_writeoff_percent=15.5), [], shop)
    assert total.bonus > 0 and sales.bonus == 0
    assert sales.writeoff_percent == pytest.approx(17.647, rel=1e-3)


def test_checks_can_be_switched_off(db):
    anna = db.add_user("anna", "Анна", "1")
    shifts = month_shifts(db, anna)
    penalties = penalty_list(db, shifts[0], 4)
    rules = SalaryRules(shift_rate=0, tiers=[Tier(30000, 5)], max_penalties=0, max_writeoff_percent=0)
    sal = calculate(shifts, [], rules, penalties, ShopWriteoff(90000, 10000))
    assert sal.bonus == 20000 and sal.blocked == []  # 0 — «не проверять»; санкции всё равно вычитаются
    assert sal.penalties_total == 2500 and sal.writeoff_percent is None


def test_no_bonus_reason_hidden_when_plan_not_met(db):
    anna = db.add_user("anna", "Анна", "1")
    shifts = month_shifts(db, anna, revenue=10000)  # до плана не дотянул
    sal = calculate(shifts, [], RULES, penalty_list(db, shifts[0], 3), ShopWriteoff())
    assert sal.bonus == 0 and sal.bonus_earned == 0 and sal.blocked == []  # терять нечего — не пугаем


def test_default_rules_and_catalog():
    assert SalaryRules().max_penalties == 3 and SalaryRules().max_writeoff_percent == 15.0
    assert [(k.id, k.amount) for k in DEFAULT_PENALTY_KINDS] == [(1, 500), (2, 500), (3, 500), (4, 1000)]


def test_settings_catalog_and_rules(monkeypatch):
    store = {}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: store.get(key, default))
    assert [k.title for k in settings_dialog.penalty_catalog()][:2] == ["Грязная вода (не заменена вода)", "Витрина (вялые букеты)"]
    assert settings_dialog.salary_rules().max_penalties == 3 and settings_dialog.salary_rules().max_writeoff_percent == 15
    store.update({"penalty_catalog": json.dumps([[7, "Опоздал", 300]]), "bonus_max_penalties": "2",
                  "bonus_max_writeoff": "12.5", "writeoff_base": "sales", "penalty_max_id": "9"})
    assert settings_dialog.penalty_catalog() == [PenaltyKind(7, "Опоздал", 300)]
    rules = settings_dialog.salary_rules()
    assert (rules.max_penalties, rules.max_writeoff_percent, rules.writeoff_base) == (2, 12.5, "sales")
    assert settings_dialog.penalty_max_id() == 9
    store["writeoff_base"] = "мусор"
    assert settings_dialog.salary_rules().writeoff_base == "total"  # неизвестное значение — безопасный вариант


def test_catalog_editor_keeps_ids(monkeypatch, request):
    from app.salary_dialog import PenaltyCatalogEditor
    app = QApplication.instance() or QApplication(sys.argv)
    editor = PenaltyCatalogEditor(KINDS)
    request.addfinalizer(lambda: shiboken6.delete(editor))  # иначе виджет умирает после приложения и роняет выход
    editor.max_used_id = 6  # номера 5 и 6 когда-то были и убраны — их не отдаём
    editor.table.removeRow(0)  # убрали санкцию 1
    editor._add_row(None, "Опоздал", 300)
    kinds = editor.kinds()
    assert [k.id for k in kinds] == [2, 3, 4, 7] and kinds[-1] == PenaltyKind(7, "Опоздал", 300)
    editor.table.item(0, 0).setText("Витрина (вялые)")
    assert editor.kinds()[0].id == 2 and editor.kinds()[0].title == "Витрина (вялые)"  # переименование не меняет номер
    assert app is not None


# ----- Telegram -----

def ctx_for(db, **kw):
    return BotContext(db=db, now=NOW, change=CH, grace=30, water=WaterSchedule(date(2026, 10, 18), 2),
                      rules=lambda uid: RULES, version="1", penalty_kinds=KINDS, **kw)


def test_parse_penalty_links():
    assert parse_command("/pen_12_3") == ("pen", "12 3") and parse_command("/unpen_5") == ("unpen", "5")
    assert parse_command("/pen_12_3@MyBot") == ("pen", "12 3")
    assert parse_command("/санкция Анна 2") == ("sanction", "Анна 2") and parse_command("/штрафы")[0] == "penalties"
    assert parse_command("pen_1_2") is None


def test_closed_shift_message_has_penalty_links(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 20), writeoff=3000)
    text = telegram.shift_closed_text(s, CH, False, None, [], KINDS, "за месяц по магазину 8.2%")
    assert "Списано: 3 000 ₽ (за месяц по магазину 8.2%)" in text
    assert f"/pen_{s.id}_1 Грязная вода (не заменена вода) — 500 ₽" in text and f"/pen_{s.id}_4 Нет ценников — 1 000 ₽" in text
    assert "/pen_" not in telegram.shift_closed_text(s, CH, False, None, [], [], "")  # без списка — без ссылок


def test_owner_taps_penalty_link(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 20))
    audit, changed = [], []
    ctx = ctx_for(db, audit=lambda a, t: audit.append((a, t)), penalty_changed=lambda p, on, who: changed.append((p.title, on, who)))
    answer = BotHandler().handle(f"/pen_{s.id}_4", ctx, "111", "Мария")
    assert "Санкция записана: Анна, смена 20.10 — Нет ценников, −1 000 ₽" in answer
    assert "За месяц (Анна): 1 шт. на 1 000 ₽" in answer and "Без потери премии можно ещё: 1" in answer
    assert changed == [("Нет ценников", True, "Мария")] and audit[0][0] == "Штрафная санкция поставлена"
    [p] = db.penalties_for_shift(s.id)
    assert p.created_by == "Мария (Telegram)" and f"/unpen_{p.id}" in answer
    # второй администратор нажал ту же ссылку — не штрафуем дважды
    assert "уже поставлена" in BotHandler().handle(f"/pen_{s.id}_4", ctx_for(db), "222", "Игорь")
    assert len(db.penalties_for_shift(s.id)) == 1


def test_third_penalty_warns_about_bonus(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 20))
    handler = BotHandler()
    answers = [handler.handle(f"/pen_{s.id}_{k}", ctx_for(db), "111", "Мария") for k in (1, 2, 3)]
    assert "Премию за месяц это отменяет" in answers[2] and "Премию" not in answers[0]


def test_penalty_link_errors(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 20))
    h = BotHandler()
    assert "смены уже нет" in h.handle("/pen_999_1", ctx_for(db), "1", "М")
    assert "убрали в настройках" in h.handle(f"/pen_{s.id}_77", ctx_for(db), "1", "М")
    assert db.penalties_for_shift(s.id) == []


def test_unpen_and_repeat(db):
    anna = db.add_user("anna", "Анна", "1")
    s = closed_shift(db, anna, date(2026, 10, 20))
    p = db.add_penalty(s.id, 1, "Вода", 500, "М", NOW)
    changed = []
    ctx = ctx_for(db, penalty_changed=lambda pp, on, who: changed.append(on))
    assert BotHandler().handle(f"/unpen_{p.id}", ctx, "111", "Мария").startswith("↩ Санкция снята")
    assert changed == [False] and db.penalties_for_shift(s.id) == []
    assert "уже нет" in BotHandler().handle(f"/unpen_{p.id}", ctx_for(db), "222", "Игорь")


def test_sanction_command_after_shift(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    closed_shift(db, anna, date(2026, 10, 18))
    last = closed_shift(db, anna, date(2026, 10, 19))
    closed_shift(db, oleg, date(2026, 10, 19))
    answer = BotHandler().handle("/санкция Анна 2", ctx_for(db), "111", "Мария")
    assert "Санкция записана: Анна, смена 19.10 — Витрина (вялые букеты)" in answer  # на последнюю смену Анны
    assert [p.shift_id for p in db.penalties_between(date(2026, 10, 1), date(2026, 10, 31), anna.id)] == [last.id]
    assert "Укажите сотрудника" in BotHandler().handle("/санкция", ctx_for(db), "1", "М")
    assert "Нет санкции с таким номером" in BotHandler().handle("/санкция Анна 99", ctx_for(db), "1", "М")
    assert "Такого сотрудника нет" in BotHandler().handle("/санкция Мария 1", ctx_for(db), "1", "М")


def test_penalties_overview_and_salary(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег", "1")
    shifts = [closed_shift(db, anna, date(2026, 10, 1) + timedelta(days=i), writeoff=2000) for i in range(3)]
    closed_shift(db, oleg, date(2026, 10, 10))
    for k in (1, 2, 3):
        db.add_penalty(shifts[0].id, k, KINDS[k - 1].title, KINDS[k - 1].amount, "М", NOW)
    db.add_penalty(db.last_shift_of(oleg.id).id, 4, "Нет ценников", 1000, "М", NOW)
    overview = BotHandler().handle("/штрафы", ctx_for(db), "111", "Мария")
    assert "• Анна: 3 шт. на 1 500 ₽  ❌ без премии" in overview and "• Олег: 1 шт. на 1 000 ₽  ✅ премия возможна" in overview
    assert "Списания по магазину: 6 000 ₽" in overview and "/санкция Имя Номер" in overview
    assert "/unpen_" in overview
    only_oleg = BotHandler().handle("/штрафы Олег", ctx_for(db), "111", "Мария")
    assert "Олег" in only_oleg and "• Анна" not in only_oleg
    detail = BotHandler().handle("/зарплата Анна", ctx_for(db), "111", "Мария")
    assert "Санкций за месяц: 3 (для премии меньше 3)" in detail and "Премия не начислена" in detail
    assert "(не выплачивается)" in detail  # заработанную сумму показываем, но отмечаем, что её не будет
    assert "Списания по магазину" in detail and "Санкция «Нет ценников»" not in detail  # чужая санкция не попадает
    assert "⚠ без премии" in BotHandler().handle("/зарплата", ctx_for(db), "111", "Мария")
