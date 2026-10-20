"""Праздничные дни. В праздничную смену выручка не идёт в премию, а ставка за смену — своя.

Список хранится текстом, по строке на дату: «08.03» — каждый год, «23.11.2026» — только в этот год
(для переходящих праздников вроде Дня матери). После даты можно написать пояснение.
"""

import re
from dataclasses import dataclass, field
from datetime import date

DEFAULT_TEXT = """01.01 Новый год
14.02 День святого Валентина
07.03 Канун 8 Марта
08.03 8 Марта
01.09 День знаний
31.12 Канун Нового года"""

LINE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?(?:\s+.*)?$")


@dataclass
class Holidays:
    yearly: set[tuple[int, int]] = field(default_factory=set)  # (день, месяц) — каждый год
    once: set[date] = field(default_factory=set)  # конкретные даты

    def is_holiday(self, day: date) -> bool:
        return (day.day, day.month) in self.yearly or day in self.once


def parse_holidays(text: str) -> tuple[Holidays, list[str]]:
    """Список праздников и строки, которые не удалось понять."""
    holidays, bad = Holidays(), []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            bad.append(line.strip())
            continue
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            if year:
                holidays.once.add(date(int(year), month, day))
            else:
                date(2024, month, day)  # високосный год: 29.02 допустимо
                holidays.yearly.add((day, month))
        except ValueError:
            bad.append(line.strip())
    return holidays, bad
