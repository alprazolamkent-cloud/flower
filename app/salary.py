"""Зарплата флориста за месяц: ставка за смену + процент с цветов − недостачи ± доплаты и удержания."""

from dataclasses import dataclass, field

from .shifts import Adjustment, Shift


@dataclass
class SalaryRules:
    shift_rate: int = 0  # ₽ за сданную смену
    flowers_percent: float = 0.0  # % от продажи цветов
    deduct_shortage: bool = True  # вычитать недостачу кассы


@dataclass
class Salary:
    shifts: int = 0
    base: int = 0
    flowers: int = 0
    percent_part: int = 0
    shortage: int = 0
    not_closed: int = 0  # несданные смены в расчёт не входят
    adjustments: list[Adjustment] = field(default_factory=list)

    @property
    def adjustments_total(self) -> int:
        return sum(a.amount for a in self.adjustments)

    @property
    def total(self) -> int:
        return self.base + self.percent_part - self.shortage + self.adjustments_total


def calculate(shifts: list[Shift], adjustments: list[Adjustment], rules: SalaryRules) -> Salary:
    closed = [s for s in shifts if s.closed_at is not None]
    flowers = sum(s.flowers for s in closed)
    shortage = sum(-d for s in closed if (d := s.difference()) is not None and d < 0)
    return Salary(
        shifts=len(closed),
        base=len(closed) * rules.shift_rate,
        flowers=flowers,
        percent_part=round(flowers * rules.flowers_percent / 100),
        shortage=shortage if rules.deduct_shortage else 0,
        not_closed=len(shifts) - len(closed),
        adjustments=list(adjustments),
    )
