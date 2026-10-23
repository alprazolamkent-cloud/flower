"""Разговор бота клиентов: меню, витрина, оформление заказа, оплата, сообщения флористу.

Без сети и без окон: на вход — сообщение клиента, на выход — ответы бота и события для программы
(новый заказ, «я оплатил(а)», сообщение флористу). Состояние разговора хранится в базе.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .db import ORDER_STATUS, ClientOrder, Database
from .delivery_dialog import money

SHOWCASE, CUSTOM, CHAT, ORDERS = "💐 Витрина", "✍️ Свой букет", "💬 Написать флористу", "📦 Мои заказы"
TO_MENU, RESTART, SEND = "⬅ В меню", "↩ Начать заново", "✅ Отправить заказ"
PICKUP, SELF, NO_CARD = "🏪 Заберу сам", "Себе", "Без открытки"
STAFF_TODAY, STAFF_TOMORROW = "📋 Сегодня", "📅 Завтра"  # меню флориста: видно только в привязанном чате
STAFF_MENU = {"keyboard": [[{"text": STAFF_TODAY}, {"text": STAFF_TOMORROW}]], "resize_keyboard": True}
STAFF_LIST_MAX = 10

MENU = {"keyboard": [[{"text": SHOWCASE}, {"text": CUSTOM}], [{"text": CHAT}, {"text": ORDERS}]],
        "resize_keyboard": True}


def keyboard(*rows: list) -> dict:
    return {"keyboard": [[b if isinstance(b, dict) else {"text": b} for b in row] for row in rows],
            "resize_keyboard": True}


def inline(*buttons: tuple[str, str]) -> dict:
    return {"inline_keyboard": [[{"text": text, "callback_data": data}] for text, data in buttons]}


CLIENT_STATUS = {
    "new": "у флориста — посчитает сумму",
    "priced": "ждёт оплаты",
    "paid_claimed": "оплата на проверке",
    "confirmed": "принят ✅",
    "rejected": "отклонён",
}
assert set(CLIENT_STATUS) == set(ORDER_STATUS)

WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6}


@dataclass
class Incoming:
    chat_id: str
    text: str = ""
    name: str = ""
    username: str = ""
    contact_phone: str = ""
    photo: bytes | None = None  # фото уже скачано (самое крупное разумного размера)
    callback: str = ""  # данные нажатой кнопки под сообщением
    album: str = ""  # несколько фото одним сообщением приходят по одному с общим номером альбома


@dataclass
class Reply:
    text: str
    markup: dict | None = None
    photo: bytes | None = None
    file_id: str = ""
    ref: str = ""


@dataclass
class Result:
    replies: list[Reply] = field(default_factory=list)
    events: list[tuple] = field(default_factory=list)  # ("order", id) / ("paid", id) / ("message", id) / ("staff", user)

    def say(self, text: str, markup: dict | None = None, **kw):
        self.replies.append(Reply(text, markup, **kw))


def parse_when(text: str, today: date) -> tuple[date | None, str]:
    """«завтра к 15:00» → (дата, «к 15:00»); без понятной даты — (None, текст)."""
    low = text.lower().strip()
    day, rest = None, low
    m = re.search(r"\b(сегодня|послезавтра|завтра)\b", low)
    if m:
        day = today + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[m.group(1)])
        rest = low[:m.start()] + low[m.end():]
    else:
        m = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", low)
        if m:
            year = int(m.group(3)) if m.group(3) else today.year
            year += 2000 if year < 100 else 0
            try:
                day = date(year, int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None, text
            if not m.group(3) and day < today:
                day = day.replace(year=day.year + 1)  # «05.01» в декабре — это следующий год
            rest = low[:m.start()] + low[m.end():]
        else:
            for word, weekday in WEEKDAYS.items():
                m = re.search(rf"\b(?:в|во)?\s*{word}\w*", low)
                if m:
                    day = today + timedelta(days=(weekday - today.weekday()) % 7)
                    rest = low[:m.start()] + low[m.end():]
                    break
    rest = re.sub(r"\s+", " ", rest).strip(" ,.-")
    rest = re.sub(r"^(в|во|на)\s+", "", rest)
    return day, rest


def _phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    return text.strip() if 10 <= len(digits) <= 15 else None


def order_summary(o: ClientOrder | dict) -> str:
    get = (lambda k: getattr(o, k)) if isinstance(o, ClientOrder) else o.get
    day = get("day")
    day = date.fromisoformat(day) if isinstance(day, str) else day
    lines = [f"💐 {get('items')}", f"📅 {day:%d.%m} {get('time')}".rstrip(),
             f"📍 {get('address')}", f"👤 Кому: {get('recipient')}"]
    phone = get("client_phone") if isinstance(o, ClientOrder) else get("phone")
    if phone:
        lines.append(f"📱 Ваш телефон: {phone}")
    if get("card"):
        lines.append(f"✉️ Открытка: {get('card')}")
    return "\n".join(lines)


def payment_text(o: ClientOrder, requisites: str, qr: bool = False) -> str:
    """Что клиент видит при оплате. С QR-кодом (он идёт картинкой к этому тексту) — коротко: подпись к фото
    ограничена, а сумму QR не подставляет, её вводят вручную."""
    if qr and requisites.strip():  # на выбор: QR или перевод
        return (f"Заказ №{o.id}\n💳 К оплате: {money(o.amount)}\n\nОплатить можно любым способом:\n"
                f"1. QR-кодом — отсканируйте его в приложении банка и введите сумму {money(o.amount)} (в QR её нет).\n"
                f"2. Переводом:\n{requisites.strip()}\n\n"
                "После оплаты нажмите «Я оплатил(а)» или пришлите скриншот.")
    if qr:
        return (f"Заказ №{o.id}\n💳 К оплате: {money(o.amount)}\n\n"
                f"Отсканируйте QR-код в приложении банка и введите сумму {money(o.amount)} — "
                f"в QR её нет.\n\nПосле оплаты нажмите «Я оплатил(а)» или пришлите скриншот.")
    return (f"Заказ №{o.id}\n{order_summary(o)}\n\n💳 К оплате: {money(o.amount)}\n{requisites.strip()}\n\n"
            "После оплаты нажмите «Я оплатил(а)» или пришлите сюда скриншот перевода.")


MAX_LINK_TRIES = 5  # неверных кодов сотрудника с одного чата — дальше коды не проверяем (подбор)


class ClientFlow:
    def __init__(self, db: Database, shop_name: str, link_tries: dict | None = None):
        self.db, self.shop = db, shop_name or "наш цветочный"
        self.link_tries = link_tries if link_tries is not None else {}  # чат → неверных кодов

    def handle(self, msg: Incoming, now: datetime) -> Result:
        res = Result()
        text = msg.text.strip()
        if text.startswith("/start") and len(text.split()) > 1:  # флорист привязывает Telegram кодом из программы
            tries = self.link_tries.get(msg.chat_id, 0)
            if tries < MAX_LINK_TRIES and self.db.link_work_phone(text.split()[1], msg.chat_id, now):
                res.say("Готово, это рабочий телефон магазина 📱 Внизу кнопки «Сегодня» и «Завтра»: доставки со "
                        "кнопками «Собрал» и «Доставлено». Когда букет собран, я попрошу его фото. "
                        "Кто собрал — я определю по тому, кто сейчас на смене.", STAFF_MENU)
                res.events.append(("work_phone", msg.chat_id))
                return res
            user = self.db.link_staff(text.split()[1], msg.chat_id) if tries < MAX_LINK_TRIES else None
            if user is not None:
                res.say(f"Готово, {user.name or user.login}! Внизу кнопки «Сегодня» и «Завтра»: доставки со "
                        "кнопками «Собрал» и «Доставлено». Когда букет собран, я попрошу у вас его фото.",
                        STAFF_MENU)
                res.events.append(("staff", user.id))
                return res
            self.link_tries[msg.chat_id] = tries + 1
        if self.db.is_work_phone(msg.chat_id):  # общий рабочий телефон: меню флориста, а не клиента
            self._work_phone(msg, res, now)
            return res
        staff = self.db.staff_by_chat(msg.chat_id)
        if staff is not None:  # чат флориста: клиентского меню у него нет, а у клиентов нет этого
            self._staff(msg, res, staff, now)
            return res

        chat = self.db.touch_client(msg.chat_id, msg.name, msg.username, now)
        state, data = chat.state, dict(chat.data)

        def go(new_state: str, new_data: dict | None = None, phone: str | None = None):
            self.db.save_client_state(msg.chat_id, new_state, data if new_data is None else new_data, phone)

        if msg.callback:
            self._callback(msg, res, now, go, data)
            return res
        if text in ("/start", "/menu", TO_MENU, "Меню", "меню") or text.startswith("/start"):
            go("", {})
            res.say(f"Здравствуйте! Это {self.shop} 🌸\nВыберите букет на витрине, опишите свой — или просто "
                    "напишите флористу.", MENU)
            return res
        if text == SHOWCASE:
            go("", {})
            self._showcase(res)
            return res
        if text == CUSTOM:
            go("custom", {})
            res.say("Опишите, какой букет хотите: цветы, цвет, размер и бюджет. Можно приложить фото-пример.",
                    keyboard([TO_MENU]))
            return res
        if text == CHAT:
            go("chat", {})
            res.say("Напишите сообщение — флорист ответит прямо здесь.", keyboard([TO_MENU]))
            return res
        if text == ORDERS:
            self._orders(msg.chat_id, res)
            return res
        if text == RESTART:
            go("", {})
            res.say("Начнём заново.", MENU)
            return res

        if state == "custom":
            if msg.photo is not None:
                self._to_florist(msg, res, now, "фото-пример к заказу" + (f": {text}" if text else ""), quiet=True)
                if not text:
                    res.say("Фото передал флористу. Теперь опишите словами, что хотите и на какой бюджет.")
                    return res
            if not text:
                return res
            data = {"items": text}
            self._ask_when(res)
            go("when", data)
            return res
        if state == "when":
            day, rest = parse_when(text, now.date())
            if day is None or day < now.date() or day > now.date() + timedelta(days=365):
                res.say("Не понял дату 🙈 Напишите, например: «завтра к 15:00» или «25.10 с 12 до 14».")
                return res
            data["day"] = day.isoformat()
            if not rest:
                go("when_time", data)
                res.say(f"{day:%d.%m} — во сколько доставить? Например «к 15:00» или «с 12 до 14».")
                return res
            data["time"] = rest
            go("address", data)
            self._ask_address(res)
            return res
        if state == "when_time":
            if not text:
                return res
            data["time"] = text
            go("address", data)
            self._ask_address(res)
            return res
        if state == "address":
            if not text:
                return res
            data["address"] = "Самовывоз" if text == PICKUP else text
            if text.startswith("📍"):  # точка на карте: подъезд и квартиру она не покажет
                go("address_detail", data)
                res.say("Точку на карте принял 📍 Теперь подъезд, этаж, квартира и домофон — "
                        "или «—», если это частный дом.", keyboard(["—"], [TO_MENU]))
                return res
            go("recipient", data)
            res.say("Кому букет? Имя и телефон получателя — или нажмите «Себе».", keyboard([SELF], [TO_MENU]))
            return res
        if state == "address_detail":
            if not text:
                return res
            if text != "—":
                data["address"] += f"\n{text}"
            go("recipient", data)
            res.say("Кому букет? Имя и телефон получателя — или нажмите «Себе».", keyboard([SELF], [TO_MENU]))
            return res
        if state == "recipient":
            if not text:
                return res
            data["recipient"] = "себе" if text == SELF else text
            if chat.phone:
                data["phone"] = chat.phone
                go("card", data)
                self._ask_card(res)
            else:
                go("phone", data)
                res.say("Ваш номер телефона для связи:",
                        keyboard([{"text": "📱 Отправить мой номер", "request_contact": True}], [TO_MENU]))
            return res
        if state == "phone":
            phone = msg.contact_phone or _phone(text)
            if not phone:
                res.say("Не похоже на номер 🙈 Нажмите «📱 Отправить мой номер» или напишите его цифрами.")
                return res
            data["phone"] = phone
            go("card", data, phone=phone)
            self._ask_card(res)
            return res
        if state == "card":
            if not text:
                return res
            data["card"] = "" if text == NO_CARD else text
            go("confirm", data)
            res.say(f"Проверьте заказ:\n\n{order_summary(data)}\n\nВсё верно?", keyboard([SEND], [RESTART, TO_MENU]))
            return res
        if state == "confirm":
            if text != SEND:
                res.say("Нажмите «✅ Отправить заказ» или «↩ Начать заново».")
                return res
            order = self.db.add_client_order(
                msg.chat_id, now, date.fromisoformat(data["day"]), data.get("time", ""), data["items"],
                data["address"], data["recipient"], data.get("phone", ""), data.get("card", ""),
                data.get("showcase_id"))
            go("", {})
            res.events.append(("order", order.id))
            res.say(f"Заказ №{order.id} отправлен флористу 🌸\nОн посчитает сумму и пришлёт её сюда вместе с "
                    "реквизитами для оплаты.", MENU)
            return res

        # чат с флористом или просто сообщение вне заказа — ничего не теряется
        waiting = next((o for o in self.db.client_orders(msg.chat_id, open_only=True)
                        if o.status == "priced" or (o.status == "paid_claimed" and not o.has_paid_photo)), None)
        if msg.photo is not None and waiting is not None:  # скриншот перевода
            self.db.set_paid_photo(waiting.id, msg.photo, now)
            res.events.append(("paid", waiting.id))
            res.say(f"Спасибо! Флорист проверит оплату заказа №{waiting.id} и подтвердит.", MENU)
            return res
        self._to_florist(msg, res, now, text, quiet=state == "chat" and data.get("acked"))
        if state == "chat":
            data["acked"] = True
            go("chat", data)
        return res

    # ----- меню флориста -----

    def _staff(self, msg: Incoming, res: Result, staff, now: datetime):
        text = msg.text.strip()
        if msg.callback:
            kind, _, value = msg.callback.partition(":")
            if kind in ("sa", "sd") and value.isdigit():  # собрал / доставлено — программа сама решит и ответит
                res.events.append(("staff_assembled" if kind == "sa" else "staff_delivered", staff.id, int(value)))
        elif msg.photo is not None:
            res.events.append(("staff_photo", staff.id, msg.photo, msg.album))
        elif text in (STAFF_TODAY, STAFF_TOMORROW):
            self._staff_list(res, now.date() + timedelta(days=text == STAFF_TOMORROW), now.date())
        else:
            res.say("Доставки — кнопками внизу. Когда отметите букет собранным, я попрошу фото — "
                    "просто пришлите его сюда.", STAFF_MENU)

    def _work_phone(self, msg: Incoming, res: Result, now: datetime):
        """Рабочий телефон: те же кнопки, но человек не известен — его определит программа по смене."""
        text, chat = msg.text.strip(), msg.chat_id
        if msg.callback:
            kind, _, value = msg.callback.partition(":")
            if kind in ("sa", "sd") and value.isdigit():
                res.events.append(("work_assembled" if kind == "sa" else "work_delivered", chat, int(value), None))
            elif kind == "sw":  # «Кто собрал?» — выбрали человека: sw:a:12:3
                what, _, rest = value.partition(":")
                delivery, _, user = rest.partition(":")
                if what in ("a", "d") and delivery.isdigit() and user.isdigit():
                    res.events.append(("work_assembled" if what == "a" else "work_delivered", chat,
                                       int(delivery), int(user)))
        elif msg.photo is not None:
            res.events.append(("work_photo", chat, msg.photo, msg.album))
        elif text in (STAFF_TODAY, STAFF_TOMORROW):
            self._staff_list(res, now.date() + timedelta(days=text == STAFF_TOMORROW), now.date())
        else:
            res.say("Доставки — кнопками внизу. Когда отметите букет собранным, я попрошу фото — "
                    "просто пришлите его сюда.", STAFF_MENU)

    def _staff_list(self, res: Result, day: date, today: date):
        items = [d for d in self.db.for_day(day) if not d.delivered and d.delete_requested_at is None]
        title = "сегодня" if day == today else f"{day:%d.%m}"
        if not items:
            res.say(f"На {title} доставок нет 🌿", STAFF_MENU)
            return
        res.say(f"Доставки на {title}: {len(items)}", STAFF_MENU)
        for d in items[:STAFF_LIST_MAX]:
            what = (d.items.splitlines() or ["—"])[0]
            state = "💐 собрано" if d.assembled else "⏳ не собрано"
            button = ("✓ Доставлено", f"sd:{d.id}") if d.assembled else ("💐 Собрал", f"sa:{d.id}")
            res.say(f"{d.time or '—'} · {what}\n📍 {d.address or 'адрес не указан'}\n{state}", inline(button))
        if len(items) > STAFF_LIST_MAX:
            res.say(f"…и ещё {len(items) - STAFF_LIST_MAX}: остальные в программе.")

    # ----- части разговора -----

    def _callback(self, msg: Incoming, res: Result, now: datetime, go, data: dict):
        kind, _, value = msg.callback.partition(":")
        if kind == "pick" and value.isdigit():
            item = self.db.get_showcase_item(int(value))
            if item is None or not item.active:
                res.say("Этого букета уже нет на витрине 😔 Посмотрите другие или опишите свой.", MENU)
                return
            go("when", {"items": f"{item.title} — {money(item.price)}", "showcase_id": item.id})
            res.say(f"Отличный выбор: {item.title} 💐")
            self._ask_when(res)
        elif kind == "paid" and value.isdigit():
            order = self.db.get_client_order(int(value))
            if order is None or order.chat_id != str(msg.chat_id):
                return
            if order.status == "priced":
                self.db.set_client_order_status(order.id, "paid_claimed", now)
                res.events.append(("paid", order.id))
                res.say(f"Спасибо! Флорист проверит оплату заказа №{order.id} и подтвердит. "
                        "Если есть скриншот перевода — пришлите его сюда.")
            elif order.status == "paid_claimed":
                res.say("Уже передал флористу — он проверит оплату и подтвердит.")
            elif order.status == "confirmed":
                res.say("Оплата уже подтверждена, заказ принят ✅")

    def _showcase(self, res: Result):
        items = self.db.showcase(only_active=True)
        if not items:
            res.say("Витрина сейчас обновляется 🌷 Опишите свой букет («✍️ Свой букет») или напишите флористу.", MENU)
            return
        res.say("Вот что есть сейчас. Нажмите «Заказать» под понравившимся:", MENU)
        for item in items[:15]:
            caption = f"{item.title} — {money(item.price)}" + (f"\n{item.description}" if item.description else "")
            button = inline(("🛒 Заказать", f"pick:{item.id}"))
            if item.photo or item.file_id:
                res.say(caption, button, photo=None if item.file_id else item.photo, file_id=item.file_id,
                        ref=f"showcase:{item.id}")
            else:
                res.say(caption, button)

    def _orders(self, chat_id: str, res: Result):
        orders = self.db.client_orders(chat_id, limit=5)
        if not orders:
            res.say("Заказов пока нет. Выберите букет на витрине или опишите свой 🌸", MENU)
            return
        lines = ["Ваши последние заказы:"]
        for o in orders:
            status = CLIENT_STATUS.get(o.status, o.status)
            if o.status == "priced":
                status += f" ({money(o.amount)})"
            if o.status == "confirmed" and o.delivery_id:
                d = self.db.get_delivery(o.delivery_id)
                if d is not None:
                    status = "доставлен ✅" if d.delivered else "букет собран 💐" if d.assembled else status
            lines.append(f"№{o.id} · {o.day:%d.%m} {o.time} · {o.items.splitlines()[0][:40]} — {status}")
        res.say("\n".join(lines), MENU)

    def _to_florist(self, msg: Incoming, res: Result, now: datetime, text: str, quiet: bool = False):
        if not text and msg.photo is None:
            return
        message_id = self.db.add_client_message(msg.chat_id, True, text, now, msg.photo)
        res.events.append(("message", message_id))
        if not quiet:
            res.say("Передал флористу 👌 Он ответит здесь.", MENU)

    @staticmethod
    def _ask_when(res: Result):
        res.say("Когда доставить? Напишите дату и время, например «завтра к 15:00» или «25.10 с 12 до 14».",
                keyboard([TO_MENU]))

    @staticmethod
    def _ask_address(res: Result):
        res.say("Куда доставить? Напишите адрес (улица, дом, квартира, подъезд) или поделитесь точкой на карте. "
                "Или нажмите «🏪 Заберу сам».",
                keyboard([{"text": "📍 Отправить геолокацию", "request_location": True}], [PICKUP], [TO_MENU]))

    @staticmethod
    def _ask_card(res: Result):
        res.say("Нужна открытка? Напишите текст — или нажмите «Без открытки».", keyboard([NO_CARD], [TO_MENU]))
