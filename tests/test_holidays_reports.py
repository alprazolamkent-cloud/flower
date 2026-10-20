"""Праздники и ставки смен, прогноз и рейтинг, причины списаний, споры о санкциях, автоматические отчёты."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from app import reports, telegram
from app.bot_commands import BotContext, BotHandler, parse_command
from app.db import Database, UserError
from app.holidays import DEFAULT_TEXT, Holidays, parse_holidays
from app.salary import SalaryRules, Tier, calculate, forecast, monthly_ranking
from app.shifts import WaterSchedule

CH = time(8, 0)
NOW = datetime(2026, 10, 20, 15, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def sh(db, user, day, revenue=40000, flowers=36000, items=None, writeoff=0):
    s = db.open_shift(user.id, 5000, datetime.combine(day, time(8, 5)), CH)
    return db.close_shift(s.id, datetime.combine(day + timedelta(days=1), time(7, 50)), 17000, 12000, 0,
                          revenue=revenue, flowers=flowers, writeoff=writeoff, writeoff_items=items)


def fake(day, revenue):
    return SimpleNamespace(day=day, revenue=revenue, closed_at=datetime.combine(day, time(23)), user_name="Анна",
                           difference=lambda: 0)


def test_parse_holidays():
    holidays, bad = parse_holidays("08.03 Женский день\n1.1.2027\nерунда\n31.02")
    assert holidays.is_holiday(date(2030, 3, 8)) and holidays.is_holiday(date(2027, 1, 1))
    assert not holidays.is_holiday(date(2028, 1, 1)) and len(bad) == 2
    default, bad = parse_holidays(DEFAULT_TEXT)
    assert not bad and default.is_holiday(date(2026, 2, 14))


def test_holiday_shift_has_own_rate_and_no_bonus_revenue():
    rules = SalaryRules(2000, [Tier(30000, 5)], holiday_rate=3000, weekend_rate=2500,
                        holidays=Holidays(yearly={(8, 3)}))
    shifts = [fake(date(2026, 3, 6), 40000),   # пятница — обычная
              fake(date(2026, 3, 7), 40000),   # суббота — выходная
              fake(date(2026, 3, 8), 90000)]   # праздник
    sal = calculate(shifts, [], rules)
    assert sal.base == 2000 + 2500 + 3000
    assert sal.holiday_shifts == 1 and sal.holiday_revenue == 90000
    assert sal.revenue == 80000 and sal.bonus_shifts == 2 and sal.average == 40000
    assert sal.bonus == 4000  # 5% только с 80 000, праздничные 90 000 не в счёт


def test_only_holiday_shifts_no_bonus():
    rules = SalaryRules(2000, [Tier(30000, 5)], holidays=Holidays(yearly={(8, 3)}))
    sal = calculate([fake(date(2026, 3, 8), 90000)], [], rules)
    assert sal.bonus == 0 and sal.tier is None and sal.base == 2000


def test_forecast_and_ranking():
    rules = SalaryRules(2000, [Tier(30000, 5), Tier(40000, 7)])
    shifts = [fake(date(2026, 10, d), 32000) for d in (1, 5, 10)]
    sal = calculate(shifts, [], rules)
    plan = forecast(sal, rules, date(2026, 10, 15))
    assert plan.target.per_shift == 40000 and plan.remaining_shifts == 3
    # (40000 × 6 − 96000) / 3 = 48000
    assert plan.needed_average == 48000
    assert forecast(sal, rules, date(2026, 10, 31)) is None  # месяц кончился — оставшихся смен нет
    top = calculate([fake(date(2026, 10, 1), 45000)], [], rules)
    assert forecast(top, rules, date(2026, 10, 15)) is None  # уже высшая ступень
    ranking = monthly_ranking([fake(date(2026, 10, 1), 50000), fake(date(2026, 10, 2), 30000)], rules)
    assert ranking.best_shift.revenue == 50000 and ranking.best_average == 40000


def test_writeoff_reasons_saved(db):
    anna = db.add_user("anna", "Анна", "1")
    s = sh(db, anna, date(2026, 10, 1), items={"Завяло": 2000, "Разбито": 500})
    assert db.get_shift(s.id).writeoff == 2500
    assert db.writeoff_items_for_shift(s.id) == {"Завяло": 2000, "Разбито": 500}
    assert db.writeoff_by_reason(date(2026, 10, 1), date(2026, 10, 31)) == {"Завяло": 2000, "Разбито": 500}
    with pytest.raises(UserError):
        s2 = db.open_shift(anna.id, 0, datetime(2026, 10, 3, 8), CH)
        db.close_shift(s2.id, datetime(2026, 10, 4, 7), 0, 0, 0, writeoff_items={"Завяло": -5})


def test_recent_writeoff_average(db):
    anna = db.add_user("anna", "Анна", "1")
    for d in range(1, 5):
        sh(db, anna, date(2026, 10, d), writeoff=1000)
    big = sh(db, anna, date(2026, 10, 5), writeoff=9000)
    assert db.recent_writeoff_average(big) == pytest.approx(1000)


def test_dispute_flow(db):
    anna = db.add_user("anna", "Анна", "1")
    s = sh(db, anna, date(2026, 10, 1))
    p = db.add_penalty(s.id, 4, "Нет ценников", 1000, "Мария", datetime(2026, 10, 2, 9))
    with pytest.raises(UserError):
        db.dispute_penalty(p.id, "  ", NOW)
    disputed = db.dispute_penalty(p.id, "Ценники были, упали", NOW)
    assert disputed.disputed_at == NOW and disputed.dispute_text == "Ценники были, упали"
    with pytest.raises(UserError):
        db.dispute_penalty(p.id, "ещё раз", NOW)
    text = telegram.dispute_text(db.get_penalty(p.id))
    assert "Анна" in text and "Ценники были, упали" in text and f"/pok_{p.id}" in text and f"/unpen_{p.id}" in text


def make_ctx(db):
    rules = SalaryRules(2000, [Tier(30000, 5)], holidays=Holidays(yearly={(8, 3)}))
    return BotContext(db=db, now=NOW, change=CH, grace=30, water=WaterSchedule(date(2026, 10, 18), 2),
                      rules=lambda uid: rules, version="1.0")


def test_bot_pok_writeoffs_rating(db):
    anna = db.add_user("anna", "Анна", "1")
    s = sh(db, anna, date(2026, 10, 18), revenue=50000, items={"Завяло": 4000})
    p = db.add_penalty(s.id, 4, "Нет ценников", 1000, "Мария", datetime(2026, 10, 19, 9))
    db.dispute_penalty(p.id, "упали", NOW)
    assert parse_command(f"/pok_{p.id}") == ("pok", str(p.id))
    handler, ctx = BotHandler(), make_ctx(db)
    penalties = handler.handle("/штрафы", ctx)
    assert "оспорено" in penalties and f"/pok_{p.id}" in penalties
    assert "Спор закрыт" in handler.handle(f"/pok_{p.id}", ctx)
    assert "оспорено" not in handler.handle("/штрафы", ctx)
    assert "Завяло: 4 000 ₽" in handler.handle("/списания", ctx)
    rating = handler.handle("/рейтинг", ctx)
    assert "Лучшая смена: Анна, 18.10 — 50 000 ₽" in rating
    assert "списания" in handler.handle("/help", ctx).lower()


def test_closed_text_has_reasons_and_alert(db):
    anna = db.add_user("anna", "Анна", "1")
    s = sh(db, anna, date(2026, 10, 1), items={"Завяло": 4000})
    text = telegram.shift_closed_text(s, CH, False, None, [], reasons=db.writeoff_items_for_shift(s.id),
                                      alert="❗ Списание необычно большое")
    assert "Завяло 4 000 ₽" in text and "❗ Списание необычно большое" in text


def test_due_reports():
    cfg = {"morning": True, "time": "08:30", "weekly": True, "monthly": True}
    assert reports.due_reports(datetime(2026, 10, 20, 8, 10), CH, cfg) == []  # рано
    tue = reports.due_reports(datetime(2026, 10, 20, 9, 0), CH, cfg)
    assert [r.kind for r in tue] == ["morning"] and tue[0].first == date(2026, 10, 19)
    monday = reports.due_reports(datetime(2026, 10, 19, 9, 0), CH, cfg)
    assert [r.kind for r in monday] == ["morning", "weekly"]
    assert (monday[1].first, monday[1].last) == (date(2026, 10, 12), date(2026, 10, 18))
    first = reports.due_reports(datetime(2026, 11, 1, 9, 0), CH, cfg)
    assert [r.kind for r in first] == ["morning", "monthly"]
    assert (first[1].first, first[1].last) == (date(2026, 10, 1), date(2026, 10, 31))
    assert first[1].key == "report:monthly:2026-10"
    off = {**cfg, "morning": False, "weekly": False, "monthly": False}
    assert reports.due_reports(datetime(2026, 11, 2, 9, 0), CH, off) == []
    assert reports.parse_time("bad").hour == 8


def test_report_texts(db):
    anna = db.add_user("anna", "Анна", "1")
    sh(db, anna, date(2026, 10, 19), revenue=50000, items={"Завяло": 1000})
    ctx = make_ctx(db)
    cfg = {"morning": True, "time": "08:30", "weekly": True, "monthly": True}
    morning = reports.due_reports(datetime(2026, 10, 20, 9, 0), CH, cfg)[0]
    text = reports.report_text(morning, ctx)
    assert "Доброе утро" in text and "Приход: 50 000 ₽" in text and "Завяло" in text
    month = reports.due_reports(datetime(2026, 11, 1, 9, 0), CH, cfg)[1]
    ctx.now = datetime(2026, 11, 1, 9, 0)
    text = reports.report_text(month, ctx)
    assert "Отчёт за октябрь" in text and "Зарплата" in text and "Рейтинг" in text and "Анна" in text
