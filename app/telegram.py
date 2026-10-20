"""Сообщения хозяину в Telegram: итоги смен и проблемы. Отправка — через очередь в базе."""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from .delivery_dialog import money
from .shifts import Shift

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 15
FLUSH_EVERY_MS = 60_000
MAX_TEXT = 4000  # предел Telegram — 4096 знаков
POLL_EVERY_MS = 30_000  # запасной таймер: если опрос оборвался, запустить заново
LONG_POLL = 25  # секунд ожидания новых сообщений за один запрос
STALE_COMMAND = timedelta(minutes=15)  # команду старше этого не выполняем (программа была выключена)


class TelegramError(Exception):
    pass


def _call(token: str, method: str, params: dict | None = None, timeout: float = TIMEOUT) -> dict:
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API.format(token=token.strip(), method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as e:
        try:
            payload = json.load(e)
        except ValueError:
            raise TelegramError(f"Telegram ответил ошибкой {e.code}") from None
    except OSError as e:
        raise TelegramError(f"Нет связи с Telegram: {e}") from None
    if not payload.get("ok"):
        raise TelegramError(payload.get("description") or "Telegram вернул ошибку")
    return payload


def send_message(token: str, chat_id: str, text: str):
    _call(token, "sendMessage", {"chat_id": chat_id, "text": text})


def find_chats(token: str) -> list[tuple[str, str]]:
    """Чаты, которые писали боту: [(chat_id, название)]. Сначала нужно написать боту что-нибудь."""
    chats: dict[str, str] = {}
    for update in _call(token, "getUpdates").get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if "id" in chat:
            title = chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "")
            chats[str(chat["id"])] = title
    return list(chats.items())


ANNOUNCE_PREFIX = "!"


def get_updates(token: str, offset: int | None, wait: int = LONG_POLL) -> list[dict]:
    """Новые сообщения. wait — сколько секунд Telegram держит запрос, если новых нет:
    ответ на команду приходит почти сразу, а не через полминуты."""
    params = {"timeout": wait}
    if offset is not None:
        params["offset"] = offset  # подтверждает получение всего, что раньше
    return _call(token, "getUpdates", params, timeout=wait + 15).get("result", [])


def register_commands(token: str):
    """Меню команд бота (кнопка «/» в Telegram)."""
    from .bot_commands import MENU
    _call(token, "setMyCommands", {"commands": json.dumps(
        [{"command": name, "description": text} for name, text in MENU], ensure_ascii=False)})


def parse_announcements(updates: list[dict], chat_id: str) -> list[tuple[int, str, datetime]]:
    """Объявления из сообщений хозяина: только из его чата и только начинающиеся с «!»."""
    found = []
    for update in updates:
        message = update.get("message") or {}
        if str((message.get("chat") or {}).get("id")) != str(chat_id):
            continue  # чужой человек нашёл бота — игнорируем
        text = (message.get("text") or message.get("caption") or "").strip()
        if not text.startswith(ANNOUNCE_PREFIX):
            continue  # обычный ответ боту («ок», «понял») — не объявление
        text = text[len(ANNOUNCE_PREFIX):].strip()
        if text:
            found.append((update["update_id"], text, datetime.fromtimestamp(message.get("date", 0))))
    return found


# ----- настройки -----

def settings() -> dict:
    s = QSettings("FlowerDeliveries", "FlowerDeliveries")
    return {
        "token": str(s.value("tg_token", "")),
        "chat_id": str(s.value("tg_chat_id", "")),
        "on_start": str(s.value("tg_on_start", "true")) == "true",
        "on_close": str(s.value("tg_on_close", "true")) == "true",
    }


def configured() -> bool:
    cfg = settings()
    return bool(cfg["token"] and cfg["chat_id"])


# ----- тексты -----

def diff_words(diff: int | None) -> str:
    if diff is None or diff == 0:
        return "сходится ✅"
    return f"❗ недостача {money(-diff)}" if diff < 0 else f"❗ излишек {money(diff)}"


def shift_started_text(shift: Shift, handover: int | None, prev_name: str) -> str:
    text = f"🟢 {shift.user_name or 'Сотрудник'} начал(а) смену в {shift.opened_at:%H:%M}\n" \
           f"Приняли в кассе: {money(shift.opening_cash)}"
    if handover:
        text += f"\n❗ Не сходится с тем, что оставил(а) {prev_name or 'прошлая смена'}: {diff_words(handover)}"
    return text


def shift_closed_text(shift: Shift, change: time, late: bool, water: str | None,
                      missed_duties: list[str]) -> str:
    lines = [f"🌸 Смена сдана — {shift.user_name or 'сотрудник'}",
             f"{shift.opened_at:%d.%m %H:%M} → {shift.closed_at:%d.%m %H:%M}"]
    if late:
        lines.append(f"⏰ Сдана с опозданием (пересменка в {change:%H:%M})")
    lines += [
        f"Приход: {money(shift.revenue)} · Цветы: {money(shift.flowers)}",
        f"Терминал: {money(shift.terminal)} · QR: {money(shift.qr)} · Наличные: {money(shift.cash_sales)}",
        f"Касса: должно {money(shift.expected_cash())}, сдали {money(shift.closing_cash or 0)} — "
        f"{diff_words(shift.difference())}",
    ]
    if shift.expenses or shift.withdrawals:
        lines.append(f"Расходы: {money(shift.expenses)} · Изъятия: {money(shift.withdrawals)}")
    if water == "done":
        lines.append("💧 Вода поменяна")
    elif water is not None:
        lines.append("❗ 💧 Вода не поменяна")
    if shift.duties_total:
        line = f"📋 Обязанности: {shift.duties_done} из {shift.duties_total}"
        if missed_duties:
            line += " — не сделано: " + ", ".join(missed_duties)
        lines.append(line)
    if shift.note:
        lines.append(f"💬 {shift.note}")
    return "\n".join(lines)


