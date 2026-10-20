"""Окно настроек — только для администратора."""

import os
import sys
from datetime import date, datetime, time

from PySide6.QtCore import QDate, QSettings, QTime, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QTimeEdit, QVBoxLayout, QWidget,
)

from . import autostart, backup, telegram
from .db import UserError
from .duties_dialog import DutiesEditor
from .salary import SalaryRules
from .shifts import DEFAULT_CHANGE, DEFAULT_GRACE_MINUTES, WaterSchedule
from .templates_dialog import TemplatesDialog

DEFAULT_LEAD_MINUTES = 20


def _settings() -> QSettings:
    return QSettings("FlowerDeliveries", "FlowerDeliveries")


def lead_minutes() -> int:
    """За сколько минут до доставки напоминать."""
    return int(_settings().value("lead_minutes", DEFAULT_LEAD_MINUTES))


def shift_change() -> time:
    """Время пересменки."""
    try:
        h, m = str(_settings().value("shift_change", DEFAULT_CHANGE.strftime("%H:%M"))).split(":")
        return time(int(h), int(m))
    except ValueError:
        return DEFAULT_CHANGE


def grace_minutes() -> int:
    """Сколько минут после пересменки ещё можно сдать смену."""
    return int(_settings().value("grace_minutes", DEFAULT_GRACE_MINUTES))


def water_schedule() -> WaterSchedule:
    """График смены воды. Пока админ его не задал — считаем от сегодняшней смены."""
    settings = _settings()
    try:
        anchor = date.fromisoformat(str(settings.value("water_anchor", "")))
    except ValueError:
        anchor = date.today()
        settings.setValue("water_anchor", anchor.isoformat())
    return WaterSchedule(anchor, int(settings.value("water_every", 2)))


def salary_rules() -> SalaryRules:
    s = _settings()
    return SalaryRules(int(s.value("salary_rate", 0)), float(s.value("salary_percent", 0)),
                       str(s.value("salary_deduct", "true")) == "true")


def backup_dir() -> str:
    return str(_settings().value("backup_dir", ""))


def backup_keep() -> int:
    return int(_settings().value("backup_keep", backup.DEFAULT_KEEP))


def last_backup() -> datetime | None:
    try:
        return datetime.fromisoformat(str(_settings().value("last_backup", "")))
    except ValueError:
        return None


def set_last_backup(moment: datetime):
    _settings().setValue("last_backup", moment.isoformat(timespec="seconds"))


def _gray_label(text: str) -> QLabel:
    """Подсказка с переносами строк, расставленными вручную (автоперенос в форме обрезается)."""
    label = QLabel(text)
    label.setStyleSheet("color: gray")
    return label


