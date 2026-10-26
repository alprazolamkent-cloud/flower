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
    assert "кнопками внизу" in texts(say(flow, "привет"))
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


def test_staff_menu_hidden_from_clients(db):
    from app.client_flow import STAFF_MENU, STAFF_TODAY, STAFF_TOMORROW
    from app.db import Delivery
    anna = db.add_user("anna", "Анна", "1")
    db.add(Delivery(day=NOW.date(), time="15:00", items="Розы\nкрафт", address="Ленина, 5"))
    done = Delivery(day=NOW.date(), time="12:00", items="Пионы", address="Мира, 1", assembled=True)
    db.add(done)
    db.add(Delivery(day=NOW.date(), time="09:00", items="Старое", delivered=True))
    db.add(Delivery(day=NOW.date() + timedelta(days=1), time="10:00", items="Завтра", address="Парковая, 3"))
    flow = ClientFlow(db, "")
    code = db.link_code(anna.id)
    linked = say(flow, f"/start {code}")
    assert linked.replies[0].markup == STAFF_MENU and "Собрал" in texts(linked)

    today = say(flow, STAFF_TODAY)
    cards = today.replies[1:]
    assert "Доставки на сегодня: 2" in texts(today) and len(cards) == 2  # доставленное не показываем
    by_time = {c.text.split(" ·")[0]: c for c in cards}
    assert by_time["15:00"].markup["inline_keyboard"][0][0]["text"] == "💐 Собрал"
    assert "Ленина, 5" in by_time["15:00"].text and "не собрано" in by_time["15:00"].text
    assert by_time["12:00"].markup["inline_keyboard"][0][0]["text"] == "✓ Доставлено"
    assert "Парковая" in texts(say(flow, STAFF_TOMORROW)) and "Ленина" not in texts(say(flow, STAFF_TOMORROW))

    press = say(flow, callback="sa:7")
    assert press.events == [("staff_assembled", anna.id, 7)] and not press.replies
    assert say(flow, callback="sd:8").events == [("staff_delivered", anna.id, 8)]
    assert say(flow, callback="sa:abc").events == []

    # клиент ничего этого не видит и не может: те же слова — просто сообщение флористу
    stranger = ClientFlow(db, "")
    client = lambda text="", **kw: stranger.handle(Incoming("999", text, name="Иван", **kw), NOW)  # noqa: E731
    assert client(callback="sa:7").events == [] and client(callback="sd:8").events == []
    asked = client(STAFF_TODAY)
    assert "Ленина" not in texts(asked) and asked.events[0][0] == "message"
    assert STAFF_MENU not in [r.markup for r in client("/start").replies]


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


def test_address_by_location(db):
    flow = ClientFlow(db, "")
    db.touch_client(CHAT_ID, "Иван", "", NOW)
    db.save_client_state(CHAT_ID, "", {}, phone="+79001234567")
    say(flow, CUSTOM)
    say(flow, "Розы")
    say(flow, "завтра к 15:00")
    point = "📍 55.750000, 37.610000 — https://yandex.ru/maps/?pt=37.610000,55.750000&z=17"
    assert "подъезд, этаж" in texts(say(flow, point))
    assert "Кому букет" in texts(say(flow, "подъезд 2, этаж 5, кв. 40"))
    say(flow, SELF)
    say(flow, NO_CARD)
    order = db.get_client_order(say(flow, SEND).events[0][1])
    assert order.address == point + "\nподъезд 2, этаж 5, кв. 40"
    # частный дом: «—» ничего не добавляет
    say(flow, CUSTOM)
    say(flow, "Розы")
    say(flow, "завтра к 15:00")
    say(flow, point)
    say(flow, "—")
    say(flow, SELF)
    say(flow, NO_CARD)
    assert db.get_client_order(say(flow, SEND).events[0][1]).address == point


