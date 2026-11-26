"""Сайт для телефона в Wi-Fi магазина: доставки дня и остатки — без интернета и мессенджеров.

Страницы рисуются на сервере простым HTML с крупными кнопками под палец. К базе обращается
только главный поток программы: сюда запросы передаёт web_server через очередь.
"""

import html
import secrets
from datetime import date, datetime, timedelta

from . import order_ops
from .client_bot import QR_FILE, client_settings
from .db import ORDER_STATUS, Database, User
from .delivery_dialog import money
from .stock import qty_text

SESSION_HOURS = 12
COOKIE = "aloflorist_session"

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — AloFlorist</title><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, sans-serif; background: #fdf5f8; color: #3a2a30; }}
header {{ background: #d63384; color: #fff; padding: 10px 14px; display: flex; align-items: center;
          gap: 14px; flex-wrap: wrap; position: sticky; top: 0; }}
header b {{ font-size: 18px; }}
header a {{ color: #fff; text-decoration: none; padding: 6px 10px; border-radius: 8px; }}
header a.here {{ background: rgba(255,255,255,.25); font-weight: bold; }}
header .who {{ margin-left: auto; font-size: 14px; }}
main {{ padding: 12px; max-width: 760px; margin: 0 auto; }}
.card {{ background: #fff; border: 1px solid #f1c3d3; border-radius: 12px; padding: 12px 14px;
         margin-bottom: 10px; }}
.card.done {{ opacity: .55; }}
.time {{ font-weight: bold; color: #d63384; }}
.items {{ font-size: 17px; margin: 4px 0; }}
.addr {{ color: #6b5560; font-size: 14px; white-space: pre-line; }}
.row {{ display: flex; gap: 8px; margin-top: 8px; }}
button, .btn {{ font-size: 16px; padding: 10px 14px; border-radius: 10px; border: 1px solid #d63384;
       background: #fff; color: #d63384; cursor: pointer; flex: 1; }}
button.on {{ background: #2e9e5b; border-color: #2e9e5b; color: #fff; }}
button.del {{ background: #d63384; color: #fff; }}
input {{ font-size: 16px; padding: 10px 12px; border: 1px solid #e3b7c8; border-radius: 10px;
        width: 100%; margin-bottom: 10px; }}
.login {{ max-width: 340px; margin: 15vh auto 0; text-align: center; }}
.error {{ color: #c0392b; margin-bottom: 8px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; }}
td, th {{ padding: 8px 10px; border-bottom: 1px solid #f5dde6; text-align: left; font-size: 15px; }}
th {{ color: #d63384; }}
tr.low td {{ color: #c0392b; font-weight: bold; }}
.cat {{ background: #fbe9f1; font-weight: bold; }}
.gray {{ color: #6b5560; font-size: 13px; }}
a.card {{ display: block; text-decoration: none; color: inherit; }}
select {{ font-size: 16px; padding: 10px 12px; border: 1px solid #e3b7c8; border-radius: 10px;
         width: 100%; margin-bottom: 10px; background: #fff; }}
a.btn {{ display: block; text-align: center; text-decoration: none; }}
a.btn.del, button.del {{ background: #d63384; color: #fff; }}
.here2 {{ background: #d63384; color: #fff; }}
.st-new {{ color: #e67e22; font-weight: bold; }}
.st-priced {{ color: #2980b9; font-weight: bold; }}
.st-paid_claimed {{ color: #c0392b; font-weight: bold; }}
.st-confirmed {{ color: #2e9e5b; font-weight: bold; }}
.st-rejected {{ color: #6b5560; }}
h3 {{ margin: 4px 0 10px; }}
</style></head><body>{body}</body></html>"""


def _esc(text: str) -> str:
    return html.escape(str(text))


def _page(title: str, body: str) -> bytes:
    return PAGE.format(title=_esc(title), body=body).encode("utf-8")


def _redirect(to: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    return 303, {"Location": to, **(headers or {})}, b""


class WebApp:
    """Маршруты сайта. Все методы вызываются в главном потоке программы."""

    def __init__(self, db: Database, on_assembled=None, on_delivered=None, on_change=None,
                 send_to_client=None, notify_admin=None, sale_shift_id=None):
        self.db = db
        self.on_assembled = on_assembled  # колбэки главного окна: сообщить клиенту, попросить фото
        self.on_delivered = on_delivered
        self.on_change = on_change  # обновить таблицу доставок в программе
        # заказы из бота: отправка клиенту, уведомление администраторам, смена для продажи
        self.send_to_client = send_to_client or (lambda *a, **k: None)
        self.notify_admin = notify_admin
        self.sale_shift_id = sale_shift_id
        self.sessions: dict[str, tuple[User, datetime]] = {}  # токен → (сотрудник, действует до)

    # ----- сессии -----

    def _user(self, cookies: dict) -> User | None:
        token = cookies.get(COOKIE, "")
        saved = self.sessions.get(token)
        if not saved:
            return None
        user, until = saved
        if until < datetime.now():
            self.sessions.pop(token, None)
            return None
        self.sessions[token] = (user, datetime.now() + timedelta(hours=SESSION_HOURS))
        return user

    def _login(self, form: dict) -> tuple[int, dict, bytes]:
        user = self.db.authenticate(form.get("login", ""), form.get("password", ""))
        if user is None:
            return 200, {}, _page("Вход", _LOGIN_BODY.format(error='<div class="error">Неверный логин или пароль.</div>'))
        token = secrets.token_hex(16)
        self.sessions[token] = (user, datetime.now() + timedelta(hours=SESSION_HOURS))
        self.db.log_action(user, "Вход на сайт с телефона", "")
        return _redirect("/", {"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
                                             f"Max-Age={SESSION_HOURS * 3600}"})

    # ----- маршруты -----

    def handle(self, method: str, path: str, form: dict, cookies: dict) -> tuple[int, dict, bytes]:
        if path == "/login":
            if method == "POST":
                return self._login(form)
            return 200, {}, _page("Вход", _LOGIN_BODY.format(error=""))
        user = self._user(cookies)
        if user is None:
            return _redirect("/login")
        if path == "/logout":
            self.sessions.pop(cookies.get(COOKIE, ""), None)
            return _redirect("/login", {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
        if path == "/toggle" and method == "POST":
            return self._toggle(user, form)
        if path == "/orders":
            return 200, {}, _page("Заказы", self._orders_page(user))
        if path == "/order":
            return 200, {}, _page("Заказ", self._order_page(user, form))
        if path == "/order/price" and method == "POST":
            return self._order_price(user, form)
        if path == "/order/confirm":
            return self._order_confirm(user, form, method)
        if path == "/order/reject" and method == "POST":
            return self._order_reject(user, form)
        if path == "/stock":
            return 200, {}, _page("Склад", self._stock_page(user, form.get("q", "")))
        if path == "/":
            return 200, {}, _page("Доставки", self._deliveries_page(user, form.get("day", "today")))
        return 404, {}, _page("Не найдено", '<main><div class="card">Такой страницы нет. '
                                           '<a href="/">На главную</a></div></main>')

    # ----- общие куски страниц -----

    def _header(self, user: User, here: str) -> str:
        def tab(href, name, key):
            cls = ' class="here"' if key == here else ""
            return f'<a href="{href}"{cls}>{name}</a>'

        open_orders = len(self.db.client_orders(open_only=True))
        orders_name = f"Заказы ({open_orders})" if open_orders else "Заказы"
        return (f'<header><b>🌸 AloFlorist</b>{tab("/", "Доставки", "deliveries")}'
                f'{tab("/orders", orders_name, "orders")}{tab("/stock", "Склад", "stock")}'
                f'<span class="who">{_esc(user.name or user.login)} · <a href="/logout">выйти</a></span></header>')

    def _error_page(self, user: User, text: str, back: str) -> tuple[int, dict, bytes]:
        body = (f'{self._header(user, "")}<main><div class="card">{_esc(text)}</div>'
                f'<div class="row"><a class="btn" href="{back}">← Назад</a></div></main>')
        return 200, {}, _page("Ошибка", body)

    # ----- доставки -----

    def _toggle(self, user: User, form: dict) -> tuple[int, dict, bytes]:
        back = _redirect("/?day=" + ("tomorrow" if form.get("day") == "tomorrow" else "today"))
        try:
            d = self.db.get_delivery(int(form.get("id", "0")))
        except (TypeError, ValueError):
            d = None
        if d is None:
            return back
        now = datetime.now()
        what = form.get("what")
        if what == "assembled" and not d.delivered:
            new = not d.assembled
            self.db.set_assembled(d.id, new, user.id, now)
            self.db.log_action(user, "Букет собран (сайт)" if new else "Отметка «Собрано» снята (сайт)",
                               f"№{d.id} {d.items[:60]}")
            if new and self.on_assembled:
                self.on_assembled(d.id)
        elif what == "delivered":
            new = not d.delivered
            self.db.set_delivered(d.id, new, user.id, now)
            self.db.log_action(user, "Доставлено (сайт)" if new else "Отметка «Доставлено» снята (сайт)",
                               f"№{d.id} {d.items[:60]}")
            if new and self.on_delivered:
                self.on_delivered(d.id)
        if self.on_change:
            self.on_change()
        return back

    def _deliveries_page(self, user: User, which: str) -> str:
        day = date.today() + (timedelta(days=1) if which == "tomorrow" else timedelta())
        items = [d for d in self.db.for_day(day) if d.delete_requested_at is None]
        cards = []
        for d in items:
            done = " done" if d.delivered else ""
            assembled = ('<button class="on">💐 Собран ✓</button>' if d.assembled
                         else "<button>💐 Собрать</button>")
            delivered = ('<button class="on">✓ Доставлен ✓</button>' if d.delivered
                         else '<button class="del">✓ Доставлен</button>')
            assembled_form = ("" if d.delivered else f"""<form method="post" action="/toggle" class="row">
<input type="hidden" name="id" value="{d.id}"><input type="hidden" name="what" value="assembled">
<input type="hidden" name="day" value="{which}">{assembled}</form>""")
            delivered_form = f"""<form method="post" action="/toggle" class="row">
<input type="hidden" name="id" value="{d.id}"><input type="hidden" name="what" value="delivered">
<input type="hidden" name="day" value="{which}">{delivered}</form>"""
            where = "Самовывоз" if d.address == "Самовывоз" else d.address
            cards.append(f"""<div class="card{done}">
<div class="time">{_esc(d.time or "без времени")}</div>
<div class="items">{_esc(d.items)}</div>
<div class="addr">{_esc(d.recipient)}{" · " if d.recipient else ""}{_esc(where)}</div>
{assembled_form}{delivered_form}</div>""")
        if not cards:
            cards.append('<div class="card">Доставок нет 🌷</div>')
        title = "Завтра" if which == "tomorrow" else "Сегодня"
        tabs = (f'<div class="row" style="margin-bottom:10px">'
                f'<a class="btn{" here2" if which != "tomorrow" else ""}" href="/?day=today">Сегодня</a>'
                f'<a class="btn{" here2" if which == "tomorrow" else ""}" href="/?day=tomorrow">Завтра</a></div>')
        return (self._header(user, "deliveries") + f"<main>{tabs}"
                f"<div class='gray' style='margin-bottom:8px'>{title}, {day:%d.%m.%Y} — "
                f"доставок: {len(items)}</div>{''.join(cards)}</main>")

    # ----- заказы из бота -----

    def _orders_page(self, user: User) -> str:
        orders = self.db.client_orders(open_only=True)
        cards = []
        for o in orders:
            status = ORDER_STATUS.get(o.status, o.status)
            when = "сегодня" if o.day == date.today() else f"{o.day:%d.%m}"
            cards.append(f'''<a class="card order" href="/order?id={o.id}">
<div><b>№{o.id}</b> · <span class="st-{_esc(o.status)}">{_esc(status)}</span></div>
<div class="items">{_esc(o.items.splitlines()[0] if o.items else "—")}</div>
<div class="addr">{when} {o.time} · {_esc(o.client_name or o.client_phone or "клиент")}</div></a>''')
        if not cards:
            cards.append('<div class="card">Открытых заказов нет 🌷</div>')
        return (self._header(user, "orders") + f"<main>{''.join(cards)}"
                '<div class="gray">Заказы из бота для клиентов. Новый заказ ждёт суммы, '
                '«оплата на проверке» — подтверждения.</div></main>')

    def _order_page(self, user: User, form: dict) -> str:
        try:
            o = self.db.get_client_order(int(form.get("id", "0")))
        except (TypeError, ValueError):
            o = None
        if o is None:
            return self._error_page(user, "Такого заказа нет.", "/orders")[2].decode()
        status = ORDER_STATUS.get(o.status, o.status)
        rows = [("Статус", f'<span class="st-{_esc(o.status)}">{_esc(status)}</span>'),
                ("Когда", f"{o.day:%d.%m.%Y} {o.time}"), ("Что", _esc(o.items)),
                ("Куда", _esc(o.address)), ("Кому", _esc(o.recipient)),
                ("Телефон", _esc(o.client_phone or "—"))]
        if o.card:
            rows.append(("Открытка", _esc(o.card)))
        if o.bonus_spent:
            rows.append(("Оплачено баллами", f"−{o.bonus_spent} ₽"))
        if o.amount:
            rows.append(("К оплате", money(o.amount) + (f" (из них доставка {money(o.delivery_fee)})"
                                                         if o.delivery_fee else "")))
        if o.has_paid_photo:
            rows.append(("Оплата", "клиент прислал скриншот перевода — проверьте в банке"))
        if o.note:
            rows.append(("Причина отказа", _esc(o.note)))
        body_rows = "".join(f'<tr><td class="gray" style="padding-right:12px" valign="top">{k}</td>'
                            f"<td>{v}</td></tr>" for k, v in rows)
        actions = []
        open_ = o.status in ("new", "priced", "paid_claimed")
        if open_:
            amount = o.amount
            if not amount and o.showcase_id:  # букет с витрины — подставить его цену
                item = self.db.get_showcase_item(o.showcase_id)
                amount = item.price if item else 0
            method_row = ""
            if client_settings()["requisites"].strip() and self.db.get_file(QR_FILE):
                method_row = ('<select name="method"><option value="both">QR или перевод</option>'
                              '<option value="qr">Только QR-код</option>'
                              '<option value="transfer">Только перевод</option></select>')
            actions.append(f'''<div class="card"><b>💳 Сумма к оплате</b>
<form method="post" action="/order/price">
<input type="hidden" name="id" value="{o.id}">
<input name="amount" type="number" inputmode="numeric" min="1" value="{amount or ""}" placeholder="Сумма, ₽">
<input name="fee" type="number" inputmode="numeric" min="0" value="{o.delivery_fee or ""}"
 placeholder="Из них доставка, ₽">
{method_row}
<button class="del">Отправить сумму клиенту</button></form></div>''')
        if o.status in ("priced", "paid_claimed"):
            actions.append(f'''<div class="card"><b>✅ Оплата пришла?</b>
<div class="gray">Проверьте поступление в банке — заказ попадёт в доставки, клиенту придёт подтверждение.</div>
<div class="row"><a class="btn del" href="/order/confirm?id={o.id}">Оплата пришла — принять заказ</a></div></div>''')
        if open_:
            actions.append(f'''<div class="card"><b>🚫 Отклонить</b>
<form method="post" action="/order/reject">
<input type="hidden" name="id" value="{o.id}">
<input name="reason" placeholder="Причина — её увидит клиент (можно пусто)">
<button>Отклонить заказ</button></form></div>''')
        if o.status == "confirmed":
            actions.append('<div class="card">Заказ в доставках — отметки «собрано/доставлено» '
                           'на вкладке «Доставки».</div>')
        return (self._header(user, "orders") + f'<main><h3>Заказ №{o.id}</h3>'
                f'<div class="card"><table>{body_rows}</table></div>{"".join(actions)}'
                f'<div class="row"><a class="btn" href="/orders">← Все заказы</a></div></main>')

    def _order_price(self, user: User, form: dict) -> tuple[int, dict, bytes]:
        try:
            o, can_bonus = order_ops.send_price(
                self.db, int(form.get("id", "0") or 0), int(form.get("amount", "0") or 0),
                int(form.get("fee", "0") or 0), datetime.now(), self.send_to_client,
                method=form.get("method", "both"))
        except (UserError, ValueError) as e:
            return self._error_page(user, str(e), "/orders")
        self.db.log_action(user, "Клиенту отправлена сумма (сайт)",
                           f"заказ №{o.id}: {money(o.amount)}"
                           + (f", можно списать {can_bonus} баллов" if can_bonus else ""))
        return _redirect(f"/order?id={o.id}")

    def _order_confirm(self, user: User, form: dict, method: str) -> tuple[int, dict, bytes]:
        try:
            o = self.db.get_client_order(int(form.get("id", "0")))
        except (TypeError, ValueError):
            o = None
        if o is None:
            return self._error_page(user, "Такого заказа нет.", "/orders")
        if method != "POST":  # сначала экран подтверждения — защита от случайного касания
            body = (f'{self._header(user, "orders")}<main><div class="card"><h3>Принять заказ №{o.id}?</h3>'
                    f"<div>Деньги ({money(o.amount)}) точно пришли? Заказ попадёт в доставки на "
                    f"{o.day:%d.%m} {o.time}, клиенту придёт подтверждение.</div>"
                    f'<form method="post" action="/order/confirm"><input type="hidden" name="id" value="{o.id}">'
                    '<div class="row"><button class="del">✅ Да, оплата пришла</button></div></form>'
                    f'<div class="row"><a class="btn" href="/order?id={o.id}">Назад</a></div></div></main>')
            return 200, {}, _page("Принять заказ", body)
        try:
            d, points, rate, doubled = order_ops.confirm_paid(
                self.db, o.id, user, self.sale_shift_id() if self.sale_shift_id else None,
                datetime.now(), self.send_to_client, self.notify_admin)
        except UserError as e:
            return self._error_page(user, str(e), f"/order?id={o.id}")
        self.db.log_action(user, "Заказ из бота принят (сайт)",
                           f"№{o.id}, оплачено {money(o.amount)} → доставка №{d.id}"
                           + (f", +{points} баллов" if points else ""))
        return _redirect("/orders")

    def _order_reject(self, user: User, form: dict) -> tuple[int, dict, bytes]:
        try:
            oid = int(form.get("id", "0") or 0)
        except ValueError:
            oid = 0
        reason = form.get("reason", "").strip()
        try:
            order_ops.reject(self.db, oid, user.name or user.login, reason, datetime.now(),
                             self.send_to_client)
        except UserError as e:
            return self._error_page(user, str(e), "/orders")
        self.db.log_action(user, "Заказ из бота отклонён (сайт)", f"№{oid}" + (f": {reason}" if reason else ""))
        return _redirect("/orders")

    # ----- склад -----


    def _stock_page(self, user: User, query: str) -> str:
        needle = query.strip().casefold()
        products = [p for p in self.db.products()
                    if not needle or needle in p.name.casefold() or needle in p.barcode]
        total = len(products)
        products = sorted(products, key=lambda x: (x.category, x.name))[:300]  # слабому телефону — порциями
        rows, category = [], None
        for p in products:
            if p.category != category:
                category = p.category
                rows.append(f'<tr class="cat"><td colspan="3">{_esc(category)}</td></tr>')
            low = ' class="low"' if p.low else ""
            rows.append(f"<tr{low}><td>{_esc(p.name)}</td>"
                        f"<td>{qty_text(p.stock)} {_esc(p.unit)}</td><td>{p.price} ₽</td></tr>")
        if not rows:
            rows.append('<tr><td colspan="3">Ничего не найдено.</td></tr>')
        more = (f'<div class="gray" style="margin-top:8px">Показаны первые 300 из {total} — '
                "уточните поиск.</div>" if total > 300 else "")
        return (self._header(user, "stock") +
                f'<main><form method="get" action="/stock"><input name="q" value="{_esc(query)}" '
                f'placeholder="Поиск: название или штрихкод" autofocus></form>'
                f"<table><tr><th>Товар</th><th>Остаток</th><th>Цена</th></tr>{''.join(rows)}</table>"
                f'{more}<div class="gray" style="margin-top:8px">Красным — заканчивается.</div></main>')


_LOGIN_BODY = """<main><form method="post" action="/login" class="card login">
<h2>🌸 AloFlorist</h2>{error}
<input name="login" placeholder="Логин" autocomplete="username" autofocus>
<input name="password" type="password" placeholder="Пароль" autocomplete="current-password">
<button class="del" type="submit">Войти</button>
<div class="gray" style="margin-top:10px">Тот же логин и пароль, что в программе.</div>
</form></main>"""
