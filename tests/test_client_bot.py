"""Бот клиентов: разбор сообщений Telegram, очередь отправки, окно «Клиенты», фото букетов."""

import json
import sys
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from app import client_bot, telegram
from app import main_window as mw
from app.client_bot import ClientBot, Parsed, best_photo, parse_update
from app.client_center import needs_action
from app.client_flow import Incoming
from app.db import Database, Delivery, ShowcaseItem
from app.delivery_dialog import DeliveryDialog
from app.photos import load_photo, to_jpeg

NOW = datetime(2026, 10, 20, 12, 0)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def store(monkeypatch):
    values = {"client_token": "tok", "client_requisites": "Сбер +7 900 000-00-00"}
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: values.get(key, default))
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: values.__setitem__(key, value))
    monkeypatch.setattr(QSettings, "remove", lambda self, key: values.pop(key, None))
    monkeypatch.setattr(ClientBot, "poll", lambda self: None)  # без сети
    monkeypatch.setattr(ClientBot, "flush", lambda self: None)
    return values


def jpeg(color="red", w=40, h=30) -> bytes:
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor(color))
    return to_jpeg(image)


def outbox(db):
    return [(chat, text, json.loads(markup) if markup else None, photo is not None or bool(file_id))
            for _, chat, text, markup, photo, file_id, _ in db.unsent_client(limit=100)]


# ----- разбор сообщений -----

def test_parse_update_kinds():
    base = {"chat": {"id": 5, "type": "private"}, "from": {"first_name": "Иван", "last_name": "П", "username": "ivan"}}
    text = parse_update({"update_id": 1, "message": {**base, "text": " Привет "}})
    assert (text.message.chat_id, text.message.text, text.message.name, text.message.username) == \
        ("5", "Привет", "Иван П", "ivan")
    photo = parse_update({"update_id": 2, "message": {**base, "caption": "вот так", "media_group_id": "77", "photo": [
        {"file_id": "s", "width": 90, "height": 60}, {"file_id": "m", "width": 1280, "height": 960},
        {"file_id": "xl", "width": 2560, "height": 1920}]}})
    assert photo.photo_id == "m" and photo.message.text == "вот так" and photo.message.album == "77"
    doc = parse_update({"update_id": 3, "message": {**base, "document": {"file_id": "d", "mime_type": "image/png",
                                                                          "file_size": 1000}}})
    assert doc.photo_id == "d"
    contact = parse_update({"update_id": 4, "message": {**base, "contact": {"phone_number": "+79001112233"}}})
    assert contact.message.contact_phone == "+79001112233"
    place = parse_update({"update_id": 5, "message": {**base, "location": {"latitude": 55.75, "longitude": 37.61}}})
    assert "55.750000, 37.610000" in place.message.text and "yandex.ru/maps" in place.message.text
    assert parse_update({"update_id": 6, "message": {**base, "sticker": {}}}).message is None
    assert parse_update({"update_id": 7, "message": {**base, "voice": {}}}).unsupported
    group = {"chat": {"id": -100, "type": "group"}, "from": {"id": 1}, "text": "hi"}
    assert parse_update({"update_id": 8, "message": group}).message is None
    press = parse_update({"update_id": 9, "callback_query": {"id": "cb", "data": "pick:3", "from": {"id": 5},
                                                             "message": {"chat": {"id": 5}}}})
    assert press.callback_id == "cb" and press.message.callback == "pick:3" and press.message.chat_id == "5"
    assert best_photo([{"file_id": "huge", "width": 4000, "height": 3000}]) == "huge"  # других нет


def test_send_photo_and_answer(monkeypatch):
    calls = []

    def fake(token, method, params=None, timeout=0, body=None, content_type=""):
        calls.append((method, params, body))
        return {"ok": True, "result": {"photo": [{"file_id": "a", "width": 90}, {"file_id": "b", "width": 800}]}}

    monkeypatch.setattr(telegram, "_call", fake)
    assert telegram.send_photo("t", "5", "x" * 2000, file_id="OLD", markup={"inline_keyboard": []}) == "b"
    method, params, _ = calls[-1]
    assert params["photo"] == "OLD" and len(params["caption"]) == telegram.MAX_CAPTION and "reply_markup" in params
    telegram.send_photo("t", "5", "Нежность", photo=b"JPEGDATA")
    _, _, body = calls[-1]
    assert b"JPEGDATA" in body and b"image/jpeg" in body and "Нежность".encode() in body
    telegram.send_message("t", "5", "hi", {"keyboard": [[{"text": "a"}]]})
    assert json.loads(calls[-1][1]["reply_markup"])["keyboard"][0][0]["text"] == "a"


