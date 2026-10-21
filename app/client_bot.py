"""Бот для клиентов: слушает Telegram и отправляет ответы. Разговор — в client_flow, здесь только сеть.

Это отдельный бот (свой токен): у администраторов — команды и отчёты, у клиентов — витрина и заказы.
Сеть — в фоновых потоках, база — только в главном (SQLite не любит потоки).
"""

import json
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from . import telegram
from .client_flow import ClientFlow, Incoming
from .photos import shrink

POLL_EVERY_MS = 30_000  # запасной таймер: если опрос оборвался, запустить заново
FLUSH_EVERY_MS = 30_000
RETRY_MS = 15_000  # нет связи — следующая попытка
PHOTO_MAX_SIDE = 1600
MAX_IMAGE_FILE = 10 * 1024 * 1024
DEFAULT_SHOP = "Страна Чудес"
UNSUPPORTED = "Пока понимаю только текст и фото 🙏 Напишите, пожалуйста, словами."


def _settings() -> QSettings:
    return QSettings("FlowerDeliveries", "FlowerDeliveries")


def client_settings() -> dict:
    s = _settings()
    return {
        "token": str(s.value("client_token", "")).strip(),
        "shop": str(s.value("client_shop", DEFAULT_SHOP)).strip() or DEFAULT_SHOP,
        "requisites": str(s.value("client_requisites", "")),
        "username": str(s.value("client_bot_username", "")),
    }


def client_configured() -> bool:
    return bool(client_settings()["token"])


def bot_link(username: str, start: str = "") -> str:
    return f"https://t.me/{username}" + (f"?start={start}" if start else "") if username else ""


@dataclass
class Parsed:
    """Обновление от Telegram, разобранное в фоне."""
    update_id: int
    message: Incoming | None  # None — нечего обрабатывать (стикер, группа, служебное)
    photo_id: str = ""  # фото, которое нужно скачать
    callback_id: str = ""  # нажата кнопка — Telegram ждёт ответа, иначе на ней крутятся «часики»
    unsupported: bool = False  # голосовое, видео, файл — вежливо попросить написать словами


def _name(user: dict) -> str:
    return " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or user.get("username", "")


def best_photo(sizes: list[dict]) -> str:
    """Самый крупный вариант фото, но не больше PHOTO_MAX_SIDE по стороне (оригинал бывает огромным)."""
    if not sizes:
        return ""
    side = lambda p: max(p.get("width", 0), p.get("height", 0))  # noqa: E731
    fitting = [p for p in sizes if side(p) <= PHOTO_MAX_SIDE]
    pick = max(fitting, key=side) if fitting else min(sizes, key=side)
    return pick.get("file_id", "")


def parse_update(update: dict) -> Parsed:
    update_id = update["update_id"]
    callback = update.get("callback_query")
    if callback:
        sender = callback.get("from") or {}
        chat = ((callback.get("message") or {}).get("chat") or {}).get("id", sender.get("id"))
        return Parsed(update_id, Incoming(str(chat), name=_name(sender), username=sender.get("username", ""),
                                          callback=callback.get("data", "")), callback_id=callback.get("id", ""))
    message = update.get("message")
    if not message or (message.get("chat") or {}).get("type", "private") != "private":
        return Parsed(update_id, None)  # группы и каналы — не клиенты
    sender = message.get("from") or {}
    msg = Incoming(str(message["chat"]["id"]), (message.get("text") or message.get("caption") or "").strip(),
                   _name(sender), sender.get("username", ""))
    parsed = Parsed(update_id, msg)
    document = message.get("document") or {}
    msg.album = str(message.get("media_group_id", ""))
    if message.get("photo"):
        parsed.photo_id = best_photo(message["photo"])
    elif document.get("mime_type", "").startswith("image/") and document.get("file_size", 0) <= MAX_IMAGE_FILE:
        parsed.photo_id = document.get("file_id", "")  # фото, отправленное файлом
    elif message.get("contact"):
        msg.contact_phone = message["contact"].get("phone_number", "")
    elif message.get("location"):
        lat, lon = message["location"].get("latitude"), message["location"].get("longitude")
        msg.text = f"📍 {lat:.6f}, {lon:.6f} — https://yandex.ru/maps/?pt={lon:.6f},{lat:.6f}&z=17"
    elif not msg.text:
        if "sticker" in message:
            return Parsed(update_id, None)  # «спасибо» стикером — отвечать не нужно
        parsed.unsupported = True
    return parsed


