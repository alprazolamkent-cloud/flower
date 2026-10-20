"""Сообщения хозяину в Telegram: итоги смен и проблемы. Отправка — через очередь в базе."""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from .delivery_dialog import money
from .shifts import Shift

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 15
FLUSH_EVERY_MS = 60_000


class TelegramError(Exception):
    pass


def _call(token: str, method: str, params: dict | None = None) -> dict:
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API.format(token=token.strip(), method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
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