# ----- бот: обработка и отправка -----

def test_bot_processes_and_queues(db, store, app):
    bot = ClientBot(db)
    events = []
    bot.event.connect(events.append)
    item = db.save_showcase_item(ShowcaseItem(0, "Нежность", price=3500, photo=b"jpg"))
    bot.process([Parsed(1, Incoming("5", "/start", "Иван")), Parsed(2, Incoming("5", "💐 Витрина")),
                 Parsed(3, None),
                 Parsed(4, Incoming("5", ""), unsupported=True), Parsed(5, Incoming("5", "Есть тюльпаны?"))], NOW)
    sent = outbox(db)
    assert "Страна Чудес" in sent[0][1] and sent[0][2]["keyboard"]  # приветствие с меню
    assert any(has_photo and "Нежность" in text for _, text, _, has_photo in sent)
    assert any(text == client_bot.UNSUPPORTED for _, text, _, _ in sent)
    assert [e[0] for e in events] == ["message"]

    # отправилось: фото витрины запомнили по номеру, дальше — без загрузки
    rows = db.unsent_client(limit=100)
    showcase_row = next(r for r in rows if r[6] == f"showcase:{item}")
    results = [{"id": r[0], "chat": r[1], "text": r[2], "markup": r[3], "ref": r[6], "by_file_id": False,
                "error": "", "permanent": False, "file_id": "FID" if r is showcase_row else "", "wait": 0} for r in rows]
    bot._on_sent(results)
    assert db.unsent_client() == [] and db.get_showcase_item(item).file_id == "FID"
    bot.deleteLater()


def test_bot_send_errors(db, store, app, monkeypatch):
    bot = ClientBot(db)
    item = db.save_showcase_item(ShowcaseItem(0, "Нежность", price=3500, photo=b"jpg"))
    db.set_showcase_file_id(item, "STALE")
    db.queue_client("5", "Нежность — 3 500 ₽", NOW, {"inline_keyboard": [[{"text": "🛒", "callback_data": "pick:1"}]]},
                    None, "STALE", f"showcase:{item}")
    db.queue_client("6", "второй", NOW)
    db.queue_client("6", "третий", NOW)
    sent = []

    def fake_photo(token, chat, caption, photo=None, file_id="", markup=None):
        raise telegram.TelegramError("Bad Request: wrong file identifier", permanent=True)

    def fake_message(token, chat, text, markup=None):
        if text == "третий":
            raise telegram.TelegramError("Too Many Requests", retry_after=3)
        sent.append(text)

    monkeypatch.setattr(telegram, "send_photo", fake_photo)
    monkeypatch.setattr(telegram, "send_message", fake_message)
    got = []
    bot.sent.disconnect()
    bot.sent.connect(got.append)
    bot._send("tok", db.unsent_client())
    [results] = got
    assert [r["error"] != "" for r in results] == [True, False, True] and sent == ["второй"]
    bot._on_sent(results)
    assert bot.last_error == "Too Many Requests"
    left = db.unsent_client()
    # «третий» ждёт; фото витрины — заново файлом, номер сброшен
    assert [(r[1], r[2], r[4] is not None, r[5]) for r in left] == [("6", "третий", False, ""),
                                                                      ("5", "Нежность — 3 500 ₽", True, "")]
    assert db.get_showcase_item(item).file_id == ""
    bot.deleteLater()


# ----- в программе -----