def _bool(value: bool) -> str:
    return "true" if value else "false"


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.main = window
        self.db = window.db
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._main_tab(), "Основное")
        self.tabs.addTab(self._shop_tab(), "Магазин")
        self.tabs.addTab(self._salary_tab(), "Зарплата")
        self.tabs.addTab(self._telegram_tab(), "Telegram")
        self.tabs.addTab(self._backup_tab(), "Резервные копии")
        self.tabs.addTab(self._program_tab(), "Программа")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ----- вкладки -----

    def _main_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        reminders = QGroupBox("Напоминания о доставках")
        form = QFormLayout(reminders)
        self.lead_box = QSpinBox()
        self.lead_box.setRange(1, 180)
        self.lead_box.setSuffix(" мин")
        self.lead_box.setValue(lead_minutes())
        form.addRow("Напоминать за:", self.lead_box)
        layout.addWidget(reminders)

        shifts = QGroupBox("Смены")
        shift_form = QFormLayout(shifts)
        self.change_edit = QTimeEdit()
        self.change_edit.setDisplayFormat("HH:mm")
        change = shift_change()
        self.change_edit.setTime(QTime(change.hour, change.minute))
        shift_form.addRow("Пересменка в:", self.change_edit)
        self.grace_box = QSpinBox()
        self.grace_box.setRange(0, 180)
        self.grace_box.setSuffix(" мин")
        self.grace_box.setValue(grace_minutes())
        shift_form.addRow("Сдать после пересменки можно ещё:", self.grace_box)
        shift_form.addRow(_gray_label("За 30 минут до пересменки — напоминание сдать смену.\n"
                                      "В пересменку сотрудника выводит из программы.\n"
                                      "Сданная позже смена — «с опозданием», не сданная — «не сдана»."))
        layout.addWidget(shifts)

        water = QGroupBox("Смена воды")
        water_form = QFormLayout(water)
        schedule = water_schedule()
        self.water_every = QSpinBox()
        self.water_every.setRange(1, 7)
        self.water_every.setPrefix("раз в ")
        self.water_every.setSuffix(" дн.")
        self.water_every.setValue(schedule.every)
        water_form.addRow("Менять воду:", self.water_every)
        self.water_anchor = QDateEdit()
        self.water_anchor.setCalendarPopup(True)
        self.water_anchor.setDisplayFormat("dd.MM.yyyy")
        self.water_anchor.setDate(QDate(schedule.anchor.year, schedule.anchor.month, schedule.anchor.day))
        water_form.addRow("Один из дней замены:", self.water_anchor)
        water_form.addRow(_gray_label("От этого дня считаются следующие дни замены.\n"
                                      "Если график сбился — укажите день, когда воду меняли последний раз."))
        layout.addWidget(water)
        layout.addStretch(1)
        return page

    def _shop_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        for text, tip, slot in (
                ("👥 Сотрудники…", "Добавить, удалить, сменить пароль, назначить администратором",
                 self._open_users),
                ("📋 Обязанности смены…", "Список дел, которые сотрудник отмечает за смену",
                 self._open_duties),
                ("💬 Тексты сообщений заказчику…", "«Курьер выехал», «доставлено», «просьба об отзыве»",
                 lambda: TemplatesDialog(self).exec())):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setMinimumHeight(34)
            button.clicked.connect(slot)
            layout.addWidget(button)
        layout.addStretch(1)
        return page

    def _salary_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Как считать зарплату флориста")
        form = QFormLayout(box)
        rules = salary_rules()
        self.rate_box = QSpinBox()
        self.rate_box.setRange(0, 100_000)
        self.rate_box.setSingleStep(100)
        self.rate_box.setGroupSeparatorShown(True)
        self.rate_box.setSuffix(" ₽")
        self.rate_box.setValue(rules.shift_rate)
        form.addRow("Ставка за смену:", self.rate_box)
        self.percent_box = QDoubleSpinBox()
        self.percent_box.setRange(0, 100)
        self.percent_box.setDecimals(1)
        self.percent_box.setSuffix(" %")
        self.percent_box.setValue(rules.flowers_percent)
        form.addRow("Процент с продажи цветов:", self.percent_box)
        self.deduct_check = QCheckBox("Вычитать недостачу кассы из зарплаты")
        self.deduct_check.setChecked(rules.deduct_shortage)
        form.addRow(self.deduct_check)
        form.addRow(_gray_label("Зарплата = смены × ставка + цветы × процент − недостачи ± аванс, премия, штраф.\n"
                                "Считаются только сданные смены. Зарплату видно в личном кабинете.\n"
                                "Аванс, премию или штраф добавляйте в кабинете, выбрав сотрудника."))
        layout.addWidget(box)
        layout.addStretch(1)
        return page

    def _telegram_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cfg = telegram.settings()
        steps = QLabel(
            "<b>Как подключить</b> (один раз):<br>"
            "1. В Telegram найдите <b>@BotFather</b>, отправьте <b>/newbot</b> и придумайте боту имя.<br>"
            "2. BotFather пришлёт <b>токен</b> — длинную строку с двоеточием. Вставьте её ниже.<br>"
            "3. Откройте своего нового бота и напишите ему что угодно, например «привет».<br>"
            "4. Нажмите «Найти мой чат», затем «Отправить проверку» и «Сохранить».")
        steps.setWordWrap(True)
        layout.addWidget(steps)

        form = QFormLayout()
        self.token_edit = QLineEdit(cfg["token"])
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("1234567890:AAH…")
        show = QCheckBox("показать")
        show.toggled.connect(lambda on: self.token_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        token_row = QHBoxLayout()
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(show)
        form.addRow("Токен бота:", token_row)
        self.chat_edit = QLineEdit(cfg["chat_id"])
        find = QPushButton("Найти мой чат")
        find.clicked.connect(self._find_chat)
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat_edit, 1)
        chat_row.addWidget(find)
        form.addRow("Чат:", chat_row)
        self.tg_start = QCheckBox("Сообщать о начале смены")
        self.tg_start.setChecked(cfg["on_start"])
        form.addRow(self.tg_start)
        self.tg_close = QCheckBox("Присылать итоги смены при сдаче")
        self.tg_close.setChecked(cfg["on_close"])
        form.addRow(self.tg_close)
        form.addRow(_gray_label("О проблемах (несданная смена, расхождение кассы при приёме) бот пишет всегда.\n"
                                "Нет интернета — сообщения дождутся связи и уйдут позже.\n"
                                "Объявление сотрудникам: напишите боту сообщение, начиная с «!»."))
        layout.addLayout(form)
        test = QPushButton("Отправить проверку")
        test.clicked.connect(self._test_telegram)
        layout.addWidget(test)
        if self.main.telegram.last_error:
            error = QLabel(f"<span style='color:#c62828'>Последняя ошибка отправки: "
                           f"{self.main.telegram.last_error}</span>")
            error.setWordWrap(True)
            layout.addWidget(error)
        layout.addStretch(1)
        return page

    def _backup_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Куда копировать")
        form = QFormLayout(box)
        self.backup_edit = QLineEdit(backup_dir())
        self.backup_edit.setPlaceholderText("например E:\\ — флешка или папка Яндекс.Диска")
        choose = QPushButton("Выбрать…")
        choose.clicked.connect(self._choose_backup_dir)
        row = QHBoxLayout()
        row.addWidget(self.backup_edit, 1)
        row.addWidget(choose)
        form.addRow("Флешка или папка:", row)
        self.keep_box = QSpinBox()
        self.keep_box.setRange(3, 365)
        self.keep_box.setSuffix(" копий")
        self.keep_box.setValue(backup_keep())
        form.addRow("Хранить последние:", self.keep_box)
        form.addRow(_gray_label("Копия делается при каждой сдаче смены и раз в сутки.\n"
                                "Всегда — на этом компьютере, и, если указано, — на флешку или в папку.\n"
                                "Если флешка не вставлена, копия останется на компьютере, а программа предупредит."))
        layout.addWidget(box)

        last = last_backup()
        self.backup_info = QLabel(f"Последняя копия: {last:%d.%m.%Y %H:%M}" if last else "Копий ещё не было.")
        layout.addWidget(self.backup_info)
        buttons = QHBoxLayout()
        for text, slot in (("💾 Сделать копию сейчас", self._backup_now),
                           ("📂 Открыть папку с копиями", self._open_backups),
                           ("↩ Восстановить из копии…", self._restore)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _program_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.autostart_check = QCheckBox("Запускать вместе с Windows")
        self.autostart_check.setChecked(autostart.is_enabled())
        self.autostart_check.setVisible(sys.platform == "win32")
        layout.addWidget(self.autostart_check)
        layout.addWidget(_gray_label("Напоминания приходят, пока программа запущена.\n"
                                     "Крестик сворачивает её к часам, выход — через значок у часов."))
        layout.addWidget(self.main.updater.build_box())
        layout.addStretch(1)
        return page

    # ----- действия -----

    def _open_users(self):
        # Сначала закрываем настройки: после удаления себя из списка программа просит войти заново.
        self.reject()
        QTimer.singleShot(0, self.main.manage_users)

    def _open_duties(self):
        DutiesEditor(self, self.db).exec()

    def _find_chat(self):
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "Telegram", "Сначала вставьте токен бота.")
            return
        try:
            chats = telegram.find_chats(token)
        except telegram.TelegramError as e:
            QMessageBox.warning(self, "Telegram", f"Не получилось: {e}")
            return
        if not chats:
            QMessageBox.information(self, "Telegram", "Бот пока не получал сообщений.\n"
                                    "Откройте бота в Telegram, напишите ему «привет» и нажмите ещё раз.")
            return
        chat_id, title = chats[-1]
        self.chat_edit.setText(chat_id)
        QMessageBox.information(self, "Telegram", f"Нашёл чат: {title or chat_id}.\nТеперь нажмите «Отправить проверку».")

    def _test_telegram(self):
        token, chat_id = self.token_edit.text().strip(), self.chat_edit.text().strip()
        if not token or not chat_id:
            QMessageBox.warning(self, "Telegram", "Заполните токен и чат.")
            return
        try:
            telegram.send_message(token, chat_id, "🌸 Проверка: программа «Доставки цветов» подключена.")
        except telegram.TelegramError as e:
            QMessageBox.warning(self, "Telegram", f"Не отправилось: {e}")
            return
        QMessageBox.information(self, "Telegram", "Отправлено! Проверьте Telegram. Не забудьте «Сохранить».")

    def _choose_backup_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Куда копировать", self.backup_edit.text())
        if folder:
            self.backup_edit.setText(os.path.normpath(folder))

    def _backup_now(self):
        result = backup.make_backups(self.db, self.backup_edit.text().strip(), self.keep_box.value(), datetime.now())
        set_last_backup(datetime.now())
        self.backup_info.setText(f"Последняя копия: {datetime.now():%d.%m.%Y %H:%M}")
        text = f"Копия на компьютере:\n{result.local}"
        if result.extra:
            text += f"\n\nКопия на флешке / в папке:\n{result.extra}"
        if result.extra_error:
            QMessageBox.warning(self, "Резервная копия", f"{text}\n\nНа флешку не получилось: {result.extra_error}")
        else:
            QMessageBox.information(self, "Резервная копия", text)

    def _open_backups(self):
        folder = backup.local_dir(self.db.path)
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите резервную копию", backup.local_dir(self.db.path),
                                              "Резервные копии (*.db)")
        if not path:
            return
        answer = QMessageBox.question(
            self, "Восстановить из копии",
            f"Заменить ВСЕ текущие данные (доставки, смены, сотрудников) данными из копии?\n\n{path}\n\n"
            "Перед этим текущие данные сохранятся отдельной копией.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        backup.make_backups(self.db, "", self.keep_box.value(), datetime.now())  # на всякий случай
        try:
            self.db.restore(path)
        except UserError as e:
            QMessageBox.warning(self, "Восстановление", str(e))
            return
        self.reject()
        QTimer.singleShot(0, self.main.after_restore)

    def accept(self):
        settings = _settings()
        settings.setValue("lead_minutes", self.lead_box.value())
        settings.setValue("shift_change", self.change_edit.time().toString("HH:mm"))
        settings.setValue("grace_minutes", self.grace_box.value())
        settings.setValue("water_every", self.water_every.value())
        settings.setValue("water_anchor", self.water_anchor.date().toString("yyyy-MM-dd"))
        settings.setValue("salary_rate", self.rate_box.value())
        settings.setValue("salary_percent", self.percent_box.value())
        settings.setValue("salary_deduct", _bool(self.deduct_check.isChecked()))
        if self.token_edit.text().strip() != telegram.settings()["token"]:
            settings.remove("tg_offset")  # другой бот — читать его сообщения с начала
        settings.setValue("tg_token", self.token_edit.text().strip())
        settings.setValue("tg_chat_id", self.chat_edit.text().strip())
        settings.setValue("tg_on_start", _bool(self.tg_start.isChecked()))
        settings.setValue("tg_on_close", _bool(self.tg_close.isChecked()))
        settings.setValue("backup_dir", self.backup_edit.text().strip())
        settings.setValue("backup_keep", self.keep_box.value())
        if sys.platform == "win32":
            autostart.set_enabled(self.autostart_check.isChecked())
        super().accept()
        self.main.check_reminders()