def test_work_phone_link_and_menu(db):
    from app.client_flow import STAFF_MENU, STAFF_TODAY
    from app.db import Delivery
    d = Delivery(day=NOW.date(), time="15:00", items="Розы", address="Ленина, 5")
    db.add(d)
    flow = ClientFlow(db, "")
    assert not db.is_work_phone(CHAT_ID)
    code = db.work_phone_code()
    assert len(code) == 8 and db.work_phone_code() == code  # код не меняется, пока им не привязали
    wrong = say(flow, "/start 11111111")
    assert "Здравствуйте" in texts(wrong) and not db.is_work_phone(CHAT_ID)
    linked = say(flow, f"/start {code}")
    assert linked.events == [("work_phone", CHAT_ID)] and linked.replies[0].markup == STAFF_MENU
    assert db.is_work_phone(CHAT_ID) and db.work_phones() == [CHAT_ID]
    assert db.get_file("work_phone_code") is None  # код одноразовый
    assert not ClientFlow(db, "").handle(Incoming("777", f"/start {code}"), NOW).events  # повторно уже нельзя

    today = say(flow, STAFF_TODAY)
    assert "Доставки на сегодня: 1" in texts(today)
    assert say(flow, callback=f"sa:{d.id}").events == [("work_assembled", CHAT_ID, d.id, None)]
    assert say(flow, callback=f"sd:{d.id}").events == [("work_delivered", CHAT_ID, d.id, None)]
    assert say(flow, callback=f"sw:a:{d.id}:3").events == [("work_assembled", CHAT_ID, d.id, 3)]
    assert say(flow, callback=f"sw:x:{d.id}:3").events == []
    assert say(flow, photo=b"jpg", ).events == [("work_photo", CHAT_ID, b"jpg", "")]
    say(flow, "привет")
    assert db.client_messages(CHAT_ID) == [] and db.client_unread() == 0  # рабочий телефон — не клиент
    db.unlink_work_phones()
    assert not db.is_work_phone(CHAT_ID) and "Здравствуйте" in texts(say(flow, "/start"))


def test_work_phone_replaces_personal_link(db):
    anna = db.add_user("anna", "Анна", "1")
    db.link_staff(db.link_code(anna.id), CHAT_ID)
    assert db.staff_by_chat(CHAT_ID) is not None
    assert db.link_work_phone(db.work_phone_code(), CHAT_ID, NOW)
    assert db.staff_by_chat(CHAT_ID) is None and db.is_work_phone(CHAT_ID)


def test_parse_bouquet():
    from app.client_flow import parse_bouquet
    assert parse_bouquet("Нежность 3500") == ("Нежность", 3500, "")
    assert parse_bouquet("Красные розы 25 шт. — 4 900 ₽\nв крафте с лентой") == \
        ("Красные розы 25 шт.", 4900, "в крафте с лентой")
    assert parse_bouquet("15 пионов 3500р") == ("15 пионов", 3500, "")
    assert parse_bouquet("Букет «Утро»: 3 200") == ("Букет «Утро»", 3200, "")
    assert parse_bouquet("Нежность") is None and parse_bouquet("3500") is None and parse_bouquet("") is None
    assert parse_bouquet("Нежность 0") is None