@pytest.fixture
def window(app, db, store, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    store.update({"tg_token": "admin", "tg_chat_id": "111"})
    w = mw.MainWindow(db)
    w.tray.showMessage = lambda *a: None
    w.queued, w.admin_photos, keys = [], [], set()

    def queue(text, key=None, chat=None, except_chat=None):  # как настоящая очередь: с тем же key — один раз
        if key is None or key not in keys:
            keys.add(key)
            w.queued.append(text)

    w.telegram.queue = queue
    w.telegram.send_photos = lambda items: w.admin_photos.__setitem__(slice(None), items)  # что ещё не ушло
    w.set_user(db.add_user("anna", "Анна", "1"))
    yield w
    w.assembly.hide()
    if w.client_center is not None:
        w.client_center.hide()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


def client_order(w, chat="555", showcase_id=None, address="ул. Ленина, 5"):
    w.db.touch_client(chat, "Иван", "ivan", NOW)
    o = w.db.add_client_order(chat, datetime.now(), date.today() + timedelta(days=1), "к 15:00", "Нежность — 3 500 ₽",
                              address, "Маша", "+79001112233", "С днём рождения!", showcase_id)
    w.client_bot.event.emit(("order", o.id))
    return o


def test_order_to_delivery_in_center(window):
    w, db = window, window.db
    item = db.save_showcase_item(ShowcaseItem(0, "Нежность", price=3500))
    o = client_order(w, showcase_id=item)
    assert any(f"Новый заказ из бота №{o.id}" in t and "Ленина" in t for t in w.queued)
    assert w.clients_btn.text() == "🛍 Клиенты (1)" and needs_action(db) == 1
    w.open_clients()
    c = w.client_center
    assert c.order_list.count() == 1 and c.amount_box.value() == 3500  # цена с витрины подставилась
    assert c.confirm_btn.isHidden()  # пока сумму не назначили, принимать нечего
    c.amount_box.setValue(4200)
    c.fee_box.setValue(700)
    db.add_client_message("777", True, "Здравствуйте", NOW)
    w.client_bot.event.emit(("message", 1))  # пока вписывали сумму, написал другой клиент
    assert (c.amount_box.value(), c.fee_box.value()) == (4200, 700) and db.client_unread() == 1
    c.price_btn.click()
    [(chat, text, markup, _)] = outbox(db)
    assert chat == "555" and "4 200 ₽" in text and "Сбер" in text
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"paid:{o.id}"
    assert db.get_client_order(o.id).status == "priced" and needs_action(db) == 1  # осталось сообщение
    db.mark_client_read("777")

    # клиент нажал «Я оплатил(а)» — флорист принимает
    db.set_client_order_status(o.id, "paid_claimed", datetime.now())
    w.client_bot.event.emit(("paid", o.id))
    assert any("сообщил об оплате" in t for t in w.queued) and w.clients_btn.text() == "🛍 Клиенты (1)"
    c.confirm_btn.click()
    order = db.get_client_order(o.id)
    d = db.get_delivery(order.delivery_id)
    assert order.status == "confirmed" and d.price == 700 and d.client_chat == "555" and d.source == "Telegram-бот"
    assert "Оплата получена" in outbox(db)[-1][1]
    assert any("из бота принят" in t for t in w.queued)
    history = [m.text for m in db.client_messages("555")]  # всё, что ушло клиенту, видно в переписке
    assert history[0].startswith(f"Заказ №{o.id}") and history[1].startswith("Оплата получена")

    # окно доставки не теряет «Telegram-бот» при сохранении
    dlg = DeliveryDialog(w, d, db, lambda data, parent: None)
    dlg.accept()
    assert d.source == "Telegram-бот"
    dlg.deleteLater()


def test_reject_and_chat(window, monkeypatch):
    w, db = window, window.db
    o = client_order(w)
    w.open_clients()
    c = w.client_center
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("нет пионов", True)))
    c.reject_btn.click()
    assert db.get_client_order(o.id).status == "rejected" and "нет пионов" in outbox(db)[-1][1]

    db.add_client_message("555", True, "А есть тюльпаны?", NOW, b"photo")
    w.client_bot.event.emit(("message", 1))
    assert w.clients_btn.text() == "🛍 Клиенты (1)"
    c.open_chat("555")
    assert db.client_unread() == 0 and w.clients_btn.text() == "🛍 Клиенты"
    assert "тюльпаны" in c.history.toPlainText() and "открыть фото" in c.history.toPlainText()
    c.reply_edit.setPlainText("Есть, белые и красные")
    c._send_reply()
    assert outbox(db)[-1][:2] == ("555", "Есть, белые и красные")
    assert db.client_messages("555")[-1].author == "Анна"


def test_showcase_editor(window):
    w, db = window, window.db
    w.open_clients()
    c = w.client_center
    c._new_item()
    c.title_edit.setText("Нежность")
    c.desc_edit.setPlainText("пионы")
    c.item_price.setValue(3500)
    c._set_item_photo(jpeg())
    c._save_item()
    [item] = db.showcase()
    assert (item.title, item.price, item.active, item.photo is not None) == ("Нежность", 3500, True, True)
    assert c.show_list.count() == 1 and c.show_list.currentItem().data(Qt.UserRole) == item.id
    c.active_check.setChecked(False)
    c._save_item()
    assert not db.get_showcase_item(item.id).active and "скрыт" in c.show_list.item(0).text()
    c._delete_item()
    assert db.showcase() == []


