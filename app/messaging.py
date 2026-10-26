"""Сообщения заказчику: готовые тексты и ссылки, которые открывают чат."""

import re
from dataclasses import dataclass
from urllib.parse import quote

from .db import Delivery

CHANNELS = ["WhatsApp", "Telegram", "ВКонтакте", "Макс", "Звонок / SMS", "Другое"]

KINDS = {
    "departed": "🚚 Курьер выехал",
    "delivered": "✅ Букет доставлен",
    "review": "⭐ Просьба об отзыве",
}
SHORT = {"departed": "выехал", "delivered": "доставлен", "review": "отзыв"}

DEFAULT_TEMPLATES = {
    "departed": "Здравствуйте! Ваш заказ передан курьеру и уже в пути. "
                "Адрес доставки: {адрес}. Спасибо, что выбрали нас!",
    "delivered": "Здравствуйте! Ваш букет доставлен по адресу: {адрес}. Спасибо за заказ!",
    "review": "Здравствуйте! Спасибо, что заказали у нас 💐 Будем очень благодарны за отзыв — "
              "он помогает нам становиться лучше.",
}

PLACEHOLDERS = {
    "{адрес}": "адрес доставки",
    "{время}": "время доставки",
    "{получатель}": "имя получателя",
    "{что}": "первая строка из «Что доставить»",
}

LINK_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
NAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")
VK_ID_RE = re.compile(r"^(?:id)?(\d{3,12})$", re.IGNORECASE)

COPY_NOTE = "Текст сообщения скопирован. Вставьте его в чат (Ctrl+V) и отправьте."


@dataclass
class Action:
    url: str | None  # что открыть в браузере / мессенджере
    copy_text: bool  # скопировать ли текст в буфер обмена
    note: str  # что сказать пользователю ("" — ничего)


def readable_address(address: str) -> str:
    """Адрес для текста клиенту. Точку на карте из бота («📍 55.75, 37.61 — ссылка» + подъезд) — по-человечески."""
    if not address.startswith("📍"):
        return address
    details = ", ".join(line.strip() for line in address.splitlines()[1:] if line.strip())
    return "точке на карте" + (f" ({details})" if details else "")


def render(template: str, d: Delivery) -> str:
    values = {
        "{адрес}": readable_address(d.address),
        "{время}": d.time,
        "{получатель}": d.recipient,
        "{что}": d.items.splitlines()[0] if d.items else "",
    }
    for key, value in values.items():
        template = template.replace(key, value)
    return template


def normalize_phone(text: str) -> str | None:
    """Номер в виде цифр для ссылки (7900…), или None, если это не похоже на номер."""
    text = text.strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        return digits if 10 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    return None


def build_action(channel: str, contact: str, text: str) -> Action:
    contact = contact.strip()
    if not contact:
        return Action(None, True, "У доставки не указан контакт заказчика.\n" + COPY_NOTE)

    link = contact if LINK_RE.match(contact) else None

    if channel == "WhatsApp":
        phone = normalize_phone(contact)
        if phone:
            return Action(f"https://wa.me/{phone}?text={quote(text)}", False, "")
        return Action(link, True, "Не удалось понять номер WhatsApp.\n" + COPY_NOTE)

    if channel == "Telegram":
        if link:
            return Action(link, True, COPY_NOTE)
        m = NAME_RE.match(contact)
        if m:  # Telegram умеет подставлять текст, если известен логин
            return Action(f"https://t.me/{m.group(1)}?text={quote(text)}", False, "")
        return Action(None, True, "Для Telegram нужен логин вида @имя или ссылка на чат.\n" + COPY_NOTE)

    if channel == "ВКонтакте":
        if link:
            return Action(link, True, COPY_NOTE)
        m = VK_ID_RE.match(contact)
        if m:
            return Action(f"https://vk.com/id{m.group(1)}", True, COPY_NOTE)
        if NAME_RE.match(contact):
            return Action(f"https://vk.com/{contact.lstrip('@')}", True, COPY_NOTE)
        return Action(None, True, COPY_NOTE)

    # Макс, звонок/SMS, другое: чат по номеру или логину не открыть, только по ссылке
    if link:
        return Action(link, True, COPY_NOTE)
    return Action(None, True, f"Откройте чат с заказчиком ({contact}).\n" + COPY_NOTE)
