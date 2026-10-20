"""Главное окно: календарь на месяц и список доставок на выбранный день."""

import calendar
import copy
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QLocale, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QBrush, QColor, QDesktopServices, QFont, QKeySequence, QPainter, QPen, QShortcut,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .db import Database, Delivery, Totals, User
from .delivery_dialog import DeliveryDialog, money
from .icon import flower_icon
from .messaging import KINDS, SHORT, build_action, render
from . import backup, telegram, theme
from .announcements_dialog import HistoryDialog, UnreadDialog, short
from .notifications_dialog import NotificationsDialog
from .bot_commands import BotContext, BotHandler, chunk
from .version import VERSION
from .duties_dialog import ChecklistDialog
from .reports import due_reports, report_text
from .settings_dialog import (
    SettingsDialog, assembly_alert_minutes, assembly_minutes, backup_dir, backup_keep, backup_to_telegram,
    grace_minutes, last_backup, late_minutes, lead_minutes,
    penalty_catalog, report_settings, salary_rules, set_last_backup, shift_change, theme_mode, water_schedule,
    writeoff_alert,
)
from .shift_dialogs import CabinetDialog, CashOpDialog, CloseShiftDialog, StartShiftDialog, fmt_dt
from .shifts import (
    PROBLEM_STATUSES, REMIND_BEFORE_MINUTES, Shift, business_day, opening_business_day, short_name, window,
)
from .templates_dialog import load_templates
from .theme import C
from .update_ui import UpdateController
from .assembly_dialog import SNOOZE, AssemblyReminder
from .reminders import assembly_due, assembly_escalations, due_reminders, overdue_deliveries, reminder_key
from .shift_plan import ShiftPlanDialog
from .salary import pct, shop_writeoff
from .users_dialog import LoginDialog, PasswordDialog, UserFormDialog, UsersDialog

MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

HEADERS = ["💐", "✓", "Время", "Что доставить", "Кому", "Адрес",
           "Взяли", "Курьеру", "Осталось", "Заказчик", "Писали", "Кто"]
COL_ASSEMBLED, COL_DONE, COL_TIME, COL_ITEMS, COL_RECIPIENT, COL_ADDRESS, \
    COL_PRICE, COL_COURIER, COL_REST, COL_CUSTOMER, COL_MESSAGES, COL_WHO = range(len(HEADERS))
WRAP_UP_TO = {COL_RECIPIENT: 170, COL_CUSTOMER: 160}  # длинное — переносится, а не растягивает столбец
MIN_WIDTH = {COL_ITEMS: 220, COL_ADDRESS: 160}  # главные столбцы: всё свободное место, но не уже этого

REMINDER_CHECK_MS = 30_000

WATER_REMIND_AFTER = timedelta(hours=4)  # через сколько после начала смены напомнить про воду
BACKUP_EVERY = timedelta(hours=24)
ANNOUNCE_FOR = timedelta(days=7)  # сколько дней новое объявление всплывает тем, кто его не прочитал


def to_date(qd: QDate) -> date:
    return date(qd.year(), qd.month(), qd.day())


def describe_delivery(d: Delivery) -> str:
    """Доставка одной строкой — для журнала и сообщений."""
    what = d.items.splitlines()[0] if d.items else "—"
    return (f"№{d.id}: {d.day:%d.%m} {d.time or '—'}, {what}, {d.address or 'адрес не указан'}"
            + (f" ({money(d.price)})" if d.price else "")).replace("№None: ", "")


DELIVERY_FIELDS = (("day", "дата"), ("time", "время"), ("items", "что доставить"), ("recipient", "получатель"),
                   ("phone", "телефон"), ("address", "адрес"), ("price", "взяли"), ("courier_pay", "курьеру"),
                   ("assembled", "собрано"), ("delivered", "доставлено"), ("source", "откуда заказ"), ("customer_contact", "контакт заказчика"))


def delivery_changes(old: Delivery, new: Delivery) -> str:
    """Что поменяли в доставке: «адрес: «а» → «б»; взяли: 500 → 700»."""
    parts = []
    for field, title in DELIVERY_FIELDS:
        a, b = getattr(old, field), getattr(new, field)
        if a != b:
            fmt = (lambda v: ("да" if v else "нет") if isinstance(v, bool) else
                   f"{v:%d.%m.%Y}" if isinstance(v, date) else f"«{v}»" if isinstance(v, str) else str(v))
            parts.append(f"{title}: {fmt(a)} → {fmt(b)}")
    return "; ".join(parts)


class LoggingTray(QSystemTrayIcon):
    """Значок у часов, который запоминает каждое показанное уведомление."""

    def __init__(self, icon, parent, on_message):
        super().__init__(icon, parent)
        self._on_message = on_message

    def showMessage(self, title, message, *args):
        self._on_message(title, message)
        super().showMessage(title, message, *args)


