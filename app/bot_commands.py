"""Команды Telegram-боту: хозяин пишет /касса, /выручка, /зарплата Анна… — бот отвечает данными из программы.

Логика без Qt и без сети: на вход текст и контекст, на выход текст ответа. Команды, которые что-то
записывают (аванс, премия, штраф), сначала переспрашивают.
"""

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .db import Database, User, UserError
from .delivery_dialog import money
from .salary import SalaryRules, calculate
from .shifts import STATUS_TEXT, Shift, WaterSchedule, business_day, window

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


# ----- общие помощники -----

def parse_command(text: str) -> tuple[str, str] | None:
    """«/Касса@MyBot  аргументы» → («cash», «аргументы»); обычный текст — None."""
    text = text.strip()
    if not text:
        return None
    word, _, args = text.partition(" ")
    word = word.lstrip("/").split("@")[0].lower()
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
    lines += ["", "Записать: /аванс Анна 5000 на ремонт, /премия Анна 3000 за праздники, "
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
    first, last, title = _period(args, ctx.now, ctx.change)
    shifts = ctx.db.shifts_between(first, last)
    closed = [s for s in shifts if s.closed_at is not None]
    if not shifts:
        return f"Смен {title} нет."
    total = {k: sum(getattr(s, k) for s in closed) for k in ("revenue", "flowers", "terminal", "qr", "cash_sales")}
    lines = [f"📊 Выручка {title}", f"Сдано смен: {len(closed)} из {len(shifts)}"]
    if closed:
        lines += [f"Приход: {money(total['revenue'])}", f"Цветы: {money(total['flowers'])}",
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
    first, last, title = _period("месяц", ctx.now, ctx.change)
    shifts = ctx.db.shifts_between(first, last)
    if not shifts:
        return "В этом месяце смен ещё не было."
    lines = [f"👥 Сотрудники {title}:"]
    for u in ctx.db.users():
        own = [s for s in shifts if s.user_id == u.id]
        closed = [s for s in own if s.closed_at is not None]
        if own:
            revenue = sum(s.revenue for s in closed)
            avg = f", в среднем {money(round(revenue / len(closed)))}" if closed else ""
            lines.append(f"• {u.name or u.login}: смен {len(closed)}, приход {money(revenue)}{avg}")
    return "\n".join(lines)


def _salary_detail(ctx: BotContext, user: User, first: date, last: date, month: str) -> str:
    shifts = ctx.db.shifts_between(first, last, user.id)
    rules = ctx.rules(user.id)
    sal = calculate(shifts, ctx.db.adjustments(user.id, month), rules)
    lines = [f"💰 {user.name or user.login}, {month[5:]}.{month[:4]}",
             f"Смены: {sal.shifts} × {money(rules.shift_rate)} = {money(sal.base)}"]
    if rules.active_tiers():
        lines.append(f"Приход {money(sal.revenue)}, в среднем {money(round(sal.average))} за смену")
        if sal.tier is None:
            lines.append(f"План не выполнен (нужно от {money(sal.plan)} за смену) — премии нет")
        else:
            lines.append(f"Ступень «от {money(sal.tier.per_shift)}»: {sal.tier.percent:g}% с "
                         f"{money(sal.bonus_base)} = {money(sal.bonus)}")
        if sal.next_tier is not None and sal.shifts:
            lines.append(f"До «от {money(sal.next_tier.per_shift)}» ({sal.next_tier.percent:g}%) не хватает "
                         f"{money(round(sal.next_tier.per_shift - sal.average))} в среднем за смену")
    if sal.shortage:
        lines.append(f"Недостача: −{money(sal.shortage)}")
    for a in sal.adjustments:
        lines.append(f"{a.reason}: {'+' if a.amount > 0 else '−'}{money(abs(a.amount))}")
    lines.append(f"Итого: {money(sal.total)}")
    if sal.not_closed:
        lines.append(f"(несданные смены не учтены: {sal.not_closed})")
    return "\n".join(lines)


def cmd_salary(ctx: BotContext, args: str) -> str:
    first, last, _ = _period("месяц", ctx.now, ctx.change)
    month = f"{first:%Y-%m}"
    if args.strip():
        user, error = find_user(ctx.db, args)
        return error if user is None else _salary_detail(ctx, user, first, last, month)
    lines = [f"💰 Зарплата, {month[5:]}.{month[:4]} (на сейчас):"]
    for u in ctx.db.users():
        shifts = ctx.db.shifts_between(first, last, u.id)
        if shifts:
            sal = calculate(shifts, ctx.db.adjustments(u.id, month), ctx.rules(u.id))
            lines.append(f"• {u.name or u.login}: {money(sal.total)}")
    if len(lines) == 1:
        return "В этом месяце смен ещё не было."
    return "\n".join(lines) + "\nПодробнее: /зарплата Имя"


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


COMMANDS: dict[str, Callable[[BotContext, str], str]] = {
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
