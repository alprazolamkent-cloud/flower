"""Зарплата флориста за месяц: ставка за смену + премия ступенями − недостачи ± доплаты и удержания.

Премия: средний приход за смену (приход за месяц ÷ сданные смены) сравнивается со ступенями,
срабатывает самая высокая достигнутая. Не дотянул до первой ступени (плана) — премии нет.
Кроме того, премия выдаётся только если за месяц штрафных санкций у сотрудника меньше заданного числа
(по умолчанию 3) и списания цветов по магазину меньше заданного процента (по умолчанию 15%).
Сами штрафные санкции вычитаются из зарплаты.

Праздничные смены: выручка в расчёт премии не идёт (ни в сумму, ни в средний приход за смену), зато за них
может платиться своя ставка. В обычные выходные (суббота, воскресенье) всё как в будни.
"""

import calendar
import math
from dataclasses import dataclass, field
from datetime import date

from .holidays import Holidays
from .shifts import Adjustment, Penalty, Shift

MAX_TIERS = 5
WRITEOFF_BASES = {
    "total": "от всех цветов (проданные + списанные)",
    "sales": "от проданных цветов",
}


@dataclass
class PenaltyKind:
    """Вид штрафной санкции из списка, который задаёт администратор."""
    id: int
    title: str
    amount: int


DEFAULT_PENALTY_KINDS = [
    PenaltyKind(1, "Грязная вода (не заменена вода)", 500),
    PenaltyKind(2, "Витрина (вялые букеты)", 500),
    PenaltyKind(3, "Не расставлена витрина", 500),
    PenaltyKind(4, "Нет ценников", 1000),
]


def pct(value: float, digits: int | None = 1) -> str:
    """Процент по-русски: 2,9% (digits=None — без лишних нулей: 15%, 12,5%)."""
    text = f"{value:g}" if digits is None else f"{value:.{digits}f}"
    return text.replace(".", ",") + "%"


def writeoff_percent(writeoff: int, flowers: int, base: str = "total") -> float:
    """Доля списаний, %. total — от всех цветов (проданные + списанные), sales — от проданных."""
    denominator = flowers + writeoff if base == "total" else flowers
    if denominator <= 0:
        return 100.0 if writeoff > 0 else 0.0
    return writeoff * 100 / denominator


@dataclass
class ShopWriteoff:
    """Списания всего магазина за период — условие премии общее для всех сотрудников."""
    writeoff: int = 0
    flowers: int = 0

    def percent(self, base: str = "total") -> float:
        return writeoff_percent(self.writeoff, self.flowers, base)


def shop_writeoff(shifts: list[Shift]) -> ShopWriteoff:
    closed = [s for s in shifts if s.closed_at is not None]
    return ShopWriteoff(sum(s.writeoff for s in closed), sum(s.flowers for s in closed))


@dataclass
class Tier:
    per_shift: int  # средний приход за смену — «от»
    percent: float


@dataclass
class SalaryRules:
    shift_rate: int = 0  # ₽ за сданную смену
    tiers: list[Tier] = field(default_factory=list)
    above_plan_only: bool = False  # процент только с прихода сверх плана (план — первая ступень)
    deduct_shortage: bool = True  # вычитать недостачу кассы
    max_penalties: int = 3  # премия, только если санкций за месяц МЕНЬШЕ этого числа (0 — не проверять)
    max_writeoff_percent: float = 15.0  # и списаний по магазину МЕНЬШЕ этого процента (0 — не проверять)
    writeoff_base: str = "total"  # от чего считать процент списаний, см. WRITEOFF_BASES
    holiday_rate: int = 0  # ставка за праздничную смену (0 — как обычная)
    holidays: Holidays = field(default_factory=Holidays)

    def shift_kind(self, day: date) -> str:
        """holiday или normal (обычные выходные — как будни)."""
        return "holiday" if self.holidays.is_holiday(day) else "normal"

    def rate_for(self, day: date) -> int:
        if self.shift_kind(day) == "holiday" and self.holiday_rate:
            return self.holiday_rate
        return self.shift_rate

    def active_tiers(self) -> list[Tier]:
        """Заполненные ступени по возрастанию."""
        return sorted((t for t in self.tiers if t.percent > 0), key=lambda t: t.per_shift)


KIND_TITLES = {"normal": "обычные", "holiday": "праздничные"}


@dataclass
class Salary:
    shifts: int = 0
    base: int = 0
    base_parts: list[tuple[str, int, int]] = field(default_factory=list)  # (вид смен, сколько, ставка)
    holiday_shifts: int = 0  # праздничные смены: их выручка в премию не идёт
    holiday_revenue: int = 0
    bonus_shifts: int = 0  # смены, по которым считается премия (без праздничных)
    revenue: int = 0  # выручка, по которой считается премия (без праздничных смен)
    average: float = 0.0
    tier: Tier | None = None  # достигнутая ступень
    next_tier: Tier | None = None  # следующая ступень — к чему стремиться
    plan: int = 0  # первая ступень (средний приход за смену)
    bonus_base: int = 0  # с какой суммы считали процент
    bonus: int = 0  # выплата: 0, если не выполнены условия
    bonus_earned: int = 0  # сколько было бы без условий — чтобы видеть, что потеряно
    blocked: list[str] = field(default_factory=list)  # почему премии нет (штрафы, списания)
    shortage: int = 0
    not_closed: int = 0  # несданные: ставка начислена, но выручки нет — в премию не идут
    adjustments: list[Adjustment] = field(default_factory=list)
    penalties: list[Penalty] = field(default_factory=list)
    writeoff: ShopWriteoff = field(default_factory=ShopWriteoff)
    writeoff_percent: float | None = None  # None — списания не проверяются

    @property
    def adjustments_total(self) -> int:
        return sum(a.amount for a in self.adjustments)

    @property
    def penalties_total(self) -> int:
        return sum(p.amount for p in self.penalties)

    @property
    def total(self) -> int:
        return self.base + self.bonus - self.shortage - self.penalties_total + self.adjustments_total