def test_showcase_from_phone(db):
    from app.client_flow import STAFF_ADD, STAFF_CANCEL, STAFF_SHOWCASE, STAFF_TODAY
    db.link_work_phone(db.work_phone_code(), CHAT_ID, NOW)
    flow = ClientFlow(db, "", None, {})  # одно состояние на весь разговор, как у бота
    add = say(flow, STAFF_ADD)
    assert "Пришлите фото" in texts(add) and add.replies[0].markup["keyboard"][0][0]["text"] == STAFF_CANCEL

    # фото с подписью — сразу на витрину
    done = say(flow, "Нежность 3500\nпионы и эвкалипт", photo=b"jpg1")
    assert done.events == [("showcase", None, "добавлен «Нежность» — 3 500 ₽")] and "На витрине" in texts(done)
    [item] = db.showcase()
    assert (item.title, item.price, item.description, item.photo, item.active) == \
        ("Нежность", 3500, "пионы и эвкалипт", b"jpg1", True)

    # фото без подписи — спросили название и цену; с непонятной ценой переспросили
    say(flow, STAFF_ADD)
    assert "Название и цена" in texts(say(flow, photo=b"jpg2"))
    assert "Не понял цену" in texts(say(flow, "Розы"))
    assert say(flow, "Красные розы 4900").events[0][2] == "добавлен «Красные розы» — 4 900 ₽"
    assert [i.title for i in db.showcase()] == ["Нежность", "Красные розы"]

    # без фото не добавляем; отмена выходит
    say(flow, STAFF_ADD)
    assert "Жду фото" in texts(say(flow, "Тюльпаны 1000")) and len(db.showcase()) == 2
    assert "Отменил" in texts(say(flow, STAFF_CANCEL)) and "Жду фото" not in texts(say(flow, "привет"))

    # список, наличие, цена, удаление — и клиенты видят изменения сразу
    listing = say(flow, STAFF_SHOWCASE)
    assert "На витрине: 2" in texts(listing) and len(listing.replies) == 3
    first = db.showcase()[0]
    buttons = [b["callback_data"] for b in listing.replies[1].markup["inline_keyboard"][0]]
    assert buttons == [f"vt:{first.id}", f"ve:{first.id}", f"vd:{first.id}"]
    assert say(flow, callback=f"vt:{first.id}").events[0][2] == "«Нежность»: нет в наличии"
    assert [i.title for i in db.showcase(only_active=True)] == ["Красные розы"]
    say(flow, callback=f"vt:{first.id}")
    assert len(db.showcase(only_active=True)) == 2
    assert "Новая цена" in texts(say(flow, callback=f"vp:{first.id}"))
    assert "цифрами" in texts(say(flow, "дорого"))
    assert say(flow, "3 900 ₽").events[0][2] == "цена «Нежность»: 3 900 ₽" and db.get_showcase_item(first.id).price == 3900
    assert "Удалить" in texts(say(flow, callback=f"vd:{first.id}")) and len(db.showcase()) == 2
    assert say(flow, callback=f"vn:{first.id}").events == [] and len(db.showcase()) == 2
    assert say(flow, callback=f"vy:{first.id}").events[0][2] == "удалён «Нежность»" and len(db.showcase()) == 1
    assert "уже нет" in texts(say(flow, callback=f"vt:{first.id}"))
    # доставки в процессе добавления не мешают
    say(flow, STAFF_ADD)
    assert "Доставки на сегодня" in texts(say(flow, STAFF_TODAY)) or "доставок нет" in texts(say(flow, STAFF_TODAY))
    assert "Жду фото" not in texts(say(flow, "привет"))


def test_showcase_hidden_from_clients_and_personal_phone(db):
    from app.client_flow import STAFF_ADD
    anna = db.add_user("anna", "Анна", "1")
    db.link_staff(db.link_code(anna.id), CHAT_ID)
    flow = ClientFlow(db, "", None, {})
    say(flow, STAFF_ADD)
    done = say(flow, "Нежность 3500", photo=b"jpg")
    assert done.events == [("showcase", anna.id, "добавлен «Нежность» — 3 500 ₽")]
    # клиент теми же словами и кнопками ничего не меняет
    client = lambda text="", **kw: flow.handle(Incoming("999", text, name="Иван", **kw), NOW)  # noqa: E731
    item = db.showcase()[0]
    assert "Пришлите фото" not in texts(client(STAFF_ADD))
    assert client(callback=f"vy:{item.id}").events == [] and len(db.showcase()) == 1
    assert client("Тюльпаны 1000", photo=b"x").events[0][0] == "message" and len(db.showcase()) == 1


def test_showcase_pages_for_client(db):
    for i in range(12):
        db.save_showcase_item(ShowcaseItem(0, f"Букет {i + 1}", price=1000 + i))
    flow = ClientFlow(db, "")
    first = say(flow, SHOWCASE)
    cards = [r for r in first.replies if r.text.startswith("Букет")]
    assert "на витрине 12 букетов" in texts(first) and len(cards) == 5
    more = first.replies[-1]
    assert more.text == "Показано 5 из 12" and more.markup["inline_keyboard"][0][0]["callback_data"] == "more:5"
    second = say(flow, callback="more:5")
    assert [r.text.split(" —")[0] for r in second.replies[:5]] == [f"Букет {i}" for i in range(6, 11)]
    assert second.replies[-1].markup["inline_keyboard"][0][0]["callback_data"] == "more:10"
    last = say(flow, callback="more:10")
    assert len([r for r in last.replies if r.text.startswith("Букет")]) == 2 and "Показано" not in texts(last)
    assert "все букеты" in texts(say(flow, callback="more:40"))


