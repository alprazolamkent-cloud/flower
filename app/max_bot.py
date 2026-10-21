"""Бот для клиентов в MAX: тот же разговор, заказы и окно «Клиенты», что и в Telegram, — другая сеть.

Клиенты из MAX хранятся как «max:номер_пользователя», поэтому в базе и в окне всё общее.
В MAX нет постоянной клавиатуры внизу: меню — кнопками под сообщением (нажатие = сообщение от клиента).
"""

import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PySide6.QtCore import QSettings

from .client_bot import MAX_IMAGE_FILE, MAX_PREFIX, ClientBot, Parsed, client_settings, max_settings
from .client_flow import Incoming

API = "https://platform-api2.max.ru"  # Bot API v2 (официальный клиент MAX)
OLD_API = "https://platform-api.max.ru"  # прежний адрес — на случай, если новый недоступен
TIMEOUT = 20
LONG_POLL = 25
MAX_TEXT = 4000
UPDATE_TYPES = "message_created,message_callback,bot_started"
NOT_READY_RETRIES = 3  # фото только что загрузили — MAX может ещё обрабатывать его


class MaxError(Exception):
    def __init__(self, message: str, permanent: bool = False, retry_after: int = 0, code: str = ""):
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after
        self.code = code


def _base() -> str:
    return str(QSettings("FlowerDeliveries", "FlowerDeliveries").value("max_api", API)) or API


def _call(token: str, method: str, path: str, query: dict | None = None, body: dict | None = None,
          timeout: float = TIMEOUT, base: str | None = None) -> dict:
    url = f"{base or _base()}/{path}"
    query = {k: v for k, v in (query or {}).items() if v not in (None, "")}
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", token.strip())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            payload = json.load(e)
        except ValueError:
            payload = {}
        message = payload.get("message") or payload.get("code") or f"MAX ответил ошибкой {e.code}"
        raise MaxError(message, permanent=e.code in (400, 401, 403, 404), code=payload.get("code", ""),
                       retry_after=5 if e.code == 429 else 0) from None
    except OSError as e:
        raise MaxError(f"Нет связи с MAX: {e}") from None


def max_requisites() -> str:
    return client_settings()["requisites"].strip()


def get_me(token: str) -> dict:
    """Кто этот бот: {"user_id", "first_name", "username"}. Заодно проверка токена.
    Новый адрес API не отвечает — пробуем прежний и запоминаем, какой работает."""
    try:
        return _call(token, "GET", "me")
    except MaxError as e:
        if e.code == "verify.token" or "access_token" in str(e):
            raise
        me = _call(token, "GET", "me", base=OLD_API)
        QSettings("FlowerDeliveries", "FlowerDeliveries").setValue("max_api", OLD_API)
        return me


def get_updates(token: str, marker: int | None, wait: int = LONG_POLL) -> tuple[list[dict], int | None]:
    data = _call(token, "GET", "updates", {"timeout": wait, "marker": marker, "types": UPDATE_TYPES, "limit": 100},
                 timeout=wait + 15)
    return data.get("updates") or [], data.get("marker")


def answer_callback(token: str, callback_id: str, text: str = "👌"):
    _call(token, "POST", "answers", {"callback_id": callback_id}, {"notification": text})


def download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(MAX_IMAGE_FILE + 1)
    except OSError as e:
        raise MaxError(f"Нет связи с MAX: {e}") from None
    if len(data) > MAX_IMAGE_FILE:
        raise MaxError("файл слишком большой", permanent=True)
    return data


def upload_image(token: str, data: bytes) -> dict:
    """Загрузить фото: адрес для загрузки → файл туда → то, что вставить во вложение."""
    target = _call(token, "POST", "uploads", {"type": "image"})
    boundary = uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="photo.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(target["url"], data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        raise MaxError(f"MAX не принял фото ({e.code})", permanent=400 <= e.code < 500) from None
    except OSError as e:
        raise MaxError(f"Нет связи с MAX: {e}") from None
    try:
        result = json.loads(raw)
    except ValueError:
        result = {}
    if result.get("photos"):
        return {"photos": result["photos"]}
    token_value = result.get("token") or target.get("token")
    if not token_value:
        raise MaxError("MAX не вернул загруженное фото", permanent=True)
    return {"token": token_value}


