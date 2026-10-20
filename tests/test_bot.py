"""Команды Telegram-боту."""

from datetime import date, datetime, time, timedelta

import pytest

from app import bot_commands as bc
from app import telegram
from app.bot_commands import BotContext, BotHandler, chunk, find_user, parse_amount, parse_command
from app.db import Database, Delivery
from app.salary import SalaryRules, Tier
from app.shifts import WaterSchedule

CH = time(8, 0)
NOW = datetime(2026, 10, 20, 15, 0)  # смена 20.10 идёт с 8:00


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def make_ctx(db, now=NOW, **kw):
    rules = SalaryRules(2000, [Tier(30000, 5), Tier(40000, 7)])
    return BotContext(db=db, now=now, change=CH, grace=30, water=WaterSchedule(date(2026, 10, 18), 2),
                      rules=lambda uid: rules, version="1.18", **kw)


def shift(db, user, day, revenue=30000, closing=17000, flowers=24000, cash=12000):
    s = db.open_shift(user.id, 5000, datetime.combine(day, time(8, 5)), CH)
    return db.close_shift(s.id, datetime.combine(day + timedelta(days=1), time(7, 50)), closing, cash, 0,
                          revenue=revenue, flowers=flowers, terminal=15000, qr=3000)


@pytest.fixture
def shop(db):
    anna, oleg = db.add_user("anna", "Анна", "1"), db.add_user("oleg", "Олег Иванов", "1")
    shift(db, anna, date(2026, 10, 18), revenue=36000)
    shift(db, oleg, date(2026, 10, 19), revenue=28000, closing=16700)  # недостача 300
    db.open_shift(anna.id, 17000, datetime(2026, 10, 20, 8, 10), CH)  # сейчас на смене
    return db


def ask(db, text, ctx=None, handler=None):
    return (handler or BotHandler()).handle(text, ctx or make_ctx(db))


# ----- разбор -----

def test_parse_command():
    assert parse_command("/касса") == ("cash", "")
    assert parse_command("/Cash@MyShopBot") == ("cash", "")
    assert parse_command("/зарплата  Анна Петрова") == ("salary", "Анна Петрова")
    assert parse_command("/revenue вчера") == ("revenue", "вчера")
    assert parse_command("привет") is None  # обычная переписка с ботом
    assert parse_command("да") == ("yes", "") and parse_command("Нет") == ("no", "")
    assert parse_command("! объявление") is None
    assert parse_command("   ") is None


def test_parse_amount():
    assert [parse_amount(t) for t in ("5000", "5 000", "5000р", "5к", "2,5к", "3000 ₽ за ремонт")] == \
        [5000, 5000, 5000, 5000, 2500, 3000]
    assert parse_amount("без суммы") is None


def test_find_user(shop):
    assert find_user(shop, "анна")[0].login == "anna"
    assert find_user(shop, "Олег")[0].login == "oleg"  # по началу имени
    assert find_user(shop, "oleg")[0].name == "Олег Иванов"
    user, error = find_user(shop, "Мария")
    assert user is None and "Анна" in error and "Олег" in error
    assert find_user(shop, "")[0] is None


# ----- чтение -----

def test_help_lists_all_menu_commands(db):
    text = ask(db, "/help")
    assert all(f"/{name}" in text for name, _ in bc.MENU) and "/аванс" in text


def test_unknown_command(db):
    assert "Не знаю" in ask(db, "/абракадабра")
    assert ask(db, "просто текст") is None


def test_shift_and_cash(shop):
    assert "Анна" in ask(shop, "/смена") and "до 08:00" in ask(shop, "/смена")
    cash = ask(shop, "/касса")
    assert "Олег Иванов" in cash and "в кассе оставлено 16 700 ₽" in cash and "недостача 300 ₽" in cash
    assert "Сейчас на смене Анна" in cash and "17 000 ₽" in cash


