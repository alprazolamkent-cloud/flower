"""Склад и продажи: товары, остатки, поставки, техкарты букетов, продажи, списания.

Остаток товара — сумма движений (приход +, расход −). Себестоимость — средняя закупочная цена:
при каждой поставке пересчитывается с учётом того, что уже лежит на складе.
Методы подмешиваются в Database (класс StockStore), таблицы создаёт STOCK_SCHEMA.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

STOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    category  TEXT    NOT NULL DEFAULT 'Цветы',
    unit      TEXT    NOT NULL DEFAULT 'шт',
    price     INTEGER NOT NULL DEFAULT 0,
    avg_cost  REAL    NOT NULL DEFAULT 0,
    min_stock REAL    NOT NULL DEFAULT 0,
    active    INTEGER NOT NULL DEFAULT 1,
    photo     BLOB
);
CREATE TABLE IF NOT EXISTS stock_moves (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    at         TEXT    NOT NULL,
    qty        REAL    NOT NULL,
    kind       TEXT    NOT NULL,
    cost       REAL    NOT NULL DEFAULT 0,
    ref        INTEGER,
    reason     TEXT    NOT NULL DEFAULT '',
    shift_id   INTEGER,
    user_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_stock_moves_product ON stock_moves(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_moves_shift ON stock_moves(shift_id, kind);
CREATE TABLE IF NOT EXISTS supplies (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT    NOT NULL,
    supplier TEXT    NOT NULL DEFAULT '',
    note     TEXT    NOT NULL DEFAULT '',
    total    INTEGER NOT NULL DEFAULT 0,
    user_id  INTEGER
);
CREATE TABLE IF NOT EXISTS recipes (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    title  TEXT    NOT NULL,
    price  INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    photo  BLOB,
    ready_cost REAL NOT NULL DEFAULT 0,
    in_bot INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ready_moves (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id  INTEGER NOT NULL,
    at         TEXT    NOT NULL,
    qty        REAL    NOT NULL,
    kind       TEXT    NOT NULL,
    cost       REAL    NOT NULL DEFAULT 0,
    ref        INTEGER,
    reason     TEXT    NOT NULL DEFAULT '',
    shift_id   INTEGER,
    user_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ready_moves_recipe ON ready_moves(recipe_id);
CREATE TABLE IF NOT EXISTS recipe_items (
    recipe_id  INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    qty        REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS sales (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT    NOT NULL,
    shift_id      INTEGER,
    user_id       INTEGER,
    total         INTEGER NOT NULL,
    discount      INTEGER NOT NULL DEFAULT 0,  -- меньше нуля — наценка
    adjust_note   TEXT    NOT NULL DEFAULT '',  -- почему скидка / наценка
    flowers       INTEGER NOT NULL DEFAULT 0,
    cost          REAL    NOT NULL DEFAULT 0,
    payment       TEXT    NOT NULL,
    note          TEXT    NOT NULL DEFAULT '',
    cancelled_at  TEXT,
    cancel_reason TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sales_at ON sales(at);
CREATE INDEX IF NOT EXISTS idx_sales_shift ON sales(shift_id);
CREATE TABLE IF NOT EXISTS sale_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id    INTEGER NOT NULL,
    recipe_id  INTEGER,
    product_id INTEGER,
    title      TEXT    NOT NULL,
    qty        REAL    NOT NULL,
    price      INTEGER NOT NULL,
    cost       REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sale_lines_sale ON sale_lines(sale_id);
"""

STOCK_MIGRATIONS = {  # (таблица, столбец) → как добавить в базу прошлой версии
    ("products", "photo"): "ALTER TABLE products ADD COLUMN photo BLOB",
    ("recipes", "photo"): "ALTER TABLE recipes ADD COLUMN photo BLOB",
    ("sales", "adjust_note"): "ALTER TABLE sales ADD COLUMN adjust_note TEXT NOT NULL DEFAULT ''",
    ("recipes", "ready_cost"): "ALTER TABLE recipes ADD COLUMN ready_cost REAL NOT NULL DEFAULT 0",
    ("recipes", "in_bot"): "ALTER TABLE recipes ADD COLUMN in_bot INTEGER NOT NULL DEFAULT 1",
}
OPEN_ORDER = "('new', 'priced', 'paid_claimed')"  # заказ из бота ещё не принят — букет забронирован

CATEGORIES = ("Цветы", "Зелень", "Упаковка", "Горшечные", "Сопутствующие")
FLOWER_CATEGORIES = ("Цветы", "Зелень")  # что считается «цветами» в выручке смены
PAYMENTS = {"cash": "Наличные", "card": "Терминал", "qr": "QR / перевод"}