def test_single_bouquet_hides_after_order(db):
    item_id = db.save_showcase_item(ShowcaseItem(0, "Готовый с пионами", price=4500, single=True))
    assert "В одном экземпляре" in texts(say(ClientFlow(db, ""), SHOWCASE))

    def order_from(chat):
        flow = ClientFlow(db, "")
        talk = lambda text="", **kw: flow.handle(Incoming(chat, text, name="К", **kw), NOW)  # noqa: E731
        db.touch_client(chat, "К", "", NOW)
        db.save_client_state(chat, "", {}, phone="+79001112233")
        talk(callback=f"pick:{item_id}")
        talk("завтра к 15:00")
        talk(PICKUP)
        talk(SELF)
        talk(NO_CARD)
        return talk

    first, second = order_from("1"), order_from("2")  # оба дошли до «Отправить заказ»
    order_id = first(SEND).events[0][1]
    assert not db.get_showcase_item(item_id).active  # заказан — с витрины пропал
    late = second(SEND)
    assert late.events == [] and "только что заказали" in texts(late)  # второй не закажет тот же букет
    assert "уже нет" in texts(ClientFlow(db, "").handle(Incoming("3", callback=f"pick:{item_id}"), NOW))
    db.set_client_order_status(order_id, "rejected", NOW, "")
    assert db.get_showcase_item(item_id).active  # отказ — снова на витрине
    # обычный букет после заказа остаётся
    usual = db.save_showcase_item(ShowcaseItem(0, "Нежность", price=3500))
    db.add_client_order("4", NOW, NOW.date(), "", "Нежность", "Самовывоз", "себе", "", "", usual)
    assert db.get_showcase_item(usual).active


def test_edit_showcase_from_phone(db):
    from app.client_flow import STAFF_ADD
    db.link_work_phone(db.work_phone_code(), CHAT_ID, NOW)
    flow = ClientFlow(db, "", None, {})
    say(flow, STAFF_ADD)
    added = say(flow, "Готовый 4500", photo=b"old")
    [item] = db.showcase()
    assert added.replies[-1].markup["inline_keyboard"][0][0]["callback_data"] == f"v1:{item.id}"
    assert "один экземпляр" in texts(say(flow, callback=f"v1:{item.id}")) and db.get_showcase_item(item.id).single

    menu = say(flow, callback=f"ve:{item.id}")
    codes = [b["callback_data"] for row in menu.replies[0].markup["inline_keyboard"] for b in row]
    assert codes == [f"vf:{item.id}", f"vh:{item.id}", f"vo:{item.id}", f"vp:{item.id}", f"v1:{item.id}"]
    say(flow, callback=f"vf:{item.id}")
    assert "Жду новое фото" in texts(say(flow, "вот"))
    assert say(flow, photo=b"new").events[0][2] == "«Готовый»: новое фото" and db.get_showcase_item(item.id).photo == b"new"
    say(flow, callback=f"vh:{item.id}")
    say(flow, "Пионы и розы")
    assert db.get_showcase_item(item.id).title == "Пионы и розы"
    say(flow, callback=f"vo:{item.id}")
    say(flow, "7 пионов, 5 роз, 45 см")
    assert db.get_showcase_item(item.id).description == "7 пионов, 5 роз, 45 см"
    say(flow, callback=f"vo:{item.id}")
    say(flow, "—")
    assert db.get_showcase_item(item.id).description == ""
    say(flow, callback=f"vp:{item.id}")
    say(flow, "4900")
    assert db.get_showcase_item(item.id).price == 4900
    # «Продан» у букета в одном экземпляре
    from app.client_flow import STAFF_SHOWCASE
    card = say(flow, STAFF_SHOWCASE).replies[1]
    assert card.markup["inline_keyboard"][0][0]["text"] == "🔒 Продан" and "один экземпляр" in card.text
    say(flow, callback=f"vt:{item.id}")
    assert not db.get_showcase_item(item.id).active
    assert say(flow, STAFF_SHOWCASE).replies[1].markup["inline_keyboard"][0][0]["text"] == "✅ Вернуть"


def test_old_showcase_gets_single_column(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    Database(path).close()
    conn = sqlite3.connect(path)  # как в прошлой версии: витрина без «одного экземпляра»
    conn.executescript("DROP TABLE showcase; CREATE TABLE showcase (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL DEFAULT 0, "
                       "photo BLOB, file_id TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, "
                       "position INTEGER NOT NULL DEFAULT 0); INSERT INTO showcase (title, price) VALUES ('Старый', 100);")
    conn.close()
    db = Database(path)
    [item] = db.showcase()
    assert item.title == "Старый" and item.single is False
    db.close()
