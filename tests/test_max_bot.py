"""Бот MAX: разбор обновлений, кнопки, отправка с фото, общая очередь и окно «Клиенты» вместе с Telegram."""

import io
import sys
from datetime import datetime

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import max_bot
from app.client_bot import ClientBot, chat_configured
from app.client_flow import SEND, SHOWCASE
from app.db import Database, ShowcaseItem
from app.max_bot import MaxBot, keyboard, parse_update, vcf_phone

NOW = datetime(2026, 10, 20, 12, 0)
USER = {"user_id": 42, "first_name": "Ольга", "last_name": "К", "username": None, "is_bot": False}


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def store(monkeypatch):
    values = {"max_token": "mx", "client_token": "tg", "client_requisites": "Сбер +7 900"}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: values.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: values.__setitem__(key, value))
    monkeypatch.setattr(QSettings, "remove", lambda self, key: values.pop(key, None))
    monkeypatch.setattr(ClientBot, "poll", lambda self: None)
    return values


def created(body: dict, chat_type="dialog") -> dict:
    return {"update_type": "message_created", "timestamp": 1,
            "message": {"sender": USER, "recipient": {"chat_id": 7, "chat_type": chat_type, "user_id": 1}, "body": body}}


def test_parse_updates():
    text = parse_update(created({"mid": "m", "seq": 1, "text": " Привет "}))
    assert (text.message.chat_id, text.message.text, text.message.name) == ("max:42", "Привет", "Ольга К")
    photo = parse_update(created({"text": "пример", "attachments": [
        {"type": "image", "payload": {"url": "https://i.max.ru/1.jpg", "token": "t", "photo_id": 1}}]}))
    assert photo.photo_id == "https://i.max.ru/1.jpg" and photo.message.text == "пример"
    contact = parse_update(created({"text": "", "attachments": [{"type": "contact", "payload": {
        "vcf_info": "BEGIN:VCARD\nVERSION:3.0\nTEL;TYPE=cell:+79001112233\nEND:VCARD", "max_info": USER}}]}))
    assert contact.message.contact_phone == "+79001112233"
    place = parse_update(created({"text": None, "attachments": [{"type": "location", "latitude": 55.75,
                                                                   "longitude": 37.61}]}))
    assert "55.750000, 37.610000" in place.message.text
    assert parse_update(created({"text": "", "attachments": [{"type": "sticker", "payload": {}}]})).message is None
    assert parse_update(created({"text": "", "attachments": [{"type": "audio", "payload": {}}]})).unsupported
    assert parse_update(created({"text": "hi"}, chat_type="chat")).message is None  # группа
    started = parse_update({"update_type": "bot_started", "chat_id": 7, "user": USER, "payload": "12345678"})
    assert started.message.text == "/start 12345678" and started.message.chat_id == "max:42"
    assert parse_update({"update_type": "bot_started", "chat_id": 7, "user": USER}).message.text == "/start"
    press = parse_update({"update_type": "message_callback", "callback": {"callback_id": "cb", "payload": "pick:3",
                                                                          "user": USER}})
    assert press.callback_id == "cb" and press.message.callback == "pick:3"
    assert vcf_phone("TEL:8 (900) 111-22-33") == "8 (900) 111-22-33" and vcf_phone("") == ""


def test_keyboard_conversion():
    reply = {"keyboard": [[{"text": SHOWCASE}, {"text": "📱 Отправить мой номер", "request_contact": True}]]}
    assert keyboard(reply) == {"type": "inline_keyboard", "payload": {"buttons": [[
        {"type": "message", "text": SHOWCASE}, {"type": "request_contact", "text": "📱 Отправить мой номер"}]]}}
    pick = keyboard({"inline_keyboard": [[{"text": "🛒 Заказать", "callback_data": "pick:5"}]]})
    assert pick["payload"]["buttons"] == [[{"type": "callback", "text": "🛒 Заказать", "payload": "pick:5"}]]
    assert keyboard(None) is None and keyboard({"keyboard": []}) is None


