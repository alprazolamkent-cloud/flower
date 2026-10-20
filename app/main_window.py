"""Главное окно: календарь на месяц и список доставок на выбранный день."""

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
from .settings_dialog import SettingsDialog, grace_minutes, lead_minutes, shift_change, water_schedule
from .shift_dialogs import CabinetDialog, CashOpDialog, CloseShiftDialog, StartShiftDialog, fmt_dt
from .shifts import PROBLEM_STATUSES, REMIND_BEFORE_MINUTES, Shift, business_day, short_name, window
from .templates_dialog import load_templates
from .update_ui import UpdateController
from .reminders import due_reminders, reminder_key
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

HEADERS = ["✓", "Время", "Что доставить", "Кому", "Телефон", "Адрес",
           "Взяли", "Курьеру", "Осталось", "Заказчик", "Писали", "Добавил", "Отметил"]
COL_DONE, COL_TIME, COL_ITEMS, COL_RECIPIENT, COL_PHONE, COL_ADDRESS, \
    COL_PRICE, COL_COURIER, COL_REST, COL_CUSTOMER, COL_MESSAGES, \
    COL_CREATED_BY, COL_DELIVERED_BY = range(len(HEADERS))

REMINDER_CHECK_MS = 30_000

DELIVERED_BG = QColor("#e3f4e4")
PENDING_DAY_BG = QColor("#ffe0b2")  # в этот день есть недоставленные
DONE_DAY_BG = QColor("#c8e6c9")  # всё доставлено
WATER_COLORS = {"planned": "#1e88e5", "done": "#1e88e5", "missed": "#c62828"}
WATER_REMIND_AFTER = timedelta(hours=4)  # через сколько после начала смены напомнить про воду


def to_date(qd: QDate) -> date:
    return date(qd.year(), qd.month(), qd.day())


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
            color = QColor("white" if qdate == self.selectedDate() else WATER_COLORS[water])
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
            painter.setPen(QColor("#c62828" if problem else "#546e7a"))
        area = rect.adjusted(2, 0, -2, -1)
        text = painter.fontMetrics().elidedText(names, Qt.ElideRight, area.width())
        painter.drawText(area, Qt.AlignHCenter | Qt.AlignBottom, text)
        painter.restore()


