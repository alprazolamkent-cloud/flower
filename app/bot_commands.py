"""Команды Telegram-боту: хозяин пишет /касса, /выручка, /зарплата Анна… — бот отвечает данными из программы.

Логика без Qt и без сети: на вход текст и контекст, на выход текст ответа. Команды, которые что-то
записывают (аванс, премия, штраф), сначала переспрашивают.
"""

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .db import Database, Delivery, User, UserError
from .delivery_dialog import money
from .salary import KIND_TITLES, PenaltyKind, SalaryRules, calculate, forecast, monthly_ranking, shop_writeoff
from .shifts import STATUS_TEXT, Penalty, Shift, WaterSchedule, business_day, window

CONFIRM_FOR = timedelta(minutes=5)
MAX_LIST = 15

# Команды для меню Telegram (латиницей — только такие подсвечиваются и попадают в меню) и русские псевдонимы.
MENU = [
    ("help", "Список команд"),
    ("today", "Сводка за сегодня"),
    ("shift", "Кто сейчас на смене"),
    ("cash", "Касса: что осталось и что известно"),
    ("revenue", "Выручка: сегодня, вчера, неделя, месяц"),
    ("week", "Выручка за неделю"),
    ("month", "Выручка за месяц"),
    ("shifts", "Последние смены"),
    ("staff", "Сотрудники: смены и приход за месяц"),
    ("salary", "Зарплата за месяц: все или /salary Анна"),
    ("problems", "Проблемы: недостачи, несданные смены"),
    ("deliveries", "Доставки: сегодня, завтра или дата"),
    ("water", "График смены воды"),
    ("duties", "Обязанности смены"),
    ("ann", "Объявления и кто прочитал"),
    ("status", "Состояние программы"),
    ("penalties", "Штрафные санкции за месяц"),
    ("writeoffs", "Списания: сколько и по каким причинам"),
    ("rating", "Рейтинг месяца: лучшая смена и средний приход"),
    ("backup", "Сделать резервную копию"),
]
ALIASES = {
    "помощь": "help", "start": "help", "старт": "help", "команды": "help",
    "сегодня": "today", "итоги": "today",
    "смена": "shift", "кто": "shift",
    "касса": "cash",
    "выручка": "revenue", "приход": "revenue",
    "неделя": "week", "месяц": "month",
    "смены": "shifts",
    "сотрудники": "staff", "персонал": "staff",
    "зарплата": "salary", "зп": "salary",
    "проблемы": "problems", "недостачи": "problems",
    "доставки": "deliveries", "доставка": "deliveries",
    "вода": "water",
    "обязанности": "duties", "дела": "duties",
    "объявления": "ann",
    "статус": "status",
    "копия": "backup", "бэкап": "backup", "бекап": "backup",
    "аванс": "advance", "премия": "bonus", "штраф": "fine",
    "удалить": "del", "оставить": "keep",
    "списания": "writeoffs", "списание": "writeoffs", "рейтинг": "rating",
    "санкция": "sanction", "санкции": "penalties", "штрафы": "penalties", "fines": "penalties",
    "да": "yes", "yes": "yes", "ок": "yes", "нет": "no", "no": "no", "отмена": "no", "cancel": "no",
}
ADJUSTMENTS = {"advance": ("Аванс", -1, "аванс"), "bonus": ("Премия", 1, "премия"), "fine": ("Штраф", -1, "штраф")}


@dataclass
class BotContext:
    db: Database
    now: datetime
    change: time
    grace: int
    water: WaterSchedule
    rules: Callable[[int | None], SalaryRules]
    version: str = ""
    last_backup: datetime | None = None
    backup_now: Callable[[], str] | None = None
    author: str = ""  # кто из хозяев пишет — для журнала и сообщения второму
    decided: Callable[[Delivery, bool, str], None] | None = None  # (доставка, удалена?, кто решил)
    audit: Callable[[str, str], None] | None = None  # (действие, подробности) — запись в журнал действий
    penalty_kinds: list[PenaltyKind] = field(default_factory=list)  # список санкций из настроек
    penalty_changed: Callable[[Penalty, bool, str], None] | None = None  # (санкция, поставлена?, кто)


# ----- общие помощники -----

def parse_command(text: str) -> tuple[str, str] | None:
    """«/Касса@MyBot  аргументы» → («cash», «аргументы»); обычный текст — None."""
    text = text.strip()
    if not text:
        return None
    word, _, args = text.partition(" ")
    word = word.lstrip("/").split("@")[0].lower()
    m = re.fullmatch(r"(del|keep)_(\d+)", word)  # ссылки из сообщения о запросе: /del_17, /keep_17
    if m and text.startswith("/"):
        return m.group(1), m.group(2)
    m = re.fullmatch(r"pen_(\d+)_(\d+)", word)  # ссылка из итогов смены: смена, санкция
    if m and text.startswith("/"):
        return "pen", f"{m.group(1)} {m.group(2)}"
    m = re.fullmatch(r"pok_(\d+)", word)  # оставить санкцию в силе после спора сотрудника
    if m and text.startswith("/"):
        return "pok", m.group(1)
    m = re.fullmatch(r"unpen_(\d+)", word)  # «Отменить» под сообщением о санкции
    if m and text.startswith("/"):
        return "unpen", m.group(1)
    if not text.startswith("/") and word not in ("да", "нет", "yes", "no", "ок", "отмена"):
        return None  # не команда (кроме ответов «да» / «нет» на вопрос бота)
    return ALIASES.get(word, word), args.strip()


