"""Зарплата флориста за месяц: ставка за смену + премия ступенями − недостачи ± доплаты и удержания.

Премия: средний приход за смену (приход за месяц ÷ сданные смены) сравнивается со ступенями,
срабатывает самая высокая достигнутая. Не дотянул до первой ступени (плана) — премии нет.
"""

from dataclasses import dataclass, field

from .shifts import Adjustment, Shift

MAX_TIERS = 5


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

    def active_tiers(self) -> list[Tier]:
        """Заполненные ступени по возрастанию."""
        return sorted((t for t in self.tiers if t.percent > 0), key=lambda t: t.per_shift)


@dataclass
class Salary:
    shifts: int = 0
    base: int = 0
    revenue: int = 0
    average: float = 0.0
    tier: Tier | None = None  # достигнутая ступень
    next_tier: Tier | None = None  # следующая ступень — к чему стремиться
    plan: int = 0  # первая ступень (средний приход за смену)
    bonus_base: int = 0  # с какой суммы считали процент
    bonus: int = 0
    shortage: int = 0
    not_closed: int = 0  # несданные смены в расчёт не входят
    adjustments: list[Adjustment] = field(default_factory=list)

    @property
    def adjustments_total(self) -> int:
        return sum(a.amount for a in self.adjustments)

    @property
    def total(self) -> int:
        return self.base + self.bonus - self.shortage + self.adjustments_total


def calculate(shifts: list[Shift], adjustments: list[Adjustment], rules: SalaryRules) -> Salary:
    closed = [s for s in shifts if s.closed_at is not None]
    revenue = sum(s.revenue for s in closed)
    average = revenue / len(closed) if closed else 0.0
    shortage = sum(-d for s in closed if (d := s.difference()) is not None and d < 0)
    tiers = rules.active_tiers()

    tier = next((t for t in reversed(tiers) if closed and average >= t.per_shift), None)
    next_tier = next((t for t in tiers if average < t.per_shift), None)
    plan = tiers[0].per_shift if tiers else 0
    bonus_base = 0
    if tier is not None:
        bonus_base = max(0, revenue - plan * len(closed)) if rules.above_plan_only else revenue

    return Salary(
        shifts=len(closed),
        base=len(closed) * rules.shift_rate,
        revenue=revenue,
        average=average,
        tier=tier,
        next_tier=next_tier,
        plan=plan,
        bonus_base=bonus_base,
        bonus=round(bonus_base * tier.percent / 100) if tier else 0,
        shortage=shortage if rules.deduct_shortage else 0,
        not_closed=len(shifts) - len(closed),
        adjustments=list(adjustments),
    )