def test_revenue_periods(shop):
    month = ask(shop, "/выручка месяц")
    assert "Приход: 64 000 ₽" in month and "Сдано смен: 2 из 3" in month and "В среднем за смену: 32 000 ₽" in month
    assert "Недостачи: 300 ₽" in month and "Несданные смены" in month
    week = ask(shop, "/week")
    assert "за 7 дней" in week and "Приход: 64 000 ₽" in week and week == ask(shop, "/неделя")
    yesterday = ask(shop, "/выручка вчера")  # вчера (19.10) была смена Олега
    assert "Приход: 28 000 ₽" in yesterday and "недостачи: 300 ₽" in yesterday.lower()
    today = ask(shop, "/выручка")  # сегодняшняя смена ещё не сдана — в сумму не входит
    assert "Сдано смен: 0 из 1" in today and "станет известна, когда сдадут смену" in today
    assert "Приход" not in today  # нулей вместо «ещё не знаем» не показываем


def test_revenue_nothing(db):
    assert "нет" in ask(db, "/выручка вчера")


def test_staff_and_salary(shop):
    staff = ask(shop, "/сотрудники")
    assert "Анна: смен 1, приход 36 000 ₽" in staff and "Олег Иванов: смен 1, приход 28 000 ₽" in staff
    everyone = ask(shop, "/зарплата")
    assert "• Анна: 3 800 ₽" in everyone and "• Олег Иванов: 1 700 ₽" in everyone and "/зарплата Имя" in everyone
    detail = ask(shop, "/зарплата Анна")
    # смена 2000 + 5% с 36 000 = 1800
    assert "Смены: 1 × 2 000 ₽ = 2 000 ₽" in detail and "5% с 36 000 ₽ = 1 800 ₽" in detail
    assert "Итого: 3 800 ₽" in detail and "До «от 40 000 ₽» (7%) не хватает 4 000 ₽" in detail
    oleg = ask(shop, "/зарплата Олег")
    assert "План не выполнен" in oleg and "Недостача: −300 ₽" in oleg and "Итого: 1 700 ₽" in oleg
    assert "Такого сотрудника нет" in ask(shop, "/зарплата Мария")


def test_problems(shop):
    text = ask(shop, "/проблемы")
    assert "Олег Иванов: недостача 300 ₽" in text and "при приёме кассы" in text
    assert "проблем нет" in ask(Database(":memory:"), "/проблемы")


def test_deliveries(shop):
    shop.add(Delivery(day=date(2026, 10, 20), time="12:00", items="Букет роз", address="ул. Ленина, 10",
                      price=500, delivered=True))
    shop.add(Delivery(day=date(2026, 10, 21), time="09:00", items="Тюльпаны", address="пр. Мира, 2"))
    today = ask(shop, "/доставки")
    assert "Доставки на 20.10: 1, доставлено 1" in today and "✅ 12:00 Букет роз, ул. Ленина, 10 (500 ₽)" in today
    assert "⏳ 09:00 Тюльпаны" in ask(shop, "/доставки завтра") and "⏳ 09:00 Тюльпаны" in ask(shop, "/доставки 21.10")
    assert "нет" in ask(shop, "/доставки 25.12")
    assert "Дата в виде" in ask(shop, "/доставки когда-нибудь") and "не бывает" in ask(shop, "/доставки 31.02")


def test_water_duties_ann_status(shop):
    ctx = make_ctx(shop)
    water = ask(shop, "/вода", ctx)
    assert "раз в 2 дн." in water and "Сегодня день замены — ещё не поменяли" in water
    anna = shop.users()[0]
    shop.mark_water(date(2026, 10, 20), anna.id, NOW)
    assert "Сегодня день замены — поменяли (Анна, 15:00)" in ask(shop, "/вода", ctx)
    assert "Следующая замена: 22.10" in ask(shop, "/вода", make_ctx(shop, now=NOW + timedelta(days=1)))
    assert "не заданы" in ask(shop, "/обязанности")
    shop.save_duties([(None, "Вынести мусор")])
    assert "Вынести мусор" in ask(shop, "/обязанности")
    assert "Объявлений ещё не было" in ask(shop, "/объявления")
    shop.add_announcement("Розы по 150", NOW)
    assert "Розы по 150" in ask(shop, "/объявления") and "пока никто" in ask(shop, "/объявления")
    status = ask(shop, "/статус", make_ctx(shop, last_backup=datetime(2026, 10, 20, 9, 0)))
    assert "Версия 1.18" in status and "Резервная копия: 20.10 09:00" in status