class ClientBot(QObject):
    fetched = Signal(list, str)  # (разобранные обновления, ошибка) — из фонового потока
    sent = Signal(list)  # итоги отправки — из фонового потока
    identified = Signal(str)  # логин бота — из фонового потока
    event = Signal(object)  # событие разговора для главного окна: ("order", id), ("message", id)…

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.polling = False
        self.sending = False
        self.last_error = ""
        self._known_token = ""
        self.link_tries: dict[str, int] = {}  # подбор кода сотрудника — пока программа работает
        self.fetched.connect(self._on_fetched)
        self.sent.connect(self._on_sent)
        self.identified.connect(lambda name: _settings().setValue("client_bot_username", name))
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(POLL_EVERY_MS)
        self.flush_timer = QTimer(self)
        self.flush_timer.timeout.connect(self.flush)
        self.flush_timer.start(FLUSH_EVERY_MS)
        QTimer.singleShot(1000, self.poll)

    # ----- приём -----

    @staticmethod
    def _offset() -> int | None:
        value = _settings().value("client_offset", "")
        return int(value) if str(value).lstrip("-").isdigit() else None

    def poll(self):
        token = client_settings()["token"]
        if self.polling or not token:
            return
        self.polling = True
        threading.Thread(target=self._fetch, args=(token, self._offset()), daemon=True).start()

    def _fetch(self, token: str, offset: int | None):
        try:
            if self._known_token != token:  # новый бот — узнать его логин (для ссылок)
                self.identified.emit(telegram.get_me(token).get("username", ""))
                self._known_token = token
            parsed = [parse_update(u) for u in telegram.get_updates(token, offset)]
            for p in parsed:
                if p.callback_id:
                    try:
                        telegram.answer_callback(token, p.callback_id)
                    except telegram.TelegramError:
                        pass  # «часики» на кнопке — не повод терять сообщение
                if p.photo_id and p.message is not None:
                    try:
                        p.message.photo = telegram.download_file(token, p.photo_id)
                    except telegram.TelegramError as e:
                        if not e.permanent:
                            raise  # нет связи — заберём все сообщения ещё раз, когда появится
                        p.message.text = p.message.text or "[фото не загрузилось — попросите прислать ещё раз]"
        except telegram.TelegramError as e:
            self.fetched.emit([], str(e))
            return
        self.fetched.emit(parsed, "")

    def _on_fetched(self, parsed: list, error: str):
        self.polling = False
        if error:
            self.last_error = error
            QTimer.singleShot(RETRY_MS, self.poll)
            return
        self.last_error = ""
        QTimer.singleShot(200, self.poll)  # сразу ждём следующие сообщения
        if not parsed:
            return
        self.process(parsed, datetime.now())
        _settings().setValue("client_offset", max(p.update_id for p in parsed) + 1)
        self.flush()

    def process(self, parsed: list[Parsed], now: datetime):
        flow = ClientFlow(self.db, client_settings()["shop"], self.link_tries)
        for p in parsed:
            if p.message is None:
                continue
            if p.message.photo:
                p.message.photo = shrink(p.message.photo)  # база не должна пухнуть от фото с телефона
            if p.unsupported:
                self.db.queue_client(p.message.chat_id, UNSUPPORTED, now)
                continue
            try:
                result = flow.handle(p.message, now)
            except Exception:  # noqa: BLE001 — одно странное сообщение не должно останавливать бота
                traceback.print_exc()
                continue
            for r in result.replies:
                self.db.queue_client(p.message.chat_id, r.text, now, r.markup, r.photo, r.file_id, r.ref)
            for ev in result.events:
                self.event.emit(ev)

    # ----- отправка -----

    def queue(self, chat_id: str, text: str, photo: bytes | None = None, markup: dict | None = None):
        """Сообщение клиенту (или флористу) — уйдёт сразу, а без интернета — когда появится."""
        self.db.queue_client(chat_id, text, datetime.now(), markup, photo)
        QTimer.singleShot(0, self.flush)

    def flush(self):
        token = client_settings()["token"]
        if self.sending or not token:
            return
        items = self.db.unsent_client()
        if not items:
            return
        self.sending = True
        threading.Thread(target=self._send, args=(token, items), daemon=True).start()

    def _send(self, token: str, items: list):
        results = []
        for outbox_id, chat, text, markup, photo, file_id, ref in items:
            result = {"id": outbox_id, "chat": chat, "text": text, "markup": markup, "ref": ref,
                      "by_file_id": bool(file_id), "error": "", "permanent": False, "file_id": "", "wait": 0}
            try:
                buttons = json.loads(markup) if markup else None
                if photo or file_id:
                    result["file_id"] = telegram.send_photo(token, chat, text, photo, file_id, buttons)
                else:
                    telegram.send_message(token, chat, text[:telegram.MAX_TEXT], buttons)
            except telegram.TelegramError as e:
                result.update(error=str(e), permanent=e.permanent, wait=e.retry_after)
            results.append(result)
            if result["error"] and not result["permanent"]:
                break  # нет связи или «слишком часто» — порядок сообщений не путаем, остальное позже
        self.sent.emit(results)

    def _on_sent(self, results: list):
        self.sending = False
        now = datetime.now()
        retry_ms = 0
        for r in results:
            if r["error"] and not r["permanent"]:
                self.last_error = r["error"]
                retry_ms = r["wait"] * 1000 + 500 if r["wait"] else RETRY_MS
                continue
            self.db.mark_client_sent(r["id"], now)  # отправлено — или клиент заблокировал бота: не повторять
            kind, _, value = r["ref"].partition(":")
            if kind != "showcase" or not value.isdigit():
                continue
            if r["file_id"]:
                self.db.set_showcase_file_id(int(value), r["file_id"])  # дальше — без загрузки
            elif r["error"] and r["by_file_id"]:  # номер фото устарел (например, сменили бота) — файлом
                self.db.set_showcase_file_id(int(value), "")
                item = self.db.get_showcase_item(int(value))
                if item is not None and item.photo:
                    self.db.queue_client(r["chat"], r["text"], now, json.loads(r["markup"]) if r["markup"] else None,
                                         item.photo, "", r["ref"])
        if results and not any(r["error"] for r in results):
            self.last_error = ""
        if retry_ms:
            QTimer.singleShot(retry_ms, self.flush)
        elif self.db.unsent_client(limit=1):
            QTimer.singleShot(300, self.flush)
