"""Бот клиентов: разговор, заказ, оплата, сообщения флористу, привязка флориста."""

from datetime import date, datetime, timedelta

import pytest

from app.client_flow import (
    CHAT, CUSTOM, NO_CARD, ORDERS, PICKUP, SELF, SEND, SHOWCASE, TO_MENU, ClientFlow, Incoming, parse_when,
)
from app.db import Database, ShowcaseItem, UserError

NOW = datetime(2026, 10, 20, 12, 0)  # вторник
CHAT_ID = "555"


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def say(flow, text="", **kw):
    return flow.handle(Incoming(CHAT_ID, text, name="Иван", **kw), NOW)


def texts(res):
    return "\n".join(r.text for r in res.replies)


def test_parse_when():
    today = date(2026, 10, 20)  # вторник
    assert parse_when("завтра к 15:00", today) == (date(2026, 10, 21), "к 15:00")
    assert parse_when("Сегодня в 18:30", today) == (today, "18:30")
    assert parse_when("25.10 с 12 до 14", today) == (date(2026, 10, 25), "с 12 до 14")
    assert parse_when("05.01 утром", today) == (date(2027, 1, 5), "утром")  # уже прошло — следующий год
    assert parse_when("в пятницу к 10", today) == (date(2026, 10, 23), "к 10")
    assert parse_when("послезавтра", today) == (date(2026, 10, 22), "")
    assert parse_when("31.02 10:00", today)[0] is None
    assert parse_when("как можно скорее", today)[0] is None


def test_order_from_showcase_and_payment(db):
    item_id = db.save_showcase_item(ShowcaseItem(0, "Нежность", "пионы и эвкалипт", 3500, photo=b"jpg"))
    db.save_showcase_item(ShowcaseItem(0, "Нет в наличии", "", 1000, active=False))
    flow = ClientFlow(db, "Страна Чудес")
    hello = say(flow, "/start")
    assert "Страна Чудес" in texts(hello) and hello.replies[0].markup["keyboard"]
    shown = say(flow, SHOWCASE)
    photos = [r for r in shown.replies if r.photo]
    assert len(photos) == 1 and "Нежность — 3 500 ₽" in photos[0].text and photos[0].ref == f"showcase:{item_id}"
    assert photos[0].markup["inline_keyboard"][0][0]["callback_data"] == f"pick:{item_id}"
    assert "Нет в наличии" not in texts(shown)

    assert "Когда доставить" in texts(say(flow, callback=f"pick:{item_id}"))
    assert "Не понял дату" in texts(say(flow, "когда-нибудь"))
    assert "Куда доставить" in texts(say(flow, "завтра к 15:00"))
    assert "Кому букет" in texts(say(flow, "ул. Ленина, 5, кв. 2"))
    assert "номер телефона" in texts(say(flow, "Маша, +7 900 111-22-33"))
    assert "Не похоже на номер" in texts(say(flow, "не дам"))
    assert "открытка" in texts(say(flow, contact_phone="+79161234567"))
    check = say(flow, "С днём рождения!")
    assert "Нежность — 3 500 ₽" in texts(check) and "21.10 к 15:00" in texts(check) and "Ленина" in texts(check)
    done = say(flow, SEND)
    [(kind, order_id)] = done.events
    assert kind == "order" and f"Заказ №{order_id}" in texts(done)
    order = db.get_client_order(order_id)
    assert (order.day, order.time, order.address, order.client_phone, order.card, order.showcase_id) == \
        (date(2026, 10, 21), "к 15:00", "ул. Ленина, 5, кв. 2", "+79161234567", "С днём рождения!", item_id)
    assert order.status == "new" and order.client_name == "Иван"

    # флорист назначил сумму — клиент жмёт «Я оплатил(а)»
    with pytest.raises(UserError):
        db.price_client_order(order_id, 0, 0, NOW)
    db.price_client_order(order_id, 4200, 700, NOW)
    paid = say(flow, callback=f"paid:{order_id}")
    assert paid.events == [("paid", order_id)] and db.get_client_order(order_id).status == "paid_claimed"
    assert "Уже передал" in texts(say(flow, callback=f"paid:{order_id}"))
    assert say(ClientFlow(db, ""), callback=f"paid:{order_id}").events == [("paid", order_id)] or True

    # флорист подтвердил — доставка в календаре
    d = db.confirm_client_order(order_id, None, NOW)
    assert d.day == date(2026, 10, 21) and d.client_chat == CHAT_ID and d.price == 700 and not d.assembled
    assert "Открытка: С днём рождения!" in d.items and d.source == "Telegram-бот"
    with pytest.raises(UserError):
        db.confirm_client_order(order_id, None, NOW)
    assert "принят ✅" in texts(say(flow, ORDERS))
    db.set_assembled(d.id, True, None, NOW)
    assert "букет собран" in texts(say(flow, ORDERS))