def calculate(shifts: list[Shift], adjustments: list[Adjustment], rules: SalaryRules,
              penalties: list[Penalty] | None = None, shop: ShopWriteoff | None = None) -> Salary:
    """shifts, adjustments, penalties — сотрудника за месяц; shop — списания всего магазина за месяц."""
    penalties = list(penalties or [])
    closed = [s for s in shifts if s.closed_at is not None]
    holiday = [s for s in closed if rules.shift_kind(s.day) == "holiday"]
    counted = [s for s in closed if rules.shift_kind(s.day) != "holiday"]  # премия — без праздничных смен
    revenue = sum(s.revenue for s in counted)
    average = revenue / len(counted) if counted else 0.0
    base_parts = []  # ставка — за все смены, в том числе несданные: смену отработали, просто не сдали
    for kind in ("normal", "holiday"):
        group = [s for s in shifts if rules.shift_kind(s.day) == kind]
        if group:
            base_parts.append((kind, len(group), rules.rate_for(group[0].day)))
    shortage = sum(-d for s in closed if (d := s.difference()) is not None and d < 0)
    tiers = rules.active_tiers()

    tier = next((t for t in reversed(tiers) if counted and average >= t.per_shift), None)
    next_tier = next((t for t in tiers if average < t.per_shift), None)
    plan = tiers[0].per_shift if tiers else 0
    bonus_base = 0
    if tier is not None:
        bonus_base = max(0, revenue - plan * len(counted)) if rules.above_plan_only else revenue

    earned = round(bonus_base * tier.percent / 100) if tier else 0

    blocked = []
    if rules.max_penalties > 0 and len(penalties) >= rules.max_penalties:
        blocked.append(f"штрафных санкций за месяц {len(penalties)} (для премии нужно меньше {rules.max_penalties})")
    percent = None
    if rules.max_writeoff_percent > 0 and shop is not None:
        percent = shop.percent(rules.writeoff_base)
        if percent >= rules.max_writeoff_percent:
            blocked.append(f"списания по магазину {pct(percent)} (для премии нужно меньше {pct(rules.max_writeoff_percent, None)})")

    return Salary(
        shifts=len(shifts),
        base=sum(count * rate for _, count, rate in base_parts),
        base_parts=base_parts,
        holiday_shifts=len(holiday),
        holiday_revenue=sum(s.revenue for s in holiday),
        bonus_shifts=len(counted),
        revenue=revenue,
        average=average,
        tier=tier,
        next_tier=next_tier,
        plan=plan,
        bonus_base=bonus_base,
        bonus=0 if blocked else earned,
        bonus_earned=earned,
        blocked=blocked if earned else [],  # причину показываем, только если премия вообще была бы
        shortage=shortage if rules.deduct_shortage else 0,
        not_closed=len(shifts) - len(closed),
        adjustments=list(adjustments),
        penalties=penalties,
        writeoff=shop or ShopWriteoff(),
        writeoff_percent=percent,
    )


def shifts_word(n: int) -> str:
    """1 смена, 3 смены, 5 смен."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} смена"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} смены"
    return f"{n} смен"


@dataclass
class Forecast:
    remaining_shifts: int  # сколько смен ожидается до конца месяца при нынешнем темпе
    target: Tier  # к какой ступени идём
    needed_average: int  # сколько в среднем за смену нужно набрать на оставшихся сменах


def forecast(sal: Salary, rules: SalaryRules, today: date) -> Forecast | None:
    """Что нужно, чтобы к концу месяца выйти на следующую ступень премии. Только для идущего месяца."""
    tiers = rules.active_tiers()
    if not tiers or not sal.shifts:
        return None
    target = sal.next_tier
    if target is None:
        return None  # уже на высшей ступени
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    pace = sal.shifts / ((today - first).days + 1)  # смен в день
    remaining = round(pace * (last - today).days)
    if remaining <= 0:
        return None
    needed = (target.per_shift * (sal.bonus_shifts + remaining) - sal.revenue) / remaining
    return Forecast(remaining, target, max(0, math.ceil(needed)))


@dataclass
class Ranking:
    best_shift: Shift | None = None
    best_average_name: str = ""
    best_average: int = 0


def monthly_ranking(shifts: list[Shift], rules: SalaryRules, min_shifts: int = 2) -> Ranking:
    """Лучшая смена месяца и лучший средний приход — без праздничных смен: в праздник выручка сама
    по себе огромная (8 Марта всегда было бы «лучшей сменой»), и в премию она тоже не идёт."""
    counted = [s for s in shifts if s.closed_at is not None and rules.shift_kind(s.day) != "holiday"]
    result = Ranking(best_shift=max(counted, key=lambda s: s.revenue, default=None))
    per_user: dict[str, list[int]] = {}
    for s in counted:
        if s.user_name:
            per_user.setdefault(s.user_name, []).append(s.revenue)
    averages = {name: sum(v) / len(v) for name, v in per_user.items() if len(v) >= min_shifts}
    if averages:
        result.best_average_name = max(averages, key=averages.get)
        result.best_average = round(averages[result.best_average_name])
    return result
