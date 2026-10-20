"""«Компьютер на связи»: программа раз в 2 минуты обновляет закреплённое сообщение в чате с ботом.

Сам компьютер без интернета (или выключенный) ничего отправить не может, поэтому следит за ним
GitHub: каждые 10 минут смотрит, когда это сообщение обновлялось (tools/watchdog.py), и если давно —
бот пишет администраторам. Когда связь вернётся, программа сама сообщит, сколько её не было.
"""

import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from . import telegram

BEAT_EVERY_MS = 2 * 60_000
OFFLINE_AFTER = timedelta(minutes=10)
PAUSED_MARK = "⏸"  # по этому значку сторож понимает, что слежение выключили нарочно


def status_text(now: datetime) -> str:
    return (f"🟢 Компьютер в магазине на связи\nОбновлено: {now:%d.%m %H:%M}\n\n"
            "По этому сообщению проверяется, что программа работает и есть интернет. Не открепляйте его.")


def paused_text(now: datetime) -> str:
    return f"{PAUSED_MARK} Слежение за связью выключено в настройках ({now:%d.%m %H:%M})."


def back_online_text(last: datetime, now: datetime) -> str | None:
    """Связь пропадала на 10 минут и больше — сообщение «снова на связи», иначе None."""
    gap = now - last
    if gap < OFFLINE_AFTER:
        return None
    minutes = int(gap.total_seconds() // 60)
    span = f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"
    return (f"✅ Компьютер в магазине снова на связи.\nСвязи не было {span} "
            f"(с {last:%d.%m %H:%M} до {now:%H:%M}).")


def _settings() -> QSettings:
    return QSettings("FlowerDeliveries", "FlowerDeliveries")


def watch_enabled() -> bool:
    return str(_settings().value("tg_watch", "true")) == "true"


class Heartbeat(QObject):
    """Обновляет закреплённое сообщение в фоне; итог — сигнал beaten (чат, номер сообщения, ошибка)."""

    beaten = Signal(str, int, str)
    back_online = Signal(str)  # текст «снова на связи» — главное окно отправит его всем администраторам

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.beaten.connect(self._done)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.beat)
        self.timer.start(BEAT_EVERY_MS)
        QTimer.singleShot(5_000, self.beat)

    def beat(self):
        cfg = telegram.settings()
        if self.busy or not (cfg["token"] and cfg["chats"]):
            return
        s = _settings()
        chat = cfg["chats"][0]  # закреплённое сообщение — в первом чате из настроек
        message_id = int(s.value("tg_status_msg", 0) or 0) if str(s.value("tg_status_chat", "")) == chat else 0
        enabled = watch_enabled()
        if not enabled and (not message_id or str(s.value("tg_status_paused", "")) == "true"):
            return  # выключено и сторожу уже сказано
        self.busy = True
        text = status_text(datetime.now()) if enabled else paused_text(datetime.now())
        threading.Thread(target=self._send, args=(cfg["token"], chat, message_id, text), daemon=True).start()

    def _send(self, token: str, chat: str, message_id: int, text: str):
        try:
            if message_id:
                try:
                    telegram.edit_message(token, chat, message_id, text)
                except telegram.TelegramError as e:
                    if not e.permanent:
                        raise
                    message_id = 0  # сообщение удалили — пришлём новое
            if not message_id:
                message_id = telegram.send_message(token, chat, text)
                telegram.pin_message(token, chat, message_id)
            self.beaten.emit(chat, message_id, "")
        except telegram.TelegramError as e:
            self.beaten.emit(chat, message_id, str(e))

    def _done(self, chat: str, message_id: int, error: str):
        self.busy = False
        if error or not message_id:
            return
        s, now = _settings(), datetime.now()
        s.setValue("tg_status_chat", chat)
        s.setValue("tg_status_msg", message_id)
        s.setValue("tg_status_paused", "false" if watch_enabled() else "true")
        try:
            last = datetime.fromisoformat(str(s.value("tg_last_beat", "")))
        except ValueError:
            last = None
        s.setValue("tg_last_beat", now.isoformat(timespec="seconds"))
        if last is not None and watch_enabled():
            text = back_online_text(last, now)
            if text:
                self.back_online.emit(text)
