"""Сторож для GitHub Actions: компьютер в магазине на связи?

Программа раз в 2 минуты обновляет закреплённое сообщение в чате с ботом. Этот скрипт (каждые 10 минут
по расписанию) смотрит, когда оно обновлялось, и если больше 10 минут назад — пишет администраторам.
Нужны секреты репозитория: TG_TOKEN (токен бота) и TG_CHATS (чаты через запятую, как в настройках
программы). Необязательный TG_TZ — часовой пояс для времени в сообщении (по умолчанию Europe/Moscow).
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MOSCOW = timezone(timedelta(hours=3), "МСК")

OFFLINE_AFTER_MIN = 10
PAUSED_MARK = "⏸"


def call(token: str, method: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data, timeout=20) as r:
        return json.load(r)


def zone(name: str) -> tzinfo:
    """Часовой пояс по имени; нет базы поясов (бывает на Windows) или опечатка — Москва."""
    try:
        return ZoneInfo(name) if name else MOSCOW
    except (ZoneInfoNotFoundError, ValueError):
        return MOSCOW


def alert_text(pinned: dict | None, now: datetime, tz: tzinfo) -> str | None:
    """Текст тревоги или None (всё в порядке, слежение выключено или ещё не настроено)."""
    if not pinned or PAUSED_MARK in (pinned.get("text") or ""):
        return None
    updated = datetime.fromtimestamp(pinned.get("edit_date") or pinned.get("date") or 0, timezone.utc)
    minutes = int((now - updated).total_seconds() // 60)
    if minutes < OFFLINE_AFTER_MIN:
        return None
    span = f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"
    return (f"⚠️ Компьютер в магазине не на связи уже {span}\n"
            f"Последний раз — {updated.astimezone(tz):%d.%m %H:%M}.\n"
            "Нет интернета, выключен компьютер или закрыта программа. Напомню через 10 минут.")


HEARTBEAT_MARKS = ("Компьютер в магазине на связи", PAUSED_MARK)


def find_heartbeat(pins: list[dict | None]) -> dict | None:
    """Закреплённое сообщение программы «на связи» среди закреплённых в чатах (самое свежее).
    Чужие закреплённые сообщения не в счёт — неважно, в каком порядке вписаны чаты."""
    ours = [p for p in pins if p and any(mark in (p.get("text") or "") for mark in HEARTBEAT_MARKS)]
    return max(ours, key=lambda p: p.get("edit_date") or p.get("date") or 0, default=None)


def main() -> int:
    token = os.environ.get("TG_TOKEN", "").strip()
    chats = [c for c in re.split(r"[,;\s]+", os.environ.get("TG_CHATS", "")) if c]
    if not token or not chats:
        print("Секреты TG_TOKEN и TG_CHATS не заданы — сторож выключен.")
        return 0
    tz = zone(os.environ.get("TG_TZ", "").strip())
    pins = []
    for chat in chats:
        try:
            pins.append(call(token, "getChat", {"chat_id": chat}).get("result", {}).get("pinned_message"))
        except Exception as e:  # noqa: BLE001 — неверный номер чата не должен ронять проверку
            print(f"Чат {chat}: не получилось прочитать ({e}) — проверьте номер в TG_CHATS.")
    pinned = find_heartbeat(pins)
    now = datetime.now(timezone.utc)
    if pinned is None:
        print("Закреплённого сообщения «🟢 Компьютер в магазине на связи» нет ни в одном чате из TG_CHATS. "
              "Программа ставит его сама, если в Настройки → Telegram включено «Следить, что компьютер на связи», "
              "и пишет в первый чат из поля «Чаты» — он должен быть и в TG_CHATS. Пока его нет, сторож молчит.")
        return 0
    if PAUSED_MARK in (pinned.get("text") or ""):
        print("Слежение выключено в программе (Настройки → Telegram) — сторож молчит.")
        return 0
    text = alert_text(pinned, now, tz)
    if text is None:
        updated = datetime.fromtimestamp(pinned.get("edit_date") or pinned.get("date") or 0, timezone.utc)
        print(f"Связь в порядке: программа отмечалась {updated.astimezone(tz):%d.%m %H:%M} "
              f"({int((now - updated).total_seconds() // 60)} мин назад).")
        return 0
    for chat in chats:
        try:
            call(token, "sendMessage", {"chat_id": chat, "text": text})
        except Exception as e:  # noqa: BLE001 — один недоступный чат не мешает остальным
            print(f"{chat}: {e}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
