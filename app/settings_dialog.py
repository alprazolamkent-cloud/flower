"""Окно настроек — только для администратора."""

import json
import os
import sys
from datetime import date, datetime, time

from PySide6.QtCore import QDate, QSettings, Qt, QTime, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTabWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from . import autostart, backup, telegram, theme
from .audit_dialog import AuditDialog
from .client_bot import bot_link, client_settings
from .db import UserError
from .duties_dialog import DutiesEditor
from .salary import DEFAULT_PENALTY_KINDS, WRITEOFF_BASES, PenaltyKind, SalaryRules, Tier
from .holidays import DEFAULT_TEXT as DEFAULT_HOLIDAYS
from .holidays import Holidays, parse_holidays
from .heartbeat import watch_enabled
from .reports import parse_time
from .salary_dialog import EmployeeTiersDialog, HolidaysDialog, PenaltyCatalogEditor, TiersEditor
from .shifts import DEFAULT_CHANGE, DEFAULT_GRACE_MINUTES, WaterSchedule
from .templates_dialog import TemplatesDialog
from .theme import C

DEFAULT_LEAD_MINUTES = 20


def _settings() -> QSettings:
    return QSettings("FlowerDeliveries", "FlowerDeliveries")


def assembly_minutes() -> int:
    """За сколько минут до доставки требовать собрать букет (0 — не напоминать)."""
    return int(_settings().value("assembly_minutes", 60))


def assembly_alert_minutes() -> int:
    """За сколько минут до доставки сообщить администраторам, что букет всё ещё не собран (0 — не сообщать)."""
    return int(_settings().value("assembly_alert_minutes", 30))


def backup_to_telegram() -> bool:
    """Раз в сутки присылать копию базы администраторам в Telegram."""
    return str(_settings().value("backup_to_telegram", "true")) == "true"


def late_minutes() -> int:
    """Через сколько минут после времени доставки сообщать, что она не отмечена (0 — не сообщать)."""
    return int(_settings().value("late_minutes", 20))


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


def theme_mode() -> str:
    mode = str(_settings().value("theme", "light"))
    return mode if mode in theme.THEMES else "light"


def _tiers_from(raw) -> list[Tier]:
    return [Tier(int(a), float(p)) for a, p in raw]


def bonus_tiers() -> list[Tier]:
    try:
        return _tiers_from(json.loads(str(_settings().value("bonus_tiers", "[]"))))
    except (ValueError, TypeError):
        return []


