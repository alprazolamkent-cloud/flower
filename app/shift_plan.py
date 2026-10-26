"""«План на смену»: что сегодня сделать — доставки (что собрать и к какому времени), вода, обязанности."""

import html
from datetime import datetime, time, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout

from .db import Database, Delivery
from .delivery_dialog import money
from .reminders import parse_deadline_time, parse_start_time
from .shifts import Shift, WaterSchedule, window
from .theme import C

AHEAD_DAYS = 7  # сколько дней вперёд показать заказы «на потом»


def _status(d: Delivery, lead_minutes: int, now: datetime) -> str:
    if d.delivered:
        return f"<span style='color:{C.green}'>✅ доставлено</span>"
    if d.delete_requested_at is not None:
        return f"<span style='color:{C.gray}'>🗑 ждёт удаления</span>"
    start = parse_start_time(d.time)
    if start is not None and now > datetime.combine(d.day, parse_deadline_time(d.time) or start):
        return f"<b style='color:{C.red}'>время прошло — отметьте</b>"
    if d.assembled:
        return f"<span style='color:{C.green}'>💐 собрано</span>"
    if start is None:
        return f"<span style='color:{C.orange}'>собрать (время не указано)</span>"
    by = datetime.combine(d.day, start) - timedelta(minutes=max(lead_minutes, 0))
    if now >= by:
        return f"<b style='color:{C.red}'>собрать сейчас!</b>"
    return f"<b style='color:{C.orange}'>собрать к {by:%H:%M}</b>"


def _sort_key(d: Delivery):
    return d.day, parse_start_time(d.time) or time(23, 59)


def plan_html(db: Database, shift: Shift, change: time, water: WaterSchedule | None, lead_minutes: int,
              now: datetime | None = None) -> str:
    now = now or datetime.now()
    start, end = window(shift.day, change)
    parts = [f"<h3>План на смену</h3><span style='color:{C.gray}'>{start:%d.%m %H:%M} → {end:%d.%m %H:%M}</span>"]

    own = sorted(db.deliveries_for_shift(shift.day, change), key=_sort_key)
    if own:
        todo = sum(1 for d in own if not d.assembled and not d.delivered)
        title = f"🚚 Доставки за смену: {len(own)}" + (f" — собрать: <b>{todo}</b>" if todo else " — всё собрано")
        rows = "".join(
            f"<tr><td><b>{html.escape(d.time or '—')}</b>{'' if d.day == shift.day else f' ({d.day:%d.%m})'}</td>"
            f"<td>{html.escape(d.items.splitlines()[0] if d.items else '—')}"
            f"<br><span style='color:{C.gray}'>{html.escape(d.address or 'адрес не указан')}</span></td>"
            f"<td align='right'>{_status(d, lead_minutes, now)}</td></tr>"
            for d in own)
        parts.append(f"<p><b>{title}</b></p><table width='100%' cellspacing='0' cellpadding='4'>{rows}</table>")
    else:
        parts.append("<p><b>🚚 Доставок за смену нет.</b></p>")

    later = [d for k in range(1, AHEAD_DAYS + 1) for d in db.deliveries_for_shift(shift.day + timedelta(days=k), change)
             if d.delete_requested_at is None]
    if later:
        first = min(later, key=_sort_key)
        parts.append(f"<p>📅 Заказов на следующие {AHEAD_DAYS} дней: <b>{len(later)}</b> — собирать заранее не нужно. "
                     f"Ближайший: {first.day:%d.%m} {html.escape(first.time or '')}.</p>")

    if water is not None and water.is_water_day(shift.day):
        done = db.water_change(shift.day)
        parts.append(f"<p style='color:{C.blue}'>💧 Сегодня день смены воды"
                     + (f" — уже поменяли ({html.escape(done[0] or '—')})." if done else
                        " — поменяйте и отметьте кнопкой «Вода поменяна».") + "</p>")

    items = db.checklist(shift)
    if items:
        lines = "".join(f"<li>{'✅ ' if i.done else ''}{html.escape(i.text)}</li>" for i in items)
        parts.append(f"<p><b>📋 Обязанности смены</b></p><ul style='margin-top:0'>{lines}</ul>")

    cash = sum(d.price for d in own)
    if cash:
        parts.append(f"<p style='color:{C.gray}'>За доставки этой смены взяли {money(cash)} (перевод / QR).</p>")
    return "".join(parts)


class ShiftPlanDialog(QDialog):
    def __init__(self, parent, db: Database, shift: Shift, change: time, water: WaterSchedule | None,
                 lead_minutes: int):
        super().__init__(parent)
        self.setWindowTitle("План на смену")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        label = QLabel(plan_html(db, shift, change, water, lead_minutes))
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)  # план сверху окна, а не посередине
        label.setContentsMargins(8, 4, 8, 4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(label)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Понятно")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
