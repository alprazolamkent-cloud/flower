"""Окно «Клиенты»: заказы из бота (сумма, оплата, принятие), переписка с клиентами, витрина."""

import html
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from .client_bot import bot_link, client_configured, client_settings, is_max, max_settings
from .client_flow import MENU, inline, payment_text
from .db import ORDER_STATUS, ClientOrder, ShowcaseItem, UserError
from .delivery_dialog import money, money_box
from .icon import flower_icon
from .photos import PhotoViewer, pick_photo, pixmap
from .theme import C

STATUS_COLORS = {"new": "orange", "priced": "blue", "paid_claimed": "red", "confirmed": "green", "rejected": "gray"}
NEEDS_ACTION = ("new", "paid_claimed")  # ждут флориста: назначить сумму / проверить оплату
HINTS = {
    "new": "Посчитайте сумму (букет + доставка), впишите и нажмите «Отправить сумму и реквизиты» — "
           "клиент получит её вместе с реквизитами.",
    "priced": "Ждём оплату. Деньги пришли — нажмите «Оплата пришла — принять заказ» "
              "(даже если клиент не нажал «Я оплатил»).",
    "paid_claimed": "Клиент сообщил, что оплатил. Проверьте поступление в банке и нажмите "
                    "«Оплата пришла — принять заказ».",
    "confirmed": "Заказ в доставках. Отметите букет собранным и доставленным — клиенту придут сообщения сами.",
    "rejected": "",
}


def needs_action(db) -> int:
    """Сколько ждёт флориста: новые заказы, оплаты на проверке и непрочитанные сообщения."""
    return sum(o.status in NEEDS_ACTION for o in db.client_orders(open_only=True)) + db.client_unread()


def order_html(o: ClientOrder, username: str = "") -> str:
    color = getattr(C, STATUS_COLORS.get(o.status, "gray"))
    who = " ".join(x for x in (o.client_name, f"@{username}" if username else "") if x)
    rows = [("Клиент", who or "—"), ("Телефон", o.client_phone or "—"), ("Когда", f"{o.day:%d.%m.%Y} {o.time}"),
            ("Что", o.items), ("Куда", o.address), ("Кому", o.recipient)]
    if o.card:
        rows.append(("Открытка", o.card))
    if o.amount:
        rows.append(("К оплате", money(o.amount) + (f" (из них доставка {money(o.delivery_fee)})"
                                                     if o.delivery_fee else "")))
    if o.has_paid_photo:
        rows.append(("Оплата", "клиент прислал скриншот перевода"))
    if o.note:
        rows.append(("Причина отказа", o.note))
    body = "".join(f"<tr><td style='color:{C.gray}; padding-right:12px' valign='top'>{k}</td>"
                   f"<td>{html.escape(v).replace(chr(10), '<br>')}</td></tr>" for k, v in rows)
    return (f"<h3>Заказ №{o.id} — <span style='color:{color}'>{ORDER_STATUS.get(o.status, o.status)}</span></h3>"
            f"<table cellspacing='0' cellpadding='2'>{body}</table>"
            f"<p style='color:{C.gray}'>Пришёл {o.created_at:%d.%m %H:%M}"
            + (f", изменён {o.updated_at:%d.%m %H:%M}" if o.updated_at != o.created_at else "") + "</p>")


def _bold(item: QListWidgetItem):
    font = QFont(item.font())
    font.setBold(True)
    item.setFont(font)


