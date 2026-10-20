"""Блок «Зарплата за месяц» в личном кабинете: три карточки вместо сплошного текста.

«Расчёт» — как чек: что начислено и удержано, внизу крупно «К выплате».
«Премия» — средний приход, полоска до следующей ступени, условия премии.
«Месяц в магазине» — рейтинг, списания по причинам, праздничные смены, споры.
"""

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from .delivery_dialog import money as _money
from .salary import KIND_TITLES, Forecast, Ranking, Salary, SalaryRules, pct, shifts_word
from .theme import C


def money(value: int) -> str:
    """Сумма для карточек: «59 000 ₽» не разрывается переносом строки."""
    return _money(value).replace(" ", "&nbsp;")


def _esc(text: str) -> str:
    return html.escape(text or "—")


def _row(label: str, amount: str, color: str = "", note: str = "") -> str:
    style = f" style='color:{color}'" if color else ""
    note_html = f"<br><span style='color:{C.gray}; font-size:11px'>{note}</span>" if note else ""
    return (f"<tr><td{style}>{label}{note_html}</td>"
            f"<td align='right' style='white-space:nowrap{'; color:' + color if color else ''}'>{amount}</td></tr>")


def calc_html(sal: Salary, rules: SalaryRules) -> str:
    """Расчёт по строкам: смены, премия, удержания, доплаты — и итог."""
    rows = []
    if not sal.base_parts:
        rows.append(_row("Смены", money(0)))
    for kind, count, rate in sal.base_parts:
        title = "Смены" if len(sal.base_parts) == 1 else f"{KIND_TITLES[kind].capitalize()} смены"
        rows.append(_row(f"{title}: {count} × {money(rate)}", money(count * rate)))
    if rules.active_tiers():
        if sal.tier is None:
            rows.append(_row("Премия: план не выполнен", money(0), C.gray))
        elif sal.blocked:
            rows.append(_row("Премия не начислена", money(0), C.red, f"было бы {money(sal.bonus_earned)}"))
        else:
            source = "со сверх плана " if rules.above_plan_only else "с "
            rows.append(_row(f"Премия: {pct(sal.tier.percent, None)} {source}{money(sal.bonus_base)}",
                             f"+{money(sal.bonus)}", C.green))
    if sal.shortage:
        rows.append(_row("Недостача кассы", f"−{money(sal.shortage)}", C.red))
    for p in sal.penalties:
        disputed = p.disputed_at is not None and not p.dispute_result
        rows.append(_row(f"Санкция: {_esc(p.title)} ({p.shift_day:%d.%m})" + (" ✋" if disputed else ""),
                         f"−{money(p.amount)}", C.red, "оспорена, ждёт решения" if disputed else ""))
    for a in sal.adjustments:
        reason = _esc(a.reason)
        rows.append(_row(reason[:1].upper() + reason[1:], ("+" if a.amount > 0 else "−") + money(abs(a.amount)),
                         C.green if a.amount > 0 else C.red))
    total = (f"<tr><td colspan='2'><hr></td></tr>"
             f"<tr><td><b style='font-size:15px'>К выплате</b></td>"
             f"<td align='right' style='white-space:nowrap'><b style='font-size:18px'>{money(sal.total)}</b></td></tr>")
    note = (f"<p style='color:{C.gray}; font-size:11px'>Несданных смен: {sal.not_closed} — ставка начислена, "
            "но их выручка неизвестна и в премию не идёт</p>"
            if sal.not_closed else "")
    return f"<table width='100%' cellspacing='0' cellpadding='2'>{''.join(rows)}{total}</table>{note}"


def bonus_top_html(sal: Salary, rules: SalaryRules) -> str:
    if not rules.active_tiers():
        return f"<span style='color:{C.gray}'>Премия от прихода не настроена.</span>"
    counted = f"за {shifts_word(sal.bonus_shifts)}" + (", без праздничных" if sal.holiday_shifts else "")
    text = (f"<span style='color:{C.gray}'>Средний приход за смену</span><br>"
            f"<b style='font-size:20px'>{money(round(sal.average))}</b> "
            f"<span style='color:{C.gray}'>{counted}</span><br>")
    if sal.tier is None:
        text += f"<span style='color:{C.red}'>План (от {money(sal.plan)}) пока не выполнен</span>"
    else:
        text += (f"Ступень <b>от {money(sal.tier.per_shift)}</b> → "
                 f"<b style='color:{C.green}'>{pct(sal.tier.percent, None)}</b>")
    return text


