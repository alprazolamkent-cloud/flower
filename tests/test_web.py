"""Сайт для телефона: вход, доставки дня, отметки, склад; сервер — очередь в главный поток."""

import threading
import time
import urllib.request
from datetime import date, datetime

import pytest

from app.db import Database, Delivery
from app.stock import Product
from app.web import COOKIE, WebApp
from app.web_server import WebServer


@pytest.fixture
def db():
    database = Database(":memory:")
    database.add_user("anna", "Анна", "1234")
    database.add(Delivery(day=date.today(), time="к 15:00", items="Нежность — 3 500 ₽",
                          recipient="Маша", address="ул. Ленина, 5", price=500))
    database.save_product(Product(0, "Роза Кения", "Цветы", price=250))
    yield database
    database.close()


def login(app: WebApp) -> dict:
    status, headers, _ = app.handle("POST", "/login", {"login": "anna", "password": "1234"}, {})
    assert status == 303 and headers["Location"] == "/"
    return {COOKIE: headers["Set-Cookie"].split(";")[0].split("=", 1)[1]}


def test_login_required_and_bad_password(db):
    app = WebApp(db)
    status, headers, _ = app.handle("GET", "/", {}, {})
    assert status == 303 and headers["Location"] == "/login"
    status, _, body = app.handle("POST", "/login", {"login": "anna", "password": "нет"}, {})
    assert status == 200 and "Неверный логин или пароль" in body.decode()
    status, _, body = app.handle("GET", "/login", {}, {})
    assert "AloFlorist" in body.decode()


def test_deliveries_page_and_toggles(db):
    called = []
    app = WebApp(db, on_assembled=lambda did: called.append(("a", did)),
                 on_delivered=lambda did: called.append(("d", did)),
                 on_change=lambda: called.append(("change",)))
    cookies = login(app)
    status, _, body = app.handle("GET", "/", {}, cookies)
    text = body.decode()
    assert status == 200 and "Нежность" in text and "ул. Ленина" in text and "Анна" in text
    assert "viewport" in text and "<script" not in text  # лёгкая страница для слабого телефона

    d = db.for_day(date.today())[0]
    status, headers, _ = app.handle("POST", "/toggle",
                                    {"id": str(d.id), "what": "assembled", "day": "today"}, cookies)
    assert status == 303 and db.get_delivery(d.id).assembled
    app.handle("POST", "/toggle", {"id": str(d.id), "what": "assembled", "day": "today"}, cookies)
    assert not db.get_delivery(d.id).assembled  # повторное нажатие — снять отметку
    app.handle("POST", "/toggle", {"id": str(d.id), "what": "delivered", "day": "today"}, cookies)
    d = db.get_delivery(d.id)
    assert d.delivered and d.assembled  # доставленная считается собранной
    app.handle("POST", "/toggle", {"id": str(d.id), "what": "assembled", "day": "today"}, cookies)
    assert db.get_delivery(d.id).assembled  # у доставленной «собрано» не снимается
    assert ("a", d.id) in called and ("d", d.id) in called and ("change",) in called

    _, _, body = app.handle("GET", "/", {}, cookies)
    assert "done" in body.decode()  # доставленная — приглушённая карточка


def test_stock_page_and_search(db):
    app = WebApp(db)
    cookies = login(app)
    _, _, body = app.handle("GET", "/stock", {}, cookies)
    assert "Роза Кения" in body.decode()
    _, _, body = app.handle("GET", "/stock", {"q": "тюльпан"}, cookies)
    assert "Роза Кения" not in body.decode() and "Ничего не найдено" in body.decode()
    _, _, body = app.handle("GET", "/logout", {}, cookies)
    status, headers, _ = app.handle("GET", "/", {}, cookies)
    assert status == 303  # после выхода — снова на вход


def test_server_roundtrip(db):
    server = WebServer(WebApp(db), port=0)  # 0 — свободный порт выберет система
    server.start()
    result = {}

    def fetch():
        url = f"http://127.0.0.1:{server.actual_port}/login"
        result["body"] = urllib.request.urlopen(url, timeout=10).read().decode()

    thread = threading.Thread(target=fetch)
    thread.start()
    deadline = time.time() + 10
    while thread.is_alive() and time.time() < deadline:  # база — только в этом (главном) потоке
        server.process_pending()
        time.sleep(0.02)
    thread.join()
    server.stop()
    assert "AloFlorist" in result["body"]


NOW = datetime(2026, 11, 26, 12, 0)


def test_bot_orders_on_site(db):
    from datetime import timedelta
    sent, admins = [], []
    app = WebApp(db, send_to_client=lambda chat, text, photo=None, markup=None: sent.append((chat, text)),
                 notify_admin=lambda text, key: admins.append(text), sale_shift_id=lambda: None)
    cookies = login(app)
    db.touch_client("555", "Иван", "ivan", NOW)
    o = db.add_client_order("555", NOW, date.today() + timedelta(days=1), "к 15:00", "Нежность — 3 500 ₽",
                            "ул. Ленина, 5", "Маша", "+79001112233", "", None)
    _, _, body = app.handle("GET", "/orders", {}, cookies)
    assert "Нежность" in body.decode() and "назначьте сумму" in body.decode()
    _, _, body = app.handle("GET", "/order", {"id": str(o.id)}, cookies)
    assert "Сумма к оплате" in body.decode()

    status, _, _ = app.handle("POST", "/order/price",
                              {"id": str(o.id), "amount": "4200", "fee": "700"}, cookies)
    assert status == 303
    order = db.get_client_order(o.id)
    assert order.status == "priced" and order.amount == 4200
    assert sent and "4 200" in sent[-1][1]  # клиенту ушли реквизиты

    _, _, body = app.handle("GET", "/order/confirm", {"id": str(o.id)}, cookies)
    assert "точно пришли" in body.decode()  # экран подтверждения от случайного касания
    status, _, _ = app.handle("POST", "/order/confirm", {"id": str(o.id)}, cookies)
    assert status == 303
    order = db.get_client_order(o.id)
    assert order.status == "confirmed" and order.delivery_id
    assert any("Оплата получена" in text for _, text in sent)
    assert admins and f"№{o.id}" in admins[-1]
    assert db.loyalty().balance("79001112233") == 175  # 5% с 3500 (без доставки)


def test_reject_returns_bonus_on_site(db):
    sent = []
    app = WebApp(db, send_to_client=lambda chat, text, photo=None, markup=None: sent.append(text))
    cookies = login(app)
    db.loyalty().adjust("79001112233", 500, note="тест")
    db.touch_client("555", "Иван", "ivan", NOW)
    o = db.add_client_order("555", NOW, date.today(), "к 15:00", "Букет", "ул. Ленина, 5", "Маша",
                            "+79001112233", "", None)
    db.price_client_order(o.id, 3000, 0, NOW)
    db.loyalty().spend("79001112233", 300, now=NOW, source="Заказ", author="Клиент")
    db.set_order_bonus(o.id, 300, 0, 0)
    status, _, _ = app.handle("POST", "/order/reject", {"id": str(o.id), "reason": "нет цветов"}, cookies)
    assert status == 303
    assert db.get_client_order(o.id).status == "rejected"
    assert db.loyalty().balance("79001112233") == 500  # списанные баллы вернулись
    assert "нет цветов" in sent[-1]