class ClientCenter(QDialog):
    """Не модальное: заказы приходят, пока окно открыто, — оно обновляется само."""

    def __init__(self, window):
        super().__init__(window, Qt.Window)
        self.main = window
        self.db = window.db
        self.setWindowTitle("Клиенты — бот «" + client_settings()["shop"] + "»")
        self.setWindowIcon(flower_icon())
        self.resize(1000, 640)
        layout = QVBoxLayout(self)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._orders_tab(), "🛍 Заказы")
        self.tabs.addTab(self._chats_tab(), "💬 Сообщения")
        self.tabs.addTab(self._showcase_tab(), "💐 Витрина")
        layout.addWidget(self.tabs, 1)
        self._extra_chat = ""  # переписка, открытая из заказа, пока сообщений в ней нет
        self._shown_order: tuple | None = None  # (номер, когда изменён) — чтобы не сбить вписанную сумму
        self.refresh()
        self.tabs.currentChanged.connect(lambda i: self._show_chat() if i == 1 else None)

    def refresh(self, showcase: bool = True):
        if client_configured():
            self.banner.hide()
        else:
            self.banner.setText(f"<span style='color:{C.orange}'><b>Бот для клиентов не подключён.</b></span> "
                                "Витрину можно готовить уже сейчас. Подключает администратор: "
                                "Настройки → Клиенты.")
            self.banner.show()
        self._refresh_orders()
        self._refresh_chats()
        if showcase:
            self._refresh_showcase()

    def _author(self) -> str:
        user = self.main.user
        return (user.name or user.login) if user else ""

    def _to_client(self, chat_id: str, text: str, photo: bytes | None = None, markup: dict | None = None):
        """Сообщение клиенту: уходит через бота и остаётся в переписке."""
        self.db.add_client_message(chat_id, False, text, datetime.now(), photo, author=self._author())
        self.main.client_bot.queue(chat_id, text, photo, markup)

    # ----- заказы -----

    def _orders_tab(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.order_list = QListWidget()
        self.order_list.setWordWrap(True)
        self.order_list.setSpacing(3)
        self.order_list.currentItemChanged.connect(lambda *_: self._show_order())
        left_layout.addWidget(self.order_list, 1)
        self.closed_check = QCheckBox("Показать принятые и отклонённые")
        self.closed_check.toggled.connect(lambda _: self._refresh_orders())
        left_layout.addWidget(self.closed_check)
        splitter.addWidget(left)

        right = QWidget()
        panel = QVBoxLayout(right)
        self.order_info = QLabel()
        self.order_info.setWordWrap(True)
        self.order_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.order_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        panel.addWidget(self.order_info)
        self.price_frame = QFrame()
        form = QFormLayout(self.price_frame)
        form.setContentsMargins(0, 0, 0, 0)
        self.amount_box = money_box()
        self.amount_box.setMaximumWidth(200)
        form.addRow("Сумма к оплате (всё вместе):", self.amount_box)
        self.fee_box = money_box()
        self.fee_box.setMaximumWidth(200)
        form.addRow("Из неё за доставку:", self.fee_box)
        fee_hint = QLabel("Доставка попадёт в «Взяли» у доставки — как обычно.")
        fee_hint.setStyleSheet(f"color: {C.gray}")
        form.addRow(fee_hint)
        panel.addWidget(self.price_frame)
        self.order_hint = QLabel()
        self.order_hint.setWordWrap(True)
        panel.addWidget(self.order_hint)
        buttons = QHBoxLayout()
        self.price_btn = QPushButton("💳 Отправить сумму и реквизиты")
        self.price_btn.clicked.connect(self._send_price)
        self.confirm_btn = QPushButton("✅ Оплата пришла — принять заказ")
        self.confirm_btn.clicked.connect(self._confirm)
        for b in (self.price_btn, self.confirm_btn):
            b.setMinimumHeight(34)
            buttons.addWidget(b)
        panel.addLayout(buttons)
        more = QHBoxLayout()
        self.shot_btn = QPushButton("🧾 Скриншот оплаты")
        self.shot_btn.clicked.connect(self._show_paid_photo)
        self.chat_btn = QPushButton("💬 Переписка")
        self.chat_btn.clicked.connect(self._open_order_chat)
        self.day_btn = QPushButton("📅 Показать в доставках")
        self.day_btn.clicked.connect(self._show_in_calendar)
        self.reject_btn = QPushButton("❌ Отклонить…")
        self.reject_btn.clicked.connect(self._reject)
        for b in (self.shot_btn, self.chat_btn, self.day_btn, self.reject_btn):
            more.addWidget(b)
        more.addStretch(1)
        panel.addLayout(more)
        panel.addStretch(1)
        splitter.addWidget(right)
        splitter.setSizes([340, 640])
        row.addWidget(splitter)
        return page

    def _order(self) -> ClientOrder | None:
        item = self.order_list.currentItem()
        return self.db.get_client_order(item.data(Qt.UserRole)) if item else None

    def _refresh_orders(self, select: int | None = None):
        current = select or (self.order_list.currentItem().data(Qt.UserRole) if self.order_list.currentItem() else None)
        self.order_list.blockSignals(True)
        self.order_list.clear()
        orders = self.db.client_orders(open_only=not self.closed_check.isChecked())
        orders.sort(key=lambda o: (o.status not in NEEDS_ACTION, o.status in ("confirmed", "rejected"), -o.id))
        for o in orders:
            first = (o.items.splitlines() or [""])[0]
            item = QListWidgetItem(f"№{o.id} · {o.day:%d.%m} {o.time}\n{first[:40]}\n{ORDER_STATUS[o.status]}")
            item.setData(Qt.UserRole, o.id)
            if o.status in NEEDS_ACTION:
                _bold(item)
                item.setForeground(self._color(o.status))
            elif o.status in ("confirmed", "rejected"):
                item.setForeground(self._color("gray"))
            self.order_list.addItem(item)
            if o.id == current:
                self.order_list.setCurrentItem(item)
        if self.order_list.currentItem() is None and self.order_list.count():
            self.order_list.setCurrentRow(0)
        self.order_list.blockSignals(False)
        self._show_order()

    @staticmethod
    def _color(status: str) -> QColor:
        return QColor(getattr(C, STATUS_COLORS.get(status, status)))

    def _show_order(self):
        o = self._order()
        has = o is not None
        for w in (self.price_frame, self.price_btn, self.confirm_btn, self.shot_btn, self.chat_btn, self.day_btn,
                  self.reject_btn):
            w.setVisible(has)
        if not has:
            self.order_info.setText(
                "<h3>Заказов, ждущих ответа, нет</h3>"
                f"<span style='color:{C.gray}'>Заказы приходят из бота «{html.escape(client_settings()['shop'])}»: "
                "клиент выбирает букет на витрине или описывает свой, пишет дату, адрес и получателя. "
                "Флорист назначает сумму, клиент оплачивает переводом, флорист подтверждает — "
                "и заказ попадает в доставки.</span>")
            self.order_hint.setText("")
            return
        chat = self.db.client_chat(o.chat_id)
        self.order_info.setText(order_html(o, chat.username if chat else ""))
        open_ = o.status in ("new", "priced", "paid_claimed")
        self.price_frame.setVisible(open_)
        self.price_btn.setVisible(open_)
        self.confirm_btn.setVisible(o.status in ("priced", "paid_claimed"))
        self.reject_btn.setVisible(open_)
        self.shot_btn.setVisible(o.has_paid_photo)
        self.day_btn.setVisible(o.status == "confirmed" and o.delivery_id is not None)
        self.price_btn.setText("💳 Отправить новую сумму" if o.status != "new" else "💳 Отправить сумму и реквизиты")
        amount = o.amount
        if not amount and o.showcase_id:  # букет с витрины — подставить его цену, флорист добавит доставку
            item = self.db.get_showcase_item(o.showcase_id)
            amount = item.price if item else 0
        if self._shown_order != (o.id, o.updated_at):  # тот же заказ без изменений — не сбивать вписанное
            self._shown_order = (o.id, o.updated_at)
            self.amount_box.setValue(amount)
            self.fee_box.setValue(o.delivery_fee)
        hint = HINTS.get(o.status, "")
        self.order_hint.setText(f"<span style='color:{C.gray}'>{hint}</span>" if hint else "")

    def _send_price(self):
        o = self._order()
        if o is None:
            return
        requisites = client_settings()["requisites"].strip()
        if not requisites:
            QMessageBox.warning(self, "Реквизиты", "Не вписаны реквизиты для оплаты.\n"
                                "Администратор вписывает их: Настройки → Клиенты.")
            return
        if o.status == "paid_claimed" and QMessageBox.question(
                self, "Новая сумма", "Клиент уже сообщил, что оплатил. Всё равно отправить новую сумму?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            o = self.db.price_client_order(o.id, self.amount_box.value(), self.fee_box.value(), datetime.now())
        except UserError as e:
            QMessageBox.warning(self, "Сумма", str(e))
            return
        self._to_client(o.chat_id, payment_text(o, requisites), markup=inline(("✅ Я оплатил(а)", f"paid:{o.id}")))
        self.main.audit("Клиенту отправлена сумма", f"заказ из бота №{o.id}: {money(o.amount)}"
                        + (f" (доставка {money(o.delivery_fee)})" if o.delivery_fee else ""))
        self._changed(o.id)

    def _confirm(self):
        o = self._order()
        if o is None:
            return
        if QMessageBox.question(
                self, "Принять заказ",
                f"Деньги ({money(o.amount)}) точно пришли?\n\nЗаказ №{o.id} попадёт в доставки на "
                f"{o.day:%d.%m} {o.time}, клиенту придёт подтверждение.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        now = datetime.now()
        try:
            d = self.db.confirm_client_order(o.id, self.main.user.id if self.main.user else None, now)
        except UserError as e:
            QMessageBox.warning(self, "Принять заказ", str(e))
            return
        then = ("Напишем, когда букет будет готов — можно будет забирать." if o.address == "Самовывоз"
                else "Напишем, когда букет соберём и когда доставим.")
        self._to_client(o.chat_id, f"Оплата получена ✅ Заказ №{o.id} принят: {o.day:%d.%m} {o.time}.\n{then}",
                        markup=MENU)
        self.main.telegram.queue(f"✅ Заказ №{o.id} из бота принят — {self._author() or 'флорист'}\n"
                                 f"Оплачено {money(o.amount)}, доставка {o.day:%d.%m} {o.time}",
                                 key=f"client_ok:{o.id}")
        self.main.audit("Заказ из бота принят", f"№{o.id}, оплачено {money(o.amount)} → доставка №{d.id}")
        self._changed(o.id)
        self.main.refresh_all()

    def _reject(self):
        o = self._order()
        if o is None:
            return
        reason, ok = QInputDialog.getText(self, "Отклонить заказ",
                                          "Причина — её увидит клиент (можно оставить пустой):")
        if not ok:
            return
        reason = reason.strip()
        self.db.set_client_order_status(o.id, "rejected", datetime.now(), reason)
        self._to_client(o.chat_id, f"К сожалению, заказ №{o.id} не можем принять" + (f": {reason}" if reason else ".")
                        + "\nНапишите нам — подберём другой вариант 🌷", markup=MENU)
        self.main.audit("Заказ из бота отклонён", f"№{o.id}" + (f": {reason}" if reason else ""))
        if o.status == "paid_claimed":
            QMessageBox.information(self, "Отклонён", "Клиент сообщал, что оплатил. Если деньги пришли — "
                                    "не забудьте вернуть.")
        self._changed(o.id)

    def _show_paid_photo(self):
        o = self._order()
        data = self.db.paid_photo(o.id) if o else None
        if data:
            PhotoViewer(self, data, f"Оплата заказа №{o.id}").exec()

    def _open_order_chat(self):
        o = self._order()
        if o is not None:
            self.open_chat(o.chat_id)

    def _show_in_calendar(self):
        o = self._order()
        d = self.db.get_delivery(o.delivery_id) if o and o.delivery_id else None
        if d is None:
            QMessageBox.information(self, "Доставка", "Доставку по этому заказу удалили.")
            return
        self.main.show_delivery(d)

    def _changed(self, order_id: int | None = None):
        self._refresh_orders(order_id)
        self._refresh_chats()
        self.main.update_clients_button()

    # ----- переписка -----

    def _chats_tab(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        splitter = QSplitter()
        self.chat_list = QListWidget()
        self.chat_list.setWordWrap(True)
        self.chat_list.setSpacing(3)
        self.chat_list.currentItemChanged.connect(lambda *_: self._show_chat())
        splitter.addWidget(self.chat_list)
        right = QWidget()
        panel = QVBoxLayout(right)
        panel.setContentsMargins(0, 0, 0, 0)
        self.chat_title = QLabel()
        panel.addWidget(self.chat_title)
        self.history = QTextBrowser()
        self.history.setOpenLinks(False)
        self.history.anchorClicked.connect(self._open_link)
        panel.addWidget(self.history, 1)
        self.reply_edit = QPlainTextEdit()
        self.reply_edit.setPlaceholderText("Ответ клиенту… (Ctrl+Enter — отправить)")
        self.reply_edit.setFixedHeight(70)
        panel.addWidget(self.reply_edit)
        send_row = QHBoxLayout()
        self.photo_btn = QPushButton("📷 Фото…")
        self.photo_btn.setToolTip("Отправить клиенту фото (например, какие цветы сейчас есть)")
        self.photo_btn.clicked.connect(self._send_photo)
        self.send_btn = QPushButton("Отправить")
        self.send_btn.setMinimumHeight(32)
        self.send_btn.clicked.connect(self._send_reply)
        send_row.addWidget(self.photo_btn)
        send_row.addStretch(1)
        send_row.addWidget(self.send_btn)
        panel.addLayout(send_row)
        QShortcut(QKeySequence("Ctrl+Return"), self.reply_edit, activated=self._send_reply)
        QShortcut(QKeySequence("Ctrl+Enter"), self.reply_edit, activated=self._send_reply)
        splitter.addWidget(right)
        splitter.setSizes([300, 680])
        row.addWidget(splitter)
        return page

    def _chat_id(self) -> str:
        item = self.chat_list.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def _chat_name(self, chat_id: str) -> str:
        chat = self.db.client_chat(chat_id)
        where = " · MAX" if is_max(chat_id) else ""
        if chat is None:
            return chat_id + where
        return (" ".join(x for x in (chat.name, f"@{chat.username}" if chat.username else "") if x)
                or chat_id) + where

    @staticmethod
    def _chat_line(title: str, preview: str) -> str:
        return f"{title}\n{preview}" if preview else title

    def _refresh_chats(self, select: str = ""):
        current = select or self._chat_id()
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        rows = self.db.client_chats()
        if self._extra_chat and all(chat.chat_id != self._extra_chat for chat, _, _ in rows):
            rows.insert(0, (self.db.client_chat(self._extra_chat), 0, None))
        for chat, unread, last in rows:
            if chat is None:
                continue
            preview = ""
            if last is not None:
                preview = ("Вы: " if not last.incoming else "") + ("📷 " if last.has_photo else "") + last.text
            preview = " ".join(preview.split())
            name, preview = self._chat_name(chat.chat_id), preview[:60]
            item = QListWidgetItem(self._chat_line(name + (f"  · новых: {unread}" if unread else ""), preview))
            item.setData(Qt.UserRole, chat.chat_id)
            item.setData(Qt.UserRole + 1, (name, preview))
            if unread:
                _bold(item)
            self.chat_list.addItem(item)
            if chat.chat_id == current:
                self.chat_list.setCurrentItem(item)
        self.chat_list.blockSignals(False)
        self._show_chat()

    def open_chat(self, chat_id: str):
        self._extra_chat = chat_id
        self.tabs.setCurrentIndex(1)
        self._refresh_chats(chat_id)

    def _show_chat(self):
        chat_id = self._chat_id()
        for w in (self.reply_edit, self.send_btn, self.photo_btn):
            w.setEnabled(bool(chat_id))
        if not chat_id:
            self.chat_title.setText("")
            self.history.setHtml(f"<p style='color:{C.gray}'>Сообщений от клиентов пока нет. Всё, что клиенты "
                                 "пишут боту вне заказа, появится здесь.</p>")
            return
        looking = self.tabs.currentIndex() == 1 and self.isVisible()  # прочитано — только если видно
        had_unread = looking and any(m.incoming and not m.read for m in self.db.client_messages(chat_id, limit=20))
        if looking:
            self.db.mark_client_read(chat_id)
        chat = self.db.client_chat(chat_id)
        phone = f" · {chat.phone}" if chat and chat.phone else ""
        self.chat_title.setText(f"<b>{html.escape(self._chat_name(chat_id))}</b>{html.escape(phone)}")
        client = chat.name if chat and chat.name else "Клиент"
        parts = []
        for m in self.db.client_messages(chat_id):
            who = client if m.incoming else (m.author or "Флорист")
            body = html.escape(m.text).replace("\n", "<br>")
            if m.has_photo:
                body = f"<a href='photo:{m.id}'>📷 открыть фото</a>" + (f"<br>{body}" if body else "")
            color = C.green if m.incoming else C.blue
            parts.append(f"<p align='{'left' if m.incoming else 'right'}'>"
                         f"<span style='color:{color}'><b>{html.escape(who)}</b></span> "
                         f"<span style='color:{C.gray}'>{m.at:%d.%m %H:%M}</span><br>{body}</p>")
        self.history.setHtml("".join(parts) or f"<p style='color:{C.gray}'>Сообщений пока нет — "
                                                f"напишите клиенту первым.</p>")
        bar = self.history.verticalScrollBar()
        bar.setValue(bar.maximum())
        if had_unread:
            self.main.update_clients_button()
            item = self.chat_list.currentItem()  # прочитали — убрать «новых: N» и жирный шрифт
            name, preview = item.data(Qt.UserRole + 1)
            item.setText(self._chat_line(name, preview))
            item.setFont(self.chat_list.font())

    def _open_link(self, url: QUrl):
        text = url.toString()
        if text.startswith("photo:") and text[6:].isdigit():
            data = self.db.client_message_photo(int(text[6:]))
            if data:
                PhotoViewer(self, data).exec()
        elif text.startswith("http"):
            QDesktopServices.openUrl(url)

    def _send_reply(self):
        chat_id, text = self._chat_id(), self.reply_edit.toPlainText().strip()
        if not chat_id or not text:
            return
        self._to_client(chat_id, text)
        self.reply_edit.clear()
        self._refresh_chats(chat_id)

    def _send_photo(self):
        chat_id = self._chat_id()
        if not chat_id:
            return
        data = pick_photo(self)
        if data is None:
            return
        self._to_client(chat_id, self.reply_edit.toPlainText().strip(), photo=data)
        self.reply_edit.clear()
        self._refresh_chats(chat_id)

    # ----- витрина -----

    def _showcase_tab(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        left = QVBoxLayout()
        self.show_list = QListWidget()
        self.show_list.setIconSize(QSize(56, 56))
        self.show_list.currentItemChanged.connect(lambda *_: self._load_item())
        left.addWidget(self.show_list, 1)
        list_buttons = QHBoxLayout()
        new_btn = QPushButton("➕ Новый букет")
        new_btn.clicked.connect(self._new_item)
        up_btn = QPushButton("▲")
        up_btn.setToolTip("Выше на витрине")
        up_btn.clicked.connect(lambda: self._move_item(-1))
        down_btn = QPushButton("▼")
        down_btn.setToolTip("Ниже на витрине")
        down_btn.clicked.connect(lambda: self._move_item(1))
        list_buttons.addWidget(new_btn, 1)
        list_buttons.addWidget(up_btn)
        list_buttons.addWidget(down_btn)
        left.addLayout(list_buttons)
        row.addLayout(left, 2)

        right = QVBoxLayout()
        photo_row = QHBoxLayout()
        self.photo_label = QLabel()
        self.photo_label.setFixedSize(220, 220)
        self.photo_label.setAlignment(Qt.AlignCenter)
        self.photo_label.setStyleSheet(f"border: 1px dashed {C.card_border}; color: {C.gray};")
        photo_row.addWidget(self.photo_label)
        photo_buttons = QVBoxLayout()
        pick_btn = QPushButton("🖼 Выбрать фото…")
        pick_btn.clicked.connect(self._pick_item_photo)
        clear_btn = QPushButton("Убрать фото")
        clear_btn.clicked.connect(lambda: self._set_item_photo(None))
        photo_buttons.addWidget(pick_btn)
        photo_buttons.addWidget(clear_btn)
        photo_buttons.addStretch(1)
        photo_row.addLayout(photo_buttons)
        photo_row.addStretch(1)
        right.addLayout(photo_row)
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("например «Нежность»")
        form.addRow("Название:", self.title_edit)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("состав и размер: 15 пионов, эвкалипт, высота 50 см")
        self.desc_edit.setFixedHeight(70)
        form.addRow("Описание:", self.desc_edit)
        self.item_price = money_box()
        self.item_price.setMaximumWidth(200)
        form.addRow("Цена:", self.item_price)
        self.active_check = QCheckBox("Есть в наличии — показывать клиентам")
        form.addRow(self.active_check)
        right.addLayout(form)
        item_buttons = QHBoxLayout()
        self.save_item_btn = QPushButton("💾 Сохранить")
        self.save_item_btn.setMinimumHeight(32)
        self.save_item_btn.clicked.connect(self._save_item)
        self.delete_item_btn = QPushButton("🗑 Удалить")
        self.delete_item_btn.clicked.connect(self._delete_item)
        item_buttons.addWidget(self.save_item_btn)
        item_buttons.addWidget(self.delete_item_btn)
        item_buttons.addStretch(1)
        right.addLayout(item_buttons)
        hint = QLabel("Клиент видит витрину по кнопке «💐 Витрина» — первые 15 букетов в наличии, по порядку "
                      "списка. Закончились цветы — снимите галочку «Есть в наличии», букет спрячется.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C.gray}")
        right.addWidget(hint)
        right.addStretch(1)
        row.addLayout(right, 3)
        self._item = ShowcaseItem(0, "")
        self._photo: bytes | None = None
        return page

    def _refresh_showcase(self, select: int | None = None):
        current = select if select is not None else self._item.id
        self.show_list.blockSignals(True)
        self.show_list.clear()
        for x in self.db.showcase():
            text = f"{x.title} — {money(x.price)}" + ("" if x.active else "\nнет в наличии (скрыт)")
            item = QListWidgetItem(QIcon(pixmap(x.photo, 112)) if x.photo else QIcon(), text)
            item.setData(Qt.UserRole, x.id)
            if not x.active:
                item.setForeground(self._color("gray"))
            self.show_list.addItem(item)
            if x.id == current:
                self.show_list.setCurrentItem(item)
        self.show_list.blockSignals(False)
        if self.show_list.currentItem() is None:
            if current and self.show_list.count():
                self.show_list.setCurrentRow(0)
            self._load_item()

    def _load_item(self):
        item = self.show_list.currentItem()
        found = self.db.get_showcase_item(item.data(Qt.UserRole)) if item else None
        self._item = found or ShowcaseItem(0, "")
        self.title_edit.setText(self._item.title)
        self.desc_edit.setPlainText(self._item.description)
        self.item_price.setValue(self._item.price)
        self.active_check.setChecked(self._item.active)
        self._set_item_photo(self._item.photo)
        self.delete_item_btn.setEnabled(bool(self._item.id))
        self.save_item_btn.setText("💾 Сохранить" if self._item.id else "💾 Добавить на витрину")

    def _set_item_photo(self, data: bytes | None):
        self._photo = data
        pm = pixmap(data, 216)
        if pm.isNull():
            self.photo_label.setPixmap(pm)
            self.photo_label.setText("без фото\n(с фото заказывают охотнее)")
        else:
            self.photo_label.setPixmap(pm)

    def _pick_item_photo(self):
        data = pick_photo(self)
        if data is not None:
            self._set_item_photo(data)

    def _new_item(self):
        self.show_list.clearSelection()
        self.show_list.setCurrentItem(None)
        self._load_item()
        self.title_edit.setFocus()

    def _save_item(self):
        x = ShowcaseItem(self._item.id, self.title_edit.text(), self.desc_edit.toPlainText(), self.item_price.value(),
                         self._photo, self._item.file_id, self.active_check.isChecked())
        try:
            item_id = self.db.save_showcase_item(x)
        except UserError as e:
            QMessageBox.warning(self, "Витрина", str(e))
            return
        self.main.audit("Витрина изменена" if self._item.id else "Букет добавлен на витрину",
                        f"{x.title.strip()} — {money(x.price)}" + ("" if x.active else " (нет в наличии)"))
        self._item = ShowcaseItem(item_id, "")
        self._refresh_showcase(item_id)

    def _delete_item(self):
        if not self._item.id or QMessageBox.question(
                self, "Удалить с витрины", f"Удалить «{self._item.title}» с витрины?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.db.delete_showcase_item(self._item.id)
        self.main.audit("Букет удалён с витрины", self._item.title)
        self._item = ShowcaseItem(0, "")
        self._refresh_showcase(0)

    def _move_item(self, step: int):
        if self._item.id:
            self.db.move_showcase_item(self._item.id, step)
            self._refresh_showcase(self._item.id)


def max_link(username: str, start: str = "") -> str:
    return f"https://max.ru/{username}" + (f"?start={start}" if start else "") if username else ""


class StaffLinkDialog(QDialog):
    """Флорист привязывает свой Telegram или MAX к боту клиентов, чтобы присылать фото собранных букетов."""

    def __init__(self, parent, db, user):
        super().__init__(parent)
        self.db, self.user = db, user
        self.setWindowTitle("Телефон для фото букетов")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        cfg, mx = client_settings(), max_settings()
        text = QLabel()
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        text.setTextInteractionFlags(Qt.TextBrowserInteraction)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        linked = db.staff_chat(user.id)
        if not (cfg["token"] or mx["token"]):
            text.setText("Бот для клиентов ещё не подключён.<br>Администратор подключает его: "
                         "<b>Настройки → Клиенты</b>.")
        elif linked:
            where = "MAX" if is_max(linked) else "Telegram"
            text.setText(f"<h3>✅ Ваш {where} привязан</h3>Когда отметите букет собранным, бот "
                         f"«{html.escape(cfg['shop'])}» попросит фото — просто ответьте ему фотографией.<br><br>"
                         "Фото увидят администраторы, а если заказ пришёл из бота — и сам клиент.")
            unlink = buttons.addButton("Отвязать", QDialogButtonBox.DestructiveRole)
            unlink.clicked.connect(self._unlink)
        else:
            code = db.link_code(user.id)
            ways = []
            if cfg["token"]:
                name, link = cfg["username"], bot_link(cfg["username"], code)
                ways.append(f"в <b>Telegram</b> — бот <b>@{html.escape(name)}</b> (<a href='{link}'>ссылка</a>)"
                            if name else "в <b>Telegram</b> — наш бот")
            if mx["token"]:
                name, link = mx["username"], max_link(mx["username"], code)
                ways.append(f"в <b>MAX</b> — бот <b>{html.escape(name)}</b> (<a href='{link}'>ссылка</a>)"
                            if name else "в <b>MAX</b> — наш бот")
            text.setText(
                "<h3>Фото букетов с телефона</h3>"
                "Когда вы отметите букет собранным, бот попросит его фото. Фото увидят администраторы, "
                "а если заказ из бота — и клиент.<br><br>"
                "1. На телефоне откройте бота, где вам удобнее:<br>&nbsp;&nbsp;• " + "<br>&nbsp;&nbsp;• ".join(ways)
                + f"<br>2. Отправьте боту сообщение:<br><span style='font-size:20px'><b>/start {code}</b></span><br><br>"
                f"<span style='color:{C.gray}'>Бот ответит «Готово». Код одноразовый и только ваш.</span>")
            copy = buttons.addButton("Скопировать команду", QDialogButtonBox.ActionRole)
            copy.clicked.connect(lambda: QApplication.clipboard().setText(f"/start {code}"))
        layout.addWidget(buttons)

    def _unlink(self):
        self.db.unlink_staff(self.user.id)
        self.db.log_action(self.user, "Телефон для фото отвязан", "")
        self.accept()
