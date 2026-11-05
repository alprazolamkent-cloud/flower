"""Ряд кнопок с переносом: не помещается по ширине — следующие кнопки уходят на новую строку.

Обычный ряд (QHBoxLayout) требует ширину всех кнопок сразу — в Windows, где шрифт и значки шире,
главное окно и склад из-за этого не влезали в экран ноутбука. Сделано по примеру Flow Layout из Qt.
"""

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidgetItem


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 6, align_right: bool = False):
        super().__init__(parent)
        self.items = []
        self.gap = spacing
        self.align_right = align_right  # кнопки прижаты вправо (как справа в заголовке)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802 — имена из Qt
        self.items.append(item)

    def addWidget(self, widget):  # noqa: N802
        self.addChildWidget(widget)
        self.addItem(QWidgetItem(widget))

    def count(self):
        return len(self.items)

    def itemAt(self, index):  # noqa: N802
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):  # noqa: N802
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._place(QRect(0, 0, width, 0), apply=False)

    def minimumHeightForWidth(self, width):  # noqa: N802 — и в тесном окне не меньше, чем нужно всем строкам
        return self.heightForWidth(width)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self):  # noqa: N802
        """Желательно — в одну строку."""
        visible = [i for i in self.items if not i.isEmpty()]
        width = sum(self._size(i).width() for i in visible) + self.gap * max(len(visible) - 1, 0)
        height = max((self._size(i).height() for i in visible), default=0)
        m = self.contentsMargins()
        return QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def minimumSize(self):  # noqa: N802
        """Наименьшая ширина — самая широкая кнопка, а не все кнопки вместе."""
        size = QSize()
        for item in self.items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    @staticmethod
    def _size(item) -> QSize:
        """Сколько места нужно: «желательный» размер, но не меньше минимального (у карточек он больше)."""
        return item.sizeHint().expandedTo(item.minimumSize())

    def _place(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        rows, row, row_width, x_max = [], [], 0, area.width()
        for item in self.items:
            if item.isEmpty():
                continue
            w = self._size(item).width()
            if row and row_width + self.gap + w > x_max:
                rows.append(row)
                row, row_width = [], 0
            row_width += (self.gap if row else 0) + w
            row.append(item)
        if row:
            rows.append(row)
        y = area.y()
        for row in rows:
            height = max(self._size(i).height() for i in row)
            used = sum(self._size(i).width() for i in row) + self.gap * (len(row) - 1)
            x = area.x() + (area.width() - used if self.align_right else 0)
            for item in row:
                hint = self._size(item)
                if apply:
                    item.setGeometry(QRect(QPoint(x, y), QSize(min(hint.width(), area.width()), height)))
                x += hint.width() + self.gap
            y += height + self.gap
        return y - self.gap - rect.y() + m.bottom() if rows else 0


class _KeepHeight(QObject):
    """Ширина изменилась — минимальная высота под нужное число строк (иначе в тесном окне Qt
    отдаёт ряду одну строку, и перенесённые кнопки обрезаются)."""

    def eventFilter(self, widget, event):  # noqa: N802 — имя из Qt
        if event.type() == QEvent.Resize and widget.layout() is not None:
            need = widget.layout().heightForWidth(widget.width())
            if need > 0 and widget.minimumHeight() != need:
                widget.setMinimumHeight(need)
        return False


def flow_policy(widget):
    """Виджет с FlowLayout внутри: высота зависит от ширины (кнопки переносятся)."""
    policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    policy.setHeightForWidth(True)
    widget.setSizePolicy(policy)
    widget._keep_height = _KeepHeight(widget)
    widget.installEventFilter(widget._keep_height)
    return widget
