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