def test_today_summary(shop):
    text = ask(shop, "/сегодня")
    assert "Смена 20.10 08:00 → 21.10 08:00" in text and "Анна" in text and "Выручка" in text


def test_backup_command(db):
    assert "недоступны" in ask(db, "/копия")
    assert "Копия сделана" in ask(db, "/backup", make_ctx(db, backup_now=lambda: "Копия сделана."))


# ----- запись с подтверждением -----

def test_advance_needs_confirmation(shop):
    handler = BotHandler()
    question = handler.handle("/аванс Анна 5к на ремонт", make_ctx(shop))
    assert "Записать: аванс Анна −5 000 ₽" in question and "на ремонт" in question
    assert shop.adjustments(1, "2026-10") == []  # пока не подтвердили — ничего не записано
    answer = handler.handle("да", make_ctx(shop))
    assert "Записано: аванс" in answer
    [adj] = shop.adjustments(1, "2026-10")
    assert adj.amount == -5000 and adj.reason == "на ремонт (из Telegram)"
    assert handler.handle("да", make_ctx(shop)) is None  # второе «да» ничего не делает


def test_bonus_and_fine_signs(shop):
    handler = BotHandler()
    handler.handle("/премия Олег Иванов 3000 за праздники", make_ctx(shop))
    handler.handle("да", make_ctx(shop))
    handler.handle("/штраф Олег 500", make_ctx(shop))
    handler.handle("да", make_ctx(shop))
    adjustments = shop.adjustments(2, "2026-10")
    assert [(a.amount, a.reason) for a in adjustments] == \
        [(3000, "за праздники (из Telegram)"), (-500, "штраф (из Telegram)")]


def test_adjustment_cancel_expire_and_errors(shop):
    handler = BotHandler()
    handler.handle("/аванс Анна 1000", make_ctx(shop))
    assert "Отменено" in handler.handle("нет", make_ctx(shop)) and shop.adjustments(1, "2026-10") == []
    handler.handle("/аванс Анна 1000", make_ctx(shop))
    late = make_ctx(shop, now=NOW + timedelta(minutes=6))
    assert handler.handle("да", late) is None and shop.adjustments(1, "2026-10") == []  # вопрос устарел
    assert "Укажите сумму" in handler.handle("/аванс Анна", make_ctx(shop))
    assert "Такого сотрудника нет" in handler.handle("/аванс Мария 500", make_ctx(shop))
    assert handler.pending is None


def test_new_question_replaces_old(shop):
    handler = BotHandler()
    handler.handle("/аванс Анна 1000", make_ctx(shop))
    handler.handle("/аванс Олег 2000", make_ctx(shop))
    handler.handle("да", make_ctx(shop))
    assert shop.adjustments(1, "2026-10") == [] and [a.amount for a in shop.adjustments(2, "2026-10")] == [-2000]


# ----- сообщения -----

def test_chunk_splits_long_text():
    text = "\n".join(f"строка {i}" * 20 for i in range(100))
    parts = chunk(text, 3500)
    assert len(parts) > 1 and all(len(p) <= 3500 for p in parts) and "\n".join(parts) == text
    assert chunk("") == [""]


def test_inbox_answers_only_owner_and_fresh(db, monkeypatch):
    replies = []
    monkeypatch.setattr(telegram.QTimer, "singleShot", lambda *a: None)
    inbox = telegram.TelegramInbox.__new__(telegram.TelegramInbox)
    inbox.db, inbox.busy = db, False
    inbox.handler = lambda text: f"ответ на {text}"
    inbox.reply = replies.append
    now = int(datetime.now().timestamp())

    def update(uid, chat, text, ts=now):
        return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text, "date": ts}}

    inbox._answer_commands([update(1, 777, "/касса"), update(2, 999, "/касса"), update(3, 777, "! объявление"),
                            update(4, 777, "/смена", ts=now - 3600)], "777")
    assert replies[0] == "ответ на /касса" and "пропустила" in replies[1] and len(replies) == 2


def test_menu_matches_commands():
    assert {name for name, _ in bc.MENU} <= set(bc.COMMANDS) | {"help"}
    assert all(name.isascii() and name.islower() for name, _ in bc.MENU)  # Telegram принимает в меню только латиницу