def keyboard(markup: dict | None, requisites: str = "") -> dict | None:
    """Кнопки Telegram → кнопки MAX. Меню внизу (keyboard) становится кнопками под сообщением,
    нажатие на которые отправляет их текст от клиента — разговор понимает это так же.
    requisites — под кнопкой «Я оплатил(а)» добавить «Скопировать реквизиты» (нажал — и в буфере)."""
    if not markup:
        return None
    rows = []
    for row in markup.get("inline_keyboard") or []:
        rows.append([{"type": "callback", "text": b["text"], "payload": b["callback_data"]} for b in row])
        if requisites and any(b["callback_data"].startswith("paid:") for b in row):
            rows.insert(len(rows) - 1, [{"type": "clipboard", "text": "📋 Скопировать реквизиты",
                                         "payload": requisites[:1000]}])
    for row in markup.get("keyboard") or []:
        rows.append([{"type": "request_contact", "text": b["text"]} if b.get("request_contact")
                     else {"type": "request_geo_location", "text": b["text"]} if b.get("request_location")
                     else {"type": "message", "text": b["text"]} for b in row])
    return {"type": "inline_keyboard", "payload": {"buttons": rows}} if rows else None


BOLD_LINES = re.compile(r"^(Заказ №\d+|.+ — [\d  ]+ ₽|💳 К оплате: .+|Здравствуйте!.*|Оплата получена ✅.*)$")


def pretty(text: str) -> str:
    """Текст для MAX с оформлением: что вписал клиент — экранируется (чужой текст не должен стать разметкой),
    заголовки заказа, цены и «К оплате» — жирным."""
    lines = []
    for line in html.escape(text, quote=False).split("\n"):
        lines.append(f"<b>{line}</b>" if BOLD_LINES.match(line) else line)
    return "\n".join(lines)


def send(token: str, user_id: str, text: str, markup: dict | None = None, image: dict | None = None) -> str:
    """Сообщение клиенту. Возвращает номер фото (token) из ответа — чтобы витрину не загружать каждый раз."""
    attachments = []
    if image:
        attachments.append({"type": "image", "payload": image})
    buttons = keyboard(markup, max_requisites())
    if buttons:
        attachments.append(buttons)
    body = {"text": pretty(text[:MAX_TEXT]), "format": "html"}
    if attachments:
        body["attachments"] = attachments
    for attempt in range(NOT_READY_RETRIES + 1):
        try:
            result = _call(token, "POST", "messages", {"user_id": user_id}, body)
            break
        except MaxError as e:
            if e.code != "attachment.not.ready" or attempt == NOT_READY_RETRIES:
                raise
            time.sleep(2 ** attempt)
    for a in ((result.get("message") or {}).get("body") or {}).get("attachments") or []:
        if a.get("type") == "image" and (a.get("payload") or {}).get("token"):
            return a["payload"]["token"]
    return ""


# ----- разбор обновлений -----

def _name(user: dict) -> str:
    return " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or user.get("name") or \
        user.get("username") or ""


