"""Главное окно: календарь на месяц и список доставок на выбранный день."""

import sys
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QLocale, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QDesktopServices, QColor, QFont, QKeySequence, QShortcut, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QCheckBox, QDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSpinBox, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .db import Database, Delivery, Totals, User
from .delivery_dialog import DeliveryDialog, money
from .icon import flower_icon
from .messaging import KINDS, SHORT, build_action, render
from .templates_dialog import TemplatesDialog, load_templates
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
DEFAULT_LEAD_MINUTES = 20
AUTOSTART_KEY = r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "FlowerDeliveries"

DELIVERED_BG = QColor("#e3f4e4")
PENDING_DAY_BG = QColor("#ffe0b2")  # в этот день есть недоставленные
DONE_DAY_BG = QColor("#c8e6c9")  # всё доставлено


def to_date(qd: QDate) -> date:
    return date(qd.year(), qd.month(), qd.day())


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

        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale(QLocale.Russian, QLocale.Russia))
        self.calendar.setFirstDayOfWeek(Qt.Monday)
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setMinimumSize(420, 340)
        self.calendar.selectionChanged.connect(self.refresh_day)
        self.calendar.currentPageChanged.connect(lambda *_: self.refresh_month())
        left.addWidget(self.calendar)

        legend = QLabel(
            "<span style='background:#ffe0b2'>&nbsp;&nbsp;&nbsp;&nbsp;</span> есть недоставленные"
            " &nbsp; <span style='background:#c8e6c9'>&nbsp;&nbsp;&nbsp;&nbsp;</span> всё доставлено"
        )
        left.addWidget(legend)

        today_btn = QPushButton("Сегодня")
        today_btn.clicked.connect(self.go_today)
        left.addWidget(today_btn)

        self.month_box = QGroupBox()
        month_layout = QVBoxLayout(self.month_box)
        self.month_label = QLabel()
        self.month_label.setWordWrap(True)
        month_layout.addWidget(self.month_label)
        left.addWidget(self.month_box)
        left.addWidget(self._build_reminder_box())
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
        self.users_btn = QPushButton("👥 Сотрудники")
        self.users_btn.clicked.connect(self.manage_users)
        password_btn = QPushButton("🔑 Сменить пароль")
        password_btn.clicked.connect(self.change_password)
        logout_btn = QPushButton("🚪 Выйти")
        logout_btn.setToolTip("Выйти из профиля, чтобы вошёл другой сотрудник")
        logout_btn.clicked.connect(self.logout)
        for b in (self.users_btn, password_btn, logout_btn):
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
        write_menu.addSeparator()
        self.templates_action = write_menu.addAction("✏️ Изменить тексты сообщений…")
        self.templates_action.triggered.connect(self.edit_templates)
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

    def _build_reminder_box(self) -> QGroupBox:
        box = QGroupBox("Напоминания")
        form = QFormLayout(box)

        self.lead_box = QSpinBox()
        self.lead_box.setRange(1, 180)
        self.lead_box.setSuffix(" мин")
        self.lead_box.setValue(int(self.settings.value("lead_minutes", DEFAULT_LEAD_MINUTES)))
        self.lead_box.valueChanged.connect(self._on_lead_changed)
        form.addRow("Напоминать за:", self.lead_box)

        self.autostart_check = QCheckBox("Запускать вместе с Windows")
        self.autostart_check.setVisible(sys.platform == "win32")
        self.autostart_check.setChecked(self._autostart_enabled())
        self.autostart_check.toggled.connect(self._set_autostart)
        form.addRow(self.autostart_check)

        hint = QLabel("Напоминания приходят, пока программа запущена. "
                      "Крестик сворачивает её к часам, выход — через значок у часов.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        form.addRow(hint)
        return box

    def _on_lead_changed(self, value: int):
        self.settings.setValue("lead_minutes", value)
        self.check_reminders()

    def _autostart_settings(self) -> QSettings:
        return QSettings(AUTOSTART_KEY, QSettings.NativeFormat)

    def _autostart_enabled(self) -> bool:
        if sys.platform != "win32":
            return False
        return self._autostart_settings().contains(AUTOSTART_NAME)

    def _set_autostart(self, enabled: bool):
        run = self._autostart_settings()
        if enabled:
            run.setValue(AUTOSTART_NAME, f'"{sys.executable}" --minimized'
                         if getattr(sys, "frozen", False) else f'"{sys.executable}" "{sys.argv[0]}" --minimized')
        else:
            run.remove(AUTOSTART_NAME)

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
            dlg = LoginDialog(self, self.db)
        if dlg.exec() != QDialog.Accepted:
            return False
        self.set_user(dlg.user)
        return True

    def set_user(self, user: User | None):
        self.user = user
        if user is None:
            self.user_label.setText("")
            return
        role = "админ" if user.is_admin else "сотрудник"
        self.user_label.setText(f"Вы вошли: <b>{user.name or user.login}</b> ({role})")
        self.users_btn.setVisible(user.is_admin)
        self.templates_action.setVisible(user.is_admin)

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
        now = datetime.now()
        today = now.date()
        deliveries = self.db.for_day(today) + self.db.for_day(today + timedelta(days=1))
        due = due_reminders(deliveries, now, self.lead_box.value(), self._reminded)
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

        self.month_box.setTitle(f"Итого за {MONTHS[month - 1].lower()} {year}")
        self.month_label.setText(
            totals_text(self.db.totals_for_month(year, month)).replace(" &nbsp;|&nbsp; ", "<br>")
        )

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

    def edit_templates(self):
        TemplatesDialog(self).exec()

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
