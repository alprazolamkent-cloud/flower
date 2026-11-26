"""Действия с заказом из бота — общие для окна «Клиенты» и сайта на телефоне.

Всё работает в главном потоке. Отправка клиенту и уведомления администраторам — через
переданные колбэки: send_to_client(chat_id, text, photo, markup) и notify_admin(text, key),
поэтому модуль не зависит ни от окна, ни от сайта. Записи в журнал делают вызывающие —
им видно, откуда действие (программа или сайт).
"""

from datetime import datetime

from .client_bot import QR_FILE, client_settings
from .client_flow import MENU, inline, payment_text
from .db import ClientOrder, Database, User, UserError
from .delivery_dialog import money
from .loyalty import norm_phone


def payment_ready(db: Database) -> bool:
    """Можно ли отправлять сумму: загружен QR-код или вписаны реквизиты."""
    return bool(client_settings()["requisites"].strip() or db.get_file(QR_FILE))


def send_price(db: Database, order_id: int, amount: int, fee: int, now: datetime, send_to_client,
               method: str = "both") -> tuple[ClientOrder, int]:
    """Назначить сумму и отправить клиенту реквизиты (+ кнопку списания баллов).
    method — «both», «qr» или «transfer». Возвращает (заказ, сколько баллов может списать)."""
    o = db.get_client_order(order_id)
    if o is None:
        raise UserError("Такого заказа нет.")
    requisites = client_settings()["requisites"].strip()
    qr = db.get_file(QR_FILE)
    if method == "qr":
        requisites = ""
    elif method == "transfer":
        qr = None
    o = db.price_client_order(o.id, amount, fee, now)
    can_bonus = db.loyalty().max_spend(norm_phone(o.client_phone), max(0, o.amount - o.delivery_fee))
    buttons = [("✅ Я оплатил(а)", f"paid:{o.id}")]
    if can_bonus:
        buttons.append((f"🎁 Списать {can_bonus} баллов", f"bonus:{o.id}:{can_bonus}"))
    send_to_client(o.chat_id, payment_text(o, requisites, bool(qr)), qr, inline(*buttons))
    return o, can_bonus


def confirm_paid(db: Database, order_id: int, user: User | None, sale_shift_id: int | None,
                 now: datetime, send_to_client, notify_admin=None) -> tuple:
    """Оплата пришла: заказ становится доставкой и продажей, клиенту — подтверждение,
    баллы начисляются. Возвращает (доставка, начисленные баллы, процент)."""
    o = db.get_client_order(order_id)
    if o is None:
        raise UserError("Такого заказа нет.")
    d = db.confirm_client_order(o.id, user.id if user else None, now, sale_shift_id)
    loyalty = db.loyalty()
    phone = norm_phone(o.client_phone)
    eligible = db.order_bonus_eligible(o)  # amount уже за вычетом списанных баллов
    rate = loyalty.rate(phone, now)
    doubled = loyalty.is_occasion(phone, o.day)
    points = loyalty.accrue(phone, eligible, now=now, source=f"Заказ №{o.id}",
                            author=user.name if user else "", doubled=doubled)
    if points:
        db.set_order_bonus(o.id, o.bonus_spent, points, rate)
        sale_id = db.get_client_order(o.id).sale_id
        if sale_id:  # баллы — и в продаже, чтобы её отмена их сняла
            db.set_sale_bonus(sale_id, points, rate)
    then = ("Напишем, когда букет будет готов — можно будет забирать." if o.address == "Самовывоз"
            else "Напишем, когда букет соберём и когда доставим.")
    bonus_line = (f"\n🎁 Начислили {points} бонусов. Баланс: {loyalty.balance(phone, now)}."
                  if points else "")
    send_to_client(o.chat_id, f"Оплата получена ✅ Заказ №{o.id} принят: {o.day:%d.%m} {o.time}.\n{then}"
                   + bonus_line, None, MENU)
    if notify_admin:
        notify_admin(f"✅ Заказ №{o.id} из бота принят — {user.name if user else 'флорист'}\n"
                     f"Оплачено {money(o.amount)}, доставка {o.day:%d.%m} {o.time}", f"client_ok:{o.id}")
    return d, points, rate, doubled


def reject(db: Database, order_id: int, user_name: str, reason: str, now: datetime, send_to_client,
           restore_single: bool = True):
    """Отклонить заказ: причина клиенту, списанные баллы назад, букет «в одном экземпляре» — на витрину."""
    o = db.get_client_order(order_id)
    if o is None:
        raise UserError("Такого заказа нет.")
    db.set_client_order_status(o.id, "rejected", now, reason, restore_single=restore_single)
    if o.bonus_spent:  # клиент списывал баллы — вернуть, заказ же не состоялся
        db.loyalty().adjust(norm_phone(o.client_phone), o.bonus_spent,
                            note=f"Возврат баллов за отклонённый заказ №{o.id}", author=user_name)
        db.set_order_bonus(o.id, 0, o.bonus_accrued, o.loyalty_rate)
    send_to_client(o.chat_id, f"К сожалению, заказ №{o.id} не можем принять"
                   + (f": {reason}" if reason else ".") + "\nНапишите нам — подберём другой вариант 🌷",
                   None, MENU)