def totals_text(t: Totals) -> str:
    return (
        f"Доставок: <b>{t.count}</b> (доставлено {t.delivered})"
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
        self._really_quit = False
        self._tray_hint_shown = False
        self._last_reminded_day: date | None = None
        self.shift: Shift | None = None  # несданная смена того, кто сейчас вошёл
        self._shift_reminded: set[int] = set()
        self._shift_ended: set[int] = set()
        self._water_reminded: set[date] = set()
        self.settings = QSettings("FlowerDeliveries", "FlowerDeliveries")

        self.setWindowTitle("Доставки цветов")
        self.setWindowIcon(flower_icon())
        self.resize(1440, 720)

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

        legend = QLabel(
            "<span style='background:#ffe0b2'>&nbsp;&nbsp;&nbsp;&nbsp;</span> есть недоставленные"
            " &nbsp; <span style='background:#c8e6c9'>&nbsp;&nbsp;&nbsp;&nbsp;</span> всё доставлено"
            "<br><span style='color:#1e88e5'>○</span> поменять воду &nbsp; "
            "<span style='color:#1e88e5'>●</span> воду поменяли &nbsp; "
            "<span style='color:#c62828'>●</span> не поменяли"
        )
        left.addWidget(legend)

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
        for b in (self.shift_btn, self.cash_btn, cabinet_btn):
            shift_buttons.addWidget(b)
        shift_layout.addLayout(shift_buttons)
        water_row = QHBoxLayout()
        self.water_label = QLabel()
        self.water_btn = QPushButton("💧 Вода поменяна")
        self.water_btn.setToolTip("Отметить, что воду в вёдрах поменяли")
        self.water_btn.clicked.connect(self.mark_water)
        water_row.addWidget(self.water_label, 1)
        water_row.addWidget(self.water_btn)
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

        top = QHBoxLayout()
        top.addWidget(self.day_title, 1)
        self.user_label = QLabel()
        top.addWidget(self.user_label)
        self.settings_btn = QPushButton("⚙ Настройки")
        self.settings_btn.clicked.connect(self.open_settings)
        password_btn = QPushButton("🔑 Сменить пароль")
        password_btn.clicked.connect(self.change_password)
        logout_btn = QPushButton("🚪 Выйти")
        logout_btn.setToolTip("Выйти из профиля, чтобы вошёл другой сотрудник. Смена при этом не закрывается.")
        logout_btn.clicked.connect(self.logout)
        for b in (self.settings_btn, password_btn, logout_btn):
            top.addWidget(b)
        right.addLayout(top)

        buttons = QHBoxLayout()
        add_btn = QPushButton("➕ Добавить доставку")
        add_btn.clicked.connect(self.add_delivery)
        self.edit_btn = QPushButton("✏️ Изменить")
        self.edit_btn.clicked.connect(self.edit_delivery)
        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.clicked.connect(self.delete_delivery)
        self.write_btn = QPushButton("💬 Написать заказчику")
        self.write_btn.setToolTip("Сообщение заказчику в его мессенджер")
        write_menu = QMenu(self.write_btn)
        for kind, title in KINDS.items():
            action = write_menu.addAction(title)
            action.triggered.connect(lambda _=False, k=kind: self.write_to_customer(k))
        self.write_btn.setMenu(write_menu)
        for b in (add_btn, self.edit_btn, self.delete_btn, self.write_btn):
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
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_ITEMS, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_ADDRESS, QHeaderView.Stretch)
        self.table.horizontalHeaderItem(COL_DONE).setToolTip("Доставлено")
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        right.addWidget(self.table, 1)

        self.day_totals = QLabel()
        right.addWidget(self.day_totals)

        hint = QLabel("Галочка в первом столбце — «Доставлено». Двойной щелчок — изменить.")
        hint.setStyleSheet("color: gray")
        right.addWidget(hint)

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
        self.tray = QSystemTrayIcon(flower_icon(), self)
        self.tray.setToolTip("Доставки цветов")
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
        """Спросить логин и пароль (или создать первого админа). True — вошли."""
        if not self.db.has_users():
            dlg = UserFormDialog(self, self.db, first_admin=True)
        else:
            dlg = LoginDialog(self, self.db, self._on_shift_note())
        if dlg.exec() != QDialog.Accepted:
            return False
        self.set_user(dlg.user)
        if not self._shift_flow():
            self.set_user(None)
            return False
        self.update_shift_box()
        return True

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
                if CloseShiftDialog(self, self.db, pending, change, grace, water_schedule()).exec() != QDialog.Accepted:
                    if self.user.is_admin:
                        self.shift = pending
                        return True
                    return False
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
        dlg = StartShiftDialog(self, self.db, self.user, change)
        if dlg.exec() != QDialog.Accepted:
            return False
        self.shift = dlg.shift
        return True

    def update_shift_box(self):
        self._update_water_row()
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
            self.water_label.setText(f"<span style='color:#1e88e5'>💧 Вода поменяна</span> — "
                                     f"{who or '—'}, {when:%H:%M}")
            self.water_btn.setVisible(False)
        else:
            self.water_label.setText("<b style='color:#1e88e5'>💧 Сегодня день смены воды</b>")
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

    def start_shift(self):
        others = [s.user_name or "—" for s in self.active_shifts() if s.user_id != self.user.id]
        if others and QMessageBox.question(
                self, "Начать смену",
                f"Сейчас на смене: {', '.join(others)}. Ваша смена будет отдельной, их смена не закроется.\n\n"
                "Начать свою смену?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        dlg = StartShiftDialog(self, self.db, self.user, shift_change())
        if dlg.exec() == QDialog.Accepted:
            self.shift = dlg.shift
            self.update_shift_box()
            self.refresh_month()

    def close_shift(self):
        change, grace = shift_change(), grace_minutes()
        if not self.shift.can_close(datetime.now(), change, grace):
            QMessageBox.warning(self, "Сдача смены", "Время на сдачу этой смены уже вышло.")
            self.shift = None
            self.update_shift_box()
            return
        if CloseShiftDialog(self, self.db, self.shift, change, grace, water_schedule()).exec() != QDialog.Accepted:
            return
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
                      self.calendar.yearShown(), self.calendar.monthShown(), water_schedule()).exec()

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
            self.user_label.setText("")
            self.update_shift_box()
            return
        role = "админ" if user.is_admin else "сотрудник"
        self.user_label.setText(f"Вы вошли: <b>{user.name or user.login}</b> ({role})")
        self.settings_btn.setVisible(user.is_admin)
        self.update_shift_box()

    def logout(self):
        self.set_user(None)
        self.hide()
        if self.login():
            self.show_window()

    def change_password(self):
        if PasswordDialog(self, self.db, self.user, need_old=True).exec() == QDialog.Accepted:
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

    def quit_app(self):
        self._really_quit = True
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
                "Доставки цветов",
                "Программа работает в фоне и напомнит о доставках. "
                "Выход — правой кнопкой по значку у часов.",
                QSystemTrayIcon.Information, 5000,
            )

    def check_reminders(self):
        self.check_shift()
        now = datetime.now()
        today = now.date()
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

    # ----- обновление экрана -----

    def selected_day(self) -> date:
        return to_date(self.calendar.selectedDate())

    def go_today(self):
        self.calendar.setSelectedDate(QDate.currentDate())

    def refresh_month(self):
        year, month = self.calendar.yearShown(), self.calendar.monthShown()

        for qd in self._marked_days:
            self.calendar.setDateTextFormat(qd, QTextCharFormat())
        self._marked_days = []

        for day, (count, delivered) in self.db.counts_for_month(year, month).items():
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Bold)
            fmt.setBackground(QBrush(DONE_DAY_BG if delivered == count else PENDING_DAY_BG))
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

        self._loading = True
        self.table.setRowCount(len(self.deliveries))
        for row, d in enumerate(self.deliveries):
            done = QTableWidgetItem()
            done.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            done.setCheckState(Qt.Checked if d.delivered else Qt.Unchecked)
            done.setToolTip("Доставлено" if d.delivered else "Не доставлено")
            cells = {
                COL_DONE: done,
                COL_TIME: QTableWidgetItem(d.time),
                COL_ITEMS: QTableWidgetItem(d.items),
                COL_RECIPIENT: QTableWidgetItem(d.recipient),
                COL_PHONE: QTableWidgetItem(d.phone),
                COL_ADDRESS: QTableWidgetItem(d.address),
                COL_PRICE: QTableWidgetItem(money(d.price)),
                COL_COURIER: QTableWidgetItem(money(d.courier_pay)),
                COL_REST: QTableWidgetItem(money(d.remainder)),
                COL_CUSTOMER: QTableWidgetItem(
                    f"{d.source}: {d.customer_contact}" if d.source and d.customer_contact
                    else (d.customer_contact or d.source)),
                COL_MESSAGES: QTableWidgetItem(
                    " · ".join(SHORT[k] for k in KINDS if d.sent(k))),
                COL_CREATED_BY: QTableWidgetItem(d.created_by_name),
                COL_DELIVERED_BY: QTableWidgetItem(d.delivered_by_name if d.delivered else ""),
            }
            for col in (COL_PRICE, COL_COURIER, COL_REST):
                cells[col].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if d.remainder < 0:
                cells[COL_REST].setForeground(QColor("#c62828"))
            for col, item in cells.items():
                if d.delivered:
                    item.setBackground(DELIVERED_BG)
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()
        self._loading = False

        self.day_totals.setText(totals_text(self.db.totals_for_day(day)))
        self._update_buttons()

    def refresh_all(self):
        self.refresh_month()
        self.refresh_day()

    def _update_buttons(self):
        has = self.current_delivery() is not None
        self.edit_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        self.write_btn.setEnabled(has)

    # ----- действия -----

    def current_delivery(self) -> Delivery | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.deliveries) and self.table.selectionModel().hasSelection():
            return self.deliveries[row]
        return None

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() != COL_DONE:
            return
        d = self.deliveries[item.row()]
        delivered = item.checkState() == Qt.Checked
        self.db.set_delivered(d.id, delivered, self.user.id)
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
        if item.column() != COL_DONE:
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
            self.db.add(d)
            self._show_day(d.day)

    def edit_delivery(self):
        d = self.current_delivery()
        if d is None:
            return
        was_delivered = d.delivered
        if self._open_dialog(d):
            if d.delivered and not was_delivered:
                d.delivered_by = self.user.id
            self.db.update(d)
            self._show_day(d.day)
        else:
            self.refresh_day()  # отменили — вернуть исходные значения

    def delete_delivery(self):
        d = self.current_delivery()
        if d is None:
            return
        what = d.items.splitlines()[0] if d.items else d.address
        answer = QMessageBox.question(
            self, "Удалить доставку",
            f"Удалить доставку «{what}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.db.delete(d.id)
            self.refresh_all()
