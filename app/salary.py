"""Зарплата флориста за месяц: ставка за смену + премия ступенями − недостачи ± доплаты и удержания.

Премия: средний приход за смену (приход за месяц ÷ сданные смены) сравнивается со ступенями,
срабатывает самая высокая достигнутая. Не дотянул до первой ступени (плана) — премии нет.
Кроме того, премия выдаётся только если за месяц штрафных санкций у сотрудника меньше заданного числа
(по умолчанию 3) и списания цветов по магазину меньше заданного процента (по умолчанию 15%).
Сами штрафные санкции вычитаются из зарплаты.
"""

from dataclasses import dataclass, field

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
    bonus: int = 0  # выплата: 0, если не выполнены условия
    bonus_earned: int = 0  # сколько было бы без условий — чтобы видеть, что потеряно
    blocked: list[str] = field(default_factory=list)  # почему премии нет (штрафы, списания)
    shortage: int = 0
    not_closed: int = 0  # несданные смены в расчёт не входят
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

    earned = round(bonus_base * tier.percent / 100) if tier else 0

    blocked = []
    if rules.max_penalties > 0 and len(penalties) >= rules.max_penalties:
        blocked.append(f"штрафных санкций за месяц {len(penalties)} (для премии нужно меньше {rules.max_penalties})")
    percent = None
    if rules.max_writeoff_percent > 0 and shop is not None:
        percent = shop.percent(rules.writeoff_base)
        if percent >= rules.max_writeoff_percent:
            blocked.append(f"списания по магазину {percent:.1f}% (для премии нужно меньше {rules.max_writeoff_percent:g}%)")

    return Salary(
        shifts=len(closed),
        base=len(closed) * rules.shift_rate,
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