# списания и товаров, и готовых букетов с витрины
WRITEOFFS = ("SELECT at, qty, cost, reason, shift_id FROM stock_moves WHERE kind='writeoff'"
             " UNION ALL SELECT at, qty, cost, reason, shift_id FROM ready_moves WHERE kind='writeoff'")


class StockError(Exception):
    """Ошибка с понятным пользователю текстом (в db.py она же — UserError)."""


def _ts(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def qty_text(qty: float) -> str:
    """3.0 → «3», 2.5 → «2,5»."""
    return f"{qty:g}".replace(".", ",")


@dataclass
class Product:
    id: int
    name: str
    category: str = "Цветы"
    unit: str = "шт"
    price: int = 0
    avg_cost: float = 0
    min_stock: float = 0
    active: bool = True
    stock: float = 0  # остаток (считается из движений)
    has_photo: bool = False

    @property
    def low(self) -> bool:
        return self.stock < 0 or (self.min_stock > 0 and self.stock <= self.min_stock)


@dataclass
class Recipe:
    id: int
    title: str
    price: int = 0
    active: bool = True
    items: list[tuple[int, float]] = field(default_factory=list)  # (товар, сколько)
    has_photo: bool = False
    ready: float = 0  # собрано и стоит на витрине
    in_bot: bool = True  # собранные показывать в витрине ботов


@dataclass
class CartLine:
    """Позиция продажи: техкарта (букет), товар или свободная позиция (без склада)."""
    title: str
    qty: float
    price: int  # за единицу
    recipe_id: int | None = None
    product_id: int | None = None

    @property
    def amount(self) -> int:
        return round(self.qty * self.price)


@dataclass
class Sale:
    id: int
    at: datetime
    shift_id: int | None
    user_id: int | None
    user_name: str
    total: int
    discount: int
    flowers: int
    cost: float
    payment: str
    note: str
    cancelled_at: datetime | None
    cancel_reason: str
    lines: list[tuple[str, float, int]] = field(default_factory=list)  # (что, сколько, цена)
    adjust_note: str = ""

    @property
    def adjust_text(self) -> str:
        """«скидка 200 ₽ — постоянный клиент» / «наценка 300 ₽ — срочно» / ""."""
        if not self.discount:
            return ""
        what = "скидка" if self.discount > 0 else "наценка"
        amount = f"{abs(self.discount):,}".replace(",", " ")
        return f"{what} {amount} ₽" + (f" — {self.adjust_note}" if self.adjust_note else "")

    @property
    def what(self) -> str:
        return ", ".join(f"{t}" + (f" × {qty_text(q)}" if q != 1 else "") for t, q, _ in self.lines) or "—"


@dataclass
class Supply:
    id: int
    at: datetime
    supplier: str
    note: str
    total: int
    user_name: str
    lines: list[tuple[str, float, float]] = field(default_factory=list)  # (товар, сколько, цена за единицу)


@dataclass
class SalesReport:
    """Аналитика за период (отменённые продажи не считаются)."""
    count: int = 0
    revenue: int = 0
    cost: float = 0
    discount: int = 0
    by_day: dict[date, int] = field(default_factory=dict)
    by_user: dict[str, tuple[int, int]] = field(default_factory=dict)  # кто → (продаж, выручка)
    by_payment: dict[str, int] = field(default_factory=dict)
    top: list[tuple[str, float, int]] = field(default_factory=list)  # (что, сколько, выручка)
    markup: int = 0
    adjust_notes: dict[str, int] = field(default_factory=dict)  # «Скидка: постоянный клиент» → сумма

    @property
    def average(self) -> int:
        return round(self.revenue / self.count) if self.count else 0

    @property
    def profit(self) -> int:
        return round(self.revenue - self.cost)

    @property
    def margin(self) -> float:
        """Доля валовой прибыли в выручке, %."""
        return 100 * self.profit / self.revenue if self.revenue else 0


@dataclass
class ShiftSales:
    """Итог продаж за смену — подставляется при сдаче смены."""
    count: int = 0
    revenue: int = 0
    flowers: int = 0
    cash: int = 0
    card: int = 0
    qr: int = 0
    writeoffs: dict[str, int] = field(default_factory=dict)  # причина → на сколько списали (по себестоимости)


class StockStore:
    conn: sqlite3.Connection

    # ----- товары -----

    def products(self, active_only: bool = True) -> list[Product]:
        sql = ("SELECT p.id, p.name, p.category, p.unit, p.price, p.avg_cost, p.min_stock, p.active,"
               " COALESCE((SELECT SUM(m.qty) FROM stock_moves m WHERE m.product_id = p.id), 0),"
               " p.photo IS NOT NULL FROM products p")
        if active_only:
            sql += " WHERE p.active = 1"
        rows = self.conn.execute(sql + " ORDER BY p.category, p.name COLLATE NOCASE").fetchall()
        return [Product(r[0], r[1], r[2], r[3], r[4], r[5], r[6], bool(r[7]), round(r[8], 3), bool(r[9]))
                for r in rows]

    def get_product(self, product_id: int) -> Product | None:
        return next((p for p in self.products(active_only=False) if p.id == product_id), None)

    def save_product(self, p: Product) -> int:
        name = p.name.strip()
        if not name:
            raise StockError("Напишите название товара.")
        if p.price < 0 or p.min_stock < 0:
            raise StockError("Цена и минимальный остаток не могут быть отрицательными.")
        same = any(n.casefold() == name.casefold() for n, in self.conn.execute(  # NOCASE не знает кириллицу
            "SELECT name FROM products WHERE id <> ? AND active = 1", (p.id or 0,)).fetchall())
        if same:
            raise StockError(f"Товар «{name}» уже есть.")
        if p.id:
            self.conn.execute("UPDATE products SET name=?, category=?, unit=?, price=?, min_stock=?, active=? WHERE id=?",
                              (name, p.category, p.unit.strip() or "шт", p.price, p.min_stock, int(p.active), p.id))
        else:
            p.id = self.conn.execute(
                "INSERT INTO products (name, category, unit, price, avg_cost, min_stock, active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, p.category, p.unit.strip() or "шт", p.price, p.avg_cost, p.min_stock, int(p.active))).lastrowid
        self.conn.commit()
        return p.id

    def stock_of(self, product_id: int) -> float:
        return self.conn.execute("SELECT COALESCE(SUM(qty), 0) FROM stock_moves WHERE product_id=?",
                                 (product_id,)).fetchone()[0]

    def _move(self, product_id: int, qty: float, kind: str, cost: float, now: datetime, ref: int | None = None,
              reason: str = "", shift_id: int | None = None, user_id: int | None = None):
        self.conn.execute("INSERT INTO stock_moves (product_id, at, qty, kind, cost, ref, reason, shift_id, user_id)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (product_id, _ts(now), qty, kind, cost, ref, reason, shift_id, user_id))

    def _avg_cost(self, product_id: int) -> float:
        r = self.conn.execute("SELECT avg_cost FROM products WHERE id=?", (product_id,)).fetchone()
        return r[0] if r else 0

    def migrate_stock(self):
        for (table, column), sql in STOCK_MIGRATIONS.items():
            if column not in {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                self.conn.execute(sql)

    def photo_of(self, kind: str, ident: int) -> bytes | None:
        """Фото товара (kind="product") или букета по техкарте (kind="recipe")."""
        table = {"product": "products", "recipe": "recipes"}[kind]
        r = self.conn.execute(f"SELECT photo FROM {table} WHERE id=?", (ident,)).fetchone()
        return r[0] if r else None

    def set_photo(self, kind: str, ident: int, photo: bytes | None):
        table = {"product": "products", "recipe": "recipes"}[kind]
        self.conn.execute(f"UPDATE {table} SET photo=? WHERE id=?", (photo, ident))
        self.conn.commit()

    def popular(self, since: datetime, limit: int = 12) -> list[tuple[str, int]]:
        """Что чаще всего продают: [("recipe", id) / ("product", id)] — для «⭐ Частые» в продаже."""
        rows = self.conn.execute(
            "SELECT l.recipe_id, l.product_id FROM sale_lines l JOIN sales s ON s.id = l.sale_id"
            " WHERE s.at >= ? AND s.cancelled_at IS NULL AND (l.recipe_id IS NOT NULL OR l.product_id IS NOT NULL)"
            " GROUP BY l.recipe_id, l.product_id ORDER BY COUNT(*) DESC, SUM(l.qty) DESC LIMIT ?",
            (_ts(since), limit)).fetchall()
        return [("recipe", r[0]) if r[0] is not None else ("product", r[1]) for r in rows]

    # ----- поставки -----

    def add_supply(self, supplier: str, lines: list[tuple[int, float, float]], user_id: int | None, now: datetime,
                   note: str = "") -> int:
        """lines — (товар, сколько, цена закупки за единицу). Себестоимость товара — средняя с учётом остатка."""
        lines = [(pid, qty, cost) for pid, qty, cost in lines if qty]
        if not lines:
            raise StockError("Добавьте в поставку хотя бы один товар.")
        if any(qty < 0 or cost < 0 for _, qty, cost in lines):
            raise StockError("Количество и цена не могут быть отрицательными.")
        total = round(sum(qty * cost for _, qty, cost in lines))
        supply_id = self.conn.execute("INSERT INTO supplies (at, supplier, note, total, user_id) VALUES (?, ?, ?, ?, ?)",
                                      (_ts(now), supplier.strip(), note.strip(), total, user_id)).lastrowid
        for pid, qty, cost in lines:
            have = max(self.stock_of(pid), 0)
            old = self._avg_cost(pid)
            avg = (have * old + qty * cost) / (have + qty) if have + qty > 0 else cost
            self.conn.execute("UPDATE products SET avg_cost=? WHERE id=?", (avg, pid))
            self._move(pid, qty, "supply", cost, now, ref=supply_id, user_id=user_id)
        self.conn.commit()
        return supply_id

    def supplies(self, limit: int = 200) -> list[Supply]:
        rows = self.conn.execute(
            "SELECT s.id, s.at, s.supplier, s.note, s.total, COALESCE(NULLIF(u.name, ''), u.login, '')"
            " FROM supplies s LEFT JOIN users u ON u.id = s.user_id ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for r in rows:
            lines = self.conn.execute(
                "SELECT p.name, m.qty, m.cost FROM stock_moves m JOIN products p ON p.id = m.product_id"
                " WHERE m.kind='supply' AND m.ref=? ORDER BY m.id", (r[0],)).fetchall()
            result.append(Supply(r[0], datetime.fromisoformat(r[1]), r[2], r[3], r[4], r[5], [tuple(x) for x in lines]))
        return result

    def suppliers(self) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT supplier FROM supplies WHERE supplier <> '' GROUP BY supplier ORDER BY MAX(id) DESC").fetchall()]

    # ----- списание и инвентаризация -----

    def write_off(self, product_id: int, qty: float, reason: str, user_id: int | None, shift_id: int | None,
                  now: datetime):
        if qty <= 0:
            raise StockError("Сколько списать? Укажите количество больше нуля.")
        self._move(product_id, -qty, "writeoff", self._avg_cost(product_id), now, reason=reason or "Другое",
                   shift_id=shift_id, user_id=user_id)
        self.conn.commit()

    def count_stock(self, product_id: int, actual: float, user_id: int | None, now: datetime) -> float:
        """Инвентаризация: на складе на самом деле actual. Возвращает поправку (+ нашли, − недостача)."""
        if actual < 0:
            raise StockError("Остаток не может быть отрицательным.")
        diff = round(actual - self.stock_of(product_id), 3)
        if diff:
            self._move(product_id, diff, "count", self._avg_cost(product_id), now, reason="Инвентаризация",
                       user_id=user_id)
            self.conn.commit()
        return diff

    # ----- техкарты -----

    def recipes(self, active_only: bool = True) -> list[Recipe]:
        sql = ("SELECT r.id, r.title, r.price, r.active, r.photo IS NOT NULL, r.in_bot,"
               " COALESCE((SELECT SUM(m.qty) FROM ready_moves m WHERE m.recipe_id = r.id), 0) FROM recipes r"
               + (" WHERE r.active = 1" if active_only else ""))
        result = []
        for r in self.conn.execute(sql + " ORDER BY title COLLATE NOCASE").fetchall():
            items = self.conn.execute("SELECT product_id, qty FROM recipe_items WHERE recipe_id=? ORDER BY rowid",
                                      (r[0],)).fetchall()
            result.append(Recipe(r[0], r[1], r[2], bool(r[3]), [tuple(x) for x in items], bool(r[4]),
                                 round(r[6], 3), bool(r[5])))
        return result

    def get_recipe(self, recipe_id: int) -> Recipe | None:
        return next((r for r in self.recipes(active_only=False) if r.id == recipe_id), None)

    def save_recipe(self, r: Recipe) -> int:
        title = r.title.strip()
        if not title:
            raise StockError("Напишите название букета.")
        if r.price < 0:
            raise StockError("Цена не может быть отрицательной.")
        items = [(pid, qty) for pid, qty in r.items if qty > 0]
        if not items:
            raise StockError("Добавьте в техкарту хотя бы один цветок или упаковку.")
        if r.id:
            self.conn.execute("UPDATE recipes SET title=?, price=?, active=?, in_bot=? WHERE id=?",
                              (title, r.price, int(r.active), int(r.in_bot), r.id))
            self.conn.execute("DELETE FROM recipe_items WHERE recipe_id=?", (r.id,))
            self.conn.execute("UPDATE showcase SET price=? WHERE recipe_id=?", (r.price, r.id))  # цена и в боте
            if not r.in_bot:
                self.conn.execute("UPDATE showcase SET active=0 WHERE recipe_id=?", (r.id,))
        else:
            r.id = self.conn.execute("INSERT INTO recipes (title, price, active, in_bot) VALUES (?, ?, ?, ?)",
                                     (title, r.price, int(r.active), int(r.in_bot))).lastrowid
        self.conn.executemany("INSERT INTO recipe_items (recipe_id, product_id, qty) VALUES (?, ?, ?)",
                              [(r.id, pid, qty) for pid, qty in items])
        self.conn.commit()
        return r.id

    def archive_recipe(self, recipe_id: int):
        self.conn.execute("UPDATE recipes SET active=0 WHERE id=?", (recipe_id,))
        self.conn.execute("UPDATE showcase SET active=0 WHERE recipe_id=?", (recipe_id,))
        self.conn.commit()

    def composition(self, recipe: Recipe, flowers_only: bool = False) -> list[tuple[str, list[str]]]:
        """Состав по разделам: [(«Цветы», [«Роза пионовидная × 7», …]), («Зелень», …), …]."""
        names = {p.id: p for p in self.products(active_only=False)}
        groups: dict[str, list[str]] = {}
        for pid, qty in recipe.items:
            p = names.get(pid)
            if p is None or (flowers_only and p.category not in FLOWER_CATEGORIES):
                continue
            unit = f" {p.unit}" if p.unit != "шт" and not flowers_only else ""  # клиентам в боте — без «ветка», «м»
            groups.setdefault(p.category, []).append(f"{p.name} × {qty_text(qty)}{unit}")
        order = {c: i for i, c in enumerate(CATEGORIES)}
        return sorted(groups.items(), key=lambda g: order.get(g[0], len(order)))

    # ----- готовые букеты на витрине: собрать, продать, списать, разобрать -----

    def ready_of(self, recipe_id: int) -> float:
        return self.conn.execute("SELECT COALESCE(SUM(qty), 0) FROM ready_moves WHERE recipe_id=?",
                                 (recipe_id,)).fetchone()[0]

    def _ready_move(self, recipe_id: int, qty: float, kind: str, cost: float, now: datetime, ref: int | None = None,
                    reason: str = "", shift_id: int | None = None, user_id: int | None = None):
        self.conn.execute("INSERT INTO ready_moves (recipe_id, at, qty, kind, cost, ref, reason, shift_id, user_id)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (recipe_id, _ts(now), qty, kind, cost, ref, reason, shift_id, user_id))

    def _ready_cost(self, recipe_id: int) -> float:
        r = self.conn.execute("SELECT ready_cost FROM recipes WHERE id=?", (recipe_id,)).fetchone()
        return r[0] if r else 0

    def assemble(self, recipe_id: int, qty: int, user_id: int | None, shift_id: int | None, now: datetime) -> float:
        """Собрали букет(ы) по техкарте на витрину: цветы и упаковка списываются со склада сразу,
        на витрине +qty готовых (и в ботах). Возвращает себестоимость одного букета."""
        recipe = self.get_recipe(recipe_id)
        if recipe is None or not recipe.items:
            raise StockError("Такой техкарты нет.")
        if qty <= 0:
            raise StockError("Сколько собрали? Укажите больше нуля.")
        unit = self.recipe_cost(recipe)
        for pid, q in recipe.items:
            self._move(pid, -q * qty, "assembly", self._avg_cost(pid), now, ref=recipe_id, shift_id=shift_id,
                       user_id=user_id)
        have = max(self.ready_of(recipe_id), 0)
        avg = (have * self._ready_cost(recipe_id) + qty * unit) / (have + qty)
        self.conn.execute("UPDATE recipes SET ready_cost=? WHERE id=?", (avg, recipe_id))
        self._ready_move(recipe_id, qty, "assemble", unit, now, shift_id=shift_id, user_id=user_id)
        self._publish(recipe)
        self.conn.commit()
        return unit

    def _publish(self, recipe: Recipe):
        """Собранный букет — на витрину ботов: свой пункт витрины (создаётся один раз) снова показывается."""
        if not recipe.in_bot:
            return
        row = self.conn.execute("SELECT id FROM showcase WHERE recipe_id=?", (recipe.id,)).fetchone()
        if row:
            self.conn.execute("UPDATE showcase SET active=1 WHERE id=?", (row[0],))
            return
        parts = self.composition(recipe, flowers_only=True)
        description = ("Состав: " + ", ".join(x for _, items in parts for x in items)) if parts else ""
        position = self.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM showcase").fetchone()[0]
        self.conn.execute("INSERT INTO showcase (title, description, price, photo, active, position, single, recipe_id)"
                          " VALUES (?, ?, ?, ?, 1, ?, 0, ?)",
                          (recipe.title, description, recipe.price, self.photo_of("recipe", recipe.id), position,
                           recipe.id))

    def reserved_of(self, recipe_id: int) -> int:
        """Сколько собранных забронировано заказами из бота (ещё не принятыми)."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM client_orders o JOIN showcase s ON s.id = o.showcase_id"
            f" WHERE s.recipe_id=? AND o.status IN {OPEN_ORDER}", (recipe_id,)).fetchone()[0]

    def _check_ready(self, recipe_id: int, qty: float):
        ready = self.ready_of(recipe_id)
        if qty <= 0:
            raise StockError("Укажите, сколько букетов.")
        if qty > ready:
            raise StockError(f"На витрине только {qty_text(max(ready, 0))} шт.")

    def write_off_ready(self, recipe_id: int, qty: float, reason: str, user_id: int | None, shift_id: int | None,
                        now: datetime):
        """Готовый букет завял / сломался на витрине — списать целиком (по себестоимости сборки)."""
        self._check_ready(recipe_id, qty)
        self._ready_move(recipe_id, -qty, "writeoff", self._ready_cost(recipe_id), now, reason=reason or "Другое",
                         shift_id=shift_id, user_id=user_id)
        self.conn.commit()

    def disassemble(self, recipe_id: int, qty: float, broken: dict[int, float], reason: str, user_id: int | None,
                    shift_id: int | None, now: datetime):
        """Разобрали готовый букет: всё из него возвращается на склад, а то, что выкинули (broken: товар →
        сколько), сразу списывается с причиной. Пересобрать — после этого просто собрать другой букет."""
        self._check_ready(recipe_id, qty)
        recipe = self.get_recipe(recipe_id)
        self._ready_move(recipe_id, -qty, "disassemble", self._ready_cost(recipe_id), now, shift_id=shift_id,
                         user_id=user_id)
        for pid, q in recipe.items:
            self._move(pid, q * qty, "disassembly", self._avg_cost(pid), now, ref=recipe_id, shift_id=shift_id,
                       user_id=user_id)
        for pid, q in broken.items():
            if q > 0:
                self._move(pid, -q, "writeoff", self._avg_cost(pid), now, reason=reason or "Другое",
                           shift_id=shift_id, user_id=user_id)
        self.conn.commit()

    def ready_moves_of(self, recipe_id: int, limit: int = 100) -> list[tuple[datetime, str, float, str]]:
        rows = self.conn.execute("SELECT at, kind, qty, reason FROM ready_moves WHERE recipe_id=? ORDER BY id DESC"
                                 " LIMIT ?", (recipe_id, limit)).fetchall()
        return [(datetime.fromisoformat(r[0]), r[1], r[2], r[3]) for r in rows]

    def recipe_cost(self, recipe: Recipe) -> float:
        """Себестоимость букета по средним закупочным ценам."""
        return sum(qty * self._avg_cost(pid) for pid, qty in recipe.items)

    # ----- продажи -----

    def add_sale(self, lines: list[CartLine], payment: str, user_id: int | None, shift_id: int | None, now: datetime,
                 discount: int = 0, note: str = "", flowers: bool = False, adjust_note: str = "") -> int:
        # discount < 0 — наценка (срочно, доставка до двери, сложная упаковка): прибавляется к сумме;
        # adjust_note — почему скидка или наценка (без причины их не делают)
        """Продажа: товары и цветы из техкарт списываются со склада сами. Остаток может уйти в минус —
        тогда в «Остатках» это будет видно (значит, поставку не внесли).
        flowers — свободные позиции (без склада) тоже считать цветами (букет из заказа бота)."""
        lines = [line for line in lines if line.qty > 0]
        if not lines:
            raise StockError("В продаже нет ни одной позиции.")
        if payment not in PAYMENTS:
            raise StockError("Выберите способ оплаты.")
        gross = sum(line.amount for line in lines)
        if any(line.price < 0 for line in lines):
            raise StockError("Цена не может быть отрицательной.")
        if discount > gross:
            raise StockError("Скидка не может быть больше суммы.")
        if discount and not adjust_note.strip():
            raise StockError(f"Напишите, почему {'скидка' if discount > 0 else 'наценка'}.")
        total = gross - discount
        flowers_gross = 0
        sale_id = self.conn.execute(
            "INSERT INTO sales (at, shift_id, user_id, total, discount, payment, note, adjust_note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_ts(now), shift_id, user_id, total, discount, payment, note.strip(),
             adjust_note.strip() if discount else "")).lastrowid
        sale_cost = 0.0
        for line in lines:
            cost = 0.0
            title = line.title.strip()
            if line.recipe_id is not None:
                recipe = self.get_recipe(line.recipe_id)
                title = title or (recipe.title if recipe else "")
                ready = min(max(self.ready_of(line.recipe_id), 0), line.qty)  # сначала — собранные с витрины
                if ready:
                    unit = self._ready_cost(line.recipe_id)
                    cost += ready * unit
                    self._ready_move(line.recipe_id, -ready, "sale", unit, now, ref=sale_id, shift_id=shift_id,
                                     user_id=user_id)
                rest = line.qty - ready  # не было собранных — цветы списываются по техкарте сейчас
                for pid, qty in recipe.items if recipe and rest else []:
                    unit = self._avg_cost(pid)
                    cost += qty * rest * unit
                    self._move(pid, -qty * rest, "sale", unit, now, ref=sale_id, shift_id=shift_id, user_id=user_id)
                flowers_gross += line.amount  # букет — продажа цветов
            elif line.product_id is not None:
                unit = self._avg_cost(line.product_id)
                cost = line.qty * unit
                self._move(line.product_id, -line.qty, "sale", unit, now, ref=sale_id, shift_id=shift_id,
                           user_id=user_id)
                product = self.conn.execute("SELECT category, name FROM products WHERE id=?",
                                            (line.product_id,)).fetchone()
                title = title or (product[1] if product else "")
                if product and product[0] in FLOWER_CATEGORIES:
                    flowers_gross += line.amount
            elif flowers:
                flowers_gross += line.amount
            sale_cost += cost
            self.conn.execute(
                "INSERT INTO sale_lines (sale_id, recipe_id, product_id, title, qty, price, cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sale_id, line.recipe_id, line.product_id, title or "Позиция", line.qty, line.price, cost))
        flowers = round(flowers_gross * total / gross) if gross else 0  # скидка — пропорционально
        self.conn.execute("UPDATE sales SET flowers=?, cost=? WHERE id=?", (flowers, sale_cost, sale_id))
        self.conn.commit()
        return sale_id

    def cancel_sale(self, sale_id: int, reason: str, user_id: int | None, now: datetime):
        """Отмена (возврат): товары возвращаются на склад, в выручку продажа больше не идёт."""
        r = self.conn.execute("SELECT cancelled_at, shift_id FROM sales WHERE id=?", (sale_id,)).fetchone()
        if r is None:
            raise StockError("Такой продажи нет.")
        if r[0]:
            raise StockError("Продажа уже отменена.")
        for pid, qty, cost in self.conn.execute(
                "SELECT product_id, qty, cost FROM stock_moves WHERE kind='sale' AND ref=?", (sale_id,)).fetchall():
            self._move(pid, -qty, "return", cost, now, ref=sale_id, reason=reason, shift_id=r[1], user_id=user_id)
        for rid, qty, cost in self.conn.execute(  # проданный с витрины букет — обратно на витрину
                "SELECT recipe_id, qty, cost FROM ready_moves WHERE kind='sale' AND ref=?", (sale_id,)).fetchall():
            self._ready_move(rid, -qty, "return", cost, now, ref=sale_id, reason=reason, shift_id=r[1], user_id=user_id)
        self.conn.execute("UPDATE sales SET cancelled_at=?, cancel_reason=? WHERE id=?", (_ts(now), reason.strip(), sale_id))
        self.conn.commit()

    def _sales(self, where: str, params: tuple) -> list[Sale]:
        rows = self.conn.execute(
            "SELECT s.id, s.at, s.shift_id, s.user_id, COALESCE(NULLIF(u.name, ''), u.login, ''), s.total, s.discount,"
            " s.flowers, s.cost, s.payment, s.note, s.cancelled_at, s.cancel_reason, s.adjust_note"
            f" FROM sales s LEFT JOIN users u ON u.id = s.user_id WHERE {where} ORDER BY s.at DESC, s.id DESC",
            params).fetchall()
        result = []
        for r in rows:
            lines = self.conn.execute("SELECT title, qty, price FROM sale_lines WHERE sale_id=? ORDER BY id",
                                      (r[0],)).fetchall()
            result.append(Sale(r[0], datetime.fromisoformat(r[1]), r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                               r[10], datetime.fromisoformat(r[11]) if r[11] else None, r[12],
                               [tuple(x) for x in lines], r[13]))
        return result

    def sales_between(self, start: datetime, end: datetime, include_cancelled: bool = True) -> list[Sale]:
        where = "s.at >= ? AND s.at < ?" + ("" if include_cancelled else " AND s.cancelled_at IS NULL")
        return self._sales(where, (_ts(start), _ts(end)))

    def sales_of_shift(self, shift_id: int) -> list[Sale]:
        return self._sales("s.shift_id = ?", (shift_id,))

    def shift_sales(self, shift_id: int) -> ShiftSales:
        """Для сдачи смены: выручка, «цветы», оплаты и списания со склада за эту смену."""
        result = ShiftSales()
        for s in self.sales_of_shift(shift_id):
            if s.cancelled_at:
                continue
            result.count += 1
            result.revenue += s.total
            result.flowers += s.flowers
            setattr(result, s.payment, getattr(result, s.payment) + s.total)
        for reason, amount in self.conn.execute(
                f"SELECT reason, SUM(-qty * cost) FROM ({WRITEOFFS}) WHERE shift_id=? GROUP BY reason",
                (shift_id,)).fetchall():
            result.writeoffs[reason] = round(amount)
        return result

    def writeoffs_between(self, start: datetime, end: datetime) -> dict[str, int]:
        return {reason: round(amount) for reason, amount in self.conn.execute(
            f"SELECT reason, SUM(-qty * cost) FROM ({WRITEOFFS}) WHERE at >= ? AND at < ?"
            " GROUP BY reason ORDER BY 2 DESC", (_ts(start), _ts(end))).fetchall()}

    def supplies_total(self, start: datetime, end: datetime) -> int:
        return self.conn.execute("SELECT COALESCE(SUM(total), 0) FROM supplies WHERE at >= ? AND at < ?",
                                 (_ts(start), _ts(end))).fetchone()[0]

    def sales_report(self, start: datetime, end: datetime, top: int = 10) -> SalesReport:
        report = SalesReport()
        for s in self.sales_between(start, end, include_cancelled=False):
            report.count += 1
            report.revenue += s.total
            report.cost += s.cost
            report.discount += max(s.discount, 0)
            report.markup += max(-s.discount, 0)
            if s.discount:
                key = ("Скидка" if s.discount > 0 else "Наценка") + (f": {s.adjust_note}" if s.adjust_note else "")
                report.adjust_notes[key] = report.adjust_notes.get(key, 0) + abs(s.discount)
            day = s.at.date()
            report.by_day[day] = report.by_day.get(day, 0) + s.total
            who = s.user_name or "Бот / без смены"
            n, amount = report.by_user.get(who, (0, 0))
            report.by_user[who] = (n + 1, amount + s.total)
            report.by_payment[s.payment] = report.by_payment.get(s.payment, 0) + s.total
        rows = self.conn.execute(
            "SELECT l.title, SUM(l.qty), SUM(l.qty * l.price) FROM sale_lines l JOIN sales s ON s.id = l.sale_id"
            " WHERE s.at >= ? AND s.at < ? AND s.cancelled_at IS NULL GROUP BY l.title COLLATE NOCASE"
            " ORDER BY 3 DESC LIMIT ?", (_ts(start), _ts(end), top)).fetchall()
        report.top = [(r[0], r[1], round(r[2])) for r in rows]
        return report

    def stock_value(self) -> int:
        """Сколько стоит всё, что лежит на складе и на витрине (по себестоимости)."""
        goods = sum(p.stock * p.avg_cost for p in self.products() if p.stock > 0)
        return round(goods + sum(r.ready * self._ready_cost(r.id) for r in self.recipes() if r.ready > 0))

    def stock_moves_of(self, product_id: int, limit: int = 100) -> list[tuple[datetime, str, float, str]]:
        """История товара: (когда, что, сколько, причина)."""
        rows = self.conn.execute("SELECT at, kind, qty, reason FROM stock_moves WHERE product_id=? ORDER BY id DESC LIMIT ?",
                                 (product_id, limit)).fetchall()
        return [(datetime.fromisoformat(r[0]), r[1], r[2], r[3]) for r in rows]


def day_range(first: date, last: date) -> tuple[datetime, datetime]:
    """С начала first до конца last (полуинтервал)."""
    return datetime.combine(first, time()), datetime.combine(last + timedelta(days=1), time())