def test_send_with_photo(monkeypatch):
    calls = []

    def fake_call(token, method, path, query=None, body=None, timeout=0, base=None):
        calls.append((method, path, query, body))
        if path == "uploads":
            return {"url": "https://upload.max.ru/x"}
        if path == "messages" and sum(c[1] == "messages" for c in calls) == 1:
            raise max_bot.MaxError("not ready", code="attachment.not.ready")
        return {"message": {"body": {"attachments": [{"type": "image", "payload": {"token": "IMG"}}]}}}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    uploaded = []
    monkeypatch.setattr(max_bot, "_call", fake_call)
    monkeypatch.setattr(max_bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(max_bot.urllib.request, "urlopen",
                        lambda req, timeout=0: uploaded.append(req.data) or Response(b'{"photos": {"a": {"token": "P"}}}'))
    image = max_bot.upload_image("mx", b"JPEG")
    assert image == {"photos": {"a": {"token": "P"}}} and b"JPEG" in uploaded[0] and b'name="data"' in uploaded[0]
    token = max_bot.send("mx", "42", "Нежность — 3 500 ₽", {"inline_keyboard": [[{"text": "🛒", "callback_data": "pick:1"}]]},
                         image)
    assert token == "IMG"
    method, path, query, body = calls[-1]
    assert (method, path, query) == ("POST", "messages", {"user_id": "42"})
    assert [a["type"] for a in body["attachments"]] == ["image", "inline_keyboard"]
    assert sum(c[1] == "messages" for c in calls) == 2  # «фото ещё обрабатывается» — повторили


def test_shared_queue_and_order(db, store, app, monkeypatch):
    tg, mx = ClientBot(db), MaxBot(db)
    events = []
    mx.event.connect(events.append)
    item = db.save_showcase_item(ShowcaseItem(0, "Нежность", price=3500, photo=b"jpg"))
    db.set_showcase_file_id(item, "TG-FILE")  # номер фото от Telegram — в MAX не годится
    db.queue_client("555", "клиенту в Telegram", NOW)
    mx.process([max_bot.Parsed(0, max_bot.Incoming("max:42", "/start", "Ольга")),
                max_bot.Parsed(1, max_bot.Incoming("max:42", SHOWCASE, "Ольга"))], NOW)
    assert [r[1] for r in db.unsent_client(max_chats=False)] == ["555"]
    assert {r[1] for r in db.unsent_client(max_chats=True)} == {"max:42"}
    assert chat_configured("max:42") and chat_configured("555")

    sent = []
    monkeypatch.setattr(max_bot, "upload_image", lambda token, data: sent.append(("upload", data)) or {"token": "U"})
    monkeypatch.setattr(max_bot, "send", lambda token, user, text, markup=None, image=None:
                        sent.append((user, text[:12], image)) or ("MXIMG" if image else ""))
    got = []
    mx.sent.disconnect()
    mx.sent.connect(got.append)
    items = mx_items(mx)
    assert all(row[1] == "max:42" for row in items)
    mx._send("mx", items)
    results = got[-1]
    assert ("upload", b"jpg") in sent  # фото витрины в MAX загрузили файлом
    assert any(len(x) == 3 and x[2] == {"token": "U"} for x in sent)
    mx._on_sent(results)
    assert db.unsent_client(max_chats=True) == [] and [r[1] for r in db.unsent_client()] == ["555"]
    assert db.get_showcase_item(item).file_id == "TG-FILE"  # номер Telegram не трогаем
    # второй показ витрины в MAX — по номеру фото MAX, без загрузки
    mx.process([max_bot.Parsed(2, max_bot.Incoming("max:42", SHOWCASE, "Ольга"))], NOW)
    showcase_rows = [row for row in mx_items(mx) if row[6] == f"showcase:{item}"]
    assert showcase_rows and showcase_rows[0][4] is None and showcase_rows[0][5] == "MXIMG"

    # заказ из MAX — в доставки с пометкой «MAX-бот»
    mx.process([max_bot.Parsed(3, max_bot.Incoming("max:42", callback=f"pick:{item}")),
                max_bot.Parsed(4, max_bot.Incoming("max:42", "завтра к 15:00")),
                max_bot.Parsed(5, max_bot.Incoming("max:42", "🏪 Заберу сам")),
                max_bot.Parsed(6, max_bot.Incoming("max:42", "Себе")),
                max_bot.Parsed(7, max_bot.Incoming("max:42", contact_phone="+79001112233")),
                max_bot.Parsed(8, max_bot.Incoming("max:42", "Без открытки")),
                max_bot.Parsed(9, max_bot.Incoming("max:42", SEND))], NOW)
    [order_id] = [e[1] for e in events if e[0] == "order"]
    db.price_client_order(order_id, 3500, 0, NOW)
    d = db.confirm_client_order(order_id, None, NOW)
    assert d.source == "MAX-бот" and d.client_chat == "max:42"
    tg.deleteLater()
    mx.deleteLater()


def mx_items(bot) -> list:
    """Что бот MAX отправил бы сейчас (с подставленными фото витрины)."""
    captured = []
    real = max_bot.threading.Thread

    class Inline:
        def __init__(self, target, args, daemon):
            captured.append(args[1])

        def start(self):
            pass

    max_bot.threading.Thread = Inline
    try:
        bot.sending = False
        bot.flush()
    finally:
        max_bot.threading.Thread = real
        bot.sending = False
    return captured[0] if captured else []


def test_window_routes_to_max(app, db, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app import main_window as mw
    from app.db import Delivery
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    w = mw.MainWindow(db)
    w.tray.showMessage = lambda *a: None
    w.telegram.queue = lambda *a, **k: None
    flushed = []
    w.max_bot.flush = lambda: flushed.append(1)
    w.client_bot.queued.disconnect()
    w.client_bot.queued.connect(w.max_bot.flush)
    w.set_user(db.add_user("anna", "Анна", "1"))
    d = Delivery(day=NOW.date(), time="12:00", items="Розы", address="Самовывоз", client_chat="max:42", source="MAX-бот")
    db.add(d)
    w._set_assembled(d, True)
    assert flushed and db.unsent_client(max_chats=True)[0][2] == "🌸 Ваш букет готов — можно забирать!"
    w.assembly.hide()
