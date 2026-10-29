"""Автоматические отчёты в Telegram: утренний (итоги вчерашней смены), недельный (по понедельникам),
месячный (1-го числа). Тексты собираются из тех же помощников, что и команды бота."""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .bot_commands import (
    MONTHS, BotContext, cmd_deliveries, low_stock_text, penalties_text, ranking_text, revenue_text, sales_text,
    salary_overview, staff_text, writeoff_text,
)
from .shifts import business_day, window

MORNING_FOR = timedelta(hours=6)  # утренний отчёт, если программу включили позже, — не позже чем через 6 часов


@dataclass
class Report:
    key: str
    kind: str  # morning, weekly, monthly
    first: date
    last: date
    title: str


def parse_time(text: str, default: time = time(9, 0)) -> time:
    try:
        hours, minutes = text.split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        return default


def due_reports(now: datetime, change: time, settings: dict, grace: int = 30) -> list[Report]:
    """Какие отчёты пора отправить сейчас. Ключ уникален за период — повторно в Telegram не уйдёт."""
    at = datetime.combine(now.date(), parse_time(settings["time"]))
    if now < at:
        return []
    today = business_day(now, change)
    yesterday = today - timedelta(days=1)
    # вчерашнюю смену уже сдали или время на сдачу вышло — иначе итоги были бы неполными
    yesterday_done = now >= window(yesterday, change)[1] + timedelta(minutes=grace)
    result = []
    if settings["morning"] and yesterday_done and now < at + MORNING_FOR:
        result.append(Report(f"report:morning:{today}", "morning", yesterday, yesterday, "за вчерашнюю смену"))
    if settings["weekly"] and now.weekday() == 0:
        first = now.date() - timedelta(days=7)
        result.append(Report(f"report:weekly:{first}", "weekly", first, first + timedelta(days=6), "за прошлую неделю"))
    if settings["monthly"] and now.day == 1:
        last = now.date() - timedelta(days=1)
        result.append(Report(f"report:monthly:{last:%Y-%m}", "monthly", last.replace(day=1), last,
                             f"за {MONTHS[last.month - 1]}"))
    return result


def report_text(report: Report, ctx: BotContext) -> str:
    first, last, title = report.first, report.last, report.title
    if report.kind == "morning":
        parts = [f"☀ Доброе утро! Итоги {title}", revenue_text(ctx, first, last, title),
                 writeoff_text(ctx, first, last, title)]
        if ctx.db.products() or ctx.db.sales_report(*(ctx.now - timedelta(days=2), ctx.now)).count:
            parts += [sales_text(ctx, first, last, title), low_stock_text(ctx)]  # если склад ведут в программе
        deliveries = cmd_deliveries(ctx, "")
        if deliveries:
            parts.append(deliveries)
        return "\n\n".join(parts)
    if report.kind == "weekly":
        parts = [f"📅 Отчёт {title} ({first:%d.%m}–{last:%d.%m})", revenue_text(ctx, first, last, title),
                 staff_text(ctx, first, last, title), writeoff_text(ctx, first, last, title)]
        if ctx.db.products():
            parts.append(sales_text(ctx, first, last, title))
        return "\n\n".join(parts)
    last_day = calendar.monthrange(first.year, first.month)[1]
    parts = [f"🗓 Отчёт {title} {first.year}", revenue_text(ctx, first, last, title),
             staff_text(ctx, first, last, title), salary_overview(ctx, first, first.replace(day=last_day)),
             writeoff_text(ctx, first, last, title), ranking_text(ctx, first, last, title)]
    if ctx.db.products():
        parts.append(sales_text(ctx, first, last, title))
    penalties = penalties_text(ctx, first, last)
    if "Пока никого" not in penalties:
        parts.append(penalties)
    return "\n\n".join(parts)
