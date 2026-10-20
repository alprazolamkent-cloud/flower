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


def main() -> int:
    token = os.environ.get("TG_TOKEN", "").strip()
    chats = [c for c in re.split(r"[,;\s]+", os.environ.get("TG_CHATS", "")) if c]
    if not token or not chats:
        print("Секреты TG_TOKEN и TG_CHATS не заданы — сторож выключен.")
        return 0
    tz = zone(os.environ.get("TG_TZ", "").strip())
    pinned = call(token, "getChat", {"chat_id": chats[0]}).get("result", {}).get("pinned_message")
    text = alert_text(pinned, datetime.now(timezone.utc), tz)
    if text is None:
        print("Связь в порядке (или слежение не настроено).")
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