def vcf_phone(vcf: str) -> str:
    m = re.search(r"^TEL[^:\n]*:([+\d][\d\s()+-]{6,})", vcf or "", re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_update(update: dict, index: int = 0) -> Parsed:
    """Обновление MAX → то же, что из Telegram. photo_id здесь — адрес фото для скачивания."""
    kind = update.get("update_type")
    if kind == "bot_started":  # клиент нажал «Начать» (или открыл ссылку max.ru/бот?start=КОД)
        user = update.get("user") or {}
        payload = (update.get("payload") or "").strip()
        return Parsed(index, Incoming(f"{MAX_PREFIX}{user.get('user_id')}", "/start" + (f" {payload}" if payload else ""),
                                      _name(user), user.get("username") or ""))
    if kind == "message_callback":
        callback = update.get("callback") or {}
        user = callback.get("user") or {}
        return Parsed(index, Incoming(f"{MAX_PREFIX}{user.get('user_id')}", name=_name(user),
                                      username=user.get("username") or "", callback=callback.get("payload") or ""),
                      callback_id=callback.get("callback_id", ""))
    if kind != "message_created":
        return Parsed(index, None)
    message = update.get("message") or {}
    if (message.get("recipient") or {}).get("chat_type", "dialog") != "dialog":
        return Parsed(index, None)  # группы и каналы — не клиенты
    sender = message.get("sender") or {}
    if sender.get("is_bot"):
        return Parsed(index, None)
    body = message.get("body") or {}
    msg = Incoming(f"{MAX_PREFIX}{sender.get('user_id')}", (body.get("text") or "").strip(), _name(sender),
                   sender.get("username") or "")
    parsed = Parsed(index, msg)
    attachments = body.get("attachments") or []
    kinds = {a.get("type") for a in attachments}
    for a in attachments:
        payload = a.get("payload") or {}
        if a.get("type") == "image" and payload.get("url") and not parsed.photo_id:
            parsed.photo_id = payload["url"]
        elif a.get("type") == "contact":
            msg.contact_phone = vcf_phone(payload.get("vcf_info", "")) or \
                str((payload.get("max_info") or {}).get("phone", ""))
        elif a.get("type") == "location":
            lat, lon = a.get("latitude"), a.get("longitude")
            if lat is not None and lon is not None:
                msg.text = f"📍 {lat:.6f}, {lon:.6f} — https://yandex.ru/maps/?pt={lon:.6f},{lat:.6f}&z=17"
    if not msg.text and not parsed.photo_id and not msg.contact_phone:
        if kinds <= {"sticker"}:
            return Parsed(index, None)  # стикер или пусто — отвечать не нужно
        parsed.unsupported = True
    return parsed


class MaxBot(ClientBot):
    OFFSET_KEY = "max_marker"
    USERNAME_KEY = "max_bot_username"
    MAX_CHATS = True

    def __init__(self, db, parent=None):
        self._photo_tokens: dict[int, tuple[bytes, str]] = {}  # витрина: номер → (фото, его номер в MAX)
        super().__init__(db, parent)

    def token(self) -> str:
        return max_settings()["token"]

    def _fetch(self, token: str, marker: int | None):
        try:
            if self._known_token != token:
                me = get_me(token)
                self.identified.emit(me.get("username") or "")
                self._known_token = token
            updates, next_marker = get_updates(token, marker)
            parsed = [parse_update(u, i) for i, u in enumerate(updates)]
            for p in parsed:
                if p.callback_id:
                    try:
                        answer_callback(token, p.callback_id)
                    except MaxError:
                        pass
                if p.photo_id and p.message is not None:
                    try:
                        p.message.photo = download(p.photo_id)
                    except MaxError as e:
                        if not e.permanent:
                            raise
                        p.message.text = p.message.text or "[фото не загрузилось — попросите прислать ещё раз]"
        except MaxError as e:
            self.fetched.emit([], str(e), None)
            return
        self.fetched.emit(parsed, "", next_marker)

    def flush(self):
        """Фото витрины: в общей очереди у него номер Telegram — для MAX подставляем свой номер или сам файл."""
        token = self.token()
        if self.sending or not token:
            return
        items = []
        for outbox_id, chat, text, markup, photo, file_id, ref in self._unsent():
            kind, _, value = ref.partition(":")
            if kind == "showcase" and value.isdigit() and not photo:
                item = self.db.get_showcase_item(int(value))
                cached = self._photo_tokens.get(int(value))
                if cached and item is not None and cached[0] == item.photo:
                    photo, file_id = None, cached[1]
                else:
                    photo, file_id = (item.photo if item else None), ""
            else:
                file_id = ""  # номер фото Telegram в MAX не годится
            items.append((outbox_id, chat, text, markup, photo, file_id, ref))
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
                image = None
                if file_id:
                    image = {"token": file_id}
                elif photo:
                    image = upload_image(token, photo)
                result["file_id"] = send(token, chat[len(MAX_PREFIX):], text,
                                         json.loads(markup) if markup else None, image)
            except MaxError as e:
                result.update(error=str(e), permanent=e.permanent, wait=e.retry_after)
            results.append(result)
            if result["error"] and not result["permanent"]:
                break
        self.sent.emit(results)

    def _remember_photo(self, item_id: int, file_id: str):
        item = self.db.get_showcase_item(item_id)
        if file_id and item is not None:
            self._photo_tokens[item_id] = (item.photo, file_id)
        else:
            self._photo_tokens.pop(item_id, None)