def progress(sal: Salary, rules: SalaryRules) -> tuple[int, int, str] | None:
    """(сколько пройдено, весь путь, подпись) для полоски до следующей ступени; None — полоски нет.
    Подпись — обычный текст (не HTML), поэтому суммы без &nbsp;."""
    if not rules.active_tiers():
        return None
    if sal.next_tier is None:
        return 1, 1, "высшая ступень — дальше некуда 🎉"
    low = sal.tier.per_shift if sal.tier else 0
    high = sal.next_tier.per_shift
    done = min(max(round(sal.average) - low, 0), high - low)
    goal = "план" if sal.tier is None else pct(sal.next_tier.percent, None)
    if not sal.bonus_shifts:
        return 0, max(high - low, 1), f"до ступени «{goal}» — {_money(high)} в среднем"
    return done, max(high - low, 1), f"до «{goal}» не хватает {_money(high - round(sal.average))} в среднем"


def bonus_bottom_html(sal: Salary, rules: SalaryRules, plan: Forecast | None) -> str:
    lines = []
    if plan is not None:
        lines.append(f"🎯 Осталось ~{shifts_word(plan.remaining_shifts)}: для {pct(plan.target.percent, None)} "
                     f"нужно в среднем <b>{money(plan.needed_average)}</b>")
    if rules.max_penalties > 0:
        ok = len(sal.penalties) < rules.max_penalties
        lines.append(f"<span style='color:{C.green if ok else C.red}'>{'✓' if ok else '✗'} Санкций: "
                     f"{len(sal.penalties)} (для премии меньше {rules.max_penalties})</span>")
    if sal.writeoff_percent is not None:
        ok = sal.writeoff_percent < rules.max_writeoff_percent
        lines.append(f"<span style='color:{C.green if ok else C.red}'>{'✓' if ok else '✗'} Списания магазина: "
                     f"{pct(sal.writeoff_percent)} (для премии меньше {pct(rules.max_writeoff_percent, None)})</span>")
    return "<br>".join(lines)


def month_html(rank: Ranking, reasons: dict[str, int], sal: Salary | None = None) -> str:
    lines = []
    if rank.best_shift is not None and rank.best_shift.revenue:
        b = rank.best_shift
        lines.append(f"🏆 Лучшая смена: <b>{_esc(b.user_name)}</b>, {b.day:%d.%m} — {money(b.revenue)}")
    if rank.best_average_name:
        lines.append(f"⭐ Лучший средний приход: <b>{_esc(rank.best_average_name)}</b> — {money(rank.best_average)}")
    if reasons:
        top = sorted(reasons.items(), key=lambda x: -x[1])
        lines.append("🥀 Списания: " + ", ".join(f"{_esc(r).lower()} {money(a)}" for r, a in top))
    if sal is not None and sal.holiday_shifts:
        lines.append(f"🎉 Праздничных смен: {sal.holiday_shifts} — выручка {money(sal.holiday_revenue)} "
                     "в премию не идёт")
    if sal is not None:
        disputes = [p for p in sal.penalties if p.disputed_at and not p.dispute_result]
        if disputes:
            lines.append(f"<span style='color:{C.orange}'>✋ Оспорено, ждёт решения: "
                         + ", ".join(_esc(p.title) for p in disputes) + "</span>")
    if not lines:
        return f"<span style='color:{C.gray}'>Сданных смен в этом месяце пока нет.</span>"
    return "<br>".join(lines)


