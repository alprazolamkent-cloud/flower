"""Автоматические отчёты в Telegram: утренний (итоги вчерашней смены), недельный (по понедельникам),
месячный (1-го числа). Тексты собираются из тех же помощников, что и команды бота."""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .bot_commands import (
    MONTHS, BotContext, cmd_deliveries, cmd_penalties, ranking_text, revenue_text, salary_overview,
    staff_text, writeoff_text,
)
from .shifts import business_day


@dataclass
class Report:
    key: str
    kind: str  # morning, weekly, monthly
    first: date
    last: date
    title: str


def parse_time(text: str, default: time = time(8, 30)) -> time:
    try:
        hours, minutes = text.split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        return default


def due_reports(now: datetime, change: time, settings: dict) -> list[Report]:
    """Какие отчёты пора отправить сейчас. Ключ уникален за период — повторно в Telegram не уйдёт."""
    if now.time() < parse_time(settings["time"]):
        return []
    today = business_day(now, change)
    yesterday = today - timedelta(days=1)
    result = []
    if settings["morning"]:
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
        deliveries = cmd_deliveries(ctx, "")
        if deliveries:
            parts.append(deliveries)
        return "\n\n".join(parts)
    if report.kind == "weekly":
        return "\n\n".join([f"📅 Отчёт {title} ({first:%d.%m}–{last:%d.%m})", revenue_text(ctx, first, last, title),
                            staff_text(ctx, first, last, title), writeoff_text(ctx, first, last, title)])
    last_day = calendar.monthrange(first.year, first.month)[1]
    parts = [f"🗓 Отчёт {title} {first.year}", revenue_text(ctx, first, last, title),
             staff_text(ctx, first, last, title), salary_overview(ctx, first, first.replace(day=last_day)),
             writeoff_text(ctx, first, last, title), ranking_text(ctx, first, last, title)]
    penalties = cmd_penalties(ctx, "")
    if "Пока никого" not in penalties:
        parts.append(penalties.split("\n\nСанкции (поставить")[0])
    return "\n\n".join(parts)