def not_closed_text(shift: Shift) -> str:
    return f"❗ Смена не сдана: {shift.user_name or 'сотрудник'}, смена {shift.day:%d.%m} " \
           f"(начата {shift.opened_at:%d.%m %H:%M})"


# ----- отправка в фоне -----

class TelegramInbox(QObject):
    """Слушает бота: объявления хозяина («!…») и команды («/касса»). Опрос не прерывается."""

    fetched = Signal(list, str)  # (обновления, текст ошибки) — из фонового потока
    arrived = Signal(list)  # id новых объявлений — для главного окна

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.busy = False
        self.handler = None  # (текст) -> ответ или None; задаёт главное окно
        self.reply = None  # (текст ответа) -> None; задаёт главное окно
        self._registered_token = ""
        self.fetched.connect(self._on_fetched)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(POLL_EVERY_MS)

    @staticmethod
    def _offset() -> int | None:
        value = QSettings("FlowerDeliveries", "FlowerDeliveries").value("tg_offset", "")
        return int(value) if str(value).lstrip("-").isdigit() else None

    def poll(self):
        cfg = settings()
        if self.busy or not (cfg["token"] and cfg["chat_id"]):
            return
        self.busy = True
        threading.Thread(target=self._fetch, args=(cfg["token"], self._offset()), daemon=True).start()

    def _fetch(self, token: str, offset: int | None):
        try:
            if self._registered_token != token:
                try:
                    register_commands(token)
                    self._registered_token = token
                except TelegramError:
                    pass  # меню — удобство, без него команды всё равно работают
            self.fetched.emit(get_updates(token, offset), "")
        except TelegramError as e:
            self.fetched.emit([], str(e))

    def _schedule_next(self, delay_ms: int):
        QTimer.singleShot(delay_ms, self.poll)

    def _on_fetched(self, updates: list, error: str):
        self.busy = False
        if error:
            self._schedule_next(15_000)  # нет связи — не долбить сервер
            return
        self._schedule_next(200)  # сразу ждём следующие сообщения
        first_time = self._offset() is None
        offset = max((u["update_id"] for u in updates), default=-1) + 1
        if updates or first_time:  # 0 — «подключились, старых сообщений не было»
            QSettings("FlowerDeliveries", "FlowerDeliveries").setValue("tg_offset", offset)
        if first_time:
            return  # старые сообщения, написанные до подключения (например «привет»), не показываем
        chat_id = settings()["chat_id"]
        new_ids = [i for update_id, text, date in parse_announcements(updates, chat_id)
                   if (i := self.db.add_announcement(text, date, update_id)) is not None]
        if new_ids:
            self.arrived.emit(new_ids)
        if self.handler is not None and self.reply is not None:
            self._answer_commands(updates, chat_id)

    def _answer_commands(self, updates: list, chat_id: str):
        stale = False
        for update in updates:
            message = update.get("message") or {}
            if str((message.get("chat") or {}).get("id")) != str(chat_id):
                continue  # чужой человек — игнорируем
            text = (message.get("text") or "").strip()
            if not text or text.startswith(ANNOUNCE_PREFIX):
                continue
            sent = datetime.fromtimestamp(message.get("date", 0))
            if datetime.now() - sent > STALE_COMMAND:
                stale = stale or text.startswith("/")
                continue  # пока программа не работала, ничего не выполняем
            answer = self.handler(text)
            if answer:
                self.reply(answer)
        if stale:
            self.reply("Программа была выключена или без интернета и пропустила часть команд. "
                       "Повторите, если они ещё нужны.")


class TelegramSender(QObject):
    """Раз в минуту отправляет накопившиеся сообщения. Нет интернета — попробует позже."""

    done = Signal(list)  # [(id сообщения, текст ошибки или "")]

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.busy = False
        self.last_error = ""
        self.done.connect(self._on_done)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.flush)
        self.timer.start(FLUSH_EVERY_MS)

    def queue(self, text: str, key: str | None = None):
        if len(text) > MAX_TEXT:  # иначе Telegram откажет и сообщение застопорит очередь
            text = text[:MAX_TEXT - 1] + "…"
        if configured() and self.db.queue_message(text, datetime.now(), key):
            QTimer.singleShot(0, self.flush)

    def flush(self):
        cfg = settings()
        if self.busy or not (cfg["token"] and cfg["chat_id"]):
            return
        items = self.db.unsent_messages()
        if not items:
            return
        self.busy = True
        threading.Thread(target=self._send, args=(cfg["token"], cfg["chat_id"], items), daemon=True).start()

    def _send(self, token: str, chat_id: str, items: list[tuple[int, str]]):
        results = []
        for message_id, text in items:
            try:
                send_message(token, chat_id, text)
                results.append((message_id, ""))
            except TelegramError as e:
                results.append((message_id, str(e)))
                break  # скорее всего нет интернета — остальные попробуем в следующий раз
        self.done.emit(results)

    def _on_done(self, results: list):
        for message_id, error in results:
            if error:
                self.last_error = error
            else:
                self.db.mark_message_sent(message_id, datetime.now())
                self.last_error = ""
        self.busy = False
        if not any(error for _, error in results) and self.db.unsent_messages(limit=1):
            QTimer.singleShot(300, self.flush)  # пока отправляли, пришли новые — не ждать минуту
