from datetime import date
from urllib.parse import parse_qs, urlparse

from app.db import Database, Delivery
from app.messaging import DEFAULT_TEMPLATES, build_action, normalize_phone, render


def test_normalize_phone():
    assert normalize_phone("+7 (900) 123-45-67") == "79001234567"
    assert normalize_phone("8 900 123 45 67") == "79001234567"
    assert normalize_phone("900-123-45-67") == "79001234567"
    assert normalize_phone("+375 29 123-45-67") == "375291234567"
    assert normalize_phone("@anna") is None
    assert normalize_phone("123") is None


def test_render_placeholders():
    d = Delivery(day=date(2026, 10, 20), time="14:00", address="ул. Ленина, 10",
                 recipient="Анна", items="Букет роз\nоткрытка")
    text = render("{время} {адрес} {получатель} {что}", d)
    assert text == "14:00 ул. Ленина, 10 Анна Букет роз"
    assert "{" not in render(DEFAULT_TEMPLATES["delivered"], d)


def test_whatsapp_opens_chat_with_text():
    a = build_action("WhatsApp", "+7 900 123-45-67", "Привет & пока")
    assert not a.copy_text and not a.note
    url = urlparse(a.url)
    assert (url.netloc, url.path) == ("wa.me", "/79001234567")
    assert parse_qs(url.query)["text"] == ["Привет & пока"]


def test_whatsapp_bad_number_falls_back_to_clipboard():
    a = build_action("WhatsApp", "позвонить Ане", "текст")
    assert a.url is None and a.copy_text and a.note


def test_telegram():
    a = build_action("Telegram", "@anna_flowers", "Привет")
    assert a.url.startswith("https://t.me/anna_flowers?text=") and not a.copy_text
    a = build_action("Telegram", "+7 900 123-45-67", "Привет")  # по номеру чат не открыть
    assert a.url is None and a.copy_text
    a = build_action("Telegram", "https://t.me/anna", "Привет")
    assert a.url == "https://t.me/anna" and a.copy_text


def test_vk_max_other():
    a = build_action("ВКонтакте", "id12345", "x")
    assert a.url == "https://vk.com/id12345" and a.copy_text
    a = build_action("Макс", "+7 900 123-45-67", "x")
    assert a.url is None and a.copy_text
    a = build_action("Макс", "https://max.ru/abc", "x")
    assert a.url == "https://max.ru/abc" and a.copy_text


def test_only_web_links_are_opened():
    a = build_action("Другое", "file:///etc/passwd", "x")
    assert a.url is None
    a = build_action("Другое", "javascript:alert(1)", "x")
    assert a.url is None


def test_no_contact():
    a = build_action("WhatsApp", "", "x")
    assert a.url is None and a.copy_text and a.note


def test_messages_are_stored():
    db = Database(":memory:")
    d = Delivery(day=date(2026, 10, 20), items="Розы", source="WhatsApp", customer_contact="+7 900 1")
    db.add(d)
    db.mark_message(d.id, "departed")
    db.mark_message(d.id, "departed")
    db.mark_message(d.id, "delivered")
    stored = db.for_day(d.day)[0]
    assert stored.messages == "departed,delivered"
    assert stored.sent("departed") and stored.sent("delivered") and not stored.sent("review")
    assert (stored.source, stored.customer_contact) == ("WhatsApp", "+7 900 1")

    stored.customer_contact = "+7 900 2"
    db.update(stored)
    assert db.for_day(d.day)[0].customer_contact == "+7 900 2"
    assert db.for_day(d.day)[0].messages == "departed,delivered"  # правка не стирает историю