def test_assembled_photo_delivered(window):
    w, db = window, window.db
    staff_chat = "900"
    code = db.link_code(w.user.id)
    db.link_staff(code, staff_chat)
    d = Delivery(day=date.today(), time="23:59", items="Нежность", address="Ленина, 5", client_chat="555",
                 source="Telegram-бот")
    db.add(d)
    plain = Delivery(day=date.today(), time="23:58", items="Розы", address="Мира, 1")
    db.add(plain)

    w._set_assembled(d, True)
    texts = [(chat, text) for chat, text, _, _ in outbox(db)]
    assert ("555", "💐 Ваш букет собран и скоро поедет к получателю!") in texts
    assert any(chat == staff_chat and "Пришлите фото" in text and "увидит клиент" in text for chat, text in texts)
    w._set_assembled(d, False)
    w._set_assembled(d, True)  # сняли и снова отметили — клиенту второй раз не пишем
    assert sum(text.startswith("💐 Ваш букет собран") for _, text, _, _ in outbox(db)) == 1

    # флорист прислал два фото альбомом — оба к этому букету, клиенту первое с подписью
    w._photo_wanted[w.user.id] = [d.id]
    photo = jpeg()
    w.client_bot.event.emit(("staff_photo", w.user.id, photo, "album1"))
    w.client_bot.event.emit(("staff_photo", w.user.id, photo, "album1"))
    assert len(db.delivery_photos(d.id)) == 2 and db.get_delivery(d.id).photos == 2
    to_client = [(text, has_photo) for chat, text, _, has_photo in outbox(db) if chat == "555" and has_photo]
    assert to_client == [("Вот ваш букет 😍", True), ("", True)]
    assert [caption.splitlines()[0] for _, _, caption in w.admin_photos] == ["📷 Букет — Анна"] * 2
    w._admin_photos_sent([(pid, True) for pid, _, _ in w.admin_photos])
    assert db.photos_for_admins() == []

    # обычная доставка без запроса: фото не к чему — бот переспросит
    w._last_photo.clear()
    w._photo_wanted.clear()
    db.set_assembled(plain.id, True, None, datetime.now())  # отметил кто-то другой
    w.client_bot.event.emit(("staff_photo", w.user.id, photo, ""))
    assert "Не понял" in outbox(db)[-1][1]

    # доставлено — клиенту шаблон сам, один раз
    w.refresh_day()
    row = next(i for i, x in enumerate(w.deliveries) if x.id == d.id)
    w.table.item(row, mw.COL_DONE).setCheckState(Qt.Checked)
    delivered = [text for chat, text, _, _ in outbox(db) if chat == "555" and "доставлен" in text]
    assert len(delivered) == 1 and "Ленина, 5" in delivered[0]
    assert db.get_delivery(d.id).sent("delivered")
    assert w.table.item(row, mw.COL_ASSEMBLED).text() == "📷"

    # «Написать заказчику» для заказа из бота — прямо в бот
    w.table.selectRow(row)
    w.write_to_customer("review")
    assert "отзыв" in outbox(db)[-1][1] and outbox(db)[-1][0] == "555"


def test_photo_from_file_and_waiting_orders(window, tmp_path):
    w, db = window, window.db
    path = tmp_path / "big.png"
    image = QImage(3000, 1500, QImage.Format_ARGB32)
    image.fill(QColor(0, 128, 0, 128))
    image.save(str(path))
    data = load_photo(str(path))
    small = QImage.fromData(data)
    assert (small.width(), small.height()) == (1280, 640)

    d = Delivery(day=date.today(), time="12:00", items="Розы", client_chat="555")
    db.add(d)
    w._photo_added(d, data, w)
    assert db.get_delivery(d.id).photos == 1 and outbox(db)[-1][:2] == ("555", "Вот ваш букет 😍")

    o = client_order(w)
    w.queued.clear()
    later = datetime.now() + timedelta(minutes=25)
    w._check_clients(later)
    assert any(f"Заказ из бота №{o.id} ждёт флориста" in t for t in w.queued)
    w._check_clients(later + timedelta(minutes=1))
    assert sum("ждёт флориста" in t for t in w.queued) == 1  # администраторам — один раз


