"""Карточки зарплаты в кабинете: что попадает в «Расчёт», «Премию» и таблицу всех сотрудников."""

from datetime import date, datetime, time
from types import SimpleNamespace

from app.holidays import Holidays
from app.salary import SalaryRules, ShopWriteoff, Tier, calculate
from app.salary_view import all_html, bonus_bottom_html, calc_html, progress
from app.shifts import Adjustment, Penalty


def fake(day, revenue):
    return SimpleNamespace(day=day, revenue=revenue, closed_at=datetime.combine(day, time(23)), user_name="Анна",
                           difference=lambda: 0)


RULES = SalaryRules(2000, [Tier(30000, 5), Tier(40000, 7)], holiday_rate=3000, holidays=Holidays(yearly={(8, 3)}))


def text(html: str) -> str:
    return html.replace("&nbsp;", " ")


def test_calc_lines_and_total():
    adj = [Adjustment(1, -5000, "аванс", datetime(2026, 3, 10)), Adjustment(2, 1000, "доплата из Telegram: Мария",
                                                                             datetime(2026, 3, 11))]
    sal = calculate([fake(date(2026, 3, 6), 35000), fake(date(2026, 3, 8), 90000)], adj, RULES)
    html = text(calc_html(sal, RULES))
    assert "Обычные смены: 1 × 2 000 ₽" in html and "Праздничные смены: 1 × 3 000 ₽" in html
    assert "Премия: 5% с 35 000 ₽" in html and "+1 750 ₽" in html
    assert "Аванс" in html and "−5 000 ₽" in html and "Доплата из Telegram: Мария" in html
    assert "К выплате" in html and f"{sal.total:,}".replace(",", " ") + " ₽" in html


def test_blocked_bonus_shows_what_was_lost():
    rules = SalaryRules(2000, [Tier(30000, 5)], max_writeoff_percent=10)
    sal = calculate([fake(date(2026, 3, 6), 40000)], [], rules, shop=ShopWriteoff(3000, 7000))
    html = text(calc_html(sal, rules))
    assert "Премия не начислена" in html and "было бы 2 000 ₽" in html
    assert "✗ Списания магазина: 30,0%" in text(bonus_bottom_html(sal, rules, None))


def test_disputed_penalty_marked():
    p = Penalty(1, 1, 1, 4, "Нет ценников", 1000, datetime(2026, 3, 7), shift_day=date(2026, 3, 6),
                disputed_at=datetime(2026, 3, 8), dispute_text="были")
    sal = calculate([fake(date(2026, 3, 6), 20000)], [], RULES, [p])
    html = text(calc_html(sal, RULES))
    assert "Санкция: Нет ценников (06.03) ✋" in html and "оспорена, ждёт решения" in html
    assert "Премия: план не выполнен" in html


def test_progress_bar():
    sal = calculate([fake(date(2026, 3, 6), 35000)], [], RULES)
    done, whole, label = progress(sal, RULES)
    assert (done, whole) == (5000, 10000) and "не хватает 5 000 ₽" in label
    top = calculate([fake(date(2026, 3, 6), 45000)], [], RULES)
    assert progress(top, RULES)[2].startswith("высшая ступень")
    assert progress(top, SalaryRules(2000)) is None


def test_all_employees_table():
    anna = calculate([fake(date(2026, 3, 6), 35000)], [Adjustment(1, -5000, "аванс", datetime(2026, 3, 10))], RULES)
    oleg = calculate([fake(date(2026, 3, 7), 20000)], [], RULES)
    html = text(all_html([("Анна", anna), ("Олег", oleg)]))
    assert "Анна" in html and "Олег" in html and "−5 000 ₽" in html
    assert "Всего" in html and f"{anna.total + oleg.total:,}".replace(",", " ") + " ₽" in html
    assert "Смен за месяц нет" in all_html([])