def all_html(rows: list[tuple[str, Salary]]) -> str:
    """Все сотрудники: по строке на каждого и общий итог."""
    if not rows:
        return f"<span style='color:{C.gray}'>Смен за месяц нет.</span>"
    head = "".join(f"<th align='{a}' style='color:{C.gray}; font-weight:normal'>{t}</th>"
                   for t, a in (("Сотрудник", "left"), ("Смен", "right"), ("Премия", "right"),
                                ("Удержано", "right"), ("К выплате", "right")))
    body = []
    for name, sal in rows:
        kept = sal.shortage + sal.penalties_total - sum(a.amount for a in sal.adjustments if a.amount < 0)
        flags = []
        if sal.blocked:
            flags.append(f"<span style='color:{C.red}'>без премии</span>")
        if any(p.disputed_at and not p.dispute_result for p in sal.penalties):
            flags.append(f"<span style='color:{C.orange}'>✋ спор</span>")
        flag = f"<br><span style='font-size:11px'>{' · '.join(flags)}</span>" if flags else ""
        body.append(f"<tr><td><b>{_esc(name)}</b>{flag}</td><td align='right'>{sal.shifts}</td>"
                    f"<td align='right' style='color:{C.green if sal.bonus else C.gray}'>{money(sal.bonus)}</td>"
                    f"<td align='right' style='color:{C.red if kept else C.gray}'>{'−' + money(kept) if kept else '—'}</td>"
                    f"<td align='right'><b>{money(sal.total)}</b></td></tr>")
    total = sum(sal.total for _, sal in rows)
    foot = (f"<tr><td colspan='5'><hr></td></tr><tr><td><b>Всего</b></td><td></td><td></td><td></td>"
            f"<td align='right'><b style='font-size:15px'>{money(total)}</b></td></tr>")
    return f"<table width='100%' cellspacing='0' cellpadding='3'><tr>{head}</tr>{''.join(body)}{foot}</table>"


def _label() -> QLabel:
    label = QLabel()
    label.setWordWrap(True)
    label.setTextFormat(Qt.RichText)
    return label


class SalaryPanel(QWidget):
    """Три карточки рядом и колонка кнопок справа (кнопки добавляет кабинет в self.buttons)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.calc_card, calc = self._card("Расчёт")
        self.calc = _label()
        calc.addWidget(self.calc)
        calc.addStretch(1)
        self.bonus_card, bonus = self._card("Премия")
        self.bonus_top = _label()
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setFixedHeight(20)
        self.bonus_bottom = _label()
        for w in (self.bonus_top, self.bar, self.bonus_bottom):
            bonus.addWidget(w)
        bonus.addStretch(1)
        self.month_card, month = self._card("Месяц в магазине")
        self.month = _label()
        month.addWidget(self.month)
        month.addStretch(1)
        layout.addWidget(self.calc_card, 5)
        layout.addWidget(self.bonus_card, 4)
        layout.addWidget(self.month_card, 4)
        self.buttons = QVBoxLayout()
        layout.addLayout(self.buttons)

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 8, 12, 10)
        head = QLabel(title)
        head.setObjectName("cardTitle")
        box.addWidget(head)
        frame.title = head
        return frame, box

    def _style(self):
        """Цвета берутся из темы при каждом показе — тема могла смениться."""
        self.setStyleSheet(
            f"QFrame#card {{ background: {C.card_bg}; border: 1px solid {C.card_border}; border-radius: 8px; }}"
            f"QLabel#cardTitle {{ color: {C.gray}; font-weight: bold; }}"
            f"QProgressBar {{ border: 1px solid {C.card_border}; border-radius: 4px; text-align: center; }}"
            f"QProgressBar::chunk {{ background: {C.bar}; border-radius: 3px; }}")

    def show_person(self, sal: Salary, rules: SalaryRules, month_name: str, plan: Forecast | None,
                    rank: Ranking, reasons: dict[str, int]):
        self._style()
        self.calc_card.title.setText(f"Расчёт за {month_name}")
        self.calc.setText(calc_html(sal, rules))
        self.bonus_card.setVisible(True)
        self.bonus_top.setText(bonus_top_html(sal, rules))
        bar = progress(sal, rules)
        self.bar.setVisible(bar is not None)
        if bar is not None:
            done, whole, text = bar
            self.bar.setRange(0, whole)
            self.bar.setValue(done)
            self.bar.setFormat(text)
        self.bonus_bottom.setText(bonus_bottom_html(sal, rules, plan))
        self.month.setText(month_html(rank, reasons, sal))

    def show_all(self, rows: list[tuple[str, Salary]], month_name: str, rank: Ranking, reasons: dict[str, int]):
        self._style()
        self.calc_card.title.setText(f"Зарплата за {month_name}")
        self.calc.setText(all_html(rows))
        self.bonus_card.setVisible(False)  # у каждого своя премия — подробно, если выбрать сотрудника
        self.month.setText(month_html(rank, reasons))