def parse_amount(text: str) -> int | None:
    """«5000», «5 000», «5000р», «5к», «2,5к» → рубли."""
    m = re.search(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(к|k|тыс\w*)?\s*(?:₽|р\b|руб\w*)?", text, re.IGNORECASE)
    if not m:
        return None
    number = float(m.group(1).replace(" ", "").replace(",", "."))
    return round(number * (1000 if m.group(2) else 1))


def find_user(db: Database, query: str) -> tuple[User | None, str]:
    """Сотрудник по имени или логину (без учёта регистра, можно начало слова). Второе — текст ошибки."""
    query = query.strip().casefold()
    if not query:
        return None, "Укажите сотрудника, например: /зарплата Анна"
    users = db.users()
    exact = [u for u in users if query in ((u.name or "").casefold(), u.login.casefold())]
    found = exact or [u for u in users if (u.name or "").casefold().startswith(query)
                      or u.login.casefold().startswith(query)]
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, "Такого сотрудника нет. Есть: " + ", ".join(u.name or u.login for u in users)
    return None, "Подходит несколько: " + ", ".join(u.name or u.login for u in found) + ". Уточните."


def _name(user_name: str) -> str:
    return user_name or "—"


def _period(arg: str, now: datetime, change: time) -> tuple[date, date, str]:
    """(с какого дня смены, по какой, название периода)."""
    today = business_day(now, change)
    arg = arg.strip().lower()
    if arg in ("вчера", "yesterday"):
        return today - timedelta(days=1), today - timedelta(days=1), "вчера"
    if arg in ("неделя", "week", "7"):
        return today - timedelta(days=6), today, "за 7 дней"
    if arg in ("месяц", "month"):
        first = today.replace(day=1)
        return first, today.replace(day=calendar.monthrange(today.year, today.month)[1]), f"за {MONTHS[today.month - 1]}"
    if arg in ("прошлый", "прошлыймесяц", "прошлый месяц"):
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last, f"за {MONTHS[last.month - 1]}"
    return today, today, "за сегодняшнюю смену"


MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def _diff_words(diff: int | None) -> str:
    if diff is None:
        return ""
    if diff == 0:
        return "касса сходится"
    return f"недостача {money(-diff)}" if diff < 0 else f"излишек {money(diff)}"


def _shift_line(s: Shift, ctx: BotContext) -> str:
    status = s.status(ctx.now, ctx.change, ctx.grace)
    line = f"{s.day:%d.%m} {_name(s.user_name)} — {STATUS_TEXT[status]}"
    if s.closed_at is not None:
        line += f", приход {money(s.revenue)}"
        if s.difference():
            line += f", {_diff_words(s.difference())}"
    return line


def _deliveries_totals(db: Database, first: date, last: date) -> tuple[int, int, int]:
    """(доставок, доставлено, взяли за доставки) за дни с first по last."""
    count = delivered = taken = 0
    day = first
    while day <= last:
        t = db.totals_for_day(day)
        count, delivered, taken = count + t.count, delivered + t.delivered, taken + t.price
        day += timedelta(days=1)
    return count, delivered, taken


# ----- команды -----

def cmd_help(ctx: BotContext, args: str) -> str:
    lines = ["🌸 Что я умею (можно писать по-русски: /касса, /зарплата Анна):", ""]
    lines += [f"/{name} — {text}" for name, text in MENU]
    lines += ["", "Штрафные санкции: после сдачи смены придут ссылки — нажмите нужную. Позже: /санкция Анна 2. Список: /штрафы.",
              "Сотрудник может оспорить санкцию — придёт его объяснение со ссылками: оставить /pok_N, снять /unpen_N.",
              "Списания по причинам: /списания (или /списания неделя). Рейтинг месяца: /рейтинг.",
              "Когда сотрудник просит удалить доставку, придёт сообщение со ссылками: удалить /del_N, оставить /keep_N.",
              "Записать: /аванс Анна 5000 на ремонт, /премия Анна 3000 за праздники, "
                  "/штраф Анна 500 разбила вазу — сначала спрошу «да / нет».",
              "Объявление сотрудникам: напишите сообщение, начав с «!»."]
    return "\n".join(lines)


def cmd_shift(ctx: BotContext, args: str) -> str:
    active = [s for s in ctx.db.open_shifts() if s.status(ctx.now, ctx.change, ctx.grace) in ("open", "closing")]
    stale = [s for s in ctx.db.open_shifts() if s not in active]
    if not active:
        text = "Сейчас никто не на смене."
    else:
        text = "\n".join(
            f"👤 {_name(s.user_name)} — с {s.opened_at:%d.%m %H:%M}, до {s.end(ctx.change):%H:%M}, "
            f"принято в кассе {money(s.opening_cash)}"
            + (f" (смена закончилась, сдать до {s.deadline(ctx.change, ctx.grace):%H:%M})"
               if s.status(ctx.now, ctx.change, ctx.grace) == "closing" else "")
            for s in active)
    if stale:
        text += "\n❗ Не сдано: " + ", ".join(f"{_name(s.user_name)} ({s.day:%d.%m})" for s in stale)
    return text


def cmd_cash(ctx: BotContext, args: str) -> str:
    db = ctx.db
    last = db.shift_before(ctx.now)
    lines = []
    open_now = [s for s in db.open_shifts() if s.status(ctx.now, ctx.change, ctx.grace) in ("open", "closing")]
    closed = db.shifts_between(date(2000, 1, 1), ctx.now.date() + timedelta(days=1))
    closed = [s for s in closed if s.closed_at is not None]
    if closed:
        s = max(closed, key=lambda x: x.closed_at)
        lines.append(f"💰 Последняя сдача: {_name(s.user_name)}, {s.closed_at:%d.%m %H:%M} — "
                     f"в кассе оставлено {money(s.closing_cash or 0)} ({_diff_words(s.difference())})")
    else:
        lines.append("Смен ещё не сдавали.")
    for s in open_now:
        spent = f", расходы {money(s.expenses)}" if s.expenses else ""
        taken = f", изъятия {money(s.withdrawals)}" if s.withdrawals else ""
        lines.append(f"Сейчас на смене {_name(s.user_name)}: принято в кассе {money(s.opening_cash)}{spent}{taken}.")
    if open_now:
        lines.append("Продажи наличными станут известны при сдаче смены — точная сумма в кассе будет тогда.")
    if last is not None and last.closing_cash is not None and not open_now:
        lines.append(f"В кассе до следующей смены: {money(last.closing_cash)}.")
    return "\n".join(lines)


def cmd_revenue(ctx: BotContext, args: str) -> str:
    return revenue_text(ctx, *_period(args, ctx.now, ctx.change))


def revenue_text(ctx: BotContext, first: date, last: date, title: str) -> str:
    shifts = ctx.db.shifts_between(first, last)
    closed = [s for s in shifts if s.closed_at is not None]
    if not shifts:
        return f"Смен {title} нет."
    total = {k: sum(getattr(s, k) for s in closed) for k in ("revenue", "flowers", "terminal", "qr", "cash_sales")}
    lines = [f"📊 Выручка {title}", f"Сдано смен: {len(closed)} из {len(shifts)}"]
    if closed:
        written_off = sum(s.writeoff for s in closed)
        lines += [f"Приход: {money(total['revenue'])}", f"Цветы: {money(total['flowers'])}, списано {money(written_off)}",
                  f"Терминал: {money(total['terminal'])} · QR: {money(total['qr'])} · "
                  f"Наличные: {money(total['cash_sales'])}",
                  f"В среднем за смену: {money(round(total['revenue'] / len(closed)))}"]
    else:
        lines.append("Выручка станет известна, когда сдадут смену.")
    shortage = sum(-d for s in closed if (d := s.difference()) and d < 0)
    if shortage:
        lines.append(f"❗ Недостачи: {money(shortage)}")
    count, delivered, taken = _deliveries_totals(ctx.db, first, last + timedelta(days=1))
    if count:
        lines.append(f"🚚 Доставок: {count} (доставлено {delivered}), взяли за доставки {money(taken)}")
    if len(shifts) > len(closed):
        lines.append("Несданные смены в сумму не входят.")
    return "\n".join(lines)


def cmd_today(ctx: BotContext, args: str) -> str:
    today = business_day(ctx.now, ctx.change)
    start, end = window(today, ctx.change)
    parts = [f"🌸 Смена {start:%d.%m %H:%M} → {end:%d.%m %H:%M}", cmd_shift(ctx, ""), "",
             cmd_revenue(ctx, "")]
    water = ctx.water.status(today, ctx.db.water_change(today) is not None, ctx.now, ctx.change)
    if water:
        done = ctx.db.water_change(today)
        parts.append("💧 Вода поменяна" + (f" ({_name(done[0])}, {done[1]:%H:%M})" if done else "") if water == "done"
                     else "💧 Сегодня день смены воды — ещё не поменяли")
    announcements = ctx.db.announcements(limit=1)
    if announcements and ctx.now - announcements[0].created_at < timedelta(days=1):
        parts.append(f"📢 Объявление: {len(announcements[0].readers)} прочитали")
    return "\n".join(p for p in parts if p is not None)


def cmd_shifts(ctx: BotContext, args: str) -> str:
    today = business_day(ctx.now, ctx.change)
    shifts = ctx.db.shifts_between(today - timedelta(days=14), today)[-10:]
    if not shifts:
        return "Смен пока не было."
    return "🗓 Последние смены:\n" + "\n".join(_shift_line(s, ctx) for s in reversed(shifts))


def cmd_staff(ctx: BotContext, args: str) -> str:
    return staff_text(ctx, *_period("месяц", ctx.now, ctx.change))


def staff_text(ctx: BotContext, first: date, last: date, title: str) -> str:
    shifts = ctx.db.shifts_between(first, last)
    if not shifts:
        return f"Смен {title} не было."
    lines = [f"👥 Сотрудники {title}:"]
    for u in ctx.db.users():
        own = [s for s in shifts if s.user_id == u.id]
        closed = [s for s in own if s.closed_at is not None]
        if own:
            revenue = sum(s.revenue for s in closed)
            avg = f", в среднем {money(round(revenue / len(closed)))}" if closed else ""
            lines.append(f"• {u.name or u.login}: смен {len(closed)}, приход {money(revenue)}{avg}")
    return "\n".join(lines)


def _calc(ctx: BotContext, user_id: int, first: date, last: date, month: str):
    """Зарплата сотрудника: санкции — его, списания — по всему магазину."""
    return calculate(ctx.db.shifts_between(first, last, user_id), ctx.db.adjustments(user_id, month),
                     ctx.rules(user_id), ctx.db.penalties_between(first, last, user_id),
                     shop_writeoff(ctx.db.shifts_between(first, last)))


def _conditions_lines(sal, rules: SalaryRules) -> list[str]:
    lines = []
    if rules.max_penalties > 0:
        lines.append(f"Санкций за месяц: {len(sal.penalties)} (для премии меньше {rules.max_penalties})")
    if sal.writeoff_percent is not None:
        base = "от всех цветов" if rules.writeoff_base == "total" else "от проданных"
        lines.append(f"Списания по магазину: {sal.writeoff_percent:.1f}% {base} "
                     f"(для премии меньше {rules.max_writeoff_percent:g}%)")
    if sal.blocked:
        lines.append("❗ Премия не начислена: " + "; ".join(sal.blocked) + f". Без этого было бы {money(sal.bonus_earned)}.")
    return lines


def _salary_detail(ctx: BotContext, user: User, first: date, last: date, month: str) -> str:
    rules = ctx.rules(user.id)
    sal = _calc(ctx, user.id, first, last, month)
    pieces = [(f"{KIND_TITLES[kind]} " if len(sal.base_parts) > 1 else "") + f"{count} × {money(rate)}"
              for kind, count, rate in sal.base_parts]
    lines = [f"💰 {user.name or user.login}, {month[5:]}.{month[:4]}",
             f"Смены: {' + '.join(pieces) or '0'} = {money(sal.base)}"]
    if sal.holiday_shifts:
        lines.append(f"🎉 Праздничных смен {sal.holiday_shifts} (выручка {money(sal.holiday_revenue)} в премию не идёт)")
    if rules.active_tiers():
        lines.append(f"Приход {money(sal.revenue)}, в среднем {money(round(sal.average))} за смену")
        if sal.tier is None:
            lines.append(f"План не выполнен (нужно от {money(sal.plan)} за смену) — премии нет")
        else:
            lines.append(f"Ступень «от {money(sal.tier.per_shift)}»: {sal.tier.percent:g}% с "
                         f"{money(sal.bonus_base)} = {money(sal.bonus_earned)}" + (" (не выплачивается)" if sal.blocked else ""))
        if sal.next_tier is not None and sal.shifts:
            lines.append(f"До «от {money(sal.next_tier.per_shift)}» ({sal.next_tier.percent:g}%) не хватает "
                         f"{money(round(sal.next_tier.per_shift - sal.average))} в среднем за смену")
    today = ctx.now.date()
    plan = forecast(sal, rules, today) if first <= today <= last else None
    if plan is not None:
        lines.append(f"🎯 Прогноз: ещё ~{plan.remaining_shifts} смен; для «от {money(plan.target.per_shift)}» нужно "
                     f"в среднем {money(plan.needed_average)} за смену")
    if sal.shortage:
        lines.append(f"Недостача: −{money(sal.shortage)}")
    for p in sal.penalties:
        lines.append(f"Санкция «{p.title}» ({p.shift_day:%d.%m}): −{money(p.amount)}"
                     + (" ✋ оспорена" if p.disputed_at and not p.dispute_result else ""))
    lines += _conditions_lines(sal, rules)
    for a in sal.adjustments:
        lines.append(f"{a.reason}: {'+' if a.amount > 0 else '−'}{money(abs(a.amount))}")
    lines.append(f"Итого: {money(sal.total)}")
    if sal.not_closed:
        lines.append(f"(несданные смены не учтены: {sal.not_closed})")
    return "\n".join(lines)


def cmd_salary(ctx: BotContext, args: str) -> str:
    first, last, _ = _period("месяц", ctx.now, ctx.change)
    if args.strip():
        user, error = find_user(ctx.db, args)
        return error if user is None else _salary_detail(ctx, user, first, last, f"{first:%Y-%m}")
    return salary_overview(ctx, first, last)


def salary_overview(ctx: BotContext, first: date, last: date) -> str:
    month = f"{first:%Y-%m}"
    lines = [f"💰 Зарплата, {month[5:]}.{month[:4]}" + (" (на сейчас):" if last >= ctx.now.date() else ":")]
    for u in ctx.db.users():
        shifts = ctx.db.shifts_between(first, last, u.id)
        if shifts:
            sal = _calc(ctx, u.id, first, last, month)
            lines.append(f"• {u.name or u.login}: {money(sal.total)}" + ("  ⚠ без премии" if sal.blocked else ""))
    if len(lines) == 1:
        return "В этом месяце смен ещё не было."
    return "\n".join(lines) + "\nПодробнее: /зарплата Имя"


def writeoff_text(ctx: BotContext, first: date, last: date, title: str) -> str:
    shop = shop_writeoff(ctx.db.shifts_between(first, last))
    rules = ctx.rules(None)
    lines = [f"🥀 Списания {title}"]
    if not shop.writeoff:
        return lines[0] + "\nСписаний не было."
    lines.append(f"Всего: {money(shop.writeoff)} = {shop.percent(rules.writeoff_base):.1f}% "
                 f"{'от всех цветов' if rules.writeoff_base == 'total' else 'от проданных'}")
    reasons = ctx.db.writeoff_by_reason(first, last)
    lines += [f"• {r}: {money(a)}" for r, a in sorted(reasons.items(), key=lambda x: -x[1])]
    known = sum(reasons.values())
    if known < shop.writeoff:
        lines.append(f"• без причины (старые смены): {money(shop.writeoff - known)}")
    if rules.max_writeoff_percent > 0:
        lines.append(f"Для премий нужно меньше {rules.max_writeoff_percent:g}%")
    return "\n".join(lines)


def cmd_writeoffs(ctx: BotContext, args: str) -> str:
    arg = args.strip() or "месяц"
    return writeoff_text(ctx, *_period(arg, ctx.now, ctx.change))


def ranking_text(ctx: BotContext, first: date, last: date, title: str) -> str:
    rank = monthly_ranking(ctx.db.shifts_between(first, last), ctx.rules(None))
    if rank.best_shift is None or not rank.best_shift.revenue:
        return f"🏆 Рейтинг {title}: сданных смен ещё нет."
    b = rank.best_shift
    lines = [f"🏆 Рейтинг {title}", f"Лучшая смена: {_name(b.user_name)}, {b.day:%d.%m} — {money(b.revenue)}"]
    if rank.best_average_name:
        lines.append(f"Лучший средний приход за смену: {rank.best_average_name} — {money(rank.best_average)}")
    return "\n".join(lines)


def cmd_rating(ctx: BotContext, args: str) -> str:
    return ranking_text(ctx, *_period(args.strip() or "месяц", ctx.now, ctx.change))


def cmd_problems(ctx: BotContext, args: str) -> str:
    first, last, title = _period("месяц", ctx.now, ctx.change)
    shifts = ctx.db.shifts_between(first, last)
    waters = ctx.db.water_changes_between(first, last)
    lines = []
    for s in shifts:
        status = s.status(ctx.now, ctx.change, ctx.grace)
        issues = []
        if status == "not_closed":
            issues.append("не сдана")
        elif status == "late":
            issues.append("сдана с опозданием")
        if s.difference():
            issues.append(_diff_words(s.difference()))
        handover = ctx.db.handover_difference(s)
        if handover:
            issues.append(f"при приёме кассы {_diff_words(handover)}")
        missed = [i.text for i in ctx.db.checklist(s) if not i.done] if s.closed_at else []
        if missed:
            issues.append("не сделано: " + ", ".join(missed))
        if ctx.water.status(s.day, s.day in waters, ctx.now, ctx.change) == "missed":
            issues.append("вода не поменяна")
        if issues:
            lines.append(f"• {s.day:%d.%m} {_name(s.user_name)}: " + "; ".join(issues))
    if not lines:
        return f"✅ {title.capitalize()}: проблем нет."
    shown = lines[-MAX_LIST:]
    more = f"\n…и ещё {len(lines) - MAX_LIST} раньше" if len(lines) > MAX_LIST else ""
    return f"❗ Проблемы {title}:\n" + "\n".join(shown) + more


def cmd_deliveries(ctx: BotContext, args: str) -> str:
    arg = args.strip().lower()
    today = ctx.now.date()
    day = today
    if arg in ("завтра", "tomorrow"):
        day = today + timedelta(days=1)
    elif arg in ("вчера", "yesterday"):
        day = today - timedelta(days=1)
    elif arg:
        m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", arg)
        if not m:
            return "Дата в виде 25.10 или слова: сегодня, завтра, вчера."
        year = int(m.group(3) or today.year)
        try:
            day = date(year + 2000 if year < 100 else year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return "Такой даты не бывает."
    items = ctx.db.for_day(day)
    if not items:
        return f"🚚 На {day:%d.%m} доставок нет."
    lines = [f"🚚 Доставки на {day:%d.%m}: {len(items)}, доставлено {sum(d.delivered for d in items)}"]
    for d in items[:MAX_LIST]:
        what = d.items.splitlines()[0] if d.items else "—"
        lines.append(f"{'✅' if d.delivered else '⏳'} {d.time or '—'} {what}, {d.address or 'адрес не указан'}"
                     + (f" ({money(d.price)})" if d.price else ""))
    if len(items) > MAX_LIST:
        lines.append(f"…и ещё {len(items) - MAX_LIST}")
    return "\n".join(lines)


def cmd_water(ctx: BotContext, args: str) -> str:
    today = business_day(ctx.now, ctx.change)
    every = max(ctx.water.every, 1)
    days_until = (every - (today - ctx.water.anchor).days % every) % every
    last_done = None
    first = today - timedelta(days=every * 3)
    done = ctx.db.water_changes_between(first, today)
    if done:
        last_done = max(done)
    lines = [f"💧 Вода меняется раз в {every} дн."]
    if days_until == 0:
        who = done.get(today)
        lines.append("Сегодня день замены — " + (f"поменяли ({_name(who[0])}, {who[1]:%H:%M})" if who else "ещё не поменяли"))
    else:
        lines.append(f"Следующая замена: {today + timedelta(days=days_until):%d.%m} (через {days_until} дн.)")
    if last_done:
        lines.append(f"Последний раз: {last_done:%d.%m}, {_name(done[last_done][0])}")
    return "\n".join(lines)


def cmd_duties(ctx: BotContext, args: str) -> str:
    today = business_day(ctx.now, ctx.change)
    shifts = ctx.db.shifts_between(today - timedelta(days=3), today)
    if not shifts:
        return "Смен за последние дни не было."
    s = shifts[-1]
    items = ctx.db.checklist(s)
    if not items:
        return "Обязанности не заданы."
    lines = [f"📋 Обязанности смены {s.day:%d.%m} ({_name(s.user_name)}): {sum(i.done for i in items)} из {len(items)}"]
    lines += [f"{'✅' if i.done else '❌'} {i.text}" for i in items]
    return "\n".join(lines)


def cmd_ann(ctx: BotContext, args: str) -> str:
    items = ctx.db.announcements(limit=5)
    if not items:
        return "Объявлений ещё не было. Чтобы написать — отправьте сообщение, начав с «!»."
    lines = ["📢 Последние объявления:"]
    for a in items:
        readers = ", ".join(f"{_name(n)} {t:%H:%M}" for n, t in a.readers) or "пока никто"
        lines.append(f"• {a.created_at:%d.%m %H:%M}: {' '.join(a.text.split())[:80]}\n   прочитали: {readers}")
    return "\n".join(lines)


def cmd_status(ctx: BotContext, args: str) -> str:
    unsent = len(ctx.db.unsent_messages(limit=100))
    lines = [f"🖥 Программа работает. Версия {ctx.version or '—'}, время на компьютере {ctx.now:%d.%m %H:%M}",
             f"Резервная копия: {ctx.last_backup:%d.%m %H:%M}" if ctx.last_backup else "Резервных копий ещё не было"]
    if unsent:
        lines.append(f"Сообщений в очереди на отправку: {unsent}")
    return "\n".join(lines)


def cmd_backup(ctx: BotContext, args: str) -> str:
    if ctx.backup_now is None:
        return "Резервные копии здесь недоступны."
    return "💾 " + ctx.backup_now()


def _delivery_title(d: Delivery) -> str:
    what = d.items.splitlines()[0] if d.items else "—"
    return f"№{d.id}: {d.day:%d.%m} {d.time or '—'}, {what}, {d.address or 'адрес не указан'}" \
           + (f" ({money(d.price)})" if d.price else "")


def _decide_delete(ctx: BotContext, args: str, approve: bool) -> str:
    try:
        delivery_id = int(args.strip())
    except ValueError:
        return "Укажите номер доставки, например: /del_17 (ссылка есть в сообщении о запросе)."
    d = ctx.db.get_delivery(delivery_id)
    if d is None:
        return "Такой доставки уже нет (возможно, её уже удалили)."
    if d.delete_requested_at is None:
        return "На эту доставку нет запроса на удаление (возможно, уже решён)."
    if approve:
        ctx.db.delete(delivery_id)
    else:
        ctx.db.cancel_delete_request(delivery_id)
    if ctx.audit:
        ctx.audit("Удаление доставки подтверждено" if approve else "Удаление доставки отклонено",
                  f"{_delivery_title(d)}; просил(а) {d.delete_requested_by_name or '—'}, решил(а) {ctx.author or 'Telegram'}")
    if ctx.decided:
        ctx.decided(d, approve, ctx.author)
    return ("🗑 Удалено: " if approve else "↩ Оставлено: ") + _delivery_title(d)


def cmd_del(ctx: BotContext, args: str) -> str:
    return _decide_delete(ctx, args, True)


def cmd_keep(ctx: BotContext, args: str) -> str:
    return _decide_delete(ctx, args, False)


def _month_bounds(day: date) -> tuple[date, date]:
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _apply_penalty(ctx: BotContext, shift: Shift, kind: PenaltyKind) -> str:
    who = f"{ctx.author} (Telegram)" if ctx.author else "Telegram"
    penalty = ctx.db.add_penalty(shift.id, kind.id, kind.title, kind.amount, who, ctx.now)
    if penalty is None:
        return f"Такая санкция за эту смену ({_name(shift.user_name)}, {shift.day:%d.%m}) уже поставлена."
    if ctx.audit:
        ctx.audit("Штрафная санкция поставлена",
                  f"{_name(shift.user_name)}, смена {shift.day:%d.%m}: {kind.title}, −{money(kind.amount)}; поставил(а) {ctx.author or 'Telegram'}")
    if ctx.penalty_changed:
        ctx.penalty_changed(penalty, True, ctx.author)
    first, last = _month_bounds(shift.day)
    month_items = ctx.db.penalties_between(first, last, shift.user_id)
    rules = ctx.rules(shift.user_id)
    lines = [f"⚠ Санкция записана: {_name(shift.user_name)}, смена {shift.day:%d.%m} — {kind.title}, −{money(kind.amount)}",
             f"За месяц ({_name(shift.user_name)}): {len(month_items)} шт. на {money(sum(p.amount for p in month_items))}"]
    if rules.max_penalties > 0:
        if len(month_items) >= rules.max_penalties:
            lines.append(f"❗ Премию за месяц это отменяет (для премии нужно меньше {rules.max_penalties}).")
        else:
            lines.append(f"Без потери премии можно ещё: {rules.max_penalties - 1 - len(month_items)}.")
    lines.append(f"Отменить: /unpen_{penalty.id}")
    return "\n".join(lines)


def cmd_pen(ctx: BotContext, args: str) -> str:
    """/pen_<смена>_<санкция> — ссылка из итогов смены."""
    try:
        shift_id, kind_id = (int(x) for x in args.split())
    except ValueError:
        return "Не понял ссылку. Санкцию можно поставить командой /санкция Имя Номер."
    shift = ctx.db.get_shift(shift_id)
    if shift is None:
        return "Такой смены уже нет."
    kind = next((k for k in ctx.penalty_kinds if k.id == kind_id), None)
    if kind is None:
        return "Такой санкции уже нет в списке (её убрали в настройках)."
    return _apply_penalty(ctx, shift, kind)


def cmd_sanction(ctx: BotContext, args: str) -> str:
    """/санкция Имя Номер — санкция последней смене сотрудника (когда выяснилось уже после итогов)."""
    m = re.fullmatch(r"(.+?)\s+(\d+)", args.strip())
    if not m:
        return "Укажите сотрудника и номер санкции, например: /санкция Анна 2\n\n" + _catalog_lines(ctx)
    user, error = find_user(ctx.db, m.group(1))
    if user is None:
        return error
    kind = next((k for k in ctx.penalty_kinds if k.id == int(m.group(2))), None)
    if kind is None:
        return "Нет санкции с таким номером.\n\n" + _catalog_lines(ctx)
    shift = ctx.db.last_shift_of(user.id)
    if shift is None:
        return f"У {user.name or user.login} ещё не было смен."
    return _apply_penalty(ctx, shift, kind)


def cmd_unpen(ctx: BotContext, args: str) -> str:
    try:
        penalty = ctx.db.delete_penalty(int(args.strip()))
    except ValueError:
        return "Укажите номер санкции."
    if penalty is None:
        return "Такой санкции уже нет (возможно, её уже отменили)."
    if ctx.audit:
        ctx.audit("Штрафная санкция снята", f"{_name(penalty.user_name)}, смена {penalty.shift_day:%d.%m}: "
                  f"{penalty.title}, −{money(penalty.amount)}; снял(а) {ctx.author or 'Telegram'}")
    if ctx.penalty_changed:
        ctx.penalty_changed(penalty, False, ctx.author)
    return f"↩ Санкция снята: {_name(penalty.user_name)}, смена {penalty.shift_day:%d.%m} — {penalty.title}"


def cmd_pok(ctx: BotContext, args: str) -> str:
    try:
        penalty = ctx.db.resolve_dispute(int(args.strip()))
    except ValueError:
        return "Укажите номер санкции."
    if penalty is None:
        return "Такой санкции уже нет (возможно, вы её уже сняли)."
    if ctx.audit:
        ctx.audit("Спор по санкции: оставлена", f"{_name(penalty.user_name)}, смена {penalty.shift_day:%d.%m}: "
                  f"{penalty.title}; решил(а) {ctx.author or 'Telegram'}")
    return (f"✔ Санкция остаётся: {_name(penalty.user_name)}, смена {penalty.shift_day:%d.%m} — {penalty.title}. "
            "Спор закрыт.")


def _catalog_lines(ctx: BotContext) -> str:
    if not ctx.penalty_kinds:
        return "Список санкций пуст — добавьте их в настройках (вкладка «Санкции и списания»)."
    return "Санкции (поставить: /санкция Имя Номер):\n" + "\n".join(
        f"{k.id} — {k.title}, {money(k.amount)}" for k in ctx.penalty_kinds)


def cmd_penalties(ctx: BotContext, args: str) -> str:
    first, last = _month_bounds(business_day(ctx.now, ctx.change))
    user = None
    if args.strip():
        user, error = find_user(ctx.db, args)
        if user is None:
            return error
    items = ctx.db.penalties_between(first, last, user.id if user else None)
    lines = [f"⚠ Штрафные санкции за {MONTHS[first.month - 1]}"]
    if not items:
        lines.append("Пока никого не штрафовали.")
    by_user: dict = {}
    for p in items:
        by_user.setdefault((p.user_id, p.user_name), []).append(p)
    for (user_id, name), group in by_user.items():
        rules = ctx.rules(user_id)
        status = ""
        if rules.max_penalties > 0:
            status = "  ❌ без премии" if len(group) >= rules.max_penalties else "  ✅ премия возможна"
        lines.append(f"• {_name(name)}: {len(group)} шт. на {money(sum(p.amount for p in group))}{status}")
        for p in group:
            lines.append(f"    {p.shift_day:%d.%m} {p.title} −{money(p.amount)} (отменить /unpen_{p.id})")
            if p.disputed_at and not p.dispute_result:
                lines.append(f"      ✋ оспорено: «{p.dispute_text}» — оставить /pok_{p.id}")
    rules = ctx.rules(None)
    if rules.max_writeoff_percent > 0:
        shop = shop_writeoff(ctx.db.shifts_between(first, last))
        percent = shop.percent(rules.writeoff_base)
        mark = "✅" if percent < rules.max_writeoff_percent else "❌ премий не будет"
        lines.append(f"\nСписания по магазину: {money(shop.writeoff)} = {percent:.1f}% "
                     f"(для премий меньше {rules.max_writeoff_percent:g}%) {mark}")
    lines += ["", _catalog_lines(ctx)]
    return "\n".join(lines)


COMMANDS: dict[str, Callable[[BotContext, str], str]] = {
    "del": cmd_del, "keep": cmd_keep, "pen": cmd_pen, "unpen": cmd_unpen, "sanction": cmd_sanction,
    "penalties": cmd_penalties, "pok": cmd_pok, "writeoffs": cmd_writeoffs, "rating": cmd_rating,
    "help": cmd_help, "shift": cmd_shift, "cash": cmd_cash, "revenue": cmd_revenue, "today": cmd_today,
    "shifts": cmd_shifts, "staff": cmd_staff, "salary": cmd_salary, "problems": cmd_problems,
    "deliveries": cmd_deliveries, "water": cmd_water, "duties": cmd_duties, "ann": cmd_ann,
    "status": cmd_status, "backup": cmd_backup,
    "week": lambda ctx, args: cmd_revenue(ctx, "неделя"),
    "month": lambda ctx, args: cmd_revenue(ctx, "месяц"),
}


# ----- обработчик с вопросами «да / нет» -----

@dataclass
class Pending:
    expires: datetime
    run: Callable[[], str]


@dataclass
class BotHandler:
    """Разбирает сообщения хозяина. Помнит неподтверждённую запись на 5 минут."""

    pending: dict[str, Pending] = field(default_factory=dict)  # у каждого чата свой вопрос «да / нет»

    def handle(self, text: str, ctx: BotContext, chat: str = "", author: str = "") -> str | None:
        """Ответ на сообщение или None, если это не команда (обычная переписка с ботом).
        chat — кто пишет (хозяев может быть несколько: «да» одного не подтверждает вопрос другого)."""
        parsed = parse_command(text)
        if parsed is None:
            return None
        ctx.author = author
        name, args = parsed
        if name in ("yes", "no"):
            return self._answer(name == "yes", ctx, chat)
        if name in ADJUSTMENTS:
            return self._ask_adjustment(name, args, ctx, chat, author)
        if name in COMMANDS:
            try:
                return COMMANDS[name](ctx, args)
            except Exception as e:  # noqa: BLE001 — бот не должен молчать при сбое
                return f"Не получилось выполнить команду: {e}"
        return "Не знаю такой команды. Список: /help"

    def _answer(self, yes: bool, ctx: BotContext, chat: str) -> str | None:
        pending = self.pending.pop(chat, None)
        if pending is None or ctx.now > pending.expires:
            return None  # вопроса нет или он устарел — «да» без вопроса ничего не делает
        return pending.run() if yes else "Отменено, ничего не записано."

    def _ask_adjustment(self, kind: str, args: str, ctx: BotContext, chat: str, author: str) -> str:
        title, sign, default_reason = ADJUSTMENTS[kind]
        amount = parse_amount(args)
        if amount is None or amount <= 0:
            return f"Укажите сумму, например: /{kind_ru(kind)} Анна 3000 за что"
        m = re.search(r"\d", args)
        name_part, rest = args[:m.start()].strip(), args[m.start():]
        user, error = find_user(ctx.db, name_part)
        if user is None:
            return error
        reason = re.sub(r"^\s*\d[\d\s]*(?:[.,]\d+)?\s*(?:к|k|тыс\w*)?\s*(?:₽|р\b|руб\w*)?\s*", "", rest,
                        flags=re.IGNORECASE).strip() or default_reason
        month = f"{business_day(ctx.now, ctx.change):%Y-%m}"
        db, now = ctx.db, ctx.now
        source = f"из Telegram: {author}" if author else "из Telegram"

        def run() -> str:
            try:
                db.add_adjustment(user.id, month, sign * amount, f"{reason} ({source})", None, now)
            except UserError as e:
                return f"Не записано: {e}"
            if ctx.audit:
                ctx.audit(f"{title} сотруднику", f"{user.name or user.login}: {'+' if sign > 0 else '−'}{money(amount)}, "
                                                 f"{reason} ({source})")
            return f"✅ Записано: {title.lower()} {user.name or user.login} {'+' if sign > 0 else '−'}{money(amount)} " \
                   f"({reason}). Видно в кабинете за {month[5:]}.{month[:4]}."

        self.pending[chat] = Pending(ctx.now + CONFIRM_FOR, run)
        return (f"Записать: {title.lower()} {user.name or user.login} {'+' if sign > 0 else '−'}{money(amount)} "
                f"за {MONTHS[int(month[5:]) - 1]} («{reason}»)?\nОтветьте «да» или «нет» (действует 5 минут).")


def kind_ru(kind: str) -> str:
    return {"advance": "аванс", "bonus": "премия", "fine": "штраф"}[kind]


def chunk(text: str, size: int = 3500) -> list[str]:
    """Telegram не принимает сообщения длиннее 4096 знаков — режем по строкам."""
    parts, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > size:
            parts.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts or [""]