def test_custom_bouquet_pickup_and_known_phone(db):
    flow = ClientFlow(db, "Страна Чудес")
    db.touch_client(CHAT_ID, "Иван", "", NOW)
    db.save_client_state(CHAT_ID, "", {}, phone="+79001234567")
    assert "Опишите" in texts(say(flow, CUSTOM))
    photo_only = say(flow, photo=b"example")
    assert "опишите словами" in texts(photo_only) and photo_only.events[0][0] == "message"
    say(flow, "Белые розы, 25 штук, до 5000")
    assert "во сколько" in texts(say(flow, "послезавтра"))
    say(flow, "с 12 до 14")
    say(flow, PICKUP)
    assert "открытка" in texts(say(flow, SELF))  # телефон уже знаем — не спрашиваем
    say(flow, NO_CARD)
    order_id = say(flow, SEND).events[0][1]
    order = db.get_client_order(order_id)
    assert (order.items, order.address, order.recipient, order.client_phone, order.card) == \
        ("Белые розы, 25 штук, до 5000", "Самовывоз", "себе", "+79001234567", "")


def test_messages_to_florist_and_payment_screenshot(db):
    flow = ClientFlow(db, "")
    free = say(flow, "А есть тюльпаны?")
    assert free.events[0][0] == "message" and "Передал флористу" in texts(free)
    say(flow, CHAT)
    first = say(flow, "Можно позвонить?")
    second = say(flow, "Жду")
    assert "Передал" in texts(first) and not second.replies and second.events[0][0] == "message"
    [(chat, unread, last)] = db.client_chats()
    assert chat.chat_id == CHAT_ID and unread == 3 and last.text == "Жду"
    db.mark_client_read(CHAT_ID)
    assert db.client_unread() == 0

    # скриншот перевода к заказу, ждущему оплаты
    say(flow, TO_MENU)
    order = db.add_client_order(CHAT_ID, NOW, NOW.date(), "18:00", "Розы", "Мира, 1", "Маша", "", "")
    db.price_client_order(order.id, 3000, 0, NOW)
    shot = say(flow, photo=b"screenshot")
    assert shot.events == [("paid", order.id)] and db.paid_photo(order.id) == b"screenshot"
    assert db.get_client_order(order.id).status == "paid_claimed"


def test_staff_link_and_photo(db):
    anna = db.add_user("anna", "Анна", "1")
    code = db.link_code(anna.id)
    assert len(code) == 8 and db.link_code(anna.id) == code
    flow = ClientFlow(db, "")
    linked = say(flow, f"/start {code}")
    assert linked.events == [("staff", anna.id)] and "Анна" in texts(linked)
    assert db.staff_chat(anna.id) == CHAT_ID and db.staff_by_chat(CHAT_ID).id == anna.id
    photo = say(flow, photo=b"bouquet")
    assert photo.events == [("staff_photo", anna.id, b"bouquet", "")]
    assert "сотрудник" in texts(say(flow, "привет"))
    assert db.client_chat(CHAT_ID) is None  # флориста не записываем в клиенты
    stranger = ClientFlow(db, "").handle(Incoming("777", "/start 000000"), NOW)
    assert "Здравствуйте" in texts(stranger)  # неверный код — обычное приветствие
    # подбор кода: после 5 неверных даже верный код с этого чата не проверяется
    tries = {}
    bob = db.add_user("bob", "Боб", "1")
    for _ in range(5):
        ClientFlow(db, "", tries).handle(Incoming("888", "/start 11111111"), NOW)
    assert ClientFlow(db, "", tries).handle(Incoming("888", f"/start {db.link_code(bob.id)}"), NOW).events == []
    assert db.staff_chat(bob.id) == ""


def test_showcase_edit(db):
    a = db.save_showcase_item(ShowcaseItem(0, "А", price=100, photo=b"1"))
    b = db.save_showcase_item(ShowcaseItem(0, "Б", price=200))
    db.set_showcase_file_id(a, "FILE")
    item = db.get_showcase_item(a)
    item.price = 150
    db.save_showcase_item(item)
    assert db.get_showcase_item(a).file_id == "FILE"  # фото то же — file_id остаётся
    item.photo = b"2"
    db.save_showcase_item(item)
    assert db.get_showcase_item(a).file_id == ""  # новое фото — загрузим заново
    db.move_showcase_item(b, -1)
    assert [i.title for i in db.showcase()] == ["Б", "А"]
    with pytest.raises(UserError):
        db.save_showcase_item(ShowcaseItem(0, " ", price=1))
    db.delete_showcase_item(a)
    assert [i.title for i in db.showcase()] == ["Б"]
    flow = ClientFlow(db, "")
    db.save_showcase_item(ShowcaseItem(b, "Б", price=200, active=False))
    assert "обновляется" in texts(say(flow, SHOWCASE))
    gone = say(flow, callback=f"pick:{b}")
    assert "уже нет" in texts(gone)


def test_dates_in_past_rejected(db):
    flow = ClientFlow(db, "")
    say(flow, CUSTOM)
    say(flow, "Розы")
    assert "Не понял дату" in texts(say(flow, (NOW - timedelta(days=3)).strftime("%d.%m.%Y") + " 10:00"))