def test_old_photos_pruned(db):
    d = Delivery(day=NOW.date(), items="Розы")
    db.add(d)
    db.add_delivery_photo(d.id, b"old", "Анна", NOW - timedelta(days=40))
    db.add_delivery_photo(d.id, b"new", "Анна", NOW)
    db.add_client_message("5", True, "старое", NOW - timedelta(days=40), b"old")
    db.save_showcase_item(ShowcaseItem(0, "А", price=1, photo=b"keep"))
    assert db.prune_photos(NOW - timedelta(days=30)) == 2
    assert [p[2] for p in db.delivery_photos(d.id)] == [b"new"] and db.client_messages("5")[0].text == "старое"
    assert db.showcase()[0].photo == b"keep" and db.prune_photos(NOW - timedelta(days=30)) == 0


def test_settings_clients_tab(window, monkeypatch):
    from app.settings_dialog import SettingsDialog
    w = window
    dlg = SettingsDialog(w)
    dlg.client_token_edit.setText("admin")  # тот же, что у администраторов — нельзя
    dlg.accept()
    assert dlg.result() == 0 and dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Клиенты"
    dlg.client_token_edit.setText("new-bot")
    dlg.shop_edit.setText("Страна Чудес")
    dlg.requisites_edit.setPlainText("Тинькофф +7 900")
    w.db.save_showcase_item(ShowcaseItem(0, "А", price=1, photo=b"1", file_id="OLD"))
    dlg.accept()
    cfg = client_bot.client_settings()
    assert (cfg["token"], cfg["requisites"]) == ("new-bot", "Тинькофф +7 900")
    assert w.db.showcase()[0].file_id == ""  # другой бот — фото загрузим заново
    dlg.deleteLater()


def test_payment_by_qr_code(window):
    w, db = window, window.db
    o = client_order(w)
    w.open_clients()
    c = w.client_center
    db.set_file("payment_qr", jpeg("black", 200, 200))
    c.amount_box.setValue(4200)
    c.fee_box.setValue(700)
    c.price_btn.click()
    [(chat, text, markup, has_photo)] = outbox(db)
    assert chat == "555" and has_photo and markup["inline_keyboard"][0][0]["callback_data"] == f"paid:{o.id}"
    assert "QR-кодом" in text and "введите сумму 4 200 ₽" in text and "Сбер" in text
    assert "Ленина" not in text  # с картинкой подпись короткая: заказ клиент и так знает
    assert "любым способом" in text and c.pay_box.isVisibleTo(c) and c.pay_box.count() == 3  # оба способа — на выбор
    c.pay_box.setCurrentIndex(c.pay_box.findData("qr"))
    c.price_btn.click()
    only_qr = outbox(db)[-1]
    assert only_qr[3] and "Отсканируйте" in only_qr[1] and "Сбер" not in only_qr[1] and "любым" not in only_qr[1]
    c.pay_box.setCurrentIndex(c.pay_box.findData("transfer"))
    c.price_btn.click()
    only_transfer = outbox(db)[-1]
    assert not only_transfer[3] and "Сбер" in only_transfer[1] and "QR" not in only_transfer[1]
    c.pay_box.setCurrentIndex(c.pay_box.findData("both"))
    assert db.get_client_order(o.id).status == "priced"
    # без QR — по-старому, текстом с реквизитами
    db.set_file("payment_qr", None)
    c.price_btn.click()
    assert "Отсканируйте" not in outbox(db)[-1][1] and not outbox(db)[-1][3] and "Сбер" in outbox(db)[-1][1]
    assert c.pay_box.isHidden()  # один способ — выбирать нечего


def test_settings_qr_saved(window):
    from app.settings_dialog import SettingsDialog
    w = window
    dlg = SettingsDialog(w)
    assert dlg._qr is None
    dlg._set_qr(jpeg("black", 300, 300))
    dlg.accept()
    assert w.db.get_file("payment_qr")
    dlg2 = SettingsDialog(w)
    assert dlg2._qr is not None and not dlg2.qr_label.pixmap().isNull()
    dlg2._set_qr(None)
    dlg2.accept()
    assert w.db.get_file("payment_qr") is None
    dlg.deleteLater()
    dlg2.deleteLater()