def bonus_overrides() -> dict[int, list[Tier]]:
    """Свои ступени отдельных сотрудников: id сотрудника -> ступени."""
    try:
        raw = json.loads(str(_settings().value("bonus_overrides", "{}")))
        return {int(k): _tiers_from(v) for k, v in raw.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def _dump_tiers(tiers: list[Tier]) -> list:
    return [[t.per_shift, t.percent] for t in tiers]


def holidays_text() -> str:
    return str(_settings().value("holidays_text", DEFAULT_HOLIDAYS))


def holidays_calendar() -> Holidays:
    return parse_holidays(holidays_text())[0]


def report_settings() -> dict:
    """Автоматические отчёты в Telegram: утренний, недельный, месячный."""
    s = _settings()
    on = lambda key, default="true": str(s.value(key, default)) == "true"
    return {"morning": on("report_morning"), "time": str(s.value("report_time", "09:00")),
            "weekly": on("report_weekly"), "monthly": on("report_monthly")}


def writeoff_alert() -> tuple[float, int]:
    """(во сколько раз выше обычного, не меньше ₽): тревога о большом списании. Кратность 0 — выключено."""
    s = _settings()
    return float(s.value("writeoff_alert_factor", 3)), int(s.value("writeoff_alert_min", 3000))


def penalty_catalog() -> list[PenaltyKind]:
    """Список штрафных санкций; пока админ его не менял — четыре стандартные."""
    raw = str(_settings().value("penalty_catalog", ""))
    if not raw:
        return [PenaltyKind(k.id, k.title, k.amount) for k in DEFAULT_PENALTY_KINDS]
    try:
        return [PenaltyKind(int(i), str(t), int(a)) for i, t, a in json.loads(raw)]
    except (ValueError, TypeError):
        return [PenaltyKind(k.id, k.title, k.amount) for k in DEFAULT_PENALTY_KINDS]


def penalty_max_id() -> int:
    """Наибольший номер санкции за всё время: убранный номер не отдаём новой санкции — старые ссылки
    в Telegram не должны вдруг поставить другую санкцию."""
    return int(_settings().value("penalty_max_id", max([k.id for k in penalty_catalog()] + [0])))


def salary_rules(user_id: int | None = None) -> SalaryRules:
    """Правила зарплаты; для сотрудника со своими ступенями — его ступени."""
    s = _settings()
    tiers = bonus_overrides().get(user_id) if user_id is not None else None
    return SalaryRules(
        shift_rate=int(s.value("salary_rate", 0)),
        tiers=tiers if tiers is not None else bonus_tiers(),
        above_plan_only=str(s.value("bonus_above_plan", "false")) == "true",
        deduct_shortage=str(s.value("salary_deduct", "true")) == "true",
        holiday_rate=int(s.value("salary_holiday_rate", 0)),
        holidays=holidays_calendar(),
        max_penalties=int(s.value("bonus_max_penalties", 3)),
        max_writeoff_percent=float(s.value("bonus_max_writeoff", 15)),
        writeoff_base=str(s.value("writeoff_base", "total")) if str(s.value("writeoff_base", "total")) in WRITEOFF_BASES
        else "total",
    )


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
    label.setStyleSheet(f"color: {C.gray}")
    return label


def _bool(value: bool) -> str:
    return "true" if value else "false"


class ChatPicker(QDialog):
    """Кто из написавших боту получает итоги и может им командовать. Отмечать нужно только своих."""

    def __init__(self, parent, chats: list[tuple[str, str]], selected: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Кто управляет ботом")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        hint = QLabel("Эти люди писали вашему боту. Отметьте тех, кому нужны итоги смен и кто может "
                      "управлять ботом.\nЕсли видите незнакомого — не отмечайте: бот найден посторонним.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = QListWidget()
        known = {chat_id for chat_id, _ in chats}
        rows = [(c, t) for c, t in chats] + [(c, "(сейчас в настройках, бот давно не получал от него сообщений)")
                                              for c in selected if c not in known]
        for chat_id, title in rows:
            item = QListWidgetItem(f"{title or '(без имени)'} — {chat_id}")
            item.setData(Qt.UserRole, chat_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if chat_id in selected else Qt.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Готово")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.main = window
        self.db = window.db
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(780)  # чтобы все вкладки помещались без стрелок прокрутки
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._main_tab(), "Основное")
        self.tabs.addTab(self._shop_tab(), "Магазин")
        self.tabs.addTab(self._salary_tab(), "Зарплата")
        self.tabs.addTab(self._penalty_tab(), "Санкции")
        self.tabs.addTab(self._telegram_tab(), "Telegram")
        self.tabs.addTab(self._clients_tab(), "Клиенты")
        self.tabs.addTab(self._reports_tab(), "Отчёты")
        self.tabs.addTab(self._backup_tab(), "Копии")
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

        look = QGroupBox("Оформление")
        look_form = QFormLayout(look)
        self.theme_box = QComboBox()
        for key, title in theme.THEMES.items():
            self.theme_box.addItem(title, key)
        self.theme_box.setCurrentIndex(max(self.theme_box.findData(theme_mode()), 0))
        look_form.addRow("Тема:", self.theme_box)
        layout.addWidget(look)

        reminders = QGroupBox("Напоминания о доставках")
        form = QFormLayout(reminders)
        self.lead_box = QSpinBox()
        self.lead_box.setRange(1, 180)
        self.lead_box.setSuffix(" мин")
        self.lead_box.setValue(lead_minutes())
        form.addRow("Напоминать за:", self.lead_box)
        self.late_box = QSpinBox()
        self.late_box.setRange(0, 240)
        self.late_box.setSuffix(" мин")
        self.late_box.setSpecialValueText("не сообщать")
        self.late_box.setValue(late_minutes())
        form.addRow("Доставка не отмечена через:", self.late_box)
        form.addRow(_gray_label("Если после времени доставки (для «14:00–16:00» — после 16:00) прошло столько минут,\n"
                                "а «Доставлено» не отмечено — сотрудник получит уведомление, а администраторы — сообщение в Telegram."))
        self.assembly_box = QSpinBox()
        self.assembly_box.setRange(0, 600)
        self.assembly_box.setSingleStep(15)
        self.assembly_box.setSuffix(" мин")
        self.assembly_box.setSpecialValueText("не напоминать")
        self.assembly_box.setValue(assembly_minutes())
        form.addRow("Букет не собран — требовать за:", self.assembly_box)
        self.assembly_alert_box = QSpinBox()
        self.assembly_alert_box.setRange(0, 600)
        self.assembly_alert_box.setSingleStep(5)
        self.assembly_alert_box.setSuffix(" мин")
        self.assembly_alert_box.setSpecialValueText("не сообщать")
        self.assembly_alert_box.setValue(assembly_alert_minutes())
        form.addRow("Сообщить администраторам, если не собран за:", self.assembly_alert_box)
        form.addRow(_gray_label("Окно «Не собрано!» всплывает поверх всех окон и повторяется каждые 10 минут,\n"
                                "пока букет не отметят собранным. Заказы на дальние дни собирать заранее не нужно."))
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
                ("🧾 Журнал действий…", "Кто и что менял: доставки, деньги, сотрудники, настройки",
                 lambda: AuditDialog(self, self.db).exec()),
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
        rules = salary_rules()

        base = QGroupBox("Ставка")
        base_form = QFormLayout(base)
        self.rate_box = QSpinBox()
        self.rate_box.setRange(0, 100_000)
        self.rate_box.setSingleStep(100)
        self.rate_box.setGroupSeparatorShown(True)
        self.rate_box.setSuffix(" ₽")
        self.rate_box.setValue(rules.shift_rate)
        base_form.addRow("За каждую сданную смену:", self.rate_box)
        self.holiday_box = QSpinBox()
        self.holiday_box.setRange(0, 100_000)
        self.holiday_box.setSingleStep(100)
        self.holiday_box.setGroupSeparatorShown(True)
        self.holiday_box.setSuffix(" ₽")
        self.holiday_box.setSpecialValueText("как обычная")
        self.holiday_box.setValue(rules.holiday_rate)
        holiday_row = QHBoxLayout()
        holiday_row.addWidget(self.holiday_box, 1)
        holidays_btn = QPushButton("Праздничные дни…")
        holidays_btn.clicked.connect(self._edit_holidays)
        holiday_row.addWidget(holidays_btn)
        base_form.addRow("Праздничная смена:", holiday_row)
        base_form.addRow(_gray_label("В праздничные дни премия за выручку не начисляется: выручка таких смен\n"
                                     "не идёт в расчёт премии, зато ставка за смену — праздничная."))
        self.holidays_text = holidays_text()
        self.deduct_check = QCheckBox("Вычитать недостачу кассы из зарплаты")
        self.deduct_check.setChecked(rules.deduct_shortage)
        base_form.addRow(self.deduct_check)
        layout.addWidget(base)

        bonus = QGroupBox("Премия от прихода — только при выполнении плана")
        bonus_layout = QVBoxLayout(bonus)
        bonus_layout.addWidget(_gray_label(
            "Берётся средний приход сотрудника за смену за месяц (приход ÷ сданные смены),\n"
            "праздничные смены не считаются.\n"
            "Срабатывает самая высокая ступень, до которой он дотянул.\n"
            "Первая ступень — это план: не дотянул до неё — премии нет. Пустые (—) не считаются."))
        self.tiers_editor = TiersEditor(bonus_tiers())
        bonus_layout.addWidget(self.tiers_editor)
        form = QFormLayout()
        self.above_box = QComboBox()
        self.above_box.addItem("со всего прихода за месяц", False)
        self.above_box.addItem("только с прихода сверх плана", True)
        self.above_box.setCurrentIndex(1 if rules.above_plan_only else 0)
        form.addRow("Процент считать:", self.above_box)
        bonus_layout.addLayout(form)
        self.overrides = bonus_overrides()
        own = QPushButton("Свои ступени для сотрудника…")
        own.clicked.connect(self._employee_tiers)
        bonus_layout.addWidget(own)
        layout.addWidget(bonus)
        layout.addWidget(_gray_label("Зарплата = смены × ставка + премия − недостачи ± аванс, премия, штраф.\n"
                                     "Аванс, премию или штраф добавляйте в личном кабинете, выбрав сотрудника."))
        layout.addStretch(1)
        return page

    def _edit_holidays(self):
        dlg = HolidaysDialog(self, self.holidays_text)
        if dlg.exec() == QDialog.Accepted:
            self.holidays_text = dlg.text()

    def _reports_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        reports = report_settings()
        box = QGroupBox("Отчёты сами приходят в Telegram")
        form = QFormLayout(box)
        self.rep_morning = QCheckBox("Каждое утро — итоги прошлой смены и доставки на сегодня")
        self.rep_morning.setChecked(reports["morning"])
        form.addRow(self.rep_morning)
        self.rep_time = QTimeEdit()
        self.rep_time.setDisplayFormat("HH:mm")
        at = parse_time(reports["time"])
        self.rep_time.setTime(QTime(at.hour, at.minute))
        form.addRow("Во сколько присылать:", self.rep_time)
        self.rep_weekly = QCheckBox("По понедельникам — итоги недели")
        self.rep_weekly.setChecked(reports["weekly"])
        form.addRow(self.rep_weekly)
        self.rep_monthly = QCheckBox("1-го числа — итоги прошлого месяца: выручка, зарплаты, санкции, списания")
        self.rep_monthly.setChecked(reports["monthly"])
        form.addRow(self.rep_monthly)
        form.addRow(_gray_label("Приходят всем подключённым чатам. Программа должна быть запущена: если компьютер\n"
                                "включили позже, отчёт придёт при запуске. Утренний ждёт, пока сдадут ночную смену,\n"
                                "и не приходит, если программу включили больше чем через 6 часов."))
        layout.addWidget(box)

        alerts = QGroupBox("Тревога о большом списании")
        alert_form = QFormLayout(alerts)
        factor, minimum = writeoff_alert()
        self.alert_factor = QDoubleSpinBox()
        self.alert_factor.setRange(0, 50)
        self.alert_factor.setDecimals(1)
        self.alert_factor.setPrefix("в ")
        self.alert_factor.setSuffix(" раза выше обычного")
        self.alert_factor.setSpecialValueText("выключено")
        self.alert_factor.setValue(factor)
        alert_form.addRow("Сообщать, если списали:", self.alert_factor)
        self.alert_min = QSpinBox()
        self.alert_min.setRange(0, 1_000_000)
        self.alert_min.setSingleStep(500)
        self.alert_min.setGroupSeparatorShown(True)
        self.alert_min.setSuffix(" ₽")
        self.alert_min.setValue(minimum)
        alert_form.addRow("и не меньше, чем:", self.alert_min)
        alert_form.addRow(_gray_label("«Обычное» — среднее списание за смену по последним 14 сданным сменам магазина\n"
                                      "(сравнивается, когда сданных смен уже хотя бы три).\n"
                                      "Списания вводит сам сотрудник, так что это единственная цифра, которую\n"
                                      "программа не может проверить, — поэтому о скачках она сообщает вам."))
        layout.addWidget(alerts)
        layout.addStretch(1)
        return page

    def _employee_tiers(self):
        dlg = EmployeeTiersDialog(self, self.db, self.tiers_editor.tiers(), self.overrides)
        if dlg.exec() == QDialog.Accepted:
            self.overrides = dlg.overrides

    def _penalty_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        rules = salary_rules()

        conditions = QGroupBox("Когда премия выдаётся")
        form = QFormLayout(conditions)
        self.max_pen_box = QSpinBox()
        self.max_pen_box.setRange(0, 50)
        self.max_pen_box.setSpecialValueText("не проверять")
        self.max_pen_box.setValue(rules.max_penalties)
        form.addRow("Штрафных санкций за месяц меньше:", self.max_pen_box)
        self.max_wo_box = QDoubleSpinBox()
        self.max_wo_box.setRange(0, 100)
        self.max_wo_box.setDecimals(1)
        self.max_wo_box.setSuffix(" %")
        self.max_wo_box.setSpecialValueText("не проверять")
        self.max_wo_box.setValue(rules.max_writeoff_percent)
        form.addRow("Списаний по магазину за месяц меньше:", self.max_wo_box)
        self.wo_base_box = QComboBox()
        for key, title in WRITEOFF_BASES.items():
            self.wo_base_box.addItem(title, key)
        self.wo_base_box.setCurrentIndex(max(self.wo_base_box.findData(rules.writeoff_base), 0))
        form.addRow("Процент списаний считать:", self.wo_base_box)
        form.addRow(_gray_label("Штрафы считаются у каждого сотрудника свои, списания — общие по магазину.\n"
                                "«Меньше» значит строго: при «меньше 3» премия есть у тех, у кого 0, 1 или 2 санкции.\n"
                                "Пример: продано цветов на 85 000 ₽, списано на 15 000 ₽ — по первому способу\n"
                                "15 000 ÷ 100 000 = 15%, по второму 15 000 ÷ 85 000 = 17,6%."))
        layout.addWidget(conditions)

        catalog = QGroupBox("Штрафные санкции (вычитаются из зарплаты)")
        catalog_layout = QVBoxLayout(catalog)
        self.catalog_editor = PenaltyCatalogEditor(penalty_catalog())
        self.catalog_editor.max_used_id = penalty_max_id()
        catalog_layout.addWidget(self.catalog_editor)
        catalog_layout.addWidget(_gray_label("Санкцию ставит администратор: после сдачи смены в Telegram приходят ссылки\n"
                                             "по этому списку, или командой /санкция Имя Номер. Одну и ту же санкцию\n"
                                             "за одну смену дважды поставить нельзя."))
        layout.addWidget(catalog)
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
            "4. Нажмите «Найти чаты…», отметьте себя, затем «Отправить проверку» и «Сохранить».<br>"
            "<b>Второй человек:</b> пусть найдёт вашего бота в Telegram и тоже напишет ему «привет», "
            "а вы снова нажмите «Найти чаты…» и отметьте обоих. Итоги смен придут обоим, командовать смогут оба.")
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
        self.chat_edit.setPlaceholderText("номера через запятую, например 123456789, 987654321")
        find = QPushButton("Найти чаты…")
        find.clicked.connect(self._find_chat)
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat_edit, 1)
        chat_row.addWidget(find)
        form.addRow("Чаты:", chat_row)
        self.tg_start = QCheckBox("Сообщать о начале смены")
        self.tg_start.setChecked(cfg["on_start"])
        form.addRow(self.tg_start)
        self.tg_close = QCheckBox("Присылать итоги смены при сдаче")
        self.tg_close.setChecked(cfg["on_close"])
        form.addRow(self.tg_close)
        self.tg_watch = QCheckBox("Следить, что компьютер на связи (пропала больше чем на 10 минут — "
                                  "сообщение каждые 10 минут)")
        self.tg_watch.setChecked(watch_enabled())
        form.addRow(self.tg_watch)
        form.addRow(_gray_label("Программа держит в чате закреплённое сообщение «на связи» и обновляет его.\n"
                                "Следит за ним GitHub — один раз добавьте там секреты TG_TOKEN и TG_CHATS\n"
                                "(инструкция в README, раздел «Компьютер не на связи»)."))
        form.addRow(_gray_label("О проблемах (несданная смена, расхождение кассы при приёме) бот пишет всегда.\n"
                                "Нет интернета — сообщения дождутся связи и уйдут позже.\n"
                                "Объявление сотрудникам: напишите боту сообщение, начиная с «!».\n"
                                "Команды: /help — полный список (касса, выручка, зарплата, доставки…).\n"
                                "Бот слушает только эти чаты — лучше личные, не групповые."))
        layout.addLayout(form)
        test = QPushButton("Отправить проверку")
        test.clicked.connect(self._test_telegram)
        layout.addWidget(test)
        if self.main.telegram.last_error:
            error = QLabel(f"<span style='color:{C.red}'>Последняя ошибка отправки: "
                           f"{self.main.telegram.last_error}</span>")
            error.setWordWrap(True)
            layout.addWidget(error)
        layout.addStretch(1)
        return page

    def _clients_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cfg = client_settings()
        steps = QLabel(
            "<b>Бот для клиентов</b> — отдельный от бота администраторов. Клиенты смотрят витрину, заказывают "
            "букет и пишут флористу, а заказы и сообщения появляются в окне «🛍 Клиенты».<br><br>"
            "<b>Как подключить</b> (один раз):<br>"
            "1. В Telegram откройте <b>@BotFather</b>, отправьте <b>/newbot</b>. Имя — «Страна Чудес», "
            "логин — латиницей и с bot на конце (например StranaChudesFlowers_bot).<br>"
            "2. Вставьте присланный токен ниже, нажмите «Проверить» и «Сохранить».<br>"
            "3. Впишите реквизиты для оплаты — бот пришлёт их клиенту вместе с суммой.<br>"
            "4. Ссылку на бота дайте клиентам: в профиле Instagram, на визитке, в статусе WhatsApp.<br>"
            "Фото и описание бота меняются там же, в BotFather: /setuserpic и /setdescription.")
        steps.setWordWrap(True)
        layout.addWidget(steps)
        form = QFormLayout()
        self.client_token_edit = QLineEdit(cfg["token"])
        self.client_token_edit.setEchoMode(QLineEdit.Password)
        self.client_token_edit.setPlaceholderText("токен второго бота, 1234567890:AAH…")
        show = QCheckBox("показать")
        show.toggled.connect(
            lambda on: self.client_token_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        check = QPushButton("Проверить")
        check.clicked.connect(self._check_client_bot)
        token_row = QHBoxLayout()
        token_row.addWidget(self.client_token_edit, 1)
        token_row.addWidget(show)
        token_row.addWidget(check)
        form.addRow("Токен бота:", token_row)
        self.client_status = QLabel()
        self.client_status.setOpenExternalLinks(True)
        self.client_status.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._client_username = ""
        if cfg["token"] and cfg["username"]:
            link = bot_link(cfg["username"])
            self.client_status.setText(f"Бот @{cfg['username']}, ссылка для клиентов: <a href='{link}'>{link}</a>")
        form.addRow("", self.client_status)
        self.shop_edit = QLineEdit(cfg["shop"])
        form.addRow("Название в приветствии:", self.shop_edit)
        self.requisites_edit = QPlainTextEdit(cfg["requisites"])
        self.requisites_edit.setPlaceholderText("Перевод по номеру +7 900 000-00-00 (Сбербанк, получатель Мария И.)")
        self.requisites_edit.setFixedHeight(70)
        form.addRow("Реквизиты для оплаты:", self.requisites_edit)
        form.addRow(_gray_label("Сумму флорист вписывает каждый раз сам (букет + доставка), бот присылает её\n"
                                "с реквизитами и кнопкой «Я оплатил(а)». Заказ попадает в доставки, когда\n"
                                "флорист подтвердит, что деньги пришли."))
        form.addRow(_gray_label("Бот работает, пока компьютер включён и есть интернет. Что клиенты напишут,\n"
                                "пока он выключен, бот получит позже (Telegram хранит сообщения сутки)."))
        layout.addLayout(form)
        if self.main.client_bot.last_error:
            error = QLabel(f"<span style='color:{C.red}'>Последняя ошибка бота: "
                           f"{self.main.client_bot.last_error}</span>")
            error.setWordWrap(True)
            layout.addWidget(error)
        layout.addStretch(1)
        return page

    def _check_client_bot(self):
        token = self.client_token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "Бот для клиентов", "Сначала вставьте токен бота.")
            return
        if token == self.token_edit.text().strip():
            QMessageBox.warning(self, "Бот для клиентов", "Это токен бота для администраторов. "
                                "Для клиентов нужен отдельный бот — создайте его в @BotFather (/newbot).")
            return
        try:
            me = telegram.get_me(token)
        except telegram.TelegramError as e:
            self.client_status.setText(f"<span style='color:{C.red}'>Не работает: {e}</span>")
            return
        self._client_username = me.get("username", "")
        link = bot_link(self._client_username)
        self.client_status.setText(f"<span style='color:{C.green}'>✅ Работает: @{self._client_username}</span> — "
                                   f"ссылка для клиентов: <a href='{link}'>{link}</a>. Не забудьте «Сохранить».")

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
        self.tg_backup_check = QCheckBox("Каждый день присылать копию администраторам в Telegram")
        self.tg_backup_check.setChecked(backup_to_telegram())
        form.addRow(self.tg_backup_check)
        form.addRow(_gray_label("Файл приходит после утренней пересменки — даже если компьютер сломается,\n"
                                "данные останутся у вас в Telegram. Ещё раз — командой /копия. В файле телефоны\n"
                                "клиентов и зашифрованные пароли: никому не пересылайте."))
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
        chats = list(self.db.seen_chats())  # кто писал боту, пока программа его слушала
        try:  # плюс то, что Telegram ещё не отдал программе (первая настройка, программа пока не слушает)
            known = {c for c, _ in chats}
            chats += [(c, t) for c, t in telegram.find_chats(token) if c not in known]
        except telegram.TelegramError as e:
            if not chats:
                QMessageBox.warning(self, "Telegram", f"Не получилось: {e}")
                return
        if not chats:
            QMessageBox.information(self, "Telegram", "Бот пока не получал сообщений.\n"
                                    "Откройте бота в Telegram, напишите ему «привет» и нажмите ещё раз.")
            return
        picker = ChatPicker(self, chats, telegram.chat_ids(self.chat_edit.text()))
        if picker.exec() == QDialog.Accepted:
            self.chat_edit.setText(", ".join(picker.selected()))

    def _test_telegram(self):
        token, chats = self.token_edit.text().strip(), telegram.chat_ids(self.chat_edit.text())
        if not token or not chats:
            QMessageBox.warning(self, "Telegram", "Заполните токен и чат.")
            return
        failed = []
        for chat in chats:
            try:
                telegram.send_message(token, chat, "🌸 Проверка: программа AloFlorist подключена.")
            except telegram.TelegramError as e:
                failed.append(f"{chat}: {e}")
        if failed:
            QMessageBox.warning(self, "Telegram", "Не отправилось:\n" + "\n".join(failed)
                                + "\n\nЧеловек должен сначала написать боту «привет» (бот не может писать первым).")
        else:
            QMessageBox.information(self, "Telegram", f"Отправлено ({len(chats)})! Проверьте Telegram. "
                                    "Не забудьте «Сохранить».")

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
                                              "Резервные копии (*.db *.zip)")
        if not path:
            return
        source = path
        if path.lower().endswith(".zip"):  # копия из Telegram приходит архивом
            try:
                path = backup.extract_db(path)
            except UserError as e:
                QMessageBox.warning(self, "Восстановление", str(e))
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
        # журнал тоже вернулся к состоянию копии — эта запись осталась бы единственным следом восстановления
        self.db.log_action(self.main.user, "Данные восстановлены из копии", os.path.basename(source))
        self.reject()
        QTimer.singleShot(0, self.main.after_restore)

    def _log_changes(self):
        """Что именно поменяли в настройках — в журнал действий (секреты не пишем)."""
        rules, schedule = salary_rules(), water_schedule()
        old_tg = telegram.settings()
        rows = [
            ("напоминать о доставке за (мин)", lead_minutes(), self.lead_box.value(), False),
            ("доставка не отмечена через (мин)", late_minutes(), self.late_box.value(), False),
            ("требовать собрать за (мин)", assembly_minutes(), self.assembly_box.value(), False),
            ("сообщить, если не собран за (мин)", assembly_alert_minutes(), self.assembly_alert_box.value(), False),
            ("копия в Telegram", backup_to_telegram(), self.tg_backup_check.isChecked(), False),
            ("пересменка", shift_change().strftime("%H:%M"), self.change_edit.time().toString("HH:mm"), False),
            ("сдать смену ещё (мин)", grace_minutes(), self.grace_box.value(), False),
            ("вода раз в (дн.)", schedule.every, self.water_every.value(), False),
            ("день замены воды", schedule.anchor.isoformat(), self.water_anchor.date().toString("yyyy-MM-dd"), False),
            ("ставка за смену", rules.shift_rate, self.rate_box.value(), False),
            ("ставка в праздники", rules.holiday_rate, self.holiday_box.value(), False),
            ("праздничные дни", " ".join(holidays_text().split()), " ".join(self.holidays_text.split()), True),
            ("утренний отчёт", report_settings()["morning"], self.rep_morning.isChecked(), False),
            ("время отчёта", report_settings()["time"], self.rep_time.time().toString("HH:mm"), False),
            ("недельный отчёт", report_settings()["weekly"], self.rep_weekly.isChecked(), False),
            ("месячный отчёт", report_settings()["monthly"], self.rep_monthly.isChecked(), False),
            ("тревога о списании (раз)", writeoff_alert()[0], self.alert_factor.value(), False),
            ("тревога о списании от (₽)", writeoff_alert()[1], self.alert_min.value(), False),
            ("вычитать недостачу", rules.deduct_shortage, self.deduct_check.isChecked(), False),
            ("процент сверх плана", rules.above_plan_only, bool(self.above_box.currentData()), False),
            ("премия, если санкций меньше", rules.max_penalties, self.max_pen_box.value(), False),
            ("премия, если списаний меньше (%)", rules.max_writeoff_percent, self.max_wo_box.value(), False),
            ("списания считать", rules.writeoff_base, self.wo_base_box.currentData(), False),
            ("список штрафных санкций",
             "; ".join(f"{k.title} {k.amount}" for k in penalty_catalog()),
             "; ".join(f"{k.title} {k.amount}" for k in self.catalog_editor.kinds()), False),
            ("ступени премии", json.dumps(_dump_tiers(bonus_tiers())), json.dumps(_dump_tiers(self.tiers_editor.tiers())), True),
            ("свои ступени сотрудников", json.dumps({str(k): _dump_tiers(v) for k, v in bonus_overrides().items()}),
             json.dumps({str(k): _dump_tiers(v) for k, v in self.overrides.items()}), True),
            ("тема", theme_mode(), self.theme_box.currentData(), False),
            ("токен Telegram-бота", old_tg["token"], self.token_edit.text().strip(), True),
            ("чаты Telegram", ", ".join(old_tg["chats"]), ", ".join(telegram.chat_ids(self.chat_edit.text())), False),
            ("сообщать о начале смены", old_tg["on_start"], self.tg_start.isChecked(), False),
            ("итоги при сдаче", old_tg["on_close"], self.tg_close.isChecked(), False),
            ("следить за связью", watch_enabled(), self.tg_watch.isChecked(), False),
            ("токен бота для клиентов", client_settings()["token"], self.client_token_edit.text().strip(), True),
            ("название магазина в боте", client_settings()["shop"], self.shop_edit.text().strip(), False),
            ("реквизиты для оплаты", client_settings()["requisites"], self.requisites_edit.toPlainText().strip(), True),
            ("флешка / папка для копий", backup_dir(), self.backup_edit.text().strip(), False),
            ("хранить копий", backup_keep(), self.keep_box.value(), False),
        ]
        changes = [f"{title}: изменено" if hidden else f"{title}: {old} → {new}"
                   for title, old, new, hidden in rows if str(old) != str(new)]
        if changes:
            self.db.log_action(self.main.user, "Настройки изменены", "; ".join(changes))

    def accept(self):
        client_token = self.client_token_edit.text().strip()
        if client_token and client_token == self.token_edit.text().strip():
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.client_token_edit.parentWidget()))
            QMessageBox.warning(self, "Бот для клиентов", "Токен бота для клиентов совпадает с ботом администраторов. "
                                "Нужен отдельный бот — иначе они будут мешать друг другу.")
            return
        self._log_changes()
        settings = _settings()
        if client_token != client_settings()["token"]:  # другой бот: читать с начала, фото витрины загрузить заново
            settings.remove("client_offset")
            settings.remove("client_bot_username")
            self.db.reset_showcase_file_ids()
        if self._client_username:
            settings.setValue("client_bot_username", self._client_username)
        settings.setValue("client_token", client_token)
        settings.setValue("client_shop", self.shop_edit.text().strip())
        settings.setValue("client_requisites", self.requisites_edit.toPlainText().strip())
        QTimer.singleShot(0, self.main.client_bot.poll)  # новый бот — сразу начать слушать
        settings.setValue("lead_minutes", self.lead_box.value())
        settings.setValue("late_minutes", self.late_box.value())
        settings.setValue("assembly_minutes", self.assembly_box.value())
        settings.setValue("assembly_alert_minutes", self.assembly_alert_box.value())
        settings.setValue("backup_to_telegram", _bool(self.tg_backup_check.isChecked()))
        settings.setValue("shift_change", self.change_edit.time().toString("HH:mm"))
        settings.setValue("grace_minutes", self.grace_box.value())
        settings.setValue("water_every", self.water_every.value())
        settings.setValue("water_anchor", self.water_anchor.date().toString("yyyy-MM-dd"))
        settings.setValue("salary_rate", self.rate_box.value())
        settings.setValue("salary_holiday_rate", self.holiday_box.value())
        settings.setValue("holidays_text", self.holidays_text)
        settings.setValue("report_morning", _bool(self.rep_morning.isChecked()))
        settings.setValue("report_time", self.rep_time.time().toString("HH:mm"))
        settings.setValue("report_weekly", _bool(self.rep_weekly.isChecked()))
        settings.setValue("report_monthly", _bool(self.rep_monthly.isChecked()))
        settings.setValue("writeoff_alert_factor", self.alert_factor.value())
        settings.setValue("writeoff_alert_min", self.alert_min.value())
        settings.setValue("salary_deduct", _bool(self.deduct_check.isChecked()))
        settings.setValue("bonus_tiers", json.dumps(_dump_tiers(self.tiers_editor.tiers())))
        settings.setValue("bonus_above_plan", _bool(self.above_box.currentData()))
        settings.setValue("bonus_max_penalties", self.max_pen_box.value())
        settings.setValue("bonus_max_writeoff", self.max_wo_box.value())
        settings.setValue("writeoff_base", self.wo_base_box.currentData())
        kinds = self.catalog_editor.kinds()
        settings.setValue("penalty_catalog", json.dumps([[k.id, k.title, k.amount] for k in kinds], ensure_ascii=False))
        settings.setValue("penalty_max_id", max([k.id for k in kinds] + [self.catalog_editor.max_used_id]))
        settings.setValue("bonus_overrides", json.dumps(
            {str(uid): _dump_tiers(tiers) for uid, tiers in self.overrides.items()}))
        if self.token_edit.text().strip() != telegram.settings()["token"]:
            settings.remove("tg_offset")  # другой бот — читать его сообщения с начала
        settings.setValue("tg_token", self.token_edit.text().strip())
        settings.setValue("tg_chat_id", ", ".join(telegram.chat_ids(self.chat_edit.text())))
        settings.setValue("tg_on_start", _bool(self.tg_start.isChecked()))
        settings.setValue("tg_on_close", _bool(self.tg_close.isChecked()))
        settings.setValue("tg_watch", _bool(self.tg_watch.isChecked()))
        QTimer.singleShot(0, self.main.heartbeat.beat)  # сразу обновить (или поставить на паузу) сообщение
        settings.setValue("backup_dir", self.backup_edit.text().strip())
        settings.setValue("backup_keep", self.keep_box.value())
        if sys.platform == "win32":
            autostart.set_enabled(self.autostart_check.isChecked())
        new_theme = self.theme_box.currentData()
        changed_theme = new_theme != theme_mode()
        settings.setValue("theme", new_theme)
        super().accept()
        if changed_theme:
            self.main.apply_theme()
        self.main.check_reminders()
