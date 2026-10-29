"""Разговор бота клиентов: меню, витрина, оформление заказа, оплата, сообщения флористу.

Без сети и без окон: на вход — сообщение клиента, на выход — ответы бота и события для программы
(новый заказ, «я оплатил(а)», сообщение флористу). Состояние разговора хранится в базе.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .db import ORDER_STATUS, ClientOrder, Database, ShowcaseItem
from .delivery_dialog import money
from .reminders import parse_deadline_time

SHOWCASE, CUSTOM, CHAT, ORDERS = "💐 Витрина", "✍️ Свой букет", "💬 Написать флористу", "📦 Мои заказы"
TO_MENU, RESTART, SEND = "⬅ В меню", "↩ Начать заново", "✅ Отправить заказ"
PICKUP, SELF, NO_CARD = "🏪 Заберу сам", "Себе", "Без открытки"
STAFF_TODAY, STAFF_TOMORROW = "📋 Сегодня", "📅 Завтра"  # меню флориста: видно только в привязанном чате
STAFF_SHOWCASE, STAFF_ADD, STAFF_CANCEL = "💐 Витрина", "➕ Новый букет", "✖ Отмена"
STAFF_MENU = {"keyboard": [[{"text": STAFF_TODAY}, {"text": STAFF_TOMORROW}],
                           [{"text": STAFF_SHOWCASE}, {"text": STAFF_ADD}]], "resize_keyboard": True}
STAFF_LIST_MAX = 10
SHOWCASE_PAGE = 5  # столько букетов витрины клиент видит за раз
STAFF_HELP = ("Кнопки внизу: «Сегодня» и «Завтра» — доставки («Собрал», «Доставлено»), «Витрина» — что видят "
              "клиенты, «Новый букет» — добавить на витрину фото с подписью «Название цена».")


def bouquets_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "букет"
    return "букета" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "букетов"


MENU = {"keyboard": [[{"text": SHOWCASE}, {"text": CUSTOM}], [{"text": CHAT}, {"text": ORDERS}]],
        "resize_keyboard": True}
PREORDER = "🌷 Предзаказ на "  # кнопка перед праздником: «🌷 Предзаказ на 8 Марта»
PREORDER_DAYS = 14  # за сколько дней до праздника бот предлагает предзаказ


def preorder_menu(name: str) -> dict:
    return {"keyboard": [[{"text": PREORDER + name}], *MENU["keyboard"]], "resize_keyboard": True}


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
MONTHS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
MONTH_RE = re.compile(r"\b(\d{1,2})\s+(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*")
ORDER_STEPS = ("when", "when_time", "address", "address_detail", "recipient", "phone", "card", "confirm")


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
        words = MONTH_RE.search(low) if m is None else None
        if words:  # «8 марта», «1 ноября»
            month = next(i for i, prefix in enumerate(MONTHS, 1) if words.group(2).startswith(prefix))
            try:
                day = date(today.year, month, int(words.group(1)))
            except ValueError:
                return None, text
            if day < today:
                day = day.replace(year=day.year + 1)
            rest = low[:words.start()] + low[words.end():]
        elif m:
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


def parse_bouquet(text: str) -> tuple[str, int, str] | None:
    """«Нежность 3500» → («Нежность», 3500, ""); описание — со второй строки. Нет названия или цены — None."""
    lines = text.strip().splitlines()
    if not lines:
        return None
    m = re.search(r"(\d[\d \u00a0]{0,9}\d|\d)\s*(?:₽|р\b\.?|руб\w*)?\s*$", lines[0].strip(), re.IGNORECASE)
    if m is None:
        return None
    first = lines[0].strip()
    title = first[:m.start()].strip(" —–-:,")
    price = int(re.sub(r"\D", "", m.group(1)))
    if not title or price <= 0:
        return None
    return title[:80], price, "\n".join(lines[1:]).strip()


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
    def __init__(self, db: Database, shop_name: str, link_tries: dict | None = None,
                 staff_state: dict | None = None, preorder: tuple[date, str] | None = None):
        self.db, self.shop = db, shop_name or "наш цветочный"
        self.preorder = preorder  # (дата, название) ближайшего праздника — бот принимает на него предзаказы
        self.MENU = preorder_menu(preorder[1]) if preorder else MENU
        self.today = date.today()
        self.link_tries = link_tries if link_tries is not None else {}  # чат → неверных кодов
        self.staff_state = staff_state if staff_state is not None else {}  # чат флориста → шаг «новый букет»

    def handle(self, msg: Incoming, now: datetime) -> Result:
        res = Result()
        self.today = now.date()
        text = msg.text.strip()
        if text.startswith("/start") and len(text.split()) > 1:  # флорист привязывает Telegram кодом из программы
            tries = self.link_tries.get(msg.chat_id, 0)
            if tries < MAX_LINK_TRIES and self.db.link_work_phone(text.split()[1], msg.chat_id, now):
                res.say("Готово, это рабочий телефон магазина 📱\n" + STAFF_HELP + "\nКогда букет собран, я попрошу "
                        "его фото. Кто собрал — определю по тому, кто сейчас на смене.", STAFF_MENU)
                res.events.append(("work_phone", msg.chat_id))
                return res
            user = self.db.link_staff(text.split()[1], msg.chat_id) if tries < MAX_LINK_TRIES else None
            if user is not None:
                res.say(f"Готово, {user.name or user.login}!\n" + STAFF_HELP + "\nКогда букет собран, я попрошу "
                        "у вас его фото.", STAFF_MENU)
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
            banner = (f"\n\n🌷 Принимаем предзаказы на {self.preorder[1]} ({self.preorder[0]:%d.%m}) — "
                      "нажмите кнопку ниже." if self.preorder else "")
            res.say(f"Здравствуйте! Это {self.shop} 🌸\nВыберите букет на витрине, опишите свой — или просто "
                    "напишите флористу." + banner, self.MENU)
            return res
        if self.preorder and text == PREORDER + self.preorder[1]:
            day, name = self.preorder
            go("", {"preset_day": day.isoformat()})
            res.say(f"Предзаказ на {name}, {day:%d.%m} 🌷 Выберите букет ниже или нажмите «✍️ Свой букет» — "
                    "дату я уже записал, спрошу только время.", self.MENU)
            self._showcase(res, header=False)
            return res
        if text == SHOWCASE:
            go("", {})
            self._showcase(res)
            return res
        if text == CUSTOM:
            go("custom", {"preset_day": data["preset_day"]} if data.get("preset_day") else {})
            res.say("Опишите, какой букет хотите: цветы, цвет, размер и бюджет. Можно приложить фото-пример.",
                    keyboard([TO_MENU]))
            return res
        if text == CHAT:
            go("chat", {})
            res.say("Напишите сообщение — флорист ответит прямо здесь.", keyboard([TO_MENU]))
            return res
        if text == ORDERS:
            go("", {})
            self._orders(msg.chat_id, res)
            return res
        if text == RESTART:
            go("", {})
            res.say("Начнём заново.", self.MENU)
            return res

        if state in ORDER_STEPS and msg.photo is not None:  # фото посреди заказа — флористу, а не в пустоту
            self._to_florist(msg, res, now, "фото к заказу" + (f": {text}" if text else ""), quiet=True)
            res.say("Фото передал флористу 👌 Продолжим оформление — ответьте, пожалуйста, на вопрос выше.")
            return res
        if state in ORDER_STEPS and not text and msg.contact_phone and state != "phone":
            text = msg.contact_phone  # прислали контакт (например, получателя) — как будто написали номер
        if state == "custom":
            if msg.photo is not None:
                self._to_florist(msg, res, now, "фото-пример к заказу" + (f": {text}" if text else ""), quiet=True)
                if not text:
                    res.say("Фото передал флористу. Теперь опишите словами, что хотите и на какой бюджет.")
                    return res
            if not text:
                return res
            if self._preset(data):  # предзаказ на праздник: дата известна — только время
                day = date.fromisoformat(data["preset_day"])
                go("when_time", {"items": text, "day": day.isoformat()})
                res.say(f"{day:%d.%m} — во сколько доставить? Например «к 15:00» или «с 12 до 14».",
                        keyboard([TO_MENU]))
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
            if day == now.date() and self._time_passed(rest, now):
                res.say("Это время сегодня уже прошло 🙈 Напишите другое время или дату.")
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
            if data.get("day") == now.date().isoformat() and self._time_passed(text, now):
                res.say("Это время сегодня уже прошло 🙈 Напишите другое время.")
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
            item = self.db.get_showcase_item(data["showcase_id"]) if data.get("showcase_id") else None
            if item is not None and ((item.single and not item.active)  # пока оформляли, его заказал другой
                                     or (item.stock is not None and item.stock <= 0)):  # собранные разобрали
                go("", {})
                res.say("Ох, этот букет только что заказали 😔 Готовых больше нет. Посмотрите другие "
                        "на витрине или опишите свой — соберём похожий.", self.MENU)
                return res
            order = self.db.add_client_order(
                msg.chat_id, now, date.fromisoformat(data["day"]), data.get("time", ""), data["items"],
                data["address"], data["recipient"], data.get("phone", ""), data.get("card", ""),
                data.get("showcase_id"))
            go("", {})
            res.events.append(("order", order.id))
            res.say(f"Заказ №{order.id} отправлен флористу 🌸\nОн посчитает сумму и пришлёт её сюда вместе с "
                    "реквизитами для оплаты.", self.MENU)
            return res

        # чат с флористом или просто сообщение вне заказа — ничего не теряется
        waiting = next((o for o in self.db.client_orders(msg.chat_id, open_only=True)
                        if o.status == "priced" or (o.status == "paid_claimed" and not o.has_paid_photo)), None)
        if msg.photo is not None and waiting is not None:  # скриншот перевода
            self.db.set_paid_photo(waiting.id, msg.photo, now)
            res.events.append(("paid", waiting.id))
            res.say(f"Спасибо! Флорист проверит оплату заказа №{waiting.id} и подтвердит.", self.MENU)
            return res
        self._to_florist(msg, res, now, text, quiet=state == "chat" and data.get("acked"))
        if msg.photo is not None or text:
            go("chat", {"acked": True})  # «Передал флористу» — один раз, следующие сообщения уходят молча
        return res

    # ----- меню флориста -----

    def _staff(self, msg: Incoming, res: Result, staff, now: datetime):
        text = msg.text.strip()
        if self._showcase_part(msg, res, staff.id):
            return
        if msg.callback:
            kind, _, value = msg.callback.partition(":")
            if kind in ("sa", "sd") and value.isdigit():  # собрал / доставлено — программа сама решит и ответит
                res.events.append(("staff_assembled" if kind == "sa" else "staff_delivered", staff.id, int(value)))
        elif msg.photo is not None:
            res.events.append(("staff_photo", staff.id, msg.photo, msg.album))
        elif text in (STAFF_TODAY, STAFF_TOMORROW):
            self._staff_list(res, now.date() + timedelta(days=text == STAFF_TOMORROW), now.date())
        else:
            res.say(STAFF_HELP, STAFF_MENU)

    def _work_phone(self, msg: Incoming, res: Result, now: datetime):
        """Рабочий телефон: те же кнопки, но человек не известен — его определит программа по смене."""
        text, chat = msg.text.strip(), msg.chat_id
        if self._showcase_part(msg, res, None):
            return
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
            res.say(STAFF_HELP, STAFF_MENU)

    def _showcase_part(self, msg: Incoming, res: Result, who: int | None) -> bool:
        """Витрина с телефона: «➕ Новый букет» (фото → название и цена), список с «в наличии», ценой и удалением.
        True — сообщение разобрано здесь. who — флорист (None — общий рабочий телефон)."""
        text, chat = msg.text.strip(), msg.chat_id
        state = self.staff_state.get(chat)
        kind, _, value = msg.callback.partition(":") if msg.callback else ("", "", "")
        if text in (STAFF_TODAY, STAFF_TOMORROW):
            self.staff_state.pop(chat, None)  # пошёл в доставки — незаконченный букет бросаем
            return False
        if text in (STAFF_CANCEL, "/cancel") and state:
            self.staff_state.pop(chat, None)
            res.say("Отменил.", STAFF_MENU)
            return True
        if text == STAFF_ADD:
            self.staff_state[chat] = {"step": "photo"}
            res.say("Пришлите фото букета 📷 Можно сразу с подписью: «Нежность 3500» (название и цена). "
                    "Описание — второй строкой, по желанию.", keyboard([STAFF_CANCEL]))
            return True
        if text == STAFF_SHOWCASE:
            self.staff_state.pop(chat, None)
            self._showcase_list(res)
            return True
        if kind == "vl" and value.isdigit():  # «Показать ещё» в списке витрины
            self._showcase_list(res, int(value))
            return True
        if kind in ("vt", "vd", "vy", "vn", "vp", "ve", "vf", "vh", "vo", "v1") and value.isdigit():
            self._showcase_button(kind, int(value), res, chat, who)
            return True
        if state is None:
            return False
        if state["step"] in ("edit_photo", "edit_title", "edit_desc"):
            item = self.db.get_showcase_item(state["id"])
            if item is None:
                self.staff_state.pop(chat, None)
                res.say("Этого букета уже нет на витрине.", STAFF_MENU)
                return True
            if state["step"] == "edit_photo":
                if msg.photo is None:
                    res.say("Жду новое фото 📷 Или «✖ Отмена».", keyboard([STAFF_CANCEL]))
                    return True
                item.photo, what = msg.photo, "новое фото"
            elif not text:
                return True
            elif state["step"] == "edit_title":
                item.title, what = text.splitlines()[0][:80], "название"
            else:
                item.description, what = ("" if text in ("—", "-") else text), "описание"
            self.staff_state.pop(chat, None)
            self.db.save_showcase_item(item)
            res.events.append(("showcase", who, f"«{item.title}»: {what}"))
            res.say(f"Готово, {what} сохранено: {item.title} — {money(item.price)}", STAFF_MENU)
            return True
        if state["step"] == "price":
            if re.fullmatch(r"\d[\d \u00a0]*\s*(₽|р\.?)?", text) and int(re.sub(r"\D", "", text)) > 0:
                item = self.db.get_showcase_item(state["id"])
                self.staff_state.pop(chat, None)
                if item is not None:
                    item.price = int(re.sub(r"\D", "", text))
                    self.db.save_showcase_item(item)
                    res.events.append(("showcase", who, f"цена «{item.title}»: {money(item.price)}"))
                    res.say(f"Готово: {item.title} — {money(item.price)}", STAFF_MENU)
                return True
            res.say("Напишите цену цифрами, например 3500.", keyboard([STAFF_CANCEL]))
            return True
        if msg.photo is not None and state["step"] == "photo":
            parsed = parse_bouquet(text) if text else None
            if parsed is None:
                self.staff_state[chat] = {"step": "title", "photo": msg.photo}
                res.say("Название и цена, например: Нежность 3500\nОписание (состав, размер) — второй строкой, "
                        "по желанию.", keyboard([STAFF_CANCEL]))
            else:
                self._add_bouquet(res, chat, who, msg.photo, *parsed)
            return True
        if state["step"] == "title" and msg.photo is not None and not text:
            state["photo"] = msg.photo
            res.say("Фото заменил 👌 Теперь название и цена, например: Нежность 3500", keyboard([STAFF_CANCEL]))
            return True
        if state["step"] == "title" and text:
            if msg.photo is not None:
                state["photo"] = msg.photo
            parsed = parse_bouquet(text)
            if parsed is None:
                res.say("Не понял цену 🙈 Напишите название и цену, например: Нежность 3500", keyboard([STAFF_CANCEL]))
            else:
                self._add_bouquet(res, chat, who, state["photo"], *parsed)
            return True
        if state["step"] == "photo":
            res.say("Жду фото букета 📷 Или нажмите «✖ Отмена».", keyboard([STAFF_CANCEL]))
            return True
        return False

    def _add_bouquet(self, res: Result, chat: str, who: int | None, photo: bytes, title: str, price: int,
                     description: str):
        self.staff_state.pop(chat, None)
        item = ShowcaseItem(0, title, description, price, photo)
        item_id = self.db.save_showcase_item(item)
        res.events.append(("showcase", who, f"добавлен «{title}» — {money(price)}"))
        res.say(f"✅ На витрине: {title} — {money(price)}\nКлиенты увидят его в «💐 Витрина».", STAFF_MENU)
        res.say("Это готовый букет, который стоит в магазине? Тогда отметьте — после первого заказа он сам "
                "пропадёт с витрины, и второй клиент его не закажет.",
                {"inline_keyboard": [[{"text": "1️⃣ Да, один экземпляр", "callback_data": f"v1:{item_id}"}],
                                     [{"text": "✏️ Изменить", "callback_data": f"ve:{item_id}"}]]})

    def _showcase_list(self, res: Result, offset: int = 0):
        items = self.db.showcase()
        if not items:
            res.say("Витрина пока пустая. Нажмите «➕ Новый букет».", STAFF_MENU)
            return
        if offset == 0:
            res.say(f"На витрине: {len(items)}. Кнопки под букетом — наличие, изменить, удалить.", STAFF_MENU)
        for item in items[offset:offset + STAFF_LIST_MAX]:
            if item.stock is not None:  # собирается по техкарте в программе — наличие считается само
                state = (f"💐 собран: в наличии {max(item.stock, 0)}" if item.stock > 0 else "💐 собранных нет (скрыт)")
                if not item.active:
                    state += ", 🚫 скрыт вручную"
                toggle = "🚫 Скрыть" if item.active else "✅ Вернуть"
            elif item.single:
                state = "1️⃣ один экземпляр, " + ("✅ на витрине" if item.active else "🔒 продан / заказан (скрыт)")
                toggle = "🔒 Продан" if item.active else "✅ Вернуть"
            else:
                state = "✅ в наличии" if item.active else "🚫 нет в наличии (скрыт)"
                toggle = "🚫 Нет в наличии" if item.active else "✅ Вернуть"
            res.say(f"{item.title} — {money(item.price)}\n{state}", {"inline_keyboard": [[
                {"text": toggle, "callback_data": f"vt:{item.id}"},
                {"text": "✏️ Изменить", "callback_data": f"ve:{item.id}"},
                {"text": "🗑", "callback_data": f"vd:{item.id}"}]]})
        shown = offset + STAFF_LIST_MAX
        if shown < len(items):
            res.say(f"Показано {shown} из {len(items)}", inline(("⬇ Показать ещё", f"vl:{shown}")))

    def _showcase_button(self, kind: str, item_id: int, res: Result, chat: str, who: int | None):
        item = self.db.get_showcase_item(item_id)
        if item is None:
            res.say("Этого букета уже нет на витрине.", STAFF_MENU)
        elif kind == "vt":
            item.active = not item.active
            self.db.save_showcase_item(item)
            hidden = "продан" if item.single else "нет в наличии"
            res.events.append(("showcase", who, f"«{item.title}»: " + ("в наличии" if item.active else hidden)))
            res.say(f"{item.title}: " + ("✅ вернул на витрину" if item.active else "🔒 скрыл — клиенты не увидят"))
        elif kind == "ve":
            res.say(f"{item.title} — {money(item.price)}\nЧто поменять?", {"inline_keyboard": [
                [{"text": "📷 Фото", "callback_data": f"vf:{item_id}"},
                 {"text": "✏️ Название", "callback_data": f"vh:{item_id}"}],
                [{"text": "📝 Описание", "callback_data": f"vo:{item_id}"},
                 {"text": "💰 Цена", "callback_data": f"vp:{item_id}"}],
                [{"text": "1️⃣ Один экземпляр: " + ("да — сделать обычным" if item.single else "нет — включить"),
                  "callback_data": f"v1:{item_id}"}]]})
        elif kind in ("vf", "vh", "vo"):
            step, ask = {"vf": ("edit_photo", "Пришлите новое фото букета 📷"),
                         "vh": ("edit_title", f"Новое название (сейчас «{item.title}»):"),
                         "vo": ("edit_desc", "Новое описание: состав, размер. «—» — убрать описание.")}[kind]
            self.staff_state[chat] = {"step": step, "id": item_id}
            res.say(ask, keyboard([STAFF_CANCEL]))
        elif kind == "v1":
            item.single = not item.single
            self.db.save_showcase_item(item)
            res.events.append(("showcase", who, f"«{item.title}»: " + ("один экземпляр" if item.single
                                                                         else "можно заказывать много раз")))
            res.say(f"{item.title}: " + ("1️⃣ один экземпляр — после первого заказа сам пропадёт с витрины"
                                         if item.single else "можно заказывать сколько угодно раз"))
        elif kind == "vp":
            self.staff_state[chat] = {"step": "price", "id": item_id}
            res.say(f"Новая цена для «{item.title}» (сейчас {money(item.price)}):", keyboard([STAFF_CANCEL]))
        elif kind == "vd":
            res.say(f"Удалить «{item.title}» с витрины насовсем?", {"inline_keyboard": [[
                {"text": "🗑 Да, удалить", "callback_data": f"vy:{item_id}"},
                {"text": "Нет", "callback_data": f"vn:{item_id}"}]]})
        elif kind == "vy":
            self.db.delete_showcase_item(item_id)
            res.events.append(("showcase", who, f"удалён «{item.title}»"))
            res.say(f"Удалил «{item.title}» 🗑")
        else:
            res.say("Оставил.")

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

    @staticmethod
    def _time_passed(when: str, now: datetime) -> bool:
        """Сегодняшнее время уже прошло (с запасом 10 минут): «к 10:00» в полдень. Окно «с 12 до 14» в 13:00 — нет."""
        deadline = parse_deadline_time(when)
        return deadline is not None and datetime.combine(now.date(), deadline) < now - timedelta(minutes=10)

    # ----- части разговора -----

    def _callback(self, msg: Incoming, res: Result, now: datetime, go, data: dict):
        kind, _, value = msg.callback.partition(":")
        if kind == "pick" and value.isdigit():
            item = self.db.get_showcase_item(int(value))
            if item is None or not item.visible:
                res.say("Этого букета уже нет на витрине 😔 Посмотрите другие или опишите свой.", self.MENU)
                return
            chosen = {"items": f"{item.title} — {money(item.price)}", "showcase_id": item.id}
            res.say(f"Отличный выбор: {item.title} 💐")
            if self._preset(data):  # предзаказ на праздник: дата известна — только время
                day = date.fromisoformat(data["preset_day"])
                go("when_time", {**chosen, "day": day.isoformat()})
                res.say(f"{day:%d.%m} — во сколько доставить? Например «к 15:00» или «с 12 до 14».",
                        keyboard([TO_MENU]))
                return
            go("when", chosen)
            self._ask_when(res)
        elif kind == "more" and value.isdigit():
            self._showcase(res, int(value))
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
            elif order.status == "rejected":
                res.say(f"Заказ №{order.id} отменён — оплачивать его не нужно. Если есть вопросы, напишите флористу.",
                        MENU)

    def _preset(self, data: dict) -> bool:
        """Есть дата предзаказа на праздник, и она ещё впереди."""
        try:
            return date.fromisoformat(data.get("preset_day") or "") > self.today
        except ValueError:
            return False

    def _showcase(self, res: Result, offset: int = 0, header: bool = True):
        """Витрина порциями: по SHOWCASE_PAGE букетов и «Показать ещё» — чат не заваливается фотографиями."""
        items = self.db.showcase(only_active=True)
        if not items:
            res.say("Витрина сейчас обновляется 🌷 Опишите свой букет («✍️ Свой букет») или напишите флористу.", self.MENU)
            return
        if offset >= len(items):
            res.say("Это все букеты на витрине 🌷 Не нашли подходящий — «✍️ Свой букет».", self.MENU)
            return
        if offset == 0 and header:
            res.say(f"Сейчас на витрине {len(items)} {bouquets_word(len(items))}. Нажмите «Заказать» "
                    "под понравившимся:", self.MENU)
        page = items[offset:offset + SHOWCASE_PAGE]
        for item in page:
            caption = (f"{item.title} — {money(item.price)}" + (f"\n{item.description}" if item.description else "")
                       + ("\n✨ В одном экземпляре — готов прямо сейчас" if item.single else "")
                       + (("\n✨ Готов прямо сейчас" + (f" — в наличии {item.stock}" if item.stock > 1 else ""))
                          if item.stock is not None else ""))
            button = inline(("🛒 Заказать", f"pick:{item.id}"))
            if item.photo or item.file_id:
                res.say(caption, button, photo=None if item.file_id else item.photo, file_id=item.file_id,
                        ref=f"showcase:{item.id}")
            else:
                res.say(caption, button)
        shown = offset + len(page)
        if shown < len(items):
            res.say(f"Показано {shown} из {len(items)}", inline(("⬇ Показать ещё", f"more:{shown}")))

    def _orders(self, chat_id: str, res: Result):
        orders = self.db.client_orders(chat_id, limit=5)
        if not orders:
            res.say("Заказов пока нет. Выберите букет на витрине или опишите свой 🌸", self.MENU)
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
        res.say("\n".join(lines), self.MENU)

    def _to_florist(self, msg: Incoming, res: Result, now: datetime, text: str, quiet: bool = False):
        if not text and msg.photo is None:
            return
        message_id = self.db.add_client_message(msg.chat_id, True, text, now, msg.photo)
        res.events.append(("message", message_id))
        if not quiet:
            res.say("Передал флористу 👌 Он ответит здесь.", self.MENU)

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