class ShiftCalendar(QCalendarWidget):
    """Календарь, в клетках которого внизу подписано, чья была смена."""

    def __init__(self):
        super().__init__()
        self.marks: dict[date, tuple[str, bool]] = {}  # день -> (имена, есть ли проблема)
        self.water: dict[date, str] = {}  # день смены воды -> planned / done / missed

    def paintCell(self, painter, rect, qdate):
        super().paintCell(painter, rect, qdate)
        water = self.water.get(to_date(qdate))
        if water:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            color = QColor("white" if qdate == self.selectedDate() else (C.red if water == "missed" else C.blue))
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(color if water != "planned" else Qt.NoBrush)
            size = max(6, min(rect.width(), rect.height()) // 6)
            painter.drawEllipse(rect.right() - size - 4, rect.top() + 4, size, size)
            painter.restore()
        mark = self.marks.get(to_date(qdate))
        if not mark:
            return
        names, problem = mark
        painter.save()
        font = painter.font()
        font.setPointSizeF(max(6.5, font.pointSizeF() * 0.72))
        painter.setFont(font)
        if qdate == self.selectedDate():
            painter.setPen(QColor("white"))  # на синей подсветке выбранного дня
        else:
            painter.setPen(QColor(C.red if problem else C.shift_name))
        area = rect.adjusted(2, 0, -2, -1)
        text = painter.fontMetrics().elidedText(names, Qt.ElideRight, area.width())
        painter.drawText(area, Qt.AlignHCenter | Qt.AlignBottom, text)
        painter.restore()


def totals_text(t: Totals) -> str:
    return (
        f"Доставок: <b>{t.count}</b> (собрано {t.assembled}, доставлено {t.delivered})"
        f" &nbsp;|&nbsp; Взяли: <b>{money(t.price)}</b>"
        f" &nbsp;|&nbsp; Курьерам: <b>{money(t.courier_pay)}</b>"
        f" &nbsp;|&nbsp; Осталось: <b>{money(t.remainder)}</b>"
    )


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.deliveries: list[Delivery] = []
        self._marked_days: list[QDate] = []
        self._loading = False
        self.user: User | None = None
        self._reminded: set = set()
        self._reports_done: set = set()
        self.assembly = AssemblyReminder()  # «Не собрано!» — отдельное окно поверх всех
        self.assembly.assembled.connect(self._assembled_from_reminder)
        self.assembly.delivered.connect(self._delivered_from_reminder)
        self.assembly.snoozed.connect(self._snooze_assembly)
        self._assembly_snooze_until: datetime | None = None
        self._assembly_seen: set[int] = set()  # о каких уже напоминали: новая — показываем сразу
        self._assembly_first: dict[int, datetime] = {}  # когда впервые напомнили — хозяевам только после 10 минут
        self._backup_attempt: datetime | None = None
        self._backup_day_pending = ""
        self._really_quit = False
        self._tray_hint_shown = False
        self._last_reminded_day: date | None = None
        self.shift: Shift | None = None  # несданная смена того, кто сейчас вошёл
        self._shift_reminded: set[int] = set()
        self._shift_ended: set[int] = set()
        self._water_reminded: set[date] = set()
        self._backup_warned: date | None = None
        self.telegram = telegram.TelegramSender(db, self)
        self.telegram.file_sent.connect(self._backup_file_sent)
        self.inbox = telegram.TelegramInbox(db, self)
        self.inbox.arrived.connect(self._on_announcements)
        self.bot = BotHandler()
        self._current_bot_chat = None  # чат того, чью команду обрабатываем сейчас
        self._current_bot_author = ""

        def handle(text, chat, author):
            self._current_bot_chat, self._current_bot_author = chat, author
            try:
                return self.bot.handle(text, self._bot_context(), chat, author)
            finally:
                self._current_bot_chat, self._current_bot_author = None, ""

        self.inbox.handler = handle
        self.inbox.reply = self._bot_reply
        self.settings = QSettings("FlowerDeliveries", "FlowerDeliveries")

        self.setWindowTitle("AloFlorist")
        self.setWindowIcon(flower_icon())
        self.resize(1440, 720)
        screen = QApplication.primaryScreen()
        if screen is not None:  # на ноутбуке 1366×768 окно не должно вылезать за экран
            area = screen.availableGeometry()
            self.resize(min(1440, area.width()), min(720, area.height() - 40))

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # Левая часть: календарь и итоги месяца
        left = QVBoxLayout()
        root.addLayout(left, 0)

        self.calendar = ShiftCalendar()
        self.calendar.setLocale(QLocale(QLocale.Russian, QLocale.Russia))
        self.calendar.setFirstDayOfWeek(Qt.Monday)
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setMinimumSize(420, 300)
        self.calendar.selectionChanged.connect(self.refresh_day)
        self.calendar.currentPageChanged.connect(lambda *_: self.refresh_month())
        left.addWidget(self.calendar)

        self.legend = QLabel()
        left.addWidget(self.legend)

        today_btn = QPushButton("Сегодня")
        today_btn.clicked.connect(self.go_today)
        left.addWidget(today_btn)

        self.shift_box = QGroupBox("Смена")
        shift_layout = QVBoxLayout(self.shift_box)
        self.shift_label = QLabel()
        self.shift_label.setWordWrap(True)
        shift_layout.addWidget(self.shift_label)
        shift_buttons = QHBoxLayout()
        self.shift_btn = QPushButton()
        self.shift_btn.clicked.connect(self.toggle_shift)
        self.cash_btn = QPushButton("💸 Расход / изъятие")
        self.cash_btn.setToolTip("Записать расход из кассы или изъятие денег")
        self.cash_btn.clicked.connect(self.cash_operation)
        cabinet_btn = QPushButton("👤 Кабинет")
        cabinet_btn.setToolTip("Личный кабинет: смены и итоги за месяц")
        cabinet_btn.clicked.connect(self.open_cabinet)
        self.plan_btn = QPushButton("📋 План")
        self.plan_btn.setToolTip("План на смену: что собрать и к какому времени, вода, обязанности")
        self.plan_btn.clicked.connect(self.open_plan)
        for b in (self.shift_btn, self.cash_btn, self.plan_btn, cabinet_btn):
            shift_buttons.addWidget(b)
        shift_layout.addLayout(shift_buttons)
        water_row = QHBoxLayout()
        self.water_label = QLabel()
        self.water_btn = QPushButton("💧 Вода поменяна")
        self.water_btn.setToolTip("Отметить, что воду в вёдрах поменяли")
        self.water_btn.clicked.connect(self.mark_water)
        water_row.addWidget(self.water_label, 1)
        water_row.addWidget(self.water_btn)
        self.duties_btn = QPushButton()
        self.duties_btn.setToolTip("Отметить, что сделано из обязанностей смены")
        self.duties_btn.clicked.connect(self.open_checklist)
        water_row.addWidget(self.duties_btn)
        shift_layout.addLayout(water_row)
        left.addWidget(self.shift_box)

        self.month_box = QGroupBox()
        month_layout = QVBoxLayout(self.month_box)
        self.month_label = QLabel()
        self.month_label.setWordWrap(True)
        month_layout.addWidget(self.month_label)
        left.addWidget(self.month_box)
        self.updater = UpdateController(self)
        left.addStretch(1)

        # Правая часть: доставки за день
        right = QVBoxLayout()
        root.addLayout(right, 1)

        self.day_title = QLabel()
        title_font = QFont()
        title_font.setPointSize(15)
        title_font.setBold(True)
        self.day_title.setFont(title_font)
        self.day_title.setWordWrap(True)  # на узком экране — в две строки, а не шире экрана

        top = QHBoxLayout()
        top.addWidget(self.day_title, 1)
        self.user_btn = QPushButton()
        self.user_btn.setToolTip("Кто сейчас вошёл. Здесь же — смена пароля")
        user_menu = QMenu(self.user_btn)
        password_action = user_menu.addAction("🔑 Сменить пароль")
        password_action.triggered.connect(self.change_password)
        self.user_btn.setMenu(user_menu)
        top.addWidget(self.user_btn)
        self.ann_btn = QPushButton("📢 Объявления")
        self.ann_btn.clicked.connect(self.open_announcements)
        top.addWidget(self.ann_btn)
        self.notif_btn = QPushButton("🔔 Уведомления")
        self.notif_btn.setToolTip("Всё, что программа показывала в углу экрана за последние 24 часа")
        self.notif_btn.clicked.connect(self.open_notifications)
        top.addWidget(self.notif_btn)
        self.settings_btn = QPushButton("⚙ Настройки")
        self.settings_btn.clicked.connect(self.open_settings)
        logout_btn = QPushButton("🚪 Выйти")
        logout_btn.setToolTip("Выйти из профиля, чтобы вошёл другой сотрудник. Смена при этом не закрывается.")
        logout_btn.clicked.connect(self.logout)
        for b in (self.settings_btn, logout_btn):
            top.addWidget(b)
        right.addLayout(top)

        buttons = QHBoxLayout()
        add_btn = QPushButton("➕ Добавить доставку")
        add_btn.clicked.connect(self.add_delivery)
        self.edit_btn = QPushButton("✏️ Изменить")
        self.edit_btn.clicked.connect(self.edit_delivery)
        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self.delete_delivery)
        self.keep_btn = QPushButton("↩ Оставить")
        self.keep_btn.setToolTip("Отклонить запрос сотрудника на удаление этой доставки")
        self.keep_btn.clicked.connect(self.keep_delivery)
        self.write_btn = QPushButton("💬 Написать заказчику")
        self.write_btn.setToolTip("Сообщение заказчику в его мессенджер")
        write_menu = QMenu(self.write_btn)
        for kind, title in KINDS.items():
            action = write_menu.addAction(title)
            action.triggered.connect(lambda _=False, k=kind: self.write_to_customer(k))
        self.write_btn.setMenu(write_menu)
        for b in (add_btn, self.edit_btn, self.delete_btn, self.keep_btn, self.write_btn):
            b.setMinimumHeight(32)
            buttons.addWidget(b)
        buttons.addStretch(1)
        right.addLayout(buttons)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col in range(len(HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.Interactive if col in (*WRAP_UP_TO, *MIN_WIDTH)
                                        else QHeaderView.ResizeToContents)
        self.table.horizontalHeaderItem(COL_DONE).setToolTip("Доставлено")
        self.table.horizontalHeaderItem(COL_ASSEMBLED).setToolTip("Букет собран")
        self.table.horizontalHeaderItem(COL_WHO).setToolTip("Кто добавил доставку и кто отметил «Доставлено» (✓)")
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        right.addWidget(self.table, 1)

        self.day_totals = QLabel()
        right.addWidget(self.day_totals)

        self.hint = QLabel("💐 — букет собран, ✓ — доставлено. Двойной щелчок — изменить.")
        self.hint.setStyleSheet(f"color: {C.gray}")
        right.addWidget(self.hint)

        QShortcut(QKeySequence.New, self, activated=self.add_delivery)
        QShortcut(QKeySequence.Delete, self.table, activated=self.delete_delivery)

        self._setup_tray()
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start(REMINDER_CHECK_MS)

        self.refresh_month()
        self.refresh_day()
        QTimer.singleShot(2000, self.check_reminders)

    # ----- напоминания и трей -----

    def _setup_tray(self):
        self.tray = LoggingTray(flower_icon(), self, self._log_notification)
        self.tray.setToolTip("AloFlorist")
        menu = QMenu(self)
        open_action = QAction("Открыть", self)
        open_action.triggered.connect(self.show_window)
        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(open_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self._on_message_clicked)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _on_message_clicked(self):
        self.show_window()
        if self._last_reminded_day is not None:
            d = self._last_reminded_day
            self.calendar.setSelectedDate(QDate(d.year, d.month, d.day))

    # ----- вход и сотрудники -----

    def login(self) -> bool:
        """Спросить логин и пароль (или создать первого админа). True — вошли, False — нажали «Отмена»."""
        while True:
            if not self.db.has_users():
                dlg = UserFormDialog(self, self.db, first_admin=True)
            else:
                dlg = LoginDialog(self, self.db, self._on_shift_note())
            if dlg.exec() != QDialog.Accepted:
                return False
            self.set_user(dlg.user)
            if self._shift_flow():
                self.update_shift_box()
                return True
            # Сдал смену и не начал новую (или передумал) — окно входа для следующего сотрудника.
            self.set_user(None)

    # ----- смены -----

    def _shift_flow(self) -> bool:
        """После входа: сдать закончившуюся смену, начать новую. Сотрудник без смены не работает."""
        now, change, grace = datetime.now(), shift_change(), grace_minutes()
        self.shift = None
        pending = self.db.current_shift(self.user.id)
        just_closed = False
        if pending is not None:
            status = pending.status(now, change, grace)
            if status == "open":
                self.shift = pending
                return True
            if status == "closing":
                QMessageBox.information(
                    self, "Сдача смены",
                    f"Смена закончилась в {pending.end(change):%H:%M}. Сдайте её до "
                    f"{pending.deadline(change, grace):%H:%M} — иначе она будет отмечена как не сданная.")
                closing = CloseShiftDialog(self, self.db, pending, change, grace, water_schedule())
                if closing.exec() != QDialog.Accepted:
                    if self.user.is_admin:
                        self.shift = pending
                        self._shift_ended.add(pending.id)  # уже знает, что смена кончилась
                        return True
                    return False
                self._after_shift_closed(closing.shift)
                just_closed = True
            else:
                QMessageBox.warning(
                    self, "Смена не сдана",
                    f"Ваша смена за {pending.day:%d.%m} не была сдана вовремя. "
                    "Администратор увидит это в отчёте.")
        if self.user.is_admin:
            return True  # админ может работать и без смены
        if just_closed:
            answer = QMessageBox.question(self, "Новая смена", "Смена сдана. Начать новую смену?",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                return False
        same_day = self._same_day_shifts()
        if same_day and QMessageBox.question(
                self, "Смена уже идёт",
                f"Смена за сегодня уже идёт: {', '.join(same_day)}. Касса одна — две смены сразу "
                "запутают расчёт.\n\nВсё равно начать ещё одну смену?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        dlg = StartShiftDialog(self, self.db, self.user, change)
        if dlg.exec() != QDialog.Accepted:
            return False
        self.shift = dlg.shift
        self._after_shift_started(self.shift)
        QTimer.singleShot(0, self.open_plan)  # сразу показать, что сегодня сделать
        return True

    def _after_shift_started(self, shift: Shift):
        handover = self.db.handover_difference(shift)
        if telegram.settings()["on_start"] or handover:
            prev = self.db.previous_shift(shift)
            self.telegram.queue(telegram.shift_started_text(shift, handover, prev.user_name if prev else ""),
                                key=f"start:{shift.id}")

    def _after_shift_closed(self, shift: Shift):
        """Итоги — в Telegram, данные — в резервную копию."""
        if shift is not None and shift.closed_at is not None:
            alert = self._writeoff_alert(shift)
            if telegram.settings()["on_close"]:
                change = shift_change()
                water = water_schedule().status(shift.day, self.db.water_change(shift.day) is not None,
                                                datetime.now(), change)
                missed = [i.text for i in self.db.checklist(shift) if not i.done]
                late = shift.status(datetime.now(), change, grace_minutes()) == "late"
                self.telegram.queue(telegram.shift_closed_text(
                    shift, change, late, water, missed, penalty_catalog(), self._writeoff_note(shift),
                    self.db.writeoff_items_for_shift(shift.id), alert),
                    key=f"close:{shift.id}")
            elif alert:  # итоги смен выключены, но о большом списании хозяева узнают всё равно
                self.telegram.queue(f"{alert}\n{shift.user_name or 'Сотрудник'}, смена {shift.day:%d.%m}",
                                    key=f"writeoff_alert:{shift.id}")
        self.run_backup()

    def update_shift_box(self):
        self._update_water_row()
        self.duties_btn.setVisible(False)
        self.plan_btn.setVisible(self.user is not None and (self.shift is not None or bool(self.active_shifts())))
        if self.user is None:
            self.shift_label.setText("")
            return
        if self.shift is None:
            others = [s for s in self.active_shifts() if s.user_id != self.user.id]
            if others:
                change = shift_change()
                lines = [f"На смене: <b>{s.user_name or '—'}</b>, с {fmt_dt(s.opened_at)} до "
                         f"{fmt_dt(s.end(change))}" for s in others]
                self.shift_label.setText("<br>".join(lines) + "<br><span style='color:gray'>Вы не на смене. "
                                         "Смена продолжается — сотруднику достаточно снова войти.</span>")
            else:
                self.shift_label.setText("Вы не на смене.")
            self.shift_btn.setText("🟢 Начать смену")
            self.shift_btn.setVisible(self.user.is_admin)  # сотрудник начинает смену при входе
            self.cash_btn.setEnabled(False)
            return
        change = shift_change()
        s = self.db.get_shift(self.shift.id) or self.shift
        text = (f"На смене: <b>{s.user_name or '—'}</b>, с {fmt_dt(s.opened_at)} до "
                f"{fmt_dt(s.end(change))}.<br>Приняли {money(s.opening_cash)}")
        if s.expenses:
            text += f" · расходы {money(s.expenses)}"
        if s.withdrawals:
            text += f" · изъятия {money(s.withdrawals)}"
        self.shift_label.setText(text)
        self.shift_btn.setText("🔴 Сдать смену")
        self.shift_btn.setVisible(True)
        self.cash_btn.setEnabled(True)
        if s.duties_total:
            self.duties_btn.setText(f"📋 Обязанности {s.duties_done}/{s.duties_total}")
            self.duties_btn.setVisible(True)

    def open_checklist(self):
        if self.shift is not None:
            ChecklistDialog(self, self.db, self.shift).exec()
            self.update_shift_box()

    def _water_day(self) -> date:
        """Смена, для которой показываем воду: текущая смена сотрудника или идущая сейчас."""
        if self.shift is not None:
            return self.shift.day
        return business_day(datetime.now(), shift_change())

    def _update_water_row(self):
        day = self._water_day()
        if self.user is None or not water_schedule().is_water_day(day):
            self.water_label.setText("")
            self.water_btn.setVisible(False)
            return
        done = self.db.water_change(day)
        if done:
            who, when = done
            self.water_label.setText(f"<span style='color:{C.blue}'>💧 Вода поменяна</span> — "
                                     f"{who or '—'}, {when:%H:%M}")
            self.water_btn.setVisible(False)
        else:
            self.water_label.setText(f"<b style='color:{C.blue}'>💧 Сегодня день смены воды</b>")
            self.water_btn.setVisible(self.shift is not None or self.user.is_admin)

    def mark_water(self):
        self.db.mark_water(self._water_day(), self.user.id, datetime.now())
        self.update_shift_box()
        self.refresh_month()

    def toggle_shift(self):
        if self.shift is None:
            self.start_shift()
        else:
            self.close_shift()

    def _same_day_shifts(self) -> list[str]:
        """Кто уже на смене за тот же день, что сейчас начнётся (передача смены утром — не в счёт)."""
        now, change = datetime.now(), shift_change()
        day = opening_business_day(now, change)
        return [f"{s.user_name or '—'} (с {s.opened_at:%H:%M})" for s in self.active_shifts()
                if s.day == day and (self.user is None or s.user_id != self.user.id)]

    def start_shift(self):
        others = [s.user_name or "—" for s in self.active_shifts() if s.user_id != self.user.id]
        same_day = self._same_day_shifts()
        if same_day:
            text = (f"Смена за сегодня уже идёт: {', '.join(same_day)}. Касса одна — две смены сразу "
                    "запутают расчёт.\n\nВсё равно начать ещё одну смену?")
        else:
            text = (f"Сейчас на смене: {', '.join(others)}. Ваша смена будет отдельной, их смена не закроется.\n\n"
                    "Начать свою смену?")
        if others and QMessageBox.question(self, "Начать смену", text,
                                           QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        dlg = StartShiftDialog(self, self.db, self.user, shift_change())
        if dlg.exec() == QDialog.Accepted:
            self.shift = dlg.shift
            self._after_shift_started(self.shift)
            self.update_shift_box()
            self.refresh_month()
            self.open_plan()

    def close_shift(self):
        change, grace = shift_change(), grace_minutes()
        if not self.shift.can_close(datetime.now(), change, grace):
            QMessageBox.warning(self, "Сдача смены", "Время на сдачу этой смены уже вышло.")
            self.shift = None
            self.update_shift_box()
            return
        closing = CloseShiftDialog(self, self.db, self.shift, change, grace, water_schedule())
        if closing.exec() != QDialog.Accepted:
            return
        self._after_shift_closed(closing.shift)
        self.shift = None
        self.refresh_month()
        if self.user.is_admin:
            self.update_shift_box()
            QMessageBox.information(self, "Смена сдана", "Смена сдана.")
        else:
            QMessageBox.information(self, "Смена сдана", "Смена сдана. Спасибо за работу!")
            self.logout()

    def cash_operation(self):
        if self.shift is not None and CashOpDialog(self, self.db, self.shift, self.user).exec() == QDialog.Accepted:
            self.update_shift_box()

    def open_cabinet(self):
        CabinetDialog(self, self.db, self.user, shift_change(), grace_minutes(),
                      self.calendar.yearShown(), self.calendar.monthShown(), water_schedule(),
                      salary_rules, penalty_catalog, self._dispute_made, self._penalty_in_app).exec()

    def active_shifts(self) -> list[Shift]:
        """Кто сейчас на смене (или ещё может её сдать)."""
        now, change, grace = datetime.now(), shift_change(), grace_minutes()
        return [s for s in self.db.open_shifts() if s.status(now, change, grace) in ("open", "closing")]

    def _on_shift_note(self) -> str:
        """Подсказка в окне входа: кто сейчас на смене."""
        change = shift_change()
        return "<br>".join(f"Сейчас на смене: <b>{s.user_name or '—'}</b> (до {s.end(change):%H:%M}). "
                           "Чтобы продолжить смену, просто войдите снова." for s in self.active_shifts())

    def check_shift(self):
        """Напомнить о пересменке и смене воды; в пересменку вывести из программы того, чья смена."""
        now, change, grace = datetime.now(), shift_change(), grace_minutes()
        active = self.active_shifts()
        for s in active:
            mine = self.shift is not None and s.id == self.shift.id
            end, name = s.end(change), s.user_name or "сотрудник"
            if end - timedelta(minutes=REMIND_BEFORE_MINUTES) <= now < end and s.id not in self._shift_reminded:
                self._shift_reminded.add(s.id)
                text = (f"Смена заканчивается в {end:%H:%M}. Сдайте смену: кнопка «Сдать смену»." if mine
                        else f"Смена ({name}) заканчивается в {end:%H:%M} — нужно войти и сдать смену.")
                self.tray.showMessage("Скоро пересменка", text, QSystemTrayIcon.Warning, 60_000)
            if now >= end and s.id not in self._shift_ended:
                if mine and QApplication.activeModalWidget() is not None:
                    continue  # человек что-то заполняет — выведем, когда закроет окно
                self._shift_ended.add(s.id)
                deadline = s.deadline(change, grace)
                text = (f"Сдайте смену до {deadline:%H:%M}: войдите в программу и заполните итоги." if mine
                        else f"Смена ({name}) закончилась — нужно войти и сдать её до {deadline:%H:%M}.")
                self.tray.showMessage("Смена закончилась", text, QSystemTrayIcon.Warning, 60_000)
                if mine:
                    self.logout()
                    return

        for s in self.db.open_shifts():
            deadline = s.deadline(change, grace)
            if deadline <= now < deadline + timedelta(days=1):  # не сдана — сообщить хозяину один раз
                self.telegram.queue(telegram.not_closed_text(s), key=f"not_closed:{s.id}")

        day = business_day(now, change)
        if active and now >= window(day, change)[0] + WATER_REMIND_AFTER and day not in self._water_reminded \
                and water_schedule().is_water_day(day) and not self.db.water_change(day):
            self._water_reminded.add(day)
            self.tray.showMessage("Смена воды", "Сегодня день смены воды. Поменяйте воду и отметьте "
                                  "кнопкой «💧 Вода поменяна».", QSystemTrayIcon.Information, 60_000)

    def set_user(self, user: User | None):
        self.user = user
        if user is None:
            self.shift = None
            self.user_btn.setText("")
            self.update_shift_box()
            return
        role = "админ" if user.is_admin else "сотрудник"
        self.user_btn.setText(f"👤 {user.name or user.login} ({role})")
        self.settings_btn.setVisible(user.is_admin)
        self.update_shift_box()
        self._update_ann_button()
        self._update_notif_button()

    def logout(self):
        self.set_user(None)
        self.hide()
        if self.login():
            self.show_window()

    def change_password(self):
        if PasswordDialog(self, self.db, self.user, need_old=True, actor=self.user).exec() == QDialog.Accepted:
            QMessageBox.information(self, "Готово", "Пароль изменён.")

    def manage_users(self):
        UsersDialog(self, self.db, self.user).exec()
        # Могли переименовать или снять права с текущего пользователя
        current = next((u for u in self.db.users() if u.id == self.user.id), None)
        if current is None:
            self.logout()
        else:
            self.set_user(current)
            self.refresh_day()

    def show_window(self):
        if self.user is None and not self.login():
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh_all()
        self.updater.show_pending()
        QTimer.singleShot(0, self.show_unread_announcements)

    def quit_app(self):
        self._really_quit = True
        self.assembly.hide()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if self._really_quit or not QSystemTrayIcon.isSystemTrayAvailable():
            event.accept()
            QApplication.quit()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(
                "AloFlorist",
                "Программа работает в фоне и напомнит о доставках. "
                "Выход — правой кнопкой по значку у часов.",
                QSystemTrayIcon.Information, 5000,
            )

    def _bot_context(self) -> BotContext:
        return BotContext(
            db=self.db, now=datetime.now(), change=shift_change(), grace=grace_minutes(),
            water=water_schedule(), rules=salary_rules, version=VERSION, last_backup=last_backup(),
            backup_now=self._bot_backup, decided=self._delete_decided, audit=self._audit_telegram,
            penalty_kinds=penalty_catalog(), penalty_changed=self._penalty_changed, dispute_kept=self._dispute_kept)

    def audit(self, action: str, details: str = ""):
        """Запись в журнал действий от имени вошедшего сотрудника."""
        try:
            self.db.log_action(self.user, action, details)
        except Exception:  # noqa: BLE001 — журнал не должен ломать работу
            pass

    def _audit_telegram(self, action: str, details: str):
        try:
            who = f"{self._current_bot_author} (Telegram)" if self._current_bot_author else "Telegram"
            self.db.log_action(None, action, details, who=who)
        except Exception:  # noqa: BLE001
            pass

    def _bot_reply(self, text: str, chat: str):
        """Ответ на команду уходит только тому, кто спросил."""
        for part in chunk(text):
            self.telegram.queue(part, chat=chat)

    def _bot_backup(self) -> str:
        """Копия по команде из Telegram: что получилось, одним сообщением."""
        result = self.run_backup()
        if result is None:
            return "Копию сделать не удалось."
        text = "Копия сделана на компьютере"
        if result.extra:
            text += " и на флешке / в папке"
        if result.extra_error:
            text += f". На флешку не получилось: {result.extra_error}"
        if self._current_bot_chat:  # файл — тому, кто попросил
            self.telegram.send_file(backup.zip_copy(result.local, datetime.now()), self._backup_caption(),
                                    "bot", [self._current_bot_chat])
            text += ". Файл пришлю следующим сообщением"
        return text + "."

    def run_backup(self):
        """Копия базы на компьютер и, если настроено, на флешку. Возвращает результат или None."""
        if self.db.path == ":memory:":
            return None
        now = datetime.now()
        try:
            result = backup.make_backups(self.db, backup_dir(), backup_keep(), now)
        except Exception as e:  # noqa: BLE001 — копия не должна мешать работе
            self.tray.showMessage("Резервная копия", f"Не удалось сделать копию: {e}",
                                  QSystemTrayIcon.Warning, 30_000)
            return None
        set_last_backup(now)
        if result.extra_error and self._backup_warned != now.date():
            self._backup_warned = now.date()
            self.tray.showMessage("Резервная копия",
                                  f"На флешку не скопировано ({result.extra_error}). "
                                  "Копия сохранена только на компьютере.", QSystemTrayIcon.Warning, 30_000)
        return result

    # ----- объявления -----

    def _unread(self) -> list:
        if self.user is None or self.user.is_admin:
            return []  # объявления пишет хозяин — показываем их сотрудникам
        return self.db.unread_announcements(self.user.id, datetime.now() - ANNOUNCE_FOR)

    # ----- журнал уведомлений -----

    def _log_notification(self, title: str, text: str):
        """Всё, что показано в углу экрана, остаётся в журнале на сутки — можно посмотреть ещё раз."""
        try:
            self.db.add_notification(title, text, datetime.now())
            self._update_notif_button()
        except Exception:  # noqa: BLE001 — журнал не должен мешать самому уведомлению
            pass

    def _notif_seen_key(self) -> str:
        return f"notif_seen_{self.user.id}"

    def _update_notif_button(self):
        if self.user is None:
            return
        try:
            seen = datetime.fromisoformat(str(self.settings.value(self._notif_seen_key(), "")))
        except ValueError:
            seen = datetime.now() - timedelta(hours=24)
        count = len([n for n in self.db.notifications_since(datetime.now() - timedelta(hours=24)) if n[0] > seen])
        self.notif_btn.setText(f"🔔 Уведомления ({count})" if count else "🔔 Уведомления")
        self.notif_btn.setStyleSheet(f"font-weight: bold; color: {C.orange}" if count else "")

    def open_notifications(self):
        NotificationsDialog(self, self.db).exec()
        self.settings.setValue(self._notif_seen_key(), datetime.now().isoformat(timespec="seconds"))
        self._update_notif_button()

    def _update_ann_button(self):
        count = len(self._unread())
        self.ann_btn.setText(f"📢 Объявления ({count})" if count else "📢 Объявления")
        self.ann_btn.setStyleSheet(f"font-weight: bold; color: {C.red}" if count else "")

    def _on_announcements(self, ids: list):
        for item in self.db.announcements(limit=len(ids) + 5):
            if item.id in ids:
                self.telegram.queue(f"✅ Принято, покажу сотрудникам: «{short(item.text)}»", key=f"ann:{item.id}")
                self.tray.showMessage("📢 Объявление", item.text, QSystemTrayIcon.Information, 60_000)
        self._update_ann_button()
        if self.isVisible() and QApplication.activeModalWidget() is None:
            self.show_unread_announcements()

    def show_unread_announcements(self):
        items = self._unread()
        if not items or not self.isVisible() or QApplication.activeModalWidget() is not None:
            return
        name = self.user.name or self.user.login
        if UnreadDialog(self, items, name).exec() == QDialog.Accepted:
            now = datetime.now()
            self.db.mark_announcements_read([i.id for i in items], self.user.id, now)
            for item in items:
                self.telegram.queue(f"👀 {name} прочитал(а) в {now:%H:%M}: «{short(item.text)}»",
                                    key=f"read:{item.id}:{self.user.id}")
        self._update_ann_button()

    def open_announcements(self):
        HistoryDialog(self, self.db, self.user).exec()
        self._update_ann_button()

    def apply_theme(self):
        """Переключить тему сразу, без перезапуска: палитра + перерисовать свои подсветки."""
        theme.apply(QApplication.instance(), theme_mode())
        self.hint.setStyleSheet(f"color: {C.gray}")
        self.refresh_all()
        self.update_shift_box()
        self._update_ann_button()

    def after_restore(self):
        QMessageBox.information(self, "Восстановление", "Данные восстановлены из копии. Войдите заново.")
        self.logout()

    def check_reminders(self):
        last = last_backup()
        if last is None or datetime.now() - last >= BACKUP_EVERY:
            self.run_backup()
        self.check_shift()
        now = datetime.now()
        today = now.date()
        self._check_overdue(now)
        self._check_assembly(now)
        self._check_reports(now)
        self._maybe_send_backup(now)
        deliveries = self.db.for_day(today) + self.db.for_day(today + timedelta(days=1))
        due = due_reminders(deliveries, now, lead_minutes(), self._reminded)
        for d in due:
            self._reminded.add(reminder_key(d))
        if not due:
            return

        lines = []
        for d in due:
            what = d.items.splitlines()[0] if d.items else "доставка"
            line = f"{d.time} — {what}"
            if d.address:
                line += f", {d.address}"
            lines.append(line)
        title = "Скоро доставка!" if len(due) == 1 else f"Скоро доставки: {len(due)}"
        self._last_reminded_day = due[0].day
        self.tray.showMessage(title, "\n".join(lines), QSystemTrayIcon.Warning, 60_000)
        QApplication.alert(self)

    def _check_overdue(self, now: datetime):
        """Доставка не отмечена через N минут после времени — сказать сотруднику и хозяевам в Telegram."""
        today = now.date()
        candidates = self.db.for_day(today - timedelta(days=1)) + self.db.for_day(today)
        for d in overdue_deliveries(candidates, now, late_minutes(), self._reminded):
            self._reminded.add(("late",) + reminder_key(d))
            text = describe_delivery(d)
            self.tray.showMessage("Доставка не отмечена", f"Время прошло, а доставка не отмечена: {text}",
                                  QSystemTrayIcon.Warning, 60_000)
            self.telegram.queue(f"⏰ Доставка не отмечена вовремя\n{text}"
                                + (f"\nТелефон: {d.phone}" if d.phone else ""), key=f"late:{d.id}:{d.day}:{d.time}")

    # ----- обновление экрана -----

    def selected_day(self) -> date:
        return to_date(self.calendar.selectedDate())

    def go_today(self):
        self.calendar.setSelectedDate(QDate.currentDate())

    def refresh_month(self):
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        self.legend.setText(
            f"<span style='background:{C.pending_day}'>&nbsp;&nbsp;&nbsp;&nbsp;</span> есть недоставленные"
            f" &nbsp; <span style='background:{C.done_day}'>&nbsp;&nbsp;&nbsp;&nbsp;</span> всё доставлено"
            f"<br><span style='color:{C.blue}'>○</span> поменять воду &nbsp; "
            f"<span style='color:{C.blue}'>●</span> воду поменяли &nbsp; "
            f"<span style='color:{C.red}'>●</span> не поменяли")

        weekend = QTextCharFormat()  # свой красный: стандартный не читается на тёмной теме
        weekend.setForeground(QColor(C.weekend))
        for weekday in (Qt.Saturday, Qt.Sunday):
            self.calendar.setWeekdayTextFormat(weekday, weekend)
        for qd in self._marked_days:
            self.calendar.setDateTextFormat(qd, QTextCharFormat())
        self._marked_days = []

        for day, (count, delivered) in self.db.counts_for_month(year, month).items():
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Bold)
            fmt.setBackground(QBrush(QColor(C.done_day if delivered == count else C.pending_day)))
            fmt.setToolTip(f"Доставок: {count}, доставлено: {delivered}")
            qd = QDate(day.year, day.month, day.day)
            self.calendar.setDateTextFormat(qd, fmt)
            self._marked_days.append(qd)

        self.refresh_shift_marks(year, month)
        self.month_box.setTitle(f"Итого за {MONTHS[month - 1].lower()} {year}")
        self.month_label.setText(
            totals_text(self.db.totals_for_month(year, month)).replace(" &nbsp;|&nbsp; ", "<br>")
        )

    def refresh_shift_marks(self, year: int, month: int):
        first = date(year, month, 1) - timedelta(days=7)  # видны и соседние недели
        last = date(year, month, 28) + timedelta(days=14)
        now, change, grace = datetime.now(), shift_change(), grace_minutes()
        show_problems = self.user is not None and self.user.is_admin
        names: dict[date, list[str]] = {}
        problems: dict[date, bool] = {}
        for s in self.db.shifts_between(first, last):
            names.setdefault(s.day, [])
            if short_name(s.user_name) not in names[s.day]:
                names[s.day].append(short_name(s.user_name))
            bad = (s.status(now, change, grace) in PROBLEM_STATUSES - {"closing"}
                   or bool(s.difference()) or bool(self.db.handover_difference(s)))
            problems[s.day] = problems.get(s.day, False) or (show_problems and bad)
        self.calendar.marks = {d: (", ".join(n), problems[d]) for d, n in names.items()}

        schedule = water_schedule()
        done = self.db.water_changes_between(first, last)
        self.calendar.water = {}
        day = first
        while day <= last:
            status = schedule.status(day, day in done, now, change)
            if status:
                self.calendar.water[day] = status
            day += timedelta(days=1)
        self.calendar.updateCells()

    def refresh_day(self):
        day = self.selected_day()
        self.day_title.setText(
            f"Доставки на {day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year}, {WEEKDAYS[day.weekday()]}"
        )
        self.deliveries = self.db.for_day(day)

        due_ids = {d.id for d in assembly_due(self.deliveries, datetime.now(), assembly_minutes())}
        self._loading = True
        self.table.setRowCount(len(self.deliveries))
        for row, d in enumerate(self.deliveries):
            done = QTableWidgetItem()
            done.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            done.setCheckState(Qt.Checked if d.delivered else Qt.Unchecked)
            done.setToolTip("Доставлено" if d.delivered else "Не доставлено")
            assembled = QTableWidgetItem()
            assembled.setFlags((Qt.ItemIsUserCheckable if not d.delivered else Qt.NoItemFlags)
                               | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            assembled.setCheckState(Qt.Checked if d.assembled else Qt.Unchecked)
            assembled.setToolTip(f"Собрано{' — ' + d.assembled_by_name if d.assembled_by_name else ''}"
                                 if d.assembled else "Не собрано")
            cells = {
                COL_ASSEMBLED: assembled,
                COL_DONE: done,
                COL_TIME: QTableWidgetItem(d.time),
                COL_ITEMS: QTableWidgetItem(("🗑 ждёт удаления · " if d.delete_requested_at else "") + d.items),
                COL_RECIPIENT: QTableWidgetItem("\n".join(x for x in (d.recipient, d.phone) if x)),
                COL_ADDRESS: QTableWidgetItem(d.address),
                COL_PRICE: QTableWidgetItem(money(d.price)),
                COL_COURIER: QTableWidgetItem(money(d.courier_pay)),
                COL_REST: QTableWidgetItem(money(d.remainder)),
                COL_CUSTOMER: QTableWidgetItem(
                    f"{d.source}: {d.customer_contact}" if d.source and d.customer_contact
                    else (d.customer_contact or d.source)),
                COL_MESSAGES: QTableWidgetItem(
                    " · ".join(SHORT[k] for k in KINDS if d.sent(k))),
                COL_WHO: QTableWidgetItem("\n".join(
                    x for x in (d.created_by_name, f"✓ {d.delivered_by_name}" if d.delivered and d.delivered_by_name
                                else "") if x)),
            }
            for col in (COL_PRICE, COL_COURIER, COL_REST):
                cells[col].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for col, item in cells.items():
                if d.delivered:
                    item.setBackground(QColor(C.delivered_bg))
                    item.setForeground(QColor(C.delivered_fg))
                self.table.setItem(row, col, item)
            if d.remainder < 0:
                cells[COL_REST].setForeground(QColor(C.red))
            if d.id in due_ids:  # скоро везти, а букет не собран
                for col in (COL_ASSEMBLED, COL_TIME):
                    cells[col].setBackground(QColor(C.pending_day))
                cells[COL_TIME].setForeground(QColor(C.red))
                cells[COL_ASSEMBLED].setToolTip("Скоро доставка — соберите букет и поставьте галочку")
            if d.delete_requested_at:
                cells[COL_ITEMS].setForeground(QColor(C.orange))
                cells[COL_ITEMS].setToolTip(
                    f"{d.delete_requested_by_name or 'Сотрудник'} попросил(а) удалить эту доставку "
                    f"({d.delete_requested_at:%d.%m %H:%M}). Ждёт решения администратора.")
        self._fit_columns()
        self._loading = False

        self.day_totals.setText(totals_text(self.db.totals_for_day(day)))
        self._update_buttons()

    def _fit_columns(self):
        """«Что доставить» и «Адрес» делят свободное место; на узком экране — не уже минимума (тогда прокрутка вбок)."""
        header = self.table.horizontalHeader()
        for col, limit in WRAP_UP_TO.items():
            wanted = max(self.table.sizeHintForColumn(col), header.sectionSizeHint(col))
            header.resizeSection(col, max(80, min(wanted, limit)))
        others = sum(header.sectionSize(c) for c in range(len(HEADERS)) if c not in MIN_WIDTH)
        free = self.table.viewport().width() - others
        items = max(MIN_WIDTH[COL_ITEMS], free * 3 // 5)
        header.resizeSection(COL_ITEMS, items)
        header.resizeSection(COL_ADDRESS, max(MIN_WIDTH[COL_ADDRESS], free - items))
        self.table.resizeRowsToContents()
        for row in range(self.table.rowCount()):  # немного воздуха: двухстрочные ячейки не слипаются
            self.table.setRowHeight(row, self.table.rowHeight(row) + 10)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_columns)  # после того как таблица получит новый размер

    def refresh_all(self):
        self.refresh_month()
        self.refresh_day()

    def _update_buttons(self):
        has = self.current_delivery() is not None
        self.edit_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        self.write_btn.setEnabled(has)
        d = self.current_delivery()
        self.keep_btn.setVisible(bool(self.user and self.user.is_admin and d and d.delete_requested_at))
        pending = bool(d and d.delete_requested_at)
        self.delete_btn.setText("🗑 Удалить" if not pending or (self.user and self.user.is_admin)
                                else "🗑 Ждёт решения")
        self.delete_btn.setEnabled(has and not (pending and self.user and not self.user.is_admin))

    # ----- действия -----

    def current_delivery(self) -> Delivery | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.deliveries) and self.table.selectionModel().hasSelection():
            return self.deliveries[row]
        return None

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() not in (COL_DONE, COL_ASSEMBLED):
            return
        d = self.deliveries[item.row()]
        if item.column() == COL_ASSEMBLED:
            self._set_assembled(d, item.checkState() == Qt.Checked)
            return
        delivered = item.checkState() == Qt.Checked
        self.db.set_delivered(d.id, delivered, self.user.id, datetime.now())
        self.audit("Доставка отмечена доставленной" if delivered else "Отметка «Доставлено» снята", describe_delivery(d))
        self.refresh_all()
        if delivered and d.customer_contact and not d.sent("delivered"):
            QTimer.singleShot(0, lambda: self._offer_delivered_message(d))

    def _offer_delivered_message(self, d: Delivery):
        answer = QMessageBox.question(
            self, "Сообщить заказчику",
            f"Написать заказчику ({d.source or d.customer_contact}), что букет доставлен?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.write_to_customer("delivered", d)

    def write_to_customer(self, kind: str, delivery: Delivery | None = None):
        d = delivery or self.current_delivery()
        if d is None:
            return
        text = render(load_templates()[kind], d)
        action = build_action(d.source, d.customer_contact, text)
        if action.copy_text:
            QApplication.clipboard().setText(text)
        if action.url:
            QDesktopServices.openUrl(QUrl(action.url))
        if action.note:
            QMessageBox.information(self, "Сообщение заказчику", action.note)
        self.db.mark_message(d.id, kind)
        self.refresh_day()

    def open_settings(self):
        if self.user is not None and self.user.is_admin:
            SettingsDialog(self).exec()

    def _on_double_click(self, item: QTableWidgetItem):
        if item.column() not in (COL_DONE, COL_ASSEMBLED):
            self.edit_delivery()

    def _open_dialog(self, delivery: Delivery) -> bool:
        dlg = DeliveryDialog(self, delivery)
        return dlg.exec() == DeliveryDialog.Accepted

    def _show_day(self, day: date):
        qd = QDate(day.year, day.month, day.day)
        if self.calendar.selectedDate() != qd:
            self.calendar.setSelectedDate(qd)  # сам вызовет refresh_day
        self.refresh_all()

    def add_delivery(self):
        d = Delivery(day=self.selected_day(), created_by=self.user.id, delivered_by=self.user.id)
        if self._open_dialog(d):
            if d.assembled or d.delivered:
                d.assembled_by, d.assembled_at = self.user.id, datetime.now()
            self.db.add(d)
            self.audit("Доставка создана", describe_delivery(d))
            self._show_day(d.day)

    def edit_delivery(self):
        d = self.current_delivery()
        if d is None:
            return
        was_delivered, was_assembled = d.delivered, d.assembled
        before = copy.copy(d)  # окно правит доставку на месте — запоминаем, что было
        if self._open_dialog(d):
            if d.delivered and not was_delivered:
                d.delivered_by = self.user.id
            if (d.assembled or d.delivered) and not was_assembled:
                d.assembled_by, d.assembled_at = self.user.id, datetime.now()
            self.db.update(d)
            changes = delivery_changes(before, d)
            if changes:
                self.audit("Доставка изменена", f"{describe_delivery(before)}: {changes}")
            self._show_day(d.day)
        else:
            self.refresh_day()  # отменили — вернуть исходные значения

    def delete_delivery(self):
        d = self.current_delivery()
        if d is None:
            return
        what = d.items.splitlines()[0] if d.items else d.address
        if not self.user.is_admin:
            self._request_delete(d, what)
            return
        approving = d.delete_requested_at is not None
        answer = QMessageBox.question(
            self, "Удалить доставку",
            (f"{d.delete_requested_by_name or 'Сотрудник'} просил(а) удалить доставку «{what}». Удалить?"
             if approving else f"Удалить доставку «{what}»?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.db.delete(d.id)
            self.audit("Удаление доставки подтверждено" if approving else "Доставка удалена", describe_delivery(d))
            self.refresh_all()

    def _request_delete(self, d: Delivery, what: str):
        """Сотрудник не удаляет сам: доставка остаётся, а администратор решает в Telegram (или в программе)."""
        if d.delete_requested_at is not None:
            QMessageBox.information(self, "Удаление доставки", "Запрос уже отправлен администратору, ждём решения.")
            return
        answer = QMessageBox.question(
            self, "Запрос на удаление",
            f"Удалять доставки может только администратор.\n\nОтправить ему запрос на удаление «{what}»? "
            "Доставка останется в списке, пока он не решит.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return
        asked_at = datetime.now()
        self.db.request_delete(d.id, self.user.id, asked_at)
        self.audit("Запрос на удаление доставки", describe_delivery(d))
        who = self.user.name or self.user.login
        self.telegram.queue(
            f"🗑 {who} просит удалить доставку\n{describe_delivery(d)}\n\n"
            f"Удалить: /del_{d.id}\nОставить: /keep_{d.id}", key=f"del:{d.id}:{asked_at:%Y%m%d%H%M%S}")
        configured = telegram.configured()
        QMessageBox.information(
            self, "Запрос отправлен",
            "Администратор получил запрос в Telegram и решит." if configured
            else "Администратор увидит запрос в программе и решит.")
        self.refresh_all()

    def keep_delivery(self):
        """Админ отклоняет запрос на удаление (кнопка «Оставить»)."""
        d = self.current_delivery()
        if d is None or d.delete_requested_at is None:
            return
        self.db.cancel_delete_request(d.id)
        self.audit("Удаление доставки отклонено", f"{describe_delivery(d)}; просил(а) {d.delete_requested_by_name or '—'}")
        self.refresh_all()

    def _penalty_changed(self, p, set_: bool, who: str):
        """Админ поставил или снял санкцию в Telegram: обновить экран, сказать сотрудникам, сообщить второму хозяину."""
        self.refresh_all()
        what = f"{p.title} −{money(p.amount)} ({p.user_name or '—'}, смена {p.shift_day:%d.%m})"
        self.tray.showMessage("Штрафная санкция" if set_ else "Санкция снята", what, QSystemTrayIcon.Warning, 60_000)
        verb = "поставил(а) санкцию" if set_ else "снял(а) санкцию"
        self.telegram.queue(f"ℹ️ {who or 'Администратор'} {verb}: {what}",
                            key=f"pen:{p.id}:{int(set_)}:{datetime.now():%Y%m%d%H%M%S}",
                            except_chat=self._current_bot_chat)

    # ----- сборка букетов -----

    def _check_assembly(self, now: datetime):
        """Букет не собран, а до доставки час (по настройке) — окно поверх всех, пока не соберут."""
        today = now.date()
        candidates = (self.db.for_day(today - timedelta(days=1)) + self.db.for_day(today)
                      + self.db.for_day(today + timedelta(days=1)))
        due = assembly_due(candidates, now, assembly_minutes())
        self._assembly_first = {d.id: self._assembly_first.get(d.id, now) for d in due}
        for d in assembly_escalations(candidates, now, assembly_alert_minutes(), self._reminded):
            if now - self._assembly_first.get(d.id, now) < SNOOZE:
                continue  # срочный заказ только что приняли — сначала напомнить флористу, хозяевам потом
            self._reminded.add(("assembly",) + reminder_key(d))
            self.telegram.queue(f"💐 Букет ещё не собран, а доставка скоро\n{describe_delivery(d)}",
                                key=f"assembly:{d.id}:{d.day}:{d.time}")
        if not due:
            self.assembly.hide()
            self._assembly_seen.clear()
            return
        new = {d.id for d in due} - self._assembly_seen
        snoozed = self._assembly_snooze_until is not None and now < self._assembly_snooze_until
        if self.assembly.isVisible() or new or not snoozed:
            self.assembly.show_items(due, now)
        if new or (not snoozed and not self.assembly.isVisible()):
            self._assembly_seen |= {d.id for d in due}
            self._assembly_snooze_until = None
            self.assembly.show()
            self.assembly.raise_()
            self.assembly.activateWindow()
            QApplication.beep()
            QApplication.alert(self.assembly)
            if new:
                first = next(d for d in due if d.id in new)
                self.tray.showMessage("Букет не собран!", f"{first.time} — {describe_delivery(first)}",
                                      QSystemTrayIcon.Critical, 60_000)

    def _snooze_assembly(self):
        self._assembly_snooze_until = datetime.now() + SNOOZE

    def _assembled_from_reminder(self, delivery_id: int):
        d = self.db.get_delivery(delivery_id)
        if d is not None:
            self._set_assembled(d, True)
        self._check_assembly(datetime.now())

    def _delivered_from_reminder(self, delivery_id: int):
        d = self.db.get_delivery(delivery_id)
        if d is not None:
            self.db.set_delivered(d.id, True, self.user.id if self.user else None, datetime.now())
            self.audit("Доставка отмечена доставленной", describe_delivery(d))
            self.refresh_all()
        self._check_assembly(datetime.now())

    def _set_assembled(self, d: Delivery, assembled: bool):
        self.db.set_assembled(d.id, assembled, self.user.id if self.user else None, datetime.now())
        self.audit("Букет собран" if assembled else "Отметка «Собрано» снята", describe_delivery(d))
        self.refresh_all()

    # ----- план смены -----

    def open_plan(self):
        shift = self.shift or next(iter(self.active_shifts()), None)
        if shift is None:
            QMessageBox.information(self, "План на смену", "Сейчас никто не на смене.")
            return
        ShiftPlanDialog(self, self.db, shift, shift_change(), water_schedule(), assembly_minutes()).exec()
        QTimer.singleShot(0, self.show_unread_announcements)  # пока был открыт план, объявление ждало

    # ----- копия базы в Telegram -----

    @staticmethod
    def _backup_caption() -> str:
        return (f"💾 Копия базы AloFlorist, {datetime.now():%d.%m.%Y %H:%M}.\n"
                "Восстановить: ⚙ Настройки → Копии → «Восстановить из копии» и выбрать этот файл. "
                "Никому не пересылайте: в нём телефоны клиентов.")

    def _maybe_send_backup(self, now: datetime):
        """Раз в сутки — после утренней пересменки и сдачи смены — свежая копия хозяевам в Telegram."""
        if not (backup_to_telegram() and telegram.configured()) or self.db.path == ":memory:":
            return
        change, day = shift_change(), business_day(now, shift_change())
        if str(self.settings.value("tg_backup_day", "")) == day.isoformat():
            return
        if now < window(day, change)[0] + timedelta(minutes=grace_minutes() + 5):
            return  # ночная смена ещё сдаётся — пусть в копию попадут её итоги
        if self._backup_attempt is not None and now - self._backup_attempt < timedelta(hours=1):
            return  # не получилось недавно (нет интернета) — не чаще раза в час
        self._backup_attempt = now
        last, fresh = last_backup(), backup.latest(backup.local_dir(self.db.path))
        if fresh is None or last is None or last < window(day, change)[0]:  # свежей (после пересменки) нет
            result = self.run_backup()
            fresh = result.local if result is not None else None
        if fresh is not None:
            self._backup_day_pending = day.isoformat()
            self.telegram.send_file(backup.zip_copy(fresh, now), self._backup_caption(), "daily")

    def _backup_file_sent(self, tag: str, errors: list):
        if tag == "daily" and len(errors) < len(telegram.settings()["chats"]):  # хотя бы одному дошло
            self.settings.setValue("tg_backup_day", self._backup_day_pending)
        if errors:
            self.db.log_action(None, "Копия в Telegram не отправлена", "; ".join(errors), who="программа")

    def _check_reports(self, now: datetime):
        """Утренний, недельный и месячный отчёты хозяевам в Telegram — по одному разу за период."""
        if not telegram.configured():
            return
        for report in due_reports(now, shift_change(), report_settings(), grace_minutes()):
            if report.key in self._reports_done:
                continue
            self._reports_done.add(report.key)  # даже если не получилось — не пытаться каждые 30 секунд
            try:
                text = report_text(report, self._bot_context())
            except Exception:  # noqa: BLE001 — отчёт не должен мешать напоминаниям о доставках
                continue
            for i, part in enumerate(chunk(text)):
                self.telegram.queue(part, key=report.key if i == 0 else f"{report.key}:{i}")

    def _writeoff_alert(self, shift) -> str:
        """Тревога, если за смену списали заметно больше обычного (среднего по последним сменам магазина)."""
        factor, minimum = writeoff_alert()
        if factor <= 0 or shift.writeoff < max(minimum, 1):
            return ""
        usual = self.db.recent_writeoff_average(shift)
        if usual is None:
            return ""
        if shift.writeoff >= usual * factor:
            return (f"❗ Списание необычно большое: {money(shift.writeoff)} при обычных "
                    f"~{money(round(usual))} за смену")
        return ""

    def _dispute_made(self, p) -> bool:
        """Сотрудник оспорил санкцию: хозяевам — в Telegram, в журнал — для админа в программе.
        True — ушло в Telegram."""
        self.audit("Санкция оспорена", f"{p.user_name or '—'}, {p.title}, смена {p.shift_day:%d.%m}: {p.dispute_text}")
        self.telegram.queue(telegram.dispute_text(p), key=f"dispute:{p.id}")
        return telegram.configured()

    def _penalty_in_app(self, p, change: str):
        """Санкцию поставили, сняли или оставили после спора в программе — то же, что из Telegram."""
        who = (self.user.name or self.user.login) if self.user else ""
        if change == "kept":
            self._dispute_kept(p, who)
        else:
            self._penalty_changed(p, change == "set", who)

    def _dispute_kept(self, p, who: str):
        """Хозяин в Telegram оставил оспоренную санкцию: сотруднику — на экран, второму хозяину — в Telegram."""
        what = f"{p.title} ({p.user_name or '—'}, смена {p.shift_day:%d.%m})"
        self.tray.showMessage("Спор по санкции решён", f"Санкция остаётся: {what}", QSystemTrayIcon.Information, 60_000)
        self.telegram.queue(f"ℹ️ {who or 'Администратор'} оставил(а) санкцию после спора: {what}",
                            key=f"pok:{p.id}", except_chat=self._current_bot_chat)

    def _writeoff_note(self, shift) -> str:
        """Списания по магазину за месяц смены — для итогов в Telegram."""
        first = shift.day.replace(day=1)
        last = shift.day.replace(day=calendar.monthrange(shift.day.year, shift.day.month)[1])
        rules = salary_rules()
        percent = shop_writeoff(self.db.shifts_between(first, last)).percent(rules.writeoff_base)
        note = f"за месяц по магазину {pct(percent)}"
        if rules.max_writeoff_percent > 0 and percent >= rules.max_writeoff_percent:
            note += f" ❗ для премии нужно меньше {pct(rules.max_writeoff_percent, None)}"
        return note

    def _delete_decided(self, d: Delivery, approved: bool, who: str):
        """Решение пришло из Telegram: обновить экран, сказать сотрудникам, сообщить второму хозяину."""
        self.refresh_all()
        verdict = "подтверждено" if approved else "отклонено"
        self.tray.showMessage(f"Удаление доставки {verdict}", describe_delivery(d), QSystemTrayIcon.Information, 30_000)
        self.telegram.queue(f"ℹ️ {who or 'Администратор'}: удаление {verdict} — {describe_delivery(d)}",
                            key=f"deldone:{d.id}:{verdict}:{datetime.now():%Y%m%d%H%M%S}",
                            except_chat=self._current_bot_chat)
